import unittest
from unittest.mock import patch

import server
from test_server import MemoryHandler


class HikvisionRoutesTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(server.hikvision_state)
        self.saved_csi = dict(server.state)
        server.hikvision_state.update(updating=False, started=None, last_error=None, last_attempt_at=None)
        self.addCleanup(self.restore)

    def restore(self):
        server.hikvision_state.clear()
        server.hikvision_state.update(self.saved)
        server.state.clear()
        server.state.update(self.saved_csi)

    def test_hikvision_post_updates_only_its_data_and_status(self):
        pending = []
        with patch.object(server.threading, "Thread") as thread, \
                patch.object(server, "update_hikvision", return_value={"meta": {"update_status": "success"}}) as hik, \
                patch.object(server, "update") as csi:
            thread.side_effect = lambda **kw: type("Deferred", (), {"start": lambda self: pending.append(kw["target"])})()
            handler = MemoryHandler("/api/hikvision/update")
            handler.do_POST()
            self.assertEqual(handler.response, (202, {"accepted": True, "reason": "started"}))
            self.assertTrue(server.hikvision_state["updating"])
            self.assertEqual(server.state, self.saved_csi)
            pending.pop()()
            hik.assert_called_once_with(server.ROOT)
            csi.assert_not_called()
        self.assertFalse(server.hikvision_state["updating"])

    def test_separate_status_does_not_leak_other_instrument_error(self):
        server.state["last_error"] = "CSI failure"
        handler = MemoryHandler("/api/hikvision/status")
        handler.do_GET()
        self.assertEqual(handler.response[0], 200)
        self.assertIsNone(handler.response[1]["last_error"])

    def test_reading_hikvision_page_and_cache_never_fetches(self):
        with patch.object(server, "start_update") as start, patch.object(server, "update_hikvision") as hik:
            for path in ("/hikvision.html", "/hikvision-data.json", "/hikvision-history.csv"):
                handler = MemoryHandler(path)
                handler.do_GET()
                self.assertEqual(handler.response[0], 200, path)
        start.assert_not_called()
        hik.assert_not_called()
