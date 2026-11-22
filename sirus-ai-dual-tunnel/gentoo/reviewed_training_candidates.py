#!/usr/bin/env python3
"""Private, offline candidates for *future* fine-tuning from reviewed PDF quotes.

This is deliberately NOT a training loop and never runs a model. It excludes
draft inference, unreviewed evidence, stale PDF copies, scope mismatches, and
conflicting measurements. A supported quote is NOT a second approval that a
procedure is safe, nor proof that an adapter has learned it. An operator must
review exported examples and approve any later training separately.
"""
import argparse
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from urllib.parse import quote

ACTOR = re.compile(r"cms-admin:[1-9][0-9]{0,12}\Z")
SCOPE = re.compile(r"[a-z0-9][a-z0-9._-]{0,23}(?:/[a-z0-9][a-z0-9._-]{0,23}){1,5}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
MAX_CANDIDATES = 100000
FILES = ("mechanics-train-candidates.jsonl", "mechanics-eval-candidates.jsonl",
         "mechanics-candidates-manifest.json")


@contextmanager
def readonly_database(filename):
    """Read live WAL snapshots correctly; never write to either source database."""
    path = Path(filename)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Database must be an absolute regular non-symlink file")
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=15)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=15000")
        yield db
    finally:
        db.close()


def _rows(db):
    return db.execute("""
        SELECT e.id, e.source_id, e.source_revision, e.source_updated_at,
          e.applicability, e.label, e.value, e.unit, e.quote, e.quote_sha,
          e.page, e.part, e.revision, v.reviewed_by
        FROM cr_claims c
        JOIN cr_claim_versions v
          ON v.claim_id=c.id AND v.version=c.version
        JOIN cr_evidence e ON e.id=c.evidence_id
        JOIN cr_sections s ON s.slot=e.slot AND s.revision=e.revision
        JOIN cr_sources d ON d.id=e.source_id
        LEFT JOIN cr_evidence_scopes sc ON sc.source_id=e.source_id
        WHERE c.state='supported' AND v.state='supported'
          AND v.quote_sha=e.quote_sha
          AND v.source_revision=e.source_revision
          AND v.applicability=e.applicability
          AND e.applicability=COALESCE(sc.applicability,'')
          AND s.current=1 AND d.present=1 AND d.eligible=1
          AND d.excluded=0 AND d.kind='pdf'
          AND (d.reviewed=1 OR d.extraction_status='approved')
        ORDER BY e.source_id,e.page,e.part,e.id
        LIMIT ?
    """, (MAX_CANDIDATES + 1,)).fetchall()


def _conflicted_keys(db):
    """Even unresolved/unreviewed opposing ORIGINAL measurements block export."""
    rows = db.execute("""
        SELECT e.applicability,e.label,e.unit
        FROM cr_evidence e
        JOIN cr_sections s ON s.slot=e.slot AND s.revision=e.revision
        JOIN cr_sources d ON d.id=e.source_id
        WHERE s.current=1 AND d.present=1 AND d.eligible=1
          AND d.excluded=0 AND d.kind='pdf'
        GROUP BY e.applicability,e.label,e.unit
        HAVING COUNT(DISTINCT e.value)>1
    """).fetchall()
    conflicts = {(r["applicability"],r["label"],r["unit"]) for r in rows}
    # An open conflict may also refer to a non-numeric ambiguity in future schema.
    for row in db.execute("""
        SELECT e.applicability,e.label,e.unit
        FROM cr_conflicts c JOIN cr_evidence e ON e.id=c.a
        WHERE c.state='unreviewed'
        UNION
        SELECT e.applicability,e.label,e.unit
        FROM cr_conflicts c JOIN cr_evidence e ON e.id=c.b
        WHERE c.state='unreviewed'
    """):
        conflicts.add(tuple(row))
    return conflicts


