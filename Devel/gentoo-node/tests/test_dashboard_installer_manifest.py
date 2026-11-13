"""Prevent Gentoo dashboard installers from omitting verified assets.

The production failure occurred because an added health-check Python file was
parsed from /opt but absent from the installation/backup/rollback file lists.
This test audits the actual deployment script, not only Python syntax.
"""
from pathlib import Path
import re
import shlex
import subprocess
import unittest


NODE_DIR = Path(__file__).resolve().parents[1]
INSTALLER = NODE_DIR / "install-dashboard-live.sh"
REQUIRED = {
    "dashboard.py", "dashboard_live.py", "dashboard_live.js", "dashboard_health.py"
}


class DashboardInstallerManifestTests(unittest.TestCase):
    def test_all_required_files_use_one_shared_manifest(self):
        script = INSTALLER.read_text(encoding="utf-8")
        match = re.search(r"(?m)^dashboard_assets=\(([^)]+)\)$", script)
        self.assertIsNotNone(match, "Missing single, shared dashboard asset manifest")
        assets = shlex.split(match.group(1))
        self.assertEqual(set(assets), REQUIRED)
        self.assertEqual(len(assets), len(REQUIRED), "Duplicate manifest entry")
        for name in assets:
            self.assertTrue((NODE_DIR / name).is_file(), f"Missing source asset: {name}")

        # Each operational stage must iterate the same exact list:
        # preflight source, installed-symlink check, backup, rollback, install.
        self.assertEqual(
            script.count('for file in "${dashboard_assets[@]}"; do'), 1
        )
        self.assertEqual(
            script.count('for name in "${dashboard_assets[@]}"; do'), 4
        )
        self.assertNotIn("for name in dashboard.py dashboard_live.py dashboard_live.js", script)
        self.assertIn('cmp -s -- "$here/$name" "$app/$name"', script)
        self.assertIn('PYTHONDONTWRITEBYTECODE=1 runuser -u sirusnode', script)
        self.assertIn('"dashboard_health.py"', script)
        self.assertIn('python3 -B "$here/dashboard_health.py" --port 18080 --wait 30', script)

    def test_check_really_validates_all_source_assets(self):
        subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
        result = subprocess.run(
            ["bash", str(INSTALLER), "--check"],
            capture_output=True, text=True, timeout=15
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS bounded Gentoo readiness probe", result.stdout)


if __name__ == "__main__":
    unittest.main()
