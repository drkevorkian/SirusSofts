import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("web_research",ROOT/"gentoo/web_research.py")
lib=importlib.util.module_from_spec(spec);spec.loader.exec_module(lib)
class WebResearchTests(unittest.TestCase):
    def test_depth(self):
        self.assertEqual(lib.valid_depth(50),50)
        self.assertEqual(lib.valid_depth(5000),5000)
        for bad in [0,5001,True,"50",-1]:
            with self.assertRaises(ValueError):lib.valid_depth(bad)
    def test_url_and_endpoint(self):
        for bad in ["http://localhost/","https://localhost/","https://127.0.0.1/",
                    "https://192.168.1.1/private","file:///etc/passwd",
                    "https://admin:pass@site.org","https://x.internal/a","https://host.test/"]:
            self.assertIsNone(lib.clean_url(bad),bad)
        self.assertEqual(lib.clean_url("https://www.nasa.gov/b?utm_source=t&id=2#part"),
                         "https://nasa.gov/b?id=2")
        for bad in ["https://searx.org/search","http://192.168.1.1:8888/search",
                    "http://127.0.0.1:8888/private","http://127.0.0.1:8888/search?x=1"]:
            with self.assertRaises(ValueError):lib.endpoint_guard(bad)
    def test_rank_engines_duplicate_and_budget(self):
        def fake(query,engines,page):
            if page==1:return {"results":[
                {"url":"https://a.com/a","title":"gamma topic"},
                {"url":"https://nasa.gov/a","title":"gamma guide","engines":["google"]},
                {"url":"https://www.nasa.gov/a?utm_source=track","title":"gamma guide","engines":["bing"]}],
                "unresponsive_engines":[["yahoo","timeout"]]}
            if page==2:return {"results":[{"url":"https://mit.edu/a","title":"gamma lecture"}]}
            return {"results":[]}
        out=lib.discover("gamma science",50,("google","bing","yahoo"),fake,0,6,"physics")
        self.assertEqual(out["discovered"],3)
        self.assertEqual(out["results"][0]["url"],"https://nasa.gov/a")
        self.assertEqual(out["results"][1]["url"],"https://mit.edu/a")
        self.assertEqual(out["results"][0]["engines"],["bing","google"])
        self.assertEqual(out["unresponsive_engines"],["yahoo"])
        large=lib.discover("service manual",5000,("google",),
                           lambda *args:{"results":[]},0,5)
        self.assertEqual(large["requested_depth"],5000)
        self.assertEqual(large["pages_queried"],3)
if __name__=="__main__":unittest.main()
