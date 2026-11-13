import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sirus_deploy as sd

class SirusDeployTests(unittest.TestCase):
    def test_github_hmac_reference(self):
        expected = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
        self.assertTrue(sd.authentic(b"Hello, World!", expected, b"It's a Secret to Everybody"))
        self.assertFalse(sd.authentic(b"changed", expected, b"It's a Secret to Everybody"))
        self.assertFalse(sd.authentic(b"Hello, World!", "", b"It's a Secret to Everybody"))

    def test_path_traversal_rejection(self):
        for p in ("", "../outside", "/etc", "public/../.env", "a//b", "a\\b"):
            with self.subTest(path=p):
                with self.assertRaises(ValueError):
                    sd.safe_source(p)
        self.assertEqual(str(sd.safe_source("server/public")), "server/public")

    def test_example_config_starts_disabled(self):
        config_path = Path(__file__).resolve().parents[1] / "projects.example.json"
        cfg = sd.load_projects(config_path)
        self.assertEqual(len(cfg["projects"]), 2)
        self.assertTrue(all(not p["enabled"] for p in cfg["projects"].values()))

    def test_signed_push_scoping_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            config = {"sites_root": str(p / "sites"), "projects": {
                "drkevorkian/SirusSofts": {
                    "enabled": True, "slug": "cms", "branch": "main",
                    "transport": "https", "source_subdir": "public"
                }}}
            (p / "config.json").write_text(json.dumps(config))
            (p / "secret").write_text("x" * 64)
            service = sd.Service(p / "config.json", p / "secret", p / "state")
            event_id = "12345678-1234-1234-1234-123456789012"
            def payload(branch):
                return json.dumps({
                    "repository": {"full_name": "drkevorkian/SirusSofts"},
                    "ref": "refs/heads/" + branch,
                    "after": "a" * 40
                }).encode()
            self.assertEqual(service.accept("push", event_id, payload("other"))[1]["status"], "ignored")
            self.assertEqual(service.accept("push", event_id, payload("main"))[1]["status"], "queued")
            self.assertEqual(service.accept("push", event_id, payload("main"))[1]["status"], "duplicate")
            self.assertEqual(service.accept("issues", event_id, b"{}")[1]["status"], "ignored")

if __name__ == "__main__":
    unittest.main()
