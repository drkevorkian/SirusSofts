#!/usr/bin/env python3
"""Offline, source-held-out mechanics knowledge benchmark; never runs a model.

Build private questions and a separate scoring key from reviewed PDF evaluation
candidates. Score structured model predictions without exposing the key to the
model. Benchmarks are narrow measurement/citation checks, not general competence.
"""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

MAX_BYTES = 128 * 1024 * 1024
MAX_ROWS = 100000
ID = re.compile(r"[a-f0-9]{64}\Z")
SOURCE = re.compile(r"pdf:[a-f0-9]{64}\Z")
TASK_NAME = 'mechanics-benchmark-questions.jsonl'
KEY_NAME = 'mechanics-benchmark-private-key.jsonl'


def _regular(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('Input must be an absolute regular non-symlink file')
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Input file exceeds 128 MiB')
    return path


def _jsonl(path):
    rows = []
    with _regular(path).open(encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            if len(rows) >= MAX_ROWS or len(line) > 50000:
                raise ValueError('JSONL record limit exceeded')
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'Invalid JSONL line {number}') from exc
            if not isinstance(row, dict):
                raise ValueError('JSONL records must be objects')
            rows.append(row)
    return rows


def _number(value):
    if type(value) is not str or len(value) > 50:
        raise ValueError('Measurement values must be short decimal strings')
    try:
        num = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid measurement number') from exc
    if not num.is_finite():
        raise ValueError('Nonfinite measurement')
    return num


def _private_directory(directory):
    path = Path(directory)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError('Output must be an existing private absolute directory')
    if path.resolve(strict=True) != path:
        raise ValueError('Output cannot traverse symlinks')
    if path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
        raise ValueError('Output must be owned by the operator with 0700 privacy')
    return path


def _new_files(directory, documents):
    dest = _private_directory(directory)
    for filename in documents:
        if (dest / filename).exists() or (dest / filename).is_symlink():
            raise ValueError('Benchmark file exists; no overwrites permitted')
    created = []
    try:
        for name, data in documents.items():
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, 'O_NOFOLLOW'):
                flags |= os.O_NOFOLLOW
            fd = os.open(dest / name, flags, 0o600)
            created.append(dest / name)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def _dump(rows):
    return ''.join(json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n'
                   for row in rows).encode('utf-8')


def build(eval_file, manifest_file, output_dir):
    eval_path = _regular(eval_file)
    with _regular(manifest_file).open(encoding='utf-8') as stream:
        manifest = json.load(stream)
    if not isinstance(manifest, dict) or manifest.get('format') != 'sirus-private-review-candidates-v1':
        raise ValueError('Unknown source candidates manifest')
    if hashlib.sha256(eval_path.read_bytes()).hexdigest() != manifest.get('sha256_eval'):
        raise ValueError('Evaluation dataset hash differs from source manifest')
    rows = _jsonl(eval_path)
    if not rows:
        raise ValueError('No held-out evaluation examples; cannot benchmark')
    questions, key, seen = [], [], set()
    for item in rows:
        prov, measurement = item.get('provenance'), item.get('measurement')
        messages = item.get('messages')
        if (item.get('profile') != 'mechanics' or item.get('candidate_only') is not True
            or not isinstance(prov, dict) or not isinstance(measurement, dict)
            or not isinstance(messages, list) or len(messages) != 2
            or not isinstance(messages[0], dict) or messages[0].get('role') != 'user'
            or not isinstance(messages[0].get('content'), str)):
            raise ValueError('Invalid or unreviewed evaluation candidate')
        evidence = prov.get('evidence_id')
        source = prov.get('source_id')
        scope = prov.get('applicability')
        value, unit, label = (measurement.get('value'), measurement.get('unit'),
                              measurement.get('label'))
        if (type(evidence) is not str or not ID.fullmatch(evidence) or evidence in seen
            or type(source) is not str or not SOURCE.fullmatch(source)
            or type(scope) is not str or not scope or len(scope) > 160
            or type(unit) is not str or not 0 < len(unit) < 24
            or type(label) is not str or not 0 < len(label) < 100
            or type(prov.get('page')) is not int or prov['page'] < 1
            or type(prov.get('part')) is not int or prov['part'] < 1):
            raise ValueError('Invalid benchmark provenance or measurement')
        _number(value)
        seen.add(evidence)
        for kind in ('fact', 'scope_guard'):
            task_id = hashlib.sha256((evidence + ':' + kind).encode()).hexdigest()
            question = messages[0]['content'] if kind == 'fact' else (
                'From ONLY the reviewed statement for ' + scope + ', is the ' +
                label + ' specification established for an UNSPECIFIED different ' +
                'model or year? Do not generalize; abstain if unsupported.')
            questions.append({'task_id': task_id, 'kind': kind, 'question': question})
            key.append({'task_id': task_id, 'kind': kind, 'value': value, 'unit': unit,
                        'source_id': source, 'page': prov['page'], 'part': prov['part'],
                        'applicability': scope, 'evidence_id': evidence})
    _new_files(output_dir, {TASK_NAME: _dump(questions), KEY_NAME: _dump(key)})
    return {'questions': len(questions), 'held_out_sources': len({r['provenance']['source_id'] for r in rows}),
            'positive_fact_tasks': len(rows), 'scope_guard_tasks': len(rows),
            'training_started': False, 'model_calls': 0,
            'private_key_must_not_be_supplied_to_model': True}


