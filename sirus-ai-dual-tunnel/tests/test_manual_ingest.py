import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("manual_ingest",ROOT/"gentoo/manual_ingest.py")
ing=importlib.util.module_from_spec(spec);spec.loader.exec_module(ing)

class ManualIngestTests(unittest.TestCase):
    def test_page_chunks_and_review(self):
        with tempfile.TemporaryDirectory() as td:
            pdf=Path(td,"HD11 CAN-AM.pdf")
            pdf.write_bytes(b"%PDF-1.7\n"+"dummy bytes".encode()*5)
            result=Path(td,"draft.jsonl")
            n,length=ing.create_output(str(pdf),"mechanics",str(result),
                 1,2,extract=lambda name,page:("Clutch torque specs. "*230 if page==1 else "HD11 drive belt."),
                 count=2)
            self.assertGreaterEqual(n,2)
            rows=[json.loads(line) for line in result.read_text().splitlines()]
            self.assertEqual(n,len(rows))
            self.assertEqual(rows[-1]["pdf_page"],2)
            self.assertTrue(all(not row["reviewed"] for row in rows))
            self.assertTrue(all(hashlib.sha256(r["text"].encode()).hexdigest()==r["sha256"] for r in rows))
            self.assertEqual(os.stat(result).st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):
                ing.create_output(str(pdf),"mechanics",str(result),count=1,
                                  extract=lambda name,page:"again")
    def test_rejects_symlink_and_non_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td,"bad.pdf");p.write_bytes(b"<html>not a pdf"+b"x"*50)
            with self.assertRaises(ValueError):ing.pdf_path(str(p))
            p.write_bytes(b"%PDF-1.5"+b"x"*50)
            link=Path(td,"link.pdf");link.symlink_to(p)
            with self.assertRaises(ValueError):ing.pdf_path(str(link))
            with self.assertRaises(ValueError):
                ing.create_output(str(p),"invalid",str(Path(td,"bad.jsonl")),
                                  count=1,extract=lambda n,p:"hello")
    def test_text_splitting(self):
        chunks=ing.split_text("Torque specification "*500,200)
        self.assertGreater(len(chunks),2)
        self.assertTrue(all(len(part)<=200 for part in chunks))
if __name__=="__main__":unittest.main()
