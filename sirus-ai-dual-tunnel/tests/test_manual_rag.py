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

    def test_broad_2026_hd10_wire_diagram_discovers_approved_image_manuals(self):
        # Neither approved PDF has a searchable wiring diagram body: both
        # are visual drawings, so source discovery MUST use titles.
        commander = "6" * 64
        defender = "7" * 64
        shop = "8" * 64
        wrong_year = "9" * 64
        wrong_engine = "a" * 64
        unreviewed = "b" * 64
        self.add(commander, "SSV 2026 COMMANDER HD10 - Wire Diagram.pdf",
                 "approved", 1, "Legend and image references only", page=1)
        self.add(defender, "SSV 2026 DEFENDER_TRAXTER HD10 Electrical Schematics.pdf",
                 "approved", 1, "A raster circuit drawing with unlabeled image", page=1)
        self.add(shop, "SSV 2026 DEFENDER_TRAXTER HD10 - Shop Manual.pdf",
                 "approved", 1, "Unrelated clutch information", page=12)
        self.add(wrong_year, "SSV 2025 COMMANDER HD10 - Wiring Diagram.pdf",
                 "approved", 1, "Diagram legend", page=1)
        self.add(wrong_engine, "SSV 2026 DEFENDER HD11 - Wiring Diagram.pdf",
                 "approved", 1, "Electrical connections", page=1)
        self.add(unreviewed, "SSV 2026 DEFENDER HD10 - Wiring Diagram DRAFT.pdf",
                 "ready", 0, "Electrical drawing", page=1)
        rag.sync(self.source,self.index)
        for query in ("2026 HD10 wire diagram", "2026 HD10 wiring diagrams",
                      "Show me 2026 HD10 electrical schematics",
                      "I need the 2026 HD10 wire diagram PDF",
                      "Where can I find the 2026 HD10 wiring diagram?",
                      "Can you provide a 2026 HD-10 wire diagram?"):
            self.assertTrue(rag._broad_wiring_lookup(query), query)
            refs = rag.retrieve(query,self.source,self.index)
            self.assertEqual({r["sha256"] for r in refs},
                             {commander,defender}, query)
            self.assertTrue(all(r["reference_only"] for r in refs),query)
            self.assertTrue(all(r["page"] == 1 for r in refs),query)
            ctx = rag.augment([{"role":"user","content":query}],
                              query,self.source,self.index)[0]["content"]
            self.assertIn("CATALOG MATCH ONLY",ctx)
            self.assertIn("NOT a verified diagram page",ctx)
            self.assertIn("PDF page 1",ctx)
            self.assertNotIn("2025 COMMANDER",ctx)
            self.assertNotIn("HD11",ctx)
            self.assertNotIn("DRAFT",ctx)
            self.assertNotIn("Shop Manual.pdf",ctx)
            sources = rag.include_source_manifest(
                "Found matching reference manuals",[
                    {"role":"system","content":ctx}
                ])
            self.assertIn("[[SIRUS_MANUAL_SOURCES_START]]",sources)
            self.assertIn(commander,sources)
            self.assertIn(defender,sources)

    def test_broad_wire_shop_fallback_without_dedicated_diagram(self):
        shop = "c" * 64
        other = "d" * 64
        self.add(shop,"SSV 2026 COMMANDER HD10 - Shop Manual.pdf",
                 "approved",1,"Introduction to engine service",page=2)
        self.add(other,"SSV 2026 COMMANDER HD11 - Wiring Diagram.pdf",
                 "approved",1,"Electrical circuit illustration",page=1)
        rag.sync(self.source,self.index)
        refs=rag.retrieve("2026 Commander HD10 wire diagram",self.source,self.index)
        self.assertEqual(len(refs),1)
        self.assertEqual(refs[0]["sha256"],shop)
        self.assertTrue(refs[0]["reference_only"])
        context=rag.augment([{"role":"user","content":"2026 Commander HD10 wire diagram"}],
                            "2026 Commander HD10 wire diagram",
                            self.source,self.index)[0]["content"]
        self.assertIn("NOT a verified diagram page",context)
        # Targeted shop procedure continues to require actual page matches.
        self.assertEqual(rag.retrieve("2026 Commander HD10 shop manual primary clutch removal",
                                      self.source,self.index),[])

    def test_specific_wire_diagram_uses_extracted_electrical_page(self):
        wire="e" * 64
        shop="f" * 64
        self.add(wire,"SSV 2026 Defender HD10 - Wire Diagram.pdf",
                 "approved",1,"IGNITION SWITCH. Wiring connector color and relay",page=31)
        self.add(shop,"SSV 2026 Defender HD10 - Shop Manual.pdf",
                 "approved",1,"IGNITION SWITCH unrelated mechanical maintenance",page=46)
        rag.sync(self.source,self.index)
        question="2026 Defender HD10 wire diagram ignition switch"
        self.assertFalse(rag._broad_wiring_lookup(question))
        refs=rag.retrieve(question,self.source,self.index)
        self.assertTrue(refs)
        self.assertEqual(refs[0]["sha256"],wire)
        self.assertEqual(refs[0]["page"],31)
        self.assertNotIn("reference_only",refs[0])

    def test_broad_diagram_prefers_text_identified_page_when_present(self):
        pdf="e"*64
        self.add(pdf,"SSV 2026 DEFENDER HD10 Wiring Diagram.pdf",
                 "approved",1,"Generic note about vehicle electronics",page=1)
        with ingest.connect(self.disk) as db:
            db.execute("INSERT INTO excerpts VALUES (?,?,?,?)",
                       (pdf,84,1,"WIRING DIAGRAM: accessory connector harness and relay"))
            db.execute("UPDATE documents SET excerpts=2 WHERE sha256=?",(pdf,))
        rag.sync(self.source,self.index)
        refs=rag.retrieve("2026 Defender HD10 wire diagram",self.source,self.index)
        self.assertTrue(refs)
        self.assertEqual(refs[0]["page"],84)
        self.assertFalse(refs[0]["reference_only"])
        self.assertEqual(refs[0]["sha256"],pdf)

    def test_negative_wire_diagram_phrase_does_not_restrict_shop_request(self):
        shop="a"*64
        wiring="b"*64
        self.add(shop,"2027 Defender HD11 Shop Manual.pdf",
                 "approved",1,"PRIMARY CLUTCH REMOVAL. Remove the bolt.",page=80)
        self.add(wiring,"2027 Defender HD11 Wire Diagram.pdf",
                 "approved",1,"PRIMARY CLUTCH REMOVAL electrical circuit.",page=10)
        rag.sync(self.source,self.index)
        question="2027 Defender HD11 shop manual primary clutch removal; do not substitute a wire diagram"
        refs=rag.retrieve(question,self.source,self.index)
        self.assertTrue(refs)
        self.assertTrue(all(x["sha256"]==shop for x in refs))

    def test_real_bpr_2026_titles_broad_hd10_wiring_candidates(self):
        # Real Gentoo filenames found in the 2026 catalog do not mention HD10.
        # The exact PDF titles are evidence of year/platform, not engine fit.
        commander = "6" * 64
        defender = "7" * 64
        mav = "8" * 64
        named_hd11 = "9" * 64
        x3 = "a" * 64
        roadster = "b" * 64
        wrong_year = "c" * 64
        self.add(commander, "SSV 2026 Commander- Wiring Diagram_219101142_WD91Y026_en.pdf",
                 "approved", 1, "TABLE OF CONTENTS", page=1)
        self.add(defender, "SSV 2026 DEFENDER - Wiring Diagram_219101142_WD91Y026_en.pdf",
                 "approved", 1, "BRP vehicle electrical overview", page=1)
        self.add(mav, "SSV 2026 MAV Trail_Sport - Wir Diagram_219101142_WD91Y026_en.pdf",
                 "approved", 1, "Electrical component reference illustration", page=1)
        self.add(named_hd11, "SSV 2026 DEFENDER_TRAXTER HD11- Wiring Diagram_219101142_WD91Y026_en.pdf",
                 "approved", 1, "WIRING DIAGRAM HD11 vehicle only", page=1)
        self.add(x3, "SSV 2026 Maverick X3 - Wiring Diagram_219101142_WD91Y026_en.pdf",
                 "approved", 1, "WIRING DIAGRAM X3", page=1)
        self.add(roadster, "ROADSTER 2026 F3 - Wiring Diagram_219101147_WD61Y026_en.pdf",
                 "approved", 1, "WIRING DIAGRAM F3", page=1)
        self.add(wrong_year, "SSV 2025 DEFENDER - Wiring Diagram_219101142_WD91Y025_en.pdf",
                 "approved", 1, "WIRING DIAGRAM 2025", page=1)
        rag.sync(self.source, self.index)
        query = "2026 HD10 wire diagram"
        scope = rag._vehicle_scope(query, [
            {"filename":title, "sha256":sha} for sha,title in [
                (commander,"SSV 2026 Commander- Wiring Diagram_219101142_WD91Y026_en.pdf"),
                (defender,"SSV 2026 DEFENDER - Wiring Diagram_219101142_WD91Y026_en.pdf"),
                (mav,"SSV 2026 MAV Trail_Sport - Wir Diagram_219101142_WD91Y026_en.pdf"),
                (named_hd11,"SSV 2026 DEFENDER_TRAXTER HD11- Wiring Diagram_219101142_WD91Y026_en.pdf"),
            ]])
        self.assertEqual({r[1] for r in scope},{commander,defender,mav})
        refs = rag.retrieve(query,self.source,self.index)
        self.assertEqual({r["sha256"] for r in refs},{commander,defender,mav})
        self.assertTrue(all(r["engine_unverified"] for r in refs))
        self.assertTrue(all(r["reference_only"] for r in refs))
        self.assertTrue(all(r["page"]==1 for r in refs))
        prompt = rag.augment([{"role":"user","content":query}],
                             query,self.source,self.index)[0]["content"]
        self.assertIn("ENGINE APPLICABILITY UNVERIFIED",prompt)
        self.assertIn("does NOT specify the requested HD engine",prompt)
        self.assertNotIn("DEFENDER_TRAXTER HD11-",prompt)
        self.assertNotIn("Maverick X3",prompt)
        self.assertNotIn("ROADSTER",prompt)
        self.assertNotIn("SSV 2025",prompt)
        result=rag.include_source_manifest("Candidate manual titles",[
            {"role":"system","content":prompt}])
        self.assertIn("[[SIRUS_MANUAL_SOURCES_START]]",result)
        self.assertIn("Engine applicability not verified",result)
        self.assertIn(commander,result)
        self.assertIn(defender,result)
        self.assertIn(mav,result)

        for platform, expected in (
            ("Defender",defender),("Commander",commander),("Mav",mav)
        ):
            results = rag.retrieve("2026 "+platform+" HD10 wire diagram",
                                   self.source,self.index)
            self.assertEqual({r["sha256"] for r in results},{expected})
        # Detailed wiring answers still require verified, page-attributed
        # evidence and cannot silently use an unspecified engine's diagram.
        self.assertEqual(rag.retrieve("2026 HD10 wiring diagram ignition switch",
                                      self.source,self.index),[])

    def test_broad_unnamed_engine_never_takes_other_named_hd_model(self):
        wrong = "d"*64
        other = "e"*64
        self.add(wrong,"SSV 2026 Defender HD11 Wiring Diagram.pdf",
                 "approved",1,"WIRING DIAGRAM HD11 only")
        self.add(other,"SSV 2026 Commander HD9 Wiring Diagram.pdf",
                 "approved",1,"WIRING DIAGRAM HD9 only")
        rag.sync(self.source,self.index)
        self.assertEqual(rag.retrieve("2026 HD10 wire diagram",self.source,self.index),[])

    def test_missing_2026_hd10_broad_diagrams_do_not_mix_year_or_engine(self):
        self.add("0"*64,"SSV 2025 COMMANDER HD10 - Wire Diagram.pdf",
                 "approved",1,"Diagram legend")
        self.add("1"*64,"SSV 2026 COMMANDER HD11 - Wiring Diagram.pdf",
                 "approved",1,"Circuit legend")
        rag.sync(self.source,self.index)
        self.assertEqual(rag.retrieve("2026 HD10 wire diagram",self.source,self.index),[])

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
