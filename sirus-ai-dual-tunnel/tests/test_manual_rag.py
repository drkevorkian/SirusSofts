"""Offline RAG: only approved manuals, exact source pages, revocations, FTS sync."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("manual_rag", ROOT / "gentoo" / "manual_rag.py")
rag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rag)
ingest_path = ROOT.parent / "gentoo-node" / "pdf_ingest.py"
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

    def test_2027_hd11_shop_procedure_not_wiring_or_contents(self):
        shop = "1" * 64
        wiring = "2" * 64
        old = "3" * 64
        wrong_model = "4" * 64
        self.add(shop, "SSV 2027 DEFENDER_TRAXTER HD11 - Shop Manual_SM91Y027_en.pdf",
                 "approved", 1, "TABLE OF CONTENTS\nPRIMARY CLUTCH REMOVAL .......... 214\n"
                 "PRIMARY CLUTCH INSTALLATION .......... 217", page=15)
        con = ingest.connect(self.disk)
        # Real procedure appears far beyond a page's opening safety notes.
        prefix = "Safety notices and introductory discussion.\n" * 45
        procedure = (prefix + "\nPRIMARY CLUTCH REMOVAL\n"
                     "Remove the belt guard. Disconnect the drive clutch bolt. "
                     "Use the approved puller to remove the primary clutch. "
                     "Observe the correct tool safety sequence.")
        con.execute("INSERT INTO excerpts VALUES (?,?,?,?)", (shop, 214, 1, procedure))
        con.execute("UPDATE documents SET excerpts=2 WHERE sha256=?", (shop,))
        con.close()
        self.add(wiring, "SSV 2027 Defender Traxter HD11 Wiring Diagram (2).pdf",
                 "approved", 1, "PRIMARY CLUTCH REMOVAL wiring circuit circuit relay "
                 "PRIMARY CLUTCH REMOVAL wiring circuit circuit relay", page=18)
        self.add(old, "SSV 2026 DEFENDER_TRAXTER HD11 - Shop Manual_2026.pdf",
                 "approved", 1, "PRIMARY CLUTCH REMOVAL. Remove the clutch.", page=200)
        self.add(wrong_model, "SSV 2027 DEFENDER_TRAXTER HD10 - Shop Manual.pdf",
                 "approved", 1, "PRIMARY CLUTCH REMOVAL. Remove the clutch.", page=130)
        rag.sync(self.source, self.index)
        question = ("In the 2027 Can-Am Defender HD11 shop manual, what is the "
                    "primary clutch removal procedure? Give me the exact manual "
                    "title and page numbers.")
        refs = rag.retrieve(question, self.source, self.index)
        self.assertTrue(refs)
        self.assertEqual(refs[0]["sha256"], shop)
        self.assertEqual(refs[0]["page"], 214)
        self.assertTrue(all(row["sha256"] == shop for row in refs))
        # Exact production browser prompt that previously produced zero
        # sources: "Do not substitute wiring diagrams" is a NEGATION,
        # not a positive request for wiring diagrams.
        browser_question = (
            "In the 2027 Can-Am Defender HD11 shop manual, give me the "
            "primary clutch removal procedure step by step. Cite the exact "
            "manual title and page number for every step. Do not substitute "
            "wiring diagrams or another model year. If the retrieved "
            "excerpts are insufficient, identify which steps are missing."
        )
        self.assertTrue(rag._negated_document_mention(
            browser_question, r"\bwiring\s+diagrams?\b"))
        browser_refs = rag.retrieve(browser_question,self.source,self.index)
        self.assertTrue(browser_refs,"Negated wiring diagrams must not eliminate the shop manual")
        self.assertEqual(browser_refs[0]["sha256"],shop)
        self.assertEqual(browser_refs[0]["page"],214)
        self.assertTrue(all(r["sha256"]==shop for r in browser_refs))
        self.assertFalse("wiring" in rag._topic_terms(browser_question))
        browser_ctx = rag.augment(
            [{"role":"user","content":browser_question}],
            browser_question,self.source,self.index)[0]["content"]
        self.assertIn("[M1]",browser_ctx)
        self.assertNotIn("No APPROVED matching manual excerpts",browser_ctx)
        self.assertNotIn("Wiring Diagram (2).pdf",browser_ctx)
        # A positive wiring request still selects wiring documentation.
        wiring_question = "2027 Defender HD11 wiring diagram primary clutch"
        wiring_refs = rag.retrieve(wiring_question,self.source,self.index)
        self.assertTrue(wiring_refs)
        self.assertEqual(wiring_refs[0]["sha256"],wiring)
        # Explicitly comparing two document types means either is allowed.
        both_question = ("Compare 2027 Defender HD11 shop manual and wiring "
                         "diagram for primary clutch removal")
        scope = rag._vehicle_scope(both_question,[
            {"filename":"SSV 2027 DEFENDER_TRAXTER HD11 - Shop Manual_SM91Y027_en.pdf",
             "sha256":shop},
            {"filename":"SSV 2027 Defender Traxter HD11 Wiring Diagram (2).pdf",
             "sha256":wiring},
        ])
        self.assertEqual({r[1] for r in scope},{shop,wiring})
        message = rag.augment([{"role": "user", "content": question}],
                              question, self.source, self.index)[0]["content"]
        self.assertIn("PDF page 214", message)
        self.assertIn("Use the approved puller", message)
        self.assertNotIn("Wiring Diagram", message)
        self.assertNotIn("2026", message)
        # The LLM may still deny access; the response guard must not pretend
        # to know any repair details beyond the actual retrieved sources.
        denial = ("I apologize, but I don't have access to the specific service manual "
                  "for the 2027 Can-Am Defender HD11.")
        corrected = rag.repair_false_no_access(
            denial, [{"role": "system", "content": message},
                     {"role": "user", "content": question}])
        self.assertIn("Shop Manual_SM91Y027_en.pdf", corrected)
        self.assertIn("PDF page 214", corrected)
        self.assertNotIn("don't have access", corrected)
        self.assertEqual(
            rag.repair_false_no_access("Here is an answer based on [M1].",
                                       [{"role": "system", "content": message}]),
            "Here is an answer based on [M1].")

    def test_requested_unavailable_year_must_not_mix_documents(self):
        self.add("5" * 64, "SSV 2026 DEFENDER_TRAXTER HD11 - Shop Manual.pdf",
                 "approved", 1, "PRIMARY CLUTCH REMOVAL Remove the bolt.", page=90)
        rag.sync(self.source,self.index)
        rows = rag.retrieve("2027 Defender HD11 shop manual primary clutch removal",
                            self.source,self.index)
        self.assertEqual(rows, [])

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
