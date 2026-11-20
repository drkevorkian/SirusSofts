#!/usr/bin/env python3
"""SirusSofts AI: persistent inbound task queue and GPU-preferred local router.

Only listens on Gentoo loopback. Ubuntu's AI input SSH tunnel is the caller.
Uses SQLite WAL for durable tasks and outbound events. No external modules.
"""
import argparse
from contextlib import contextmanager
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

CPU = 'http://127.0.0.1:18482'
NVIDIA = 'http://127.0.0.1:18486'
AMD = 'http://127.0.0.1:18487'
OUT = 'http://127.0.0.1:18084/internal/push'
MAX_BYTES = 128 * 1024
MAX_TEXT = 16000  # Existing input message validation; not an output limit.
OUTPUT_CHUNK_CHARS = 8192  # Bounded transfer packets, unlimited count per answer.
MAX_CONTEXT_MESSAGES = 20
STALE_JOB_LIMIT = 30
VALID_ID = re.compile(r'^[a-f0-9]{32}$')
MODEL_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.:+/-]{0,127}$')
DEFAULT_MODEL = 'sirussofts-coder'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

sys.path.insert(0,str(Path(__file__).resolve().parent))
from knowledge import KnowledgeManager,PROFILES
from continuous_research import ContinuousResearch, init_schema as init_continuous_schema
from research_evidence import EvidenceLedger, init_schema as init_evidence_schema
from research_admin_auth import verify as verify_research_admin, init_schema as init_admin_auth_schema
from research_loop import (validated_spec, read_source, pass_messages,
                           list_local_manuals, pdf_segments)
from manual_rag import (augment as augment_manuals, sync as sync_manuals,
                        repair_false_no_access, include_source_manifest,
                        RetrievalUnavailable)


@contextmanager
def db_connect(path):
    con = sqlite3.connect(path, timeout=15, isolation_level=None)
    try:
        con.execute('PRAGMA busy_timeout=15000')
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA synchronous=FULL')
        with con:
            yield con
    finally:
        con.close()