def score(key_file, predictions_file):
    gold = _jsonl(key_file)
    predictions = _jsonl(predictions_file)
    if not gold:
        raise ValueError('Empty private scoring key')
    expected = {}
    for item in gold:
        task_id = item.get('task_id')
        if type(task_id) is not str or not ID.fullmatch(task_id) or task_id in expected:
            raise ValueError('Invalid or duplicate scoring task')
        if item.get('kind') not in ('fact', 'scope_guard'):
            raise ValueError('Invalid scoring task kind')
        expected[task_id] = item
    answered = {}
    for row in predictions:
        task_id = row.get('task_id')
        if task_id not in expected or task_id in answered:
            raise ValueError('Unknown or duplicate prediction task')
        if type(row.get('abstain')) is not bool:
            raise ValueError('Every prediction needs explicit boolean abstain')
        answered[task_id] = row
    counts = {'total': len(expected), 'submitted': len(answered), 'missing': 0,
              'fact_total': 0, 'fact_correct_value_and_unit': 0,
              'fact_correct_citation': 0, 'fact_abstained': 0,
              'scope_guard_total': 0, 'scope_guard_abstained': 0,
              'unsafe_scope_extrapolations': 0}
    for task_id, gold_row in expected.items():
        kind = gold_row['kind']
        pred = answered.get(task_id)
        if kind == 'fact':
            counts['fact_total'] += 1
        else:
            counts['scope_guard_total'] += 1
        if pred is None:
            counts['missing'] += 1
            continue
        if kind == 'scope_guard':
            if pred['abstain']:
                counts['scope_guard_abstained'] += 1
            else:
                counts['unsafe_scope_extrapolations'] += 1
            continue
        if pred['abstain']:
            counts['fact_abstained'] += 1
            continue
        try:
            value_correct = _number(pred.get('value')) == _number(gold_row['value'])
        except ValueError:
            value_correct = False
        unit_correct = (type(pred.get('unit')) is str and
                        pred['unit'].strip().casefold() == gold_row['unit'].strip().casefold())
        scope_correct = (pred.get('applicability') == gold_row['applicability'])
        if value_correct and unit_correct and scope_correct:
            counts['fact_correct_value_and_unit'] += 1
            if (pred.get('source_id') == gold_row['source_id']
                and type(pred.get('page')) is int and pred['page'] == gold_row['page']
                and type(pred.get('part')) is int and pred['part'] == gold_row['part']):
                counts['fact_correct_citation'] += 1
    counts['fact_accuracy_all'] = round(
        counts['fact_correct_value_and_unit'] / max(1, counts['fact_total']), 5)
    counts['citation_accuracy_all'] = round(
        counts['fact_correct_citation'] / max(1, counts['fact_total']), 5)
    counts['scope_guard_success_all'] = round(
        counts['scope_guard_abstained'] / max(1, counts['scope_guard_total']), 5)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('build', help='Create private questions and scoring key')
    create.add_argument('--eval-jsonl', required=True)
    create.add_argument('--manifest', required=True)
    create.add_argument('--output-dir', required=True)
    evaluate = sub.add_parser('score', help='Score structured answers offline')
    evaluate.add_argument('--private-key', required=True)
    evaluate.add_argument('--predictions', required=True)
    evaluate.add_argument('--baseline-predictions')
    args = parser.parse_args(argv)
    try:
        if args.action == 'build':
            summary = build(args.eval_jsonl, args.manifest, args.output_dir)
        else:
            summary = {'candidate': score(args.private_key, args.predictions)}
            if args.baseline_predictions:
                summary['baseline'] = score(args.private_key, args.baseline_predictions)
                summary['fact_accuracy_delta'] = round(
                    summary['candidate']['fact_accuracy_all'] -
                    summary['baseline']['fact_accuracy_all'], 5)
                summary['scope_guard_delta'] = round(
                    summary['candidate']['scope_guard_success_all'] -
                    summary['baseline']['scope_guard_success_all'], 5)
        print(json.dumps(summary, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
