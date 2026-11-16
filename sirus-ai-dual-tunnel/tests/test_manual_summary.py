import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("manual_summary",ROOT/"gentoo/manual_summary.py")
lib=importlib.util.module_from_spec(spec);spec.loader.exec_module(lib)
class ManualSummaryTests(unittest.TestCase):
    def sample(self):
        text="HD11 belt specification requires original PDF diagram."
        return {"kind":"sirus-ai-document-v1","reviewed":False,"profile":"mechanics",
                "text":text,"title":"HD11 page 5","source":"","pdf_page":5,
                "manual_name":"HD11.pdf","manual_sha256":"a"*64,
                "sha256":hashlib.sha256(text.encode()).hexdigest()}
    def test_local_source_attribution(self):
        with tempfile.TemporaryDirectory() as td:
            inp=Path(td,"draft.jsonl");out=Path(td,"summary.md")
            inp.write_text(json.dumps(self.sample())+"\n")
            got=[]
            def fake(messages,backend):
                got.append((messages,backend))
                return "Check belt setup [p5]; diagram review required."
            count=lib.write_summary(str(inp),str(out),"amd",invoke=fake)
            self.assertEqual(count,1)
            self.assertEqual(got[0][1],"amd")
            self.assertIn("[PDF page 5]",got[0][0][1]["content"])
            self.assertIn("not verified",out.read_text())
            self.assertIn("p5",out.read_text())
            with self.assertRaises(FileExistsError):
                lib.write_summary(str(inp),str(out),"amd",invoke=fake)
    def test_tampered_or_approved_not_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            inp=Path(td,"draft.jsonl");row=self.sample()
            row["reviewed"]=True
            inp.write_text(json.dumps(row))
            with self.assertRaises(ValueError):lib.draft_records(str(inp))
            row["reviewed"]=False;row["sha256"]="x"*64
            inp.write_text(json.dumps(row))
            with self.assertRaises(ValueError):lib.draft_records(str(inp))
if __name__=="__main__":unittest.main()
