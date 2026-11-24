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
                                 register_seed, configure_seed, review_candidates,
                                 auto_status, configure_auto)
from procedure_review_queue import _json, _sha, _source_signature
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
    candidates = review_candidates(report)
    if not candidates or _sha(_json(candidates)) != row["evidence_hash"]:
        return None
    return request, report


def overview(*, queue, source, index, research_db):
    """Metadata-only, HMAC-authorized view; no text or write on initial GET."""
    if not _configured(queue):
        return {"configured":False,"attestations":attestation.recent(queue),"suggestions":[],"decisions":[],
                "automatic_discovery":auto_status(queue),
                "warning":"No private procedure discovery configured"}
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
        votes = (db.execute("""SELECT digest,suggestion,left_scope,right_scope,decision,
            actor,reason,created FROM procedure_evidence_votes ORDER BY rowid DESC LIMIT 10""").fetchall()
                 if _has_table(db,"procedure_evidence_votes") else [])
        blocked = db.execute("""SELECT id,last_reason,last_version FROM procedure_review_targets
            WHERE status='blocked' ORDER BY updated DESC LIMIT 10""").fetchall()
        seeds = db.execute("""SELECT id,source_id,heading,state,last_reason,
            scanned,next_due FROM procedure_discovery_seeds
            ORDER BY created,id LIMIT 10""").fetchall()
    return {"configured":True,"attestations":attestation.recent(queue),"suggestions":visible,"seeds":[dict(x) for x in seeds],
            "decisions":[dict(x) for x in decision_rows],
            "automatic_discovery":auto_status(queue),
            "votes":[dict(x) for x in votes],"blocked":[dict(x) for x in blocked],
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
        "candidates":review_candidates(report)[:MAX_EVIDENCE],
        "warning":"UNVERIFIED procedure candidate. Revision changes do not establish which manual is older or superseded. Confirm original PDFs and applicability; no action approves a repair.",
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
    if action in ("inspect_procedure","vote_procedure"):
        return procedure_packet(data,queue=queue,source=source,index=index,research_db=research_db)
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
    if action in ("auto_enable","auto_pause") and set(data)=={
            "local_only","action","actor","confirmation"}:
        _validate_actor(data["actor"])
        return configure_auto(queue=queue,enabled=action=="auto_enable",
                              actor=data["actor"],confirmation=data["confirmation"])
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


def _vote_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS procedure_evidence_votes(
            digest TEXT NOT NULL, actor TEXT NOT NULL, suggestion TEXT NOT NULL,
            left_scope TEXT NOT NULL, right_scope TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('support','reject')),
            reason TEXT NOT NULL, created REAL NOT NULL, original_citations TEXT NOT NULL,
            PRIMARY KEY(digest,actor));
        CREATE TRIGGER IF NOT EXISTS procedure_votes_no_update
            BEFORE UPDATE ON procedure_evidence_votes BEGIN
            SELECT RAISE(ABORT,'Immutable procedure evidence vote'); END;
        CREATE TRIGGER IF NOT EXISTS procedure_votes_no_delete
            BEFORE DELETE ON procedure_evidence_votes BEGIN
            SELECT RAISE(ABORT,'Immutable procedure evidence vote'); END;
    """)


def _has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def _procedure_packet(db, id, left_scope, right_scope, source, index, research_db):
    for value in (id,left_scope,right_scope):
        _validate_id(value)
    row = db.execute("SELECT * FROM procedure_discovery_suggestions WHERE id=?", (id,)).fetchone()
    if row is None:
        raise ValueError("Unknown procedure candidate")
    if _has_table(db, "procedure_discovery_decisions") and db.execute(
            "SELECT 1 FROM procedure_discovery_decisions WHERE suggestion_id=? AND action='rejected'",
            (id,)).fetchone():
        raise ValueError("Rejected procedure candidate")
    fresh = _fresh(row,source=source,index=index,research_db=research_db)
    if fresh is None:
        raise ValueError("Procedure source or declared scope changed")
    request, report = fresh
    if report["comparisons"][0]["status"] != "possible_order_disagreement":
        raise ValueError("Revision-change candidates are not order conflicts; use private candidate review")
    scopes = []
    for key, selection in zip((left_scope,right_scope),request["selections"]):
        item = attestation._inspect(db,key,source,research_db)
        if (item["state"] != "support" or item["source_id"] != selection["source_id"]
                or item["evidence"]["scope"] != selection["scope"]):
            raise ValueError("A current supported attestation is required for each selected PDF in order")
        scopes.append(item)
    signature = _source_signature(source,index,request,report["windows"])
    if signature is None:
        raise ValueError("Procedure source changed during inspection")
    digest = _sha(_json([id,request,row["evidence_hash"],signature,left_scope,right_scope]))
    votes = []
    if _has_table(db,"procedure_evidence_votes"):
        votes = [dict(v) for v in db.execute("""SELECT actor,decision,reason,created
            FROM procedure_evidence_votes WHERE digest=? ORDER BY created,actor LIMIT 2""",(digest,))]
    state = ("rejected" if any(v["decision"]=="reject" for v in votes)
             else "supported" if len(votes)==2 else "pending")
    return {"id":id,"digest":digest,"state":state,"votes":votes,"scopes":scopes,
            "windows":report["windows"],
            "disagreements":[{"kind":c["status"],"phrases":c.get("action_keys",c.get("phrases",[]))}
                             for c in report["comparisons"][0]["candidates"]],
            "service_use_approved":False}


def procedure_packet(data, *, queue, source, index, research_db):
    writing = data["action"] == "vote_procedure"
    keys = {"local_only","id","action","left_scope","right_scope"}
    if writing:
        keys.update(("actor","decision","confirmation","reason","expected_hash"))
    if set(data) != keys:
        raise ValueError("Invalid procedure evidence fields")
    with closing(_connect(queue,writable=writing)) as db:
        if writing:
            _validate_actor(data["actor"])
            decision = data["decision"]
            reason = data["reason"]
            if (decision not in ("support","reject") or
                    data["confirmation"] != decision.upper()+" PRINTED EVIDENCE" or
                    not isinstance(reason,str) or not 3 <= len(reason.strip()) <= len(reason) <= MAX_REASON):
                raise ValueError("Confirm a printed evidence decision and give a short reason")
            _vote_schema(db)
            db.execute("BEGIN IMMEDIATE")
        packet = _procedure_packet(db,data["id"],data["left_scope"],data["right_scope"],
                                   source,index,research_db)
        if not writing:
            return packet
        if (data["expected_hash"] != packet["digest"] or packet["state"] != "pending"
                or any(v["actor"]==data["actor"] for v in packet["votes"])):
            raise ValueError("Decision stale, final or already reviewed by this account")
        db.execute("INSERT INTO procedure_evidence_votes VALUES(?,?,?,?,?,?,?,?,?)",
                   (packet["digest"],data["actor"],data["id"],data["left_scope"],
                    data["right_scope"],decision,reason.strip(),time.time(),_json(packet["windows"])))
        db.commit()
    return {"id":data["id"],"action":"vote_procedure","recorded":True,"service_use_approved":False}
