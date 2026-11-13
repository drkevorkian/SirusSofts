"""Disposable local Nginx + HTTP backend test; no real server deployment."""
import http.client
import http.server
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nginx_vhost_agent as vhost


class Backend(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"path": self.path, "prefix": self.headers.get("X-Forwarded-Prefix"),
                           "host": self.headers.get("Host")}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(shutil.which("nginx") and shutil.which("openssl"), "Nginx and OpenSSL required")
class NginxProxyEndToEnd(unittest.TestCase):
    def test_live_proxy_from_both_root_domains_and_subdomains(self):
        with tempfile.TemporaryDirectory(prefix="sirus-proxy-") as raw:
            tmp = Path(raw)
            rootport = unused_port()
            tlsport = unused_port()
            backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
            backendport = backend.server_address[1]
            worker = threading.Thread(target=backend.serve_forever, daemon=True)
            worker.start()
            nginx = None
            try:
                cert = tmp / "certificate.pem"
                key = tmp / "privatekey.pem"
                subprocess.run(["openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=test-project.sirussofts.org",
                    "-addext", "subjectAltName=DNS:test-project.sirussofts.org,DNS:test-project.sirussofts.com"],
                    capture_output=True, check=True, timeout=15)
                config = vhost.NginxVhosts(cert=cert, key=key)
                snippets = tmp / "snippets"
                routes = tmp / "routes"
                sites = tmp / "sites"
                for p in (snippets, routes, sites):
                    p.mkdir()
                (snippets / "fastcgi-php.conf").write_text(
                    "fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;\n")
                (routes / "app.conf").write_text(config._app_directory_route("test-project", backendport))
                site = config._app_config("test-project", backendport)
                site = site.replace("listen 80;", "listen 127.0.0.1:" + str(rootport) + ";")
                site = site.replace("listen 443 ssl;", "listen 127.0.0.1:" + str(tlsport) + " ssl;")
                (sites / "app.conf").write_text(site)
                rootTemplate = Path(__file__).resolve().parents[1] / "nginx-sirussofts.conf"
                main = rootTemplate.read_text()
                main = main.replace("/etc/nginx/sirussofts-managed/routes/*.conf",
                                    str(routes) + "/*.conf")
                main = main.replace("/srv/sirussofts/current/CMS", str(tmp / "CMS"))
                main = main.replace("listen [::]:80;", "")
                main = main.replace("listen 80;", "listen 127.0.0.1:" + str(rootport) + ";")
                (sites / "main.conf").write_text(main)
                mainconf = tmp / "nginx.conf"
                mainconf.write_text("pid " + str(tmp / "nginx.pid") + ";\n"
                    "error_log " + str(tmp / "error.log") + " warn;\n"
                    "events {}\nhttp {\naccess_log off;\n"
                    "include " + str(sites) + "/*.conf;\n}\n")
                compileResult = subprocess.run(["nginx", "-t", "-c", str(mainconf), "-p", str(tmp)],
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(compileResult.returncode, 0, compileResult.stderr)
                nginx = subprocess.Popen(["nginx", "-c", str(mainconf), "-p", str(tmp),
                    "-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                for _ in range(35):
                    if nginx.poll() is not None:
                        raise RuntimeError("Disposable nginx stopped during startup")
                    try:
                        with socket.create_connection(("127.0.0.1", rootport), timeout=0.3):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Disposable nginx did not start")
                for host in ("sirussofts.org", "sirussofts.com"):
                    conn = http.client.HTTPConnection("127.0.0.1", rootport, timeout=3)
                    conn.request("GET", "/test-project/api/v1?test=1", headers={"Host": host})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200, (host, response.status))
                    data = json.loads(response.read())
                    self.assertEqual(data["path"], "/api/v1?test=1")
                    self.assertEqual(data["prefix"], "/test-project")
                    self.assertEqual(data["host"], host)
                    conn.close()
                context = ssl.create_default_context(cafile=str(cert))
                for host in ("test-project.sirussofts.org", "test-project.sirussofts.com"):
                    rawsock = socket.create_connection(("127.0.0.1", tlsport), timeout=3)
                    with context.wrap_socket(rawsock, server_hostname=host) as secure:
                        secure.sendall(("GET /health HTTP/1.1\r\nHost: " + host +
                                        "\r\nConnection: close\r\n\r\n").encode())
                        response = http.client.HTTPResponse(secure)
                        response.begin()
                        self.assertEqual(response.status, 200)
                        data = json.loads(response.read())
                        self.assertEqual(data["path"], "/health")
                        self.assertEqual(data["host"], host)
                        self.assertIsNone(data["prefix"])
            finally:
                if nginx is not None:
                    nginx.terminate()
                    try:
                        nginx.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        nginx.kill()
                        nginx.communicate(timeout=5)
                backend.shutdown()
                backend.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
