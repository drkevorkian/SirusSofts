#!/usr/bin/env python3
"""Signed-CMS-only procedural candidate review facade.

This module never approves a repair, validates actual fitment, exports to RAG,
or reads generated model drafts. All actions are bounded, source-rechecked,
and intended exclusively for the existing replay-protected CMS admin bridge.
"""
from __future__ import annotations

from contextlib import closing
import json
import re
import sqlite3
import time

from procedure_discovery import (_configured, _connect, _validate_suggestion,
                                 _schema as discovery_schema, _report,
                                 register_seed, configure_seed)
from procedure_review_queue import _json, _sha
import equipment_attestation as attestation
from manual_procedure_dependencies import inspect

HEX = re.compile(r"[0-9a-f]{64}\Z")
ACTOR = re.compile(r"cms-admin:[1-9][0-9]{0,12}\Z")
MAX_REASON = 200
MAX_VISIBLE = 10
MAX_EVIDENCE = 10


def _decisions(db):
    """Additive, immutable operator decision ledger; no source migrations."""
    discovery_schema(db)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS procedure_discovery_decisions (
      suggestion_id TEXT PRIMARY KEY, action TEXT NOT NULL
        CHECK(action IN ('accepted','rejected')),
      actor TEXT NOT NULL, reason TEXT NOT NULL,
      evidence_hash TEXT NOT NULL, review_id TEXT NOT NULL DEFAULT '',
      created REAL NOT NULL,
      FOREIGN KEY(suggestion_id) REFERENCES procedure_discovery_suggestions(id)
    );
    CREATE TRIGGER IF NOT EXISTS procedure_discovery_decisions_no_update
      BEFORE UPDATE ON procedure_discovery_decisions BEGIN
      SELECT RAISE(ABORT,'Operator decisions are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS procedure_discovery_decisions_no_delete
      BEFORE DELETE ON procedure_discovery_decisions BEGIN
      SELECT RAISE(ABORT,'Operator decisions cannot be deleted'); END;
    """)


def _validate_id(value):
    if not isinstance(value, str) or HEX.fullmatch(value) is None:
        raise ValueError("Invalid private suggestion identifier")
    return value


def _validate_actor(actor):
    if not isinstance(actor, str) or ACTOR.fullmatch(actor) is None:
        raise ValueError("Valid authenticated CMS administrator required")
    return actor


def _rows(queue, *, include_accepted=False, limit=MAX_VISIBLE):
    if not _configured(queue):
        return []
    with closing(_connect(queue, writable=False)) as db:
        decisions = db.execute("""SELECT 1 FROM sqlite_master WHERE
            type='table' AND name='procedure_discovery_decisions'""").fetchone()
        where = ("""AND NOT EXISTS (
                SELECT 1 FROM procedure_discovery_decisions d
                WHERE d.suggestion_id=s.id)""" if decisions else "")
        status = "" if include_accepted else "AND s.status='pending'"
        rows = db.execute("""SELECT s.* FROM procedure_discovery_suggestions s
            WHERE 1=1 """+status+" "+where+
            """ ORDER BY s.created,s.id LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]


def _record(queue, suggestion_id):
    with closing(_connect(queue, writable=False)) as db:
        row = db.execute("""SELECT * FROM procedure_discovery_suggestions
            WHERE id=?""", (suggestion_id,)).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("Suggestion unavailable or already accepted")
        decision_exists = db.execute("""SELECT 1 FROM sqlite_master WHERE
            type='table' AND name='procedure_discovery_decisions'""").fetchone()
        if decision_exists and db.execute("""SELECT 1 FROM procedure_discovery_decisions
            WHERE suggestion_id=?""", (suggestion_id,)).fetchone():
            raise ValueError("Suggestion already decided")
        return dict(row)


def _fresh(row, *, source, index, research_db):
    request = _validate_suggestion(row, source=source, index=index,
                                   research_db=research_db)
    if request is None:
        return None
    report = inspect(request, source=source, index=index)
    if report.get("complete") is not True or len(report.get("comparisons",[])) != 1:
        return None
    comparison = report["comparisons"][0]
    if comparison.get("status") != "possible_order_disagreement":
        return None
    if _sha(_json(comparison["candidates"])) != row["evidence_hash"]:
        return None
    return request, report


