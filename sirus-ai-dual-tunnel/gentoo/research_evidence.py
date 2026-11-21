#!/usr/bin/env python3
"""Private, source-grounded comparison candidates and human review ledger.

This module never consumes model answers as evidence, never promotes drafts
automatically, never calls a network service and never exports RAG/training data.
It operates only on the source manifest and the original read-only PDF catalog.
"""
import re
from contextlib import closing
import sqlite3
import time
from decimal import Decimal, InvalidOperation

from continuous_research import connect, digest
from research_loop import _manual_connection

# Deliberately narrow conservative parser. Ambiguous procedures, diagrams,
# unit conversions and inferred applications require human interpretation.
UNITS = r"(?:N[\s\u00b7-]*m|Nm|ft[\s-]*lb|in[\s-]*lb|mm|cm|mA|kPa|psi|bar|V|A|ohm|rpm|\u00b0C|\u00b0F)"
SPEC_LINE = re.compile(
    r"(?im)^[ \t]*([A-Za-z][A-Za-z0-9 /()_.-]{2,70}?)"
    r"[ \t]*(?::|=)[ \t]*([+-]?\d{1,7}(?:\.\d{1,6})?)"
    r"[ \t]*(" + UNITS + r")(?=$|[ \t,;).])"
)
RELEVANT = re.compile(
    r"(?i)\b(?:torque|tightening|clearance|pressure|voltage|"
    r"resistance|gap|temperature|current|diameter|length|width|height|"
    r"speed|idle|output|capacity|tolerance|setting)\b"
)
SCOPE = re.compile(r"[a-z0-9][a-z0-9._-]{0,23}(?:/[a-z0-9][a-z0-9._-]{0,23}){1,5}\Z")
ID = re.compile(r"[0-9a-f]{64}\Z")
ACTOR = re.compile(r"cms-admin:[1-9][0-9]{0,12}\Z")
MAX_INDEX_BATCH = 64


def parse_evidence(text):
    """Return exact quotes and typed values; no interpretation from LLM text."""
    result = []
    # Research sections are already bounded to 7,000 source characters;
    # parsing ALL matching measurement lines cannot grow without bound.
    # Never mark a section indexed after silently skipping later candidates.
    for match in SPEC_LINE.finditer(text):
        label = " ".join(match.group(1).lower().split())
        if not RELEVANT.search(label):
            continue
        try:
            value = str(Decimal(match.group(2)).normalize())
        except InvalidOperation:
            continue
        unit = re.sub(r"\s+", "", match.group(3)).lower().replace("\u00b7", "")
        # Never silently convert units; different unit classes do not compare.
        quote = match.group(0).strip()
        if len(quote) > 180:
            continue
        result.append((label, value, unit, quote))
    return result


