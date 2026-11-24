#!/usr/bin/env python3
"""Private durable rechecks of explicitly selected procedural review candidates.

This is NOT a conflict verifier, a source of service instructions, a public
endpoint, an automatic source-discovery tool, or a model-training pipeline.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import time

from manual_rag import connect_source
from manual_procedure_dependencies import _validate, inspect
from manual_relationship_index import open_index

MAX_INPUT = 16000
MAX_EVENTS = 64
MAX_CANDIDATES = 64
MAX_SNAPSHOT_BYTES = 96000
LEASE_SECS = 120
RECHECK_SECS = 3600
BLOCK_RETRY_SECS = 300
HEX_ID = re.compile(r"[a-f0-9]{64}\Z")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _queue_path(path, *, create=False):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError("Unsafe private procedure-review database path")
    if create:
        # Explicitly create with owner-only permissions, independent of umask.
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Private procedure-review database unavailable")
    if path.stat().st_mode & 0o077:
        raise ValueError("Procedure-review database must be owner-only")
    return path


def _connect(path, *, writable):
    path = _queue_path(path, create=writable)
    if writable:
        db = sqlite3.connect(path, timeout=10, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
    else:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
        db.execute("PRAGMA query_only=ON")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS procedure_review_targets(
      id TEXT PRIMARY KEY, request_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','leased','monitoring','blocked')),
      lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
      next_due REAL NOT NULL DEFAULT 0, last_checked REAL,
      last_digest TEXT NOT NULL DEFAULT '', last_version INTEGER NOT NULL DEFAULT 0,
      current_candidates INTEGER NOT NULL DEFAULT 0,
      last_reason TEXT NOT NULL DEFAULT '',
      created REAL NOT NULL, updated REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS procedure_review_due
      ON procedure_review_targets(status,next_due,updated);
    CREATE TABLE IF NOT EXISTS procedure_review_observations(
      target_id TEXT NOT NULL, version INTEGER NOT NULL,
      observed_at REAL NOT NULL, result TEXT NOT NULL
        CHECK(result IN ('monitoring','blocked')),
      reason TEXT NOT NULL, digest TEXT NOT NULL,
      count INTEGER NOT NULL, evidence_json TEXT NOT NULL,
      changes_json TEXT NOT NULL,
      PRIMARY KEY(target_id,version),
      FOREIGN KEY(target_id) REFERENCES procedure_review_targets(id)
    );
    CREATE TRIGGER IF NOT EXISTS procedure_review_observations_no_update
      BEFORE UPDATE ON procedure_review_observations BEGIN
      SELECT RAISE(ABORT,'Review observations are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS procedure_review_observations_no_delete
      BEFORE DELETE ON procedure_review_observations BEGIN
      SELECT RAISE(ABORT,'Review observations cannot be deleted'); END;
    """)


def register(*, queue, request, now=None):
    """Only explicit SHA/heading/scope selections become durable work."""
    _validate(request)
    canonical = _json(request)
    if len(canonical) > MAX_INPUT:
        raise ValueError("Registration exceeds bounded request size")
    key = _sha(canonical)
    stamp = time.time() if now is None else now
    with closing(_connect(queue, writable=True)) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            inserted = db.execute("""INSERT OR IGNORE INTO procedure_review_targets
                (id,request_json,created,updated) VALUES(?,?,?,?)""",
                (key, canonical, stamp, stamp)).rowcount == 1
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"id": key, "registered": inserted, "status": "pending" if inserted else "already_registered",
            "note": "Explicit operator selection; no autonomous cross-model discovery"}


def _source_signature(source, index, request, windows):
    """Observed ingest metadata and selected excerpt bytes, never model claims."""
    window_map = {w["source_id"]: w for w in windows}
    state = []
    with closing(connect_source(Path(source))) as src, closing(open_index(index)) as derived:
        src.execute("BEGIN")
        derived.execute("BEGIN")
        for selection in request["selections"]:
            sid = selection["source_id"]
            sha = sid[4:]
            row = src.execute("""SELECT filename,updated_at,excerpts,status,reviewed
                FROM documents WHERE sha256=?""", (sha,)).fetchone()
            if row is None:
                state.append([sid, "missing"])
                continue
            item = [sid, str(row["filename"]), int(row["updated_at"]),
                    int(row["excerpts"]), str(row["status"]), int(row["reviewed"])]
            if sid in window_map:
                # An index/source revision mismatch or an unannounced edit
                # between evaluation and persistence must discard candidates.
                indexed = derived.execute("SELECT * FROM rel_docs WHERE sha=?", (sha,)).fetchone()
                if (row["status"] != "approved" or not row["reviewed"]
                        or indexed is None or not indexed["complete"]
                        or indexed["filename"] != row["filename"]
                        or indexed["updated_at"] != row["updated_at"]
                        or indexed["excerpt_count"] != row["excerpts"]):
                    return None
                w = window_map[sid]
                excerpts = src.execute("""SELECT text FROM excerpts WHERE sha256=?
                    AND page=? AND part=?""", (sha, w["page"], w["part"])).fetchall()
                if len(excerpts) != 1:
                    return None
                body = excerpts[0]["text"]
                current_lines = {line.strip() for line in body.splitlines()}
                used_quotes = [w["heading_citation"]]
                used_quotes += [s["citation"] for s in w["steps"]]
                used_quotes += [p["citation"] for p in w["explicit_relations"]]
                if any(q["quote"] not in current_lines
                       or _sha(q["quote"]) != q["quote_sha256"]
                       for q in used_quotes):
                    return None
                item.extend((w["page"], w["part"], _sha(body)))
            state.append(item)
    return _sha(_json(state))