def overview(*, queue, source, index, research_db):
    """Metadata-only, HMAC-authorized view; no text or write on initial GET."""
    if not _configured(queue):
        return {"configured":False,"attestations":attestation.recent(queue),"suggestions":[],"decisions":[],
                "warning":"No operator-seeded private procedure discovery configured"}
    rows = _rows(queue,limit=MAX_VISIBLE)
    visible=[]
    for row in rows:
        try:
            valid = _validate_suggestion(row,source=source,index=index,
                                         research_db=research_db)
        except (OSError,sqlite3.Error,ValueError,KeyError):
            valid=None
        visible.append({
            "id":row["id"], "seed_id":row["seed_id"],
            "heading":row["heading"],
            "source_ids":([x["source_id"] for x in valid["selections"]]
                          if valid is not None else []),
            "status":"unreviewed_suggestion" if valid else "stale_or_unavailable",
        })
    with closing(_connect(queue, writable=False)) as db:
        found = db.execute("""SELECT 1 FROM sqlite_master WHERE
            type='table' AND name='procedure_discovery_decisions'""").fetchone()
        decision_rows = (db.execute("""SELECT suggestion_id,action,actor,reason,
            evidence_hash,review_id,created FROM procedure_discovery_decisions
            ORDER BY created DESC,suggestion_id LIMIT ?""",
            (MAX_VISIBLE,)).fetchall() if found else [])
        seeds = db.execute("""SELECT id,source_id,heading,state,last_reason,
            scanned,next_due FROM procedure_discovery_seeds
            ORDER BY created,id LIMIT 10""").fetchall()
    return {"configured":True,"attestations":attestation.recent(queue),"suggestions":visible,"seeds":[dict(x) for x in seeds],
            "decisions":[dict(x) for x in decision_rows],
            "note":"Candidate metadata only; no proof of fitment, validated conflict or repair"}


def detail(*, queue, suggestion_id, source, index, research_db):
    """Show freshly checked original citations only, never stored stale excerpts."""
    _validate_id(suggestion_id)
    if not _configured(queue):
        raise ValueError("No private procedure discovery configured")
    row = _record(queue, suggestion_id)
    fresh = _fresh(row,source=source,index=index,research_db=research_db)
    if fresh is None:
        return {"id":suggestion_id,"status":"stale_or_unavailable",
                "reviewable":False}
    request, report = fresh
    return {
        "id":suggestion_id, "status":"unreviewed_suggestion", "reviewable":True,
        "evidence_hash":row["evidence_hash"],
        "scope_authority":"cms_admin_declared_not_independently_verified",
        "selections":request["selections"],
        "windows":[{
            "source_id":w["source_id"],
            "heading_citation":w["heading_citation"],
            "steps":w["steps"][:32],
            "explicit_relations":w["explicit_relations"][:16],
        } for w in report["windows"][:2]],
        "candidates":report["comparisons"][0]["candidates"][:MAX_EVIDENCE],
        "warning":"UNVERIFIED order candidate. Confirm original PDFs and vehicle applicability before deciding; no action approves a repair.",
    }


