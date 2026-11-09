import importlib.util
import json
from pathlib import Path
import tempfile
import os
import sys
import unittest
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1] / "tunnel_supervisor.py"
spec=importlib.util.spec_from_file_location("sirus_tunnel_supervisor",FILE)
supervisor=importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class TunnelTests(unittest.TestCase):
    def settings(self):
        return {
            "remote_host":"74.208.40.187","remote_user":"sirus-tunnel",
            "ssh_port":22,"reverse_port":22122,"local_target_port":22,
            "key_file":"/etc/sirus-node/tunnel_ed25519",
            "known_hosts_file":"/etc/sirus-node/known_hosts",
            "alive_interval":15,"alive_count":3
        }

    def test_no_public_binding_and_no_password_auth(self):
        cfg=supervisor.validate_config(self.settings())
        cmd=supervisor.ssh_command(cfg)
        self.assertIn("127.0.0.1:22122:127.0.0.1:22",cmd)
        self.assertNotIn("0.0.0.0:22122:127.0.0.1:22",cmd)
        self.assertIn("BatchMode=yes",cmd)
        self.assertIn("StrictHostKeyChecking=yes",cmd)
        self.assertIn("ExitOnForwardFailure=yes",cmd)
        self.assertIn("ServerAliveInterval=15",cmd)
        self.assertIn("ServerAliveCountMax=3",cmd)
        self.assertIn("-N",cmd)
        self.assertIn("-T",cmd)

    def test_untrusted_config_rejected(self):
        for field,value in [
            ("remote_host","-oProxyCommand=bad"),("remote_host","evil/path"),
            ("remote_user","root;echo hi"),("alive_interval",0),
            ("reverse_port",-1),("ssh_port","22"),
            ("local_target_port",65536),("key_file","relative"),
        ]:
            cfg=self.settings()
            cfg[field]=value
            with self.assertRaises((ValueError,PermissionError),msg=field):
                supervisor.validate_config(cfg)

    def test_reject_key_symlink_and_public_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            key=Path(d)/"key"
            host=Path(d)/"known_hosts"
            key.write_text("dummy secret")
            host.write_text("host ssh-ed25519 AAAA")
            cfg=self.settings()
            cfg["key_file"],cfg["known_hosts_file"]=str(key),str(host)
            key.chmod(0o644)
            host.chmod(0o600)
            with self.assertRaises(PermissionError):
                supervisor.validate_private_files(cfg)
            key.chmod(0o600)
            supervisor.validate_private_files(cfg)
            link=Path(d)/"linked"
            link.symlink_to(key)
            cfg["key_file"]=str(link)
            with self.assertRaises(PermissionError):
                supervisor.validate_private_files(cfg)

    def test_check_has_no_network_or_file_access(self):
        with patch.object(supervisor.Path,"read_text",side_effect=AssertionError("file read")):
            self.assertEqual(supervisor.main(["--check"]),0)

if __name__=="__main__":
    unittest.main()
