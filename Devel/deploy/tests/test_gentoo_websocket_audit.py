"""Regression test for redacted public WebSocket request status report."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/"ubuntu24"/"audit-gentoo-websocket.py"
spec=spec_from_file_location("audit_gentoo_live",SCRIPT)
audit=module_from_spec(spec)
spec.loader.exec_module(audit)


class WebSocketAuditTests(unittest.TestCase):
    def test_only_websocket_path_and_status_visible(self):
        lines=[
            '192.0.2.5 - - [12/Nov/2026:06:12:00 -0700] '
            '"GET /Gentoo/live HTTP/1.1" 403 12 "-" "-"',
            '192.0.2.99 - - [12/Nov/2026:06:12:30 -0700] '
            '"GET /Gentoo/live-token?snapshot=1 HTTP/2.0" 200 22 "-" "-"',
            '192.0.2.5 - - [12/Nov/2026:06:13:00 -0700] '
            '"GET /Gentoo/live HTTP/1.1" 101 0 "-" "-"',
            '192.0.2.6 - - [12/Nov/2026:06:14:00 -0700] '
            '"GET /Gentoo/dashboard_live.js HTTP/2.0" 200 100 "-" "-"',
        ]
        with tempfile.TemporaryDirectory() as temp:
            filename=Path(temp)/"access.log"
            filename.write_text("\n".join(lines)+"\n")
            matches=audit.scan_log(filename)
            self.assertEqual(matches,[
                ("12/Nov/2026:06:12:00 -0700","403"),
                ("12/Nov/2026:06:13:00 -0700","101"),
            ])
            self.assertNotIn("192.0.2.5",str(matches))
            self.assertNotIn("snapshot=1",str(matches))


if __name__=="__main__":
    unittest.main()
