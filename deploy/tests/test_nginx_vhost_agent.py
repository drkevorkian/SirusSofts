import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nginx_vhost_agent as vhost


class VhostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        p = Path(self.temp.name)
        self.root = p / "CMS"
        (self.root / "app").mkdir(parents=True)
        (self.root / "index.php").write_text("CMS", encoding="utf-8")
        (self.root / "app/core.php").write_text("CORE", encoding="utf-8")
        self.commands = []
        self.agent = vhost.NginxVhosts(
            root=self.root, managed=p / "managed", sites=p / "sites",
            cert=p / "fullchain.pem", key=p / "privkey.pem",
            runner=lambda cmd: self.commands.append(cmd), tls_checker=lambda slug: True,
            app_routes=p / "apps.json", fragments=p / "routes",
            upstream_checker=lambda slug, port: True)

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_and_safe_apply_rollback(self):
        preview = self.agent.preview("example", "page")
        self.assertTrue(preview["ready"])
        self.assertIn("sirussofts.com", preview["secondary_subdomain_url"])
        self.assertEqual(self.agent.apply("example", "page")["status"], "applied")
        self.assertEqual(self.agent.apply("example", "page")["status"], "already-managed")
        self.assertEqual(self.commands[:2], [["nginx", "-t"], ["systemctl", "reload", "nginx"]])
        self.assertEqual(self.agent.rollback("example", "page")["status"], "rolled-back")
        self.assertEqual(self.agent._state(), {})

    def test_reject_apps_protected_slugs_and_missing_tls(self):
        self.assertEqual(self.agent.preview("example", "app")["status"], "blocked")
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "app")
        for slug in ("cpoint", "worldcom", "../secrets", "a.b", "admin"):
            with self.assertRaises(vhost.VhostError):
                self.agent.preview(slug, "page")
        self.agent.tls_checker = lambda _: False
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "page")
        self.assertEqual(self.commands, [])

    def allow_app(self, slug="example", port=23081, enabled=True):
        self.agent.app_routes.write_text(json.dumps({
            "schema_version": 1,
            "applications": {slug: {"enabled": enabled, "host": "127.0.0.1", "port": port}}
        }), encoding="utf-8")
        self.agent.app_routes.chmod(0o600)

    def test_tls_public_key_chain_and_permissions_checked(self):
        cert = self.agent.cert
        key = self.agent.key
        cert.write_text("test certificate")
        key.write_text("test key")
        key.chmod(0o600)
        self.agent.tls_checker = None
        commands = []
        def trust(command):
            commands.append(command)
            if "-pubkey" in command:
                return "matching public key\n"
            if "-pubout" in command:
                return "matching public key\n"
            return ""
        self.agent.runner = trust
        self.assertTrue(self.agent._tls_valid("example"))
        self.assertTrue(any("verify" in cmd for cmd in commands))
        self.assertTrue(any("-checkhost" in cmd for cmd in commands))
        self.agent.runner = lambda cmd: "wrong" if "-pubout" in cmd else "matching public key"
        self.assertFalse(self.agent._tls_valid("example"))
        key.chmod(0o644)
        self.assertFalse(self.agent._tls_valid("example"))

    def test_app_routes_both_domains_and_rollback(self):
        self.allow_app()
        preview = self.agent.preview("example", "app")
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["kind"], "app")
        self.assertEqual(self.agent.apply("example", "app")["status"], "applied")
        site = self.agent._file("example").read_text()
        fragment = self.agent._route_file("example").read_text()
        self.assertIn("example.sirussofts.org example.sirussofts.com", site)
        self.assertIn("proxy_pass http://127.0.0.1:23081;", site)
        self.assertIn("location ^~ /example/", fragment)
        self.assertIn("proxy_pass http://127.0.0.1:23081/;", fragment)
        self.assertEqual(self.agent.apply("example", "app")["status"], "already-managed")
        self.assertEqual(self.agent.rollback("example", "app")["status"], "rolled-back")
        self.assertFalse(self.agent._route_file("example").exists())
        self.assertFalse(self.agent._file("example").exists())

    def test_app_requires_explicit_healthy_backend(self):
        self.allow_app(enabled=False)
        self.assertFalse(self.agent.preview("example", "app")["ready"])
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "app")
        self.allow_app(enabled=True)
        self.agent.upstream_checker = lambda slug, port: False
        self.assertFalse(self.agent.preview("example", "app")["ready"])
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "app")
        self.assertFalse(self.agent._file("example").exists())

    def test_app_rejects_tamper_and_config_switch(self):
        self.allow_app()
        self.agent.apply("example", "app")
        self.agent._route_file("example").write_text("modified")
        with self.assertRaises(vhost.VhostError):
            self.agent.rollback("example", "app")
        self.allow_app(port=24082)
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "app")

    def test_app_cannot_route_protected_projects(self):
        self.allow_app(slug="cpoint")
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("cpoint", "app")
        self.allow_app(slug="example", port=80)
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "app")
        self.allow_app()
        self.agent.app_routes.chmod(0o666)
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "app")

    def test_failed_app_nginx_test_removes_both_routes(self):
        self.allow_app()
        def fail(command):
            if command == ["nginx", "-t"]:
                raise vhost.VhostError("Bad nginx config")
        self.agent.runner = fail
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "app")
        self.assertFalse(self.agent._file("example").exists())
        self.assertFalse(self.agent._route_file("example").exists())

    def test_refuses_modified_config(self):
        self.agent.apply("example", "page")
        config = self.agent._file("example")
        config.write_text("altered", encoding="utf-8")
        with self.assertRaises(vhost.VhostError):
            self.agent.rollback("example", "page")
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "page")

    def test_failed_nginx_test_reverts_new_file(self):
        def fail_on_test(command):
            if command == ["nginx", "-t"]:
                raise vhost.VhostError("nginx test failed")
        self.agent.runner = fail_on_test
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "page")
        self.assertFalse(self.agent._file("example").exists())


if __name__ == "__main__":
    unittest.main()
