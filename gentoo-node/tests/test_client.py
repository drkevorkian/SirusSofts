import importlib.util
import hashlib
import hmac
import json
from pathlib import Path
import unittest
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1]/"client.py"
spec=importlib.util.spec_from_file_location("sirus_gentoo_node_client",FILE)
client=importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)

class DelegationTests(unittest.TestCase):
    def settings(self):
        return {
            "node_id":"gentoo-home",
            "coordinator_url":"https://sirussofts.com/node-heartbeat.php",
            "work_url":"https://sirussofts.com/node-work.php",
            "key_file":"/etc/sirus-gentoo-node/heartbeat.key",
            "period_seconds":60
        }

    def test_https_and_work_endpoint_pinning(self):
        self.assertEqual(client.validate_config(self.settings())["work_url"],
                         "https://sirussofts.com/node-work.php")
        for bad in ("http://sirussofts.com/node-work.php",
                    "https://sirussofts.com/admin", "https://sirussofts.com/node-work.php?x=1"):
            data=self.settings();data["work_url"]=bad
            with self.assertRaises(ValueError):client.validate_config(data)

    def test_signed_job_request_is_replay_resistant(self):
        cfg=client.validate_config(self.settings())
        key=b"S"*32
        req=client.signed_job_request(cfg,key,"submit","a"*32,"health_snapshot")
        payload=json.loads(req.data)
        self.assertEqual(payload["op"],"submit")
        self.assertEqual(payload["kind"],"health_snapshot")
        self.assertEqual(payload["payload"],{})
        signature=hmac.new(key,req.data,hashlib.sha256).hexdigest()
        self.assertEqual(req.get_header("X-sirus-node-signature"),signature)
        again=client.signed_job_request(cfg,key,"submit","a"*32,"health_snapshot")
        self.assertNotEqual(req.data,again.data)
        with self.assertRaises(ValueError):
            client.signed_job_request(cfg,key,"submit","a"*32,"sudo bash")

    def test_status_request_never_leaks_other_jobs(self):
        cfg=client.validate_config(self.settings())
        request=client.signed_job_request(cfg,b"X"*32,"status","d"*32)
        data=json.loads(request.data)
        self.assertEqual(data["op"],"status")
        self.assertNotIn("kind",data)
        self.assertNotIn("payload",data)

if __name__=="__main__":unittest.main()
