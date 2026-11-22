"""Offline tests for narrow, unprivileged process control authorization."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import process_actions
import process_inspector
from test_process_inspector import stat

class ProcessActionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.proc=self.root/"128";self.proc.mkdir()
        (self.proc/"stat").write_text(stat(128,"worker",12345))
        (self.proc/"status").write_text("Uid:\t1002\nVmRSS:\t100 kB\n")
        (self.proc/"cmdline").write_bytes(b"worker\0")
        self.affinity=Mock(return_value={0,2,4,6})
        self.set_affinity=Mock()
        self.openfd=Mock(return_value=15)
        self.signal=Mock()
    def req(self,action="set_cpu_cores",cores=2):
        return {"action":action,"pid":128,"start_ticks":12345,
                "confirmation":"SET CORES" if action=="set_cpu_cores" else "TERMINATE",
                "cores":cores}
    def call(self,req,uid=1002):
        return process_actions.execute(req,proc_root=self.root,uid=uid,
               affinity=self.affinity,set_affinity=self.set_affinity,
               pidfd_open=self.openfd,pidfd_signal=self.signal)
    def test_affinity_is_narrowed_and_no_command_is_executed(self):
        out=self.call(self.req())
        self.assertEqual(out["allowed_logical_cpus"],[0,2])
        self.set_affinity.assert_called_once_with(128,{0,2})
    def test_wrong_identity_and_cross_user_are_forbidden(self):
        for changes in ({"start_ticks":12346},{"pid":1},
                        {"confirmation":"yes"},{"cores":9},{"action":"gpu_reset"}):
            candidate=self.req();candidate.update(changes)
            with self.assertRaises(process_actions.ControlError):self.call(candidate)
        for uid in (0,1003):
            with self.assertRaises(process_actions.ControlError):self.call(self.req(),uid)
        self.signal.assert_not_called()
    def test_protect_dashboard_and_root(self):
        (self.proc/"cmdline").write_bytes(b"/opt/sirus-gentoo-node/dashboard.py\0")
        with self.assertRaises(process_actions.ControlError):self.call(self.req())
        (self.proc/"cmdline").write_bytes(b"worker\0")
        (self.proc/"status").write_text("Uid:\t0\n")
        with self.assertRaises(process_actions.ControlError):self.call(self.req())
    def test_workload_requires_stronger_confirmation(self):
        (self.proc/"stat").write_text(stat(128,"llama-server",12345))
        with self.assertRaises(process_actions.ControlError):self.call(self.req())
        chosen=self.req()
        chosen["confirmation"]="SET CORES WORKLOAD"
        self.assertTrue(self.call(chosen)["ok"])

if __name__=="__main__":unittest.main()
