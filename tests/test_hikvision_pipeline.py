import csv
import importlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from hikvision_pipeline import (SECURITY, validate_eastmoney_response,
                               normalize_hikvision_frame, fetch_hikvision,
                               update_hikvision)


def raw_response():
    rows = [{"SECURITY_CODE": "002415", "SECUCODE": "002415.SZ",
             "SECURITY_NAME_ABBR": "海康威视", "TRADE_DATE": day,
             "PE_TTM": pe, "PE_LAR": 99.0, "CLOSE_PRICE": price}
            for day, pe, price in [("2026-10-29 00:00:00", 17.92, 34.79),
                                  ("2026-10-30 00:00:00", 17.8, 34.55)]]
    return {"success": True, "code": 0, "result": {"pages": 1, "count": 2, "data": rows}}


def sample_frame():
    frame = pd.DataFrame({"数据日期": ["2026-10-29", "2026-10-30"],
                          "PE(TTM)": [17.92, 17.8], "PE(静)": [99.0, 99.0],
                          "当日收盘价": [34.79, 34.55]})
    frame.attrs["validated_security"] = {"code": "002415", "name": "海康威视",
                                          "exchange_code": "002415.SZ"}
    frame.attrs.update(source_count=2, source_pages=1)
    return frame


class HikvisionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.preview = patch("preview.render_preview").start()
        self.addCleanup(patch.stopall)

    def test_response_identity_and_complete_pagination_required(self):
        validate_eastmoney_response(raw_response())
        variants = []
        for key, value in [("SECURITY_CODE", "000985"), ("SECUCODE", "002415.SH"),
                           ("SECURITY_NAME_ABBR", "其他公司")]:
            payload = raw_response()
            payload["result"]["data"][0][key] = value
            variants.append(payload)
        for key, value in [("pages", 2), ("count", 3)]:
            payload = raw_response()
            payload["result"][key] = value
            variants.append(payload)
        payload = raw_response()
        payload["success"] = False
        variants.append(payload)
        for payload in variants:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_eastmoney_response(payload)

    def test_normalize_uses_ttm_and_keeps_missing_nonpositive_values_as_null(self):
        frame = sample_frame()
        records = normalize_hikvision_frame(frame, date(2026, 11, 2))
        self.assertEqual([r["pe"] for r in records], [17.92, 17.8])
        for value in (None, 0, -3, float("inf")):
            frame.loc[1, "PE(TTM)"] = value
            self.assertIsNone(normalize_hikvision_frame(frame, date(2026, 11, 2))[1]["pe"])
        with self.assertRaises(ValueError):
            normalize_hikvision_frame(frame.drop(columns="PE(TTM)"), date(2026, 11, 2))

    def test_rejects_unverified_identity_all_empty_duplicate_and_future_dates(self):
        frames = []
        frame = sample_frame()
        frame.attrs.clear()
        frames.append(frame)
        frame = sample_frame()
        frame["PE(TTM)"] = None
        frames.append(frame)
        for day in ("2026-10-29", "2026-11-03"):
            frame = sample_frame()
            frame.loc[1, "数据日期"] = day
            frames.append(frame)
        for frame in frames:
            with self.assertRaises(ValueError):
                normalize_hikvision_frame(frame, date(2026, 11, 2))

    def test_fetch_uses_akshare_bounded_request_and_restores_its_helper(self):
        import akshare as ak
        module = importlib.import_module(ak.stock_value_em.__module__)
        original = module.make_request_with_retry_json
        response = Mock()
        response.json.return_value = raw_response()

        def fake_akshare(symbol):
            self.assertEqual(symbol, "002415")
            module.make_request_with_retry_json(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                params={"filter": '(SECURITY_CODE="002415")', "reportName": "RPT_VALUEANALYSIS_DET"})
            return sample_frame()

        with patch.object(ak, "stock_value_em", side_effect=fake_akshare) as mocked, patch("requests.get", return_value=response) as get:
            # The mocked public entry point still needs the real source module identity.
            mocked.__module__ = module.__name__
            result = fetch_hikvision()
            self.assertEqual(result.attrs["source_count"], 2)
            self.assertEqual(get.call_args.kwargs["timeout"], (10, 35))
        self.assertIs(module.make_request_with_retry_json, original)
        response.json.side_effect = ValueError("bad JSON")
        with patch.object(ak, "stock_value_em", side_effect=fake_akshare) as mocked, patch("requests.get", return_value=response):
            mocked.__module__ = module.__name__
            with self.assertRaises(ValueError):
                fetch_hikvision()
        self.assertIs(module.make_request_with_retry_json, original)

    def test_success_writes_independent_cache_csv_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "web").mkdir()
            original = b'{"csi":"untouched"}'
            (root / "web" / "data.json").write_bytes(original)
            result = update_hikvision(root, fetcher=lambda **kw: sample_frame(), today=date(2026, 11, 2))
            self.assertEqual(result["index"], SECURITY)
            self.assertEqual(result["meta"]["update_status"], "success")
            self.assertEqual((root / "web" / "data.json").read_bytes(), original)
            with (root / "web" / "hikvision-history.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(len(row) == 3 for row in rows))
            self.preview.assert_called_once_with(root, result)

    def test_failure_and_shorter_history_preserve_last_successful_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = update_hikvision(root, fetcher=lambda **kw: sample_frame(), today=date(2026, 11, 2))
            history = (root / "data" / "hikvision-history.json").read_bytes()
            def failed(**kwargs):
                raise ConnectionError("upstream unavailable")
            bad = sample_frame().iloc[1:].copy()
            bad.attrs["source_count"] = 1
            for fetch in (failed, lambda **kw: bad):
                result = update_hikvision(root, fetcher=fetch, today=date(2026, 11, 2))
                self.assertEqual(result["meta"]["update_status"], "error")
                self.assertEqual(result["records"], first["records"])
                self.assertEqual(result["meta"]["fetched_at"], first["meta"]["fetched_at"])
                self.assertEqual((root / "data" / "hikvision-history.json").read_bytes(), history)

    def test_latest_source_date_regression_and_wrong_cache_identity_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = update_hikvision(root, fetcher=lambda **kw: sample_frame(), today=date(2026, 11, 2))
            bad = sample_frame().iloc[:-1].copy()
            bad.attrs["source_count"] = len(bad)
            result = update_hikvision(root, fetcher=lambda **kw: bad, today=date(2026, 11, 2))
            self.assertEqual(result["meta"]["update_status"], "error")
            self.assertEqual(result["records"], first["records"])
            first["index"] = {"code": "000985", "name": "中证全指"}
            (root / "data" / "hikvision-history.json").write_text(json.dumps(first), encoding="utf-8")
            with self.assertRaises(ValueError):
                update_hikvision(root, fetcher=lambda **kw: sample_frame(), today=date(2026, 11, 2))

    def test_revisions_are_accepted_and_preview_failure_keeps_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            update_hikvision(root, fetcher=lambda **kw: sample_frame(), today=date(2026, 11, 2))
            revised = sample_frame()
            revised.loc[0, "PE(TTM)"] = 18.0
            self.preview.side_effect = OSError("preview locked")
            result = update_hikvision(root, fetcher=lambda **kw: revised, today=date(2026, 11, 2))
            self.assertEqual(result["meta"]["update_status"], "success")
            self.assertEqual(result["records"][0]["pe"], 18.0)
            self.assertIn("preview locked", result["meta"]["preview_error"])

    def test_verified_revision_can_replace_cached_positive_pe_with_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            update_hikvision(root, fetcher=lambda **kw: sample_frame(), today=date(2026, 11, 2))
            revised = sample_frame()
            revised.loc[0, "PE(TTM)"] = -7.0
            result = update_hikvision(root, fetcher=lambda **kw: revised, today=date(2026, 11, 2))
            self.assertEqual(result["meta"]["update_status"], "success")
            self.assertIsNone(result["records"][0]["pe"])
            self.assertEqual(result["meta"]["valid_count"], 1)
            self.assertEqual(result["meta"]["missing_count"], 1)

    def test_latest_source_day_can_be_revised_to_null_without_retaining_old_pe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            update_hikvision(root, fetcher=lambda **kw: sample_frame(), today=date(2026, 11, 2))
            revised = sample_frame()
            revised.loc[1, "PE(TTM)"] = None
            result = update_hikvision(root, fetcher=lambda **kw: revised, today=date(2026, 11, 2))
            self.assertEqual(result["meta"]["update_status"], "success")
            self.assertEqual(result["records"][-1]["date"], "2026-10-30")
            self.assertIsNone(result["records"][-1]["pe"])
            self.assertEqual(result["meta"]["last_date"], "2026-10-29")
            self.assertEqual(result["meta"]["missing_count"], 1)
            saved = json.loads((root / "data" / "hikvision-history.json").read_text(encoding="utf-8"))
            self.assertIsNone(saved["records"][-1]["pe"])

    def test_publication_failure_restores_prior_csv_history_and_web_records(self):
        import hikvision_pipeline
        real_save_json = hikvision_pipeline.save_json
        for failed_name in ("hikvision-history.json", "hikvision-data.json"):
            with self.subTest(failed_name=failed_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                first = update_hikvision(root, fetcher=lambda **kw: sample_frame(), today=date(2026, 11, 2))
                history_path = root / "data" / "hikvision-history.json"
                csv_path = root / "web" / "hikvision-history.csv"
                previous_history = history_path.read_bytes()
                previous_csv = csv_path.read_bytes()
                revised = sample_frame()
                revised.loc[0, "PE(TTM)"] = 18.0

                def fail_publication(path, payload):
                    if path.name == failed_name and payload["meta"]["update_status"] == "success":
                        raise OSError("injected snapshot publication failure")
                    return real_save_json(path, payload)

                with patch("hikvision_pipeline.save_json", side_effect=fail_publication):
                    result = update_hikvision(root, fetcher=lambda **kw: revised, today=date(2026, 11, 2))
                self.assertEqual(result["meta"]["update_status"], "error")
                self.assertEqual(result["records"], first["records"])
                self.assertEqual(history_path.read_bytes(), previous_history)
                self.assertEqual(csv_path.read_bytes(), previous_csv)
                web = json.loads((root / "web" / "hikvision-data.json").read_text(encoding="utf-8"))
                self.assertEqual(web["records"], first["records"])
                self.assertEqual(web["meta"]["fetched_at"], first["meta"]["fetched_at"])
                self.assertEqual(web["meta"]["update_status"], "error")

    def test_first_publication_failure_removes_new_csv_and_history(self):
        import hikvision_pipeline
        real_save_json = hikvision_pipeline.save_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def fail_history(path, payload):
                if path.name == "hikvision-history.json":
                    raise OSError("history is unwritable")
                return real_save_json(path, payload)
            with patch("hikvision_pipeline.save_json", side_effect=fail_history):
                result = update_hikvision(root, fetcher=lambda **kw: sample_frame(), today=date(2026, 11, 2))
            self.assertEqual(result["meta"]["update_status"], "error")
            self.assertEqual(result["records"], [])
            self.assertFalse((root / "data" / "hikvision-history.json").exists())
            self.assertFalse((root / "web" / "hikvision-history.csv").exists())


if __name__ == "__main__":
    unittest.main()
