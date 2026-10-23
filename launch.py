"""Start the local background server, then open the chart in the default browser."""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ready():
    try:
        with opener.open(URL + "/api/health", timeout=1) as response:
            return json.load(response).get("app") == "asock-pe-view"
    except Exception:
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not ready():
        with (ROOT / "server.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        for _ in range(60):
            if ready():
                break
            if process.poll() is not None:
                raise SystemExit("Server failed to start. See server.log (port 8765 may be in use).")
            time.sleep(0.25)
        else:
            raise SystemExit("Server startup timed out. See server.log.")
    if not args.no_browser:
        webbrowser.open(URL)
    print(URL)
