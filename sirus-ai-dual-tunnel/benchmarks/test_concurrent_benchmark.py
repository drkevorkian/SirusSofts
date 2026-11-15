#!/usr/bin/env python3
"""Offline checks for simultaneous benchmark aggregation and worker isolation."""
import time
import unittest
from unittest import mock

import concurrent_benchmark as run


class ConcurrentBenchmarkTests(unittest.TestCase):
    def test_round_launches_every_worker_and_counts_tokens(self):
        def dummy_run(url, messages, max_tokens, timeout):
            time.sleep(0.01)
            return {"completion_tokens": 20, "wall_seconds": 0.01,
                    "generation_tokens_per_second": 50}
        with mock.patch.object(run.baseline, "one_run", side_effect=dummy_run) as fn:
            result = run.measure_round(
                ["cpu", "amd-vulkan", "nvidia-cuda"], [], 80, 10)
        self.assertEqual(fn.call_count, 3)
        self.assertTrue(result["all_succeeded"])
        self.assertEqual(result["generation_tokens"], 60)
        self.assertEqual({x["worker"] for x in result["workers"]},
                         {"cpu", "amd-vulkan", "nvidia-cuda"})
        self.assertGreater(result["aggregate_generation_tokens_per_wall_second"], 0)

    def test_errors_do_not_get_counted_as_success(self):
        def dummy_run(url, messages, max_tokens, timeout):
            if url.endswith("18487"):
                raise OSError("worker disconnected")
            return {"completion_tokens": 25}
        with mock.patch.object(run.baseline, "one_run", side_effect=dummy_run):
            result = run.measure_round(["cpu", "amd-vulkan"], [], 64, 10)
        self.assertFalse(result["all_succeeded"])
        self.assertEqual(result["generation_tokens"], 25)
        failed = [x for x in result["workers"] if not x["ok"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["worker"], "amd-vulkan")
        self.assertIn("disconnected", failed[0]["error"])

    def test_empty_median(self):
        self.assertIsNone(run.median_numeric([]))


if __name__ == "__main__":
    unittest.main()
