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
from hikvision_pipeline import update_hikvision

ROOT = Path(__file__).resolve().parent
PORT = 8765
state_lock = threading.Lock()
state = {"app": "asock-pe-view", "updating": False, "started": None,
         "last_error": None, "last_attempt_at": None}
hikvision_state = {"app": "asock-pe-view", "updating": False, "started": None,
                   "last_error": None, "last_attempt_at": None}
# AKShare adapters temporarily bound upstream helpers; run them serially.
update_run_lock = threading.Lock()


def start_update(instrument="csi"):
    if instrument not in {"csi", "hikvision"}:
        raise ValueError("Unknown instrument")
    target_state = hikvision_state if instrument == "hikvision" else state
    with state_lock:
        if target_state["updating"]:
            return {"accepted": False, "reason": "busy"}
        now = time.monotonic()
        if target_state["started"] is not None and now - target_state["started"] < 30:
            return {"accepted": False, "reason": "throttled"}
        target_state["updating"] = True
        target_state["started"] = now
        target_state["last_attempt_at"] = datetime.now(TZ).isoformat(timespec="seconds")

    def run():
        error = None
        try:
            with update_run_lock:
                updater = update_hikvision if instrument == "hikvision" else update
                result = updater(ROOT)
            meta = result["meta"]
            if meta.get("update_status") != "success":
                error = meta.get("error") or "Update did not complete successfully"
            with state_lock:
                target_state["last_attempt_at"] = meta.get("last_attempt_at") or target_state["last_attempt_at"]
            print(json.dumps(meta, ensure_ascii=True), flush=True)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"Update skipped: {exc}", flush=True)
        finally:
            with state_lock:
                target_state["last_error"] = error
                target_state["updating"] = False

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
        if path in {"/api/status", "/api/health", "/api/hikvision/status"}:
            target_state = hikvision_state if path == "/api/hikvision/status" else state
            with state_lock:
                data = {"app": target_state["app"], "updating": target_state["updating"],
                        "last_error": target_state["last_error"], "last_attempt_at": target_state["last_attempt_at"]}
            return self.reply(200, json.dumps(data).encode())
        allowed = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
                   "/styles.css": "styles.css", "/plotly.min.js": "plotly.min.js",
                   "/data.json": "data.json", "/history.csv": "history.csv",
                   "/hikvision.html": "hikvision.html", "/hikvision-data.json": "hikvision-data.json",
                   "/hikvision-history.csv": "hikvision-history.csv"}
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
        path = urlsplit(self.path).path
        if path not in {"/api/update", "/api/hikvision/update"}:
            return self.reply(404, b'{"error":"Not found"}')
        result = start_update("hikvision") if path == "/api/hikvision/update" else start_update()
        return self.reply(202 if result["accepted"] else 200, json.dumps(result).encode())


def main():
    """Serve cached data; upstream requests require an explicit update POST."""
    with ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as server:
        print(f"CSI PE chart: http://127.0.0.1:{PORT}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
