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

    def test_memory_excludes_reclaimable_cache(self):
        vm = ("Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
              "Pages free: 1000.\nPages active: 100.\n"
              "Pages inactive: 900.\nPages wired down: 200.\n"
              "Pages occupied by compressor: 50.\n")
        def fake_run(args, timeout=5):
            return vm if args[0].endswith("/vm_stat") else (
                "System-wide free memory percentage: 48" if
                args[0].endswith("/memory_pressure") else "")
        with mock.patch.object(mac, "run", side_effect=fake_run):
            free, pressure = mac.memory_info(8 * 1024 ** 3)
        self.assertEqual(free, 8 * 1024 ** 3 - 350 * 4096)
        self.assertEqual(pressure, "normal")

    def test_memory_missing_probe_is_unknown(self):
        with mock.patch.object(mac, "run", return_value=""):
            self.assertEqual(mac.memory_info(8 * 1024 ** 3),
                             (None, "unknown"))

    def test_dynamic_intel_gpu_is_shared_memory(self):
        import plistlib
        report = plistlib.dumps([{"_items": [{
            "sppci_model": "Intel HD Graphics 6000",
            "spdisplays_vendor": "Intel",
            "spdisplays_vram_dyn": "1536 MB (Dynamic, Max)",
        }]}]).decode()
        with mock.patch.object(mac, "run", return_value=report):
            result = mac.gpu_inventory()
        self.assertEqual(result[0]["vendor"], "0x8086")
        self.assertEqual(result[0]["memory_kind"], "shared")
        self.assertEqual(result[0]["vram_total_bytes"], 1536 * 1024 ** 2)
        self.assertIsNone(result[0]["usage_pct"])

    def test_unknown_temperature_is_not_invented(self):
        with mock.patch.object(mac, "run", return_value=""):
            self.assertIsNone(mac.thermal_limit())

    def test_unknown_job_rejected(self):
        with self.assertRaises(ValueError):
            mac.execute("shell",{},False)


if __name__ == "__main__":
    unittest.main()