def init_db(path):
    with db_connect(path) as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, messages TEXT NOT NULL, target TEXT NOT NULL,
          state TEXT NOT NULL, created REAL NOT NULL, worker TEXT DEFAULT '',
           profile TEXT NOT NULL DEFAULT 'auto', model_id TEXT NOT NULL DEFAULT 'sirussofts-coder',
          skip_knowledge INTEGER NOT NULL DEFAULT 0, max_tokens INTEGER
        );
        CREATE TABLE IF NOT EXISTS outbox (
          job_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
          content TEXT NOT NULL, worker TEXT NOT NULL DEFAULT '',
          acked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job_id,seq)
        );
        CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(acked,job_id,seq);
        CREATE TABLE IF NOT EXISTS research_jobs (
          id TEXT PRIMARY KEY, spec TEXT NOT NULL, target TEXT NOT NULL,
          model_id TEXT NOT NULL, state TEXT NOT NULL
            CHECK(state IN ('queued','running','completed','error','canceled')),
          source_url TEXT NOT NULL DEFAULT '',
          source_sha256 TEXT NOT NULL DEFAULT '',
          source_text TEXT NOT NULL DEFAULT '',
          last_notes TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '',
          pass_completed INTEGER NOT NULL DEFAULT 0,
          section_completed INTEGER NOT NULL DEFAULT 0,
          sections_per_pass INTEGER NOT NULL DEFAULT 0,
          source_updated_at INTEGER NOT NULL DEFAULT 0,
          source_pages TEXT NOT NULL DEFAULT '',
          cancel_requested INTEGER NOT NULL DEFAULT 0,
          worker TEXT NOT NULL DEFAULT '',
          created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS research_jobs_queue
          ON research_jobs(state,created);
        CREATE TABLE IF NOT EXISTS research_passes (
          job_id TEXT NOT NULL REFERENCES research_jobs(id),
          pass_number INTEGER NOT NULL, notes TEXT NOT NULL,
          sha256 TEXT NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(job_id,pass_number)
        );
        CREATE TABLE IF NOT EXISTS research_sections (
          job_id TEXT NOT NULL REFERENCES research_jobs(id),
          pass_number INTEGER NOT NULL,
          section_number INTEGER NOT NULL,
          pages TEXT NOT NULL DEFAULT '',
          notes TEXT NOT NULL,
          sha256 TEXT NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(job_id,pass_number,section_number)
        );
        ''')
        if 'profile' not in {col[1] for col in con.execute('PRAGMA table_info(jobs)')}:
            con.execute("ALTER TABLE jobs ADD COLUMN profile TEXT NOT NULL DEFAULT 'auto'")
        if 'model_id' not in {col[1] for col in con.execute('PRAGMA table_info(jobs)')}:
            con.execute("ALTER TABLE jobs ADD COLUMN model_id TEXT NOT NULL DEFAULT 'sirussofts-coder'")
        cols={col[1] for col in con.execute('PRAGMA table_info(jobs)')}
        if 'skip_knowledge' not in cols:
            con.execute('ALTER TABLE jobs ADD COLUMN skip_knowledge INTEGER NOT NULL DEFAULT 0')
        if 'max_tokens' not in cols:
            con.execute('ALTER TABLE jobs ADD COLUMN max_tokens INTEGER')
        # If Gentoo reboots mid-inference, retry the task from the beginning.
        con.execute("UPDATE jobs SET state='queued' WHERE state='running'")
        # Resume interrupted multi-pass analysis from the last committed pass.
        con.execute("UPDATE research_jobs SET state='queued' WHERE state='running'")
    init_continuous_schema(path)
    init_evidence_schema(path)
    init_admin_auth_schema(path)


def assert_local_worker_url(url):
    parts = urlsplit(url)
    if (parts.scheme != 'http' or parts.hostname != '127.0.0.1'
            or parts.username or parts.password or parts.path or parts.query or not parts.port):
        raise ValueError('Only explicit IPv4 localhost inference workers are allowed')


def local_open(url, *, data=None, timeout=3):
    parts = urlsplit(url)
    if parts.scheme != 'http' or parts.hostname != '127.0.0.1':
        raise ValueError('External inference endpoints are forbidden')
    req = urllib.request.Request(url, data=data,
        headers={'Content-Type':'application/json'} if data is not None else {},
        method='POST' if data is not None else 'GET')
    return LOCAL_OPENER.open(req, timeout=timeout)


def local_model_ids(url, timeout=1.5):
    assert_local_worker_url(url)
    try:
        with local_open(url + '/v1/models', timeout=timeout) as response:
            raw=response.read(65537)
        if len(raw)>65536: return []
        parsed=json.loads(raw)
        rows=parsed.get('data') if isinstance(parsed,dict) else None
        if not isinstance(rows,list): return []
        ids=[item.get('id') for item in rows[:16] if isinstance(item,dict)]
        return sorted({mid for mid in ids if isinstance(mid,str) and MODEL_ID.fullmatch(mid)})
    except (OSError, ValueError, RuntimeError, TimeoutError, urllib.error.URLError):
        return []


def worker_healthy(url, timeout=0.7):
    try:
        with local_open(url + '/health', timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def choose_worker(job_id, target, gpu_percent=90, probe=worker_healthy, model_id=None):
    workers = {'nvidia': NVIDIA, 'amd': AMD, 'cpu': CPU}
    if target not in ('auto', *workers):
        raise ValueError('Unrecognized compute worker')
    for endpoint in workers.values(): assert_local_worker_url(endpoint)
    if model_id is not None:
        if not isinstance(model_id,str) or not MODEL_ID.fullmatch(model_id):
            raise ValueError('Invalid local model ID')
        original_probe=probe
        probe=lambda endpoint: original_probe(endpoint) and model_id in local_model_ids(endpoint)
    if target != 'auto':
        if not probe(workers[target]):
            raise RuntimeError(target.upper() + ' worker is unavailable')
        return target, workers[target]
    gpu = [w for w in ('nvidia', 'amd') if probe(workers[w])]
    dice = int.from_bytes(hashlib.sha256(job_id.encode('ascii')).digest()[:4], 'big') % 100
    if gpu and (dice < gpu_percent or not probe(CPU)):
        index = int(hashlib.sha256((job_id + 'gpu').encode()).hexdigest(), 16) % (3 if len(gpu) == 2 else 1)
        chosen = 'nvidia' if len(gpu) == 2 and index < 2 else ('amd' if len(gpu) == 2 else gpu[0])
        return chosen, workers[chosen]
    if probe(CPU):
        return 'cpu', CPU
    if gpu:
        return gpu[0], workers[gpu[0]]
    raise RuntimeError('All inference workers are offline')


def add_event(con, job_id, seq, kind, content='', worker=''):
    con.execute('INSERT OR IGNORE INTO outbox(job_id,seq,type,content,worker) VALUES(?,?,?,?,?)',
                (job_id, seq, kind, content, worker))


def add_completed_events(con, job_id, answer, worker):
    """Durable, ordered output fragments for arbitrarily long model answers.

    Preserve the v1 one-event completion for short responses, while long
    responses use sequential chunk events and a final completed sentinel.
    The output gateway must support chunks before this agent is deployed.
    """
    if len(answer) <= OUTPUT_CHUNK_CHARS:
        add_event(con, job_id, 3, 'completed', answer, worker)
        return
    seq = 3
    for offset in range(0, len(answer), OUTPUT_CHUNK_CHARS):
        add_event(con, job_id, seq, 'chunk',
                  answer[offset:offset + OUTPUT_CHUNK_CHARS], worker)
        seq += 1
    add_event(con, job_id, seq, 'completed', '', worker)


def chat(url, messages, timeout=None, model_id=DEFAULT_MODEL, max_tokens=None):
    # Context capacity is chosen by the model server, not fixed to 768 tokens here.
    # Long generations need a configurable timeout; this is not an output cap.
    if timeout is None:
        timeout = int(os.environ.get('SIRUS_AI_REQUEST_TIMEOUT', '1800'))
    if timeout <= 0:
        raise ValueError('SIRUS_AI_REQUEST_TIMEOUT must be positive')
    assert_local_worker_url(url)
    if not isinstance(model_id,str) or not MODEL_ID.fullmatch(model_id):
        raise ValueError('Invalid local model ID')
    if model_id not in local_model_ids(url):
        raise RuntimeError('Selected local model is not loaded on worker')
    request={'model': model_id, 'messages': messages,
             'temperature': 0.55, 'stream': False}
    if max_tokens is not None:
        if type(max_tokens) is not int or not 64 <= max_tokens <= 8192:
            raise ValueError('Invalid output-token limit')
        request['max_tokens']=max_tokens
    payload=json.dumps(request).encode()
    with local_open(url + '/v1/chat/completions', data=payload, timeout=timeout) as r:
        data = json.load(r)
    return str(data['choices'][0]['message']['content'])


class Engine:
    def __init__(self, db, secret, gpu_percent=90, knowledge_file=None):
        self.db = db
        self.secret = Path(secret).read_text().strip()
        if len(self.secret) < 32:
            raise RuntimeError('Shared output authentication secret is missing')
        self.gpu_percent = gpu_percent
        self.stop = threading.Event()
        self.knowledge = KnowledgeManager(knowledge_file) if knowledge_file else None
        self.continuous = ContinuousResearch(self.db)
        self.evidence = EvidenceLedger(self.continuous)

    def enqueue(self, messages, target, profile='auto', model_id=DEFAULT_MODEL, skip_knowledge=False, max_tokens=None):
        if not isinstance(model_id,str) or not MODEL_ID.fullmatch(model_id):
            raise ValueError('Invalid local model ID')
        if type(skip_knowledge) is not bool:
            raise ValueError('Invalid local knowledge policy')
        if max_tokens is not None and (type(max_tokens) is not int or not 64 <= max_tokens <= 8192):
            raise ValueError('Invalid token budget')
        job_id = uuid.uuid4().hex
        with db_connect(self.db) as con:
            queue_size = con.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
            if queue_size >= STALE_JOB_LIMIT:
                raise RuntimeError('Inference queue full; try again after current tasks finish')
            con.execute('INSERT INTO jobs(id,messages,target,state,created,worker,profile,model_id,skip_knowledge,max_tokens) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (job_id,json.dumps(messages),target,'queued',time.time(),'',profile,model_id,int(skip_knowledge),max_tokens))
            add_event(con, job_id, 1, 'queued')
        return job_id

    def enqueue_research(self, raw, target, model_id):
        spec=validated_spec(raw)
        if target not in ('auto','cpu','nvidia','amd'):
            raise ValueError('Invalid research worker')
        if not isinstance(model_id,str) or not MODEL_ID.fullmatch(model_id):
            raise ValueError('Invalid research model ID')
        job_id=uuid.uuid4().hex
        with db_connect(self.db) as con:
            con.execute('BEGIN IMMEDIATE')
            busy=con.execute("SELECT count(*) FROM research_jobs WHERE state IN ('queued','running')").fetchone()[0]
            if busy:
                raise RuntimeError('Another research job is queued or running; finish or cancel it first.')
            now=time.time()
            con.execute("""INSERT INTO research_jobs
                (id,spec,target,model_id,state,created,updated)
                VALUES(?,?,?,?,'queued',?,?)""",
                (job_id,json.dumps(spec),target,model_id,now,now))
        return job_id

    def research_status(self, job_id):
        if not VALID_ID.fullmatch(job_id):
            raise ValueError('Invalid research job ID')
        with db_connect(self.db) as con:
            row=con.execute("""SELECT spec,state,source_url,source_sha256,
                pass_completed,last_notes,error,cancel_requested,worker,created,updated,
                section_completed,sections_per_pass,source_pages
                FROM research_jobs WHERE id=?""",(job_id,)).fetchone()
            if row is None:
                raise ValueError('Research job not found')
        spec=json.loads(row[0])
        return {'id':job_id,'title':spec['title'],'source_type':spec['source_type'],
                'source_url':row[2] or spec['url'],'source_sha256':row[3],
                'focus':spec['focus'],'passes':spec['passes'],'completed':row[4],
                'state':row[1],'latest_notes':row[5], 'error':row[6],
                'cancel_requested':bool(row[7]),'worker':row[8],
                'created':row[9],'updated':row[10],'reviewed':False,
                'weight_training':False,'section_completed':row[11],
                'sections_per_pass':row[12],'source_pages':row[13],
                'pdf_sha256':spec['pdf_sha256'],'page_start':spec['page_start'],
                'page_end':spec['page_end']}

    def research_sections(self, job_id, offset=0):
        if not isinstance(job_id,str) or not VALID_ID.fullmatch(job_id):
            raise ValueError('Invalid research job ID')
        if type(offset) is not int or not 0 <= offset <= 999999:
            raise ValueError('Invalid research section offset')
        with db_connect(self.db) as con:
            if con.execute('SELECT 1 FROM research_jobs WHERE id=?',(job_id,)).fetchone() is None:
                raise ValueError('Research job not found')
            rows=con.execute("""SELECT pass_number,section_number,pages,notes,sha256,created
                FROM research_sections WHERE job_id=?
                ORDER BY pass_number DESC,section_number DESC LIMIT 13 OFFSET ?""",
                (job_id,offset)).fetchall()
        results=[]
        for pass_no,section_no,pages,notes,sha,created in rows[:12]:
            results.append({'pass':pass_no,'section':section_no,
                            'pages':json.loads(pages),'notes':notes,
                            'sha256':sha,'created':created})
        return {'id':job_id,'offset':offset,'sections':results,'has_more':len(rows)>12}

    def cancel_research(self, job_id):
        if not VALID_ID.fullmatch(job_id):
            raise ValueError('Invalid research job ID')
        with db_connect(self.db) as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT state FROM research_jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:
                raise ValueError('Research job not found')
            if row[0] in ('queued','running'):
                new_state='canceled' if row[0]=='queued' else 'running'
                con.execute('UPDATE research_jobs SET cancel_requested=1,state=?,updated=? WHERE id=?',
                            (new_state,time.time(),job_id))
        return self.research_status(job_id)

    def run_research_one(self):
        with db_connect(self.db) as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute("""SELECT id,spec,target,model_id,source_url,source_sha256,
                source_text,last_notes,pass_completed,section_completed,sections_per_pass,
                source_updated_at FROM research_jobs
                WHERE state='queued' ORDER BY created LIMIT 1""").fetchone()
            if row is None:
                return False
            # Explicit research takes precedence over autonomous work. Fenced
            # background leases prevent two research engines sharing one model.
            if con.execute("SELECT 1 FROM cr_jobs WHERE state='leased' LIMIT 1").fetchone():
                return False
            (job_id,raw,target,model_id,src_url,src_sha,src_text,notes,
             completed,section_completed,sections_per_pass,source_stamp)=row
            con.execute("UPDATE research_jobs SET state='running',updated=? WHERE id=?",
                        (time.time(),job_id))
        try:
            spec=json.loads(raw)
            if spec['source_type']=='pdf':
                # No raw PDF upload or original binary access. Use the existing
                # Gentoo extracted text database, read-only and page-attributed.
                meta,segments=pdf_segments(spec)
                if source_stamp and meta['updated_at']!=source_stamp:
                    raise RuntimeError('PDF source changed during analysis. Rerun to use the new revision.')
                if sections_per_pass and sections_per_pass!=len(segments):
                    raise RuntimeError('PDF extraction changed its section count. Start a new run.')
                src_sha=meta['sha256']
                src_url='manual:'+src_sha
                src_text=''
                source_stamp=meta['updated_at']
                pages=str(meta['page_start'])+'-'+str(meta['page_end'])
                with db_connect(self.db) as con:
                    con.execute("""UPDATE research_jobs SET source_url=?,source_sha256=?,
                        sections_per_pass=?,source_updated_at=?,source_pages=?,updated=?
                        WHERE id=?""",(src_url,src_sha,len(segments),source_stamp,pages,
                                       time.time(),job_id))
            else:
                if not src_text:
                    src_url,src_text,src_sha=read_source(spec)
                    with db_connect(self.db) as con:
                        con.execute("""UPDATE research_jobs SET source_url=?,source_text=?,
                            source_sha256=?,sections_per_pass=1,updated=? WHERE id=?""",
                            (src_url,src_text,src_sha,time.time(),job_id))
                segments=[{'text':src_text,'pages':[]}]
                if sections_per_pass and sections_per_pass!=1:
                    raise RuntimeError('Unexpected research checkpoint section count.')
            worker,endpoint=choose_worker(job_id,target,self.gpu_percent,model_id=model_id)
            with db_connect(self.db) as con:
                con.execute('UPDATE research_jobs SET worker=?,updated=? WHERE id=?',
                            (worker,time.time(),job_id))
            for pass_no in range(completed+1,spec['passes']+1):
                start_section=section_completed if pass_no==completed+1 else 0
                for section_no in range(start_section+1,len(segments)+1):
                    with db_connect(self.db) as con:
                        cancel=con.execute('SELECT cancel_requested FROM research_jobs WHERE id=?',
                                           (job_id,)).fetchone()[0]
                        if cancel:
                            con.execute("UPDATE research_jobs SET state='canceled',updated=? WHERE id=?",
                                        (time.time(),job_id))
                            return True
                    segment=segments[section_no-1]
                    messages=pass_messages(spec,segment['text'],src_sha,pass_no,notes)
                    if spec['source_type']=='pdf':
                        messages[-1]['content']+=(
                            "\\nPDF pages in this section: "+",".join(map(str,segment['pages']))+
                            "\\nSection "+str(section_no)+" of "+str(len(segments))+
                            ". Cite the actual PDF page labels. This section is not the entire PDF.")
                    answer=chat(endpoint,messages,model_id=model_id,max_tokens=2048)
                    if not isinstance(answer,str) or not answer.strip() or len(answer)>24000:
                        raise RuntimeError('Local model returned invalid or oversized notes')
                    notes=answer.strip()
                    now=time.time()
                    with db_connect(self.db) as con:
                        con.execute('BEGIN IMMEDIATE')
                        con.execute("""INSERT OR IGNORE INTO research_sections
                            (job_id,pass_number,section_number,pages,notes,sha256,created)
                            VALUES(?,?,?,?,?,?,?)""",
                            (job_id,pass_no,section_no,
                             json.dumps(segment['pages']),notes,
                             hashlib.sha256(notes.encode()).hexdigest(),now))
                        con.execute("""UPDATE research_jobs
                            SET section_completed=?,last_notes=?,updated=?
                            WHERE id=?""",(section_no,notes,now,job_id))
                now=time.time()
                with db_connect(self.db) as con:
                    con.execute('BEGIN IMMEDIATE')
                    con.execute("""INSERT OR IGNORE INTO research_passes
                        (job_id,pass_number,notes,sha256,created) VALUES(?,?,?,?,?)""",
                        (job_id,pass_no,notes,hashlib.sha256(notes.encode()).hexdigest(),now))
                    con.execute("""UPDATE research_jobs
                        SET pass_completed=?,section_completed=0,last_notes=?,updated=?
                        WHERE id=?""",(pass_no,notes,now,job_id))
                section_completed=0
            with db_connect(self.db) as con:
                con.execute("UPDATE research_jobs SET state='completed',updated=? WHERE id=?",
                            (time.time(),job_id))
        except Exception as exc:
            with db_connect(self.db) as con:
                con.execute("UPDATE research_jobs SET state='error',error=?,updated=? WHERE id=?",
                            (str(exc)[:350],time.time(),job_id))
        return True

    def continuous_inventory_loop(self):
        while not self.stop.is_set():
            try:
                refreshed=self.continuous.scan_if_due()
                if refreshed:
                    self.evidence.reconcile()
                with db_connect(self.db) as con:
                    enabled=con.execute("SELECT mode FROM cr_config WHERE id=1").fetchone()[0]=="running"
                    interactive=con.execute("""SELECT 1 FROM jobs WHERE state IN
                        ('queued','running') LIMIT 1""").fetchone() is not None
                if enabled and not interactive:
                    self.evidence.index_batch(32)
                    self.evidence.compare_batch(64)
            except (OSError,sqlite3.Error,RuntimeError,ValueError) as exc:
                # Never leak private PDF text or paths to shared service logs.
                print("Continuous inventory unavailable:",type(exc).__name__,file=sys.stderr)
            self.stop.wait(30)

    def continuous_loop(self):
        # A single, lower-priority private research worker. Disabled by default.
        while not self.stop.is_set():
            if not self.continuous.run_once(self._continuous_infer):
                self.stop.wait(2)

    def _continuous_infer(self, messages, target, model_id, job_id):
        worker, endpoint = choose_worker(job_id,target,self.gpu_percent,model_id=model_id)
        return chat(endpoint,messages,model_id=model_id,max_tokens=2048), worker

    def research_loop(self):
        while not self.stop.is_set():
            if not self.run_research_one():
                self.stop.wait(1)

    def run_one(self):
        with db_connect(self.db) as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT id,messages,target,profile,model_id,skip_knowledge,max_tokens FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                con.execute('COMMIT')
                return False
            job_id,raw_messages,target,profile,model_id,skip_knowledge,max_tokens = row
            con.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
            con.execute('COMMIT')
        worker = ''
        try:
            worker, url = choose_worker(job_id, target, self.gpu_percent, model_id=model_id)
            with db_connect(self.db) as con:
                con.execute('UPDATE jobs SET worker=? WHERE id=?', (worker, job_id))
                add_event(con, job_id, 2, 'started', '', worker)
            prompt_messages=json.loads(raw_messages)
            if self.knowledge and not skip_knowledge:
                prompt_messages=self.knowledge.augment_messages(prompt_messages,profile)
            try:
                answer = chat(url,prompt_messages,model_id=model_id,max_tokens=max_tokens)
            except (TimeoutError, OSError, urllib.error.URLError, ValueError, KeyError) as exc:
                if (target != 'auto' or worker == 'cpu' or not worker_healthy(CPU)
                        or model_id not in local_model_ids(CPU)):
                    raise RuntimeError('Inference request failed on ' + worker) from exc
                # GPU died: CPU fallback is only automatic for auto-routed requests.
                worker = 'cpu'
                with db_connect(self.db) as con:
                    con.execute('UPDATE jobs SET worker=? WHERE id=?', (worker, job_id))
                answer = chat(CPU,prompt_messages,model_id=model_id,max_tokens=max_tokens)
            # The retrieved source list is independently verified. A weak local
            # model must not falsely deny all manual access when those excerpts
            # were included. Do not manufacture missing repair steps.
            answer = repair_false_no_access(answer, prompt_messages)
            answer = include_source_manifest(answer, prompt_messages)
            with db_connect(self.db) as con:
                con.execute("UPDATE jobs SET state='completed' WHERE id=?", (job_id,))
                add_completed_events(con, job_id, answer, worker)
        except Exception as exc:
            with db_connect(self.db) as con:
                con.execute("UPDATE jobs SET state='error' WHERE id=?", (job_id,))
                add_event(con, job_id, 3, 'error', str(exc)[:500], worker)
        return True

    def worker_loop(self):
        while not self.stop.is_set():
            if not self.run_one():
                self.stop.wait(0.7)

    def dispatch_loop(self):
        while not self.stop.is_set():
            with db_connect(self.db) as con:
                row = con.execute('SELECT job_id,seq,type,content,worker FROM outbox WHERE acked=0 ORDER BY rowid LIMIT 1').fetchone()
            if row:
                job_id, seq, kind, content, worker = row
                data = json.dumps({'job_id': job_id, 'seq': seq, 'type': kind,
                                   'content': content, 'worker': worker}).encode()
                req = urllib.request.Request(OUT, data=data, method='POST', headers={
                    'Content-Type': 'application/json', 'X-Sirus-AI-Token': self.secret})
                try:
                    with urllib.request.urlopen(req, timeout=4) as r:
                        if r.status == 200:
                            with db_connect(self.db) as con:
                                con.execute('UPDATE outbox SET acked=1 WHERE job_id=? AND seq=?', (job_id, seq))
                            continue
                except (OSError, TimeoutError, urllib.error.URLError):
                    pass  # durable outbox retries after the OUTPUT tunnel reconnects
            self.stop.wait(2 if row else 1)


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'SirusAI-Input/1.0'

    def log_message(self, fmt, *args):
        pass

    def respond(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ('/healthz', '/api/in/health'):
            workers={'cpu':CPU,'nvidia':NVIDIA,'amd':AMD}
            healthy={key:worker_healthy(url) for key,url in workers.items()}
            models={key:local_model_ids(url) if healthy[key] else [] for key,url in workers.items()}
            self.respond(200, {'status':'ok', 'channel':'input', 'workers':healthy,
                'models':models, 'local_only':True, 'inference_host':'Gentoo localhost llama.cpp',
                'gpu_task_target_percent':self.server.engine.gpu_percent})
        elif self.path == '/api/in/research/continuous':
            if not verify_research_admin(self.headers,self.path,b"",self.server.engine.db):
                return self.respond(403,{'error':'Administrative bridge authorization required'})
            self.respond(200,self.server.engine.continuous.status())
        elif self.path == '/api/in/research/evidence':
            if not verify_research_admin(self.headers,self.path,b"",self.server.engine.db):
                return self.respond(403,{'error':'Administrative bridge authorization required'})
            self.respond(200,self.server.engine.evidence.status())
        elif self.path.startswith('/api/in/research/manuals'):
            from urllib.parse import parse_qs
            parsed=urlsplit(self.path)
            if parsed.path!='/api/in/research/manuals':
                return self.respond(404,{'error':'Not found'})
            try:
                q=parse_qs(parsed.query).get('q',[''])[0]
                self.respond(200,{'manuals':list_local_manuals(q)})
            except (ValueError,RetrievalUnavailable,sqlite3.Error) as exc:
                self.respond(400,{'error':str(exc)[:180]})
        elif re.fullmatch(r'/api/in/research/[a-f0-9]{32}/sections/[0-9]{1,6}',self.path):
            try:
                pieces=self.path.split('/')
                self.respond(200,self.server.engine.research_sections(pieces[4],int(pieces[6])))
            except ValueError as exc:
                self.respond(404,{'error':str(exc)[:150]})
        elif re.fullmatch(r'/api/in/research/[a-f0-9]{32}',self.path):
            try:
                self.respond(200,self.server.engine.research_status(self.path.rsplit('/',1)[-1]))
            except ValueError as exc:
                self.respond(404,{'error':str(exc)[:150]})
        else:
            self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        research_create=self.path=='/api/in/research'
        continuous_control=self.path=='/api/in/research/continuous/control'
        continuous_history=self.path=='/api/in/research/continuous/history'
        evidence_control=self.path=='/api/in/research/evidence/control'
        research_cancel=re.fullmatch(r'/api/in/research/[a-f0-9]{32}/cancel',self.path)
        if self.path!='/api/in/jobs' and not research_create and not research_cancel and not continuous_control and not continuous_history and not evidence_control:
            return self.respond(404, {'error': 'Not found'})
        origin = self.headers.get('Origin')
        if origin and origin != 'https://sirussofts.com':
            return self.respond(403, {'error': 'Origin rejected'})
        if self.headers.get('Sec-Fetch-Site', '') == 'cross-site':
            return self.respond(403, {'error': 'Cross-site request rejected'})
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            return self.respond(415, {'error': 'Expected JSON'})
        try:
            n = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.respond(400, {'error': 'Invalid request length'})
        if not 0 < n <= MAX_BYTES:
            return self.respond(413, {'error': 'Request too large'})
        try:
            raw_request=self.rfile.read(n)
            if continuous_control or continuous_history or evidence_control:
                if not verify_research_admin(self.headers,self.path,raw_request,
                                             self.server.engine.db):
                    self.respond(403,{'error':'Administrative bridge authorization required'})
                    return
            data = json.loads(raw_request)
            if not isinstance(data,dict):
                raise ValueError('Expected JSON object')
            if continuous_history:
                if data.get('local_only') is not True:
                    raise ValueError('Local-only research history required')
                self.respond(200,self.server.engine.continuous.history(
                    data.get('source_id'),data.get('offset',0)))
                return
            if evidence_control:
                if data.get('local_only') is not True:
                    raise ValueError('Local-only evidence administration required')
                action=data.get('action')
                ledger=self.server.engine.evidence
                if action=='index':
                    response=ledger.index_batch(32)
                elif action=='scope':
                    response=ledger.set_scope(data.get('source_id'),
                                              data.get('applicability'))
                elif action=='review':
                    response=ledger.review(data.get('id'),data.get('quote_sha'),
                                           data.get('revision'),data.get('reviewer'),
                                           data.get('review_action'),data.get('reason',''))
                elif action=='dismiss':
                    response=ledger.dismiss_conflict(data.get('id'),
                                                     data.get('reviewer'))
                else:
                    raise ValueError('Unsupported evidence operation')
                self.respond(200,response)
                return
            if continuous_control:
                if data.get('local_only') is not True:
                    raise ValueError('Local-only administration required')
                self.respond(200,self.server.engine.continuous.control(
                    data.get('action'),source_id=data.get('source_id',''),
                    value=data.get('value')))
                return
            if research_cancel:
                self.respond(200,self.server.engine.cancel_research(self.path.split('/')[4]))
                return
            if research_create:
                if data.get('local_only') is not True:
                    raise ValueError('Local-only research required')
                job_id=self.server.engine.enqueue_research(
                    data.get('source'),data.get('target','auto'),
                    data.get('model_id',DEFAULT_MODEL))
                self.respond(202,{'job_id':job_id,'status':'queued',
                                  'local_only':True,'weight_training':False})
                return
            messages, target = data['messages'], data.get('target', 'auto')
            profile=data.get('profile','auto')
            model_id=data.get('model_id', DEFAULT_MODEL)
            skip_knowledge=data.get('skip_knowledge',False)
            max_tokens=data.get('max_tokens')
            if type(skip_knowledge) is not bool or (max_tokens is not None and
                (type(max_tokens) is not int or not 64 <= max_tokens <= 8192)):
                raise ValueError('Invalid token cap or knowledge policy')
            if data.get('local_only',True) is not True:
                raise ValueError('External LLM services are forbidden')
            if not isinstance(model_id,str) or not MODEL_ID.fullmatch(model_id):
                raise ValueError('Invalid or unknown local model ID')
            if profile not in PROFILES:raise ValueError('Invalid AI specialization')
            if target not in ('auto', 'cpu', 'nvidia', 'amd'):
                raise ValueError('Unrecognized worker')
            if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_CONTEXT_MESSAGES:
                raise ValueError('Supply 1 to 20 messages')
            if not all(isinstance(x, dict) and x.get('role') in ('user', 'assistant') and
                       isinstance(x.get('content'), str) and 0 < len(x['content']) <= MAX_TEXT
                       for x in messages):
                raise ValueError('Invalid message format')
            if messages[-1]['role'] != 'user':
                raise ValueError('Last message must be from the user')
            use_manuals=data.get('use_manuals',False)
            if type(use_manuals) is not bool:
                raise ValueError('Invalid manual retrieval policy')
            if use_manuals:
                question=data.get('manual_query',messages[-1]['content'])
                if not isinstance(question,str) or not 0<len(question)<=MAX_TEXT:
                    raise ValueError('Invalid manual search query')
                # These are source-backed system references, not user messages;
                # keep the original chat and selected model unchanged.
                messages=augment_manuals(messages,question)
            job_id = self.server.engine.enqueue(messages,target,profile,model_id,skip_knowledge,max_tokens)
            self.respond(202, {'job_id':job_id, 'status':'queued', 'model_id':model_id, 'local_only':True, 'output':'separate-output-tunnel'})
        except (ValueError, KeyError, TypeError) as e:
            self.respond(400, {'error': str(e)[:150]})
        except RuntimeError as e:
            self.respond(503, {'error': str(e)[:150]})


def manual_index_loop(stop):
    # Never write to the ingest database; the private FTS sidecar is derived
    # and can be recreated. Newly approved sources appear after next sync.
    while not stop.is_set():
        try:
            sync_manuals(batch_limit=20)
        except (OSError, sqlite3.Error, RetrievalUnavailable, ValueError) as error:
            print('Manual index sync unavailable:',type(error).__name__,file=sys.stderr,flush=True)
        stop.wait(60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=18082)
    ap.add_argument('--db', default='/var/lib/sirus-ai-dual/input.sqlite3')
    ap.add_argument('--secret', default='/etc/sirus-ai-dual/secret')
    ap.add_argument('--gpu-percent', type=int, default=90)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--knowledge-file',default=os.environ.get('SIRUS_AI_KNOWLEDGE_FILE',''),
                    help='Optional local reviewed JSONL knowledge file')
    args = ap.parse_args()
    if not (0 <= args.gpu_percent <= 100 and 1 <= args.workers <= 4):
        ap.error('Invalid GPU percentage or worker count')
    init_db(args.db)
    engine = Engine(args.db,args.secret,args.gpu_percent,args.knowledge_file)
    for _ in range(args.workers):
        threading.Thread(target=engine.worker_loop, daemon=True).start()
    threading.Thread(target=engine.dispatch_loop, daemon=True).start()
    threading.Thread(target=engine.research_loop, daemon=True).start()
    threading.Thread(target=engine.continuous_loop, daemon=True).start()
    threading.Thread(target=engine.continuous_inventory_loop, daemon=True).start()
    threading.Thread(target=manual_index_loop,args=(engine.stop,),daemon=True).start()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.engine = engine
    print('Sirus AI INPUT listening on 127.0.0.1:%s' % args.port, flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        engine.stop.set()


if __name__ == '__main__':
    main()
