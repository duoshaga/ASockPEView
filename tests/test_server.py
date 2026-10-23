import json
import unittest
from datetime import datetime
from unittest.mock import patch

import server


class MemoryHandler(server.Handler):
    """Exercise routes without opening a socket."""

    def __init__(self, path):
        self.path = path
        self.headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
        self.response = None

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        self.response = (status, json.loads(body) if "application/json" in content_type else body)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.clock = 100.0
        self.saved_state = dict(server.state)
        server.state.update(app="asock-pe-view", updating=False, started=self.saved_state["started"],
                            last_error=None, last_attempt_at=None)
        pending = self.pending

        class DeferredThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                pending.append(self.target)

        self.thread_patch = patch.object(server.threading, "Thread", DeferredThread)
        self.clock_patch = patch.object(server.time, "monotonic", lambda: self.clock)
        self.print_patch = patch("builtins.print")
        for item in (self.thread_patch, self.clock_patch, self.print_patch):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.restore_state)

    def restore_state(self):
        server.state.clear()
        server.state.update(self.saved_state)

    def post_update(self):
        handler = MemoryHandler("/api/update")
        handler.do_POST()
        return handler.response

    def status(self):
        handler = MemoryHandler("/api/status")
        handler.do_GET()
        return handler.response[1]

    def finish_update(self, *, result=None, error=None):
        with patch.object(server, "update", return_value=result, side_effect=error):
            self.pending.pop(0)()

    def test_server_startup_never_fetches_data(self):
        with patch.object(server, "ThreadingHTTPServer") as factory, \
                patch.object(server, "start_update") as start, \
                patch.object(server, "update") as fetch:
            server.main()
        factory.assert_called_once_with(("127.0.0.1", server.PORT), server.Handler)
        factory.return_value.__enter__.return_value.serve_forever.assert_called_once_with()
        start.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(self.pending, [])

    def test_opening_home_and_reading_cache_never_starts_an_update(self):
        with patch.object(server, "start_update") as start, patch.object(server, "update") as fetch:
            for path in ("/", "/data.json", "/api/status"):
                handler = MemoryHandler(path)
                handler.do_GET()
                self.assertEqual(handler.response[0], 200, path)
        start.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(self.pending, [])

    def test_first_update_starts_even_when_monotonic_clock_is_under_cooldown(self):
        self.clock = 5.0
        status, body = self.post_update()
        self.assertEqual(status, 202)
        self.assertEqual(body, {"accepted": True, "reason": "started"})
        self.assertTrue(self.status()["updating"])

    def test_update_exception_is_visible_in_status_with_attempt_time(self):
        self.post_update()
        attempted = self.status().get("last_attempt_at")
        self.assertIsNotNone(attempted)
        self.assertEqual(datetime.fromisoformat(attempted).utcoffset().total_seconds(), 28800)
        self.finish_update(error=OSError("cache write failed"))
        status = self.status()
        self.assertFalse(status["updating"])
        self.assertEqual(status["last_attempt_at"], attempted)
        self.assertIn("OSError", status["last_error"])
        self.assertIn("cache write failed", status["last_error"])

    def test_error_payload_is_visible_in_status(self):
        self.post_update()
        self.finish_update(result={"meta": {"update_status": "error", "error": "upstream unavailable",
                                           "last_attempt_at": "2026-10-23T18:30:00+08:00"}})
        status = self.status()
        self.assertFalse(status["updating"])
        self.assertEqual(status.get("last_error"), "upstream unavailable")
        self.assertEqual(status.get("last_attempt_at"), "2026-10-23T18:30:00+08:00")

    def test_success_clears_previous_update_error(self):
        server.state["last_error"] = "previous failure"
        self.post_update()
        self.finish_update(result={"meta": {"update_status": "success", "error": None,
                                           "last_attempt_at": "2026-10-23T18:31:00+08:00"}})
        status = self.status()
        self.assertFalse(status["updating"])
        self.assertIn("last_error", status)
        self.assertIsNone(status["last_error"])
        self.assertEqual(status["last_attempt_at"], "2026-10-23T18:31:00+08:00")

    def test_busy_and_throttled_requests_do_not_claim_acceptance(self):
        self.post_update()
        status, body = self.post_update()
        self.assertEqual((status, body), (200, {"accepted": False, "reason": "busy"}))
        self.finish_update(result={"meta": {"update_status": "success", "error": None}})
        self.clock += 29
        status, body = self.post_update()
        self.assertEqual((status, body), (200, {"accepted": False, "reason": "throttled"}))
        self.clock += 1
        status, body = self.post_update()
        self.assertEqual((status, body), (202, {"accepted": True, "reason": "started"}))


if __name__ == "__main__":
    unittest.main()
