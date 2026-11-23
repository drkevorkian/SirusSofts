#!/usr/bin/env python3
"""Offline-only SirusSofts PDF ingestion. No LLM calls, remote endpoints, or approval.

Keep an authoritative SQLite queue and extracted text on the same dedicated
Gentoo SSD as the PDF objects. The Ubuntu CMS integration is a separate phase.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat

from pdf_wal_guard import open_persistent_wal, grant_reader_sidecars
import subprocess
import sys
import tempfile
import time
import uuid

DEFAULT_ROOT = Path("/srv/sirussofts/storage")
MAX_PDF = 1024 * 1024 * 1024
MAX_TEXT = 96 * 1024 * 1024
MAX_PAGES = 20000
MAX_PARTS = 90000
MAX_WORKERS = 64
STABLE_SECONDS = 5
POLL_SECONDS = 3
DISPATCH_STAGGER = 0.3


def require_storage(root: Path, *, strict: bool = True) -> None:
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("Dedicated SirusSofts storage directory unavailable")
    if strict and not os.path.ismount(root):
        raise RuntimeError("Dedicated SirusSofts SSD is NOT mounted; refusing root RAID0 fallback")
    for name in ("manuals/inbox", "manuals/objects", "databases"):
        child = root / name
        if child.is_symlink():
            raise RuntimeError("Unsafe symlink in managed SirusSofts storage: " + name)
        child.mkdir(parents=True, exist_ok=True)


def connect(root: Path) -> sqlite3.Connection:
    path = root / "databases" / "pdf_ingest.sqlite3"
    # An extra SQLite handle closes AFTER this writer, retaining the sidecars.
    db = open_persistent_wal(path, timeout=30)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        if root == DEFAULT_ROOT:
            # Never grant the AI worker database or directory write rights.
            grant_reader_sidecars(path)
        return db
    except BaseException:
        db.close()
        raise


def schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    INSERT OR IGNORE INTO settings(key,value) VALUES ('workers','3');
    INSERT OR IGNORE INTO settings(key,value) VALUES ('paused','0');
    CREATE TABLE IF NOT EXISTS seen (
      rel TEXT PRIMARY KEY,
      bytes INTEGER NOT NULL,
      mtime_ns INTEGER NOT NULL,
      first_seen REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS documents (
      sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
      filename TEXT NOT NULL,
      object_rel TEXT NOT NULL UNIQUE,
      bytes INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('queued','processing','ready','failed','approved')),
      reviewed INTEGER NOT NULL DEFAULT 0 CHECK(reviewed IN (0,1)),
      pages INTEGER NOT NULL DEFAULT 0,
      excerpts INTEGER NOT NULL DEFAULT 0,
      attempts INTEGER NOT NULL DEFAULT 0,
      error TEXT NOT NULL DEFAULT '',
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS document_queue ON documents(status,created_at,sha256);
    CREATE TABLE IF NOT EXISTS excerpts (
      sha256 TEXT NOT NULL REFERENCES documents(sha256) ON DELETE CASCADE,
      page INTEGER NOT NULL,
      part INTEGER NOT NULL,
      text TEXT NOT NULL,
      PRIMARY KEY(sha256,page,part)
    );
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY,
      at INTEGER NOT NULL,
      sha256 TEXT NOT NULL,
      kind TEXT NOT NULL,
      detail TEXT NOT NULL
    );
    """)


def event(db: sqlite3.Connection, digest: str, kind: str, detail: str = "") -> None:
    db.execute("INSERT INTO events(at,sha256,kind,detail) VALUES (?,?,?,?)",
               (int(time.time()), digest, kind, detail[:300]))


def worker_count(db: sqlite3.Connection) -> int:
    count = int(db.execute("SELECT value FROM settings WHERE key='workers'").fetchone()[0])
    if not 3 <= count <= MAX_WORKERS:
        raise RuntimeError("Invalid stored worker count; expected 3..64")
    return count


