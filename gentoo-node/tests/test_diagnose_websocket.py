"""Integration coverage for safe, read-only public WebSocket diagnostics."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dashboard
import dashboard_live

spec = importlib.util.spec_from_file_location("diagnose_websocket", ROOT / "diagnose-websocket.py")
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class PublicDiagnosticTests(unittest.TestCase):
    def test_local_authenticated_upgrade_and_invalid_auth(self):
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.secret = dashboard.make_hash("safe-fixture-password-12345")
        server.config = {"node_id": "gentoo-home"}
        server.db = "/not-needed"
        server.live_state = dashboard_live.LiveState()
        server.live_slots = threading.BoundedSemaphore(4)
        server.live_state.publish({
            "type": "snapshot", "metrics": {"cpu_pct": 30},
            "hardware": {"node_id": "gentoo-home"}, "jobs": [], "graph": []
        })
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            token = dashboard_live.new_token(server.secret)
            ok, detail = diagnostic.probe(
                "127.0.0.1", server.server_port, "/live", token, encrypted=False
            )
            self.assertTrue(ok, detail)
            self.assertIn("HTTP 101", detail)
            ok, detail = diagnostic.probe(
                "127.0.0.1", server.server_port, "/live", "invalid-token",
                encrypted=False
            )
            self.assertFalse(ok)
            self.assertIn("HTTP 403", detail)
            self.assertNotIn(token, detail)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_private_record_permissions_required(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dashboard.auth"
            path.write_text("pbkdf2-sha256:safe-demo", encoding="utf8")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                diagnostic.load_auth(path)
            path.chmod(0o600)
            self.assertEqual(diagnostic.load_auth(path), "pbkdf2-sha256:safe-demo")
            other = Path(temp) / "linked"
            other.symlink_to(path)
            with self.assertRaises(ValueError):
                diagnostic.load_auth(other)


if __name__ == "__main__":
    unittest.main()
