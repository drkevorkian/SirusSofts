import importlib.util
import pathlib
import tempfile
import unittest

spec=importlib.util.spec_from_file_location("research",pathlib.Path(__file__).resolve().parents[1]/"research.py")
research=importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


class ResearchTests(unittest.TestCase):
    def test_reject_overlapping_input_output(self):
        with tempfile.TemporaryDirectory() as root:
            directory=pathlib.Path(root)
            with self.assertRaises(ValueError):
                research.valid_destination(directory/"output",directory)

    def test_reject_symlink_source(self):
        with tempfile.TemporaryDirectory() as root:
            directory=pathlib.Path(root)
            (directory/"real").mkdir()
            (directory/"link").symlink_to(directory/"real")
            with self.assertRaises(ValueError):
                research.valid_source(directory/"link")

    def test_digest_stable(self):
        with tempfile.TemporaryDirectory() as root:
            p=pathlib.Path(root)/"sample.pdf"
            p.write_bytes(b"example-data")
            self.assertEqual(research.digest_file(p), research.digest_file(p))


if __name__=="__main__":
    unittest.main()