def paused(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == "1"


def candidate_paths(root: Path):
    inbox = root / "manuals" / "inbox"
    for directory, subdirs, files in os.walk(inbox, followlinks=False):
        subdirs[:] = [d for d in subdirs if not (Path(directory) / d).is_symlink()]
        for name in files:
            p = Path(directory) / name
            if name.lower().endswith(".pdf") and not p.is_symlink():
                yield p


def file_digest(path: Path, expected: os.stat_result) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not same_file(before, expected):
            raise RuntimeError("PDF changed while being inspected")
        h = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        if not same_file(os.fstat(fd), expected):
            raise RuntimeError("PDF changed during hashing")
        return h.hexdigest()
    finally:
        os.close(fd)


def same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns) == (
        b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns
    )


def intake_file(root: Path, db: sqlite3.Connection, src: Path, observed: os.stat_result) -> bool:
    """Copy to immutable content-addressed object, verify it, then remove staged input."""
    if observed.st_size < 32 or observed.st_size > MAX_PDF:
        raise RuntimeError("PDF size outside 32 bytes..1 GiB")
    if time.time_ns() - observed.st_mtime_ns < STABLE_SECONDS * 1_000_000_000:
        return False
    digest = file_digest(src, observed)
    rel = f"manuals/objects/{digest[:2]}/{digest}.pdf"
    dst = root / rel
    dst.parent.mkdir(mode=0o750, exist_ok=True)
    existing = db.execute("SELECT sha256 FROM documents WHERE sha256=?", (digest,)).fetchone()
    if existing is not None:
        if not dst.is_file() or dst.is_symlink() or file_digest(dst, dst.stat()) != digest:
            raise RuntimeError("Existing library object does not match its recorded hash")
        if same_file(src.lstat(), observed):
            src.unlink()
            event(db, digest, "duplicate", src.name)
        return True

    fd, tmp = tempfile.mkstemp(prefix=".incoming-", dir=dst.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(src, flags), "rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or not same_file(before, observed):
                    raise RuntimeError("PDF source changed before copying")
                if source.read(8)[:5] != b"%PDF-":
                    raise RuntimeError("PDF header is missing")
                source.seek(0)
                digest_copy = hashlib.sha256()
                total = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_PDF:
                        raise RuntimeError("PDF grew beyond the 1 GiB limit")
                    digest_copy.update(chunk)
                    out.write(chunk)
                if not same_file(os.fstat(source.fileno()), observed):
                    raise RuntimeError("PDF changed while being copied")
            out.flush()
            os.fsync(out.fileno())
        if digest_copy.hexdigest() != digest or total != observed.st_size:
            raise RuntimeError("PDF changed during intake; source retained")
        if not same_file(src.lstat(), observed):
            raise RuntimeError("PDF replaced during intake; source retained")
        os.chmod(tmp, 0o640)
        try:
            os.link(tmp, dst, follow_symlinks=False)
        except FileExistsError:
            if dst.is_symlink() or file_digest(dst, dst.stat()) != digest:
                raise RuntimeError("Conflicting object hash path; source retained")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("""INSERT OR IGNORE INTO documents
              (sha256,filename,object_rel,bytes,status,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?)""",
                       (digest, src.name[:240], rel, total, "queued",
                        int(time.time()), int(time.time())))
            event(db, digest, "queued", src.name)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        # Leave the original if it has changed in the meantime.
        if same_file(src.lstat(), observed):
            src.unlink()
        return True
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def scan(root: Path, db: sqlite3.Connection) -> dict:
    require_storage(root)
    inbox = root / "manuals" / "inbox"
    found = 0
    imported = 0
    errors = 0
    for src in candidate_paths(root):
        rel = str(src.relative_to(inbox))
        try:
            current = src.lstat()
            if not stat.S_ISREG(current.st_mode):
                continue
            found += 1
            row = db.execute("SELECT * FROM seen WHERE rel=?", (rel,)).fetchone()
            if row is None or row["bytes"] != current.st_size or row["mtime_ns"] != current.st_mtime_ns:
                db.execute("""INSERT INTO seen(rel,bytes,mtime_ns,first_seen)
                  VALUES (?,?,?,?) ON CONFLICT(rel) DO UPDATE
                  SET bytes=excluded.bytes,mtime_ns=excluded.mtime_ns,
                      first_seen=excluded.first_seen""",
                           (rel, current.st_size, current.st_mtime_ns, time.time()))
                continue
            if time.time() - row["first_seen"] < STABLE_SECONDS:
                continue
            if intake_file(root, db, src, current):
                imported += 1
                db.execute("DELETE FROM seen WHERE rel=?", (rel,))
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            errors += 1
            print(f"WARN: could not intake {rel}: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
    return {"pdfs_found": found, "imported_or_deduplicated": imported, "errors": errors}


def claim(db: sqlite3.Connection) -> str | None:
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("""SELECT sha256 FROM documents WHERE status='queued'
             ORDER BY created_at,sha256 LIMIT 1""").fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        digest = row["sha256"]
        updated = db.execute("""UPDATE documents SET status='processing',
             attempts=attempts+1,updated_at=? WHERE sha256=? AND status='queued'""",
                             (int(time.time()), digest))
        if updated.rowcount != 1:
            raise RuntimeError("PDF job claim conflicted")
        event(db, digest, "processing")
        db.execute("COMMIT")
        return digest
    except Exception:
        db.execute("ROLLBACK")
        raise


def split_page(text: str, width: int = 4500):
    while text:
        if len(text) <= width:
            part = text.strip()
            if part:
                yield part
            return
        cut = text.rfind("\n", int(width * .65), width)
        if cut < 0:
            cut = text.rfind(" ", int(width * .65), width)
        if cut < 0:
            cut = width
        part = text[:cut].strip()
        if part:
            yield part
        text = text[cut:].strip()


def extract(root: Path, db: sqlite3.Connection, digest: str) -> None:
    row = db.execute("SELECT * FROM documents WHERE sha256=?", (digest,)).fetchone()
    if row is None or row["status"] != "processing":
        raise RuntimeError("PDF is not claimed")
    path = root / row["object_rel"]
    if path.is_symlink() or not path.is_file() or path.stat().st_size != row["bytes"]:
        raise RuntimeError("PDF object unavailable or modified")
    if file_digest(path, path.stat()) != digest:
        raise RuntimeError("PDF object hash verification failed")
    info = subprocess.run(["pdfinfo", str(path)], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=45, check=True)
    match = re.search(rb"^Pages:\s*(\d+)\s*$", info.stdout, re.M)
    if not match:
        raise RuntimeError("PDF page count unavailable")
    pages = int(match.group(1))
    if not 1 <= pages <= MAX_PAGES:
        raise RuntimeError("PDF has unsupported page count")
    fd, txt = tempfile.mkstemp(prefix=".extract-", suffix=".txt",
                               dir=root / "databases")
    os.close(fd)
    try:
        subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), txt],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       timeout=600, check=True)
        if os.path.getsize(txt) > MAX_TEXT:
            raise RuntimeError("Extracted text exceeds 96 MiB limit")
        with open(txt, "r", encoding="utf-8", errors="replace") as stream:
            raw = stream.read(MAX_TEXT + 1)
        if len(raw.encode("utf-8")) > MAX_TEXT:
            raise RuntimeError("Extracted text exceeds 96 MiB limit")
        all_pages = raw.split("\f")
        if all_pages and not all_pages[-1].strip():
            all_pages.pop()
        if len(all_pages) > pages:
            raise RuntimeError("PDF extraction produced more pages than pdfinfo")
        rows = []
        for page_no, text in enumerate(all_pages, 1):
            if len(text) > 2 * 1024 * 1024:
                raise RuntimeError("Individual PDF page text is too large")
            for part_no, part in enumerate(split_page(text.strip()), 1):
                rows.append((digest, page_no, part_no, part))
                if len(rows) > MAX_PARTS:
                    raise RuntimeError("Too many page-attributed excerpts")
        if not rows:
            raise RuntimeError("No selectable text; OCR/manual inspection required")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("DELETE FROM excerpts WHERE sha256=?", (digest,))
            db.executemany("""INSERT INTO excerpts(sha256,page,part,text)
                              VALUES (?,?,?,?)""", rows)
            db.execute("""UPDATE documents SET status='ready',reviewed=0,
                pages=?,excerpts=?,error='',updated_at=?
                WHERE sha256=? AND status='processing'""",
                       (pages, len(rows), int(time.time()), digest))
            event(db, digest, "ready_for_review",
                  f"{pages} pages; {len(rows)} excerpts; not approved")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    finally:
        if os.path.exists(txt):
            os.unlink(txt)


