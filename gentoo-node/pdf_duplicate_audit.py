#!/usr/bin/env python3
"""Read-only, content-hash duplicate audit of the private Gentoo PDF library.

The historical duplicate events are *previous uploads*, not extra stored PDF
objects. Currently staged inbox PDFs are SHA-256 hashed without moving or
removing them. Inaccessible/unstable inputs make the report incomplete.
No API, CMS database, original PDF, or research state is modified.
"""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from urllib.parse import quote

SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_FILES = 100000
MAX_EVENTS = 200000
MAX_PDF_BYTES = 1024 * 1024 * 1024


def _root(path):
    root = Path(path)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("Storage root must be an existing absolute directory, not a symlink")
    if root.resolve(strict=True) != root:
        raise ValueError("Storage root must not traverse symlinks")
    return root


def _hash_file(path, expected):
    if not stat.S_ISREG(expected.st_mode) or not 0 < expected.st_size <= MAX_PDF_BYTES:
        raise ValueError("Not a regular PDF within the 1 GiB intake limit")
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        actual = os.fstat(fd)
        marker = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if not stat.S_ISREG(actual.st_mode) or marker(actual) != marker(expected):
            raise ValueError("File changed before hashing")
        digest = hashlib.sha256()
        with os.fdopen(os.dup(fd), 'rb') as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        if marker(os.fstat(fd)) != marker(expected):
            raise ValueError("File changed while hashing")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _catalog(root):
    db_path = root / 'databases' / 'pdf_ingest.sqlite3'
    if (root / 'databases').is_symlink() or db_path.is_symlink() or not db_path.is_file():
        raise ValueError("Private PDF ingest SQLite catalog unavailable")
    url = 'file:' + quote(str(db_path), safe='/') + '?mode=ro'
    with closing(sqlite3.connect(url, uri=True, timeout=15)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        documents = db.execute('''SELECT sha256, filename, object_rel, bytes, status
                                  FROM documents ORDER BY sha256 LIMIT ?''',
                               (MAX_FILES + 1,)).fetchall()
        events = db.execute('''SELECT sha256, detail FROM events
                               WHERE kind='duplicate' ORDER BY id LIMIT ?''',
                            (MAX_EVENTS + 1,)).fetchall()
    if len(documents) > MAX_FILES or len(events) > MAX_EVENTS:
        raise ValueError("Audit bounds exceeded; do not report an incomplete catalog")
    catalog = {}
    for row in documents:
        digest = row['sha256']
        if not isinstance(digest, str) or not SHA.fullmatch(digest) or digest in catalog:
            raise ValueError("Invalid PDF catalog digest")
        if row['bytes'] < 1 or not isinstance(row['object_rel'], str):
            raise ValueError("Invalid PDF catalog object metadata")
        catalog[digest] = {'filename': row['filename'], 'object_rel': row['object_rel'],
                           'bytes': row['bytes'], 'status': row['status']}
    duplicates = defaultdict(list)
    errors = []
    for row in events:
        digest = row['sha256']
        if digest not in catalog:
            errors.append({'type': 'unmatched_duplicate_event', 'sha256': digest})
            continue
        duplicates[digest].append(row['detail'])
    return catalog, duplicates, errors



def _canonical_integrity(root, catalog):
    """Read-only stat of every indexed object; never follow symlinks or trust paths."""
    problems = []
    matched = 0
    for digest, row in catalog.items():
        rel = row['object_rel']
        parts = Path(rel).parts
        if (not isinstance(rel, str) or Path(rel).is_absolute()
                or len(parts) < 3 or parts[:2] != ('manuals', 'objects')
                or any(p in ('', '.', '..') for p in parts)
                or '\\' in rel):
            problems.append({'type': 'invalid_canonical_path', 'sha256': digest})
            continue
        path = root
        try:
            for name in parts[:-1]:
                path = path / name
                if path.is_symlink():
                    raise ValueError("Canonical object parent is a symlink")
            path = path / parts[-1]
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Canonical object is not a regular file")
            if info.st_size != row['bytes']:
                problems.append({'type': 'canonical_size_mismatch', 'sha256': digest,
                                 'catalog_bytes': row['bytes'], 'disk_bytes': info.st_size})
            else:
                matched += 1
        except FileNotFoundError:
            problems.append({'type': 'missing_canonical_object', 'sha256': digest})
        except (OSError, ValueError):
            problems.append({'type': 'unsafe_or_inaccessible_canonical_object',
                             'sha256': digest})
    return matched, problems

def _inbox(root):
    inbox = root / 'manuals' / 'inbox'
    if (root / 'manuals').is_symlink() or not inbox.is_dir() or inbox.is_symlink():
        raise ValueError("Private PDF inbox unavailable or symlinked")
    staged = defaultdict(list)
    problems = []
    scanned = 0
    def on_walk_error(exc):
        problems.append({'type': 'unreadable_inbox_directory',
                         'path': str(exc.filename or inbox)})

    for directory, subdirs, files in os.walk(inbox, followlinks=False, onerror=on_walk_error):
        for dirname in list(subdirs):
            if (Path(directory) / dirname).is_symlink():
                subdirs.remove(dirname)
                problems.append({'type': 'symlink_directory', 'path': str((Path(directory) / dirname).relative_to(inbox))})
        for name in sorted(files):
            if not name.lower().endswith('.pdf'):
                continue
            scanned += 1
            if scanned > MAX_FILES:
                raise ValueError("More than 100000 staged PDFs; audit cannot claim completeness")
            path = Path(directory) / name
            rel = str(path.relative_to(inbox))
            try:
                observed = path.lstat()
                digest = _hash_file(path, observed)
                staged[digest].append(rel)
            except (OSError, ValueError) as exc:
                problems.append({'type': 'unreadable_or_unstable_pdf', 'path': rel,
                                 'reason': type(exc).__name__})
    return staged, problems, scanned


def audit(root, *, include_inbox=True):
    root = _root(root)
    catalog, events, problems = _catalog(root)
    canonical_matched, canonical_problems = _canonical_integrity(root, catalog)
    problems.extend(canonical_problems)
    staged, scanned = {}, 0
    if include_inbox:
        staged, errors, scanned = _inbox(root)
        problems.extend(errors)
    unindexed = [{'sha256': digest, 'paths': sorted(paths)}
                 for digest, paths in sorted(staged.items()) if digest not in catalog]
    results = []
    for digest in sorted(set(catalog) | set(staged)):
        names = events.get(digest, [])
        pending = sorted(staged.get(digest, []))
        canonical = catalog.get(digest)
        # A single pending unindexed PDF is not a known duplicate.
        if not names and len(pending) < 2 and not (canonical and pending):
            continue
        results.append({'sha256': digest, 'canonical': canonical,
                        'historical_duplicate_upload_count': len(names),
                        'historical_duplicate_filenames': sorted(set(names)),
                        'staged_inbox_paths': pending})
    return {'report': 'gentoo-pdf-content-hash-duplicates-v1',
            'mode': 'read_only_no_deletion',
            'scope': 'canonical_ingest_catalog_and_historical_duplicate_events_and_optional_staged_inbox',
            'canonical_objects_hash_reverified': False,
            'canonical_objects_size_and_type_checked': True,
            'unindexed_staged_pdfs': unindexed,
            'inbox_scanned': include_inbox,
            'complete': not problems,
            'summary': {'catalog_unique_pdf_objects': len(catalog),
                        'historical_duplicate_uploads': sum(map(len, events.values())),
                        'staged_pdfs_encountered': scanned,
                        'canonical_objects_metadata_matched': canonical_matched,
                        'canonical_objects_missing_or_unsafe': len(canonical_problems),
                        'uncatalogued_staged_pdfs': sum(len(item['paths']) for item in unindexed),
                        'duplicate_groups': len(results),
                        'unverified_or_skipped_entries': len(problems)},
            'groups': results, 'problems': problems}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Absolute private Gentoo storage root')
    parser.add_argument('--catalog-only', action='store_true',
                        help='Skip inbox hashing; report covers only historical ingest events')
    args = parser.parse_args(argv)
    try:
        report = audit(args.root, include_inbox=not args.catalog_only)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report['complete'] else 2
    except (OSError, ValueError, sqlite3.Error) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
