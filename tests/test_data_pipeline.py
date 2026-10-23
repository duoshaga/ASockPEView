import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from data_pipeline import normalize_frame, merge_records, update


def sample_frame():
    return pd.DataFrame({
        "日期": ["2026-10-01", "2026-10-08", "2026-10-09"],
        "指数代码": ["000985"] * 3,
        "指数中文简称": ["中证全指"] * 3,
        "收盘": [5560.01, 5560.01, 5570.32],
        "成交量": [97451843608, 97451843608, 112667809763],
        "滚动市盈率": [17.10, 17.10, 17.12],
    })


class PipelineTests(unittest.TestCase):
    def test_exported_csv_has_one_line_per_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = update(root, fetcher=lambda **kw: sample_frame().iloc[1:], today=date(2026, 10, 23))
            path = root / "web" / "history.csv"
            self.assertNotIn(b"\r\r\n", path.read_bytes())
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(len(rows), len(result["records"]) + 1)
            self.assertTrue(all(len(row) == 3 for row in rows))

    def test_removes_copied_requested_start_without_dropping_real_first_day(self):
        records, quality = normalize_frame(sample_frame(), "20261001", date(2026, 10, 23))
        self.assertEqual([r["date"] for r in records], ["2026-10-08", "2026-10-09"])
        self.assertEqual(quality["removed_placeholders"], ["2026-10-01"])
        records, quality = normalize_frame(sample_frame().iloc[1:], "20261008", date(2026, 10, 23))
        self.assertEqual(records[0]["date"], "2026-10-08")
        self.assertEqual(quality["removed_placeholders"], [])

    def test_rejects_wrong_index_and_future_dates(self):
        for column, value in [("指数代码", "000906"), ("日期", "2026-10-24")]:
            with self.subTest(column=column):
                frame = sample_frame()
                frame.loc[2, column] = value
                with self.assertRaises(ValueError):
                    normalize_frame(frame, "20261001", date(2026, 10, 23))

    def test_missing_observation_remains_null(self):
        frame = sample_frame().iloc[1:].copy()
        frame.loc[2, "滚动市盈率"] = None
        records, quality = normalize_frame(frame, "20261008", date(2026, 10, 23))
        self.assertIsNone(records[1]["pe"])
        self.assertEqual(records[0]["pe"], 17.10)

    def test_all_missing_pe_is_not_a_successful_update(self):
        frame = sample_frame()
        frame["滚动市盈率"] = None
        with self.assertRaises(ValueError):
            normalize_frame(frame, "20261001", date(2026, 10, 23))

    def test_merge_keeps_valid_cache_and_accepts_corrections(self):
        old = [{"date": "2026-10-08", "pe": 17.1, "close": 5560.01},
               {"date": "2026-10-09", "pe": 17.12, "close": 5570.32}]
        new = [{"date": "2026-10-08", "pe": None, "close": None},
               {"date": "2026-10-09", "pe": 17.13, "close": 5570.32},
               {"date": "2026-10-12", "pe": 17.0, "close": 5500.0}]
        result = merge_records(old, new)
        self.assertEqual([r["pe"] for r in result], [17.1, 17.13, 17.0])
        self.assertEqual(result[0]["close"], 5560.01)

    def test_failure_preserves_records_and_success_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []

            def fetch(**kwargs):
                calls.append(kwargs)
                return sample_frame().iloc[1:]

            first = update(root, fetcher=fetch, today=date(2026, 10, 23))
            self.assertEqual(calls[0]["symbol"], "000985")
            self.assertEqual(first["meta"]["last_date"], "2026-10-09")

            def fail(**kwargs):
                raise ConnectionError("upstream unavailable")

            failed = update(root, fetcher=fail, today=date(2026, 10, 23))
            self.assertEqual(failed["meta"]["update_status"], "error")
            self.assertEqual(failed["records"], first["records"])
            self.assertEqual(failed["meta"]["fetched_at"], first["meta"]["fetched_at"])
            saved = json.loads((root / "web" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["records"], first["records"])
            self.assertIn("upstream unavailable", saved["meta"]["error"])

    def test_older_response_cannot_regress_cached_latest_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            update(root, fetcher=lambda **kw: sample_frame().iloc[1:], today=date(2026, 10, 23))
            result = update(root, fetcher=lambda **kw: sample_frame().iloc[1:2], today=date(2026, 10, 23))
            self.assertEqual(result["meta"]["update_status"], "error")
            self.assertEqual(result["meta"]["last_date"], "2026-10-09")


if __name__ == "__main__":
    unittest.main()
