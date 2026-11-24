#!/usr/bin/env python3
"""Bounded original-PDF procedural ordering and dependency review.

Only explicit operator-selected approved PDF sources are read. Printed step
order and temporal phrases are *review candidates*, not repair instructions,
causal proof, fitment, safety approval, or model-generated knowledge.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from itertools import combinations
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

from manual_rag import connect_source
from manual_relationships import HEADING, SYSTEM_HEADING, SOURCE_ID, _extract
from manual_relationship_index import KEY, open_index, search
from manual_relationship_compare import SCOPE_FIELDS, SCOPE_VALUE, _scope_delta

MAX_INPUT = 16000
MAX_LINES = 160
MAX_CHARS = 12000
MAX_STEPS = 32
MAX_EXPLICIT = 16
MAX_CANDIDATES = 48
STEP = re.compile(r"^\s*(\d{1,2})[.)]\s+(.{4,175})$")
BEFORE = re.compile(r"(?i)^before\s+(.{4,90}?),\s+(.{4,90}?)[.]?$")
AFTER = re.compile(r"(?i)^after\s+(.{4,90}?),\s+(.{4,90}?)[.]?$")
UNTIL = re.compile(r"(?i)^do\s+not\s+(.{4,90}?)\s+until\s+(.{4,90}?)[.]?$")


def _validate(request):
    if not isinstance(request, dict) or set(request) != {"selections"}:
        raise ValueError("Request must contain only selections")
    selections = request["selections"]
    if not isinstance(selections, list) or not 1 <= len(selections) <= 8:
        raise ValueError("Select 1-8 approved manuals explicitly")
    ids, scopes = [], {}
    for selection in selections:
        if (not isinstance(selection, dict) or
                set(selection) != {"source_id", "scope", "heading"}):
            raise ValueError("Each selection needs source_id, scope and heading")
        sid, scope = selection["source_id"], selection["scope"]
        heading = selection["heading"]
        if (not isinstance(sid, str) or not SOURCE_ID.fullmatch(sid)
                or sid in scopes):
            raise ValueError("Invalid or repeated PDF source ID")
        if (not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS
                or type(scope["year"]) is not int
                or not 1900 <= scope["year"] <= 2100
                or any(not isinstance(scope[k], str) or
                       not SCOPE_VALUE.fullmatch(scope[k])
                       for k in SCOPE_FIELDS - {"year"})):
            raise ValueError("Complete operator-declared equipment scope required")
        if (not isinstance(heading, str) or not KEY.fullmatch(heading)
                or not HEADING.fullmatch(heading)
                or heading != heading.strip()):
            raise ValueError("Select one exact indexed procedure heading")
        ids.append(sid)
        scopes[sid] = scope
    return ids, scopes


def _citation(sid, page, part, quote):
    return {"source_id": sid, "page": page, "part": part,
            "quote": quote,
            "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest()}


def _norm(text):
    # Lexical matching only: no synonyms, stemming, or semantic equivalence.
    return " ".join(text.lower().rstrip(" .;:").split())


def _explicit(line):
    """Yield only literal English temporal phrases, never inferred steps."""
    m = BEFORE.fullmatch(line)
    if m:
        return "before", m.group(2).strip(), m.group(1).strip()
    m = AFTER.fullmatch(line)
    if m:
        return "after", m.group(1).strip(), m.group(2).strip()
    m = UNTIL.fullmatch(line)
    if m:
        return "until", m.group(2).strip(), m.group(1).strip()
    return None


def _parse_section(sid, page, part, text, heading_quote):
    lines = text.splitlines()
    starts = [i for i, raw in enumerate(lines) if raw.strip() == heading_quote]
    if len(starts) != 1:
        return None, "heading_not_unique_in_original_excerpt"
    tail = []
    for raw in lines[starts[0] + 1:]:
        line = raw.strip()
        # A new unnumbered heading ends this excerpt-level window.
        if line and not STEP.fullmatch(line) and (
                HEADING.fullmatch(line) or SYSTEM_HEADING.fullmatch(line)):
            break
        tail.append(line)
        if len(tail) > MAX_LINES or sum(len(x) + 1 for x in tail) > MAX_CHARS:
            return None, "procedure_window_exceeds_bounds"
    steps, explicit = [], []
    for line in tail:
        if not line:
            continue
        numbered = STEP.fullmatch(line)
        if numbered:
            no = int(numbered.group(1))
            if no != len(steps) + 1 or len(steps) >= MAX_STEPS:
                return None, "ambiguous_or_oversized_numbering"
            action = numbered.group(2).strip()
            steps.append({"number": no, "action": action, "key": _norm(action),
                          "citation": _citation(sid, page, part, line)})
        temporal = _explicit(line)
        if temporal:
            if len(line) > 180 or len(explicit) >= MAX_EXPLICIT:
                return None, "explicit_relation_limit"
            marker, prerequisite, dependent = temporal
            explicit.append({"marker": marker,
                "prerequisite": prerequisite.rstrip(" ."),
                "dependent": dependent.rstrip(" ."),
                "citation": _citation(sid, page, part, line)})
    return {"source_id": sid, "page": page, "part": part,
            "heading": heading_quote, "heading_citation": _citation(
                sid, page, part, heading_quote),
            "steps": steps, "explicit_relations": explicit,
            "window_lines": len(tail),
            "whole_procedure_verified": False,
            "note": "One selected PDF excerpt; numbered order and explicit phrases are review candidates only"}, None


def _checked_window(src, idx, sid, selected):
    sha = sid[4:]
    row = src.execute("""SELECT filename,updated_at,excerpts
        FROM documents WHERE sha256=? AND status='approved' AND reviewed=1
        AND excerpts>0""", (sha,)).fetchone()
    cached = idx.execute("SELECT * FROM rel_docs WHERE sha=?", (sha,)).fetchone()
    if (row is None or cached is None or not cached["complete"] or
            row["filename"] != cached["filename"] or
            int(row["updated_at"]) != cached["updated_at"] or
            int(row["excerpts"]) != cached["excerpt_count"]):
        return None, "index_or_approval_changed"
    lines = src.execute("""SELECT text FROM excerpts WHERE sha256=? AND page=?
        AND part=?""", (sha, selected["page"], selected["part"])).fetchall()
    if len(lines) != 1:
        return None, "original_excerpt_ambiguous"
    body = lines[0]["text"]
    heading = selected["quote"]
    if not any(e["kind"] == "procedure_heading" and
               e["token"] == selected["token"] and
               e["quote_sha256"] == selected["quote_sha256"] and
               e["quote"] == heading for e in _extract(
                   sha, selected["page"], selected["part"], body)):
        return None, "original_heading_changed"
    return _parse_section(sid, selected["page"], selected["part"], body, heading)


def _compare_steps(a, b):
    """Find *exactly repeated* action sentences printed in reversed order."""
    left, right = a["steps"], b["steps"]
    left_keys = [x["key"] for x in left]
    right_keys = [x["key"] for x in right]
    # Repeated action text in a single procedure is ambiguous: no pairing.
    shared = set(left_keys).intersection(right_keys)
    shared = {x for x in shared if left_keys.count(x) == 1 and right_keys.count(x) == 1}
    result = []
    for first, second in combinations(sorted(shared), 2):
        ia, ja = left_keys.index(first), left_keys.index(second)
        ib, jb = right_keys.index(first), right_keys.index(second)
        if (ia - ja) * (ib - jb) < 0:
            result.append({
                "status": "possible_numbered_order_disagreement",
                "action_keys": [first, second],
                "left": [left[ia]["citation"], left[ja]["citation"]],
                "right": [right[ib]["citation"], right[jb]["citation"]]})
    return result


def _compare_explicit(a, b):
    """Compare identical literal temporal clauses with reversed direction."""
    def by_pair(items):
        output = {}
        for rel in items:
            pre, dep = _norm(rel["prerequisite"]), _norm(rel["dependent"])
            if pre == dep:
                continue
            pair = tuple(sorted((pre, dep)))
            output.setdefault(pair, []).append(rel)
        return output
    la, lb = by_pair(a["explicit_relations"]), by_pair(b["explicit_relations"])
    result = []
    for pair in sorted(la.keys() & lb.keys()):
        if len(la[pair]) != 1 or len(lb[pair]) != 1:
            continue  # ambiguous same-clause repeats
        x, y = la[pair][0], lb[pair][0]
        if (_norm(x["prerequisite"]) == _norm(y["dependent"]) and
                _norm(x["dependent"]) == _norm(y["prerequisite"])):
            result.append({
                "status": "possible_explicit_order_disagreement",
                "phrases": list(pair),
                "left": x["citation"], "right": y["citation"],
                "markers": [x["marker"], y["marker"]]})
    return result


def _revision_review(a, b, differences):
    """Flag changed printed windows across declared revisions, not their age.

    Revision names and catalog timestamps are not publication chronology.
    Neither manual is asserted obsolete; a human must check supersession.
    """
    if set(differences) != {"revision"} or _norm(a["heading"]) != _norm(b["heading"]):
        return None
    def signature(window):
        return (tuple(step["key"] for step in window["steps"]),
                tuple((rel["marker"], _norm(rel["prerequisite"]), _norm(rel["dependent"]))
                      for rel in window["explicit_relations"]))
    left, right = signature(a), signature(b)
    if left == right or not any(left) or not any(right):
        return None
    def evidence(window):
        return {"heading": window["heading_citation"],
                "steps": [s["citation"] for s in window["steps"]],
                "explicit_relations": [r["citation"] for r in window["explicit_relations"]]}
    return {"status": "possible_outdated_procedure_review",
            "declared_revisions": differences["revision"],
            "left": evidence(a), "right": evidence(b),
            "older_source_id": None, "supersession_verified": False,
            "note": "Printed changes across declared revisions; verify publication and applicability before use."}


def inspect(request, *, source, index):
    """Read only approved originals. Fail closed on stale/ambiguous windows."""
    ids, scopes = _validate(request)
    picked = {}
    for item in request["selections"]:
        sid, heading = item["source_id"], item["heading"]
        result = search(source=source, index=index, kind="procedure_heading",
                        term=heading.lower(), source_ids=[sid], limit=2)
        if (result["status"] != "source_checked" or not result["complete"]
                or result["has_more"] or len(result["matches"]) != 1):
            return {"status": "unavailable_or_ambiguous_heading",
                    "complete": False, "source_ids": ids,
                    "windows": [], "comparisons": []}
        picked[sid] = result["matches"][0]
    with closing(connect_source(Path(source))) as src, closing(open_index(index)) as idx:
        src.execute("BEGIN")
        idx.execute("BEGIN")
        windows = []
        for sid in ids:
            window, issue = _checked_window(src, idx, sid, picked[sid])
            if issue:
                return {"status": issue, "complete": False, "source_ids": ids,
                        "windows": [], "comparisons": []}
            windows.append(window)
    comparisons = []
    for a, b in combinations(windows, 2):
        differences = _scope_delta(scopes, a["source_id"], b["source_id"])
        record = {"source_ids": [a["source_id"], b["source_id"]],
                  "scope_differences": differences, "candidates": []}
        if differences:
            record["status"] = "different_declared_scope"
            revision_review = _revision_review(a, b, differences)
            if revision_review is not None:
                record["revision_review"] = revision_review
        elif _norm(a["heading"]) != _norm(b["heading"]):
            record["status"] = "different_selected_headings"
        else:
            candidates = _compare_steps(a, b) + _compare_explicit(a, b)
            if len(candidates) > MAX_CANDIDATES:
                return {"status": "candidate_limit", "complete": False,
                        "source_ids": ids, "windows": [], "comparisons": []}
            record["status"] = ("possible_order_disagreement" if candidates
                                else "no_exact_order_disagreement_detected")
            record["candidates"] = candidates
        comparisons.append(record)
    return {
        "kind": "sirus-procedure-review-v1",
        "status": "source_checked_excerpt_windows", "complete": True,
        "procedure_coverage_complete": False,
        "source_ids": ids, "scope_authority": "operator_declared_not_verified",
        "windows": windows, "comparisons": comparisons,
        "warnings": [
            "No automatic service, repair or safety directions may be inferred.",
            "Step numbers and explicit temporal phrases are printed-text candidates, not causal proof.",
            "Only the selected excerpt of each PDF was examined; later pages, figures and OCR gaps are not validated.",
            "Equal scopes are user-declared, not independently verified against equipment.",
            "Absence of a candidate does not prove procedures agree or are safe."
        ],
        "read_only": True
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--index", type=Path, required=True)
    args = p.parse_args(argv)
    raw = sys.stdin.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        p.error("Input exceeds 16000 characters")
    try:
        result = inspect(json.loads(raw), source=args.source, index=args.index)
    except (ValueError, OSError, sqlite3.Error) as e:
        p.error(str(e))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
