import hashlib
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
            runner=lambda cmd: self.commands.append(cmd), tls_checker=lambda slug: True)

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
        self.assertEqual(self.agent.preview("example", "app")["status"], "blocked-app")
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "app")
        for slug in ("cpoint", "worldcom", "../secrets", "a.b", "admin"):
            with self.assertRaises(vhost.VhostError):
                self.agent.preview(slug, "page")
        self.agent.tls_checker = lambda _: False
        with self.assertRaises(vhost.VhostError):
            self.agent.apply("example", "page")
        self.assertEqual(self.commands, [])

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
