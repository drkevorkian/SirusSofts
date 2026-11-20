#!/usr/bin/env python3
"""Private, opt-in, revisioned continuous research. No network or training API.

The PDF ingestion database is opened read-only. All scheduling and draft results
reside in the existing private Gentoo input SQLite database. No source text,
model endpoint, shell command or training operation is exposed by this module.
"""
import hashlib
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, Thread
import time
import uuid

from manual_rag import SOURCE, INDEX
from research_loop import _manual_connection, pass_messages

CHUNK = 7000
LEASE_SECONDS = 240
MAX_ATTEMPTS = 5
DEFAULT_MODEL = "sirussofts-coder"


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def connect(path):
    """Commit/rollback an explicit transaction and always close the handle.

    sqlite3.Connection.__exit__ alone does NOT close a connection; the
    autonomous scanner creates many short-lived handles over time.
    """
    db = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            yield db
    finally:
        db.close()


def init_schema(path):
    """Idempotent, additive migration; never modifies existing research tables."""
    with connect(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS cr_config (
          id INTEGER PRIMARY KEY CHECK(id=1),
          mode TEXT NOT NULL DEFAULT 'disabled'
            CHECK(mode IN ('disabled','paused','running','stopped')),
          passes INTEGER NOT NULL DEFAULT 1 CHECK(passes BETWEEN 1 AND 100),
          model_id TEXT NOT NULL DEFAULT 'sirussofts-coder',
          target TEXT NOT NULL DEFAULT 'auto',
          revisit_seconds INTEGER NOT NULL DEFAULT 86400,
          max_cpu_load REAL NOT NULL DEFAULT 0,
          max_chat_jobs INTEGER NOT NULL DEFAULT 0,
          last_completed REAL NOT NULL DEFAULT 0,
          updated REAL NOT NULL DEFAULT 0,
          last_inventory REAL NOT NULL DEFAULT 0,
          scan_requested INTEGER NOT NULL DEFAULT 0,
          scan_error TEXT NOT NULL DEFAULT ''
        );
        INSERT OR IGNORE INTO cr_config(id) VALUES(1);
        CREATE TABLE IF NOT EXISTS cr_sources (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
          content_sha TEXT NOT NULL, revision TEXT NOT NULL,
          extraction_status TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0,
          pages INTEGER NOT NULL DEFAULT 0, extracted_parts INTEGER NOT NULL DEFAULT 0,
          missing_pages INTEGER NOT NULL DEFAULT 0, eligible INTEGER NOT NULL DEFAULT 0,
          excluded INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0,
          present INTEGER NOT NULL DEFAULT 1, seen INTEGER NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cr_sections (
          slot TEXT NOT NULL, revision TEXT NOT NULL, source_id TEXT NOT NULL,
          page INTEGER NOT NULL, part INTEGER NOT NULL, byte_offset INTEGER NOT NULL,
          chars INTEGER NOT NULL, current INTEGER NOT NULL DEFAULT 1,
          source_revision TEXT NOT NULL, seen INTEGER NOT NULL,
          PRIMARY KEY(slot,revision)
        );
        CREATE INDEX IF NOT EXISTS cr_section_current
          ON cr_sections(source_id,current,slot);
        CREATE TABLE IF NOT EXISTS cr_cycles (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started REAL NOT NULL, completed REAL,
          state TEXT NOT NULL CHECK(state IN ('open','completed'))
        );
        CREATE TABLE IF NOT EXISTS cr_jobs (
          id TEXT PRIMARY KEY, cycle INTEGER NOT NULL, slot TEXT NOT NULL,
          revision TEXT NOT NULL, passes INTEGER NOT NULL,
          pass_completed INTEGER NOT NULL DEFAULT 0,
          state TEXT NOT NULL CHECK(state IN
            ('queued','leased','completed','failed','superseded','stopped')),
          attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
          lease_until REAL NOT NULL DEFAULT 0, token TEXT NOT NULL DEFAULT '',
          worker TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
          created REAL NOT NULL, updated REAL NOT NULL,
          UNIQUE(cycle,slot,revision)
        );
        CREATE INDEX IF NOT EXISTS cr_job_claim
          ON cr_jobs(state,retry_at,cycle,created);
        CREATE TABLE IF NOT EXISTS cr_results (
          job_id TEXT NOT NULL, pass_number INTEGER NOT NULL,
          notes TEXT NOT NULL, sha256 TEXT NOT NULL,
          review_state TEXT NOT NULL DEFAULT 'unreviewed'
            CHECK(review_state='unreviewed'),
          created REAL NOT NULL, PRIMARY KEY(job_id,pass_number)
        );
        CREATE TABLE IF NOT EXISTS cr_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, happened REAL NOT NULL,
          kind TEXT NOT NULL, subject TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
        );
        """)


class ContinuousResearch:
    def __init__(self, db_path, source=SOURCE, index=INDEX):
        self.db_path = str(db_path)
        self.source = Path(source)
        self.index = Path(index)

    def _event(self, db, kind, subject, detail=""):
        db.execute("INSERT INTO cr_events(happened,kind,subject,detail) VALUES(?,?,?,?)",
                   (time.time(), kind, subject, str(detail)[:160]))

    def inventory(self):
        """Full source snapshot, including failed PDFs and zero-text pages.

        Streaming the excerpt cursor avoids loading the complete manual library
        into RAM. Each source update is atomic; a failed scan never marks
        unseen sources deleted, and never erases previous versions or results.
        """
        stamp = time.time_ns()
        counts = {"sources": 0, "sections": 0, "unextracted_pages": 0}
        with closing(_manual_connection(self.source, self.index)) as pdf:
            pdf.execute("BEGIN")
            docs = pdf.execute("""SELECT sha256,filename,status,reviewed,pages,
                                   excerpts,updated_at FROM documents ORDER BY sha256""")
            # Do not reuse this connection for the nested excerpt query while
            # iterating a live cursor.
            for doc in docs:
                sha, filename, status, reviewed, pages, declared, updated = tuple(doc)
                source_id = "pdf:" + sha
                extracted_pages = set()
                source_hash = hashlib.sha256()
                section_rows = []
                cursor = pdf.execute("""SELECT page,part,text FROM excerpts
                    WHERE sha256=? ORDER BY page,part""", (sha,))
                for page, part, text in cursor:
                    if not text:
                        continue
                    extracted_pages.add(page)
                    for offset in range(0, len(text), CHUNK):
                        piece = text[offset:offset + CHUNK]
                        slot = f"{source_id}:{page}:{part}:{offset}"
                        rev = digest(piece)
                        section_rows.append((slot, rev, source_id, page, part,
                                             offset, len(piece), 1, "", stamp))
                        source_hash.update((slot + rev).encode("utf-8"))
                revision = digest("|".join(map(str, (
                    sha, status, reviewed, pages, declared, updated,
                    source_hash.hexdigest()))))
                missing = max(0, int(pages) - len(extracted_pages))
                eligible = int(status in ("ready", "approved") and bool(section_rows))
                with connect(self.db_path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    prior = db.execute("SELECT revision,eligible,present FROM cr_sources WHERE id=?",
                                       (source_id,)).fetchone()
                    db.execute("""INSERT INTO cr_sources(id,kind,title,content_sha,
                        revision,extraction_status,reviewed,pages,extracted_parts,
                        missing_pages,eligible,seen,updated)
                        VALUES(?,'pdf',?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title,content_sha=excluded.content_sha,
                        revision=excluded.revision,
                        extraction_status=excluded.extraction_status,
                        reviewed=excluded.reviewed,pages=excluded.pages,
                        extracted_parts=excluded.extracted_parts,
                        missing_pages=excluded.missing_pages,eligible=excluded.eligible,
                        present=1,seen=excluded.seen,updated=excluded.updated""",
                        (source_id, filename, sha, revision, status, int(reviewed),
                         pages, len(section_rows), missing, eligible, stamp, time.time()))
                    db.execute("UPDATE cr_sections SET current=0 WHERE source_id=?", (source_id,))
                    db.executemany("""INSERT INTO cr_sections(slot,revision,source_id,
                        page,part,byte_offset,chars,current,source_revision,seen)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(slot,revision) DO UPDATE SET current=1,
                        source_revision=excluded.source_revision,seen=excluded.seen""",
                        [(r[0],digest(revision + ':' + r[1])) + tuple(r[2:8]) +
                         (revision,stamp) for r in section_rows])
                    if prior is None:
                        self._event(db, "added", source_id)
                    elif prior["revision"] != revision or not prior["present"]:
                        self._event(db, "changed", source_id)
                    if prior and not prior["eligible"] and eligible:
                        self._event(db, "eligible", source_id)
                    counts["sources"] += 1
                    counts["sections"] += len(section_rows)
                    counts["unextracted_pages"] += missing
            # Snapshot existing one-off text/HTTPS research results only.
            # Never re-fetch a URL or publish a pasted source.
            with connect(self.db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                for job in db.execute("""SELECT id,spec,source_text,source_sha256,state
                    FROM research_jobs WHERE json_valid(spec)=1""").fetchall():
                    spec = json.loads(job["spec"])
                    if spec.get("source_type") not in ("text", "url"):
                        continue
                    source_id = "research:" + job["id"]
                    body = job["source_text"]
                    rev = digest(body) if body else ""
                    ready = bool(body)
                    db.execute("""INSERT INTO cr_sources(id,kind,title,content_sha,
                        revision,extraction_status,eligible,excluded,extracted_parts,
                        seen,updated)
                        VALUES(?,'snapshot',?,?,?,?,?,1,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                        content_sha=excluded.content_sha,revision=excluded.revision,
                        extraction_status=excluded.extraction_status,
                        eligible=excluded.eligible,seen=excluded.seen,
                        updated=excluded.updated,present=1""",
                        (source_id, str(spec.get("title", ""))[:160], rev, rev,
                         "ready" if ready else job["state"],int(ready),
                         (len(body) + CHUNK - 1)//CHUNK if ready else 0,
                         stamp, time.time()))
                    db.execute("UPDATE cr_sections SET current=0 WHERE source_id=?", (source_id,))
                    if ready:
                        for off in range(0, len(body), CHUNK):
                            piece = body[off:off+CHUNK]
                            slot = f"{source_id}:0:0:{off}"
                            db.execute("""INSERT INTO cr_sections(slot,revision,
                                source_id,page,part,byte_offset,chars,current,
                                source_revision,seen) VALUES(?,?,?,?,?,?,?,?,?,?)
                                ON CONFLICT(slot,revision) DO UPDATE SET current=1,
                                seen=excluded.seen""",
                                (slot,digest(rev + ':' + digest(piece)),
                                 source_id,0,0,off,len(piece),
                                 1,rev,stamp))
                    counts["sources"] += 1
                    counts["sections"] += (len(body)+CHUNK-1)//CHUNK if ready else 0
                gone = db.execute("""SELECT id FROM cr_sources WHERE kind='pdf'
                    AND seen<>? AND present=1""", (stamp,)).fetchall()
                for row in gone:
                    self._event(db, "removed", row["id"])
                db.execute("""UPDATE cr_sources SET present=0,eligible=0,
                    extraction_status='removed',updated=? WHERE kind='pdf' AND seen<>?""",
                           (time.time(), stamp))
                db.execute("""UPDATE cr_sections SET current=0 WHERE source_id IN
                    (SELECT id FROM cr_sources WHERE present=0)""")
                self._supersede(db)
        return counts

    @staticmethod
    def _supersede(db):
        db.execute("""UPDATE cr_jobs SET state='superseded',token='',lease_until=0,
            updated=? WHERE state IN ('queued','leased') AND NOT EXISTS
            (SELECT 1 FROM cr_sections s JOIN cr_sources d ON d.id=s.source_id
             WHERE s.slot=cr_jobs.slot AND s.revision=cr_jobs.revision
             AND s.current=1 AND d.eligible=1 AND d.present=1 AND d.excluded=0)""",
                   (time.time(),))

    def control(self, action, *, source_id="", value=None):
        if action not in ("enable", "pause", "resume", "stop", "disable",
                          "include", "exclude", "priority", "configure", "scan"):
            raise ValueError("Invalid control action")
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            if action in ("include", "exclude", "priority"):
                if not isinstance(source_id, str) or not (
                    (source_id.startswith("pdf:") and len(source_id)==68 and
                     all(c in "0123456789abcdef" for c in source_id[4:])) or
                    (source_id.startswith("research:") and len(source_id)==41 and
                     all(c in "0123456789abcdef" for c in source_id[9:]))):
                    raise ValueError("Invalid source identifier")
                if not db.execute("SELECT 1 FROM cr_sources WHERE id=?",
                                  (source_id,)).fetchone():
                    raise ValueError("Source not inventoried")
                if action == "priority":
                    if type(value) is not int or not -10 <= value <= 10:
                        raise ValueError("Priority must be -10..10")
                    db.execute("UPDATE cr_sources SET priority=? WHERE id=?",
                               (value,source_id))
                else:
                    db.execute("UPDATE cr_sources SET excluded=? WHERE id=?",
                               (int(action=="exclude"),source_id))
                    if action == "exclude":
                        self._supersede(db)
                    else:
                        # Re-included unchanged work resumes in the open cycle.
                        db.execute("""UPDATE cr_jobs SET state='queued',token='',
                            retry_at=0,lease_until=0,updated=?
                            WHERE state='superseded'
                            AND cycle IN (SELECT id FROM cr_cycles WHERE state='open')
                            AND EXISTS (SELECT 1 FROM cr_sections s JOIN cr_sources d
                              ON d.id=s.source_id WHERE s.slot=cr_jobs.slot
                              AND s.revision=cr_jobs.revision AND s.current=1
                              AND d.id=? AND d.present=1 AND d.eligible=1
                              AND d.excluded=0)""",(now,source_id))
                self._event(db,action,source_id)
            elif action == "scan":
                db.execute("UPDATE cr_config SET scan_requested=1,updated=? WHERE id=1",(now,))
                self._event(db,"scan-requested","research")
            elif action == "configure":
                if not isinstance(value,dict) or not value or not set(value) <= {
                    "passes","model_id","target","revisit_seconds",
                    "max_cpu_load","max_chat_jobs"}:
                    raise ValueError("Invalid research settings")
                for key,item in value.items():
                    if key=="passes" and (type(item) is not int or not 1<=item<=100):
                        raise ValueError("Pass count must be 1..100")
                    if key=="model_id" and (not isinstance(item,str) or
                        "://" in item or item.startswith("//") or
                        not __import__("re").fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:+/-]{0,127}",item)):
                        raise ValueError("Invalid local model ID")
                    if key=="target" and item not in ("auto","cpu","amd","nvidia"):
                        raise ValueError("Invalid target")
                    if key=="revisit_seconds" and (type(item) is not int or
                                                      not 60<=item<=604800):
                        raise ValueError("Revisit interval must be 60..604800 seconds")
                    if key=="max_cpu_load" and (type(item) not in (int,float) or
                                                 not 0<=item<=1024):
                        raise ValueError("Invalid load limit")
                    if key=="max_chat_jobs" and (type(item) is not int or
                                                 not 0<=item<=4):
                        raise ValueError("Invalid interactive queue budget")
                    # Column names are literal allowlisted, never external SQL.
                    db.execute(f"UPDATE cr_config SET {key}=?,updated=? WHERE id=1",
                               (item,now))
                self._event(db,"configure","research")
            else:
                next_mode={"enable":"running","resume":"running","pause":"paused",
                           "stop":"stopped","disable":"disabled"}[action]
                db.execute("UPDATE cr_config SET mode=?,updated=?,scan_requested=CASE WHEN ?='running' THEN 1 ELSE scan_requested END WHERE id=1",
                           (next_mode,now,next_mode))
                if action=="stop":
                    db.execute("""UPDATE cr_jobs SET state='stopped',token='',
                        lease_until=0,updated=? WHERE state IN ('queued','leased')""",
                               (now,))
                if action in ("enable","resume"):
                    db.execute("""UPDATE cr_jobs SET state='queued',retry_at=0
                        WHERE state='stopped'""")
                self._event(db,action,"research")
        return self.status()

    def scan_if_due(self):
        """Refresh automatically while enabled, or after a one-shot admin scan."""
        now=time.time()
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            cfg=db.execute("SELECT * FROM cr_config WHERE id=1").fetchone()
            due=bool(cfg["scan_requested"]) or (
                cfg["mode"]=="running" and now-cfg["last_inventory"]>=3600)
            if not due:
                return False
            # Record an attempt even on failure to avoid an unhealthy tight loop.
            db.execute("UPDATE cr_config SET scan_requested=0,last_inventory=? WHERE id=1",(now,))
        try:
            self.inventory()
        except (OSError,sqlite3.Error,RuntimeError,ValueError) as exc:
            with connect(self.db_path) as db:
                db.execute("UPDATE cr_config SET scan_error=? WHERE id=1",
                           (type(exc).__name__,))
        else:
            with connect(self.db_path) as db:
                db.execute("UPDATE cr_config SET scan_error='' WHERE id=1")
        return True

    def _schedule(self, db, now):
        conf = db.execute("SELECT * FROM cr_config WHERE id=1").fetchone()
        if conf["mode"]!="running":
            return
        active = db.execute("SELECT id FROM cr_cycles WHERE state='open' ORDER BY id DESC LIMIT 1").fetchone()
        if not active:
            if now - conf["last_completed"] < conf["revisit_seconds"]:
                return
            if not db.execute("""SELECT 1 FROM cr_sections s JOIN cr_sources d
                ON d.id=s.source_id WHERE s.current=1 AND d.present=1
                AND d.eligible=1 AND d.excluded=0 LIMIT 1""").fetchone():
                return
            cycle = db.execute("INSERT INTO cr_cycles(started,state) VALUES(?,'open')",
                               (now,)).lastrowid
            self._event(db,"cycle-start",str(cycle))
        else:
            cycle = active["id"]
        self._supersede(db)
        db.execute("""INSERT OR IGNORE INTO cr_jobs(id,cycle,slot,revision,passes,
            state,created,updated)
            SELECT lower(hex(randomblob(16))),?,s.slot,s.revision,?,'queued',?,?
            FROM cr_sections s JOIN cr_sources d ON d.id=s.source_id
            WHERE s.current=1 AND d.present=1 AND d.eligible=1 AND d.excluded=0""",
                   (cycle,conf["passes"],now,now))
        active_count = db.execute("""SELECT count(*) FROM cr_jobs
            WHERE cycle=? AND state IN ('queued','leased')""",(cycle,)).fetchone()[0]
        if not active_count:
            db.execute("UPDATE cr_cycles SET state='completed',completed=? WHERE id=?",
                       (now,cycle))
            db.execute("UPDATE cr_config SET last_completed=? WHERE id=1",(now,))
            self._event(db,"cycle-complete",str(cycle))

    @staticmethod
    def _retry_delay(attempts):
        return min(3600,15*2**max(0,attempts-1))

    def claim(self, *, now=None):
        now = time.time() if now is None else now
        # Source reads and model calls occur OUTSIDE this short write transaction.
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            expired = db.execute("""SELECT id,attempts FROM cr_jobs
                WHERE state='leased' AND lease_until<=?""",(now,)).fetchall()
            for row in expired:
                tries=row["attempts"]
                db.execute("""UPDATE cr_jobs SET state=?,token='',lease_until=0,
                    retry_at=?,error='Worker lease expired',updated=? WHERE id=?""",
                    ("failed" if tries>=MAX_ATTEMPTS else "queued",
                     now+self._retry_delay(tries),now,row["id"]))
                self._event(db,"lease-expired",row["id"])
            self._schedule(db,now)
            conf=db.execute("SELECT * FROM cr_config WHERE id=1").fetchone()
            if conf["mode"]!="running":
                return None
            if db.execute("SELECT 1 FROM cr_jobs WHERE state='leased' LIMIT 1").fetchone():
                return None
            if db.execute("""SELECT count(*) FROM jobs
                WHERE state IN ('queued','running')""").fetchone()[0] > conf["max_chat_jobs"]:
                return None
            if db.execute("""SELECT 1 FROM research_jobs
                WHERE state IN ('queued','running') LIMIT 1""").fetchone():
                return None
            if conf["max_cpu_load"] and os.getloadavg()[0]>conf["max_cpu_load"]:
                return None
            row=db.execute("""SELECT j.id,j.slot,j.revision,j.pass_completed,
                j.passes,s.source_id,s.page,s.part,s.byte_offset,
                s.source_revision,d.title,d.content_sha,d.priority
                FROM cr_jobs j JOIN cr_sections s ON s.slot=j.slot
                AND s.revision=j.revision JOIN cr_sources d ON d.id=s.source_id
                WHERE j.state='queued' AND j.retry_at<=? AND s.current=1
                AND d.present=1 AND d.eligible=1 AND d.excluded=0
                ORDER BY d.priority DESC,j.created,j.id LIMIT 1""",(now,)).fetchone()
            if not row:
                return None
            token=uuid.uuid4().hex
            db.execute("""UPDATE cr_jobs SET state='leased',token=?,lease_until=?,
                attempts=attempts+1,updated=? WHERE id=? AND state='queued'""",
                       (token,now+LEASE_SECONDS,now,row["id"]))
            return dict(row) | {"token":token,"target":conf["target"],
                                "model_id":conf["model_id"]}

    def heartbeat(self, job_id, token):
        now=time.time()
        with connect(self.db_path) as db:
            cur=db.execute("""UPDATE cr_jobs SET lease_until=?,updated=?
                WHERE id=? AND token=? AND state='leased' AND lease_until>?""",
                (now+LEASE_SECONDS,now,job_id,token,now))
            return cur.rowcount==1

    def _section_text(self, lease):
        if lease["source_id"].startswith("pdf:"):
            with closing(_manual_connection(self.source,self.index)) as db:
                row=db.execute("""SELECT d.status,e.text FROM documents d
                    JOIN excerpts e ON e.sha256=d.sha256
                    WHERE d.sha256=? AND e.page=? AND e.part=?""",
                    (lease["source_id"][4:],lease["page"],lease["part"])).fetchone()
                if not row or row["status"] not in ("ready","approved"):
                    raise RuntimeError("Source no longer eligible")
                body=row["text"]
        else:
            with connect(self.db_path) as db:
                row=db.execute("SELECT source_text FROM research_jobs WHERE id=?",
                               (lease["source_id"][9:],)).fetchone()
                if not row:
                    raise RuntimeError("Research snapshot unavailable")
                body=row["source_text"]
        off=lease["byte_offset"]
        part=body[off:off+CHUNK]
        if digest(lease["source_revision"] + ":" + digest(part))!=lease["revision"]:
            raise RuntimeError("Extraction revision changed; inventory refresh needed")
        if lease["page"]:
            return f"[PDF page {lease['page']} part {lease['part']}]\n"+part
        return "[Previously stored research snapshot]\n"+part

    def finish(self, lease, answer, worker=""):
        if not isinstance(answer,str) or not answer.strip() or len(answer)>24000:
            raise ValueError("Invalid or oversized research draft")
        now=time.time()
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("""SELECT pass_completed,passes FROM cr_jobs
                WHERE id=? AND token=? AND state='leased' AND lease_until>?""",
                (lease["id"],lease["token"],now)).fetchone()
            current=db.execute("""SELECT 1 FROM cr_sections s JOIN cr_sources d
                ON d.id=s.source_id WHERE s.slot=? AND s.revision=? AND
                s.current=1 AND d.present=1 AND d.eligible=1 AND d.excluded=0""",
                (lease["slot"],lease["revision"])).fetchone()
            if not row or not current:
                return False  # fenced or superseded worker cannot publish output
            pass_no=row["pass_completed"]+1
            db.execute("""INSERT INTO cr_results(job_id,pass_number,notes,sha256,created)
                VALUES(?,?,?,?,?)""",(lease["id"],pass_no,answer.strip(),
                                       digest(answer.strip()),now))
            state="completed" if pass_no>=row["passes"] else "queued"
            db.execute("""UPDATE cr_jobs SET pass_completed=?,state=?,token='',
                lease_until=0,retry_at=0,attempts=0,worker=?,error='',updated=?
                WHERE id=?""",(pass_no,state,worker[:24],now,lease["id"]))
            if state=="completed":
                self._event(db,"job-complete",lease["id"])
            return True

    def fail(self, lease, error):
        now=time.time()
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("""SELECT attempts FROM cr_jobs WHERE id=?
                AND token=? AND state='leased'""",
                (lease["id"],lease["token"])).fetchone()
            if not row:
                return False
            attempts=row["attempts"]
            db.execute("""UPDATE cr_jobs SET state=?,token='',lease_until=0,
                retry_at=?,error=?,updated=? WHERE id=?""",
                ("failed" if attempts>=MAX_ATTEMPTS else "queued",
                 now+self._retry_delay(attempts),type(error).__name__[:80],
                 now,lease["id"]))
            self._event(db,"job-error",lease["id"],type(error).__name__)
            return True

    def run_once(self, infer):
        """Run one bounded pass. Caller provides existing local llama.cpp router.

        infer(messages,target,model_id,job_id) -> (draft_text,worker_name).
        No untrusted text can select a URL, filesystem path or executable.
        """
        lease=self.claim()
        if lease is None:
            return False
        keepalive=Event()
        def renew():
            while not keepalive.wait(30):
                if not self.heartbeat(lease["id"],lease["token"]):
                    return
        watcher=Thread(target=renew,daemon=True)
        watcher.start()
        try:
            section=self._section_text(lease)
            with connect(self.db_path) as db:
                last=db.execute("""SELECT notes FROM cr_results
                    WHERE job_id=? AND pass_number=?""",
                    (lease["id"],lease["pass_completed"])).fetchone()
            spec={"title":lease["title"],"focus":"",
                  "passes":lease["passes"]}
            messages=pass_messages(spec,section,lease["content_sha"],
                                   lease["pass_completed"]+1,
                                   last["notes"] if last else "")
            messages[-1]["content"] += (
                f"\nCurrent source section: {lease['slot']}. "
                "This is one section, not the entire document. "
                "Cite only its actual page or part. Unreviewed draft.")
            answer,worker=infer(messages,lease["target"],lease["model_id"],lease["id"])
            self.finish(lease,answer,worker)
        except Exception as exc:
            self.fail(lease,exc)
        finally:
            keepalive.set()
            watcher.join(timeout=1)
        return True

    def status(self):
        with connect(self.db_path) as db:
            config=dict(db.execute("SELECT * FROM cr_config WHERE id=1").fetchone())
            rows=db.execute("""SELECT state,count(*) n FROM cr_jobs GROUP BY state""").fetchall()
            source=db.execute("""SELECT count(*) sources,
                coalesce(sum(CASE WHEN eligible=1 AND excluded=0 AND present=1
                    THEN 1 ELSE 0 END),0) eligible_sources,
                coalesce(sum(CASE WHEN excluded=1 THEN 1 ELSE 0 END),0) excluded_sources,
                coalesce(sum(missing_pages),0) missing_pages,
                coalesce(sum(CASE WHEN extraction_status IN ('failed','processing','queued')
                    THEN 1 ELSE 0 END),0) incomplete_sources
                FROM cr_sources""").fetchone()
            sections=db.execute("""SELECT count(*) total,
                coalesce(sum(CASE WHEN current=1 THEN 1 ELSE 0 END),0) current
                FROM cr_sections""").fetchone()
            cycle=db.execute("SELECT * FROM cr_cycles ORDER BY id DESC LIMIT 1").fetchone()
            recent=db.execute("""SELECT id,kind,title,extraction_status,pages,
                extracted_parts,missing_pages,eligible,excluded,priority,present
                FROM cr_sources ORDER BY priority DESC,title LIMIT 60""").fetchall()
            in_flight=db.execute("""SELECT j.id,j.pass_completed,j.passes,
                s.source_id,s.page,s.part,j.worker,j.attempts,j.error,j.lease_until
                FROM cr_jobs j JOIN cr_sections s ON s.slot=j.slot
                AND s.revision=j.revision WHERE j.state='leased' LIMIT 1""").fetchone()
            chat=db.execute("""SELECT count(*) FROM jobs WHERE state IN
                ('queued','running')""").fetchone()[0]
        return {"config":config,"jobs":{r["state"]:r["n"] for r in rows},
                "inventory":dict(source),"sections":dict(sections),
                "cycle":dict(cycle) if cycle else None,
                "active":dict(in_flight) if in_flight else None,
                "interactive_jobs":chat,"cpu_load_1m":os.getloadavg()[0],
                "sources":[dict(r) for r in recent],
                "draft_only":True,"weight_training":False}