def _snapshot(report, target):
    """Summarize only source-cited candidate reviews; bound all persisted data."""
    if not isinstance(report, dict) or report.get("complete") is not True or (
            report.get("status") != "source_checked_excerpt_windows"):
        return [], None
    all_candidates = []
    for comparison in report["comparisons"]:
        candidates = (comparison["candidates"]
                      if comparison["status"] == "possible_order_disagreement" else [])
        if comparison.get("revision_review") is not None:
            candidates = [*candidates, comparison["revision_review"]]
        for candidate in candidates:
            key = {
                "kind": candidate["status"],
                "source_ids": comparison["source_ids"],
                "action_keys": candidate.get("action_keys"),
                "phrases": candidate.get("phrases"),
            }
            stable_id = _sha(target + "|" + _json(key))
            all_candidates.append({
                "id": stable_id, "status": "unreviewed_candidate",
                "scope_differences": comparison["scope_differences"],
                "type": candidate["status"],
                "source_ids": comparison["source_ids"],
                "evidence": candidate,
            })
            if len(all_candidates) > MAX_CANDIDATES:
                return [], "candidate_limit"
    encoded = _json(all_candidates)
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        return [], "snapshot_limit"
    return all_candidates, None


def _claim(db, stamp):
    """One short lease: source evaluation happens outside SQLite write locks."""
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("""UPDATE procedure_review_targets
            SET status='pending',lease_token='',lease_until=0,updated=?
            WHERE status='leased' AND lease_until<=?""", (stamp, stamp))
        row = db.execute("""SELECT id,request_json FROM procedure_review_targets
            WHERE status IN ('pending','monitoring','blocked') AND next_due<=?
            ORDER BY next_due,updated,id LIMIT 1""", (stamp,)).fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        token = secrets.token_hex(16)
        db.execute("""UPDATE procedure_review_targets
            SET status='leased',lease_token=?,lease_until=?,updated=?
            WHERE id=?""", (token,stamp+LEASE_SECS,stamp,row["id"]))
        db.execute("COMMIT")
        return row["id"], row["request_json"], token
    except Exception:
        db.execute("ROLLBACK")
        raise