def decide(*, queue, suggestion_id, action, actor, expected_hash, confirmation,
           reason, source, index, research_db, now=None):
    """Explicit admin decision, atomic against another local reviewer/seed scan."""
    _validate_id(suggestion_id)
    _validate_actor(actor)
    if action not in ("accept","reject"):
        raise ValueError("Unknown procedure review decision")
    if not isinstance(expected_hash,str) or HEX.fullmatch(expected_hash) is None:
        raise ValueError("Refresh the candidate and use its source evidence hash")
    expected_confirm = "ACCEPT FOR REVIEW" if action=="accept" else "REJECT SUGGESTION"
    if confirmation != expected_confirm:
        raise ValueError("Explicit review confirmation required")
    if (not isinstance(reason,str) or not 3 <= len(reason.strip()) <= MAX_REASON
            or len(reason) > MAX_REASON):
        raise ValueError("Provide a short recorded review reason")
    row = _record(queue,suggestion_id)
    if row["evidence_hash"] != expected_hash:
        raise ValueError("Candidate evidence changed; reload before deciding")
    fresh = _fresh(row,source=source,index=index,research_db=research_db)
    if fresh is None:
        raise ValueError("Original evidence or applicability changed; reload")
    request,_ = fresh
    stamp=time.time() if now is None else now
    review_id = _sha(_json(request)) if action=="accept" else ""
    with closing(_connect(queue,writable=True)) as db:
        _decisions(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            current=db.execute("""SELECT status,evidence_hash FROM
                procedure_discovery_suggestions WHERE id=?""",
                (suggestion_id,)).fetchone()
            if (current is None or current["status"]!="pending"
                    or current["evidence_hash"]!=expected_hash
                    or db.execute("""SELECT 1 FROM procedure_discovery_decisions
                        WHERE suggestion_id=?""",(suggestion_id,)).fetchone()):
                raise ValueError("Candidate changed or was already decided")
            if action=="accept":
                db.execute("""INSERT OR IGNORE INTO procedure_review_targets
                    (id,request_json,created,updated) VALUES(?,?,?,?)""",
                    (review_id,_json(request),stamp,stamp))
                db.execute("""UPDATE procedure_discovery_suggestions
                    SET status='accepted',review_id=?,updated=?
                    WHERE id=?""",(review_id,stamp,suggestion_id))
            db.execute("""INSERT INTO procedure_discovery_decisions
                (suggestion_id,action,actor,reason,evidence_hash,review_id,created)
                VALUES(?,?,?,?,?,?,?)""",
                (suggestion_id,"accepted" if action=="accept" else "rejected",
                 actor,reason.strip(),expected_hash,review_id,stamp))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"suggestion_id":suggestion_id,"action":action,
            "review_id":review_id,"recorded":True,
            "verified_repair":False,"model_training":False}


def control(data, *, queue, source, index, research_db):
    """Strict fixed routing from a separately HMAC-verified local admin POST."""
    if not isinstance(data,dict) or data.get("local_only") is not True:
        raise ValueError("Private signed administrative request required")
    action=data.get("action")
    scope_ops = {
        "scope_detail": (attestation.inspect_scope, {"id"}),
        "propose_scope": (attestation.propose, {"source_id","page","part","quote",
                                              "expected_scope","actor"}),
        "review_scope": (attestation.review, {"id","decision","reason","confirmation","actor"}),
    }
    if isinstance(action, str) and action in scope_ops:
        handler, keys = scope_ops[action]
        if set(data) != keys | {"local_only","action"}:
            raise ValueError("Invalid scope evidence request")
        return handler(queue=queue, source=source, research_db=research_db,
                       **{key:data[key] for key in keys})
    if action=="detail" and set(data)=={"local_only","action","id"}:
        return detail(queue=queue,suggestion_id=data["id"],source=source,
                      index=index,research_db=research_db)
    if action=="seed" and set(data)=={"local_only","action","source_id",
                                      "heading","actor"}:
        _validate_actor(data["actor"])
        return register_seed(queue=queue,source=source,index=index,
                             research_db=research_db,
                             source_id=data["source_id"],heading=data["heading"])
    if action in ("pause","resume") and set(data)=={"local_only","action",
                                                     "seed_id","actor"}:
        _validate_actor(data["actor"])
        return configure_seed(queue=queue,seed_id=data["seed_id"],
                              enabled=action=="resume")
    if action in ("accept","reject") and set(data)=={
            "local_only","action","id","actor","expected_hash",
            "confirmation","reason"}:
        return decide(queue=queue,suggestion_id=data["id"],action=action,
                      actor=data["actor"],expected_hash=data["expected_hash"],
                      confirmation=data["confirmation"],reason=data["reason"],
                      source=source,index=index,research_db=research_db)
    raise ValueError("Unsupported private procedure-review operation")
