import hashlib
import hmac
import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent


class AgentTests(unittest.TestCase):
    def config(self):
        return {"node_id": "gentoo-home",
                "coordinator_url": "https://sirussofts.org/node-heartbeat.php",
                "key_file": "/tmp/heartbeat.key", "period_seconds": 60}

    def test_https_required(self):
        c = self.config()
        c["coordinator_url"] = "http://sirussofts.org/node-heartbeat.php"
        with self.assertRaises(ValueError):
            agent.validate_config(c)
        c["coordinator_url"] = "https://sirussofts.org/admin"
        with self.assertRaises(ValueError):
            agent.validate_config(c)

    def test_signed_wire_format(self):
        with patch.object(agent, "memory_bytes", return_value=32 * 1024**3), \
             patch.object(agent, "gpu_vendors", return_value=["0x1002", "0x10de"]), \
             patch.object(agent, "distro", return_value="gentoo"), \
             patch.object(agent, "init_system", return_value="openrc"):
            key = b"K" * 32
            request = agent.signed_request(agent.validate_config(self.config()), key, 1794154000)
            payload = json.loads(request.data)
            self.assertEqual(payload["node_id"], "gentoo-home")
            self.assertEqual(payload["ram_bytes"], 32 * 1024**3)
            self.assertEqual(payload["gpu_vendors"], ["0x1002", "0x10de"])
            self.assertEqual(payload["sent_at"], 1794154000)
            self.assertEqual(request.get_method(), "POST")
            expected = hmac.new(key, request.data, hashlib.sha256).hexdigest()
            self.assertEqual(request.get_header("X-sirus-node-signature"), expected)
            self.assertNotEqual(agent.signed_request(self.config(), key, 1794154000).data, request.data)

    def test_private_key_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "key"
            p.write_bytes(b"K" * 32)
            p.chmod(0o600)
            self.assertEqual(agent.load_key(str(p)), b"K" * 32)
            p.chmod(0o644)
            with self.assertRaises(PermissionError):
                agent.load_key(str(p))
            p.chmod(0o600)
            p.write_bytes(b"wrong size")
            with self.assertRaises(ValueError):
                agent.load_key(str(p))

    def test_probe_without_network(self):
        with patch.object(agent, "telemetry", return_value={"node_id": "probe-node"}):
            self.assertEqual(agent.main(["--probe"]), 0)


if __name__ == "__main__":
    unittest.main()
