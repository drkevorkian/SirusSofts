import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import client
import dashboard
import tunnel

class GatewayTests(unittest.TestCase):
    def test_password_is_slow_hashed_and_not_plaintext(self):
        record=dashboard.make_hash("secret-correct-123456789")
        self.assertNotIn("secret-correct-123456789",record)
        self.assertTrue(dashboard.verify("secret-correct-123456789",record))
        self.assertFalse(dashboard.verify("wrong-password-123456",record))
        self.assertFalse(dashboard.verify("wrong-password-123456","malformed"))

    def test_html_escapes_untrusted_upstream_job_values(self):
        metrics={"cpu_pct":12.5,"memory_used_bytes":1024*1024*1024,
                 "disk_used_bytes":42,"disk_total_bytes":100,
                 "uptime_seconds":88,
                 "gpus":[{"name":"<script>alert(1)</script>","vendor":"0x10de",
                          "usage_pct":8,"temp_c":45,
                          "vram_used_bytes":100,"vram_total_bytes":200}]}
        jobs=[{"request_id":"b"*32,"kind":"<img onerror=alert(1)>",
               "state":"done","result":{"payload":"<script>evil</script>"},
               "error":"","updated_at":1794168000,"attempts":1}]
        html=dashboard.page(metrics,{"node_id":"gentoo-home",
                "ram_bytes":8*1024**3,"cpu_threads":16},jobs,"","Synced").decode()
        self.assertIn("&lt;script&gt;",html)
        self.assertNotIn("<script>alert(1)</script>",html)
        self.assertNotIn("<img onerror=alert(1)>",html)
        self.assertIn("Previous tasks",html)

    def test_bounded_cpu_samples_store_24h_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db=str(Path(directory)/"samples.sqlite")
            with dashboard.metrics_db(db) as conn:
                conn.execute("INSERT INTO samples VALUES(?,?,?,?)",
                             (100,22,40,80))
                conn.execute("INSERT INTO samples VALUES(?,?,?,?)",
                             (200,50,40,80))
            self.assertIn("<svg",dashboard.chart(db))
            self.assertEqual(sqlite3.connect(db).execute(
                "SELECT COUNT(*) FROM samples").fetchone()[0],2)

    def test_ssh_reverses_only_fixed_loopback_web_ports(self):
        cfg=tunnel.validate({
            "host":"74.208.40.187","ssh_port":22,"user":"sirus-tunnel",
            "identity":"/etc/sirus-gentoo-node/tunnel_ed25519",
            "known_hosts":"/etc/sirus-gentoo-node/tunnel_known_hosts"})
        args=tunnel.ssh_command(cfg)
        self.assertEqual(args.count("-R"),2)
        self.assertIn("127.0.0.1:18480:127.0.0.1:18080",args)
        self.assertIn("127.0.0.1:18481:127.0.0.1:18081",args)
        self.assertIn("StrictHostKeyChecking=yes",args)
        self.assertIn("ExitOnForwardFailure=yes",args)
        self.assertNotIn("ClearAllForwardings=yes",args)
        with self.assertRaises(ValueError):
            tunnel.validate({**cfg,"ssh_port":0})
        with self.assertRaises(ValueError):
            tunnel.validate({**cfg,"password":"never"})

    def test_signed_history_request_never_contains_other_node_ids(self):
        config=client.validate_config({
            "node_id":"gentoo-home",
            "coordinator_url":"https://sirussofts.com/node-heartbeat.php",
            "work_url":"https://sirussofts.com/node-work.php",
            "key_file":"/etc/sirus-gentoo-node/heartbeat.key","period_seconds":60})
        import json
        request=client.signed_job_request(config,b"K"*32,"history")
        body=json.loads(request.data)
        self.assertEqual(body["node_id"],"gentoo-home")
        self.assertEqual(body["op"],"history")
        self.assertNotIn("request_id",body)
        self.assertNotIn("lease_token",body)

    def test_nginx_templates_use_loopback_and_not_existing_cms_hostname(self):
        for name,port in (("dashboard",18480),("app",18481)):
            for ext in ("tpl","stpl"):
                body=(ROOT.parent/"deploy"/"ubuntu24"/
                      f"sirus-gentoo-{name}.{ext}").read_text()
                self.assertIn(f"127.0.0.1:{port}" if ext=="stpl" else
                              "%domain_idn%",body)
                self.assertIn("%domain_idn%",body)
                self.assertNotIn("sirussofts.com",body)
                if ext=="stpl":
                    self.assertIn("ssl_certificate %ssl_pem%;",body)
                    self.assertIn("proxy_pass http://127.0.0.1:",body)

if __name__=="__main__":unittest.main()
