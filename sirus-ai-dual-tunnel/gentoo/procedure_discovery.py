#!/usr/bin/env python3
"""Opt-in, bounded discovery of procedure-review suggestions from approved PDFs.

A shared heading and matching CMS-admin-declared application scope are NOT
proof of actual applicability or service correctness. Discovery never creates
a verified claim or registers a review target without operator acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time

from manual_relationship_index import KEY, open_index, search
from manual_relationships import HEADING, SOURCE_ID
from manual_procedure_dependencies import inspect
from procedure_review_queue import (_connect, _schema as _queue_schema,
                                    _json, _sha)
from research_evidence import SCOPE, ACTOR

MAX_PEERS = 4
MAX_SUGGESTIONS = 512
MAX_INPUT = 4096
SCAN_INTERVAL = 30
SWEEP_INTERVAL = 900
BLOCK_INTERVAL = 300
LEASE_SECONDS = 120


def _research(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        raise ValueError("Private research database unavailable")
    db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=8)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=8000")
    return db


def _scope(db, sid):
    """Require full admin-reviewed scope revision and live inventory enrollment."""
    row = db.execute("""SELECT s.applicability, v.new_scope, v.reviewed_by, v.version
        FROM cr_evidence_scopes s
        JOIN cr_sources d ON d.id=s.source_id AND d.kind='pdf'
            AND d.present=1 AND d.eligible=1 AND d.reviewed=1
        JOIN cr_scope_versions v ON v.source_id=s.source_id
            AND v.version=(SELECT MAX(version) FROM cr_scope_versions
                WHERE source_id=s.source_id)
        WHERE s.source_id=?""", (sid,)).fetchone()
    if row is None or not ACTOR.fullmatch(row["reviewed_by"]):
        return None
    value = row["applicability"]
    if value != row["new_scope"] or not SCOPE.fullmatch(value):
        return None
    parts = value.split("/")
    # The existing CMS permits shorter optional scopes; discovery must not
    # silently equate distinct engines, assemblies or revisions.
    if len(parts) != 6 or not (parts[2].isdigit() and 1900 <= int(parts[2]) <= 2100):
        return None
    return {"manufacturer": parts[0], "model": parts[1], "year": int(parts[2]),
            "engine": parts[3], "application": parts[4], "revision": parts[5]}


def _selection(db, sid, heading):
    scope = _scope(db, sid)
    return None if scope is None else {
        "source_id": sid, "scope": scope, "heading": heading
    }


def _exact_heading(*, source, index, sid, heading):
    result = search(source=source, index=index, kind="procedure_heading",
                    term=heading.lower(), source_ids=[sid], limit=2)
    if (result["status"] != "source_checked" or not result["complete"] or
            result["has_more"] or len(result["matches"]) != 1 or
            result["matches"][0]["quote"] != heading):
        return None
    return result["matches"][0]


def _check_seed(source_id, heading):
    if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
        raise ValueError("Select one exact approved PDF SHA")
    if (not isinstance(heading, str) or not KEY.fullmatch(heading)
            or not HEADING.fullmatch(heading) or heading != heading.strip()):
        raise ValueError("Select one exact indexed procedure heading")


def _schema(db):
    _queue_schema(db)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS procedure_discovery_seeds (
       id TEXT PRIMARY KEY,
       source_id TEXT NOT NULL, heading TEXT NOT NULL,
       cursor_sha TEXT NOT NULL DEFAULT '',
       state TEXT NOT NULL DEFAULT 'pending'
         CHECK(state IN ('pending','leased','disabled')),
       token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
       next_due REAL NOT NULL DEFAULT 0, last_reason TEXT NOT NULL DEFAULT '',
       scanned INTEGER NOT NULL DEFAULT 0,
       created REAL NOT NULL, updated REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS procedure_discovery_due
       ON procedure_discovery_seeds(state,next_due,updated);
    CREATE TABLE IF NOT EXISTS procedure_discovery_suggestions (
       id TEXT PRIMARY KEY, seed_id TEXT NOT NULL,
       peer_source_id TEXT NOT NULL, heading TEXT NOT NULL,
       request_json TEXT NOT NULL, evidence_hash TEXT NOT NULL,
       status TEXT NOT NULL DEFAULT 'pending'
         CHECK(status IN ('pending','accepted')),
       review_id TEXT NOT NULL DEFAULT '',
       created REAL NOT NULL, updated REAL NOT NULL,
       FOREIGN KEY(seed_id) REFERENCES procedure_discovery_seeds(id)
    );
    CREATE INDEX IF NOT EXISTS procedure_discovery_suggestions_order
       ON procedure_discovery_suggestions(status,created,id);
    """)


