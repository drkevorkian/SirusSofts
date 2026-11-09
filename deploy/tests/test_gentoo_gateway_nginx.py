#!/usr/bin/env python3
"""Build Hestia-style Nginx configs for dashboard and app proxy templates."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
TEMPLATES=ROOT/"deploy"/"ubuntu24"

class GentooNginxTemplates(unittest.TestCase):
    def test_ssl_and_http_vhosts_parse_under_nginx(self):
        if not shutil.which("nginx") or not shutil.which("openssl"):
            self.skipTest("Nginx and OpenSSL required")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cert=root/"cert.pem"
            key=root/"key.pem"
            result=subprocess.run([
                "openssl","req","-x509","-nodes","-newkey","rsa:2048",
                "-keyout",str(key),"-out",str(cert),"-days","1",
                "-subj","/CN=gentoo.sirussofts.com"
            ],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            cases=[]
            for kind,port in (("dashboard",18480),("app",18481)):
                for ssl in (False,True):
                    template=TEMPLATES/f"sirus-gentoo-{kind}.{'stpl' if ssl else 'tpl'}"
                    raw=template.read_text()
                    substitutions={
                        "%ip%":"127.0.0.1",
                        "%proxy_port%":"19080",
                        "%proxy_ssl_port%":"19443",
                        "%home%":str(root),
                        "%user%":"example",
                        "%domain%":"gentoo.sirussofts.com" if kind=="dashboard" else "worldcom.sirussofts.com",
                        "%domain_idn%":"gentoo.sirussofts.com" if kind=="dashboard" else "worldcom.sirussofts.com",
                        "%alias_idn%":"",
                        "%web_system%":"nginx",
                        "%docroot%":str(root),
                        "%ssl_pem%":str(cert),
                        "%ssl_key%":str(key)
                    }
                    for placeholder,value in substitutions.items():
                        raw=raw.replace(placeholder,value)
                    raw=raw.replace(
                        "error_log /var/log/nginx/domains/"+substitutions["%domain%"]+".error.log error;",
                        f"error_log {root}/nginx-error.log error;")
                    self.assertNotIn("%proxy_",raw)
                    self.assertNotIn("%domain",raw)
                    if ssl:
                        self.assertIn(f"proxy_pass http://127.0.0.1:{port};",raw)
                        self.assertNotIn("proxy_pass http://0.0.0.0",raw)
                    else:
                        self.assertIn("return 308 https://$host$request_uri",raw)
                    cases.append(raw)
            (root/"nginx.conf").write_text(
                "worker_processes 1;\n"
                f"pid {root}/nginx.pid;\n"
                "events {worker_connections 128;}\n"
                f"http {{ access_log {root}/nginx-access.log;\n"+ "\n".join(cases)+"\n}\n")
            result=subprocess.run(["nginx","-t","-p",str(root),
                                   "-c",str(root/"nginx.conf")],
                                  capture_output=True,text=True,timeout=12)
            self.assertEqual(result.returncode,0,result.stderr)

if __name__=="__main__":unittest.main()
