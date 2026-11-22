#!/usr/bin/env python3
"""Offline, evidence-bound numerical feasibility checks for technical designs.

This conservative helper NEVER certifies a complete modification. It verifies
measurement quotations against the approved private original-PDF catalog,
compares only supported compatible units, and reports unknown interfaces.
There is no network, model invocation, training, write, or shell capability.
"""
from contextlib import closing
from decimal import Decimal, InvalidOperation
import hashlib
import re

from manual_rag import connect_source
from research_evidence import parse_evidence

_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_SHA = re.compile(r"pdf:[0-9a-f]{64}\Z")
# Conversion to one base unit per physical dimension; no approximated
# conversion from a model answer, and no assumed manufacturing tolerance.
_UNITS = {
    "mm": ("length_mm", Decimal("1")),
    "cm": ("length_mm", Decimal("10")),
    "nm": ("torque_nm", Decimal("1")),
    "ft-lb": ("torque_nm", Decimal("1.3558179483314004")),
    "in-lb": ("torque_nm", Decimal("0.1129848290276167")),
    "v": ("voltage_v", Decimal("1")),
    "a": ("current_a", Decimal("1")),
    "ma": ("current_a", Decimal("0.001")),
    "kpa": ("pressure_kpa", Decimal("1")),
    "bar": ("pressure_kpa", Decimal("100")),
    "psi": ("pressure_kpa", Decimal("6.894757293168")),
    "ohm": ("resistance_ohm", Decimal("1")),
    "rpm": ("speed_rpm", Decimal("1")),
}
_CRITERIA = {"at_most", "at_least", "equal"}


def _checked_source(db, ref):
    """Return only a measurement present in the current approved PDF excerpt."""
    if not isinstance(ref, dict) or set(ref) != {"source_id", "page", "part", "quote"}:
        return None
    source_id, page, part, quote = (ref.get(key) for key in
                                   ("source_id", "page", "part", "quote"))
    if (not isinstance(source_id, str) or not _SHA.fullmatch(source_id)
            or type(page) is not int or not 1 <= page <= 20000
            or type(part) is not int or not 1 <= part <= 100000
            or not isinstance(quote, str) or not 1 <= len(quote) <= 180):
        return None
    sha = source_id[4:]
    row = db.execute("""SELECT e.text FROM excerpts e
        JOIN documents d ON d.sha256=e.sha256
        WHERE d.sha256=? AND d.status='approved' AND d.reviewed=1
          AND e.page=? AND e.part=?""", (sha, page, part)).fetchone()
    if row is None:
        return None
    for label, value, unit, original_quote in parse_evidence(row["text"]):
        if original_quote == quote:
            return {"label": label, "value": value, "unit": unit,
                    "source_id": source_id, "page": page, "part": part,
                    "quote": original_quote,
                    "quote_sha256": hashlib.sha256(quote.encode()).hexdigest()}
    return None


def _normalized(measurement):
    unit = _UNITS.get(measurement["unit"])
    if unit is None:
        return None
    try:
        value = Decimal(measurement["value"])
        if not value.is_finite():
            return None
    except (InvalidOperation, ValueError, TypeError):
        return None
    return unit[0], value * unit[1]


def assess(plan, *, source):
    """Evaluate an operator-defined bounded set of quantitative requirements.

    A check describes its subject, criterion and two exact PDF quote pointers.
    Neither a label nor a proposed source-to-vehicle applicability is inferred
    from the PDF; applicability still needs human confirmation. Missing
    information remains unresolved even if other checks pass.
    """
    if not isinstance(plan, dict) or set(plan) != {"project", "checks"}:
        raise ValueError("Expected a project and checks")
    if not isinstance(plan["project"], str) or not 1 <= len(plan["project"]) <= 180:
        raise ValueError("Invalid project")
    checks = plan["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= 40:
        raise ValueError("Provide 1-40 requirements")
    ids = set()
    for item in checks:
        if not isinstance(item, dict) or set(item) != {
                "id", "question", "criterion", "actual", "limit"}:
            raise ValueError("Invalid requirement")
        key, question, criterion = item["id"], item["question"], item["criterion"]
        if (not isinstance(key, str) or not _ID.fullmatch(key) or key in ids
                or not isinstance(question, str) or not 1 <= len(question) <= 240
                or criterion not in _CRITERIA):
            raise ValueError("Invalid or duplicate requirement")
        ids.add(key)

    results = []
    with closing(connect_source(source)) as db:
        for item in checks:
            actual = _checked_source(db, item["actual"])
            limit = _checked_source(db, item["limit"])
            record = {"id": item["id"], "question": item["question"],
                      "criterion": item["criterion"],
                      "actual": actual, "limit": limit}
            if actual is None or limit is None:
                record.update(outcome="missing_verified_measurement",
                              reasoning="One or both original, approved PDF measurements are missing.")
            else:
                a = _normalized(actual)
                b = _normalized(limit)
                if a is None or b is None or a[0] != b[0]:
                    record.update(outcome="incompatible_or_unsupported_units",
                                  reasoning="Dimensions or units cannot be compared safely.")
                else:
                    _, observed = a
                    _, threshold = b
                    ok = (observed <= threshold if item["criterion"] == "at_most"
                          else observed >= threshold if item["criterion"] == "at_least"
                          else observed == threshold)
                    record.update(
                        outcome="measured_constraint_satisfied" if ok
                        else "measured_constraint_violated",
                        normalized_dimension=a[0],
                        normalized_actual=str(observed),
                        normalized_limit=str(threshold),
                        reasoning=("This numeric check passes; it does not prove overall fit."
                                   if ok else
                                   "The measured requirement is violated; modification, "
                                   "redesign, or a different component may be necessary."))
            results.append(record)
    blockers = [r for r in results if r["outcome"] == "measured_constraint_violated"]
    unresolved = [r for r in results if r["outcome"] in (
        "missing_verified_measurement", "incompatible_or_unsupported_units")]
    verdict = ("measured_constraints_violated" if blockers else
               "insufficient_verified_evidence" if unresolved else
               "numeric_checks_pass_only")
    return {"kind": "sirus-evidence-feasibility-v1", "project": plan["project"],
            "status": verdict, "checks": results, "blocked_count": len(blockers),
            "unresolved_count": len(unresolved),
            "questions_requiring_evidence": [r["question"] for r in unresolved],
            "scope_warning": ("Exact quotations are checked against approved PDF excerpts, "
                "but source-to-machine applicability, installation interfaces, "
                "electrical controls, mechanical loading, clearances, and real-world "
                "safety require independent verification. No complete-fit "
                "approval or fabrication instruction is issued."),
            "read_only": True}
