#!/usr/bin/env python3
"""Private, opt-in, revisioned continuous research. No network or training API.

The PDF ingestion database is opened read-only. All scheduling and draft results
reside in the existing private Gentoo input SQLite database. No source text,
model endpoint, shell command or training operation is exposed by this module.
"""
import hashlib
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import subprocess
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
          min_available_ram_mib INTEGER NOT NULL DEFAULT 0,
          min_gpu_free_mib INTEGER NOT NULL DEFAULT 0,
          max_database_mib INTEGER NOT NULL DEFAULT 0,
          quiet_start_utc INTEGER NOT NULL DEFAULT -1,
          quiet_end_utc INTEGER NOT NULL DEFAULT -1,
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
          state TEXT NOT NULL CHECK(state IN ('open','completed')),
          scope TEXT NOT NULL DEFAULT 'full'
            CHECK(scope IN ('full','new_only'))
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
          created REAL NOT NULL, duration_ms INTEGER NOT NULL DEFAULT 0,
          output_chars INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(job_id,pass_number)
        );
        CREATE TABLE IF NOT EXISTS cr_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, happened REAL NOT NULL,
          kind TEXT NOT NULL, subject TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS cr_event_subject
          ON cr_events(subject,id);
        CREATE TRIGGER IF NOT EXISTS cr_events_no_update
          BEFORE UPDATE ON cr_events BEGIN
            SELECT RAISE(ABORT,'Research audit is append-only');
          END;
        CREATE TRIGGER IF NOT EXISTS cr_events_no_delete
          BEFORE DELETE ON cr_events BEGIN
            SELECT RAISE(ABORT,'Research audit cannot be deleted');
          END;
        CREATE TABLE IF NOT EXISTS cr_energy_attempts (
          attempt_id TEXT PRIMARY KEY, started REAL NOT NULL,
          watts INTEGER NOT NULL, rate_microusd INTEGER NOT NULL,
          duration_ms INTEGER, outcome TEXT NOT NULL DEFAULT 'open');
        CREATE INDEX IF NOT EXISTS cr_energy_started ON cr_energy_attempts(started);
        CREATE TABLE IF NOT EXISTS cr_resource_samples (
          minute INTEGER PRIMARY KEY, queued INTEGER NOT NULL,
          leased INTEGER NOT NULL, failed INTEGER NOT NULL,
          process_rss_mib INTEGER NOT NULL DEFAULT -1,
          host_free_ram_mib INTEGER NOT NULL DEFAULT -1,
          database_bytes INTEGER NOT NULL DEFAULT -1
        );
        CREATE TABLE IF NOT EXISTS cr_job_cancellations (
          job_id TEXT PRIMARY KEY, actor TEXT NOT NULL, happened REAL NOT NULL,
          source_id TEXT NOT NULL, cycle INTEGER NOT NULL,
          pass_completed INTEGER NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS cr_job_cancellations_no_update
          BEFORE UPDATE ON cr_job_cancellations BEGIN
            SELECT RAISE(ABORT,'Cancellation history is append-only');
          END;
        CREATE TRIGGER IF NOT EXISTS cr_job_cancellations_no_delete
          BEFORE DELETE ON cr_job_cancellations BEGIN
            SELECT RAISE(ABORT,'Cancellation history cannot be deleted');
          END;
        """)
        # Compatible with v1 private databases; never rebuild existing tables.
        cols={row[1] for row in db.execute("PRAGMA table_info(cr_config)")}
        for column,definition in (
            ("daily_energy_wh","INTEGER NOT NULL DEFAULT 0"),
            ("power_override_until","REAL NOT NULL DEFAULT 0"),
            ("research_watts","INTEGER NOT NULL DEFAULT 0"),
            ("rate_microusd_kwh","INTEGER NOT NULL DEFAULT -1"),
            ("min_available_ram_mib","INTEGER NOT NULL DEFAULT 0"),
            ("min_gpu_free_mib","INTEGER NOT NULL DEFAULT 0"),
            ("max_database_mib","INTEGER NOT NULL DEFAULT 0"),
            ("quiet_start_utc","INTEGER NOT NULL DEFAULT -1"),
            ("quiet_end_utc","INTEGER NOT NULL DEFAULT -1")):
            if column not in cols:
                db.execute(f"ALTER TABLE cr_config ADD COLUMN {column} {definition}")
        # Existing Phase-A draft rows remain valid; unknown old timings are 0.
        columns={row[1] for row in db.execute("PRAGMA table_info(cr_results)")}
        for column in ("duration_ms","output_chars"):
            if column not in columns:
                db.execute(f"ALTER TABLE cr_results ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        # Real token telemetry is optional: old passes remain unknown, not zero.
        for column,kind in (("prompt_tokens","INTEGER"),
                            ("completion_tokens","INTEGER"),
                            ("generation_tps","REAL")):
            if column not in columns:
                db.execute(f"ALTER TABLE cr_results ADD COLUMN {column} {kind}")
        cycles={row[1] for row in db.execute("PRAGMA table_info(cr_cycles)")}
        if "scope" not in cycles:
            db.execute("""ALTER TABLE cr_cycles ADD COLUMN scope TEXT
                NOT NULL DEFAULT 'full' CHECK(scope IN ('full','new_only'))""")
        db.execute("""CREATE INDEX IF NOT EXISTS cr_job_slot_history
            ON cr_jobs(slot,revision,created)""")
        db.execute("""CREATE INDEX IF NOT EXISTS cr_result_recent
            ON cr_results(created)""")


def database_size_bytes(path):
    """On-disk private SQLite footprint, including uncheckpointed WAL bytes.

    Returns unknown instead of zero when the active main database cannot be
    stat'ed. Only used for optional background admission, never normal chat.
    """
    path=Path(path)
    try:
        total=path.stat().st_size
        for suffix in ("-wal","-shm"):
            sidecar=Path(str(path)+suffix)
            try:
                total+=sidecar.stat().st_size
            except FileNotFoundError:
                continue
        return total
    except OSError:
        return None


def process_resident_mib():
    """Linux resident memory of this process, not a claim about GPU VRAM."""
    try:
        with open("/proc/self/status",encoding="ascii") as stream:
            for line in stream:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])//1024
    except (OSError,ValueError,IndexError):
        pass
    return None


def free_memory_mib():
    """Host-level Linux RAM headroom; unreadable metrics fail closed if used."""
    try:
        with open("/proc/meminfo",encoding="ascii") as stream:
            for line in stream:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1])//1024
    except (OSError,ValueError,IndexError):
        pass
    return None


def gpu_memory_snapshot(*, drm_root="/sys/class/drm",
                        nvidia_binary="/usr/bin/nvidia-smi",
                        runner=None):
    """Read-only, bounded local GPU/VRAM discovery for Gentoo.

    Linux amdgpu sysfs provides VRAM bytes. NVIDIA's fixed-path local
    nvidia-smi provides MiB. Unknown memory is None, NEVER assumed free.
    No CUDA/Vulkan initialization, GPU workloads, shells or network calls.
    """
    devices=[]
    root=Path(drm_root)
    try:
        cards=sorted((c for c in root.glob("card[0-9]*")
                      if re.fullmatch(r"card[0-9]+",c.name)),
                     key=lambda c:int(c.name[4:]))[:32]
    except OSError:
        cards=[]
    for card in cards:
        try:
            vendor=(card/"device"/"vendor").read_text(encoding="ascii").strip().lower()
            if vendor!="0x1002":
                continue
            total=int((card/"device"/"mem_info_vram_total").read_text().strip())
            used=int((card/"device"/"mem_info_vram_used").read_text().strip())
            if total<0 or used<0 or used>total:
                raise ValueError("Invalid amdgpu VRAM counters")
            devices.append({"backend":"amd","device":card.name,
                "total_mib":total//1048576,"free_mib":(total-used)//1048576,
                "used_mib":used//1048576,"telemetry":"amdgpu-sysfs"})
        except (OSError,ValueError):
            # An AMD card with unknown memory must not be declared available.
            try:
                if (card/"device"/"vendor").read_text().strip().lower()=="0x1002":
                    devices.append({"backend":"amd","device":card.name,
                                    "total_mib":None,"free_mib":None,
                                    "used_mib":None,"telemetry":"unavailable"})
            except OSError:
                continue
    # Explicit allowlisted command path, not PATH search or document input.
    command=Path(nvidia_binary)
    if command.is_absolute() and command.is_file():
        if runner is None:
            runner=subprocess.run
        try:
            result=runner([str(command),
                "--query-gpu=index,memory.total,memory.free",
                "--format=csv,noheader,nounits"],
                capture_output=True,text=True,timeout=1,check=False)
            if result.returncode==0:
                for line in result.stdout.splitlines()[:32]:
                    fields=[piece.strip() for piece in line.split(",")]
                    if len(fields)!=3:
                        continue
                    try:
                        index,total,free=map(int,fields)
                    except ValueError:
                        continue
                    if index<0 or total<0 or not 0<=free<=total:
                        continue
                    devices.append({"backend":"nvidia","device":str(index),
                        "total_mib":total,"free_mib":free,
                        "used_mib":total-free,"telemetry":"nvidia-smi"})
        except (OSError,subprocess.TimeoutExpired,ValueError):
            pass
    return devices


def daily_energy(db, now):
    """UTC start-day usage; an interrupted or uncalibrated attempt is unknown."""
    midnight = int(now)//86400*86400
    return db.execute("""SELECT count(*) attempts,
        count(CASE WHEN watts>0 THEN duration_ms END) estimated,
        coalesce(sum(CASE WHEN watts>0 THEN watts*1.0*duration_ms END),0) watt_ms
        FROM cr_energy_attempts WHERE started>=? AND started<?""",
        (midnight,midnight+86400)).fetchone()


def power_blocks(cfg, now, usage):
    if cfg["power_override_until"]>now or not cfg["daily_energy_wh"]:
        return []
    if (not cfg["research_watts"] or usage is None or
            usage["estimated"]!=usage["attempts"]):
        return ["daily-energy-unknown"]
    if usage["watt_ms"]>=cfg["daily_energy_wh"]*3600000:
        return ["daily-energy-budget"]
    return []


def admission_reasons(cfg, *, free_mib=None, load=None, hour=None,
                      gpu_devices=None, database_bytes=None, now=None, energy_usage=None):
    """Pure, testable background gating. Never affects interactive requests."""
    if free_mib is None:
        free_mib=free_memory_mib()
    if load is None:
        load=os.getloadavg()[0]
    if hour is None:
        hour=datetime.fromtimestamp(time.time() if now is None else now,timezone.utc).hour
    blocked=[]
    if cfg["min_available_ram_mib"]:
        if free_mib is None:
            blocked.append("ram-unavailable")
        elif free_mib<cfg["min_available_ram_mib"]:
            blocked.append("low-free-ram")
    if cfg["max_cpu_load"] and load>cfg["max_cpu_load"]:
        blocked.append("cpu-load")
    if cfg["max_database_mib"]:
        if database_bytes is None:
            blocked.append("database-size-unavailable")
        elif database_bytes>=cfg["max_database_mib"]*1048576:
            blocked.append("database-size-limit")
    min_vram=cfg["min_gpu_free_mib"]
    if min_vram:
        target=cfg["target"]
        if target not in ("amd","nvidia"):
            blocked.append("gpu-target-required")
        else:
            if gpu_devices is None:
                gpu_devices=gpu_memory_snapshot()
            selected=[gpu for gpu in gpu_devices if gpu["backend"]==target]
            if not selected or any(type(gpu.get("free_mib")) is not int
                                   for gpu in selected):
                blocked.append("vram-telemetry-unavailable")
            elif any(gpu["free_mib"]<min_vram for gpu in selected):
                blocked.append("low-free-vram")
    start,end=cfg["quiet_start_utc"],cfg["quiet_end_utc"]
    now=time.time() if now is None else now
    if cfg["power_override_until"]<=now and 0<=start<=23 and 0<=end<=23:
        if (start<end and start<=hour<end) or (
            start>end and (hour>=start or hour<end)):
            blocked.append("quiet-hours-utc")
    return blocked + power_blocks(cfg,now,energy_usage)


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
        db.execute("""INSERT INTO cr_events(happened,kind,subject,detail)
            SELECT ?,'job-superseded',j.id,'' FROM cr_jobs j
            WHERE j.state IN ('queued','leased') AND NOT EXISTS
            (SELECT 1 FROM cr_sections s JOIN cr_sources d ON d.id=s.source_id
             WHERE s.slot=j.slot AND s.revision=j.revision
             AND s.current=1 AND d.eligible=1 AND d.present=1 AND d.excluded=0)""",
            (time.time(),))
        db.execute("""UPDATE cr_jobs SET state='superseded',token='',lease_until=0,
            updated=? WHERE state IN ('queued','leased') AND NOT EXISTS
            (SELECT 1 FROM cr_sections s JOIN cr_sources d ON d.id=s.source_id
             WHERE s.slot=cr_jobs.slot AND s.revision=cr_jobs.revision
             AND s.current=1 AND d.eligible=1 AND d.present=1 AND d.excluded=0)""",
                   (time.time(),))

    def control(self, action, *, source_id="", value=None, job_id="", actor=""):
        if action not in ("enable", "pause", "resume", "stop", "disable",
                          "include", "exclude", "priority", "configure", "scan",
                          "cancel","power_override"):
            raise ValueError("Invalid control action")
        if action=="cancel":
            if (not isinstance(job_id,str) or
                not re.fullmatch(r"[0-9a-f]{32}",job_id) or
                not isinstance(actor,str) or
                not re.fullmatch(r"cms-admin:[1-9][0-9]{0,12}",actor)):
                raise ValueError("Valid job ID and authenticated administrator required")
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            if action=="power_override":
                if (not isinstance(actor,str) or not re.fullmatch(r"cms-admin:[1-9][0-9]{0,12}",actor)
                        or type(value) is not int or value not in (0,15,30,60)):
                    raise ValueError("Authenticated admin and bounded override duration required")
                until=now+60*value if value else 0
                db.execute("UPDATE cr_config SET power_override_until=?,updated=? WHERE id=1",(until,now))
                self._event(db,"power-override","research",actor+" minutes="+str(value))
            elif action=="cancel":
                # An existing SQLite stopped state is used to avoid rebuilding
                # the legacy cr_jobs CHECK constraint during an in-place upgrade.
                # The immutable marker makes it distinct from a global stop.
                job=db.execute("""SELECT j.id,j.cycle,j.pass_completed,j.state,
                    s.source_id FROM cr_jobs j JOIN cr_sections s
                    ON s.slot=j.slot AND s.revision=j.revision
                    WHERE j.id=?""",(job_id,)).fetchone()
                if not job or job["state"] not in ("queued","leased","stopped"):
                    raise ValueError("Job missing or already terminal")
                if db.execute("""SELECT 1 FROM cr_job_cancellations
                    WHERE job_id=?""",(job_id,)).fetchone():
                    raise ValueError("Job already cancelled")
                db.execute("""INSERT INTO cr_job_cancellations(
                    job_id,actor,happened,source_id,cycle,pass_completed)
                    VALUES(?,?,?,?,?,?)""",(job_id,actor,now,job["source_id"],
                                              job["cycle"],job["pass_completed"]))
                db.execute("""UPDATE cr_jobs SET state='stopped',token='',
                    lease_until=0,retry_at=0,error='Cancelled by administrator',
                    updated=? WHERE id=?""",(now,job_id))
                self._event(db,"job-cancelled",job_id,actor)
                self._schedule(db,now)
            elif action in ("include", "exclude", "priority"):
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
                    "max_cpu_load","max_chat_jobs","min_available_ram_mib",
                    "min_gpu_free_mib","max_database_mib",
                    "research_watts","rate_microusd_kwh","daily_energy_wh","quiet_start_utc","quiet_end_utc"}:
                    raise ValueError("Invalid research settings")
                if "quiet_start_utc" in value or "quiet_end_utc" in value:
                    if not {"quiet_start_utc","quiet_end_utc"} <= set(value):
                        raise ValueError("Quiet hours require both UTC boundaries")
                    start,end=value["quiet_start_utc"],value["quiet_end_utc"]
                    if (type(start) is not int or type(end) is not int or
                        not ((start==-1 and end==-1) or
                             (0<=start<=23 and 0<=end<=23 and start!=end))):
                        raise ValueError("Use -1/-1 or distinct UTC hours 0..23")
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
                    if key=="daily_energy_wh" and (type(item) is not int or not 0<=item<=2400000):
                        raise ValueError("Daily energy admission budget must be 0..2400000 Wh")
                    if key=="research_watts" and (type(item) is not int or not 0<=item<=100000):
                        raise ValueError("Estimated inference watts must be 0..100000")
                    if key=="rate_microusd_kwh" and (type(item) is not int or not -1<=item<=10000000):
                        raise ValueError("Electricity rate outside supported range")
                    if key=="max_chat_jobs" and (type(item) is not int or
                                                 not 0<=item<=4):
                        raise ValueError("Invalid interactive queue budget")
                    if key=="min_available_ram_mib" and (type(item) is not int
                                                or not 0<=item<=1048576):
                        raise ValueError("Invalid minimum free RAM")
                    if key=="min_gpu_free_mib" and (type(item) is not int
                                                or not 0<=item<=1048576):
                        raise ValueError("Invalid minimum free VRAM")
                    if key=="max_database_mib" and (type(item) is not int
                                                or not 0<=item<=1048576):
                        raise ValueError("Invalid database budget")
                    # Column names are literal allowlisted, never external SQL.
                    db.execute(f"UPDATE cr_config SET {key}=?,updated=? WHERE id=1",
                               (item,now))
                self._event(db,"configure","research")
            else:
                next_mode={"enable":"running","resume":"running","pause":"paused",
                           "stop":"stopped","disable":"disabled"}[action]
                db.execute("UPDATE cr_config SET mode=?,updated=?,scan_requested=CASE WHEN ?='running' THEN 1 ELSE scan_requested END WHERE id=1",
                           (next_mode,now,next_mode))
                if action in ("disable","stop","pause"):
                    db.execute("UPDATE cr_config SET power_override_until=0 WHERE id=1")
                if action=="stop":
                    db.execute("""INSERT INTO cr_events(happened,kind,subject,detail)
                        SELECT ?,'job-stopped',id,'' FROM cr_jobs
                        WHERE state IN ('queued','leased')""",(now,))
                    db.execute("""UPDATE cr_jobs SET state='stopped',token='',
                        lease_until=0,updated=? WHERE state IN ('queued','leased')""",
                               (now,))
                if action in ("enable","resume"):
                    db.execute("""INSERT INTO cr_events(happened,kind,subject,detail)
                        SELECT ?,'job-resumed',j.id,'' FROM cr_jobs j
                        WHERE j.state='stopped' AND NOT EXISTS (
                          SELECT 1 FROM cr_job_cancellations c
                          WHERE c.job_id=j.id)""",(now,))
                    db.execute("""UPDATE cr_jobs SET state='queued',retry_at=0
                        WHERE state='stopped' AND NOT EXISTS (
                          SELECT 1 FROM cr_job_cancellations c
                          WHERE c.job_id=cr_jobs.id)""")
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
        active = db.execute("""SELECT id,scope FROM cr_cycles
            WHERE state='open' ORDER BY id DESC LIMIT 1""").fetchone()
        # The scope is durable. Subsequent claims and finish() must not widen
        # a targeted cycle to include already-completed unchanged sections.
        uncovered="""AND NOT EXISTS (SELECT 1 FROM cr_jobs previous
            WHERE previous.slot=s.slot AND previous.revision=s.revision
            AND (previous.state IN ('completed','failed') OR
                (previous.state='stopped' AND EXISTS (
                  SELECT 1 FROM cr_job_cancellations c
                  WHERE c.job_id=previous.id))))"""
        targeted=False
        if not active:
            targeted=now-conf["last_completed"]<conf["revisit_seconds"]
            # New/revised/uncovered sections must not wait for the normal
            # revisit interval. Terminal failed jobs count as covered until
            # their next full cycle, avoiding an unbounded retry loop.
            predicate=uncovered if targeted else ""
            if not db.execute("""SELECT 1 FROM cr_sections s JOIN cr_sources d
                ON d.id=s.source_id WHERE s.current=1 AND d.present=1
                AND d.eligible=1 AND d.excluded=0 """+predicate+
                " LIMIT 1").fetchone():
                return
            cycle = db.execute("""INSERT INTO cr_cycles(started,state,scope)
                VALUES(?,'open',?)""",(now,'new_only' if targeted else 'full')).lastrowid
            self._event(db,"cycle-start",str(cycle),
                        "new-or-revised-only" if targeted else "full-coverage")
        else:
            cycle = active["id"]
            targeted=active["scope"]=="new_only"
        self._supersede(db)
        # Apply the targeted filter only when creating an off-cycle scan.
        # Once open, newly discovered revisions can still join this cycle.
        predicate=uncovered if targeted else ""
        db.execute("""INSERT OR IGNORE INTO cr_jobs(id,cycle,slot,revision,passes,
            state,created,updated)
            SELECT lower(hex(randomblob(16))),?,s.slot,s.revision,?,'queued',?,?
            FROM cr_sections s JOIN cr_sources d ON d.id=s.source_id
            WHERE s.current=1 AND d.present=1 AND d.eligible=1 AND d.excluded=0 """
            +predicate,(cycle,conf["passes"],now,now))
        db.execute("""INSERT INTO cr_events(happened,kind,subject,detail)
            SELECT ?,'job-queued',j.id,'' FROM cr_jobs j
            WHERE j.cycle=? AND j.created=? AND NOT EXISTS (
              SELECT 1 FROM cr_events v WHERE v.subject=j.id
              AND v.kind='job-queued')""",(now,cycle,now))
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
        # GPU telemetry can execute a bounded local subprocess. Probe BEFORE
        # obtaining the write lock, so normal interactive work is not blocked.
        with connect(self.db_path) as preflight:
            initial=preflight.execute("""SELECT min_gpu_free_mib,max_database_mib,daily_energy_wh,mode
                FROM cr_config WHERE id=1""").fetchone()
        gpu_devices=gpu_memory_snapshot() if initial["min_gpu_free_mib"] else None
        db_bytes=database_size_bytes(self.db_path) if initial["max_database_mib"] else None
        # Source reads and model calls occur OUTSIDE this short write transaction.
        with connect(self.db_path) as db:
            usage = None
            version = None
            if initial["daily_energy_wh"] and initial["mode"]=="running":
                # Aggregate outside the writer lock. A concurrent commit on this
                # same database invalidates the read; defer to the next poll.
                version=db.execute("PRAGMA data_version").fetchone()[0]
                usage=daily_energy(db,now)
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
                if tries>=MAX_ATTEMPTS:
                    self._event(db,"job-failed",row["id"],"lease-expired")
            conf=db.execute("SELECT * FROM cr_config WHERE id=1").fetchone()
            if conf["mode"]!="running":
                return None
            # Verify any newly-enabled ceiling even if config changed between
            # preflight and the claim transaction. A ceiling blocks scheduling
            # new background work, never existing interactive chat.
            if conf["max_database_mib"] and (
                db_bytes is None or
                db_bytes>=conf["max_database_mib"]*1048576):
                return None
            self._schedule(db,now)
            if db.execute("SELECT 1 FROM cr_jobs WHERE state='leased' LIMIT 1").fetchone():
                return None
            if db.execute("""SELECT count(*) FROM jobs
                WHERE state IN ('queued','running')""").fetchone()[0] > conf["max_chat_jobs"]:
                return None
            if db.execute("""SELECT 1 FROM research_jobs
                WHERE state IN ('queued','running') LIMIT 1""").fetchone():
                return None
            if conf["min_gpu_free_mib"] and gpu_devices is None:
                return None  # Config enabled during preflight; fail closed.
            if conf["daily_energy_wh"] and version!=db.execute("PRAGMA data_version").fetchone()[0]:
                return None
            if admission_reasons(conf,gpu_devices=gpu_devices,now=now,energy_usage=usage,
                                 database_bytes=db_bytes):
                return None
            row=db.execute("""SELECT j.id,j.slot,j.revision,j.pass_completed,
                j.passes,s.source_id,s.page,s.part,s.byte_offset,
                s.source_revision,d.title,d.content_sha,d.priority
                FROM cr_jobs j JOIN cr_sections s ON s.slot=j.slot
                AND s.revision=j.revision JOIN cr_sources d ON d.id=s.source_id
                WHERE j.state='queued' AND j.retry_at<=? AND s.current=1
                AND d.present=1 AND d.eligible=1 AND d.excluded=0
                ORDER BY d.priority DESC,
                CASE WHEN EXISTS (
                    SELECT 1 FROM cr_jobs previous
                    WHERE previous.slot=j.slot AND previous.revision=j.revision
                    AND previous.cycle<j.cycle AND previous.state='completed'
                ) THEN 1 ELSE 0 END ASC,
                j.created,j.id LIMIT 1""",(now,)).fetchone()
            if not row:
                return None
            token=uuid.uuid4().hex
            db.execute("""UPDATE cr_jobs SET state='leased',token=?,lease_until=?,
                attempts=attempts+1,updated=? WHERE id=? AND state='queued'""",
                       (token,now+LEASE_SECONDS,now,row["id"]))
            self._event(db,"job-leased",row["id"],
                        "pass="+str(row["pass_completed"]+1))
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

    def finish(self, lease, answer, worker="", *, duration_ms=0, token_metrics=None):
        if not isinstance(answer,str) or not answer.strip() or len(answer)>24000:
            raise ValueError("Invalid or oversized research draft")
        if type(duration_ms) is not int or not 0<=duration_ms<=86400000:
            raise ValueError("Invalid inference duration")
        if token_metrics is None:
            token_metrics={}
        if not isinstance(token_metrics,dict):
            raise ValueError("Invalid token telemetry")
        if set(token_metrics)-{"prompt_tokens","completion_tokens","generation_tps"}:
            raise ValueError("Unknown token telemetry fields")
        for key in ("prompt_tokens","completion_tokens"):
            number=token_metrics.get(key)
            if number is not None and (type(number) is not int or not 0<=number<=10_000_000):
                raise ValueError("Invalid reported token count")
        rate=token_metrics.get("generation_tps")
        if rate is not None and (type(rate) not in (float,int) or
            not math.isfinite(rate) or not 0<rate<=1_000_000):
            raise ValueError("Invalid reported token rate")
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
            db.execute("""INSERT INTO cr_results(job_id,pass_number,notes,sha256,
                created,duration_ms,output_chars,prompt_tokens,completion_tokens,
                generation_tps)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",(lease["id"],pass_no,answer.strip(),
                    digest(answer.strip()),now,duration_ms,len(answer.strip()),
                    token_metrics.get("prompt_tokens"),
                    token_metrics.get("completion_tokens"),
                    token_metrics.get("generation_tps")))
            state="completed" if pass_no>=row["passes"] else "queued"
            db.execute("""UPDATE cr_jobs SET pass_completed=?,state=?,token='',
                lease_until=0,retry_at=0,attempts=0,worker=?,error='',updated=?
                WHERE id=?""",(pass_no,state,worker[:24],now,lease["id"]))
            self._event(db,"pass-checkpoint",lease["id"],str(pass_no))
            if state=="completed":
                self._event(db,"job-complete",lease["id"])
                # Close a fully completed coverage cycle in this same
                # transaction; do not wait for the next background poll.
                self._schedule(db,now)
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
            if attempts>=MAX_ATTEMPTS:
                self._event(db,"job-failed",lease["id"],type(error).__name__)
                self._schedule(db,now)
            return True

    def _start_energy_attempt(self, lease):
        """Snapshot assumptions immediately before inference, once per live lease."""
        now = time.time()
        with connect(self.db_path) as db:
            return db.execute("""INSERT INTO cr_energy_attempts(attempt_id,started,watts,rate_microusd)
                SELECT ?,?,c.research_watts,c.rate_microusd_kwh
                FROM cr_jobs j CROSS JOIN cr_config c
                WHERE c.id=1 AND j.id=? AND j.token=? AND j.state='leased' AND j.lease_until>?
                ON CONFLICT(attempt_id) DO NOTHING""",
                (digest(lease["token"]),now,lease["id"],lease["token"],now)).rowcount == 1

    def _end_energy_attempt(self, token, duration_ms, outcome):
        with connect(self.db_path) as db:
            db.execute("""UPDATE cr_energy_attempts SET duration_ms=?,outcome=?
                WHERE attempt_id=? AND outcome='open'""",(duration_ms,outcome,digest(token)))

    @staticmethod
    def _energy_summary(db, now):
        # 1 kWh = 3.6 billion watt-milliseconds. Use floating point only for
        # aggregation/display to avoid overflowing SQLite's integer SUM.
        return dict(db.execute("""SELECT COUNT(*) attempts,
            COUNT(duration_ms) timed, COUNT(CASE WHEN outcome='error' THEN 1 END) errors,
            COUNT(CASE WHEN watts>0 THEN duration_ms END) energy_samples,
            COUNT(CASE WHEN watts>0 AND rate_microusd>=0 THEN duration_ms END) priced_samples,
            SUM(CASE WHEN watts>0 THEN duration_ms*1.0*watts/3600000000 END) kwh,
            SUM(CASE WHEN watts>0 AND rate_microusd>=0 THEN
                duration_ms*1.0*watts/3600000000*rate_microusd/1000000 END) usd
            FROM cr_energy_attempts WHERE started BETWEEN ? AND ?""",
            (now-7*86400,now)).fetchone())

    def run_once(self, infer):
        """Run one bounded pass. Caller provides existing local llama.cpp router.

        infer(messages,target,model_id,job_id) -> (draft_text,worker_name)
        or (draft_text,worker_name,measured_token_metrics).
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
            # Optional same-PDF citation enrichment: never widen the vehicle,
            # year or source selection and never depend on index availability.
            if lease["source_id"].startswith("pdf:"):
                from manual_relationship_context import append_for_section
                append_for_section(messages, source=self.source,
                    index=self.index.with_name("manual_relationships.sqlite3"),
                    source_id=lease["source_id"], section=section)
            if not self._start_energy_attempt(lease):
                return False
            started=time.monotonic()
            outcome="error"
            try:
                returned=infer(messages,lease["target"],lease["model_id"],lease["id"])
                outcome="returned"
            finally:
                elapsed_ms=max(1,round((time.monotonic()-started)*1000))
                self._end_energy_attempt(lease["token"],elapsed_ms,outcome)
            if not isinstance(returned,(tuple,list)) or len(returned) not in (2,3):
                raise ValueError("Invalid inference worker response")
            answer,worker=returned[:2]
            tokens=returned[2] if len(returned)==3 else None
            self.finish(lease,answer,worker,
                        duration_ms=min(elapsed_ms,86400000),
                        token_metrics=tokens)
        except Exception as exc:
            self.fail(lease,exc)
        finally:
            keepalive.set()
            watcher.join(timeout=1)
        return True

    @staticmethod
    def validate_source_id(source_id):
        if (not isinstance(source_id,str) or not re.fullmatch(
            r"(?:pdf:[0-9a-f]{64}|research:[0-9a-f]{32})",source_id)):
            raise ValueError("Invalid source identifier")
        return source_id

    def history(self, source_id, offset=0, limit=8, audit_offset=0):
        """Page original private draft history with stable source provenance.

        Read-only. Includes superseded and failed jobs, and all past cycles.
        Only the existing HMAC-authenticated admin facade may call this API.
        """
        source_id=self.validate_source_id(source_id)
        if type(offset) is not int or not 0<=offset<=1000000:
            raise ValueError("Invalid history offset")
        if type(limit) is not int or not 1<=limit<=8:
            raise ValueError("Invalid history page size")
        if type(audit_offset) is not int or not 0<=audit_offset<=1000000:
            raise ValueError("Invalid audit offset")
        with connect(self.db_path) as db:
            source=db.execute("""SELECT id,kind,title,revision,content_sha,
                extraction_status,reviewed,pages,extracted_parts,missing_pages,
                eligible,excluded,priority,present FROM cr_sources
                WHERE id=?""",(source_id,)).fetchone()
            if source is None:
                raise ValueError("Source not present in the inventory")
            scope="""FROM cr_jobs j JOIN cr_sections s
                ON s.slot=j.slot AND s.revision=j.revision
                WHERE s.source_id=?"""
            count=db.execute("""SELECT count(*) """+scope,
                             (source_id,)).fetchone()[0]
            pass_total=db.execute("""SELECT count(*) FROM cr_results r JOIN cr_jobs j
                ON j.id=r.job_id JOIN cr_sections s
                ON s.slot=j.slot AND s.revision=j.revision
                WHERE s.source_id=?""",(source_id,)).fetchone()[0]
            jobs=db.execute("""SELECT j.id,j.cycle,j.state,j.pass_completed,
                j.passes,j.attempts,j.worker,j.error,j.created,j.updated,
                s.page,s.part,s.revision,s.source_revision,
                c.actor cancelled_by,c.happened cancelled_at,
                (SELECT sum(r.prompt_tokens) FROM cr_results r
                 WHERE r.job_id=j.id) prompt_tokens,
                (SELECT sum(r.completion_tokens) FROM cr_results r
                 WHERE r.job_id=j.id) completion_tokens,
                (SELECT sum(r.duration_ms) FROM cr_results r
                 WHERE r.job_id=j.id AND r.completion_tokens IS NOT NULL
                   AND r.duration_ms>0) known_output_duration_ms,
                (SELECT sum(r.completion_tokens) FROM cr_results r
                 WHERE r.job_id=j.id AND r.duration_ms>0) known_output_tokens
                FROM cr_jobs j JOIN cr_sections s
                  ON s.slot=j.slot AND s.revision=j.revision
                LEFT JOIN cr_job_cancellations c ON c.job_id=j.id
                WHERE s.source_id=? ORDER BY j.created DESC,j.id DESC LIMIT 12""",
                (source_id,)).fetchall()
            rows=db.execute("""SELECT r.job_id,r.pass_number,r.notes,r.sha256,
                r.review_state,r.created,r.duration_ms,r.output_chars,
                r.prompt_tokens,r.completion_tokens,r.generation_tps,
                j.cycle,j.state,j.worker,j.passes,s.page,s.part,
                s.revision,s.source_revision,s.current
                FROM cr_results r JOIN cr_jobs j ON j.id=r.job_id
                JOIN cr_sections s ON s.slot=j.slot AND s.revision=j.revision
                WHERE s.source_id=? ORDER BY r.created DESC,r.job_id DESC,
                r.pass_number DESC LIMIT ? OFFSET ?""",
                (source_id,limit,offset)).fetchall()
            # All source/job events are private; only metadata, no PDF text or
            # untrusted model answers. SQL pages are bounded to 20 events.
            audit_scope="""FROM cr_events e WHERE e.subject=?
                OR EXISTS (SELECT 1 FROM cr_jobs j JOIN cr_sections s
                  ON s.slot=j.slot AND s.revision=j.revision
                  WHERE j.id=e.subject AND s.source_id=?)"""
            audit_total=db.execute("SELECT count(*) "+audit_scope,
                                  (source_id,source_id)).fetchone()[0]
            audit=db.execute("""SELECT e.id,e.happened,e.kind,e.subject,e.detail
                """+audit_scope+"""
                ORDER BY e.id DESC LIMIT 20 OFFSET ?""",
                (source_id,source_id,audit_offset)).fetchall()
        job_list=[dict(j) for j in jobs]
        for job in job_list:
            ms=job.pop("known_output_duration_ms")
            tokens=job.pop("known_output_tokens")
            job["observed_output_tps_inference_wall"]=round(
                tokens*1000/ms,2) if ms and tokens is not None else None
        return {"source":dict(source),"jobs_total":count,
                "pass_results_total":pass_total,"offset":offset,
                "limit":limit,"has_more":offset+len(rows)<pass_total,
                "jobs":job_list,
                "passes":[dict(r) for r in rows],
                "audit_total":audit_total,
                "audit_offset":audit_offset,
                "audit_has_more":audit_offset+len(audit)<audit_total,
                "audit_events":[dict(r) for r in audit],
                "draft_only":True,"weight_training":False}

    def sample_resources(self, *, now=None, resident=None,
                         available=None, size_bytes=None):
        """Persist one bounded operational sample per minute (not evidence).

        Only current job counts, self process RAM, host available RAM and
        private DB size are recorded. A rotating seven-day window keeps
        telemetry bounded; evidence, audit events, claims and drafts are NEVER
        pruned. No externally supplied SQL, filesystem paths or subprocesses.
        """
        now=time.time() if now is None else now
        if type(now) not in (int,float) or not 0<=now<2**40:
            raise ValueError("Invalid operational sample time")
        minute=int(now)//60*60
        if resident is None:
            resident=process_resident_mib()
        if available is None:
            available=free_memory_mib()
        if size_bytes is None:
            size_bytes=database_size_bytes(self.db_path)
        for field in (resident,available,size_bytes):
            if field is not None and (type(field) is not int or field<0):
                raise ValueError("Invalid resource sample")
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("""SELECT 1 FROM cr_resource_samples
                WHERE minute=?""",(minute,)).fetchone():
                return False
            db.execute("DELETE FROM cr_energy_attempts WHERE started<?",(now-7*86400,))
            counts={r["state"]:r["n"] for r in db.execute(
                "SELECT state,count(*) n FROM cr_jobs GROUP BY state")}
            db.execute("""INSERT INTO cr_resource_samples(
                minute,queued,leased,failed,process_rss_mib,
                host_free_ram_mib,database_bytes)
                VALUES(?,?,?,?,?,?,?)""",
                (minute,counts.get("queued",0),counts.get("leased",0),
                 counts.get("failed",0),
                 -1 if resident is None else resident,
                 -1 if available is None else available,
                 -1 if size_bytes is None else size_bytes))
            db.execute("""DELETE FROM cr_resource_samples
                WHERE minute<?""",(minute-7*86400,))
            return True

    def status(self):
        self.sample_resources()
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
            recent_jobs=db.execute("""SELECT j.id,j.cycle,j.state,
                j.pass_completed,j.passes,j.attempts,j.error,s.source_id,
                s.page,s.part,d.title,c.actor cancelled_by
                FROM cr_jobs j JOIN cr_sections s
                  ON s.slot=j.slot AND s.revision=j.revision
                JOIN cr_sources d ON d.id=s.source_id
                LEFT JOIN cr_job_cancellations c ON c.job_id=j.id
                WHERE j.state IN ('queued','leased','failed','stopped')
                ORDER BY CASE j.state WHEN 'leased' THEN 0 WHEN 'queued' THEN 1
                  ELSE 2 END,j.updated DESC,j.id DESC LIMIT 24""").fetchall()
            chat=db.execute("""SELECT count(*) FROM jobs WHERE state IN
                ('queued','running')""").fetchone()[0]
            if cycle:
                progress=dict(db.execute("""SELECT count(*) jobs,
                    COALESCE(sum(CASE WHEN state!='superseded' THEN passes
                        ELSE 0 END),0) planned_passes,
                    COALESCE(sum(CASE WHEN state!='superseded' THEN pass_completed
                        ELSE 0 END),0) finished_passes,
                    COALESCE(sum(CASE WHEN state='completed' THEN 1 ELSE 0 END),0)
                        completed_jobs,
                    COALESCE(sum(CASE WHEN state='failed' THEN 1 ELSE 0 END),0)
                        failed_jobs
                    FROM cr_jobs WHERE cycle=?""",(cycle["id"],)).fetchone())
            else:
                progress={"jobs":0,"planned_passes":0,"finished_passes":0,
                          "completed_jobs":0,"failed_jobs":0}
            measured=db.execute("""SELECT count(*) count,
                COALESCE(sum(duration_ms),0) total_ms,
                COALESCE(sum(output_chars),0) total_chars
                FROM cr_results WHERE duration_ms>0""").fetchone()
            tokens=db.execute("""SELECT
                count(prompt_tokens) prompt_samples,
                count(completion_tokens) completion_samples,
                sum(prompt_tokens) prompt_total,
                sum(completion_tokens) completion_total,
                sum(CASE WHEN completion_tokens IS NOT NULL AND duration_ms>0
                    THEN completion_tokens END) output_tokens,
                sum(CASE WHEN completion_tokens IS NOT NULL AND duration_ms>0
                    THEN duration_ms END) output_duration_ms
                FROM cr_results""").fetchone()
            completed_hour=db.execute("""SELECT count(*) FROM cr_results
                WHERE created>=?""",(time.time()-3600,)).fetchone()[0]
            report_time=time.time()
            usage=daily_energy(db,report_time)
            energy=self._energy_summary(db,report_time)
            trend=db.execute("""SELECT minute,queued,leased,failed,
                process_rss_mib,host_free_ram_mib,database_bytes
                FROM cr_resource_samples
                ORDER BY minute DESC LIMIT 60""").fetchall()
        planned=progress["planned_passes"]
        progress["pass_percent"]=round(100*progress["finished_passes"]/planned,1) if planned else 0.0
        sample=measured["count"]
        average_ms=round(measured["total_ms"]/sample,1) if sample else None
        throughput={"measured_passes":sample,
                    "average_inference_ms":average_ms,
                    "passes_last_60_minutes":completed_hour,
                    "estimated_passes_per_hour":round(3600000*sample/
                        measured["total_ms"],1) if measured["total_ms"] else None,
                    "measured_output_characters":measured["total_chars"],
                    "prompt_token_samples":tokens["prompt_samples"],
                    "completion_token_samples":tokens["completion_samples"],
                    "prompt_tokens":tokens["prompt_total"],
                    "completion_tokens":tokens["completion_total"],
                    "observed_output_tps_inference_wall":round(
                        1000*tokens["output_tokens"]/tokens["output_duration_ms"],2)
                        if tokens["output_duration_ms"] and tokens["output_tokens"] is not None
                        else None,
                    "estimation":"inference-time-only; excludes queue delays, source IO, GPU contention and retries"}
        free_mib=free_memory_mib()
        load=os.getloadavg()[0]
        gpu_devices=gpu_memory_snapshot()
        db_bytes=database_size_bytes(self.db_path)
        resident_mib=process_resident_mib()
        blocked=admission_reasons(config,free_mib=free_mib,load=load,now=report_time,energy_usage=usage,
                                  gpu_devices=gpu_devices,database_bytes=db_bytes)
        if chat>config["max_chat_jobs"]:
            blocked.append("interactive-queue")
        history=[dict(r) for r in reversed(trend)]
        if history:
            queue_delta=history[-1]["queued"]-history[0]["queued"]
        else:
            queue_delta=0
        return {"config":config,"jobs":{r["state"]:r["n"] for r in rows},
                "inventory":dict(source),"sections":dict(sections),
                "cycle":dict(cycle) if cycle else None,
                "cycle_progress":progress,"throughput":throughput,
                "resource_history":history,"energy_7d":energy,
                "energy_today":{"estimated_wh":usage["watt_ms"]/3600000,
                                "attempts":usage["attempts"],"estimated":usage["estimated"]},
                "queued_growth_last_samples":queue_delta,
                "active":dict(in_flight) if in_flight else None,
                "interactive_jobs":chat,"cpu_load_1m":load,
                "free_ram_mib":free_mib,"process_rss_mib":resident_mib,
                "cpu_logical":os.cpu_count(),
                "database_bytes":db_bytes,"gpu_devices":gpu_devices,
                "admission_blocks":blocked,
                "sources":[dict(r) for r in recent],
                "recent_jobs":[dict(r) for r in recent_jobs],
                "draft_only":True,"weight_training":False}