def _configured(queue):
    """Checking an unconfigured queue must not create schema or write state."""
    if not Path(queue).is_file():
        return False
    with closing(_connect(queue, writable=False)) as db:
        return db.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='procedure_discovery_seeds'""").fetchone() is not None


def register_seed(*, queue, source, index, research_db, source_id, heading, now=None):
    """Explicit local opt-in; does not enroll peers or run model inference."""
    _check_seed(source_id, heading)
    with closing(_research(research_db)) as metadata:
        if _selection(metadata, source_id, heading) is None:
            raise ValueError("Full CMS-admin scope and current inventory required")
    if _exact_heading(source=source, index=index, sid=source_id, heading=heading) is None:
        raise ValueError("Selected heading unavailable, ambiguous or stale")
    stamp = time.time() if now is None else now
    key = _sha(source_id + "|" + heading.lower())
    with closing(_connect(queue, writable=True)) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            inserted = db.execute("""INSERT OR IGNORE INTO procedure_discovery_seeds
                (id,source_id,heading,created,updated) VALUES(?,?,?,?,?)""",
                (key, source_id, heading, stamp, stamp)).rowcount == 1
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"seed_id": key, "new": inserted,
            "status": "opt_in_only", "automatic_review_registration": False}


def configure_seed(*, queue, seed_id, enabled):
    """Explicit operator pause/resume; revokes a leased worker's publication."""
    from procedure_review_queue import HEX_ID
    if not isinstance(seed_id, str) or not HEX_ID.fullmatch(seed_id):
        raise ValueError("Invalid seed ID")
    if type(enabled) is not bool:
        raise ValueError("Seed enabled must be boolean")
    with closing(_connect(queue, writable=True)) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            found = db.execute("""UPDATE procedure_discovery_seeds
                SET state=?,token='',lease_until=0,next_due=0,updated=?
                WHERE id=?""",
                ("pending" if enabled else "disabled", time.time(), seed_id)).rowcount
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"found":found == 1, "enabled":enabled}


def _claim(db, now):
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("""UPDATE procedure_discovery_seeds
            SET state='pending',token='',lease_until=0
            WHERE state='leased' AND lease_until<=?""", (now,))
        row = db.execute("""SELECT id,source_id,heading,cursor_sha,scanned
            FROM procedure_discovery_seeds
            WHERE state='pending' AND next_due<=?
            ORDER BY next_due,updated,id LIMIT 1""", (now,)).fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        import secrets
        token = secrets.token_hex(16)
        db.execute("""UPDATE procedure_discovery_seeds
            SET state='leased',token=?,lease_until=?,updated=? WHERE id=?""",
            (token, now + LEASE_SECONDS, now, row["id"]))
        db.execute("COMMIT")
        return dict(row), token
    except Exception:
        db.execute("ROLLBACK")
        raise


def _peer_page(index, heading, anchor_sha, after_sha):
    with closing(open_index(index)) as db:
        # Only exact heading mentions; keyset pagination avoids rescanning
        # the same first pages when a common procedure appears in many manuals.
        rows = db.execute("""SELECT DISTINCT sha FROM rel_mentions
            WHERE kind='procedure_heading' AND token=?
                AND sha<>? AND sha>?
            ORDER BY sha LIMIT ?""",
            (heading.lower(), anchor_sha, after_sha, MAX_PEERS + 1)).fetchall()
    return [r["sha"] for r in rows[:MAX_PEERS]], len(rows) > MAX_PEERS


def _report(source, index, request):
    report = inspect(request, source=source, index=index)
    if report.get("complete") is not True:
        return None
    pairs = report["comparisons"]
    if len(pairs) != 1 or pairs[0]["status"] != "possible_order_disagreement":
        return None
    if pairs[0]["scope_differences"] or not pairs[0]["candidates"]:
        return None
    return _sha(_json(pairs[0]["candidates"]))


