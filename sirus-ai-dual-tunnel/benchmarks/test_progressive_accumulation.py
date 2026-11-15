#!/usr/bin/env python3
"""Offline integration tests for multi-completion progressive token saturation."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
spec = importlib.util.spec_from_file_location(
    "progressive_accumulation_under_test", DIR / "progressive_benchmark.py")
prog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prog)


def settings(mode="accumulate"):
    return argparse.Namespace(
        cycles=3, mode=mode, context_override=None, subject="Scientific test",
        history="checkpoint", checkpoint_chars=128, max_output_tokens=None,
        max_cycle_seconds=900, max_worker_seconds=3600,
        max_segments_per_cycle=0, stall_limit=3, min_progress_tokens=32,
        save_transcripts=False, output="/unused", timeout=20,
    )


def response(tokens, text):
    return text, {
        "completion_tokens": tokens, "prompt_tokens": 200,
        "wall_seconds": 0.05, "generation_tokens_per_second": 40.0,
        "prompt_tokens_per_second": 100, "finish_reason": "stop",
        "sha256": "a" * 64, "characters": len(text),
    }


class AccumulationTests(unittest.TestCase):
    def run_fake(self, mock_call, args=None, estimated_tokens=(50, 12), target_value=10):
        args = args or settings()
        with tempfile.TemporaryDirectory() as directory:
            report_file = Path(directory) / "progress.json"
            with mock.patch.object(prog.baseline, "healthy", return_value=True), \
                    mock.patch.object(prog, "server_context", return_value=(8192, "server-/props")), \
                    mock.patch.object(prog, "cycle_target", side_effect=lambda _: target_value), \
                    mock.patch.object(prog, "approx_prompt_tokens", return_value=estimated_tokens), \
                    mock.patch.object(prog, "request_cycle", side_effect=mock_call), \
                    mock.patch.object(prog, "report_path", return_value=report_file):
                status = prog.run_worker("amd-vulkan", args, "PROGRESSIVE TOKEN PROTOCOL")
            data = json.loads(report_file.read_text())
            return status, data

    def test_continues_early_stop_in_same_cycle_then_sends_next(self):
        calls = []
        def fake(_, messages, max_tokens, timeout):
            calls.append((messages, max_tokens))
            number = len(calls)
            return response(4 if number % 2 else 6, "substantial analysis " + str(number))
        status, data = self.run_fake(fake)
        self.assertEqual(status, 0)
        self.assertEqual(data["state"], "completed")
        self.assertEqual(len(calls), 6)
        self.assertEqual([c["actual_completion_tokens"] for c in data["cycles"]],
                         [10, 10, 10])
        self.assertTrue(all(c["status"] == "target-reached" for c in data["cycles"]))
        self.assertEqual([len(c["segments"]) for c in data["cycles"]], [2, 2, 2])
        self.assertEqual([args for _, args in calls], [10, 6, 10, 6, 10, 6])
        # A continuation is within response 1. NEXT only starts response 2.
        self.assertIn("CONTINUE RESPONSE 1, PART 2", calls[1][0][-1]["content"])
        self.assertEqual(calls[2][0][-1]["content"], "NEXT")

    def test_one_response_mode_preserved(self):
        calls = []
        def fake(_, messages, max_tokens, timeout):
            calls.append(messages)
            return response(4, "meaningful but short response " + str(len(calls)))
        status, data = self.run_fake(fake, settings("single"))
        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual([c["status"] for c in data["cycles"]],
                         ["early-stop"] * 3)
        self.assertEqual(data["state"], "completed-with-limits")

    def test_repeated_output_is_detected_not_counted_as_full_target(self):
        args = settings()
        args.stall_limit = 2
        status, data = self.run_fake(
            lambda *_: response(10, "same response repeated"),
            args=args, target_value=50,
        )
        self.assertEqual(status, 0)
        self.assertEqual(data["state"], "completed-with-limits")
        self.assertTrue(all(c["status"] == "repeated-output" for c in data["cycles"]))
        self.assertEqual([c["actual_completion_tokens"] for c in data["cycles"]],
                         [20, 20, 20])
        self.assertEqual([len(c["segments"]) for c in data["cycles"]], [2, 2, 2])

    def test_low_progress_stops_cycle(self):
        args = settings()
        args.stall_limit = 2
        number = [0]
        def fake(*_):
            number[0] += 1
            return response(2, "new response part " + str(number[0]))
        status, data = self.run_fake(fake, args=args)
        self.assertEqual(status, 0)
        self.assertEqual(data["state"], "completed-with-limits")
        self.assertTrue(all(c["status"] == "low-progress" for c in data["cycles"]))
        self.assertEqual([len(c["segments"]) for c in data["cycles"]], [2, 2, 2])

    def test_no_context_room_causes_no_model_requests(self):
        fn = mock.Mock()
        status, data = self.run_fake(fn, estimated_tokens=(8192, 24))
        self.assertEqual(status, 0)
        fn.assert_not_called()
        self.assertTrue(all(c["status"] == "context-exhausted" for c in data["cycles"]))

    def test_interrupt_aborts_remaining_cycles(self):
        def interrupt(*_):
            raise KeyboardInterrupt
        status, data = self.run_fake(interrupt)
        self.assertEqual(status, 130)
        self.assertEqual(data["state"], "interrupted")
        self.assertEqual(len(data["cycles"]), 1)
        self.assertEqual(data["cycles"][0]["status"], "interrupted")

    def test_continuation_checkpoint_uses_actual_segment_count(self):
        parts = [{"body": "the last section", "tokens": 120}]
        msg = prog.make_segment_messages(
            "protocol", [], parts, "checkpoint", 128, 4, 14069, 8000, 9)
        self.assertIn("RESPONSE 4, PART 10", msg[-1]["content"])
        self.assertIn("8000", msg[-1]["content"])


if __name__ == "__main__":
    unittest.main()
