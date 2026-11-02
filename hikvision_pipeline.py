"""Manually fetch verified Hikvision PE(TTM), keeping its cache separate from CSI."""
from __future__ import annotations

import copy
import csv
import importlib
import io
import json
import math
import os
import tempfile
import threading
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import pandas as pd

from data_pipeline import TZ, atomic_write, save_json, update_lock

SECURITY = {"code": "002415", "name": "海康威视", "metric": "滚动市盈率（PE TTM）",
            "source_name": "东方财富", "source_url": "https://data.eastmoney.com/gzfx/detail/002415.html"}
_IDENTITY = {"code": "002415", "name": "海康威视", "exchange_code": "002415.SZ"}
_ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_FETCH_LOCK = threading.Lock()


def validate_eastmoney_response(payload):
    """Check identity before AKShare drops the source's identifying columns."""
    if not isinstance(payload, dict) or payload.get("success") is not True or payload.get("code") != 0:
        raise ValueError("东方财富返回失败状态")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("东方财富返回缺少估值数据")
    rows = result.get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("东方财富返回空估值记录")
    if result.get("pages") != 1 or result.get("count") != len(rows):
        raise ValueError("东方财富历史数据分页不完整，拒绝使用截断数据")
    for row in rows:
        if not isinstance(row, dict) or any(row.get(key) != value for key, value in (
            ("SECURITY_CODE", "002415"), ("SECUCODE", "002415.SZ"), ("SECURITY_NAME_ABBR", "海康威视")
        )):
            raise ValueError("返回证券不是 002415.SZ 海康威视，拒绝替换")
    return {"validated_security": dict(_IDENTITY), "source_count": len(rows), "source_pages": 1}


def fetch_hikvision(symbol="002415"):
    """Use AKShare's conversion with one bounded, identity-checked HTTP request."""
    if symbol != "002415":
        raise ValueError("此更新器仅支持海康威视 002415")
    import akshare as ak
    import requests

    module = importlib.import_module(ak.stock_value_em.__module__)
    metadata = {}

    def request_once(url, params=None, headers=None, proxies=None, **_):
        if url != _ENDPOINT or not isinstance(params, dict) or params.get("filter") != '(SECURITY_CODE="002415")' or params.get("reportName") != "RPT_VALUEANALYSIS_DET":
            raise ValueError("AKShare 的海康威视估值请求结构改变")
        response = requests.get(url, params=params, headers=headers, proxies=proxies, timeout=(10, 35))
        response.raise_for_status()
        payload = response.json()
        metadata.update(validate_eastmoney_response(payload))
        return payload

    with _FETCH_LOCK:
        original = module.make_request_with_retry_json
        module.make_request_with_retry_json = request_once
        try:
            frame = ak.stock_value_em(symbol=symbol)
        finally:
            module.make_request_with_retry_json = original
    if not metadata or len(frame) != metadata["source_count"]:
        raise ValueError("AKShare 结果缺少身份核验或记录不完整")
    frame.attrs.update(metadata)
    return frame


def normalize_hikvision_frame(frame, today):
    required = {"数据日期", "PE(TTM)", "当日收盘价"}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError("海康威视估值数据为空或缺少 PE(TTM) 字段")
    if frame.attrs.get("validated_security") != _IDENTITY:
        raise ValueError("海康威视数据缺少可靠的证券身份核验")
    if frame.attrs.get("source_count") != len(frame) or frame.attrs.get("source_pages") != 1:
        raise ValueError("海康威视历史数据不完整")
    data = frame.copy().reset_index(drop=True)
    dates = pd.to_datetime(data["数据日期"], errors="raise")
    if dates.isna().any() or dates.duplicated().any() or (dates.dt.date > today).any():
        raise ValueError("海康威视数据日期缺失、重复或晚于今天")
    data["数据日期"] = dates.dt.strftime("%Y-%m-%d")
    data = data.sort_values("数据日期")

    def positive(value):
        if isinstance(value, bool):
            return None
        try:
            value = float(value)
            return value if math.isfinite(value) and value > 0 else None
        except (ValueError, TypeError):
            return None

    records = [{"date": row["数据日期"], "pe": positive(row["PE(TTM)"]),
                "close": positive(row["当日收盘价"])} for _, row in data.iterrows()]
    if not any(row["pe"] is not None for row in records):
        raise ValueError("海康威视返回区间没有有效的正数 PE(TTM)")
    return records


