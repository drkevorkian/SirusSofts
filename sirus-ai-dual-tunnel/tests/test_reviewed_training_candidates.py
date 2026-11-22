"""Offline reviewed mechanics candidate exporter: synthetic private databases only."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gentoo"))
sys.path.insert(0, str(ROOT / "training"))
import reviewed_training_candidates as candidates
from qlora_train import load_examples, verify_split

SHA_A = "a" * 64
SHA_B = "b" * 64
SCOPE = "brp/defender/2026/hd11/clutch"
REVIEWER = "cms-admin:1"


class ReviewedCandidateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.input = root / "input.sqlite3"
        self.pdf = root / "pdf.sqlite3"
        self.private = root / "private"
        self.private.mkdir(mode=0o700)
        with sqlite3.connect(self.input) as db:
            db.executescript("""
            CREATE TABLE cr_claims (id TEXT,evidence_id TEXT,state TEXT,version INT);
            CREATE TABLE cr_claim_versions (claim_id TEXT,version INT,state TEXT,
                 quote_sha TEXT,source_revision TEXT,applicability TEXT,reviewed_by TEXT);
            CREATE TABLE cr_evidence (id TEXT,source_id TEXT,source_revision TEXT,
                 source_updated_at INT,applicability TEXT,label TEXT,value TEXT,
                 unit TEXT,quote TEXT,quote_sha TEXT,page INT,part INT,revision TEXT,slot TEXT);
            CREATE TABLE cr_sections (slot TEXT,revision TEXT,current INT);
            CREATE TABLE cr_sources (id TEXT,present INT,eligible INT,excluded INT,
                 kind TEXT,reviewed INT,extraction_status TEXT);
            CREATE TABLE cr_evidence_scopes (source_id TEXT,applicability TEXT);
            CREATE TABLE cr_conflicts (a TEXT,b TEXT,state TEXT);
            """)
        with sqlite3.connect(self.pdf) as db:
            db.executescript("""
            CREATE TABLE documents (sha256 TEXT, updated_at INT,status TEXT,reviewed INT);
            CREATE TABLE excerpts (sha256 TEXT,page INT,part INT,text TEXT);
            """)

    def add(self, sha=SHA_A, *, quote="Rear axle torque: 40 Nm",
            label="rear axle torque", value="4E+1", approved=True,
            claim=True, revision=1, scope=SCOPE, original=True, state="supported"):
        source = "pdf:" + sha
        eid = hashlib.sha256((source + label + quote).encode()).hexdigest()
        slot = eid[:32]
        revision_hash = hashlib.sha256((eid + str(revision)).encode()).hexdigest()
        quote_sha = hashlib.sha256(quote.encode()).hexdigest()
        with sqlite3.connect(self.input) as db:
            db.execute("INSERT OR IGNORE INTO cr_sources VALUES(?,?,?,?,?,?,?)",
                       (source,1,1,0,"pdf",int(approved),"approved" if approved else "ready"))
            db.execute("INSERT OR IGNORE INTO cr_evidence_scopes VALUES(?,?)",(source,scope))
            db.execute("INSERT INTO cr_sections VALUES(?,?,1)",(slot,revision_hash))
            db.execute("""INSERT INTO cr_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (eid,source,"original-rev",revision,scope,label,value,"nm",
                 quote,quote_sha,5,1,revision_hash,slot))
            if claim:
                db.execute("INSERT INTO cr_claims VALUES(?,?,?,2)",
                           (eid,eid,state))
                db.execute("INSERT INTO cr_claim_versions VALUES(?,?,?,?,?,?,?)",
                           (eid,2,state,quote_sha,"original-rev",scope,REVIEWER))
        if original:
            with sqlite3.connect(self.pdf) as db:
                db.execute("INSERT OR IGNORE INTO documents VALUES(?,?,?,?)",
                    (sha,revision,"approved" if approved else "ready",int(approved)))
                db.execute("INSERT INTO excerpts VALUES(?,?,?,?)",
                           (sha,5,1,"Heading\n" + quote + "\nFooter"))
        return eid

    def test_unreviewed_and_missing_corpus_do_not_become_training(self):
        self.add(claim=False)
        train, evaluation, counts = candidates.collect(self.input,self.pdf)
        self.assertEqual(train + evaluation, [])
        self.assertEqual(counts["unique_source_backed_candidates"],0)

    def test_reviewed_quote_rechecked_and_qlora_format_is_compatible(self):
        self.add()
        train, evaluation, counts = candidates.collect(self.input,self.pdf)
        self.assertEqual(len(train) + len(evaluation),1)
        entry=(train + evaluation)[0]
        self.assertTrue(entry["candidate_only"])
        self.assertEqual(entry["profile"],"mechanics")
        self.assertIn("page 5",entry["messages"][1]["content"])
        self.assertEqual(entry["provenance"]["applicability"],SCOPE)
        manifest=candidates.export(self.private,train,evaluation,counts)
        self.assertFalse(manifest["automatic_training"])
        self.assertTrue(manifest["requires_independent_training_approval"])
        self.assertEqual((self.private/candidates.FILES[0]).stat().st_mode & 0o777,0o600)
        examples_train=load_examples(self.private/candidates.FILES[0])
        examples_eval=load_examples(self.private/candidates.FILES[1])
        verify_split(examples_train,examples_eval)
        self.assertEqual(len(examples_train)+len(examples_eval),1)
        self.assertEqual(
            hashlib.sha256((self.private/candidates.FILES[0]).read_bytes()).hexdigest(),
            manifest["sha256_train"])
        with self.assertRaises(ValueError):
            candidates.export(self.private,train,evaluation,counts)

    def test_original_revision_and_quote_must_still_match(self):
        self.add()
        with sqlite3.connect(self.pdf) as db:
            db.execute("UPDATE documents SET updated_at=2")
        self.assertEqual(candidates.collect(self.input,self.pdf)[0:2],([],[]))
        with sqlite3.connect(self.pdf) as db:
            db.execute("UPDATE documents SET updated_at=1")
            db.execute("UPDATE excerpts SET text='Different statement'")
        self.assertEqual(candidates.collect(self.input,self.pdf)[0:2],([],[]))

    def test_scope_change_and_revocation_excluded(self):
        self.add()
        with sqlite3.connect(self.input) as db:
            db.execute("UPDATE cr_evidence_scopes SET applicability=?",
                       ("brp/defender/2025/hd10/clutch",))
        self.assertEqual(candidates.collect(self.input,self.pdf)[0:2],([],[]))
        with sqlite3.connect(self.input) as db:
            db.execute("UPDATE cr_evidence_scopes SET applicability=?",(SCOPE,))
            db.execute("UPDATE cr_claims SET state='superseded'")
        self.assertEqual(candidates.collect(self.input,self.pdf)[0:2],([],[]))

    def test_conflicting_unreviewed_original_blocks_supported_value(self):
        self.add(SHA_A)
        self.add(SHA_B,quote="Rear axle torque: 45 Nm",value="45",claim=False)
        train, evaluation, counts = candidates.collect(self.input,self.pdf)
        self.assertEqual(train + evaluation, [])
        self.assertEqual(counts["conflicting_property"],1)

    def test_scope_unknown_and_unapproved_document_excluded(self):
        self.add(approved=False)
        self.assertEqual(candidates.collect(self.input,self.pdf)[0:2],([],[]))
        with sqlite3.connect(self.input) as db:
            db.execute("UPDATE cr_sources SET reviewed=1,extraction_status='approved'")
        with sqlite3.connect(self.pdf) as db:
            db.execute("UPDATE documents SET status='approved',reviewed=1")
        with sqlite3.connect(self.input) as db:
            db.execute("UPDATE cr_evidence SET applicability=''")
            db.execute("UPDATE cr_claim_versions SET applicability=''")
            db.execute("UPDATE cr_evidence_scopes SET applicability=''")
        self.assertEqual(candidates.collect(self.input,self.pdf)[0:2],([],[]))

    def test_private_writes_only_no_symlink_no_public_permissions(self):
        self.add()
        train, evaluation, counts = candidates.collect(self.input,self.pdf)
        self.private.chmod(0o755)
        with self.assertRaises(ValueError):
            candidates.export(self.private,train,evaluation,counts)
        self.private.chmod(0o700)
        link=self.private.parent/"private-link"
        link.symlink_to(self.private,target_is_directory=True)
        with self.assertRaises(ValueError):
            candidates.export(link,train,evaluation,counts)
        self.assertFalse(any(self.private.iterdir()))
        self.assertEqual(candidates.main([
            "--input-db",str(self.input),"--pdf-db",str(self.pdf)]),0)
        self.assertFalse(any(self.private.iterdir()))


if __name__=="__main__":
    unittest.main()
