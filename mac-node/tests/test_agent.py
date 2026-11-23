"""Portable validation of Mac worker security and payload shape."""
import importlib.util
import pathlib
import unittest
from unittest import mock

AGENT = pathlib.Path(__file__).resolve().parents[1] / "agent.py"
spec = importlib.util.spec_from_file_location("sirus_mac", AGENT)
mac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mac)


class AgentTests(unittest.TestCase):
    def test_tls_only(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as temp:
            f = pathlib.Path(temp)/"config.json"
            c = {"node_id": "mac-research-01", "coordinator_url": "http://invalid",
                 "work_url": "https://sirussofts.com/node-work.php"}
            f.write_text(json.dumps(c))
            with self.assertRaises(ValueError):
                mac.load_config(f)

    def test_cpu_budget_rejects_above_90(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as temp:
            f = pathlib.Path(temp)/"config.json"
            f.write_text(json.dumps({"node_id":"mac-research-01",
                "coordinator_url":"https://sirussofts.com/node-heartbeat.php",
                "work_url":"https://sirussofts.com/node-work.php",
                "cpu_target_percent":99}))
            with self.assertRaises(ValueError):
                mac.load_config(f)

    def test_snapshot_schema(self):
        with mock.patch.object(mac, "hardware", return_value={"arch":"x86_64","model":"MacBookAir7,2","ram":8589934592,"cpus":4}), \
             mock.patch.object(mac, "gpu_inventory", return_value=[]), \
             mock.patch.object(mac, "memory_info", return_value=(4294967296,"normal")), \
             mock.patch.object(mac, "power_info", return_value=("AC",90)), \
             mock.patch.object(mac, "uptime", return_value=1000), \
             mock.patch.object(mac, "cpu_percent", return_value=20):
            payload=mac.snapshot({"node_id":"mac-research-01"})
            self.assertEqual(payload["os"],"macos")
            self.assertEqual(payload["init"],"launchd")
            self.assertEqual(payload["metrics"]["memory_used_bytes"],4294967296)
            self.assertEqual(payload["metrics"]["mac"]["arch"],"x86_64")

    def test_unknown_job_rejected(self):
        with self.assertRaises(ValueError):
            mac.execute("shell",{},False)


if __name__ == "__main__":
    unittest.main()
