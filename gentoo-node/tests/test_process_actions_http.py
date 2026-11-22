"""End-to-end HTTP guard tests for the restricted Gentoo process endpoint."""
import base64
import http.client
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import dashboard
import dashboard_live

class ProcessEndpointTests(unittest.TestCase):
    def setUp(self):
        self.server=dashboard.ThreadingHTTPServer(("127.0.0.1",0),dashboard.Handler)
        self.server.secret=dashboard.make_hash("test-gentoo-control-secret-1234")
        self.server.config={"node_id":"gentoo-home"}
        self.server.db="/unused"
        self.server.live_state=dashboard_live.LiveState()
        self.server.live_slots=threading.BoundedSemaphore(4)
        self.server.control_lock=threading.Lock()
        self.server.used_control_tokens={}
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.basic="Basic "+base64.b64encode(
            b"admin:test-gentoo-control-secret-1234").decode()
    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
    def request(self,method,path,headers=None,body=None):
        c=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        try:
            c.request(method,path,body=body,headers={"Host":"sirussofts.com",**(headers or {})})
            resp=c.getresponse()
            return resp.status,json.loads(resp.read()) if resp.getheader("Content-Type","").startswith("application/json") else None
        finally:
            c.close()
    def token(self):
        status,body=self.request("GET","/live-token",{"Authorization":self.basic})
        self.assertEqual(status,200)
        self.assertIn("control_token",body)
        self.assertFalse(dashboard_live.verify_token(self.server.secret,body["control_token"]))
        return body["control_token"]
    def headers(self,token):
        return {"Authorization":self.basic,"Origin":"https://sirussofts.com",
                "Content-Type":"application/json","X-Sirus-Control-Token":token}
    def payload(self):
        return json.dumps({"action":"set_cpu_cores","pid":102,"start_ticks":200,
                           "cores":2,"confirmation":"SET CORES"}).encode()
    def test_rejects_cross_origin_no_auth_and_wrong_scope(self):
        body=self.payload()
        token=self.token()
        with patch.object(dashboard.process_actions,"execute") as execute:
            status,_=self.request("POST","/process/control",
                {**self.headers(token),"Origin":"https://evil.invalid"},body)
            self.assertEqual(status,403)
            status,_=self.request("POST","/process/control",
                {k:v for k,v in self.headers(token).items() if k!="Authorization"},body)
            self.assertEqual(status,401)
            status,_=self.request("POST","/process/control",
                {**self.headers(dashboard_live.new_token(self.server.secret))},body)
            self.assertEqual(status,403)
            execute.assert_not_called()
    def test_one_use_control_token_and_exact_route(self):
        token=self.token()
        with patch.object(dashboard.process_actions,"execute",return_value={
            "ok":True,"notice":"test control requested"}) as action:
            status,payload=self.request("POST","/process/control",
                self.headers(token),self.payload())
            self.assertEqual(status,200)
            self.assertTrue(payload["ok"])
            action.assert_called_once()
            status,_=self.request("POST","/process/control",
                self.headers(token),self.payload())
            self.assertEqual(status,403)
            status,_=self.request("POST","/process/control?extra=1",
                self.headers(self.token()),self.payload())
            self.assertEqual(status,404)
    def test_actual_action_rejects_nonexistent_pid(self):
        token=self.token()
        status,response=self.request("POST","/process/control",
            self.headers(token),self.payload())
        self.assertEqual(status,403)
        self.assertFalse(response["ok"])

if __name__=="__main__":unittest.main()
