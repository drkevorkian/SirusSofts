"""Image-only approved manual access over existing HMAC-signed Gentoo bridge."""
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

pdf = load("pdf_admin", "pdf_admin.py")
ing = load("pdf_ingest", "pdf_ingest.py")
viewer = load("manual_view", "manual_view.py")


class ManualViewTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        (self.root / "databases").mkdir()
        self.sha = "e" * 64
        self.bytes = b"%PDF-" + b"test fixture" * 30
        self.object = self.root / "manuals" / "objects" / self.sha[:2] / (self.sha + ".pdf")
        self.object.parent.mkdir(parents=True)
        self.object.write_bytes(self.bytes)
        with ing.connect(self.root) as db:
            ing.schema(db)
            db.execute("""INSERT INTO documents
              (sha256,filename,object_rel,bytes,status,reviewed,pages,excerpts,
               created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
               (self.sha, "2027 DEFENDER HD11 Shop Manual.pdf",
                f"manuals/objects/{self.sha[:2]}/{self.sha}.pdf",
                len(self.bytes), "approved", 1, 330, 10, 1, 1))
        self.mounted = mock.patch.object(pdf.os.path, "ismount", return_value=True)
        self.mounted.start()
        self.addCleanup(self.mounted.stop)
        self.now = 1_800_000_000
        self.nonce = 0
        self.key = b"r" * 32

    def signed(self, op, **data):
        self.nonce += 1
        body = json.dumps({
            "op": op, "node_id": "gentoo-home", "sent_at": self.now,
            "nonce": f"{self.nonce:032x}", **data
        }).encode()
        sig = hmac.new(self.key,pdf.DOMAIN+body,hashlib.sha256).hexdigest()
        return pdf.process(body,sig,self.key,"gentoo-home",self.root,self.now)

    def test_metadata_and_valid_image(self):
        metadata = self.signed("manual_meta",sha256=self.sha)
        self.assertEqual(metadata["pages"],330)
        self.assertEqual(metadata["sha256"],self.sha)
        self.assertNotIn("object_rel",metadata)

        def fake_renderer(command,**kw):
            self.assertIn("pdftoppm",command[0])
            self.assertIn("-singlefile",command)
            self.assertNotIn("-png",command)
            self.assertEqual(kw["timeout"],22)
            self.assertEqual(command[command.index("-f")+1],"298")
            Path(command[-1]+".jpg").write_bytes(
                b"\xff\xd8\xff"+b"x"*250+b"\xff\xd9")
            return mock.Mock(returncode=0)
        with mock.patch.object(viewer.subprocess, "run",side_effect=fake_renderer):
            result = self.signed("manual_page",sha256=self.sha,page=298,size="preview")
        self.assertEqual(result["mime"],"image/jpeg")
        self.assertEqual(result["page"],298)
        self.assertNotIn("object_rel",result)
        self.assertNotIn("pdf",result["mime"])
        self.assertTrue(result["base64"])
        self.assertEqual(self.signed("manual_meta",sha256=self.sha[:12])["sha256"],self.sha)

    def test_revocation_and_guardrails(self):
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_page",sha256=self.sha,page=331)
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_page",sha256=self.sha,page=0)
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_page",sha256=self.sha,page=True)
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_page",sha256=self.sha,page=2,size="pdf")
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_meta",sha256="../" + self.sha[:12])
        with ing.connect(self.root) as db:
            db.execute("UPDATE documents SET status='ready',reviewed=0 WHERE sha256=?",
                       (self.sha,))
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_meta",sha256=self.sha)
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_page",sha256=self.sha,page=298)

    def test_modified_path_and_symlinks_rejected(self):
        with ing.connect(self.root) as db:
            db.execute("UPDATE documents SET object_rel='../../etc/passwd' WHERE sha256=?",
                       (self.sha,))
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_meta",sha256=self.sha)
        with ing.connect(self.root) as db:
            db.execute("UPDATE documents SET object_rel=? WHERE sha256=?",
                       (f"manuals/objects/{self.sha[:2]}/{self.sha}.pdf",self.sha))
        self.object.unlink()
        other=self.root/"replacement.pdf"
        other.write_bytes(self.bytes)
        self.object.symlink_to(other)
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_meta",sha256=self.sha)

    def test_oversized_render_and_no_download_operation(self):
        def too_big(command,**kw):
            Path(command[-1]+".jpg").write_bytes(
                b"\xff\xd8\xff"+b"x"*(viewer.MAX_IMAGE+1)+b"\xff\xd9")
            return mock.Mock(returncode=0)
        with mock.patch.object(viewer.subprocess,"run",side_effect=too_big):
            with self.assertRaises(pdf.PDFAdminError):
                self.signed("manual_page",sha256=self.sha,page=298)
        with self.assertRaises(pdf.PDFAdminError):
            self.signed("manual_download",sha256=self.sha)


if __name__ == "__main__":
    unittest.main()
