import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import inference_worker as w


class InferenceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in ("llama-server","model.gguf","key","hosts"):
            (self.root/name).write_bytes(b"test")
        os.chmod(self.root/"llama-server",0o700)
        os.chmod(self.root/"key",0o600)
        self.config=self.root/"config.json"
        self.raw={"gentoo_host":"gentoo.local","ssh_user":"sirus-mac-inference",
                  "llama_server":str(self.root/"llama-server"),
                  "model_file":str(self.root/"model.gguf"),
                  "identity_file":str(self.root/"key"),
                  "known_hosts_file":str(self.root/"hosts"),
                  "model_sha256":hashlib.sha256(b"test").hexdigest()}
    def load(self):
        self.config.write_text(json.dumps(self.raw))
        return w.load_config(self.config)
    def test_pinned_forward_cpu_only(self):
        cfg=self.load()
        w.verify_model(cfg)
        llama,ssh=w.commands(cfg)
        self.assertIn("127.0.0.1",llama)
        self.assertEqual(llama[llama.index("--n-gpu-layers")+1],"0")
        self.assertIn("127.0.0.1:18588:127.0.0.1:18588",ssh)
        self.assertIn("StrictHostKeyChecking=yes",ssh)
        self.assertIn("ExitOnForwardFailure=yes",ssh)
        self.assertEqual(ssh[-1],"sirus-mac-inference@gentoo.local")
        self.assertIn("-N",ssh)
    def test_bad_host_and_user(self):
        for key,value in (("gentoo_host","-oProxyCommand=x"),("ssh_user","root")):
            with self.subTest(key=key):
                old=self.raw[key]
                self.raw[key]=value
                with self.assertRaises(ValueError):
                    self.load()
                self.raw[key]=old
    def test_model_mismatch(self):
        cfg=self.load()
        (self.root/"model.gguf").write_bytes(b"different")
        with self.assertRaises(ValueError):
            w.verify_model(cfg)
    def test_insecure_identity(self):
        os.chmod(self.root/"key",0o644)
        with self.assertRaises(ValueError):
            self.load()
