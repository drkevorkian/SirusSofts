"""Native Nginx security test for the Hestia-owned CMS custom include."""
import http.client
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(shutil.which("nginx"), "Nginx binary required")
class HestiaGuardTests(unittest.TestCase):
    def test_denied_private_paths_and_public_home(self):
        source = Path(__file__).resolve().parents[1] / "ubuntu24" / "sirus-cms-protect.nginx.conf"
        with tempfile.TemporaryDirectory(prefix="sirus-hestia-guard-") as name:
            root = Path(name)
            port = free_port()
            config = root / "nginx.conf"
            config.write_text("pid " + str(root / "nginx.pid") + ";\n"
                "error_log " + str(root / "nginx.log") + " warn;\n"
                "events {}\nhttp {\n access_log off;\n server {\n"
                f"listen 127.0.0.1:{port};\n"
                "server_name sirussofts.org sirussofts.com;\n"
                f"include {source};\n"
                'location / { return 200 "public"; }\n'
                "}\n}\n")
            check = subprocess.run(["nginx", "-t", "-c", str(config), "-p", name],
                                   capture_output=True, text=True, timeout=10)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            server = subprocess.Popen(["nginx", "-c", str(config), "-p", name,
                "-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                for _ in range(30):
                    if server.poll() is not None:
                        raise RuntimeError("Disposable Nginx exited prematurely")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail("Nginx failed to listen")
                for path in ["/app/core.php", "/bin/setup.php", "/tests/smoke.php",
                             "/private/x", "/deploy/config", "/.htaccess",
                             "/.user.ini", "/db.sqlite3", "/secret.key"]:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    conn.request("GET", path, headers={"Host": "sirussofts.org"})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 404, path)
                    response.read()
                    conn.close()
                for hostname in ["sirussofts.org", "sirussofts.com"]:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    conn.request("GET", "/", headers={"Host": hostname})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b"public")
                    conn.close()
            finally:
                server.terminate()
                try:
                    server.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
