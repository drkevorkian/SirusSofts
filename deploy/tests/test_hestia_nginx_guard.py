"""Real Nginx HTTP and HTTPS guard test with distinct Hestia include globs."""
import http.client
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(shutil.which("nginx") and shutil.which("openssl"), "Nginx/OpenSSL required")
class HestiaGuardTests(unittest.TestCase):
    def test_denied_private_paths_http_and_https_both_domains(self):
        directory = Path(__file__).resolve().parents[1] / "ubuntu24"
        installer = (directory / "06-deploy-cms-hestia.sh").read_text(encoding="utf-8")
        self.assertIn('HTTP_GUARD="$CONF/nginx.conf_siruscms"', installer)
        self.assertIn('HTTPS_GUARD="$CONF/nginx.ssl.conf_siruscms"', installer)
        guard = (directory / "sirus-cms-protect.nginx.conf").read_text()
        with tempfile.TemporaryDirectory(prefix="sirus-hestia-guard-") as name:
            root = Path(name)
            http_port, ssl_port = free_port(), free_port()
            http_include = root / "nginx.conf_siruscms"
            https_include = root / "nginx.ssl.conf_siruscms"
            http_include.write_text(guard)
            https_include.write_text(guard)
            cert, key = root / "cert.pem", root / "key.pem"
            subprocess.run([
                "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
                "-keyout", str(key), "-out", str(cert), "-days", "1",
                "-subj", "/CN=sirussofts.org",
                "-addext", "subjectAltName=DNS:sirussofts.org,DNS:sirussofts.com"
            ], check=True, capture_output=True, timeout=20)
            config = root / "nginx.conf"
            config.write_text(
                f"pid {root}/nginx.pid;\nerror_log {root}/nginx.log warn;\n"
                "events {}\nhttp {\n access_log off;\n"
                f"server {{ listen 127.0.0.1:{http_port};\n"
                "server_name sirussofts.org sirussofts.com;\n"
                f"include {root}/nginx.conf_*;\n"
                'location / {\n'
                '    location ~* ^.+\\.(?:css|js)$ { return 200 "UNSAFE_STATIC"; }\n'
                '    return 200 "public";\n}\n}\n'
                f"server {{ listen 127.0.0.1:{ssl_port} ssl;\n"
                "server_name sirussofts.org sirussofts.com;\n"
                f"ssl_certificate {cert}; ssl_certificate_key {key};\n"
                f"include {root}/nginx.ssl.conf_*;\n"
                'location / {\n'
                '    location ~* ^.+\\.(?:css|js)$ { return 200 "UNSAFE_STATIC"; }\n'
                '    return 200 "public";\n}\n}\n}\n'
            )
            check = subprocess.run(["nginx", "-t", "-c", str(config), "-p", name],
                                   capture_output=True, text=True, timeout=10)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            server = subprocess.Popen(["nginx", "-c", str(config), "-p", name,
                "-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                for _ in range(35):
                    if server.poll() is not None:
                        raise RuntimeError("Disposable Nginx exited prematurely")
                    try:
                        with socket.create_connection(("127.0.0.1", http_port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail("Nginx failed to listen")
                private = ["/app/core.php", "/app/secret.js", "/bin/setup.php", "/bin/secret.css", "/tests/smoke.php",
                           "/private/x", "/deploy/config", "/.htaccess",
                           "/.user.ini", "/db.sqlite3", "/secret.key"]
                hosts = ["sirussofts.org", "sirussofts.com"]
                for host in hosts:
                    for path in private + ["/"]:
                        conn = http.client.HTTPConnection("127.0.0.1", http_port, timeout=3)
                        conn.request("GET", path, headers={"Host": host})
                        response = conn.getresponse()
                        self.assertEqual(response.status, 200 if path == "/" else 404,
                                         ("HTTP", host, path))
                        response.read()
                        conn.close()
                ctx = ssl.create_default_context(cafile=str(cert))
                for host in hosts:
                    for path in private + ["/"]:
                        raw = socket.create_connection(("127.0.0.1", ssl_port), timeout=4)
                        with ctx.wrap_socket(raw, server_hostname=host) as secure:
                            secure.sendall((
                                f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
                            ).encode("ascii"))
                            response = http.client.HTTPResponse(secure)
                            response.begin()
                            self.assertEqual(response.status, 200 if path == "/" else 404,
                                             ("HTTPS", host, path))
                            response.read()
            finally:
                server.terminate()
                try:
                    server.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