def tick(*, queue, source, index, now=None, evaluator=inspect):
    """Evaluate at most one due explicitly registered selection.

    A stale approval/index blocks and clears current candidates. Historical
    observations remain private and clearly time-scoped; never auto-promoted.
    """
    if not Path(queue).is_file():
        return {"skipped": True, "reason": "no_registered_reviews"}
    stamp = time.time() if now is None else now
    with closing(_connect(queue, writable=True)) as db:
        _schema(db)
        claim = _claim(db, stamp)
    if claim is None:
        return {"skipped": True, "reason": "none_due"}
    target, raw, token = claim
    request = json.loads(raw)
    candidates = []
    reason = ""
    snapshot_signature = ""
    try:
        report = evaluator(request, source=source, index=index)
        candidates, limit_error = _snapshot(report, target)
        if limit_error:
            reason = limit_error
        elif not report.get("complete"):
            reason = str(report.get("status", "source_unavailable"))[:80]
        snapshot_signature = _source_signature(source, index, request, report.get("windows", []))
        if snapshot_signature is None:
            reason = "source_changed_during_observation"
    except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError, TypeError):
        # Never leak PDF quotations or private paths into shared error logs.
        reason = "source_recheck_unavailable"
    result = "blocked" if reason else "monitoring"
    if reason:
        candidates = []
    packet = {"result": result, "reason": reason,
              "signature": snapshot_signature, "candidates": candidates}
    fingerprint = _sha(_json(packet))
    # In production, fence against a lease that actually expired while the
    # evaluator was running. Injected test clocks remain deterministic.
    finished = time.time() if now is None else stamp
    next_due = finished + (BLOCK_RETRY_SECS if reason else RECHECK_SECS)
    with closing(_connect(queue, writable=True)) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            current = db.execute("""SELECT last_version,last_digest FROM procedure_review_targets
                WHERE id=? AND status='leased' AND lease_token=?
                AND lease_until>?""", (target,token,finished)).fetchone()
            if current is None:
                db.execute("COMMIT")
                return {"skipped": True, "reason": "lease_superseded"}
            changed = current["last_digest"] != fingerprint
            version = current["last_version"] + int(changed)
            if changed:
                previous = []
                if current["last_version"]:
                    old = db.execute("""SELECT evidence_json FROM procedure_review_observations
                        WHERE target_id=? AND version=?""",
                        (target,current["last_version"])).fetchone()
                    if old is not None:
                        previous = json.loads(old["evidence_json"])
                previous_by_id = {item["id"]: item for item in previous}
                current_by_id = {item["id"]: item for item in candidates}
                change_summary = {
                    "new": sorted(set(current_by_id)-set(previous_by_id)),
                    "no_longer_observed": sorted(set(previous_by_id)-set(current_by_id)),
                    "evidence_changed": sorted(
                        key for key in current_by_id.keys() & previous_by_id.keys()
                        if current_by_id[key] != previous_by_id[key]),
                }
                db.execute("""INSERT INTO procedure_review_observations
                    (target_id,version,observed_at,result,reason,digest,count,evidence_json,changes_json)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (target,version,finished,result,reason,fingerprint,len(candidates),
                     _json(candidates),_json(change_summary)))
            db.execute("""UPDATE procedure_review_targets
                SET status=?,lease_token='',lease_until=0,next_due=?,
                    last_checked=?,last_digest=?,last_version=?,
                    current_candidates=?,last_reason=?,updated=?
                WHERE id=?""", (result,next_due,finished,fingerprint,version,
                                len(candidates),reason,finished,target))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"id": target, "status": result, "reason": reason,
            "new_observation": changed, "version": version,
            "current_candidate_count": len(candidates),
            "source_only": True, "verified_conflict": False}


def request_recheck(*, queue, target):
    if not isinstance(target, str) or not HEX_ID.fullmatch(target):
        raise ValueError("Invalid review ID")
    with closing(_connect(queue, writable=True)) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            found = db.execute("""UPDATE procedure_review_targets
                SET next_due=0 WHERE id=? AND status!='leased'""", (target,)).rowcount
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"scheduled": found == 1}


def list_reviews(*, queue, limit=20, offset=0):
    if (type(limit) is not int or not 1 <= limit <= 50
            or type(offset) is not int or not 0 <= offset <= 100000):
        raise ValueError("Invalid paging")
    with closing(_connect(queue, writable=False)) as db:
        rows = db.execute("""SELECT id,status,last_checked,last_version,
            current_candidates,last_reason,next_due FROM procedure_review_targets
            ORDER BY created,id LIMIT ? OFFSET ?""", (limit,offset)).fetchall()
    return {"reviews": [dict(row) for row in rows],
            "note": "Candidate counts are from last observed sources, NOT live verified repair conflicts"}


def history(*, queue, target, limit=20, offset=0, include_evidence=False):
    if not isinstance(target, str) or not HEX_ID.fullmatch(target):
        raise ValueError("Invalid review ID")
    if (type(limit) is not int or not 1 <= limit <= MAX_EVENTS
            or type(offset) is not int or not 0 <= offset <= 100000
            or type(include_evidence) is not bool):
        raise ValueError("Invalid paging or evidence selection")
    with closing(_connect(queue, writable=False)) as db:
        rows = db.execute("""SELECT * FROM procedure_review_observations
            WHERE target_id=? ORDER BY version DESC LIMIT ? OFFSET ?""",
            (target,limit,offset)).fetchall()
    output = []
    for row in rows:
        item = {k:row[k] for k in ("version","observed_at","result","reason","count","digest")}
        item["candidate_changes"] = json.loads(row["changes_json"])
        if include_evidence:
            item["historical_unverified_evidence"] = json.loads(row["evidence_json"])
        output.append(item)
    return {"id":target, "observations":output,
            "warning": "Historical, unverified candidates; recheck original manuals before use"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--queue", type=Path, required=True)
    p.add_argument("--source", type=Path)
    p.add_argument("--index", type=Path)
    sub = p.add_subparsers(dest="command",required=True)
    sub.add_parser("register")
    sub.add_parser("tick")
    ls = sub.add_parser("list")
    ls.add_argument("--limit",type=int,default=20)
    ls.add_argument("--offset",type=int,default=0)
    again = sub.add_parser("recheck")
    again.add_argument("--id",required=True)
    hist = sub.add_parser("history")
    hist.add_argument("--id",required=True)
    hist.add_argument("--limit",type=int,default=20)
    hist.add_argument("--offset",type=int,default=0)
    hist.add_argument("--include-evidence",action="store_true")
    args = p.parse_args(argv)
    try:
        if args.command in ("register","tick") and (
                args.source is None or args.index is None):
            raise ValueError("--source and --index required for registration and tick")
        if args.command == "register":
            raw = sys.stdin.read(MAX_INPUT + 1)
            if len(raw) > MAX_INPUT:
                raise ValueError("Request exceeds 16000 characters")
            output = register(queue=args.queue,request=json.loads(raw))
        elif args.command == "tick":
            output = tick(queue=args.queue,source=args.source,index=args.index)
        elif args.command == "list":
            output = list_reviews(queue=args.queue,limit=args.limit,offset=args.offset)
        elif args.command == "recheck":
            output = request_recheck(queue=args.queue,target=args.id)
        else:
            output = history(queue=args.queue,target=args.id,limit=args.limit,
                             offset=args.offset,include_evidence=args.include_evidence)
    except (OSError,sqlite3.Error,ValueError) as e:
        p.error(str(e))
    print(json.dumps(output,indent=2,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
