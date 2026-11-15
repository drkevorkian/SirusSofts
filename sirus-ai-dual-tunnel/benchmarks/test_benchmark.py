#!/usr/bin/env python3
"""Offline unit tests for the benchmark runner; no GPU or network required."""
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

FILE = pathlib.Path(__file__).with_name("benchmark.py")
spec = importlib.util.spec_from_file_location("sirus_benchmark", FILE)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def test_all_expected_endpoints_are_loopback_only(self):
        self.assertEqual(set(benchmark.WORKERS), {
            "cpu", "amd-vulkan", "nvidia-cuda", "nvidia-vulkan"
        })
        self.assertTrue(all(url.startswith("http://127.0.0.1:")
                            for url in benchmark.WORKERS.values()))

    def test_median_numeric_ignores_missing_timings(self):
        values = [{"wall_seconds": 2}, {"wall_seconds": 4}, {"other": 3}]
        self.assertEqual(benchmark.median_numeric(values, "wall_seconds"), 3)
        self.assertIsNone(benchmark.median_numeric(values, "missing"))

    def test_health_check_accepts_only_ready_worker(self):
        with mock.patch.object(benchmark, "get_json", return_value=(200, {"status": "ok"})):
            self.assertTrue(benchmark.healthy("http://127.0.0.1:18482"))
        with mock.patch.object(benchmark, "get_json", return_value=(200, {"status": "loading model"})):
            self.assertFalse(benchmark.healthy("http://127.0.0.1:18482"))

    def test_response_timing_and_token_count(self):
        reply = {
            "choices": [{"finish_reason": "length", "message": {"content": "Example code"}}],
            "usage": {"prompt_tokens": 60, "completion_tokens": 128},
            "timings": {"prompt_per_second": 220.4, "predicted_per_second": 42.3},
        }
        with mock.patch.object(benchmark, "get_json", return_value=(200, reply)) as api:
            result = benchmark.one_run("http://127.0.0.1:18487", [], 128, 15)
        self.assertEqual(result["generation_tokens_per_second"], 42.3)
        self.assertEqual(result["completion_tokens"], 128)
        self.assertGreaterEqual(result["wall_seconds"], 0)
        self.assertEqual(api.call_args.args[0], "http://127.0.0.1:18487/v1/chat/completions")
        self.assertEqual(api.call_args.args[1]["max_tokens"], 128)

    def test_compare_rejects_incompatible_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            file = pathlib.Path(folder) / "invalid.json"
            file.write_text(json.dumps({"schema": "other"}))
            with self.assertRaises(ValueError):
                benchmark.compare([str(file)])

    def test_empty_completion_cannot_be_reported_as_success(self):
        reply = {"choices": [{"message": {"content": ""}}]}
        with mock.patch.object(benchmark, "get_json", return_value=(200, reply)):
            with self.assertRaisesRegex(ValueError, "empty"):
                benchmark.one_run("http://127.0.0.1:18487", [], 10, 10)


if __name__ == "__main__":
    unittest.main()
