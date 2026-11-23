import importlib.util
import pathlib
import unittest
from unittest import mock
spec = importlib.util.spec_from_file_location("proc", pathlib.Path(__file__).resolve().parents[1]/"process_manager.py")
proc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proc)


class ProcessTests(unittest.TestCase):
    def test_unapproved_app_fails_closed(self):
        with self.assertRaises(PermissionError):
            proc.graceful_quit("com.apple.finder", ["com.example.editor"], interactive=True)

    def test_no_unattended_quit(self):
        with self.assertRaises(PermissionError):
            proc.graceful_quit("com.example.editor", ["com.example.editor"], interactive=False)

    def test_inventory_never_actionable(self):
        with mock.patch.object(proc.subprocess, "run") as call:
            call.return_value.stdout = "  21  501  1024 /Applications/Safari.app/Contents/MacOS/Safari\n"
            rows=proc.inventory()
            self.assertFalse(rows[0]["actionable"])
            self.assertEqual(rows[0]["rss_bytes"], 1048576)


if __name__ == "__main__": unittest.main()
