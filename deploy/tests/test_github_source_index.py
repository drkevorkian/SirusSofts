import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

FILE = Path(__file__).resolve().parents[1] / "ubuntu24" / "github-source-index.py"
spec = importlib.util.spec_from_file_location("github_index", FILE)
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class SourceChecks(unittest.TestCase):
    def test_immutable_commit_and_expected_paths(self):
        sha = "a" * 40
        requested = []
        def fake_api(path, token):
            requested.append(path)
            self.assertEqual(token, "private-test-credential")
            if path.startswith("/commits/"):
                return {"sha": sha}
            if path.startswith("/commits?"):
                return [{"sha": sha, "commit": {"message": "Approved test release"}}]
            body = b"safe fixture"
            return {"type":"file","encoding":"base64",
                    "content":base64.b64encode(body).decode()}
        index = source.refresh("private-test-credential", fake_api, now=123)
        self.assertEqual(index["commit"], sha)
        self.assertEqual(index["checked_at"], 123)
        self.assertEqual(len(index["files"]), 8)
        self.assertEqual(len(index["candidates"]), 1)
        for path in (p for p in requested if p.startswith("/contents/")):
            self.assertIn("?ref=" + sha, path)
        self.assertEqual(index["files"][0]["sha256"], hashlib.sha256(b"safe fixture").hexdigest())

    def test_missing_file_fails_without_partial_write(self):
        def fake_api(path, token):
            if path.startswith("/commits/"):
                return {"sha": "b" * 40}
            if path.startswith("/commits?"):
                return []
            return {"type": "dir"}
        with self.assertRaises(RuntimeError):
            source.refresh("private-test-credential", fake_api)

    def test_previous_revision_selectable_when_complete(self):
        head, old = "a" * 40, "b" * 40
        def api(path, token):
            if path.startswith("/commits/"):
                return {"sha": head}
            if path.startswith("/commits?"):
                return [{"sha": head, "commit": {"message": "HEAD"}},
                        {"sha": old, "commit": {"message": "Known stable release"}}]
            return {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(path.encode()).decode()}
        index = source.refresh("private-test-credential", api, now=456)
        self.assertEqual([x["commit"] for x in index["candidates"]], [head, old])
        self.assertEqual(index["candidates"][1]["message"], "Known stable release")
        self.assertNotEqual(index["candidates"][0]["files"][0]["sha256"],
                            index["candidates"][1]["files"][0]["sha256"])

    def test_incomplete_older_revision_is_skipped(self):
        head, old = "a" * 40, "b" * 40
        def api(path, token):
            if path.startswith("/commits/"):
                return {"sha": head}
            if path.startswith("/commits?"):
                return [{"sha": head}, {"sha": old}]
            if "ref=" + old in path:
                return {"type": "dir"}
            return {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(b"ok").decode()}
        index = source.refresh("private-test-credential", api, now=456)
        self.assertEqual([x["commit"] for x in index["candidates"]], [head])

    def test_atomic_private_index(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "index.json"
            data = {"commit": "c" * 40, "files":[]}
            source.write_index(data, target)
            self.assertEqual(json.loads(target.read_text()), data)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
