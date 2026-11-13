import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('agent', Path(__file__).resolve().parents[1] / 'agent.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

class Tests(unittest.TestCase):
    def test_listener_does_not_retain_ip(self):
        src = 'LISTEN 0 128 74.208.1.22:8443 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 128 [::1]:5432 [::]:*'
        out = a.parse_listeners(src)
        self.assertEqual(out, [
            {'port': 22, 'bind': 'wildcard', 'service_hint': 'SSH'},
            {'port': 5432, 'bind': 'loopback', 'service_hint': 'PostgreSQL'},
            {'port': 8443, 'bind': 'specific-interface', 'service_hint': 'Plesk panel'}])
        self.assertNotIn('74.208', str(out))
    def test_ssh_is_allowlisted(self):
        result = a.parse_ssh_config('permitrootlogin no\nauthorizedkeysfile /home/user/.ssh/keys\npasswordauthentication yes\n')
        self.assertEqual(result, {'permitrootlogin':'no','passwordauthentication':'yes'})
    def test_plesk_version_does_not_copy_licence(self):
        self.assertEqual(a.plesk_version('Product version: Plesk Obsidian 18.0.66\nLicense key: SECRET'), '18.0.66')
    def test_private_repo_required(self):
        with mock.patch.object(a, 'github_api', return_value=(200, {'private':False})):
            with self.assertRaisesRegex(ValueError, 'private'):
                a.upload_private_report({'status':'ok'}, 'owner/public-repo', 'fake-token')
    def test_save_sets_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report' / 'latest.json'
            a.write_private_json(str(path), {'schema': 'test'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
    def test_upload_requires_valid_repo(self):
        with self.assertRaises(ValueError):
            a.upload_private_report({}, '../bad', 'fake-token')
    def test_local_collect_never_requires_network(self):
        def fakecmd(cmd, timeout=8):
            if cmd[:2] == ['ss', '-H']:
                return (0, 'LISTEN 0 128 127.0.0.1:8443 0.0.0.0:*')
            return (-1, '')
        with mock.patch.object(a, 'command', side_effect=fakecmd):
            report = a.collect()
            self.assertIn({'port':8443, 'bind':'loopback','service_hint':'Plesk panel'}, report['listeners'])
            self.assertNotIn('127.0.0.1', str(report))

if __name__ == '__main__': unittest.main()
