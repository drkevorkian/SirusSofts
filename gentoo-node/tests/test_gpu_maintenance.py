"""Offline GPU accounting and reset-denial regression cases."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gpu_maintenance
import dashboard

class GpuMaintenanceTests(unittest.TestCase):
    def test_single_nvidia_card_has_partial_compute_attribution(self):
        g=[{"vendor":"0x10de","name":"GTX 1660",
            "vram_used_bytes":300*1048576,"vram_total_bytes":600*1048576}]
        p={"processes":[{"pid":32,"vram_bytes":120*1048576,
                         "vram_attribution":"nvidia-compute-only"}]}
        row=gpu_maintenance.overview(g,p,["sirus-ai-nvidia"])[0]
        self.assertEqual(row["reported_nvidia_compute_bytes"],120*1048576)
        self.assertEqual(row["remaining_unattributed_bytes"],180*1048576)
        self.assertTrue(row["worker_reported_running"])
        self.assertFalse(row["reset_action_available"])
        self.assertEqual(row["reset_status"],"disabled")
    def test_amd_does_not_claim_per_process_ownership(self):
        g=[{"vendor":"0x1002","name":"RX 590",
            "vram_used_bytes":200,"vram_total_bytes":400}]
        row=gpu_maintenance.overview(g,{"processes":[]},["sirus-ai-amd"])[0]
        self.assertIsNone(row["remaining_unattributed_bytes"])
        self.assertIsNone(row["reported_nvidia_compute_bytes"])
        self.assertEqual(row["attribution"],"unavailable")
        self.assertTrue(row["worker_reported_running"])
    def test_multi_nvidia_card_not_wrongly_split(self):
        g=[{"vendor":"0x10de","vram_used_bytes":500,
            "vram_total_bytes":1000} for _ in range(2)]
        p={"processes":[{"pid":33,"vram_bytes":200,
                         "vram_attribution":"nvidia-compute-only"}]}
        for row in gpu_maintenance.overview(g,p,[]):
            self.assertIsNone(row["reported_nvidia_compute_bytes"])
            self.assertEqual(row["attribution"],"ambiguous")
    def test_inconsistent_telemetry_fails_closed(self):
        g=[{"vendor":"0x10de","vram_used_bytes":20,
            "vram_total_bytes":200}]
        p={"processes":[{"pid":33,"vram_bytes":30,
                         "vram_attribution":"nvidia-compute-only"}]}
        row=gpu_maintenance.overview(g,p,[])[0]
        self.assertIsNone(row["remaining_unattributed_bytes"])
        self.assertEqual(row["attribution"],"inconsistent")
        self.assertTrue(any("exceed" in r for r in row["reset_blockers"]))
    def test_missing_data_never_permits_reset(self):
        for sample in ([],[{"vendor":"0x10de"}],
                       [{"vendor":"0x1002","vram_used_bytes":-1,"vram_total_bytes":20}]):
            for row in gpu_maintenance.overview(sample,{},[]):
                self.assertFalse(row["reset_action_available"])
    def test_dashboard_contains_maintenance_view(self):
        html=dashboard.page({"gpus":[]},{"node_id":"gentoo-home",
                  "cpu_threads":4,"ram_bytes":1000},[],"","").decode()
        self.assertIn('id="live-gpu-maintenance"',html)

if __name__=="__main__":
    unittest.main()
