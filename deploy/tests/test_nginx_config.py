"""Native Nginx parser test, using isolated temporary configuration and test certificate."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nginx_vhost_agent as app


@unittest.skipUnless(shutil.which("nginx") and shutil.which("openssl"), "Nginx and OpenSSL required")
class NativeNginxConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cert = root / "test-cert.pem"
        self.key = root / "test-key.pem"
        subprocess.run(["openssl", "req", "-new", "-x509", "-nodes", "-newkey", "rsa:2048",
            "-keyout", str(self.key), "-out", str(self.cert), "-days", "1",
            "-subj", "/CN=example.invalid"], check=True, capture_output=True, timeout=15)
        self.agent = app.NginxVhosts(cert=self.cert, key=self.key)
        self.root = root

    def check_config(self, kind):
        slug = "test-project"
        sites = self.root / "sites"
        routes = self.root / "routes"
        snippets = self.root / "snippets"
        sites.mkdir()
        routes.mkdir()
        snippets.mkdir()
        (snippets / "fastcgi-php.conf").write_text(
            "fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;\n"
            "fastcgi_param QUERY_STRING $query_string;\n")
        if kind == "app":
            site = self.agent._app_config(slug, 23081)
            directory = self.agent._app_directory_route(slug, 23081)
            (routes / "app.conf").write_text(directory)
        else:
            site = self.agent._config(slug)
        (sites / "project.conf").write_text(site)
        original = Path(__file__).resolve().parents[1] / "nginx-sirussofts.conf"
        website = original.read_text().replace(
            "/etc/nginx/sirussofts-managed/routes/*.conf", str(routes) + "/*.conf")
        website = website.replace("/srv/sirussofts/current/CMS", str(self.root / "CMS"))
        (sites / "root.conf").write_text(website)
        conf = self.root / "nginx.conf"
        conf.write_text(
            "pid " + str(self.root / "nginx.pid") + ";\n"
            "error_log " + str(self.root / "nginx-error.log") + " warn;\n"
            "events {}\nhttp {\naccess_log off;\n"
            "include " + str(sites) + "/*.conf;\n}\n"
        )
        result = subprocess.run(
            ["nginx", "-t", "-c", str(conf), "-p", str(self.root)],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_both_app_route_variants_parse(self):
        self.check_config("app")

    def test_page_and_root_routes_parse(self):
        self.check_config("page")


if __name__ == "__main__":
    unittest.main()
