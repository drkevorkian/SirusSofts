"""Regression tests for local, private, checkpointed source research."""
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"gentoo"))
import research_loop as research
import input_server as ai


class ManualResearchTests(unittest.TestCase):
    @staticmethod
    def spec(passes=2, kind="text"):
        return {
            "source_type": kind, "title": "HD11 Clutch Research",
            "focus": "Specifications and mechanical issues", "passes": passes,
            "url": "", "text": "Technical procedure data for a motor",
            "pdf_sha256": "", "page_start": 1, "page_end": 0
        }

    def test_validate_loop_and_unsafe_urls(self):
        self.assertEqual(research.validated_spec(self.spec(100))["passes"],100)
        for value in (0,101,-1,True,1.5,"10"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                research.validated_spec(self.spec(value))
        for url in ("http://example.org", "https://127.0.0.1/private",
                    "https://user:password@example.org", "https://host.local"):
            spec=self.spec()
            spec.update(source_type="url",text="",url=url)
            with self.subTest(url=url), self.assertRaises(ValueError):
                research.validated_spec(spec)
        pdf=self.spec(kind="pdf")
        pdf.update(text="",pdf_sha256="a"*64,page_start=2,page_end=4)
        self.assertEqual(research.validated_spec(pdf)["page_start"],2)
        for val in ("aaa","A"*64,"../../private"):
            pdf["pdf_sha256"]=val
            with self.assertRaises(ValueError):
                research.validated_spec(pdf)

    def test_source_frozen_once_and_draft_grounding(self):
        spec=self.spec(kind="url")
        spec.update(url="https://example.org/manual",text="")
        requested=[]
        def fetch(url):
            requested.append(url)
            return url,"Reference text from source"
        link,txt,sha=research.read_source(spec,fetch=fetch)
        self.assertEqual(requested,["https://example.org/manual"])
        self.assertEqual(sha,hashlib.sha256(txt.encode()).hexdigest())
        msg=research.pass_messages(spec,txt,sha,2,"Older draft")
        self.assertIn("Older draft",msg[-1]["content"])
        self.assertIn(txt,msg[-1]["content"])
        self.assertIn("UNTRUSTED",msg[-1]["content"])

    def test_existing_pdf_catalog_and_complete_ordered_page_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td,"pdf.sqlite3")
            sha="b"*64
            con=sqlite3.connect(db)
            con.executescript("""CREATE TABLE documents(
                sha256 TEXT,filename TEXT,pages INTEGER,status TEXT,
                reviewed INTEGER,excerpts INTEGER,updated_at INTEGER);
                CREATE TABLE excerpts(sha256 TEXT,page INTEGER,part INTEGER,text TEXT);
            """)
            con.execute("INSERT INTO documents VALUES(?, ?, 3, 'ready',0,3,100)",
                        (sha,"2026 Defender HD11 Manual.pdf"))
            con.executemany("INSERT INTO excerpts VALUES(?,?,?,?)",
                            [(sha,1,1,"A"*4000),(sha,2,1,"B"*4200),(sha,3,1,"C"*2000)])
            con.commit();con.close()
            rows=research.list_local_manuals("HD11",source=db)
            self.assertEqual(len(rows),1)
            self.assertFalse(rows[0]["reviewed"])
            self.assertEqual(rows[0]["status"],"ready")
            spec=self.spec(kind="pdf")
            spec.update(text="",pdf_sha256=sha,page_start=1,page_end=0)
            info,segments=research.pdf_segments(spec,source=db,limit=5000)
            self.assertEqual(info["page_end"],3)
            combined="\n".join(s["text"] for s in segments)
            for mark,count in (("A",4000),("B",4200),("C",2000)):
                self.assertIn(mark*count,combined)
            self.assertEqual(sorted(set(p for s in segments for p in s["pages"])),[1,2,3])
            with self.assertRaises(ValueError):
                bad=dict(spec,page_start=4)
                research.pdf_metadata(bad,source=db)
            con=sqlite3.connect(db)
            con.execute("UPDATE documents SET status='queued' WHERE sha256=?",(sha,))
            con.commit();con.close()
            self.assertEqual(research.list_local_manuals("HD11",source=db),[])
            with self.assertRaises(ValueError):
                research.pdf_segments(spec,source=db)

    def test_each_pass_covers_all_pdf_sections_and_checkpoints(self):
        with tempfile.TemporaryDirectory() as td:
            key=Path(td,"key");key.write_text("s"*64)
            db=str(Path(td,"input.sqlite3"))
            ai.init_db(db)
            engine=ai.Engine(db,str(key))
            sha="d"*64
            spec=self.spec(2,kind="pdf")
            spec.update(text="",pdf_sha256=sha)
            job=engine.enqueue_research(spec,"cpu","sirussofts-coder")
            meta={"sha256":sha,"page_start":1,"page_end":2,"updated_at":99}
            sections=[{"text":"Page one technical details","pages":[1]},
                      {"text":"Page two technical details","pages":[2]}]
            prompts=[]
            def fake_chat(url,messages,**kwargs):
                prompts.append(messages[-1]["content"])
                return "Grounded draft "+str(len(prompts))
            with patch.object(ai,"pdf_segments",return_value=(meta,sections)), \
                 patch.object(ai,"choose_worker",return_value=("cpu","http://127.0.0.1:18482")), \
                 patch.object(ai,"chat",side_effect=fake_chat):
                self.assertTrue(engine.run_research_one())
            state=engine.research_status(job)
            self.assertEqual(state["state"],"completed")
            self.assertEqual(state["completed"],2)
            self.assertEqual(state["sections_per_pass"],2)
            self.assertEqual(len(prompts),4)
            self.assertEqual(sum("Page one technical details" in p for p in prompts),2)
            self.assertEqual(sum("Page two technical details" in p for p in prompts),2)
            with ai.db_connect(db) as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM research_sections WHERE job_id=?",(job,)).fetchone()[0],4)
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM research_passes WHERE job_id=?",(job,)).fetchone()[0],2)

    def test_resume_from_completed_section_and_owner_safe_cancel(self):
        with tempfile.TemporaryDirectory() as td:
            key=Path(td,"key");key.write_text("s"*64)
            db=str(Path(td,"input.sqlite3"));ai.init_db(db)
            engine=ai.Engine(db,str(key))
            spec=self.spec(2)
            job=engine.enqueue_research(spec,"cpu","sirussofts-coder")
            attempts=[]
            def failing(url,messages,**kwargs):
                attempts.append("call")
                if len(attempts)==2:
                    raise RuntimeError("Temporary model failure")
                return "Draft "+str(len(attempts))
            with patch.object(ai,"choose_worker",return_value=("cpu","http://127.0.0.1:18482")), \
                 patch.object(ai,"chat",side_effect=failing):
                engine.run_research_one()
                self.assertEqual(engine.research_status(job)["completed"],1)
                with ai.db_connect(db) as con:
                    con.execute("UPDATE research_jobs SET state='queued',error='' WHERE id=?",(job,))
                engine.run_research_one()
            self.assertEqual(engine.research_status(job)["state"],"completed")
            self.assertEqual(len(attempts),3)
            with ai.db_connect(db) as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM research_passes WHERE job_id=?",(job,)).fetchone()[0],2)
            canceled=engine.enqueue_research(self.spec(3),"cpu","sirussofts-coder")
            state=engine.cancel_research(canceled)
            self.assertEqual(state["state"],"canceled")
            self.assertFalse(engine.run_research_one())


if __name__=="__main__":
    unittest.main()
