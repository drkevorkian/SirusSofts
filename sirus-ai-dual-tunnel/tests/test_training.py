import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("training_cli",ROOT/"training/qlora_train.py")
trainer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(trainer)

class TestTrainingCLI(unittest.TestCase):
    def example(self,prompt,profile="physics"):
        return {"profile":profile,"messages":[{"role":"user","content":prompt},
                                               {"role":"assistant","content":"Verified result"}]}
    def test_filter_and_no_train_check(self):
        with tempfile.TemporaryDirectory() as td:
            train=Path(td,"train.jsonl")
            train.write_text(json.dumps(self.example("Lorentz factor"))+"\n"+
                             json.dumps(self.example("Rust check","coding"))+"\n")
            out=Path(td,"adapter")
            self.assertEqual(trainer.main(["--train-jsonl",str(train),
                                          "--profile","physics","--output-dir",str(out)]),0)
            self.assertFalse(out.exists())
            self.assertEqual(len(trainer.load_examples(train,"physics")),1)
    def test_all_cms_domains_are_accepted(self):
        expected={"coding","science","mathematics","physics","time","relativity","finance","mechanics"}
        self.assertEqual(set(trainer.PROFILES)-{"all"},expected)
        with tempfile.TemporaryDirectory() as td:
            filename=Path(td,"subjects.jsonl")
            filename.write_text("".join(
                json.dumps(self.example("Verified question in "+domain,domain))+"\\n"
                for domain in sorted(expected)
            ))
            for domain in expected:
                records=trainer.load_examples(filename,domain)
                self.assertEqual(len(records),1)
                self.assertIn(domain,records[0]["prompt"][0]["content"])

    def test_eval_overlap_detection(self):
        example=self.example("Problem")
        train=[{"prompt":[example["messages"][0]],"completion":[example["messages"][1]]}]
        with self.assertRaises(ValueError):
            trainer.verify_split(train,train)
    def test_bad_data_and_gguf_model(self):
        with tempfile.TemporaryDirectory() as td:
            file=Path(td,"bad.jsonl");file.write_text(json.dumps({"messages":[]})+"\n")
            with self.assertRaises(ValueError):trainer.load_examples(file)
            model=Path(td,"model");model.mkdir()
            (model/"config.json").write_text("{}")
            (model/"weights.gguf").write_text("")
            with self.assertRaises(ValueError):trainer.local_checkpoint(model)

if __name__=="__main__":unittest.main()
