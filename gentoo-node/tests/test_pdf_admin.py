"""Signed PDF Admin API: HMAC, replay defense, ACL-free unit fixtures, approval gate."""
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pdf_admin", HERE / "pdf_admin.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
spec_ingest = importlib.util.spec_from_file_location("pdf_ingest", HERE / "pdf_ingest.py")
ing = importlib.util.module_from_spec(spec_ingest)
spec_ingest.loader.exec_module(ing)


class PDFAdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "databases").mkdir()
        self.db = ing.connect(self.root)
        ing.schema(self.db)
        self.key = b"x" * 32
        self.now = int(time.time())
        self.doc = "a" * 64
        self.db.execute("""INSERT INTO documents(sha256,filename,object_rel,bytes,
              status,pages,excerpts,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?)""",
                        (self.doc, "HD11 Workshop.pdf", "manuals/objects/aa/" + self.doc + ".pdf",
                         300, "ready", 2, 2, self.now, self.now))
        self.db.execute("INSERT INTO excerpts(sha256,page,part,text) VALUES (?,?,?,?)",
                        (self.doc, 1, 1, "30 Nm torque"))
        self.db.execute("INSERT INTO excerpts(sha256,page,part,text) VALUES (?,?,?,?)",
                        (self.doc, 2, 1, "Check belts"))
        self.db.close()
        self.mount = mock.patch.object(mod.os.path, "ismount", return_value=True)
        self.mount.start()
        self.addCleanup(self.mount.stop)
        self.nonce = 0

    def request(self, op, **fields):
        self.nonce += 1
        payload = {"op": op, "node_id": "gentoo-home", "sent_at": self.now,
                   "nonce": f"{self.nonce:032x}", **fields}
        data = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(self.key, mod.DOMAIN + data, hashlib.sha256).hexdigest()
        return mod.process(data, signature, self.key, "gentoo-home", self.root, self.now)

    def test_summary_and_filter(self):
        v = self.request("status", filter="ready", query="HD11")
        self.assertEqual(v["total"], 1)
        self.assertEqual(v["filtered"], 1)
        self.assertEqual(v["counts"]["ready"], 1)
        self.assertEqual(v["workers"], 3)
        self.assertEqual(v["documents"][0]["filename"], "HD11 Workshop.pdf")

    def test_search_escaped_and_pagination(self):
        self.assertEqual(self.request("status", query="%")["filtered"], 0)
        self.assertEqual(self.request("status", offset=1)["documents"], [])
        with self.assertRaises(mod.PDFAdminError):
            self.request("status", limit=1000)

    def test_preview_bounded(self):
        value = self.request("preview", sha256=self.doc)
        self.assertEqual([r["page"] for r in value["excerpts"]], [1,2])
        self.assertEqual(value["document"]["reviewed"], 0)

    def test_workflow(self):
        with self.assertRaises(mod.PDFAdminError):
            self.request("approve", sha256=self.doc)
        value = self.request("approve", sha256=self.doc, confirm="APPROVE MANUAL")
        self.assertEqual(value["operation"], "approve")
        self.assertEqual(self.request("status")["counts"]["approved"], 1)
        self.request("unapprove", sha256=self.doc)
        self.assertEqual(self.request("status")["counts"]["ready"], 1)
        with self.assertRaises(mod.PDFAdminError):
            self.request("retry", sha256=self.doc)

    def test_controls(self):
        self.request("pause")
        self.assertTrue(self.request("status")["paused"])
        self.request("workers", workers=9)
        self.assertEqual(self.request("status")["workers"], 9)
        self.request("resume")
        self.assertFalse(self.request("status")["paused"])
        with self.assertRaises(mod.PDFAdminError):
            self.request("workers", workers=2)

    def test_signature_replay_node_and_expiry(self):
        payload = {"op":"status","node_id":"gentoo-home",
                   "sent_at":self.now,"nonce":"f"*32}
        raw = json.dumps(payload).encode()
        sig = hmac.new(self.key, mod.DOMAIN + raw, hashlib.sha256).hexdigest()
        mod.process(raw, sig, self.key, "gentoo-home", self.root, self.now)
        with self.assertRaisesRegex(mod.PDFAdminError, "Replayed"):
            mod.process(raw, sig, self.key, "gentoo-home", self.root, self.now)
        with self.assertRaisesRegex(mod.PDFAdminError, "signed"):
            mod.process(raw, "0"*64, self.key, "gentoo-home", self.root, self.now)
        with self.assertRaises(mod.PDFAdminError):
            mod.process(raw, sig, self.key, "another-node", self.root, self.now)
        payload["nonce"] = "e"*32
        payload["sent_at"] -= 300
        old = json.dumps(payload).encode()
        old_sig = hmac.new(self.key, mod.DOMAIN + old, hashlib.sha256).hexdigest()
        with self.assertRaisesRegex(mod.PDFAdminError, "Expired"):
            mod.process(old, old_sig, self.key, "gentoo-home", self.root, self.now)

    def test_reject_duplicate_and_unknown_json(self):
        raw = ('{"op":"status","op":"approve","node_id":"gentoo-home",'
               f'"sent_at":{self.now},"nonce":"{"a"*32}"}}').encode()
        sig = hmac.new(self.key, mod.DOMAIN + raw, hashlib.sha256).hexdigest()
        with self.assertRaises(mod.PDFAdminError):
            mod.process(raw, sig, self.key, "gentoo-home", self.root, self.now)
        with self.assertRaises(mod.PDFAdminError):
            self.request("arbitrary-shell", command="rm")

    def test_missing_mount_fails_closed(self):
        self.mount.stop()
        try:
            with self.assertRaisesRegex(mod.PDFAdminError, "not mounted"):
                self.request("status")
        finally:
            self.mount.start()


if __name__ == "__main__":
    unittest.main()
