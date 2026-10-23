"""Local-only chart server. Serves a fixed file list and runs serialized updates."""
import json
import mimetypes
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from data_pipeline import TZ, update

ROOT = Path(__file__).resolve().parent
PORT = 8765
state_lock = threading.Lock()
state = {"app": "asock-pe-view", "updating": False, "started": None,
         "last_error": None, "last_attempt_at": None}


def start_update():
    with state_lock:
        if state["updating"]:
            return {"accepted": False, "reason": "busy"}
        now = time.monotonic()
        if state["started"] is not None and now - state["started"] < 30:
            return {"accepted": False, "reason": "throttled"}
        state["updating"] = True
        state["started"] = now
        state["last_attempt_at"] = datetime.now(TZ).isoformat(timespec="seconds")

    def run():
        error = None
        try:
            result = update(ROOT)
            meta = result["meta"]
            if meta.get("update_status") != "success":
                error = meta.get("error") or "Update did not complete successfully"
            with state_lock:
                state["last_attempt_at"] = meta.get("last_attempt_at") or state["last_attempt_at"]
            print(json.dumps(meta, ensure_ascii=True), flush=True)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"Update skipped: {exc}", flush=True)
        finally:
            with state_lock:
                state["last_error"] = error
                state["updating"] = False

    threading.Thread(target=run, daemon=True).start()
    return {"accepted": True, "reason": "started"}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        return self.headers.get("Host") in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def do_GET(self):
        if not self.local_request():
            return self.reply(403, b'{"error":"Local access only"}')
        path = urlsplit(self.path).path
        if path in {"/api/status", "/api/health"}:
            with state_lock:
                data = {"app": state["app"], "updating": state["updating"],
                        "last_error": state["last_error"], "last_attempt_at": state["last_attempt_at"]}
            return self.reply(200, json.dumps(data).encode())
        allowed = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
                   "/styles.css": "styles.css", "/plotly.min.js": "plotly.min.js",
                   "/data.json": "data.json", "/history.csv": "history.csv"}
        filename = allowed.get(path)
        if not filename or not (ROOT / "web" / filename).is_file():
            return self.reply(404, b'{"error":"Not found"}')
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if filename.endswith(".js"):
            mime = "application/javascript"
        return self.reply(200, (ROOT / "web" / filename).read_bytes(), mime + "; charset=utf-8")

    def do_POST(self):
        allowed_origins = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
        if not self.local_request() or self.headers.get("Origin", f"http://127.0.0.1:{PORT}") not in allowed_origins:
            return self.reply(403, b'{"error":"Local access only"}')
        if urlsplit(self.path).path != "/api/update":
            return self.reply(404, b'{"error":"Not found"}')
        result = start_update()
        return self.reply(202 if result["accepted"] else 200, json.dumps(result).encode())


def main():
    """Serve cached data; upstream requests require an explicit update POST."""
    with ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as server:
        print(f"CSI PE chart: http://127.0.0.1:{PORT}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
