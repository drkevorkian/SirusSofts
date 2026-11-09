"""Security-focused tests for the private read-only source gateway."""
import base64
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1] / "ubuntu24" / "github-source-browser.py"
spec=importlib.util.spec_from_file_location("sirus_source_browser",path)
source=importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)

class BrowserTests(unittest.TestCase):
    def test_source_path_validation(self):
        for path in ("","README.md",".github/workflows/test.yml","src/my file.py"):
            self.assertTrue(source.safe_path(path),path)
        for path in ("../etc/passwd","/root","a//b","a/../b","a\\b","a/%00",
                     "a/\x00","a/","a/é", "a"*500):
            self.assertFalse(source.safe_path(path),path)

    def test_read_text_and_escape_path(self):
        wanted=[]
        def fetch(url,token):
            self.assertEqual(token,"private-test-token")
            wanted.append(url)
            return {"type":"file","size":15,"encoding":"base64",
                    "content":base64.b64encode(b"print('hello')\n").decode()}
        r=source.api_request("owner/MyRepo","feature/code","src/my file.py",
                             "private-test-token",fetch)
        self.assertTrue(r["ok"])
        self.assertEqual(r["kind"],"file")
        self.assertEqual(r["content"],"print('hello')\n")
        self.assertIn("src/my%20file.py",wanted[0])
        self.assertIn("feature%2Fcode",wanted[0])

    def test_directory_records_are_canonical(self):
        def fetch(url,token):
            return [{"name":"src","path":"src","type":"dir","size":0},
                    {"name":"README.md","path":"README.md","type":"file","size":20},
                    {"name":"symlink","path":"symlink","type":"symlink","size":0},
                    {"name":"notmine","path":"../escape","type":"file","size":2}]
        x=source.api_request("owner/repo","main","", "token",fetch)
        self.assertEqual([e["name"] for e in x["entries"]],["src","README.md"])

    def test_binary_and_big_file_rejected(self):
        def binary(url,token):
            return {"type":"file","size":3,"encoding":"base64",
                    "content":base64.b64encode(b"a\x00b").decode()}
        with self.assertRaisesRegex(ValueError,"Binary"):
            source.api_request("owner/repo","main","a","token",binary)
        def huge(url,token):
            return {"type":"file","size":source.MAX_FILE+1,"encoding":"base64","content":""}
        with self.assertRaisesRegex(ValueError,"large"):
            source.api_request("owner/repo","main","a","token",huge)
        with self.assertRaises(ValueError):
            source.api_request("owner/repo","main","../../private","token",binary)

if __name__=="__main__":unittest.main()