def scan_once(*, queue, source, index, research_db, now=None):
    """Check <=4 peers of <=1 seed, persist suggestions only, never reviews."""
    if not _configured(queue):
        return {"skipped": True, "reason": "no_opt_in_discovery"}
    stamp = time.time() if now is None else now
    with closing(_connect(queue, writable=True)) as db:
        _schema(db)
        claimed = _claim(db, stamp)
    if claimed is None:
        return {"skipped": True, "reason": "no_due_seeds"}
    seed, token = claimed
    suggested = []
    reason = ""
    batch = []
    has_more = False
    try:
        with closing(_research(research_db)) as metadata:
            metadata.execute("BEGIN")
            anchor = _selection(metadata, seed["source_id"], seed["heading"])
            if anchor is None or _exact_heading(source=source, index=index,
                    sid=seed["source_id"], heading=seed["heading"]) is None:
                reason = "anchor_scope_or_source_stale"
            else:
                batch, has_more = _peer_page(index, seed["heading"],
                                             seed["source_id"][4:], seed["cursor_sha"])
                for sha in batch:
                    sid = "pdf:" + sha
                    peer = _selection(metadata, sid, seed["heading"])
                    if peer is None or peer["scope"] != anchor["scope"]:
                        continue
                    if _exact_heading(source=source, index=index,
                                      sid=sid, heading=seed["heading"]) is None:
                        continue
                    request = {"selections": [anchor, peer]}
                    evidence_hash = _report(source, index, request)
                    if evidence_hash:
                        suggested.append((sha, request, evidence_hash))
    except (OSError, sqlite3.Error, ValueError, KeyError, RuntimeError):
        reason = "bounded_source_discovery_unavailable"
    finished = time.time() if now is None else stamp
    next_due = finished + (BLOCK_INTERVAL if reason else
                          SCAN_INTERVAL if has_more else SWEEP_INTERVAL)
    cursor = (batch[-1] if has_more and batch else "")
    with closing(_connect(queue, writable=True)) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            own = db.execute("""SELECT id FROM procedure_discovery_seeds WHERE id=?
                AND state='leased' AND token=? AND lease_until>?""",
                (seed["id"], token, finished)).fetchone()
            if own is None:
                db.execute("COMMIT")
                return {"skipped": True, "reason": "lease_superseded"}
            accepted = 0
            if not reason:
                for sha, request, evidence_hash in suggested:
                    key = _sha(seed["id"] + "|" + sha)
                    # Full queue requires human handling. Never evict history
                    # or continue expanding storage indefinitely.
                    count = db.execute("""SELECT COUNT(*) FROM procedure_discovery_suggestions
                        """).fetchone()[0]
                    known = db.execute("""SELECT status FROM procedure_discovery_suggestions
                        WHERE id=?""", (key,)).fetchone()
                    if known is None and count >= MAX_SUGGESTIONS:
                        reason = "suggestion_capacity_reached"
                        break
                    db.execute("""INSERT INTO procedure_discovery_suggestions
                        (id,seed_id,peer_source_id,heading,request_json,evidence_hash,
                         created,updated) VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                         request_json=CASE WHEN status='pending' THEN excluded.request_json
                                          ELSE request_json END,
                         evidence_hash=CASE WHEN status='pending' THEN excluded.evidence_hash
                                            ELSE evidence_hash END,
                         updated=CASE WHEN status='pending' THEN excluded.updated
                                      ELSE updated END""",
                        (key,seed["id"],"pdf:"+sha,seed["heading"],
                         _json(request),evidence_hash,finished,finished))
                    accepted += 1
            if reason:
                next_due = finished + BLOCK_INTERVAL
                # A blocked batch is retried rather than skipping unseen peers.
                cursor = seed["cursor_sha"]
            db.execute("""UPDATE procedure_discovery_seeds
                SET state='pending',token='',lease_until=0,cursor_sha=?,
                    next_due=?,last_reason=?,scanned=scanned+?,updated=? WHERE id=?""",
                (cursor,next_due,reason,len(batch),finished,seed["id"]))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"seed_id":seed["id"],"peers_scanned":len(batch),
            "suggestions_observed": accepted, "reason":reason,
            "complete_sweep": not has_more and not reason,
            "review_targets_registered":0, "source_only":True}


def _validate_suggestion(row, *, source, index, research_db):
    request = json.loads(row["request_json"])
    if set(request) != {"selections"} or len(request["selections"]) != 2:
        return None
    with closing(_research(research_db)) as metadata:
        metadata.execute("BEGIN")
        for selection in request["selections"]:
            now = _selection(metadata, selection["source_id"], selection["heading"])
            if now != selection:
                return None
    try:
        fresh = _report(source,index,request)
    except (OSError,sqlite3.Error,ValueError,RuntimeError):
        return None
    return request if fresh == row["evidence_hash"] else None


