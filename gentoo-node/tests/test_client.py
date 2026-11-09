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

    def test_signed_claim_has_no_server_work_arguments(self):
        cfg=client.validate_config(self.settings())
        key=b"S"*32
        req=client.signed_job_request(cfg,key,"claim")
        payload=json.loads(req.data)
        self.assertEqual(payload["op"],"claim")
        self.assertNotIn("request_id",payload)
        self.assertNotIn("kind",payload)
        self.assertNotIn("payload",payload)
        signature=hmac.new(key,req.data,hashlib.sha256).hexdigest()
        self.assertEqual(req.get_header("X-sirus-node-signature"),signature)
        again=client.signed_job_request(cfg,key,"claim")
        self.assertNotEqual(req.data,again.data)
        with self.assertRaises(ValueError):
            client.signed_job_request(cfg,key,"submit","a"*32)

    def test_completion_includes_current_lease_and_result(self):
        cfg=client.validate_config(self.settings())
        req=client.signed_job_request(cfg,b"X"*32,"complete","a"*32,
            lease_token="b"*32,state="done",result={"executor":"gentoo"})
        data=json.loads(req.data)
        self.assertEqual(data["op"],"complete")
        self.assertEqual(data["lease_token"],"b"*32)
        self.assertEqual(data["result"]["executor"],"gentoo")
        with self.assertRaises(ValueError):
            client.signed_job_request(cfg,b"X"*32,"complete","a"*32,
                lease_token="../escape",state="done",result={})

    def test_gentoo_performs_work_locally(self):
        result=client.execute_job("compute_selftest")
        self.assertEqual(result["iterations"],50000)
        self.assertEqual(len(result["sha256"]),64)
        self.assertEqual(result["executor"],"gentoo")
        with self.assertRaises(ValueError):client.execute_job("shell")
        with patch.object(client,"performance",return_value={"cpu_pct":3}):
            info=client.execute_job("health_snapshot")
            self.assertEqual(info["metrics"]["cpu_pct"],3)
            self.assertEqual(info["node"],"gentoo")

    def test_poll_acks_gentoo_result(self):
        cfg=client.validate_config(self.settings())
        requests=[]
        def fake_request(conf,key,op,rid=None,**kwargs):
            requests.append((op,rid,kwargs))
            if op=="claim":
                return {"ok":True,"state":"running","request_id":"a"*32,
                        "lease_token":"b"*32,"kind":"compute_selftest"}
            return {"ok":True,"state":"done","request_id":rid,"result":kwargs["result"]}
        with patch.object(client,"work_request",side_effect=fake_request), \
             patch.object(client,"execute_job",return_value={"executor":"gentoo"}):
            result=client.poll_once(cfg,b"Z"*32)
        self.assertEqual(result["state"],"done")
        self.assertEqual([r[0] for r in requests],["claim","complete"])
        self.assertEqual(requests[1][2]["lease_token"],"b"*32)
        self.assertEqual(requests[1][2]["result"],{"executor":"gentoo"})

    def test_unknown_server_task_is_rejected_not_executed(self):
        cfg=client.validate_config(self.settings())
        def fake_request(conf,key,op,rid=None,**kwargs):
            if op=="claim":
                return {"state":"running","request_id":"a"*32,
                        "lease_token":"b"*32,"kind":"bash"}
            self.assertEqual(kwargs["state"],"failed")
            self.assertEqual(kwargs["result"],{})
            return {"state":"failed","request_id":rid}
        with patch.object(client,"work_request",side_effect=fake_request), \
             patch.object(client,"execute_job",side_effect=AssertionError("executed")):
            self.assertEqual(client.poll_once(cfg,b"Z"*32)["state"],"failed")

    def test_idle_poll_does_not_execute(self):
        cfg=client.validate_config(self.settings())
        with patch.object(client,"work_request",return_value={"state":"idle","job":None}),\
             patch.object(client,"execute_job",side_effect=AssertionError):
            self.assertEqual(client.poll_once(cfg,b"Z"*32)["state"],"idle")

if __name__=="__main__":unittest.main()
