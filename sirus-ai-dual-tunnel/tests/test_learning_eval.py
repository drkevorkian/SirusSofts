"""Offline benchmarks: synthetic held-out manual data; no model or network."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training"))
import learning_eval as benchmark

SOURCE = 'pdf:' + 'a' * 64
EVIDENCE = 'b' * 64
SCOPE = 'brp/defender/2026/hd11/clutch'


class BenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.eval = root / 'heldout.jsonl'
        self.manifest = root / 'manifest.json'
        self.private = root / 'private'
        self.private.mkdir(mode=0o700)
        entry = {
            'profile': 'mechanics', 'candidate_only': True,
            'messages': [{'role': 'user', 'content': 'What is the axle torque for ' + SCOPE + '?'},
                         {'role': 'assistant', 'content': 'Rear axle torque: 40 Nm'}],
            'measurement': {'value': '4E+1', 'unit': 'nm', 'label': 'rear axle torque'},
            'provenance': {'source_id': SOURCE, 'evidence_id': EVIDENCE,
                           'page': 5, 'part': 1, 'applicability': SCOPE}}
        self.eval.write_text(json.dumps(entry) + '\n')
        self.manifest.write_text(json.dumps({'format': 'sirus-private-review-candidates-v1',
            'sha256_eval': hashlib.sha256(self.eval.read_bytes()).hexdigest()}))

    def generated(self):
        result = benchmark.build(self.eval, self.manifest, self.private)
        questions = benchmark._jsonl(self.private / benchmark.TASK_NAME)
        gold = benchmark._jsonl(self.private / benchmark.KEY_NAME)
        return result, questions, gold

    def test_questions_do_not_contain_answer_and_key_is_private(self):
        result, questions, gold = self.generated()
        self.assertEqual(result['questions'], 2)
        self.assertEqual(len(gold), 2)
        self.assertTrue(all('40' not in q['question'] for q in questions))
        self.assertTrue(all('value' not in q for q in questions))
        self.assertEqual((self.private / benchmark.KEY_NAME).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.generated()

    def test_accuracy_and_wrong_scope_guard(self):
        _, _, gold = self.generated()
        results = self.private / 'predictions.jsonl'
        rows = []
        for item in gold:
            if item['kind'] == 'fact':
                rows.append({'task_id': item['task_id'], 'abstain': False,
                             'value': '40.0', 'unit': 'Nm', 'applicability': SCOPE,
                             'source_id': SOURCE, 'page': 5, 'part': 1})
            else:
                rows.append({'task_id': item['task_id'], 'abstain': True})
        results.write_text(''.join(json.dumps(x) + '\n' for x in rows))
        stats = benchmark.score(self.private / benchmark.KEY_NAME, results)
        self.assertEqual(stats['fact_accuracy_all'], 1)
        self.assertEqual(stats['citation_accuracy_all'], 1)
        self.assertEqual(stats['scope_guard_success_all'], 1)
        rows[0]['applicability'] = 'brp/defender/2025/hd10/clutch'
        rows[1]['abstain'] = False
        results.write_text(''.join(json.dumps(x) + '\n' for x in rows))
        stats = benchmark.score(self.private / benchmark.KEY_NAME, results)
        self.assertEqual(stats['fact_accuracy_all'], 0)
        self.assertEqual(stats['unsafe_scope_extrapolations'], 1)

    def test_missing_and_duplicate_and_unknown_predictions(self):
        _, _, gold = self.generated()
        results = self.private / 'predict.jsonl'
        results.write_text('')
        self.assertEqual(benchmark.score(self.private / benchmark.KEY_NAME, results)['missing'], 2)
        record = {'task_id': gold[0]['task_id'], 'abstain': True}
        results.write_text(json.dumps(record)+'\n'+json.dumps(record)+'\n')
        with self.assertRaises(ValueError):
            benchmark.score(self.private / benchmark.KEY_NAME, results)
        record['task_id'] = 'f'*64
        results.write_text(json.dumps(record)+'\n')
        with self.assertRaises(ValueError):
            benchmark.score(self.private / benchmark.KEY_NAME, results)

    def test_reject_corrupt_source_and_empty_holdout(self):
        self.eval.write_text('')
        with self.assertRaises(ValueError):
            benchmark.build(self.eval, self.manifest, self.private)
        self.assertEqual(list(self.private.iterdir()), [])
        self.manifest.write_text(json.dumps({'format': 'sirus-private-review-candidates-v1',
            'sha256_eval': hashlib.sha256(b'').hexdigest()}))
        with self.assertRaises(ValueError):
            benchmark.build(self.eval, self.manifest, self.private)

    def test_reject_public_or_symlink_output_and_nonfinite_numbers(self):
        self.private.chmod(0o755)
        with self.assertRaises(ValueError):
            benchmark.build(self.eval, self.manifest, self.private)
        self.private.chmod(0o700)
        link = self.private.parent / 'link'
        link.symlink_to(self.private, target_is_directory=True)
        with self.assertRaises(ValueError):
            benchmark.build(self.eval, self.manifest, link)
        with self.assertRaises(ValueError):
            benchmark._number('NaN')
        with self.assertRaises(ValueError):
            benchmark._number('inf')


if __name__ == '__main__':
    unittest.main()
