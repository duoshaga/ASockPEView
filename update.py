"""Run once to refresh the locally cached chart data."""
import json
import sys
from pathlib import Path
from data_pipeline import update

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        payload = update(Path(__file__).resolve().parent)
        print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
        raise SystemExit(0 if payload["meta"]["update_status"] == "success" else 1)
    except OSError as exc:
        print(f"更新未执行（可能已有更新任务）：{exc}", file=sys.stderr)
        raise SystemExit(2)
