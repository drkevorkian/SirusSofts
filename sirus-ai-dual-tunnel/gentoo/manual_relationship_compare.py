#!/usr/bin/env python3
"""Read-only, explicitly scoped original-PDF comparison candidates.

No application scope is derived from a filename, repeated identifier, model
draft, or index record. Operator-declared scopes are not verified fitment.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from itertools import combinations
import json
from pathlib import Path
import re
import sqlite3
import sys

from feasibility_review import _checked_source, _normalized
from manual_rag import connect_source
from manual_relationship_index import KINDS, KEY, open_index, search
from manual_relationships import SOURCE_ID, _extract

MAX_MATCHES = 64
MAX_MEASUREMENTS = 12
MAX_INPUT = 16384
SCOPE_FIELDS = frozenset(("manufacturer", "model", "year", "engine",
                          "revision", "application"))
SCOPE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+/-]{0,63}\Z")
LABEL = re.compile(r"[A-Za-z][A-Za-z0-9 ._+/-]{0,79}\Z")
REF_FIELDS = frozenset(("source_id", "page", "part", "quote"))


def _validate(request):
    if not isinstance(request, dict) or set(request) != {
            "sources", "terms", "measurements"}:
        raise ValueError("Expected sources, exact terms and measurements")
    sources = request["sources"]
    terms = request["terms"]
    measurements = request["measurements"]
    if not isinstance(sources, list) or not 2 <= len(sources) <= 8:
        raise ValueError("Explicitly select 2-8 original manuals")
    ids = []
    scopes = {}
    for entry in sources:
        if (not isinstance(entry, dict) or
                set(entry) != {"source_id", "scope"}):
            raise ValueError("Source requires exact ID and declared scope")
        sid, scope = entry["source_id"], entry["scope"]
        if not isinstance(sid, str) or not SOURCE_ID.fullmatch(sid) or sid in scopes:
            raise ValueError("Invalid or repeated source ID")
        if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
            raise ValueError("Require explicit manufacturer/model/year/engine/revision/application")
        if type(scope["year"]) is not int or not 1900 <= scope["year"] <= 2100:
            raise ValueError("Invalid declared model year")
        if any(not isinstance(scope[k], str) or not SCOPE_VALUE.fullmatch(scope[k])
               for k in SCOPE_FIELDS - {"year"}):
            raise ValueError("Invalid or missing declared application scope")
        ids.append(sid)
        scopes[sid] = scope
    if not isinstance(terms, list) or not 1 <= len(terms) <= 4:
        raise ValueError("Select 1-4 exact indexed mentions")
    seen_terms = set()
    for term in terms:
        if (not isinstance(term, dict) or set(term) != {"kind", "term"}
                or not isinstance(term["kind"], str) or term["kind"] not in KINDS
                or not isinstance(term["term"], str) or
                not KEY.fullmatch(term["term"])):
            raise ValueError("Invalid exact indexed term")
        key = (term["kind"], term["term"])
        if key in seen_terms:
            raise ValueError("Repeated indexed term")
        seen_terms.add(key)
    if not isinstance(measurements, list) or len(measurements) > MAX_MEASUREMENTS:
        raise ValueError("At most twelve measurement pairs")
    for item in measurements:
        if (not isinstance(item, dict) or set(item) != {"question", "left", "right"}
                or not isinstance(item["question"], str) or
                not LABEL.fullmatch(item["question"])):
            raise ValueError("Invalid measurement comparison")
        for name in ("left", "right"):
            ref = item[name]
            if (not isinstance(ref, dict) or set(ref) != REF_FIELDS
                    or not isinstance(ref["source_id"], str)
                    or ref["source_id"] not in scopes
                    or type(ref["page"]) is not int or
                    not 1 <= ref["page"] <= 20000
                    or type(ref["part"]) is not int or
                    not 1 <= ref["part"] <= 100000
                    or not isinstance(ref["quote"], str)
                    or not 1 <= len(ref["quote"]) <= 180):
                raise ValueError("Measurement requires selected, exact PDF quote pointer")
        if item["left"]["source_id"] == item["right"]["source_id"]:
            raise ValueError("Measurement must compare two selected original manuals")
    return ids, scopes


def _scope_delta(scopes, left, right):
    a, b = scopes[left], scopes[right]
    return {key: [a[key], b[key]] for key in sorted(SCOPE_FIELDS)
            if str(a[key]).casefold() != str(b[key]).casefold()}


def _measure(db, item, scopes):
    left, right = item["left"], item["right"]
    a = _checked_source(db, left)
    b = _checked_source(db, right)
    diffs = _scope_delta(scopes, left["source_id"], right["source_id"])
    entry = {"question": item["question"],
             "source_ids": [left["source_id"], right["source_id"]],
             "scope_differences": diffs,
             "left": a, "right": b}
    if a is None or b is None:
        entry.update(status="unavailable_evidence",
                     explanation="One or both exact approved PDF quotations are absent.")
    elif diffs:
        entry.update(status="different_declared_scope",
                     explanation="Different declared applications/revisions are not a specification contradiction.")
    elif a["label"] != b["label"]:
        entry.update(status="different_property_labels",
                     explanation="Different measurement labels cannot be assumed equivalent.")
    else:
        normalized_a = _normalized(a)
        normalized_b = _normalized(b)
        if normalized_a is None or normalized_b is None or normalized_a[0] != normalized_b[0]:
            entry.update(status="incomparable_units",
                         explanation="No supported safe dimensional comparison.")
        elif normalized_a[1] != normalized_b[1]:
            entry.update(status="numeric_difference_candidate",
                         dimension=normalized_a[0],
                         normalized_values=[format(normalized_a[1], "f"),
                                            format(normalized_b[1], "f")],
                         explanation="Review source application, component, service conditions and revision; not a verified contradiction.")
        else:
            entry.update(status="numeric_agreement_only",
                         dimension=normalized_a[0],
                         normalized_values=[format(normalized_a[1], "f"),
                                            format(normalized_b[1], "f")],
                         explanation="Equal printed values do not establish fitment or independent verification.")
    return entry


def _current(db, idx, ids, citations, measurements):
    """Final single-snapshot gate prevents mixed stale or revoked evidence."""
    shas = [sid[4:] for sid in ids]
    marks = ",".join("?" for _ in shas)
    original = {r["sha256"]: r for r in db.execute(
        "SELECT sha256,filename,updated_at,excerpts FROM documents "
        "WHERE sha256 IN (" + marks + ") AND status='approved' AND reviewed=1 "
        "AND excerpts>0", shas)}
    indexed = {r["sha"]: r for r in idx.execute(
        "SELECT * FROM rel_docs WHERE sha IN (" + marks + ")", shas)}
    for sha in shas:
        a, b = original.get(sha), indexed.get(sha)
        if (a is None or b is None or not b["complete"] or
                str(a["filename"]) != b["filename"] or
                int(a["updated_at"]) != b["updated_at"] or
                int(a["excerpts"]) != b["excerpt_count"]):
            return False
    for mention in citations:
        sha = mention["source_id"][4:]
        rows = db.execute("SELECT text FROM excerpts WHERE sha256=? AND page=? "
                          "AND part=?", (sha, mention["page"], mention["part"]))
        if not any(x["kind"] == mention["kind"] and
                   x["token"] == mention["token"] and
                   x["quote"] == mention["quote"] and
                   x["quote_sha256"] == mention["quote_sha256"]
                   for row in rows
                   for x in _extract(sha, mention["page"], mention["part"], row["text"])):
            return False
    for item in measurements:
        for side in ("left", "right"):
            verified = item[side]
            if verified is None:
                continue
            pointer = {k: verified[k] for k in REF_FIELDS}
            refreshed = _checked_source(db, pointer)
            if refreshed is None or refreshed != verified:
                return False
    return True


def compare(request, *, source, index):
    """Return bounded lexical links and numeric *review candidates*, never fitment."""
    ids, scopes = _validate(request)
    mentions = []
    for query in request["terms"]:
        result = search(source=source, index=index, kind=query["kind"],
                        term=query["term"], source_ids=ids, limit=MAX_MATCHES)
        if result["status"] != "source_checked" or not result["complete"]:
            return {"status": "stale_or_incomplete_index", "complete": False,
                    "source_ids": ids, "shared_mentions": [], "measurement_reviews": [],
                    "reason": result["status"]}
        if result["has_more"]:
            return {"status": "bounded_index_query", "complete": False,
                    "source_ids": ids, "shared_mentions": [], "measurement_reviews": [],
                    "reason": "More than 64 mentions for an exact term; narrow the query"}
        mentions.append((query, result["matches"]))
    comparisons = []
    all_citations = []
    for query, rows in mentions:
        all_citations.extend(rows)
        grouped = {sid: [r for r in rows if r["source_id"] == sid] for sid in ids}
        for left, right in combinations(ids, 2):
            if not grouped[left] or not grouped[right]:
                continue
            differences = _scope_delta(scopes, left, right)
            comparisons.append({
                "kind": query["kind"], "term": query["term"],
                "source_ids": [left, right],
                "status": ("different_declared_scope" if differences
                           else "shared_mention_requires_application_review"),
                "scope_differences": differences,
                "mention_counts": [len(grouped[left]), len(grouped[right])],
                "citations": [grouped[left][:3], grouped[right][:3]],
                "citations_display_limited": (len(grouped[left]) > 3 or len(grouped[right]) > 3),
                "interpretation": "Printed lexical match only, NOT part interchangeability or procedural equivalence"
            })
    with closing(connect_source(Path(source))) as original, closing(open_index(index)) as derived:
        original.execute("BEGIN")
        derived.execute("BEGIN")
        reviews = [_measure(original, x, scopes) for x in request["measurements"]]
        if not _current(original, derived, ids, all_citations, reviews):
            return {"status": "stale_or_revoked_during_comparison", "complete": False,
                    "source_ids": ids, "shared_mentions": [], "measurement_reviews": []}
    missing = any(r["status"] == "unavailable_evidence" for r in reviews)
    return {"kind": "sirus-manual-comparison-v1",
            "status": "unresolved_measurements" if missing else "source_checked",
            "complete": not missing, "source_ids": ids,
            "scope_authority": "operator_declared_not_verified_against_application",
            "sources": request["sources"],
            "shared_mentions": comparisons, "measurement_reviews": reviews,
            "notes": [
                "A shared part number or heading does not prove interchangeable components.",
                "Scope differences are NOT contradictions; equal declared scopes still require independent applicability review.",
                "Numeric differences are review candidates, not verified service-specification conflicts.",
                "No diagrams, unextracted pages, model drafts, training data or outside documents are examined."
            ], "read_only": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    args = parser.parse_args(argv)
    raw = sys.stdin.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        parser.error("Request exceeds 16384 characters")
    try:
        request = json.loads(raw)
        result = compare(request, source=args.source, index=args.index)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.error(str(error))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