def suggestions(*, queue, source, index, research_db, limit=10, offset=0):
    if type(limit) is not int or not 1 <= limit <= 20 or (
            type(offset) is not int or not 0 <= offset <= 100000):
        raise ValueError("Invalid suggestion paging")
    if not _configured(queue):
        return {"suggestions":[],
                "scope_authority":"CMS_admin_declared_not_verified_fitment"}
    with closing(_connect(queue, writable=False)) as db:
        rows = db.execute("""SELECT * FROM procedure_discovery_suggestions
            WHERE status='pending' ORDER BY created,id LIMIT ? OFFSET ?""",
            (limit,offset)).fetchall()
    out = []
    for row in rows:
        current = _validate_suggestion(row,source=source,index=index,
                                       research_db=research_db)
        out.append({"id":row["id"],"seed_id":row["seed_id"],
                    "source_ids":[v["source_id"] for v in current["selections"]]
                        if current else [],
                    "status":"unreviewed_suggestion" if current else "stale_or_unavailable",
                    "requires_operator_acceptance":True})
    return {"suggestions":out,"scope_authority":"CMS_admin_declared_not_verified_fitment"}


def accept(*, queue, suggestion_id, source, index, research_db):
    """Operator-only local action. Revalidate before registering a review."""
    from procedure_review_queue import HEX_ID
    if not isinstance(suggestion_id,str) or not HEX_ID.fullmatch(suggestion_id):
        raise ValueError("Invalid suggestion ID")
    with closing(_connect(queue, writable=False)) as db:
        row = db.execute("""SELECT * FROM procedure_discovery_suggestions
            WHERE id=? AND status='pending'""", (suggestion_id,)).fetchone()
    if row is None:
        raise ValueError("Suggestion missing or already accepted")
    request = _validate_suggestion(row,source=source,index=index,
                                   research_db=research_db)
    if request is None:
        raise ValueError("Suggestion stale or scope changed; rescan before accepting")
    canonical = _json(request)
    review_id = _sha(canonical)
    stamp = time.time()
    with closing(_connect(queue, writable=True)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            # No review target can be registered if a concurrent scan has
            # updated or retired the proposed evidence in this private queue.
            current = db.execute("""SELECT evidence_hash,status
                FROM procedure_discovery_suggestions WHERE id=?""",
                (suggestion_id,)).fetchone()
            if (current is None or current["status"] != "pending" or
                    current["evidence_hash"] != row["evidence_hash"]):
                raise ValueError("Suggestion changed during acceptance; rescan first")
            db.execute("""INSERT OR IGNORE INTO procedure_review_targets
                (id,request_json,created,updated) VALUES(?,?,?,?)""",
                (review_id,canonical,stamp,stamp))
            db.execute("""UPDATE procedure_discovery_suggestions
                SET status='accepted',review_id=?,updated=?
                WHERE id=? AND status='pending' AND evidence_hash=?""",
                (review_id,stamp,suggestion_id,row["evidence_hash"]))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"suggestion_id":suggestion_id,"review_id":review_id,
            "status":"accepted_for_unverified_review",
            "automatically_verified":False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--queue",type=Path,required=True)
    p.add_argument("--source",type=Path)
    p.add_argument("--index",type=Path)
    p.add_argument("--research-db",type=Path)
    s=p.add_subparsers(dest="command",required=True)
    s.add_parser("seed");s.add_parser("scan")
    ls=s.add_parser("suggestions")
    ls.add_argument("--limit",type=int,default=10)
    ls.add_argument("--offset",type=int,default=0)
    a=s.add_parser("accept");a.add_argument("--id",required=True)
    pause=s.add_parser("pause-seed");pause.add_argument("--id",required=True)
    resume=s.add_parser("resume-seed");resume.add_argument("--id",required=True)
    args=p.parse_args(argv)
    try:
        if any(x is None for x in (args.source,args.index,args.research_db)):
            raise ValueError("All private source, index and research DB paths required")
        opts={"queue":args.queue,"source":args.source,"index":args.index,
              "research_db":args.research_db}
        if args.command=="seed":
            raw=sys.stdin.read(MAX_INPUT+1)
            if len(raw)>MAX_INPUT:
                raise ValueError("Seed input too large")
            record=json.loads(raw)
            if not isinstance(record,dict) or set(record)!={"source_id","heading"}:
                raise ValueError("Supply only source_id and heading")
            result=register_seed(**opts,**record)
        elif args.command=="scan":
            result=scan_once(**opts)
        elif args.command=="suggestions":
            result=suggestions(**opts,limit=args.limit,offset=args.offset)
        elif args.command in ("pause-seed","resume-seed"):
            result=configure_seed(queue=args.queue,seed_id=args.id,
                                  enabled=args.command=="resume-seed")
        else:
            result=accept(**opts,suggestion_id=args.id)
    except (ValueError,OSError,sqlite3.Error) as e:
        p.error(str(e))
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