def update_hikvision(root, fetcher=None, today=None):
    root = Path(root)
    with update_lock(root):
        payload = _update(root, fetcher or fetch_hikvision, today or datetime.now(TZ).date())
        if payload["meta"]["update_status"] == "success":
            try:
                from preview import render_preview
                render_preview(root, payload)
            except Exception as exc:
                payload["meta"]["preview_error"] = f"{type(exc).__name__}: {exc}"
                save_json(root / "web" / "hikvision-data.json", payload)
        return payload


def _restore_file(path, previous_bytes):
    """Restore exact prior bytes without relying on a failed JSON writer."""
    if previous_bytes is None:
        path.unlink(missing_ok=True)
        return
    if path.exists() and path.read_bytes() == previous_bytes:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".hikvision-rollback-", suffix=".tmp",
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(previous_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _publish_snapshot(root, payload, csv_text):
    """Publish web JSON last and roll all data files back after a write failure."""
    csv_path = root / "web" / "hikvision-history.csv"
    history_path = root / "data" / "hikvision-history.json"
    web_path = root / "web" / "hikvision-data.json"
    paths = (csv_path, history_path, web_path)
    backups = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        atomic_write(csv_path, csv_text)
        save_json(history_path, payload)
        save_json(web_path, payload)
    except Exception as publication_error:
        errors = []
        # Include the failing path: an exception after replace may still have
        # changed it. Unchanged files are detected and left alone.
        for path in reversed(paths):
            try:
                _restore_file(path, backups[path])
            except Exception as rollback_error:
                errors.append(f"{path.name}: {rollback_error}")
        if errors:
            raise OSError(f"{publication_error}; 缓存回滚失败: {'; '.join(errors)}") from publication_error
        raise


def _update(root, fetcher, today):
    history_path = root / "data" / "hikvision-history.json"
    previous = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else None
    if previous and (previous.get("index", {}).get("code") != "002415" or previous.get("index", {}).get("name") != "海康威视"):
        raise ValueError("海康威视缓存证券身份不匹配，拒绝读取")
    old = previous["records"] if previous else []
    attempted = datetime.now(TZ).isoformat(timespec="seconds")
    try:
        frame = fetcher(symbol="002415")
        incoming = normalize_hikvision_frame(frame, today)
        # Compare source coverage, not the last positive PE observation:
        # a verified revision may legitimately remove the final day's PE.
        if old and incoming[-1]["date"] < old[-1]["date"]:
            raise ValueError("源站返回的最新记录日期早于海康威视本地缓存")
        if not {row["date"] for row in old}.issubset({row["date"] for row in incoming}):
            raise ValueError("源站返回的海康威视历史记录缩短或缺失，已保留缓存")
        # A complete, identity-checked snapshot is authoritative, including
        # historical corrections to negative or missing PE values.
        records = incoming
        valid = [row for row in records if row["pe"] is not None]
        payload = {"index": dict(SECURITY), "meta": {
            "first_date": valid[0]["date"], "last_date": valid[-1]["date"],
            "valid_count": len(valid), "missing_count": len(records) - len(valid),
            "fetched_at": attempted, "last_attempt_at": attempted, "update_status": "success",
            "error": None, "preview_error": None, "akshare_version": version("akshare"),
            "source_rows": len(frame), "source_pages": frame.attrs["source_pages"],
            "requested_end": today.isoformat(), "new_observations": len(records) - len(old),
            "history_scope": "东方财富接口可用日度历史，不代表上市以来完整历史",
        }, "records": records}
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["日期", "海康威视滚动市盈率PE(TTM)", "收盘价(元)"])
        writer.writerows([row["date"], row["pe"], row["close"]] for row in records)
        _publish_snapshot(root, payload, "\ufeff" + output.getvalue())
    except Exception as exc:
        payload = copy.deepcopy(previous) if previous else {"index": dict(SECURITY), "records": [], "meta": {
            "first_date": None, "last_date": None, "fetched_at": None, "valid_count": 0,
            "missing_count": 0, "preview_error": None, "akshare_version": version("akshare")}}
        payload["meta"].update(last_attempt_at=attempted, update_status="error", error=f"{type(exc).__name__}: {exc}")
        save_json(root / "web" / "hikvision-data.json", payload)
    return payload