def process_one(root: Path, digest: str) -> None:
    require_storage(root)
    db = connect(root)
    try:
        extract(root, db, digest)
        print(f"READY FOR REVIEW: {digest}", flush=True)
    except Exception as exc:
        description = f"{type(exc).__name__}: {exc}"[:300]
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE documents SET status='failed',reviewed=0,
                          error=?,updated_at=? WHERE sha256=? AND status='processing'""",
                       (description, int(time.time()), digest))
            event(db, digest, "failed", description)
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        print(f"FAILED: {digest}: {description}", file=sys.stderr, flush=True)
    finally:
        db.close()


def status(db: sqlite3.Connection) -> dict:
    counts = {r["status"]: r["n"] for r in db.execute(
        "SELECT status, COUNT(*) n FROM documents GROUP BY status")}
    return {
        "configured_workers": worker_count(db),
        "paused": paused(db),
        "counts": counts,
        "recent": [dict(x) for x in db.execute("""SELECT sha256,filename,bytes,status,
                         pages,excerpts,attempts,error,reviewed FROM documents
                         ORDER BY updated_at DESC,sha256 LIMIT 30""")]
    }


def service(root: Path, db: sqlite3.Connection, *, once: bool = False) -> None:
    lock_path = root / "databases" / "pdf-ingest.lock"
    with open(lock_path, "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A Gentoo PDF ingestion service is already running")
        # A second service instance cannot claim another job while the lock holds.
        db.execute("""UPDATE documents SET status='queued',updated_at=?
                      WHERE status='processing'""", (int(time.time()),))
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            active = {}
            while True:
                require_storage(root)
                result = scan(root, db)
                for future, digest in list(active.items()):
                    if future.done():
                        try:
                            future.result()
                        except Exception as exc:
                            print(f"ERROR: worker {digest}: {exc}", file=sys.stderr)
                        del active[future]
                limit = worker_count(db)
                if not paused(db):
                    while len(active) < limit:
                        digest = claim(db)
                        if digest is None:
                            break
                        active[pool.submit(process_one, root, digest)] = digest
                        time.sleep(DISPATCH_STAGGER)
                if once:
                    # One sweep: newly encountered files still need two stable scans.
                    if not active:
                        print(json.dumps({"scan": result, "queue": status(db)}, indent=2))
                        return
                time.sleep(POLL_SECONDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline Gentoo PDF library")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    cmds = parser.add_mutually_exclusive_group(required=True)
    cmds.add_argument("--check", action="store_true")
    cmds.add_argument("--scan", action="store_true")
    cmds.add_argument("--serve", action="store_true")
    cmds.add_argument("--once", action="store_true")
    cmds.add_argument("--status", action="store_true")
    cmds.add_argument("--set-workers", type=int)
    cmds.add_argument("--pause", action="store_true")
    cmds.add_argument("--resume", action="store_true")
    cmds.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        print("PASS: offline Gentoo PDF ingestion module; no disk changes")
        return
    root = args.root
    require_storage(root)
    db = connect(root)
    try:
        schema(db)
        if args.set_workers is not None:
            if os.geteuid() != 0:
                raise PermissionError("Local worker count changes require administrator/root")
            if not 3 <= args.set_workers <= MAX_WORKERS:
                raise ValueError("Worker count must be between 3 and 64")
            db.execute("UPDATE settings SET value=? WHERE key='workers'",
                       (str(args.set_workers),))
        elif args.pause or args.resume:
            if os.geteuid() != 0:
                raise PermissionError("Local pause/resume requires administrator/root")
            db.execute("UPDATE settings SET value=? WHERE key='paused'",
                       ("1" if args.pause else "0",))
        elif args.retry_failed:
            if os.geteuid() != 0:
                raise PermissionError("Retries require administrator/root")
            db.execute("""UPDATE documents SET status='queued',error=''
                          WHERE status='failed'""")
        elif args.scan:
            print(json.dumps(scan(root, db), indent=2))
        elif args.serve or args.once:
            for exe in ("pdfinfo", "pdftotext"):
                if not shutil.which(exe):
                    raise RuntimeError("Poppler utility missing: " + exe)
            service(root, db, once=args.once)
        print(json.dumps(status(db), indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print("FAIL:", error, file=sys.stderr)
        sys.exit(1)
