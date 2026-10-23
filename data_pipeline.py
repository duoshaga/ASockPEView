"""Fetch and validate CSI All Share PE; persist last good data on failure."""
from __future__ import annotations

import csv
import io
import json
import math
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TZ = ZoneInfo("Asia/Hong_Kong")
INDEX = {"code": "000985", "name": "中证全指", "metric": "中证官网滚动市盈率",
         "source_url": "https://www.csindex.com.cn/zh-CN/indices/index-detail/000985"}


def normalize_frame(frame, requested_start, today):
    required = {"日期", "指数代码", "指数中文简称", "滚动市盈率", "收盘"}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError("中证返回空数据或字段结构改变")
    data = frame.copy().reset_index(drop=True)
    codes = data["指数代码"].astype(str).str.zfill(6)
    if not codes.eq("000985").all() or not data["指数中文简称"].eq("中证全指").all():
        raise ValueError("返回指数不是000985中证全指，拒绝替换")
    dates = pd.to_datetime(data["日期"], errors="raise")
    if dates.isna().any() or (dates.dt.date > today).any() or dates.duplicated().any():
        raise ValueError("返回日期缺失、重复或晚于今天")
    data["日期"] = dates.dt.strftime("%Y-%m-%d")
    data = data.sort_values("日期").reset_index(drop=True)
    start = datetime.strptime(requested_start, "%Y%m%d").date().isoformat()
    removed = []
    if len(data) > 1 and data.iloc[0]["日期"] == start:
        first = data.drop(columns="日期").iloc[0]
        second = data.drop(columns="日期").iloc[1]
        if first.equals(second):
            removed.append(data.iloc[0]["日期"])
            data = data.iloc[1:]

    def number(value, positive=False):
        try:
            n = float(value)
            return n if math.isfinite(n) and (not positive or n > 0) else None
        except (ValueError, TypeError):
            return None

    records = [{"date": str(row["日期"]), "pe": number(row["滚动市盈率"], True),
                "close": number(row["收盘"], True)} for _, row in data.iterrows()]
    if not any(r["pe"] is not None for r in records):
        raise ValueError("返回区间没有有效的正数市盈率")
    return records, {"removed_placeholders": removed}


def merge_records(old, new):
    merged = {row["date"]: dict(row) for row in old}
    for row in new:
        previous = merged.get(row["date"], {})
        merged[row["date"]] = {"date": row["date"],
                               "pe": row["pe"] if row["pe"] is not None else previous.get("pe"),
                               "close": row["close"] if row["close"] is not None else previous.get("close")}
    return [merged[key] for key in sorted(merged)]


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_json(path, payload):
    atomic_write(path, json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


@contextmanager
def update_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".update.lock").open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def fetch_akshare(**kwargs):
    import akshare as ak
    import requests
    original = requests.sessions.Session.request

    def bounded_request(session, method, url, **options):
        options.setdefault("timeout", (10, 45))
        result = original(session, method, url, **options)
        result.raise_for_status()
        return result

    requests.sessions.Session.request = bounded_request
    try:
        return ak.stock_zh_index_hist_csindex(**kwargs)
    finally:
        requests.sessions.Session.request = original


def update(root, fetcher=None, today=None):
    root = Path(root)
    with update_lock(root):
        payload = _update(root, fetcher or fetch_akshare, today or datetime.now(TZ).date())
        if payload["meta"]["update_status"] == "success":
            try:
                from preview import render_preview
                render_preview(root, payload)
            except Exception as exc:
                # A preview failure must not discard successfully fetched data.
                payload["meta"]["preview_error"] = f"{type(exc).__name__}: {exc}"
                save_json(root / "web" / "data.json", payload)
        return payload


def _update(root, fetcher, today):
    from importlib.metadata import version
    history_path = root / "data" / "history.json"
    previous = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else None
    old = previous["records"] if previous else []
    attempted = datetime.now(TZ).isoformat(timespec="seconds")
    start = (date.fromisoformat(old[-1]["date"]) - timedelta(days=35)).strftime("%Y%m%d") if old else "20041231"
    try:
        frame = fetcher(symbol="000985", start_date=start, end_date=today.strftime("%Y%m%d"))
        incoming, quality = normalize_frame(frame, start, today)
        last_incoming = max(r["date"] for r in incoming if r["pe"] is not None)
        if previous and last_incoming < previous["meta"]["last_date"]:
            raise ValueError("源站返回的最新有效日期早于本地缓存，已保留缓存")
        records = merge_records(old, incoming)
        first_valid = next(r["date"] for r in records if r["pe"] is not None)
        records = [r for r in records if r["date"] >= first_valid]
        valid = [r for r in records if r["pe"] is not None]
        removed = sorted(set((previous or {}).get("meta", {}).get("removed_placeholders", []) + quality["removed_placeholders"]))
        payload = {"index": INDEX, "meta": {
            "first_date": valid[0]["date"], "last_date": valid[-1]["date"],
            "valid_count": len(valid), "missing_count": len(records) - len(valid),
            "fetched_at": attempted, "last_attempt_at": attempted, "update_status": "success",
            "error": None, "removed_placeholders": removed, "akshare_version": version("akshare"),
            "preview_error": None,
            "requested_start": start, "requested_end": today.isoformat(),
            "source_rows": len(frame), "new_observations": len(records) - len(old),
        }, "records": records}
        save_json(history_path, payload)
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["日期", "中证全指滚动市盈率", "指数收盘点位"])
        writer.writerows([r["date"], r["pe"], r["close"]] for r in records)
        atomic_write(root / "web" / "history.csv", "\ufeff" + output.getvalue())
    except Exception as exc:
        payload = json.loads(json.dumps(previous)) if previous else {
            "index": INDEX, "records": [], "meta": {"fetched_at": None,
            "first_date": None, "last_date": None, "valid_count": 0, "missing_count": 0,
            "removed_placeholders": [], "akshare_version": version("akshare")}}
        payload["meta"].update({"last_attempt_at": attempted, "update_status": "error",
                                "error": f"{type(exc).__name__}: {exc}"})
    save_json(root / "web" / "data.json", payload)
    return payload
