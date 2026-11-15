#!/usr/bin/env python3
"""Ubuntu localhost output collector, event stream, and static AI frontend.

A separate Gentoo-initiated SSH -L tunnel pushes task outputs here. Requests
from browsers are protected by Hestia Nginx HTTPS + Basic authentication.
"""
import argparse
from contextlib import contextmanager
import hmac
import http.server
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent / 'web'
ID = re.compile(r'^[0-9a-f]{32}$')
# Bounded chunks allow a large answer to span many durable events.
# SQLite signed 64-bit sequence is the transport bound, not the model output cap.
MAX_EVENT_SEQ = (1 << 63) - 1


@contextmanager
def connect(path):
    con = sqlite3.connect(path, timeout=12)
    try:
        con.execute('PRAGMA busy_timeout=12000')
        con.execute('PRAGMA journal_mode=WAL')
        with con:
            yield con
    finally:
        con.close()


def init_db(path):
    with connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS events(
          job_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
          content TEXT NOT NULL, worker TEXT NOT NULL,
          created_at REAL NOT NULL, PRIMARY KEY(job_id,seq))''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at)')
        db.execute('CREATE TABLE IF NOT EXISTS channel_heartbeat (channel TEXT PRIMARY KEY, last_seen REAL NOT NULL)')


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'SirusAI-Output/1.0'

    def log_message(self, fmt, *args):
        pass

    def headers_out(self, status, ctype, size=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        if size is not None:
            self.send_header('Content-Length', str(size))

    def json(self, status, obj):
        body = json.dumps(obj).encode()
        self.headers_out(status, 'application/json; charset=utf-8', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ('/internal/push', '/internal/heartbeat'):
            return self.json(404, {'error': 'Not found'})
        token = self.headers.get('X-Sirus-AI-Token', '')
        if not hmac.compare_digest(token, self.server.secret):
            return self.json(403, {'error': 'Forbidden'})
        if self.path == '/internal/heartbeat':
            with connect(self.server.db) as db:
                db.execute('INSERT OR REPLACE INTO channel_heartbeat(channel,last_seen) VALUES(?,?)', ('output',time.time()))
            return self.json(200, {'ok': True})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.json(415, {'error': 'JSON required'})
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if not 0 < n < 128 * 1024:
                return self.json(413, {'error': 'Invalid payload length'})
            data = json.loads(self.rfile.read(n))
            job = data['job_id']
            seq = data['seq']
            kind = data['type']
            worker = data.get('worker', '')
            content = data.get('content', '')
            if not isinstance(job, str) or not ID.fullmatch(job):
                raise ValueError('Invalid job identifier')
            if type(seq) is not int or not 1 <= seq <= MAX_EVENT_SEQ:
                raise ValueError('Invalid event number')
            if kind not in ('queued', 'started', 'chunk', 'completed', 'error'):
                raise ValueError('Invalid event type')
            if (seq == 1 and kind != 'queued') or (seq == 2 and kind != 'started') or (
                    seq >= 3 and kind not in ('chunk', 'completed', 'error')):
                raise ValueError('Invalid event sequence')
            # Limit each authenticated packet, not the complete response.
            if not isinstance(content, str) or len(content) > 16000 or worker not in ('','cpu','nvidia','amd'):
                raise ValueError('Invalid event content')
            with connect(self.server.db) as db:
                db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',
                           (job, seq, kind, content, worker, time.time()))
            self.json(200, {'ok': True})
        except (ValueError, KeyError, TypeError, sqlite3.Error) as e:
            self.json(400, {'error': str(e)[:100]})

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        if path == '/healthz':
            return self.json(200, {'status': 'ok', 'channel': 'output-gateway'})
        if path == '/api/out/health':
            with connect(self.server.db) as db:
                row = db.execute('SELECT last_seen FROM channel_heartbeat WHERE channel=?', ('output',)).fetchone()
            age = round(max(0,time.time()-row[0]),1) if row else None
            return self.json(200, {'status': 'ok', 'channel': 'output',
                                   'tunnel_connected': age is not None and age < 40,
                                   'heartbeat_age_seconds': age})
        if path.startswith('/api/out/events/'):
            job = path.rsplit('/', 1)[-1]
            if not ID.fullmatch(job):
                return self.json(400, {'error': 'Invalid job ID'})
            try:
                after = int(parse_qs(parts.query).get('after', ['0'])[0])
            except ValueError:
                return self.json(400, {'error': 'Invalid event cursor'})
            if not 0 <= after <= MAX_EVENT_SEQ:
                return self.json(400, {'error': 'Invalid event cursor'})
            return self.events(job, after)
        names = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/assets/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                 '/assets/styles.css': ('styles.css', 'text/css; charset=utf-8')}
        if path not in names:
            return self.json(404, {'error': 'Not found'})
        name, ctype = names[path]
        body = (ROOT / name).read_bytes()
        self.headers_out(200, ctype, len(body))
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def events(self, job, after):
        self.headers_out(200, 'text/event-stream; charset=utf-8')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        self.wfile.write(b'retry: 1500\n\n')
        self.wfile.flush()
        start = time.monotonic()
        next_heartbeat = 0
        try:
            while time.monotonic() - start < 55:
                with connect(self.server.db) as db:
                    rows = db.execute('SELECT seq,kind,content,worker FROM events WHERE job_id=? AND seq>? ORDER BY seq',
                                      (job, after)).fetchall()
                for seq, kind, content, worker in rows:
                    event = {'job_id': job, 'seq': seq, 'type': kind, 'content': content, 'worker': worker}
                    payload = 'id: %d\nevent: update\ndata: %s\n\n' % (seq, json.dumps(event, ensure_ascii=True))
                    self.wfile.write(payload.encode())
                    self.wfile.flush()
                    after = seq
                    if kind in ('completed', 'error'):
                        return
                if time.monotonic() >= next_heartbeat:
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    next_heartbeat = time.monotonic() + 10
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=18483)
    ap.add_argument('--db', default='/var/lib/sirus-ai-dual/events.sqlite3')
    ap.add_argument('--secret', default='/etc/sirus-ai-dual/secret')
    args = ap.parse_args()
    secret = Path(args.secret).read_text().strip()
    if len(secret) < 32:
        raise SystemExit('Shared output secret missing')
    init_db(args.db)
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    srv.daemon_threads = True
    srv.db = args.db
    srv.secret = secret
    print('Sirus AI OUTPUT listening on 127.0.0.1:%s' % args.port, flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
