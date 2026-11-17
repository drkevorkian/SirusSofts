"""Offline RAG: only approved manuals, exact source pages, revocations, FTS sync."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("manual_rag", ROOT / "manual_rag.py")
rag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rag)
ingest_path = ROOT.parents[1] / "gentoo-node" / "pdf_ingest.py"
spec2 = importlib.util.spec_from_file_location("pdf_ingest", ingest_path)
ingest = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(ingest)


class ManualRagTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.disk = Path(self.dir.name) / "storage"
        (self.disk / "databases").mkdir(parents=True)
        (self.disk / "rag").mkdir()
        self.source = self.disk / "databases" / "pdf_ingest.sqlite3"
        self.index = self.disk / "rag" / "manual_fts.sqlite3"
        con = ingest.connect(self.disk)
        ingest.schema(con)
        con.close()
        self.mounted = mock.patch.object(rag.os.path, "ismount", return_value=True)
        self.mounted.start()
        self.addCleanup(self.mounted.stop)

    def add(self, sha, name, status, reviewed, text, page=1):
        con = ingest.connect(self.disk)
        con.execute("""INSERT INTO documents(
          sha256,filename,object_rel,bytes,status,reviewed,pages,excerpts,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (sha,name,"manuals/objects/"+sha+".pdf",100,status,reviewed,
           30,1,int(time.time()),int(time.time())))
        con.execute("INSERT INTO excerpts(sha256,page,part,text) VALUES (?,?,?,?)",
                    (sha,page,1,text))
        con.close()

    def test_only_approved_and_page_attributed(self):
        a,b,c="a"*64,"b"*64,"c"*64
        self.add(a,"2025 Defender HD11 Manual.pdf","approved",1,
                 "The primary clutch bolt is tightened to 75 Nm.",31)
        self.add(b,"2024 Defender HD10 Manual.pdf","ready",0,
                 "The primary clutch bolt is tightened to 99 Nm.",25)
        self.add(c,"2023 HD10 DRAFT.pdf","approved",0,
                 "The primary clutch bolt is tightened to 105 Nm.",8)
        report=rag.sync(self.source,self.index)
        self.assertEqual(report["approved_source"],1)
        refs=rag.retrieve("2025 HD11 primary clutch bolt torque",self.source,self.index)
        self.assertEqual(len(refs),1)
        self.assertEqual(refs[0]["page"],31)
        self.assertEqual(refs[0]["sha256"],a)
        messages=rag.augment([{"role":"user","content":"What is HD11 primary clutch bolt torque?"}],
                             "2025 HD11 primary clutch bolt torque",self.source,self.index)
        self.assertIn("[M1]",messages[0]["content"])
        self.assertIn("page 31",messages[0]["content"])
        self.assertNotIn("99 Nm",messages[0]["content"])

    def test_revoked_material_inaccessible_before_resync(self):
        a="e"*64
        self.add(a,"HD11 Manual.pdf","approved",1,"Clutch adjustment measurement 12 mm",5)
        rag.sync(self.source,self.index)
        self.assertTrue(rag.retrieve("clutch adjustment measurement",self.source,self.index))
        con=ingest.connect(self.disk)
        con.execute("UPDATE documents SET status='ready',reviewed=0 WHERE sha256=?", (a,))
        con.close()
        self.assertEqual(rag.retrieve("clutch adjustment measurement",self.source,self.index),[])
        updated=rag.sync(self.source,self.index)
        self.assertEqual(updated["removed"],1)

    def test_incremental_and_safe_query(self):
        a="f"*64
        self.add(a,"HD10 Manual.pdf","approved",1,"Gearbox detent torque 41 newton meters",9)
        first=rag.sync(self.source,self.index)
        self.assertEqual(first["new_or_changed"],1)
        self.assertEqual(rag.sync(self.source,self.index)["new_or_changed"],0)
        self.assertTrue(rag.retrieve("gearbox torque",self.source,self.index))
        self.assertEqual(rag.retrieve('"; DROP TABLE documents; --',
                                      self.source,self.index),[])
        self.assertTrue(rag.retrieve("gearbox torque",self.source,self.index))
        self.assertFalse(rag.retrieve("nitrogen reactor physics",self.source,self.index))

    def test_fail_closed_offline(self):
        with self.assertRaises(rag.RetrievalUnavailable):
            rag.retrieve("torque",self.source,self.index)
        self.mounted.stop()
        try:
            with self.assertRaises(rag.RetrievalUnavailable):
                rag.sync(self.source,self.index)
        finally:
            self.mounted.start()


if __name__ == "__main__":
    unittest.main()
