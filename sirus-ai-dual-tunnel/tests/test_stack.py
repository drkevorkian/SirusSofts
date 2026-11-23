import http.server
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ai = load_module('input_server', ROOT/'gentoo/input_server.py')
out = load_module('gateway', ROOT/'ubuntu/gateway.py')
tunnel = load_module('tunnel', ROOT/'gentoo/tunnel.py')


class MockModel(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        if self.path == '/v1/models':
            raw=b'{"data":[{"id":"sirussofts-coder"}]}'
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
            return
        self.send_response(200 if self.path == '/health' else 404)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def do_POST(self):
        n = int(self.headers['Content-Length'])
        data = json.loads(self.rfile.read(n))
        if self.path != '/v1/chat/completions':
            self.send_error(404); return
        msg = data['messages'][-1]['content']
        encoded = json.dumps({'choices':[{'message':{'content':'TEST ANSWER: '+msg}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(encoded)))
        self.end_headers(); self.wfile.write(encoded)


def server(Handler, port=0):
    srv = http.server.ThreadingHTTPServer(('127.0.0.1',port), Handler)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def request(url, obj=None, headers=None):
    hdr = headers or {}
    data = json.dumps(obj).encode() if obj is not None else None
    if data is not None: hdr = {'Content-Type':'application/json',**hdr}
    req = urllib.request.Request(url, data=data, headers=hdr)
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.status, json.load(r)


class TestSirusAI(unittest.TestCase):
    def test_image_only_manual_static_routes(self):
        gateway = server(out.Handler)
        gateway.db = ':memory:'
        gateway.secret = 's' * 64
        try:
            base = 'http://127.0.0.1:' + str(gateway.server_port)
            for path, expected in [
                ('/manual.html', b'Approved Manual Viewer'),
                ('/assets/manual.js', b'manuals/page/'),
                ('/assets/manual_sources.js', b'SIRUS_MANUAL_SOURCES_START')
            ]:
                with urllib.request.urlopen(base + path, timeout=4) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn(expected, resp.read())
            for raw in ('/manual.pdf', '/assets/private.pdf', '/internal/pdf-admin'):
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(base + raw, timeout=4)
                self.assertEqual(rejected.exception.code, 404)
        finally:
            gateway.shutdown()
            gateway.server_close()

    def test_router_exact_and_gpu_share(self):
        self.assertEqual(ai.choose_worker('a'*32,'amd',probe=lambda url:url == ai.AMD)[0],'amd')
        self.assertEqual(ai.choose_worker('a'*32,'auto',probe=lambda url:url == ai.CPU)[0],'cpu')
        self.assertEqual(ai.choose_worker('a'*32,'auto',gpu_percent=100,probe=lambda url:url in (ai.CPU,ai.NVIDIA))[0],'nvidia')
        self.assertEqual(ai.choose_worker('a'*32,'auto',preferred_gpu='amd',
            probe=lambda url:url in (ai.AMD,ai.NVIDIA,ai.CPU))[0],'amd')
        self.assertEqual(ai.choose_worker('a'*32,'auto',preferred_gpu='nvidia',
            probe=lambda url:url in (ai.AMD,ai.NVIDIA,ai.CPU))[0],'nvidia')
        self.assertEqual(ai.choose_worker('a'*32,'auto',preferred_gpu='amd',
            probe=lambda url:url==ai.CPU)[0],'cpu')
        with self.assertRaises(RuntimeError):
            ai.choose_worker('a'*32,'auto',preferred_gpu='nvidia',
                probe=lambda url:url==ai.CPU)
        # Explicit administrator overrides remain explicit, never silently re-routed.
        self.assertEqual(ai.choose_worker('a'*32,'cpu',preferred_gpu='nvidia',
            probe=lambda url:url==ai.CPU)[0],'cpu')

        with self.assertRaises(RuntimeError): ai.choose_worker('a'*32,'amd',probe=lambda url:False)

    def test_strict_local_models(self):
        model=server(MockModel)
        prev=(ai.CPU,ai.NVIDIA,ai.AMD)
        ai.CPU='http://127.0.0.1:'+str(model.server_port)
        ai.NVIDIA='http://127.0.0.1:1'
        ai.AMD='http://127.0.0.1:2'
        try:
            self.assertEqual(ai.local_model_ids(ai.CPU),['sirussofts-coder'])
            self.assertEqual(ai.choose_worker('a'*32,'cpu',model_id='sirussofts-coder')[0],'cpu')
            with self.assertRaises(RuntimeError):
                ai.choose_worker('a'*32,'cpu',model_id='not-loaded')
            with self.assertRaises(ValueError):
                ai.assert_local_worker_url('https://api.example.com')
            with tempfile.TemporaryDirectory() as td:
                key=Path(td,'secret'); key.write_text('a'*64)
                db=td+'/queue.sqlite3'
                ai.init_db(db)
                engine=ai.Engine(db,str(key))
                server_in=server(ai.Handler);server_in.engine=engine
                try:
                    code,health=request('http://127.0.0.1:%d/api/in/health'%server_in.server_port)
                    self.assertEqual(code,200)
                    self.assertTrue(health['local_only'])
                    self.assertEqual(health['models']['cpu'],['sirussofts-coder'])
                    with self.assertRaises(urllib.error.HTTPError) as err:
                        request('http://127.0.0.1:%d/api/in/jobs'%server_in.server_port,
                            {'messages':[{'role':'user','content':'test'}],'local_only':False})
                    self.assertEqual(err.exception.code,400)
                    code,job=request('http://127.0.0.1:%d/api/in/jobs'%server_in.server_port,
                        {'messages':[{'role':'user','content':'test'}], 'model_id':'sirussofts-coder',
                         'target':'cpu','local_only':True})
                    self.assertEqual(code,202)
                    self.assertEqual(job['model_id'],'sirussofts-coder')
                    with ai.db_connect(db) as connection:
                        self.assertEqual(connection.execute('SELECT model_id FROM jobs WHERE id=?',
                            (job['job_id'],)).fetchone()[0],'sirussofts-coder')
                finally:
                    server_in.shutdown();server_in.server_close()
        finally:
            ai.CPU,ai.NVIDIA,ai.AMD=prev
            model.shutdown();model.server_close()

    def test_separate_ssh_links_keepalives(self):
        with tempfile.TemporaryDirectory() as td:
            for f in ('known','in','out'):
                Path(td,f).write_text('placeholder')
                os.chmod(Path(td,f),0o600)
            cfg={'host':'74.208.40.187','ssh_port':22, 'known_hosts':td+'/known',
                 'input_identity':td+'/in','output_identity':td+'/out'}
            inp=tunnel.ssh_command(cfg,'input'); outp=tunnel.ssh_command(cfg,'output')
            self.assertIn('127.0.0.1:18485:127.0.0.1:18082',inp)
            self.assertIn('127.0.0.1:18084:127.0.0.1:18483',outp)
            self.assertIn('-R',inp)
            self.assertIn('-L',outp)
            self.assertNotEqual(inp[inp.index('-i')+1],outp[outp.index('-i')+1])
            for cmd in (inp,outp):
                self.assertIn('ServerAliveInterval=15',cmd)
                self.assertIn('ServerAliveCountMax=3',cmd)
                self.assertIn('StrictHostKeyChecking=yes',cmd)
                self.assertIn('ExitOnForwardFailure=yes',cmd)

    def test_durable_dual_channel_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            secret = Path(td,'secret'); secret.write_text('a'*64)
            model = server(MockModel)
            old=(ai.CPU,ai.NVIDIA,ai.AMD,ai.OUT)
            ai.CPU='http://127.0.0.1:'+str(model.server_port)
            ai.NVIDIA='http://127.0.0.1:1'; ai.AMD='http://127.0.0.1:2'
            dbout=td+'/events.sqlite3'; dbin=td+'/input.sqlite3'
            out.init_db(dbout); ai.init_db(dbin)
            engine=ai.Engine(dbin,str(secret))
            s_in=server(ai.Handler)
            s_in.engine=engine
            threading.Thread(target=engine.worker_loop,daemon=True).start()
            threading.Thread(target=engine.dispatch_loop,daemon=True).start()
            try:
                status,job=request('http://127.0.0.1:%d/api/in/jobs'%s_in.server_port,
                                   {'messages':[{'role':'user','content':'Hello dual tunnel'}], 'target':'auto'})
                self.assertEqual(status,202)
                self.assertEqual(job['output'],'separate-output-tunnel')
                job_id=job['job_id']
                limit=time.time()+8
                while time.time()<limit:
                    with ai.db_connect(dbin) as con:
                        pending=con.execute("SELECT count(*) FROM outbox WHERE job_id=? AND type='completed' AND acked=0",(job_id,)).fetchone()[0]
                    if pending: break
                    time.sleep(0.1)
                self.assertEqual(pending,1,'Model answer should be held in the Gentoo outbox')
                out_server=server(out.Handler)
                out_server.db=dbout; out_server.secret=secret.read_text()
                ai.OUT='http://127.0.0.1:%d/internal/push'%out_server.server_port
                try:
                    limit=time.time()+10
                    while time.time()<limit:
                        with out.connect(dbout) as con:
                            n=con.execute('SELECT count(*) FROM events WHERE job_id=?',(job_id,)).fetchone()[0]
                        if n==3: break
                        time.sleep(0.1)
                    self.assertEqual(n,3,'Output stream should receive all queued events after reconnect')
                    with out.connect(dbout) as con:
                        rows=con.execute('SELECT seq,kind,content FROM events WHERE job_id=? ORDER BY seq',(job_id,)).fetchall()
                    self.assertEqual([r[1] for r in rows],['queued','started','completed'])
                    self.assertEqual(rows[-1][2], 'TEST ANSWER: Hello dual tunnel')
                    _,health=request('http://127.0.0.1:%d/api/out/health'%out_server.server_port)
                    self.assertFalse(health['tunnel_connected'])
                    req=urllib.request.Request('http://127.0.0.1:%d/internal/heartbeat'%out_server.server_port,
                                             data=b'{}',method='POST',
                                             headers={'X-Sirus-AI-Token':secret.read_text()})
                    with urllib.request.urlopen(req,timeout=3) as r: self.assertEqual(r.status,200)
                    _,health=request('http://127.0.0.1:%d/api/out/health'%out_server.server_port)
                    self.assertTrue(health['tunnel_connected'])
                    self.assertLess(health['heartbeat_age_seconds'],5)
                    req=urllib.request.Request('http://127.0.0.1:%d/internal/heartbeat'%out_server.server_port,
                                             data=b'{}',method='POST',
                                             headers={'X-Sirus-AI-Token':'bad'})
                    with self.assertRaises(urllib.error.HTTPError) as e:
                        urllib.request.urlopen(req,timeout=3)
                    self.assertEqual(e.exception.code,403)
                    status,_=request('http://127.0.0.1:%d/internal/push'%out_server.server_port,
                                     {'job_id':job_id,'seq':3,'type':'completed','content':'different','worker':'cpu'},
                                     {'X-Sirus-AI-Token':secret.read_text()})
                    self.assertEqual(status,200)
                    with out.connect(dbout) as con:
                        self.assertEqual(con.execute('SELECT count(*) FROM events WHERE job_id=?',(job_id,)).fetchone()[0],3)
                finally:
                    out_server.shutdown(); out_server.server_close()
            finally:
                engine.stop.set(); s_in.shutdown(); s_in.server_close(); model.shutdown(); model.server_close()
                ai.CPU,ai.NVIDIA,ai.AMD,ai.OUT=old

    def test_long_inference_response_transport_preserves_content(self):
        # A completion larger than the old 16,000-character limit must survive
        # the Gentoo SQLite outbox, Ubuntu gateway and event storage exactly.
        with tempfile.TemporaryDirectory() as td:
            input_db = str(Path(td, 'input.sqlite3'))
            output_db = str(Path(td, 'events.sqlite3'))
            ai.init_db(input_db)
            out.init_db(output_db)
            job_id = 'd' * 32
            content = 'Large response: ' + ('AI-' * 17000)
            with ai.db_connect(input_db) as con:
                ai.add_completed_events(con, job_id, content, 'amd')
                rows = con.execute('SELECT seq,type,content,worker FROM outbox ORDER BY seq').fetchall()
            self.assertGreater(len(rows), 3)
            self.assertTrue(all(len(row[2]) <= ai.OUTPUT_CHUNK_CHARS for row in rows))
            self.assertEqual(rows[-1][1], 'completed')
            self.assertEqual(rows[-1][2], '')
            self.assertEqual(''.join(row[2] for row in rows if row[1] == 'chunk'), content)
            gateway = server(out.Handler)
            gateway.db = output_db
            gateway.secret = 'a' * 64
            try:
                for seq, kind, fragment, worker in rows:
                    status, _ = request('http://127.0.0.1:%d/internal/push' % gateway.server_port,
                                        {'job_id': job_id, 'seq': seq, 'type': kind,
                                         'content': fragment, 'worker': worker},
                                        {'X-Sirus-AI-Token': gateway.secret})
                    self.assertEqual(status, 200)
                with out.connect(output_db) as con:
                    received = con.execute(
                        'SELECT seq,kind,content FROM events WHERE job_id=? ORDER BY seq',
                        (job_id,)).fetchall()
                self.assertEqual(len(received), len(rows))
                self.assertEqual(''.join(row[2] for row in received if row[1] == 'chunk'), content)
                self.assertEqual(received[-1][1], 'completed')
            finally:
                gateway.shutdown()
                gateway.server_close()

    def test_long_generation_does_not_set_fixed_max_tokens(self):
        seen = {}
        class CaptureModel(MockModel):
            def do_POST(self):
                size = int(self.headers['Content-Length'])
                data = json.loads(self.rfile.read(size))
                seen.update(data)
                raw = json.dumps({'choices':[{'message':{'content':'OK'}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        model = server(CaptureModel)
        try:
            answer = ai.chat('http://127.0.0.1:%d' % model.server_port,
                             [{'role':'user', 'content':'Generate long response'}],timeout=10)
            self.assertEqual(answer,'OK')
            self.assertNotIn('max_tokens',seen)
            self.assertNotIn('max_completion_tokens',seen)
        finally:
            model.shutdown()
            model.server_close()



if __name__=='__main__': unittest.main(verbosity=2)
