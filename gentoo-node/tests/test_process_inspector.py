"""Synthetic, offline process-ownership telemetry and dashboard exposure."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import process_inspector as inspect
import dashboard

def stat(pid,name,started=30):
    items=["S"]+["0"]*40
    items[11],items[12],items[19]="50","20",str(started)
    return f"{pid} ({name}) "+" ".join(items)

class ProcessInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def add(self,pid,name,uid,rss):
        folder=self.root/str(pid);folder.mkdir()
        (folder/"stat").write_text(stat(pid,name))
        (folder/"status").write_text(f"Uid:\t{uid}\nVmRSS:\t{rss} kB\nThreads:\t3\n")
    def test_ranked_vram_and_unknown_memory(self):
        self.add(1,"init",0,500)
        self.add(50,"llama-server",1001,400)
        self.add(80,"custom",1001,600)
        result=inspect.inventory(self.root,affinity=lambda pid:{0,2},
                                 gpu_provider=lambda:{50:400*1048576})
        self.assertEqual(result["processes"][0]["pid"],50)
        self.assertEqual(result["processes"][0]["vram_bytes"],400*1048576)
        self.assertEqual(result["processes"][0]["importance"],"workload")
        self.assertEqual(next(x for x in result["processes"] if x["pid"]==1)["importance"],"protected")
        self.assertIsNone(next(x for x in result["processes"] if x["pid"]==80)["vram_bytes"])
        self.assertFalse(result["partial"])
    def test_unreadable_process_is_not_silently_complete(self):
        self.add(50,"process",1000,100)
        (self.root/"51").mkdir()
        result=inspect.inventory(self.root,affinity=lambda pid:set(),gpu_provider=lambda:None)
        self.assertTrue(result["partial"])
        self.assertEqual(result["gpu_attribution"],"unknown")
    def test_nvidia_no_shell_and_bounded_timeout(self):
        binary=self.root/"nvidia-smi";binary.write_text("")
        runner=Mock(return_value=Mock(returncode=0,stdout="50, 123\n50, 2\n"))
        self.assertEqual(inspect.nvidia_memory(str(binary),runner=runner),
                         {50:125*1048576})
        self.assertEqual(runner.call_args.kwargs["timeout"],2)
        self.assertNotIn("shell",runner.call_args.kwargs)
    def test_gpu_fd_observation_is_not_vram_measurement(self):
        self.add(50,"worker",1001,20)
        fd=self.root/"50"/"fd"
        fd.mkdir()
        (fd/"3").symlink_to("/dev/dri/renderD128")
        (fd/"4").symlink_to("/dev/nvidia0")
        (fd/"5").symlink_to("/etc/passwd")
        result=inspect.inventory(self.root,affinity=lambda pid:{0,1},
                                 gpu_provider=lambda:None)
        row=result["processes"][0]
        self.assertEqual(row["gpu_handles"],["/dev/dri/renderD128","/dev/nvidia0"])
        self.assertEqual(row["gpu_handle_scan"],"observed")
        self.assertIsNone(row["vram_bytes"])

    def test_dashboard_contains_readonly_table(self):
        html=dashboard.page({"gpus":[],"process_inventory":{"processes":[]}},
             {"node_id":"test","ram_bytes":1000,"cpu_threads":2},
             [],"","").decode()
        self.assertIn('id="live-process-rows"',html)
        self.assertIn("Driver reset is unavailable",html)

if __name__=="__main__": unittest.main()