def init_schema(path):
    """Additive migration: existing research, CMS knowledge and drafts intact."""
    with connect(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS cr_evidence_scopes (
          source_id TEXT PRIMARY KEY, applicability TEXT NOT NULL DEFAULT '',
          updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cr_scope_versions (
          source_id TEXT NOT NULL, version INTEGER NOT NULL,
          previous_scope TEXT NOT NULL, new_scope TEXT NOT NULL,
          reviewed_by TEXT NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(source_id,version)
        );
        CREATE TRIGGER IF NOT EXISTS cr_scope_versions_no_update
          BEFORE UPDATE ON cr_scope_versions BEGIN
            SELECT RAISE(ABORT,'Scope history is append-only');
          END;
        CREATE TRIGGER IF NOT EXISTS cr_scope_versions_no_delete
          BEFORE DELETE ON cr_scope_versions BEGIN
            SELECT RAISE(ABORT,'Scope history cannot be deleted');
          END;
        CREATE TABLE IF NOT EXISTS cr_evidence_indexed (
          slot TEXT NOT NULL, revision TEXT NOT NULL,
          indexed REAL NOT NULL, PRIMARY KEY(slot,revision)
        );
        CREATE TABLE IF NOT EXISTS cr_evidence (
          id TEXT PRIMARY KEY, source_id TEXT NOT NULL, slot TEXT NOT NULL,
          revision TEXT NOT NULL, source_revision TEXT NOT NULL,
          source_updated_at INTEGER NOT NULL DEFAULT 0,
          page INTEGER NOT NULL, part INTEGER NOT NULL,
          byte_offset INTEGER NOT NULL,
          applicability TEXT NOT NULL DEFAULT '',
          label TEXT NOT NULL, value TEXT NOT NULL, unit TEXT NOT NULL,
          quote TEXT NOT NULL, quote_sha TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS cr_evidence_match
          ON cr_evidence(applicability,label,unit,value);
        CREATE INDEX IF NOT EXISTS cr_evidence_section
          ON cr_evidence(slot,revision);
        CREATE TABLE IF NOT EXISTS cr_conflicts (
          id TEXT PRIMARY KEY, a TEXT NOT NULL, b TEXT NOT NULL,
          applicability TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'unreviewed'
            CHECK(state IN ('unreviewed','dismissed','superseded')),
          reviewed_by TEXT NOT NULL DEFAULT '', reviewed_at REAL,
          created REAL NOT NULL, UNIQUE(a,b)
        );
        CREATE TABLE IF NOT EXISTS cr_claims (
          id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL UNIQUE,
          state TEXT NOT NULL DEFAULT 'unreviewed'
            CHECK(state IN ('unreviewed','supported','disputed','rejected','superseded')),
          version INTEGER NOT NULL DEFAULT 1, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cr_claim_versions (
          claim_id TEXT NOT NULL, version INTEGER NOT NULL,
          state TEXT NOT NULL CHECK(state IN
            ('unreviewed','supported','disputed','rejected','superseded')),
          statement TEXT NOT NULL, quote_sha TEXT NOT NULL,
          source_revision TEXT NOT NULL, applicability TEXT NOT NULL DEFAULT '',
          reviewed_by TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
          PRIMARY KEY(claim_id,version)
        );
        CREATE TRIGGER IF NOT EXISTS cr_claim_versions_no_update
          BEFORE UPDATE ON cr_claim_versions BEGIN
            SELECT RAISE(ABORT,'Claim history is append-only');
          END;
        CREATE TRIGGER IF NOT EXISTS cr_claim_versions_no_delete
          BEFORE DELETE ON cr_claim_versions BEGIN
            SELECT RAISE(ABORT,'Claim history cannot be deleted');
          END;
        CREATE TABLE IF NOT EXISTS cr_evidence_settings (
          id INTEGER PRIMARY KEY CHECK(id=1),
          compare_cursor TEXT NOT NULL DEFAULT '',
          updated REAL NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO cr_evidence_settings(id) VALUES(1);
        """)
        # Preserve all historical reviews; old rows have unknown original
        # applicability, not an invented current application.
        columns={row[1] for row in db.execute("PRAGMA table_info(cr_claim_versions)")}
        if "applicability" not in columns:
            db.execute("""ALTER TABLE cr_claim_versions ADD COLUMN applicability
                TEXT NOT NULL DEFAULT ''""")


class EvidenceLedger:
    def __init__(self, research):
        self.research=research
        self.db_path=research.db_path

    @staticmethod
    def _current_clause(alias="e"):
        return (f"EXISTS (SELECT 1 FROM cr_sections s JOIN cr_sources d "
                f"ON d.id=s.source_id WHERE s.slot={alias}.slot "
                f"AND s.revision={alias}.revision AND s.current=1 "
                f"AND d.eligible=1 AND d.excluded=0 AND d.present=1)")

    @staticmethod
    def _append_review(db, evidence, state, reviewer, reason=""):
        now=time.time()
        claim=db.execute("SELECT id,state,version FROM cr_claims WHERE evidence_id=?",
                         (evidence["id"],)).fetchone()
        if claim and claim["state"]==state:
            raise ValueError("Claim already has requested review state")
        claim_id=evidence["id"]
        if not claim:
            db.execute("INSERT INTO cr_claims(id,evidence_id,updated) VALUES(?,?,?)",
                       (claim_id,evidence["id"],now))
            version=1
            db.execute("""INSERT INTO cr_claim_versions(claim_id,version,state,
                 statement,quote_sha,source_revision,applicability,reviewed_by,created)
                 VALUES(?,1,'unreviewed',?,?,?,?,'system:index',?)""",
                       (claim_id,evidence["quote"],evidence["quote_sha"],
                        evidence["source_revision"],evidence["applicability"],now))
        else:
            version=claim["version"]
        version+=1
        db.execute("""INSERT INTO cr_claim_versions(claim_id,version,state,
            statement,quote_sha,source_revision,applicability,reviewed_by,reason,created)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (claim_id,version,state,evidence["quote"],evidence["quote_sha"],
             evidence["source_revision"],evidence["applicability"],reviewer,
             reason[:200],now))
        db.execute("UPDATE cr_claims SET version=?,state=?,updated=? WHERE id=?",
                   (version,state,now,claim_id))

    def reconcile(self):
        """Invalidate changed source claims; preserve complete immutable history."""
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            revoked=db.execute("""SELECT e.* FROM cr_claims c
                JOIN cr_evidence e ON e.id=c.evidence_id
                WHERE c.state NOT IN ('superseded','rejected') AND NOT """
                +self._current_clause("e") + """ OR (
                    c.state NOT IN ('superseded','rejected')
                    AND COALESCE((SELECT v.applicability FROM cr_claim_versions v
                        WHERE v.claim_id=c.id ORDER BY v.version DESC LIMIT 1),'')
                        <>e.applicability)""").fetchall()
            for evidence in revoked:
                self._append_review(db,evidence,"superseded","system:source-revision")
            db.execute("""UPDATE cr_conflicts SET state='superseded'
                WHERE state='unreviewed' AND (
                  NOT EXISTS (SELECT 1 FROM cr_evidence e WHERE e.id=a AND """
                  +self._current_clause("e")+""")
                  OR NOT EXISTS (SELECT 1 FROM cr_evidence e WHERE e.id=b AND """
                  +self._current_clause("e")+""")
                  OR EXISTS (SELECT 1 FROM cr_evidence ea JOIN cr_evidence eb
                      ON eb.id=b WHERE ea.id=a
                      AND (ea.applicability<>eb.applicability
                           OR ea.applicability<>cr_conflicts.applicability)))""")
            return len(revoked)

    def index_batch(self, limit=MAX_INDEX_BATCH):
        if type(limit) is not int or not 1<=limit<=MAX_INDEX_BATCH:
            raise ValueError("Invalid evidence batch size")
        with connect(self.db_path) as db:
            sections=db.execute("""SELECT s.slot,s.revision,s.source_id,s.source_revision,
                    s.page,s.part,s.byte_offset,d.title,d.content_sha
                FROM cr_sections s JOIN cr_sources d ON d.id=s.source_id
                LEFT JOIN cr_evidence_indexed x ON x.slot=s.slot AND x.revision=s.revision
                WHERE s.current=1 AND d.eligible=1 AND d.present=1 AND d.excluded=0
                AND s.source_id LIKE 'pdf:%' AND x.slot IS NULL
                ORDER BY s.source_id,s.page,s.part,s.byte_offset LIMIT ?""",
                (limit,)).fetchall()
        finished=0; discovered=0
        for row in sections:
            lease=dict(row)
            try:
                text=self.research._section_text(lease)
            except (ValueError,sqlite3.Error,RuntimeError):
                # Do not mark a changed/unavailable original as fully indexed.
                continue
            candidates=parse_evidence(text)
            with closing(_manual_connection(self.research.source,self.research.index)) as pdf:
                pdf_meta=pdf.execute("""SELECT updated_at FROM documents
                    WHERE sha256=? AND status IN ('ready','approved')""",
                    (lease["source_id"][4:],)).fetchone()
            if pdf_meta is None:
                continue
            source_updated_at=pdf_meta["updated_at"]
            stamp=time.time()
            with connect(self.db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                current=db.execute("""SELECT 1 FROM cr_sections s JOIN cr_sources d
                   ON d.id=s.source_id WHERE s.slot=? AND s.revision=?
                   AND s.current=1 AND d.present=1 AND d.eligible=1 AND d.excluded=0""",
                   (lease["slot"],lease["revision"])).fetchone()
                if not current:
                    continue
                scope=db.execute("""SELECT applicability FROM cr_evidence_scopes
                   WHERE source_id=?""",(lease["source_id"],)).fetchone()
                scope=scope["applicability"] if scope else ""
                for label,value,unit,quote in candidates:
                    # Exact original document revision is checked again
                    # during human promotion, not trusted from the LLM.
                    eid=digest("|".join((lease["slot"],lease["revision"],
                                          label,value,unit,digest(quote))))
                    db.execute("""INSERT OR IGNORE INTO cr_evidence(
                        id,source_id,slot,revision,source_revision,
                        source_updated_at,page,part,byte_offset,applicability,
                        label,value,unit,quote,quote_sha,created)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (eid,lease["source_id"],lease["slot"],lease["revision"],
                         lease["source_revision"],source_updated_at,
                         lease["page"],lease["part"],lease["byte_offset"],
                         scope,label,value,unit,quote,digest(quote),stamp))
                    discovered+=1
                db.execute("""INSERT OR IGNORE INTO cr_evidence_indexed
                    (slot,revision,indexed) VALUES(?,?,?)""",
                    (lease["slot"],lease["revision"],stamp))
                finished+=1
        self.reconcile()
        return {"indexed_sections":finished,"parsed_candidates":discovered}

    def set_scope(self, source_id, scope, reviewer):
        """Admin-provided applicability key, never inferred from titles/LLM.

        Format: make/model/year[/engine[/assembly[/revision]]], lowercase.
        """
        if not isinstance(source_id,str) or not re.fullmatch(
                r"pdf:[0-9a-f]{64}",source_id):
            raise ValueError("Only inventoried original PDFs support comparison")
        if not isinstance(scope,str) or (scope and not SCOPE.fullmatch(scope)):
            raise ValueError("Scope must be make/model/year[/engine/assembly/revision]")
        if not isinstance(reviewer,str) or not ACTOR.fullmatch(reviewer):
            raise ValueError("Authenticated CMS administrator required to change scope")
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("""SELECT 1 FROM cr_sources WHERE id=? AND kind='pdf'
                AND present=1""",(source_id,)).fetchone():
                raise ValueError("PDF is not in current private inventory")
            previous=db.execute("""SELECT applicability FROM cr_evidence_scopes
                WHERE source_id=?""",(source_id,)).fetchone()
            previous=previous["applicability"] if previous else ""
            if previous==scope:
                return {"source_id":source_id,"applicability":scope,"changed":False,
                        "invalidated_claims":0}
            now=time.time()
            # A prior human approval is for a specific application. Revoke it
            # BEFORE changing the candidate scope, preserving old applicability
            # in the immutable historical claim entry. Re-review is mandatory.
            current=db.execute("""SELECT e.* FROM cr_claims c
                JOIN cr_evidence e ON e.id=c.evidence_id
                WHERE e.source_id=? AND c.state NOT IN ('superseded','rejected')""",
                (source_id,)).fetchall()
            for evidence in current:
                self._append_review(db,evidence,"superseded",
                                    "system:scope-change",
                                    "Application scope changed; manual re-review required")
            version=db.execute("""SELECT COALESCE(MAX(version),0)+1
                FROM cr_scope_versions WHERE source_id=?""",
                (source_id,)).fetchone()[0]
            db.execute("""INSERT INTO cr_scope_versions(
                source_id,version,previous_scope,new_scope,reviewed_by,created)
                VALUES(?,?,?,?,?,?)""",(source_id,version,previous,scope,reviewer,now))
            db.execute("""INSERT INTO cr_evidence_scopes(source_id,applicability,updated)
                VALUES(?,?,?) ON CONFLICT(source_id)
                DO UPDATE SET applicability=excluded.applicability,
                              updated=excluded.updated""",
                (source_id,scope,now))
            db.execute("UPDATE cr_evidence SET applicability=? WHERE source_id=?",
                       (scope,source_id))
            # Existing comparison candidates with different scope are stale.
            db.execute("""UPDATE cr_conflicts SET state='superseded'
                WHERE state='unreviewed' AND EXISTS
                  (SELECT 1 FROM cr_evidence a JOIN cr_evidence b ON b.id=cr_conflicts.b
                   WHERE a.id=cr_conflicts.a
                     AND (a.applicability<>b.applicability
                          OR a.applicability<>cr_conflicts.applicability))""")
            db.execute("UPDATE cr_evidence_settings SET compare_cursor='' WHERE id=1")
        return {"source_id":source_id,"applicability":scope,"changed":True,
                "invalidated_claims":len(current)}

    def compare_batch(self, limit=64):
        """Complete paged comparison of identically scoped original documents.

        A keyset cursor records the last (a,b) candidate pair. Unlike a
        fixed-per-source peer cap, this never silently skips additional
        disagreements when many independent documents share a property.
        """
        if type(limit) is not int or not 1<=limit<=256:
            raise ValueError("Invalid comparison batch limit")
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            token=db.execute("SELECT compare_cursor FROM cr_evidence_settings WHERE id=1").fetchone()[0]
            left,right=token.split(":",1) if token else ("","")
            matches=db.execute("""SELECT a.id a,b.id b,a.applicability scope
                FROM cr_evidence a JOIN cr_evidence b
                  ON a.applicability=b.applicability AND a.label=b.label
                  AND a.unit=b.unit AND a.value<>b.value
                  AND a.source_id<>b.source_id AND a.id<b.id
                WHERE a.applicability<>'' AND
                (a.id>? OR (a.id=? AND b.id>?)) AND """
                +self._current_clause("a")+" AND "+self._current_clause("b")+"""
                ORDER BY a.id,b.id LIMIT ?""",(left,left,right,limit)).fetchall()
            if not matches:
                db.execute("UPDATE cr_evidence_settings SET compare_cursor='' WHERE id=1")
                return {"compared_pairs":0,"new_conflicts":0,"wrapped":True}
            added=0
            for pair in matches:
                cur=db.execute("""INSERT OR IGNORE INTO cr_conflicts
                    (id,a,b,applicability,created) VALUES(?,?,?,?,?)""",
                    (digest(pair["a"]+"|"+pair["b"]),pair["a"],pair["b"],
                     pair["scope"],time.time()))
                added+=cur.rowcount
            last=matches[-1]
            db.execute("""UPDATE cr_evidence_settings
                SET compare_cursor=?,updated=? WHERE id=1""",
                (last["a"]+":"+last["b"],time.time()))
            return {"compared_pairs":len(matches),"new_conflicts":added,
                    "wrapped":False}

    def review(self, evidence_id, quote_sha, revision, reviewer, action, reason="",
               *, expected_scope=None):
        """Human-only review. No AI worker calls this; CMS guards the route."""
        if (not isinstance(evidence_id,str) or not ID.fullmatch(evidence_id)
            or not isinstance(quote_sha,str) or not ID.fullmatch(quote_sha)
            or not isinstance(revision,str) or not ID.fullmatch(revision)
            or not isinstance(reviewer,str) or not ACTOR.fullmatch(reviewer)
            or action not in ("support","dispute","reject","supersede")
            or not isinstance(reason,str) or len(reason)>200
            or not isinstance(expected_scope,str)
            or (expected_scope and not SCOPE.fullmatch(expected_scope))):
            raise ValueError("Invalid human review request")
        state={"support":"supported","dispute":"disputed",
               "reject":"rejected","supersede":"superseded"}[action]
        with connect(self.db_path) as db:
            row=db.execute("SELECT * FROM cr_evidence WHERE id=?",
                           (evidence_id,)).fetchone()
            current=db.execute("""SELECT d.reviewed,d.extraction_status,
                d.present,COALESCE(sc.applicability,'') actual_scope
                FROM cr_evidence e JOIN cr_sections s
                ON s.slot=e.slot AND s.revision=e.revision
                JOIN cr_sources d ON d.id=e.source_id
                LEFT JOIN cr_evidence_scopes sc ON sc.source_id=e.source_id
                WHERE e.id=? AND s.current=1 AND d.eligible=1 AND d.excluded=0
                AND d.present=1""",(evidence_id,)).fetchone()
        if not row or not current or row["quote_sha"]!=quote_sha or row["revision"]!=revision:
            raise ValueError("Evidence revision is missing, stale or excluded")
        if row["applicability"]!=expected_scope or current["actual_scope"]!=expected_scope:
            raise ValueError("Application scope changed; refresh the source review form")
        if row["source_id"].startswith("research:"):
            raise ValueError("Pasted text and model snapshots cannot verify claims")
        # A human can dispute/reject an unreviewed extract. Support requires
        # that the original PDF was separately approved in the ingest catalog.
        if state=="supported" and not (current["reviewed"] or
                                        current["extraction_status"]=="approved"):
            raise ValueError("Original PDF must be independently approved first")
        # The ingestion metadata is authoritative; a status-only update must
        # also invalidate review even if extracted text happens not to change.
        with closing(_manual_connection(self.research.source,self.research.index)) as pdf:
            current_meta=pdf.execute("""SELECT updated_at,status,reviewed
                FROM documents WHERE sha256=?""",(row["source_id"][4:],)).fetchone()
        if (current_meta is None or current_meta["updated_at"]!=row["source_updated_at"]
            or current_meta["status"] not in ("ready","approved")
            or (state=="supported" and not
                (current_meta["reviewed"] or current_meta["status"]=="approved"))):
            raise ValueError("Original extraction approval or revision changed")
        # Re-read original bytes from the private extraction DB and check the
        # quote exists in exactly the indexed page/part before any promotion.
        text=self.research._section_text(dict(row))
        if row["quote"] not in text:
            raise ValueError("Quote no longer matches the original source")
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            updated=db.execute("""SELECT e.applicability,
                COALESCE(sc.applicability,'') expected
                FROM cr_evidence e LEFT JOIN cr_evidence_scopes sc
                ON sc.source_id=e.source_id WHERE e.id=? AND """
                +self._current_clause("e"),(evidence_id,)).fetchone()
            if (not updated or updated["applicability"]!=expected_scope or
                updated["expected"]!=expected_scope):
                raise ValueError("Evidence or application scope changed during review")
            # Source revision and its independent approval must remain valid.
            self._append_review(db,row,state,reviewer,reason)
        return {"id":evidence_id,"state":state,"human_review":True,
                "weight_training":False}

    def dismiss_conflict(self, conflict_id, reviewer):
        if not isinstance(conflict_id,str) or not ID.fullmatch(conflict_id) or \
           not isinstance(reviewer,str) or not ACTOR.fullmatch(reviewer):
            raise ValueError("Invalid conflict review")
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            cur=db.execute("""UPDATE cr_conflicts SET state='dismissed',
                reviewed_by=?,reviewed_at=?
                WHERE id=? AND state='unreviewed'""",
                (reviewer,time.time(),conflict_id))
            if cur.rowcount!=1:
                raise ValueError("Conflict no longer awaits review")
        return {"id":conflict_id,"state":"dismissed"}

    def status(self, limit=20):
        if type(limit) is not int or not 1<=limit<=30:
            raise ValueError("Invalid result limit")
        with connect(self.db_path) as db:
            pending=db.execute("""SELECT count(*) FROM cr_sections s
                JOIN cr_sources d ON d.id=s.source_id
                LEFT JOIN cr_evidence_indexed i ON i.slot=s.slot AND i.revision=s.revision
                WHERE s.current=1 AND d.eligible=1 AND d.excluded=0
                  AND d.present=1 AND s.source_id LIKE 'pdf:%'
                  AND i.slot IS NULL""").fetchone()[0]
            evidence=db.execute("""SELECT e.id,e.source_id,e.slot,e.revision,
                e.source_revision,e.page,e.part,e.label,e.value,e.unit,e.quote,
                e.quote_sha,e.applicability,
                COALESCE(c.state,'unreviewed') review_state
                FROM cr_evidence e LEFT JOIN cr_claims c ON c.evidence_id=e.id
                WHERE """+self._current_clause("e")+"""
                ORDER BY e.created DESC,e.id LIMIT ?""",(limit,)).fetchall()
            conflicts=db.execute("""SELECT c.id,c.state,c.applicability,
                a.source_id source_a,a.page page_a,a.part part_a,
                a.quote quote_a,b.source_id source_b,b.page page_b,
                b.part part_b,b.quote quote_b
                FROM cr_conflicts c JOIN cr_evidence a ON a.id=c.a
                JOIN cr_evidence b ON b.id=c.b
                WHERE c.state='unreviewed' AND """
                +self._current_clause("a")+" AND "+self._current_clause("b")+"""
                ORDER BY c.created DESC,c.id LIMIT ?""",(limit,)).fetchall()
            reviewed=db.execute("""SELECT c.id,c.state,c.version,e.source_id,
                e.page,e.part,e.quote,e.quote_sha,e.revision
                FROM cr_claims c JOIN cr_evidence e ON e.id=c.evidence_id
                ORDER BY c.updated DESC LIMIT ?""",(limit,)).fetchall()
            # A separate human-supported feed must never include drafts,
            # disputed/rejected entries or source revisions no longer current.
            # This is proof of quote review, not validation of repair safety.
            supported=db.execute("""SELECT c.id,c.version,c.updated,e.source_id,
                d.title,e.page,e.part,e.quote,e.quote_sha,e.revision,
                e.applicability,v.reviewed_by
                FROM cr_claims c JOIN cr_evidence e ON e.id=c.evidence_id
                JOIN cr_sources d ON d.id=e.source_id
                JOIN cr_claim_versions v ON v.claim_id=c.id
                  AND v.version=c.version
                LEFT JOIN cr_evidence_scopes sc ON sc.source_id=e.source_id
                WHERE c.state='supported' AND v.state='supported'
                AND v.applicability=e.applicability
                AND e.applicability=COALESCE(sc.applicability,'')
                AND (d.reviewed=1 OR d.extraction_status='approved')
                AND """+self._current_clause("e")+"""
                ORDER BY c.updated DESC,c.id DESC LIMIT ?""",
                (limit,)).fetchall()
            scope_history=db.execute("""SELECT source_id,version,previous_scope,
                new_scope,reviewed_by,created FROM cr_scope_versions
                ORDER BY created DESC,source_id DESC,version DESC LIMIT ?""",
                (limit,)).fetchall()
            scope_rows=db.execute("""SELECT d.id,d.title,
                COALESCE(s.applicability,'') applicability
                FROM cr_sources d LEFT JOIN cr_evidence_scopes s
                ON s.source_id=d.id WHERE d.kind='pdf' AND d.present=1
                ORDER BY d.priority DESC,d.title LIMIT 40""").fetchall()
            totals=dict(db.execute("""SELECT
                (SELECT count(*) FROM cr_evidence) evidence_total,
                (SELECT count(*) FROM cr_conflicts WHERE state='unreviewed') open_conflicts,
                (SELECT count(*) FROM cr_claims WHERE state='supported') supported_claims,
                (SELECT count(*) FROM cr_claims WHERE state='superseded') superseded_claims""").fetchone())
            return {"counts":totals | {"pending_sections":pending},
                    "evidence":[dict(r) for r in evidence],
                    "conflicts":[dict(r) for r in conflicts],
                    "claims":[dict(r) for r in reviewed],
                    "supported_findings":[dict(r) for r in supported],
                    "scopes":[dict(r) for r in scope_rows],
                    "scope_history":[dict(r) for r in scope_history],
                    "draft_only":False,"automatic_promotion":False,
                    "source_grounded":True,"weight_training":False}
