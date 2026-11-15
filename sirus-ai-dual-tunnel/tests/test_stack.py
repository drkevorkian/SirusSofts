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
    def test_router_exact_and_gpu_share(self):
        self.assertEqual(ai.choose_worker('a'*32,'amd',probe=lambda url:url == ai.AMD)[0],'amd')
        self.assertEqual(ai.choose_worker('a'*32,'auto',probe=lambda url:url == ai.CPU)[0],'cpu')
        self.assertEqual(ai.choose_worker('a'*32,'auto',gpu_percent=100,probe=lambda url:url in (ai.CPU,ai.NVIDIA))[0],'nvidia')
        with self.assertRaises(RuntimeError): ai.choose_worker('a'*32,'amd',probe=lambda url:False)

    def test_separate_ssh_links_keepalives(self):
        with tempfile.TemporaryDirectory() as td:
            for f in ('known','in','out'):
                Path(td,f).write_text('placeholder')
                os.chmod(Path(td,f),0o600)
            cfg={'host':'74.208.40.187','ssh_port':22, 'known_hosts':td+'/known',
                 'input_identity':td+'/in','output_identity':td+'/out'}
            inp=tunnel.ssh_command(cfg,'input'); outp=tunnel.ssh_command(cfg,'output')
            self.assertIn('127.0.0.1:18482:127.0.0.1:18082',inp)
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


if __name__=='__main__': unittest.main(verbosity=2)
