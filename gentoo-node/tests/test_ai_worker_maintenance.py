"""Tests for root-console-only GPU service lifecycle policy, with mocked OpenRC."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ai_worker_maintenance as tool

def response(code,text):
    return type("Result",(),{"returncode":code,"stdout":text,"stderr":""})()

class MaintenanceTests(unittest.TestCase):
    def test_readonly_plan_fixed_service(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            return response(0,"started")
        info=tool.plan("amd-vulkan",runner=runner)
        self.assertEqual(info["service"],"sirus-ai-amd")
        self.assertFalse(info["website_enabled"])
        self.assertFalse(info["driver_reset_enabled"])
        self.assertEqual(calls,[[tool.SERVICE_BINARY,"sirus-ai-amd","status"]])
        with self.assertRaises(tool.MaintenanceError):tool.plan("../other")
    def test_no_web_nonroot_or_missing_tty(self):
        runner=Mock()
        for options in ({"euid":lambda:1001,"isatty":lambda _:True},
                        {"euid":lambda:0,"isatty":lambda _:False}):
            with self.assertRaises(tool.MaintenanceError):
                tool.change("amd-vulkan","stop",runner=runner,**options)
        runner.assert_not_called()
    def test_ambiguous_status_blocks_without_mutation(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            return response(1,"OpenRC cannot determine status")
        with self.assertRaises(tool.MaintenanceError):
            tool.change("amd-vulkan","stop",runner=runner,
                        euid=lambda:0,isatty=lambda _:True,
                        input_func=lambda _: "STOP sirus-ai-amd",
                        audit=lambda _:None)
        self.assertEqual(len(calls),1)
    def test_stop_needs_exact_phrase_and_audits_before_after(self):
        calls=[]; audit=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            if argv[-1]=="status":
                return response(0,"started") if len(calls)<3 else response(1,"stopped")
            if argv[-1]=="stop":
                return response(0,"stopped")
            raise AssertionError("Unexpected OpenRC command")
        with self.assertRaises(tool.MaintenanceError):
            tool.change("amd-vulkan","stop",runner=runner,
                       euid=lambda:0,isatty=lambda _:True,
                       input_func=lambda _:"no",audit=audit.append)
        self.assertEqual(len(calls),1)
        result=tool.change("amd-vulkan","stop",runner=runner,
                       euid=lambda:0,isatty=lambda _:True,
                       input_func=lambda _:"STOP sirus-ai-amd",audit=audit.append)
        self.assertTrue(result["ok"])
        self.assertEqual([a["phase"] for a in audit],["requested","completed"])
        self.assertEqual(calls[-2:],[[tool.SERVICE_BINARY,"sirus-ai-amd","stop"],
                                     [tool.SERVICE_BINARY,"sirus-ai-amd","status"]])
    def test_audit_failure_prevents_stop(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            return response(0,"running")
        def refuse(_):
            raise tool.MaintenanceError("Cannot audit")
        with self.assertRaises(tool.MaintenanceError):
            tool.change("nvidia-cuda","stop",runner=runner,euid=lambda:0,
                        isatty=lambda _:True,
                        input_func=lambda _:"STOP sirus-ai-nvidia",audit=refuse)
        self.assertEqual(calls,[[tool.SERVICE_BINARY,"sirus-ai-nvidia","status"]])
    def test_root_only_regular_audit_log(self):
        import os
        if os.geteuid()!=0:
            self.skipTest("root-owned audit file test requires root")
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"audit.jsonl"
            tool.append_audit({"action":"plan","service":"test"},path=path)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            linked=Path(directory)/"link"
            linked.symlink_to(path)
            with self.assertRaises(tool.MaintenanceError):
                tool.append_audit({"action":"stop"},path=linked)

if __name__=="__main__":
    unittest.main()
