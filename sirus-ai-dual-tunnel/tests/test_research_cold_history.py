"""Offline archive-history regression: exact provenance, privacy and no pruning."""
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
import input_server as ai
from research_archive import archive_snapshot
from research_cold_history import (
    catalog, source_history, job_provenance, retention_preview,
)

SOURCE_A="pdf:"+"a"*64
SOURCE_B="pdf:"+"b"*64
REVISION="c"*64
ORIGINAL="d"*64
JOB_A="1"*32
JOB_B="2"*32
EVIDENCE="e"*64


class ColdHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=self.root/"input.sqlite3"
        self.archives=self.root/"snapshots"
        ai.init_db(self.db)
        with sqlite3.connect(self.db) as db:
            for source,title in ((SOURCE_A,"Approved historical manual"),
                                 (SOURCE_B,"Independent original document")):
                db.execute("""INSERT INTO cr_sources
                    (id,kind,title,content_sha,revision,extraction_status,
                     reviewed,seen,updated)
                    VALUES(?, 'pdf', ?, ?, ?, 'approved',1,1,1)""",
                    (source,title,source[4:],ORIGINAL))
            for slot,source in (("source-a",SOURCE_A),("source-b",SOURCE_B)):
                db.execute("""INSERT INTO cr_sections
                    (slot,revision,source_id,page,part,byte_offset,
                     chars,source_revision,seen,current)
                    VALUES(?,?,?,2,1,0,120,?,1,0)""",
                    (slot,REVISION,source,ORIGINAL))
            db.execute("""INSERT INTO cr_cycles
                (started,completed,state,scope)
                VALUES(1,2,'completed','full')""")
            for job,slot,count in ((JOB_A,"source-a",2),(JOB_B,"source-b",1)):
                db.execute("""INSERT INTO cr_jobs
                    (id,cycle,slot,revision,passes,pass_completed,state,
                     created,updated)
                    VALUES(?,1,?,?,?,?,'completed',1,2)""",
                    (job,slot,REVISION,count,count))
            for number in (1,2):
                note="Private unreviewed draft "+str(number)+" password=secret"
                db.execute("""INSERT INTO cr_results
                    (job_id,pass_number,notes,sha256,created,duration_ms,output_chars)
                    VALUES(?,?,?,?,?,?,?)""",
                    (JOB_A,number,note,hashlib.sha256(note.encode()).hexdigest(),
                     number,10*number,len(note)))
            note="Another source draft"
            db.execute("""INSERT INTO cr_results
                (job_id,pass_number,notes,sha256,created,duration_ms,output_chars)
                VALUES(?,?,?,?,?,?,?)""",
                (JOB_B,1,note,hashlib.sha256(note.encode()).hexdigest(),1,5,len(note)))
            for number in range(22):
                db.execute("""INSERT INTO cr_events
                    (happened,kind,subject,detail)
                    VALUES(?, 'pass-checkpoint', ?, ?)""",
                    (number,JOB_A,str(number)))
            db.execute("""INSERT INTO cr_events
                (happened,kind,subject,detail)
                VALUES(30, 'job-complete',?,'')""",(JOB_B,))
            db.execute("""INSERT INTO cr_evidence
                (id,source_id,slot,revision,source_revision,page,part,
                 byte_offset,label,value,unit,quote,quote_sha,created)
                VALUES(?,?,?,?,?,2,1,0,'rear axle torque','40','nm',
                       'Rear axle torque: 40 Nm',?,1)""",
                (EVIDENCE,SOURCE_A,"source-a",REVISION,ORIGINAL,"f"*64))
            db.execute("""INSERT INTO cr_claims
                (id,evidence_id,state,version,updated)
                VALUES(?,?,'supported',2,2)""",(EVIDENCE,EVIDENCE))
            db.execute("""INSERT INTO cr_claim_versions
                (claim_id,version,state,statement,quote_sha,
                 source_revision,applicability,reviewed_by,reason,created)
                VALUES(?,2,'supported','Rear axle torque: 40 Nm',?,?,
                       'brp/defender/2026/hd11/clutch',
                       'cms-admin:1','source checked',2)""",
                (EVIDENCE,"f"*64,ORIGINAL))

    def snapshot(self):
        info=archive_snapshot(self.db,self.archives)
        return self.archives/info["archive"]

    def test_catalog_and_source_pagination_preserve_exact_immutable_links(self):
        file=self.snapshot()
        before=self.db.stat().st_size
        listing=catalog(self.archives)
        self.assertTrue(listing["complete_scan"])
        self.assertEqual(listing["total"],1)
        self.assertEqual(listing["snapshots"][0]["passes"],3)
        self.assertEqual(listing["snapshots"][0]["sources"],2)
        found=source_history(file,SOURCE_A)
        self.assertTrue(found["snapshot_verified"])
        self.assertEqual(found["source"]["id"],SOURCE_A)
        self.assertEqual(found["pass_results_total"],2)
        self.assertEqual(found["jobs_total"],1)
        self.assertEqual(found["passes"][0]["page"],2)
        self.assertEqual(found["passes"][0]["part"],1)
        self.assertEqual(found["passes"][0]["source_revision"],ORIGINAL)
        self.assertNotIn("notes",found["passes"][0])
        self.assertNotIn("secret",str(found))
        self.assertEqual(found["audit_total"],22)
        self.assertEqual(len(found["audit_events"]),20)
        self.assertTrue(found["audit_has_more"])
        tail=source_history(file,SOURCE_A,offset=1,audit_offset=20)
        self.assertEqual(len(tail["passes"]),1)
        self.assertEqual(tail["passes"][0]["pass_number"],1)
        self.assertEqual(len(tail["audit_events"]),2)
        self.assertFalse(tail["audit_has_more"])
        self.assertEqual(found["claim_versions"][0]["state"],"supported")
        self.assertEqual(found["claim_versions"][0]["reviewed_by"],"cms-admin:1")
        self.assertFalse(found["verified_service_procedures"])
        other=source_history(file,SOURCE_B)
        self.assertEqual(other["pass_results_total"],1)
        self.assertEqual(other["audit_total"],1)
        self.assertEqual(other["claim_versions"],[])
        self.assertFalse(any(e["subject"]==JOB_A for e in other["audit_events"]))
        self.assertEqual(self.db.stat().st_size,before)
        self.assertFalse((self.archives/(file.name+"-wal")).exists())

    def test_private_draft_output_requires_explicit_boolean_selection(self):
        file=self.snapshot()
        anonymous=job_provenance(file,JOB_A)
        self.assertEqual(anonymous["job"]["source_id"],SOURCE_A)
        self.assertEqual(len(anonymous["pass_results"]),2)
        self.assertNotIn("notes",anonymous["pass_results"][0])
        self.assertNotIn("secret",str(anonymous))
        explicit=job_provenance(file,JOB_A,show_private_drafts=True)
        self.assertIn("password=secret",explicit["pass_results"][0]["notes"])
        source=source_history(file,SOURCE_A,show_private_drafts=True)
        self.assertIn("password=secret",source["passes"][0]["notes"])
        self.assertTrue(source["drafts_included"])
        self.assertFalse(source["verified_service_procedures"])
        for item in ("yes",1,None):
            with self.assertRaises(ValueError):
                source_history(file,SOURCE_A,show_private_drafts=item)
            with self.assertRaises(ValueError):
                job_provenance(file,JOB_A,show_private_drafts=item)

    def test_retention_preview_is_not_approval_or_deletion(self):
        file=self.snapshot()
        sample=retention_preview(file,before_unix=10)
        self.assertTrue(sample["archive_verified"])
        self.assertEqual(sample["historical_closed_revision_jobs"],2)
        self.assertEqual(sample["historical_checkpointed_passes"],3)
        self.assertEqual(sample["distinct_source_ids"],2)
        self.assertEqual(sample["historical_review_versions"],1)
        self.assertFalse(sample["pruning_permitted"])
        self.assertFalse(sample["no_live_writes"] is False)
        self.assertEqual(retention_preview(file,before_unix=2)[
            "historical_closed_revision_jobs"],0)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_results").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT count(*) FROM cr_events").fetchone()[0],23)
            self.assertEqual(db.execute("SELECT count(*) FROM cr_claim_versions").
                             fetchone()[0],1)

    def test_archive_mismatch_missing_or_unsafe_queries_reject(self):
        file=self.snapshot()
        for source in ("../bad","pdf:bad","research:"+"a"*64,1,None):
            with self.assertRaises(ValueError):
                source_history(file,source)
        for offset in (-1,"0",True,1000001):
            with self.assertRaises(ValueError):
                source_history(file,SOURCE_A,offset=offset)
            with self.assertRaises(ValueError):
                source_history(file,SOURCE_A,audit_offset=offset)
        for job in (JOB_A.upper(),"../job","f"*32,None):
            with self.assertRaises(ValueError):
                job_provenance(file,job)
        for before in (0,-1,1.5,True,"20"):
            with self.assertRaises(ValueError):
                retention_preview(file,before_unix=before)
        with file.open("ab") as output:
            output.write(b"altered")
        for func,args in (
                (catalog,(self.archives,)),
                (source_history,(file,SOURCE_A)),
                (job_provenance,(file,JOB_A))):
            with self.assertRaises(ValueError):
                func(*args)
        with self.assertRaises(ValueError):
            retention_preview(file,before_unix=10)

    def test_catalog_rejects_orphans_and_never_silently_skips_bad_snapshot(self):
        file=self.snapshot()
        another=self.snapshot()
        self.assertNotEqual(file.name,another.name)
        self.assertEqual(catalog(self.archives)["total"],2)
        (self.archives/(another.name+".json")).unlink()
        with self.assertRaises(ValueError):
            catalog(self.archives)
        (self.archives/(file.name+".json")).rename(
            self.archives/(another.name+".json"))
        # The existing snapshot now has no manifest; verification must fail.
        with self.assertRaises(ValueError):
            catalog(self.archives)
        orphan=self.archives/(file.name+".json")
        another.unlink()
        self.assertTrue(orphan.exists() is False)


if __name__=="__main__":
    unittest.main()
