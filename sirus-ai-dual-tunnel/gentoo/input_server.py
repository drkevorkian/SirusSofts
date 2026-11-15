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
import sqlite3
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
          state TEXT NOT NULL, created REAL NOT NULL, worker TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS outbox (
          job_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
          content TEXT NOT NULL, worker TEXT NOT NULL DEFAULT '',
          acked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job_id,seq)
        );
        CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(acked,job_id,seq);
        ''')
        # If Gentoo reboots mid-inference, retry the task from the beginning.
        con.execute("UPDATE jobs SET state='queued' WHERE state='running'")


def worker_healthy(url, timeout=0.7):
    try:
        with urllib.request.urlopen(url + '/health', timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def choose_worker(job_id, target, gpu_percent=90, probe=worker_healthy):
    workers = {'nvidia': NVIDIA, 'amd': AMD, 'cpu': CPU}
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


def chat(url, messages, timeout=None):
    # Context capacity is chosen by the model server, not fixed to 768 tokens here.
    # Long generations need a configurable timeout; this is not an output cap.
    if timeout is None:
        timeout = int(os.environ.get('SIRUS_AI_REQUEST_TIMEOUT', '1800'))
    if timeout <= 0:
        raise ValueError('SIRUS_AI_REQUEST_TIMEOUT must be positive')
    payload = json.dumps({'model': 'sirussofts-coder', 'messages': messages,
                          'temperature': 0.55, 'stream': False}).encode()
    req = urllib.request.Request(url + '/v1/chat/completions', data=payload,
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return str(data['choices'][0]['message']['content'])


class Engine:
    def __init__(self, db, secret, gpu_percent=90):
        self.db = db
        self.secret = Path(secret).read_text().strip()
        if len(self.secret) < 32:
            raise RuntimeError('Shared output authentication secret is missing')
        self.gpu_percent = gpu_percent
        self.stop = threading.Event()

    def enqueue(self, messages, target):
        job_id = uuid.uuid4().hex
        with db_connect(self.db) as con:
            queue_size = con.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
            if queue_size >= STALE_JOB_LIMIT:
                raise RuntimeError('Inference queue full; try again after current tasks finish')
            con.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                        (job_id, json.dumps(messages), target, 'queued', time.time(), ''))
            add_event(con, job_id, 1, 'queued')
        return job_id

    def run_one(self):
        with db_connect(self.db) as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT id,messages,target FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                con.execute('COMMIT')
                return False
            job_id, raw_messages, target = row
            con.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
            con.execute('COMMIT')
        worker = ''
        try:
            worker, url = choose_worker(job_id, target, self.gpu_percent)
            with db_connect(self.db) as con:
                con.execute('UPDATE jobs SET worker=? WHERE id=?', (worker, job_id))
                add_event(con, job_id, 2, 'started', '', worker)
            try:
                answer = chat(url, json.loads(raw_messages))
            except (TimeoutError, OSError, urllib.error.URLError, ValueError, KeyError) as exc:
                if target != 'auto' or worker == 'cpu' or not worker_healthy(CPU):
                    raise RuntimeError('Inference request failed on ' + worker) from exc
                # GPU died: CPU fallback is only automatic for auto-routed requests.
                worker = 'cpu'
                with db_connect(self.db) as con:
                    con.execute('UPDATE jobs SET worker=? WHERE id=?', (worker, job_id))
                answer = chat(CPU, json.loads(raw_messages))
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
            self.respond(200, {'status': 'ok', 'channel': 'input', 'workers': {
                'cpu': worker_healthy(CPU), 'nvidia': worker_healthy(NVIDIA), 'amd': worker_healthy(AMD)},
                'gpu_task_target_percent': self.server.engine.gpu_percent})
        else:
            self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        if self.path != '/api/in/jobs':
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
            data = json.loads(self.rfile.read(n))
            messages, target = data['messages'], data.get('target', 'auto')
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
            job_id = self.server.engine.enqueue(messages, target)
            self.respond(202, {'job_id': job_id, 'status': 'queued', 'output': 'separate-output-tunnel'})
        except (ValueError, KeyError, TypeError) as e:
            self.respond(400, {'error': str(e)[:150]})
        except RuntimeError as e:
            self.respond(503, {'error': str(e)[:150]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=18082)
    ap.add_argument('--db', default='/var/lib/sirus-ai-dual/input.sqlite3')
    ap.add_argument('--secret', default='/etc/sirus-ai-dual/secret')
    ap.add_argument('--gpu-percent', type=int, default=90)
    ap.add_argument('--workers', type=int, default=2)
    args = ap.parse_args()
    if not (0 <= args.gpu_percent <= 100 and 1 <= args.workers <= 4):
        ap.error('Invalid GPU percentage or worker count')
    init_db(args.db)
    engine = Engine(args.db, args.secret, args.gpu_percent)
    for _ in range(args.workers):
        threading.Thread(target=engine.worker_loop, daemon=True).start()
    threading.Thread(target=engine.dispatch_loop, daemon=True).start()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.engine = engine
    print('Sirus AI INPUT listening on 127.0.0.1:%s' % args.port, flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        engine.stop.set()


if __name__ == '__main__':
    main()
