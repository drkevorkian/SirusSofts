import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"gentoo"))
spec=importlib.util.spec_from_file_location("web_source_summary",ROOT/"gentoo/web_source_summary.py")
lib=importlib.util.module_from_spec(spec);spec.loader.exec_module(lib)
class WebSourceTests(unittest.TestCase):
    def test_dns_ssrf(self):
        def reply(ip):
            return lambda *args:[(2,1,6,"",(ip,443))]
        with self.assertRaises(ValueError):lib.public_ipv4("example.com",reply("127.0.0.1"))
        with self.assertRaises(ValueError):lib.public_ipv4("example.com",reply("192.168.1.1"))
        self.assertEqual(lib.public_ipv4("example.com",reply("8.8.8.8")),"8.8.8.8")
    def test_draft_summaries_with_provenance(self):
        calls=[]
        def fetch(u):
            calls.append(u)
            return u,"Manual technical support: original guidance."
        def model(msg,worker):
            self.assertEqual(worker,"amd")
            self.assertIn("UNTRUSTED WEB SOURCE",msg[-1]["content"])
            return "Verify manual details at source."
        data=lib.research_summary(["https://example.org/manual"],fetch,model,"amd")
        self.assertTrue(data["unreviewed"])
        self.assertFalse(data["summaries"][0]["reviewed"])
        self.assertEqual(data["summaries"][0]["url"],"https://example.org/manual")
        self.assertEqual(calls,["https://example.org/manual"])
    def test_results_top_and_url_filter(self):
        urls=lib.allowed_results({"results":[{"url":"http://localhost/private"},
                          {"url":"https://nasa.gov/a"},
                          {"url":"https://www.nasa.gov/a#frag"},
                          {"url":"https://brp.com/manual"}]},2)
        self.assertEqual(urls,["https://nasa.gov/a","https://brp.com/manual"])
        with self.assertRaises(ValueError):
            lib.allowed_results({"results":[]},5000)
    def test_extract_text_ignores_scripts(self):
        txt=lib.extract_web_text("<html><p>Hello</p><script>alert('x')</script><h2>Safety</h2></html>")
        self.assertIn("Hello",txt)
        self.assertIn("Safety",txt)
        self.assertNotIn("alert",txt)
if __name__=="__main__":unittest.main()
