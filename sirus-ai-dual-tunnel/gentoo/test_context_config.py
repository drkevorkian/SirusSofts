#!/usr/bin/env python3
"""Offline tests for context edits; do not access or restart real OpenRC services."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sirus_context_config", HERE / "context-config.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ContextConfigTests(unittest.TestCase):
    def service(self, path, size="2048"):
        path.write_text(
            '#!/sbin/openrc-run\n'
            'name="SirusSofts AI AMD Vulkan worker"\n'
            'command="/usr/bin/env"\n'
            'command_args="-u DISPLAY /opt/sirus-ai/llama.cpp/build-sirus-amd/bin/llama-server '
            '--host 127.0.0.1 --port 18487 --ctx-size ' + size + ' --threads 4"\n'
        )

    def test_exact_replacement_no_duplicate_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sirus-ai-amd"
            self.service(path)
            _, _, _, original, match = mod.read_service("amd-vulkan", Path(tmp))
            changed = mod.rewrite_context(original, match, 0)
            self.assertEqual(changed.count("--ctx-size"), 1)
            self.assertIn("--ctx-size 0", changed)
            self.assertNotIn("--ctx-size 2048", changed)

    def test_adds_explicit_model_native_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sirus-ai-amd"
            self.service(path)
            path.write_text(path.read_text().replace(" --ctx-size 2048", ""))
            _, _, _, original, match = mod.read_service("amd-vulkan", Path(tmp))
            changed = mod.rewrite_context(original, match, 0)
            self.assertIn("--ctx-size 0", changed)

    def test_rejects_unrecognized_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sirus-ai-amd"
            self.service(path)
            path.write_text(path.read_text().replace("18487", "18488"))
            with self.assertRaisesRegex(ValueError, "expected"):
                mod.read_service("amd-vulkan", Path(tmp))

    def test_parse_context_values(self):
        self.assertEqual(mod.parse_size("model"), 0)
        self.assertEqual(mod.parse_size("native"), 0)
        self.assertEqual(mod.parse_size("32768"), 32768)
        self.assertRaises(Exception, mod.parse_size, "-1")


if __name__ == "__main__":
    unittest.main()