def collect(input_db, pdf_db):
    """Return source-backed examples and honest exclusion statistics; no writes."""
    counts = defaultdict(int)
    with readonly_database(input_db) as db, readonly_database(pdf_db) as pdf:
        records = _rows(db)
        if len(records) > MAX_CANDIDATES:
            raise ValueError("Too many reviewed records; export needs bounded paging")
        blocked = _conflicted_keys(db)
        by_key = {}
        source_cache = {}
        excerpt_cache = {}
        for r in records:
            counts["reviewed_rows_seen"] += 1
            key = (r["applicability"], r["label"], r["unit"])
            source = r["source_id"]
            valid = (
                isinstance(source, str) and source.startswith("pdf:")
                and HASH.fullmatch(source[4:])
                and isinstance(r["applicability"], str)
                and SCOPE.fullmatch(r["applicability"])
                and isinstance(r["reviewed_by"], str) and ACTOR.fullmatch(r["reviewed_by"])
                and isinstance(r["quote"], str) and r["quote"].strip()
                and hashlib.sha256(r["quote"].encode("utf-8")).hexdigest() == r["quote_sha"]
                and r["page"] > 0 and r["part"] > 0
            )
            if not valid:
                counts["invalid_or_unscoped"] += 1
                continue
            if key in blocked:
                counts["conflicting_property"] += 1
                continue
            digest = source[4:]
            if digest not in source_cache:
                source_cache[digest] = pdf.execute(
                    "SELECT updated_at,status,reviewed FROM documents WHERE sha256=?",
                    (digest,)).fetchone()
            doc = source_cache[digest]
            if not doc or doc["updated_at"] != r["source_updated_at"] or not (
                doc["status"] == "approved" or
                doc["status"] == "ready" and doc["reviewed"] == 1
            ):
                counts["missing_or_changed_original"] += 1
                continue
            excerpt_key = (digest, r["page"], r["part"])
            if excerpt_key not in excerpt_cache:
                excerpt_cache[excerpt_key] = pdf.execute(
                    "SELECT text FROM excerpts WHERE sha256=? AND page=? AND part=?",
                    excerpt_key).fetchone()
            excerpt = excerpt_cache[excerpt_key]
            if not excerpt or not isinstance(excerpt["text"], str) or r["quote"] not in excerpt["text"]:
                counts["quote_not_in_original"] += 1
                continue
            if key in by_key:
                counts["duplicate_property"] += 1
                continue
            prompt = (
                "For the exact application " + r["applicability"] +
                ", what does the approved original manual state for " +
                r["label"] + "? Give the citation; do not generalize to other models."
            )
            response = (
                r["quote"] + "\nOriginal PDF " + source + ", page " +
                str(r["page"]) + ", part " + str(r["part"]) + "."
            )
            by_key[key] = {
                "profile": "mechanics",
                "messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": response}
                ],
                "provenance": {
                    "source_id": source,
                    "source_revision": r["source_revision"],
                    "section_revision": r["revision"],
                    "evidence_id": r["id"],
                    "quote_sha256": r["quote_sha"],
                    "page": r["page"], "part": r["part"],
                    "applicability": r["applicability"],
                    "human_reviewer": r["reviewed_by"]
                },
                "candidate_only": True
            }

    train, evaluation = [], []
    for item in by_key.values():
        # Group on the original document: all examples from one PDF share a split.
        # A small corpus may have zero evaluation examples; report that plainly.
        source_id = item["provenance"]["source_id"]
        bucket = int(hashlib.sha256(source_id.encode()).hexdigest()[:8], 16) % 5
        (evaluation if bucket == 0 else train).append(item)
    counts["unique_source_backed_candidates"] = len(by_key)
    counts["train_candidates"] = len(train)
    counts["evaluation_candidates"] = len(evaluation)
    counts["distinct_sources"] = len({
        row["provenance"]["source_id"] for row in train + evaluation
    })
    return train, evaluation, dict(counts)


def _private_directory(directory):
    path = Path(directory)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError("Output must be an existing absolute private directory")
    # Reject symlinks in parent components as well as the last component.
    if path.resolve(strict=True) != path:
        raise ValueError("Output directory cannot traverse symlinks")
    mode = path.stat().st_mode
    if not stat.S_ISDIR(mode) or mode & 0o077:
        raise ValueError("Output directory must deny group and other access (0700)")
    if path.stat().st_uid != os.geteuid():
        raise ValueError("Output directory must be owned by the current operator")
    return path


def export(directory, train, evaluation, counts):
    """Explicit local write, new files only; never publish or overwrite."""
    dest = _private_directory(directory)
    if any((dest / name).exists() or (dest / name).is_symlink() for name in FILES):
        raise ValueError("Candidate files already exist; no overwrite is permitted")
    blobs = []
    for rows in (train, evaluation):
        blobs.append("".join(json.dumps(r, sort_keys=True, ensure_ascii=False)
                             + "\n" for r in rows).encode("utf-8"))
    manifest = {
        "format": "sirus-private-review-candidates-v1",
        "counts": counts,
        "sha256_train": hashlib.sha256(blobs[0]).hexdigest(),
        "sha256_eval": hashlib.sha256(blobs[1]).hexdigest(),
        "split_by_source": True,
        "requires_independent_training_approval": True,
        "source_recheck_required_before_training": True,
        "automatic_training": False,
        "original_manuals_and_drafts_unchanged": True
    }
    blobs.append((json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
    created = []
    try:
        for name, data in zip(FILES, blobs):
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(dest / name, flags, 0o600)
            created.append(dest / name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    except BaseException:
        for filename in created:
            filename.unlink(missing_ok=True)
        raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-db", required=True)
    parser.add_argument("--pdf-db", required=True)
    parser.add_argument("--output-dir", help="Explicitly write candidates (otherwise preview only)")
    args = parser.parse_args(argv)
    try:
        train, evaluation, counts = collect(args.input_db, args.pdf_db)
        summary = {"counts": counts, "candidate_only": True,
                   "training_started": False, "model_weights_changed": False}
        if args.output_dir:
            summary["manifest"] = export(args.output_dir, train, evaluation, counts)
            summary["written_to_private_directory"] = True
        else:
            summary["written_to_private_directory"] = False
        print(json.dumps(summary, sort_keys=True))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
