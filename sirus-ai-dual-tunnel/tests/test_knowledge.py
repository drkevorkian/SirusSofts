import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("knowledge",ROOT/"gentoo/knowledge.py")
kb=importlib.util.module_from_spec(spec)
spec.loader.exec_module(kb)

class TestKnowledge(unittest.TestCase):
    @staticmethod
    def record(text,reviewed=True):
        return {"kind":"sirus-ai-document-v1","profile":"physics",
                "title":"Lorentz factor","source":"https://example.org/physics",
                "text":text,"sha256":hashlib.sha256(text.encode()).hexdigest(),
                "reviewed":reviewed}
    def test_filter_and_citation(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td,"index.jsonl")
            f.write_text(json.dumps(self.record("Lorentz factor gamma is used in relativity"))+"\n")
            k=kb.KnowledgeIndex.from_file(f)
            messages=[{"role":"user","content":"Lorentz factor gamma?"}]
            reply=k.augment_messages(messages,"physics")
            self.assertEqual(reply[0]["role"],"system")
            self.assertIn("[K1]",reply[0]["content"])
            self.assertEqual(reply[1:],messages)
            self.assertEqual(k.augment_messages(messages,"coding"),messages)
    def test_expanded_expertise(self):
        self.assertEqual(set(kb.PROFILES), {"auto","coding","science","mathematics","physics","time","relativity","finance","mechanics"})
        with tempfile.TemporaryDirectory() as td:
            f=Path(td,"index.jsonl")
            doc=self.record("Clutch primary weights")
            doc["profile"]="mechanics"
            f.write_text(json.dumps(doc)+"\n")
            idx=kb.KnowledgeIndex.from_file(f)
            self.assertEqual(len(idx.retrieve("clutch weights","mechanics")),1)
            self.assertEqual(idx.retrieve("clutch weights","finance"),[])

    def test_integrity_and_review(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td,"index.jsonl")
            data=self.record("test",False)
            f.write_text(json.dumps(data)+"\n")
            with self.assertRaises(ValueError):kb.KnowledgeIndex.from_file(f)
            data["reviewed"]=True;data["sha256"]="0"*64
            f.write_text(json.dumps(data)+"\n")
            with self.assertRaises(ValueError):kb.KnowledgeIndex.from_file(f)
            link=Path(td,"link");link.symlink_to(f)
            with self.assertRaises(ValueError):kb.KnowledgeIndex.from_file(link)
    def test_hot_reload(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td,"index.jsonl")
            f.write_text(json.dumps(self.record("First Lorentz value"))+"\n")
            k=kb.KnowledgeManager(f);msg=[{"role":"user","content":"Lorentz"}]
            self.assertIn("First",k.augment_messages(msg)[0]["content"])
            f.write_text(json.dumps(self.record("Second Lorentz revision value"))+"\n")
            self.assertIn("Second",k.augment_messages(msg)[0]["content"])
if __name__=="__main__":unittest.main()
