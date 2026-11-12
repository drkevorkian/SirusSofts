"""Regression tests for OpenRC 'started' before dashboard socket is bound."""
import http.server
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard_health as health


class ReadinessTests(unittest.TestCase):
    def test_retries_until_server_binds(self):
        values = iter([(False, "not bound"), (False, "still initializing"),
                       (True, "healthy")])
        times = [0.0]
        sleeps = []

        def fake_clock():
            return times[0]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            times[0] += seconds

        success, message = health.wait_ready(
            port=18080, timeout=4, check=lambda port: next(values),
            clock=fake_clock, sleep=fake_sleep)
        self.assertTrue(success)
        self.assertEqual(message, "healthy")
        self.assertEqual(len(sleeps), 2)

    def test_timeout_is_bounded(self):
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds

        success, message = health.wait_ready(
            timeout=1.2, check=lambda _: (False, "socket not accepting"),
            clock=lambda: now[0], sleep=sleep)
        self.assertFalse(success)
        self.assertEqual(message, "socket not accepting")
        self.assertLessEqual(now[0], 1.21)

    def test_readiness_requires_live_endpoints_not_only_old_healthz(self):
        class OldDashboard(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path == "/healthz":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OK\n")
                elif self.path == "/":
                    self.send_response(401)
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()
        with http.server.ThreadingHTTPServer(("127.0.0.1",0), OldDashboard) as srv:
            thread=threading.Thread(target=srv.serve_forever,daemon=True)
            thread.start()
            try:
                success, message = health.probe(srv.server_port)
                self.assertFalse(success)
                self.assertIn("/live-token",message)
            finally:
                srv.shutdown()
                thread.join(timeout=3)

    def test_readiness_checks_new_dashboard_and_auth(self):
        class LiveDashboard(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path == "/healthz":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OK\n")
                elif self.path in ("/","/live-token","/dashboard_live.js"):
                    self.send_response(401)
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()
        with http.server.ThreadingHTTPServer(("127.0.0.1",0), LiveDashboard) as srv:
            thread=threading.Thread(target=srv.serve_forever,daemon=True)
            thread.start()
            try:
                success, message = health.wait_ready(srv.server_port, timeout=2)
                self.assertTrue(success, message)
                self.assertIn("live endpoints",message)
            finally:
                srv.shutdown()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
