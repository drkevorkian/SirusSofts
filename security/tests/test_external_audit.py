"""Offline tests for the owner-authorized public HTTPS audit."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "security" / "external_audit.py"
spec = spec_from_file_location("sirus_external_security_audit", SOURCE)
audit = module_from_spec(spec)
import sys
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


class SecurityAuditTests(unittest.TestCase):
    def test_every_expected_route_denies_or_allows_as_planned(self):
        self.assertEqual(len(audit.ROUTES), 11)
        self.assertEqual(len({route for _,route,_ in audit.ROUTES}), 11)
        for name, route, statuses in audit.ROUTES:
            self.assertTrue(route.startswith("/"))
            self.assertFalse(route.startswith("//"))
            result = audit.evaluate(name, route, statuses, statuses[0],
                                    b"WebSocket authentication rejected")
            self.assertTrue(result.pass_check, (name, result))

    def test_sensitive_routes_cannot_return_public_success(self):
        for name, route, statuses in audit.ROUTES:
            if "private" in name or "gentoo" in name:
                self.assertFalse(audit.evaluate(name, route, statuses, 200).pass_check)

    def test_websocket_403_must_prove_backend_signature_validation(self):
        self.assertFalse(audit.evaluate("gentoo-invalid-websocket",
            "/Gentoo/live", (403,), 403, b"Forbidden").pass_check)
        self.assertTrue(audit.evaluate("gentoo-invalid-websocket",
            "/Gentoo/live", (403,), 403,
            b"WebSocket authentication rejected").pass_check)

    def test_audit_is_hardcoded_to_owned_domain(self):
        self.assertEqual(audit.DOMAIN, "sirussofts.com")
        self.assertEqual(audit.REPORT.as_posix(),
                         "security-results/external-audit.json")


if __name__ == "__main__":
    unittest.main()
