"""Private two-person evidence review of one explicitly cited equipment scope."""
from contextlib import closing
from pathlib import Path
import json
import re
import sqlite3
import time

from manual_rag import connect_source
from manual_relationships import SOURCE_ID
from procedure_discovery import _research, _scope
from procedure_review_queue import _connect, _json, _sha

FIELDS = ("manufacturer", "model", "year", "engine", "application", "revision")
ACTOR = re.compile(r"cms-admin:[1-9][0-9]{0,12}\Z")
ID = re.compile(r"[a-f0-9]{64}\Z")


def _schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS equipment_attestations(
            id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, proposer TEXT NOT NULL,
            created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS equipment_attestation_reviews(
            id TEXT PRIMARY KEY REFERENCES equipment_attestations(id),
            decision TEXT NOT NULL CHECK(decision IN ('support','reject')),
            reviewer TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL);
    """)
    for table in ("equipment_attestations", "equipment_attestation_reviews"):
        for action in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action}
                BEFORE {action} ON {table} BEGIN
                SELECT RAISE(ABORT,'Immutable scope evidence'); END""")


def _snapshot(source, research_db, source_id, page, part, quote, expected_scope):
    if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
        raise ValueError("Invalid original PDF identifier")
    if any(type(n) is not int or not 1 <= n <= 100000 for n in (page, part)):
        raise ValueError("Invalid original page or part")
    if not isinstance(quote, str) or not 6 <= len(quote.strip()) <= len(quote) <= 1500:
        raise ValueError("Supply one bounded original quotation")
    with closing(_research(research_db)) as meta:
        meta.execute("BEGIN")
        scope = _scope(meta, source_id)
        if scope is None or "/".join(str(scope[k]) for k in FIELDS) != expected_scope:
            raise ValueError("Declared equipment scope is incomplete or changed")
        revision = meta.execute("""SELECT version,reviewed_by FROM cr_scope_versions
            WHERE source_id=? ORDER BY version DESC LIMIT 1""", (source_id,)).fetchone()
    for value in scope.values():
        if not re.search(r"(?<![\w.-])" + re.escape(str(value)) + r"(?![\w-])",
                         quote, flags=re.IGNORECASE):
            raise ValueError("The citation must explicitly contain each equipment value")
    if Path(source).is_symlink():
        raise ValueError("Unsafe PDF catalog")
    with closing(connect_source(Path(source))) as src:
        rows = src.execute("""SELECT d.updated_at,d.excerpts,CASE WHEN length(CAST(e.text AS BLOB))
            <=65536 THEN e.text END AS text
            FROM documents d JOIN excerpts e ON d.sha256=e.sha256
            WHERE d.sha256=? AND d.status='approved' AND d.reviewed=1
              AND d.excerpts>0 AND e.page=? AND e.part=? LIMIT 2""",
            (source_id[4:], page, part)).fetchall()
    if len(rows) != 1 or not isinstance(rows[0]["text"], str) or rows[0]["text"].count(quote) != 1:
        raise ValueError("Approved original PDF citation unavailable or ambiguous")
    return dict(source_id=source_id, page=page, part=part, quote=quote,
                expected_scope=expected_scope, scope=scope, scope_version=revision[0],
                declared_by=revision[1], updated_at=rows[0]["updated_at"],
                excerpt_count=rows[0]["excerpts"], excerpt_hash=_sha(rows[0]["text"]),
                quote_hash=_sha(quote))


def propose(*, queue, source, research_db, actor, **citation):
    if not isinstance(actor, str) or not ACTOR.fullmatch(actor):
        raise ValueError("Authenticated CMS administrator required")
    snapshot = _json(_snapshot(source, research_db, **citation))
    key = _sha(snapshot)
    with closing(_connect(queue, writable=True)) as db:
        _schema(db)
        inserted = db.execute("""INSERT INTO equipment_attestations VALUES(?,?,?,?)
            ON CONFLICT(id) DO NOTHING""", (key, snapshot, actor, time.time())).rowcount
    if not inserted:
        raise ValueError("This evidence already has a proposal")
    return {"id": key, "recorded": True, "action": "propose_scope"}


def _inspect(db, key, source, research_db):
    if not isinstance(key, str) or not ID.fullmatch(key):
        raise ValueError("Invalid scope proposal identifier")
    row = db.execute("""SELECT a.snapshot,a.proposer,r.decision,r.reviewer,r.reason
        FROM equipment_attestations a LEFT JOIN equipment_attestation_reviews r
        ON r.id=a.id WHERE a.id=?""", (key,)).fetchone()
    if row is None or _sha(row["snapshot"]) != key:
        raise ValueError("Unavailable scope proposal")
    saved = json.loads(row["snapshot"])
    citation = {k: saved[k] for k in ("source_id","page","part","quote","expected_scope")}
    try:
        current = _snapshot(source, research_db, **citation) == saved
    except (ValueError, sqlite3.Error, OSError):
        current = False
    return {"id": key, "state": (row["decision"] or "pending") if current else "blocked",
            "historical_decision": row["decision"], "proposer": row["proposer"],
            "reviewer": row["reviewer"], "reason": row["reason"],
            "source_id": saved["source_id"], "scope_version": saved["scope_version"],
            "evidence": saved if current else None, "service_use_approved": False}


def inspect_scope(*, queue, source, research_db, id):
    with closing(_connect(queue, writable=False)) as db:
        return _inspect(db, id, source, research_db)


def review(*, queue, source, research_db, actor, id, decision, reason, confirmation):
    if not isinstance(actor, str) or not ACTOR.fullmatch(actor):
        raise ValueError("Authenticated CMS administrator required")
    if decision not in ("support","reject") or confirmation != decision.upper()+" SCOPE":
        raise ValueError("Explicit scope evidence decision required")
    if not isinstance(reason,str) or not 3 <= len(reason.strip()) <= len(reason) <= 200:
        raise ValueError("Provide a short review reason")
    with closing(_connect(queue, writable=True)) as db:
        db.execute("BEGIN IMMEDIATE")
        item = _inspect(db, id, source, research_db)
        if item["state"] != "pending":
            raise ValueError("Scope proposal changed or already decided")
        if actor in (item["proposer"], item["evidence"]["declared_by"]):
            raise ValueError("A separate administrator must review the original citation")
        db.execute("INSERT INTO equipment_attestation_reviews VALUES(?,?,?,?,?)",
                   (id, decision, actor, reason.strip(), time.time()))
        db.commit()
    return {"id": id, "recorded": True, "action": "review_scope"}


def recent(queue):
    if not Path(queue).is_file():
        return []
    with closing(_connect(queue, writable=False)) as db:
        if not db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                AND name='equipment_attestations'""").fetchone():
            return []
        return [dict(r) for r in db.execute("""SELECT a.id,a.proposer,r.decision
            FROM equipment_attestations a LEFT JOIN equipment_attestation_reviews r
            ON r.id=a.id ORDER BY a.rowid DESC LIMIT 10""")]
