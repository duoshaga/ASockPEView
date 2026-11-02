import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preview import render_preview


def payload():
    return {
        "index": {"code": "000985"},
        "meta": {"last_date": "1900-01-01", "valid_count": 999},
        "records": [
            {"date": "2026-10-08", "pe": 10.0},
            {"date": "2026-10-09", "pe": None},
            {"date": "2026-10-12", "pe": 20.0},
            {"date": "2026-10-13", "pe": 30.0},
            {"date": "2026-10-14", "pe": None},
        ],
    }


class PreviewTests(unittest.TestCase):
    def test_hikvision_preview_uses_its_own_file_title_and_source(self):
        from PIL import Image
        data = payload()
        data["index"] = {"code": "002415", "name": "海康威视"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            existing = root / "docs" / "pe-history.png"
            existing.write_bytes(b"CSI image unchanged")
            render_preview(root, data)
            self.assertEqual(existing.read_bytes(), b"CSI image unchanged")
            with Image.open(root / "docs" / "hikvision-pe-history.png") as picture:
                description = picture.info["Description"]
                self.assertIn("Hikvision 002415", description)
                self.assertIn("PE TTM", description)
                self.assertIn("eastmoney.com", description)
                self.assertNotIn("csindex.com.cn", description)

    def test_creates_readable_png_with_statistics_from_observations(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            render_preview(root, payload())
            with Image.open(root / "docs" / "pe-history.png") as picture:
                picture.load()
                self.assertEqual(picture.format, "PNG")
                self.assertGreaterEqual(picture.width, 1600)
                description = picture.info["Description"]
                for text in ("000985", "2026-10-13", "30.00", "valid=3", "missing=2",
                             "P20=14.00", "P50=20.00", "P80=26.00"):
                    self.assertIn(text, description)
                self.assertNotIn(str(root), str(picture.info))

    def test_missing_observations_reach_plot_as_gaps(self):
        from matplotlib.axes import Axes

        plotted = []
        original = Axes.plot

        def capture(axes, *args, **kwargs):
            if len(args) >= 2:
                plotted.append(list(args[1]))
            return original(axes, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp, patch.object(Axes, "plot", capture):
            render_preview(Path(tmp), payload())
        history = next(series for series in plotted if len(series) == 5)
        self.assertTrue(math.isnan(history[1]))
        self.assertTrue(math.isnan(history[4]))
        self.assertEqual([history[0], history[2], history[3]], [10, 20, 30])

    def test_bad_inputs_leave_previous_image_intact(self):
        cases = []
        other_index = payload()
        other_index["index"]["code"] = "000906"
        cases.append(other_index)
        no_pe = payload()
        for record in no_pe["records"]:
            record["pe"] = None
        cases.append(no_pe)
        duplicate = payload()
        duplicate["records"][1]["date"] = duplicate["records"][0]["date"]
        cases.append(duplicate)
        invalid_pe = payload()
        invalid_pe["records"][0]["pe"] = -1
        cases.append(invalid_pe)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "docs" / "pe-history.png"
            output.parent.mkdir()
            output.write_bytes(b"previous image")
            for candidate in cases:
                with self.subTest(payload=candidate), self.assertRaises(ValueError):
                    render_preview(root, candidate)
                self.assertEqual(output.read_bytes(), b"previous image")

    def test_replace_failure_keeps_previous_image_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "docs" / "pe-history.png"
            output.parent.mkdir()
            output.write_bytes(b"previous image")
            with patch("preview.os.replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    render_preview(root, payload())
            self.assertEqual(output.read_bytes(), b"previous image")
            self.assertEqual([file.name for file in output.parent.iterdir()], ["pe-history.png"])


if __name__ == "__main__":
    unittest.main()
