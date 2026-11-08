"""Validation for the copy/paste PuTTY workflow and non-destructive Hestia CMS launch."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
UBUNTU = ROOT / "deploy" / "ubuntu24"


class UbuntuHestiaBootstrapTests(unittest.TestCase):
    def test_putty_guide_uses_real_code_blocks_and_public_repository(self):
        document = (UBUNTU / "PUTTY-SSH-QUICKSTART.md").read_text()
        self.assertIn("git clone --single-branch --branch feature/cms-vhost-distributed-nodes", document)
        self.assertIn("https://github.com/drkevorkian/SirusSofts.git", document)
        self.assertIn("cd /root/SirusSofts", document)
        self.assertGreaterEqual(document.count(chr(96) * 3 + "bash"), 4)
        self.assertNotIn(chr(92) + chr(96) * 3, document)
        for command in ("01-preflight.sh --strict", "02-install-hestia.sh --download",
                        "02-install-hestia.sh --install", "03-install-extensions.sh --install",
                        "05-lockdown-panel.sh --apply", "06-deploy-cms-hestia.sh --deploy",
                        "07-audit-cms-routing.sh --live"):
            self.assertIn(command, document)
        self.assertIn("SSH", document)
        self.assertIn("PuTTY", document)

    def test_hestia_default_index_compared_exactly(self):
        script = (UBUNTU / "06-deploy-cms-hestia.sh").read_text()
        self.assertIn('cmp -s "$DOCROOT/index.html" <(sed "s/%domain%/$domain/g" "$SKEL")', script)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            skel = path / "index.html.template"
            deployed = path / "index.html"
            skel.write_text("<h1>We're working on it!</h1>\nDomain: %domain%\n")
            deployed.write_text("<h1>We're working on it!</h1>\nDomain: sirussofts.org\n")
            cmd = ["bash", "-c",
                   'cmp -s "$1" <(sed "s/%domain%/$3/g" "$2")',
                   "bash", str(deployed), str(skel), "sirussofts.com"]
            self.assertEqual(subprocess.run(cmd).returncode, 0)
            deployed.write_text("<h1>We're working on it!</h1>\nHACKED\n")
            self.assertNotEqual(subprocess.run(cmd).returncode, 0)

    def test_com_primary_and_optional_org_alias_are_supported(self):
        deploy = (UBUNTU / "06-deploy-cms-hestia.sh").read_text()
        audit = (UBUNTU / "07-audit-cms-routing.sh").read_text()
        core = (ROOT / "CMS" / "app" / "core.php").read_text()
        self.assertIn("sirussofts.com", deploy)
        self.assertIn("sirussofts.org", deploy)
        self.assertIn('hostnames=("$domain")', audit)
        self.assertIn('--also-domain', audit)
        self.assertIn("const SIRUS_CMS_DOMAIN = 'sirussofts.com';", core)
        self.assertNotIn("for hostname in sirussofts.org sirussofts.com; do", audit)

    def test_installer_requires_interactive_hash_and_hestia_ownership(self):
        install = (UBUNTU / "02-install-hestia.sh").read_text()
        self.assertIn("sha256sum", install)
        self.assertIn("INSTALL HESTIA", install)
        self.assertIn("--multiphp '8.3,8.4'", install)
        deploy = (UBUNTU / "06-deploy-cms-hestia.sh").read_text()
        self.assertIn('HTTPS_GUARD="$CONF/nginx.ssl.conf_siruscms"', deploy)
        self.assertIn('HTTP_GUARD="$CONF/nginx.conf_siruscms"', deploy)
        self.assertIn('runuser -u "$user" -- php8.3', deploy)
        self.assertNotIn("systemctl enable --now sirus-nginx-vhost", deploy)


if __name__ == "__main__":
    unittest.main()
