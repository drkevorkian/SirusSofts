"""Signed, domain-separated Gentoo PDF control API over the existing SSH loopback.

The Ubuntu CMS is the only intended caller. The node key is the existing
32-byte signed-work-broker key; no credentials are exposed to the browser.
No arbitrary commands, filesystem reads by supplied path, or public access.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import manual_view

ROOT = Path("/srv/sirussofts/storage")
MAX_BODY = 4096
DOMAIN = b"sirus-pdf-admin-v1\n"
ALLOWED_STATES = {"queued", "processing", "ready", "failed", "approved"}
OPS = {"status", "preview", "pause", "resume", "workers", "retry", "approve", "unapprove", "approve_all", "manual_meta", "manual_page"}
SHA = re.compile(r"[a-f0-9]{64}\Z")
NONCE = re.compile(r"[a-f0-9]{32}\Z")


class PDFAdminError(Exception):
    pass


def verify_signature(raw: bytes, signature: str, key: bytes) -> bool:
    return (len(key) == 32 and isinstance(signature, str) and
            re.fullmatch(r"[a-f0-9]{64}", signature) is not None and
            hmac.compare_digest(
                hmac.new(key, DOMAIN + raw, hashlib.sha256).hexdigest(),
                signature))


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise PDFAdminError("Duplicate JSON attribute")
        result[name] = value
    return result


def database(root: Path) -> sqlite3.Connection:
    if not root.is_dir() or root.is_symlink() or not os.path.ismount(root):
        raise PDFAdminError("Dedicated SSD is not mounted")
    path = root / "databases" / "pdf_ingest.sqlite3"
    if path.is_symlink() or not path.is_file():
        raise PDFAdminError("Gentoo PDF ingest database not installed")
    # SQLite file is held on the Gentoo SSD, never copied over the tunnel.
    db = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    return db


def ensure_metadata(db: sqlite3.Connection):
    db.execute("""CREATE TABLE IF NOT EXISTS pdf_admin_nonces (
        nonce TEXT PRIMARY KEY, seen_at INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS pdf_admin_audit (
        id INTEGER PRIMARY KEY, at INTEGER NOT NULL, action TEXT NOT NULL,
        sha256 TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '')""")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise PDFAdminError("Invalid numeric parameter")
    return value


def digest_arg(value):
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise PDFAdminError("Invalid document SHA-256")
    return value


def snapshot(db: sqlite3.Connection, root: Path, payload: dict) -> dict:
    limit = integer(payload.get("limit", 30), 1, 100)
    offset = integer(payload.get("offset", 0), 0, 1000000)
    state = payload.get("filter", "")
    if not isinstance(state, str) or state not in ALLOWED_STATES | {""}:
        raise PDFAdminError("Unsupported state filter")
    needle = payload.get("query", "")
    if not isinstance(needle, str) or len(needle) > 120 or any(ord(c)<32 for c in needle):
        raise PDFAdminError("Invalid search query")
    clauses = []
    params = []
    if state:
        clauses.append("status=?")
        params.append(state)
    if needle:
        clauses.append("filename LIKE ? ESCAPE '\\'")
        params.append("%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    totals = {row["status"]: row["n"] for row in db.execute(
        "SELECT status, COUNT(*) AS n FROM documents GROUP BY status")}
    eligible_ready = db.execute("""
        SELECT COUNT(*) FROM documents AS d
        WHERE d.status='ready' AND d.reviewed=0 AND d.excerpts>0
          AND EXISTS (
            SELECT 1 FROM excerpts AS e WHERE e.sha256=d.sha256
            AND length(trim(e.text))>0
          )""").fetchone()[0]
    results = db.execute("SELECT COUNT(*) AS n FROM documents" + where, params).fetchone()["n"]
    rows = [dict(row) for row in db.execute(
        """SELECT sha256,filename,bytes,status,pages,excerpts,attempts,
                  substr(error,1,180) AS error,reviewed,updated_at
           FROM documents""" + where +
        " ORDER BY updated_at DESC,sha256 LIMIT ? OFFSET ?",
        [*params, limit, offset])]
    settings = dict(db.execute("SELECT key,value FROM settings WHERE key IN ('workers','paused')").fetchall())
    count = int(settings.get("workers", "3"))
    if not 3 <= count <= 64:
        raise PDFAdminError("Unexpected worker configuration")
    storage = os.statvfs(root)
    return {"ok": True, "operation": "status",
            "total": sum(totals.values()), "filtered": results, "counts": totals,
            "eligible_ready": eligible_ready,
            "workers": count, "paused": settings.get("paused") == "1",
            "storage": {"total_bytes": storage.f_blocks * storage.f_frsize,
                        "free_bytes": storage.f_bavail * storage.f_frsize},
            "limit": limit, "offset": offset, "documents": rows,
            "updated_at": int(time.time())}


def preview(db: sqlite3.Connection, payload: dict) -> dict:
    digest = digest_arg(payload.get("sha256"))
    offset = integer(payload.get("offset", 0), 0, 1000000)
    doc = db.execute("SELECT filename,status,pages,excerpts,reviewed FROM documents WHERE sha256=?",
                     (digest,)).fetchone()
    if doc is None:
        raise PDFAdminError("Unknown PDF manual")
    excerpts = [dict(x) for x in db.execute(
        """SELECT page,part,substr(text,1,1400) AS text
           FROM excerpts WHERE sha256=? ORDER BY page,part LIMIT 8 OFFSET ?""",
        (digest, offset))]
    return {"ok": True, "operation": "preview", "sha256": digest,
            "document": dict(doc), "offset": offset, "excerpts": excerpts}


def update(db: sqlite3.Connection, payload: dict, op: str) -> dict:
    digest = ""
    detail = ""
    approved_count = 0
    db.execute("BEGIN IMMEDIATE")
    try:
        if op == "pause":
            db.execute("UPDATE settings SET value='1' WHERE key='paused'")
            detail = "New extraction job dispatch paused"
        elif op == "resume":
            db.execute("UPDATE settings SET value='0' WHERE key='paused'")
            detail = "New extraction job dispatch resumed"
        elif op == "workers":
            count = integer(payload.get("workers"), 3, 64)
            db.execute("UPDATE settings SET value=? WHERE key='workers'", (str(count),))
            detail = "Configured workers: " + str(count)
        elif op == "approve_all":
            if payload.get("confirm") != "APPROVE ALL READY":
                raise PDFAdminError("Explicit bulk approval confirmation missing")
            # One transaction across the entire queue, independent of
            # pagination, sorting, or active browser filters.
            # Failed, processing, queued, already approved and textless
            # documents remain untouched.
            change = db.execute("""
                UPDATE documents
                   SET status='approved',reviewed=1,updated_at=?
                 WHERE status='ready' AND reviewed=0 AND excerpts>0
                   AND EXISTS (
                     SELECT 1 FROM excerpts AS e WHERE e.sha256=documents.sha256
                       AND length(trim(e.text))>0
                   )""", (int(time.time()),))
            approved_count = change.rowcount
            detail = f"Bulk approved {approved_count} ready manuals with extracted text"
        else:
            digest = digest_arg(payload.get("sha256"))
            row = db.execute("SELECT status,excerpts FROM documents WHERE sha256=?",
                             (digest,)).fetchone()
            if row is None:
                raise PDFAdminError("Unknown PDF manual")
            if op == "retry":
                if row["status"] != "failed":
                    raise PDFAdminError("Only failed manuals can be retried")
                db.execute("""UPDATE documents SET status='queued',reviewed=0,error='',
                             updated_at=? WHERE sha256=? AND status='failed'""",
                           (int(time.time()), digest))
                detail = "Failed manual returned to queue"
            elif op == "approve":
                if payload.get("confirm") != "APPROVE MANUAL":
                    raise PDFAdminError("Manual review confirmation missing")
                if row["status"] != "ready" or row["excerpts"] < 1:
                    raise PDFAdminError("Only successfully extracted manuals may be approved")
                db.execute("""UPDATE documents SET status='approved',reviewed=1,
                             updated_at=? WHERE sha256=? AND status='ready'""",
                           (int(time.time()), digest))
                detail = "Explicit administrator approval (Gentoo library only)"
            elif op == "unapprove":
                if row["status"] != "approved":
                    raise PDFAdminError("Only approved manuals can be reverted")
                db.execute("""UPDATE documents SET status='ready',reviewed=0,
                             updated_at=? WHERE sha256=? AND status='approved'""",
                           (int(time.time()), digest))
                detail = "Administrator approval revoked"
            else:
                raise PDFAdminError("Unsupported operation")
        db.execute("INSERT INTO pdf_admin_audit(at,action,sha256,detail) VALUES (?,?,?,?)",
                   (int(time.time()), op, digest, detail))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return {"ok": True, "operation": op, "detail": detail,
            "sha256": digest, "approved_count": approved_count}


def process(raw: bytes, signature: str, key: bytes, node_id: str,
            root: Path = ROOT, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY:
        raise PDFAdminError("Invalid envelope size")
    if not verify_signature(raw, signature, key):
        raise PDFAdminError("Invalid signed PDF control request")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as error:
        raise PDFAdminError("Invalid PDF control JSON") from error
    if not isinstance(data, dict):
        raise PDFAdminError("Expected object payload")
    op = data.get("op")
    if op not in OPS or data.get("node_id") != node_id or (
        set(data) - {"op","node_id","sent_at","nonce","limit","offset",
                      "filter","query","sha256","workers","confirm",
                      "page","size"}
    ):
        raise PDFAdminError("Unknown PDF command or identity")
    if type(data.get("sent_at")) is not int or abs(now - data["sent_at"]) > 120:
        raise PDFAdminError("Expired PDF control request")
    nonce = data.get("nonce")
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise PDFAdminError("Invalid request nonce")
    db = database(root)
    try:
        ensure_metadata(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("DELETE FROM pdf_admin_nonces WHERE seen_at<?", (now - 3600,))
            db.execute("INSERT INTO pdf_admin_nonces(nonce,seen_at) VALUES (?,?)",
                       (nonce, now))
            db.execute("COMMIT")
        except Exception as error:
            db.execute("ROLLBACK")
            if isinstance(error, sqlite3.IntegrityError):
                raise PDFAdminError("Replayed PDF control request") from error
            raise
        if op == "status":
            return snapshot(db, root, data)
        if op == "preview":
            return preview(db, data)
        if op in ("manual_meta", "manual_page"):
            try:
                if op == "manual_meta":
                    return manual_view.metadata(db, root, data)
                return manual_view.render(db, root, data)
            except manual_view.ManualViewError as error:
                raise PDFAdminError(str(error)) from error
        return update(db, data, op)
    finally:
        db.close()
