#!/usr/bin/env python3
"""Offline progressive saturation protocol tests; no llama.cpp required."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
spec = importlib.util.spec_from_file_location("progressive_benchmark", DIR / "progressive_benchmark.py")
prog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prog)


class ProgressiveTests(unittest.TestCase):
    def test_exact_growth_targets(self):
        self.assertEqual([prog.cycle_target(n) for n in range(1, 6)],
                         [1500, 2625, 4594, 8040, 14069])
        self.assertGreater(prog.cycle_target(25), prog.cycle_target(5))
        self.assertRaises(ValueError, prog.cycle_target, 0)

    def test_model_reported_context(self):
        with mock.patch.object(prog.baseline, "get_json",
                               return_value=(200, {"default_generation_settings": {"n_ctx": 32768}})):
            ctx, source = prog.server_context("http://127.0.0.1:18487")
        self.assertEqual((ctx, source), (32768, "server-/props"))

    def test_missing_server_context_requires_explicit_override(self):
        with mock.patch.object(prog.baseline, "get_json", return_value=(200, {})):
            with self.assertRaisesRegex(ValueError, "/props"):
                prog.server_context("http://127.0.0.1:18482")
        self.assertEqual(prog.server_context("http://127.0.0.1:18482", override=8192),
                         (8192, "user-override"))

    def test_context_bound_target_not_hardcoded(self):
        self.assertEqual(prog.cap_budget(1500, 2048, 1000, 64), (984, "context"))
        self.assertEqual(prog.cap_budget(1500, 32768, 1000, 64), (1500, "target"))
        self.assertEqual(prog.cap_budget(1500, 32768, 1000, 64, 400), (400, "user-limit"))
        self.assertEqual(prog.cap_budget(1500, 100, 200, 64), (0, "context"))

    def test_next_is_automatically_added(self):
        protocol = "Protocol and subject"
        first = prog.make_messages(protocol, [], "checkpoint", 120)
        self.assertEqual(first, [{"role": "user", "content": protocol}])
        later = prog.make_messages(protocol, ["A" * 200], "checkpoint", 120)
        self.assertEqual(later[-1]["content"], "NEXT")
        self.assertEqual(later[-2]["content"], "A" * 120)
        self.assertEqual(later[0]["content"], protocol)

    def test_full_history_retains_turns(self):
        messages = prog.make_messages("protocol", ["first", "second"], "full", 100)
        self.assertEqual(len(messages), 5)
        self.assertEqual([m["content"] for m in messages[1:]],
                         ["first", "NEXT", "second", "NEXT"])

    def test_request_records_real_usage(self):
        response = {
            "choices": [{"finish_reason": "length", "message": {"content": "Valid reply"}}],
            "usage": {"completion_tokens": 888, "prompt_tokens": 700},
            "timings": {"predicted_per_second": 22.3},
        }
        with mock.patch.object(prog.baseline, "get_json", return_value=(200, response)) as api:
            body, metrics = prog.request_cycle("http://127.0.0.1:18487", [], 1500, 5)
        self.assertEqual(body, "Valid reply")
        self.assertEqual(metrics["completion_tokens"], 888)
        self.assertEqual(api.call_args.args[1]["max_tokens"], 1500)

    def test_prompt_file_matches_exact_protocol(self):
        prompt = prog.prompt_for_subject(prog.DEFAULT_PROMPT, "Test science")
        self.assertIn("PROGRESSIVE TOKEN SATURATION PROTOCOL", prompt)
        self.assertIn("**Test science**", prompt)
        self.assertIn("**NEXT**", prompt)

    def test_write_report_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            prog.write_report(path, {"cycles": [{"cycle": 1}]})
            self.assertIn('"cycle": 1', path.read_text())
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
