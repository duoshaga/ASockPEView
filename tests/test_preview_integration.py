import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from data_pipeline import update
from test_data_pipeline import sample_frame


class PreviewIntegrationTests(unittest.TestCase):
    def test_success_generates_preview_but_failed_fetch_does_not(self):
        with tempfile.TemporaryDirectory() as tmp, patch("preview.render_preview") as render:
            root = Path(tmp)
            payload = update(root, fetcher=lambda **kw: sample_frame().iloc[1:], today=date(2026, 10, 23))
            render.assert_called_once_with(root, payload)
            render.reset_mock()
            def fail(**kwargs):
                raise ConnectionError("source unavailable")
            failed = update(root, fetcher=fail, today=date(2026, 10, 23))
            self.assertEqual(failed["meta"]["update_status"], "error")
            render.assert_not_called()

    def test_preview_failure_preserves_successful_data_and_exposes_warning(self):
        with tempfile.TemporaryDirectory() as tmp, patch("preview.render_preview", side_effect=OSError("image locked")):
            root = Path(tmp)
            payload = update(root, fetcher=lambda **kw: sample_frame().iloc[1:], today=date(2026, 10, 23))
            self.assertEqual(payload["meta"]["update_status"], "success")
            self.assertIn("image locked", payload["meta"]["preview_error"])
            saved = json.loads((root / "web" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["records"], payload["records"])
            self.assertEqual(saved["meta"]["preview_error"], payload["meta"]["preview_error"])
