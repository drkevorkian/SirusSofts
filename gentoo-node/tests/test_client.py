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

    def test_portage_update_check_never_installs(self):
        from types import SimpleNamespace
        with patch.object(client.shutil,"which",return_value="/usr/bin/emerge"), \\
             patch.object(client.subprocess,"run",return_value=SimpleNamespace(
                 returncode=0, stdout="[ebuild  U ] sys-apps/foo-2\\n", stderr="")) as run:
            data=client.execute_job("package_update_check")
        self.assertTrue(data["simulation_only"])
        self.assertEqual(data["upgrade_count"],1)
        command=run.call_args.args[0]
        self.assertIn("--pretend",command)
        self.assertNotIn("--ask",command)
        self.assertNotIn("--sync",command)

    def test_nvidia_nvml_fallback_populates_card_model_usage_temp_and_vram(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            drm=Path(folder)
            vendor=drm/"card0"/"device"/"vendor"
            vendor.parent.mkdir(parents=True)
            vendor.write_text("0x10de\n")
            nvml=[{"vendor":"0x10de","name":"NVIDIA GeForce RTX",
                   "usage_pct":8.0,"temp_c":41.0,
                   "vram_used_bytes":1048576,"vram_total_bytes":8589934592}]
            with patch.object(client,"nvidia_smi_samples",return_value=[]), \
                 patch.object(client,"nvml_samples",return_value=nvml), \
                 patch.object(client,"pci_model",return_value=None):
                result=client.gpu_devices(drm, drm / 'empty-pci')
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]["name"],"NVIDIA GeForce RTX")
            self.assertEqual(result[0]["usage_pct"],8.0)
            self.assertEqual(result[0]["temp_c"],41.0)
            self.assertEqual(result[0]["vram_total_bytes"],8589934592)

    def test_nvidia_sysfs_detection_survives_missing_driver_tools(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            drm=Path(folder)
            vendor=drm/"card0"/"device"/"vendor"
            vendor.parent.mkdir(parents=True)
            vendor.write_text("0x10de\n")
            with patch.object(client,"nvidia_smi_samples",return_value=[]), \
                 patch.object(client,"nvml_samples",return_value=[]), \
                 patch.object(client,"pci_model",return_value=None):
                result=client.gpu_devices(drm, drm / 'empty-pci')
            self.assertEqual(result[0]["vendor"],"0x10de")
            self.assertIsNone(result[0]["usage_pct"])
            self.assertIsNone(result[0]["temp_c"])


    def test_pci_inventory_detects_amd_without_drm_and_nvidia_with_drm(self):
        """KDE can see both PCI GPUs even when the AMD DRM card is missing."""
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            pci = base / "sys-bus-pci"
            drm = base / "drm"
            amd = pci / "0000:08:00.0"
            nvidia = pci / "0000:09:00.0"
            audio = pci / "0000:08:00.1"
            for location, vendor, klass, device_id in (
                (amd, "0x1002", "0x030000", "0x67df"),
                (nvidia, "0x10de", "0x030000", "0x2184"),
                (audio, "0x1002", "0x040300", "0xaaf0"),
            ):
                location.mkdir(parents=True)
                (location / "vendor").write_text(vendor + "\n")
                (location / "class").write_text(klass + "\n")
                (location / "device").write_text(device_id + "\n")
            # NVIDIA is also listed through DRM, but must not be duplicated.
            card = drm / "card0"
            card.mkdir(parents=True)
            (card / "device").symlink_to(nvidia, target_is_directory=True)
            amd_hwmon = amd / "hwmon" / "hwmon0"
            amd_hwmon.mkdir(parents=True)
            (amd_hwmon / "temp1_input").write_text("41000\n")
            (amd / "gpu_busy_percent").write_text("19\n")
            nvidia_sample = [{"vendor":"0x10de", "name":"GeForce GTX 1660",
                "usage_pct":None, "temp_c":None, "vram_used_bytes":None,
                "vram_total_bytes":None}]
            names = {"0000:08:00.0": "Ellesmere [Radeon RX 590 series]",
                     "0000:09:00.0": "TU116 [GeForce GTX 1660]"}
            with patch.object(client,"pci_model",
                              side_effect=lambda device: names.get(device.name)), \
                 patch.object(client,"nvidia_smi_samples",
                              return_value=nvidia_sample):
                result = client.gpu_devices(drm, pci)
                vendors = client.gpu_vendors(drm, pci)
            self.assertEqual(len(result), 2)
            self.assertEqual(vendors, ["0x1002", "0x10de"])
            self.assertEqual([item["vendor"] for item in result],
                             ["0x1002", "0x10de"])
            self.assertIn("Radeon RX 590", result[0]["name"])
            self.assertEqual(result[0]["temp_c"], 41.0)
            self.assertEqual(result[0]["usage_pct"], 19.0)
            self.assertEqual(result[1]["name"], "GeForce GTX 1660")

    def test_pci_only_gpu_no_driver_never_fabricates_performance(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pci = root / "pci"
            card = pci / "0000:08:00.0"
            card.mkdir(parents=True)
            (card / "vendor").write_text("0x1002\n")
            (card / "class").write_text("0x030000\n")
            (card / "device").write_text("0x67df\n")
            with patch.object(client,"pci_model",return_value=None), \
                 patch.object(client,"nvidia_smi_samples",return_value=[]), \
                 patch.object(client,"nvml_samples",return_value=[]):
                result = client.gpu_devices(root / "no-drm", pci)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["name"], "AMD GPU (0x67df)")
            self.assertIsNone(result[0]["usage_pct"])
            self.assertIsNone(result[0]["temp_c"])
            self.assertIsNone(result[0]["vram_total_bytes"])

if __name__=="__main__":unittest.main()
