#!/usr/bin/env python3
"""Fail CI if the canonical Continuous Research checklist or its totals drift.

This script validates tracking metadata only. It does not inspect live hosts,
claim production readiness, or automatically approve individual requirements.
"""
from pathlib import Path
import re
import sys


ROADMAP = (Path(__file__).resolve().parents[1] /
           "docs/CONTINUOUS_RESEARCH_ROADMAP.md")
HEADING = re.compile(r"^## ([1-9]|1[0-3])\. .+$")
TASK = re.compile(r"^- \[([x ])\] (.+)$")
TABLE = re.compile(r"^\|\s*(\d{1,2})\. [^|]+\|\s*(\d+)/(\d+)\s*\|\s*(\d+)\s*\|$")
SUMMARY = re.compile(
    r"^\*\*Overall source checklist: (\d+) / (\d+) checked \((\d+\.\d+)%\)\.\*\*"
)


def validate(text: str) -> dict[int, tuple[int, int]]:
    sections: dict[int, list[int]] = {}
    current = None
    rows: dict[int, tuple[int, int, int]] = {}
    total_line = None
    for line in text.splitlines():
        head = HEADING.fullmatch(line)
        if head:
            current = int(head.group(1))
            if current in sections:
                raise ValueError(f"Duplicate numbered section {current}")
            sections[current] = [0, 0]  # checked, total
            continue
        task = TASK.fullmatch(line)
        if task:
            if current is None:
                raise ValueError("Checklist item occurs outside numbered sections")
            sections[current][1] += 1
            if task.group(1) == "x":
                sections[current][0] += 1
        match = TABLE.fullmatch(line)
        if match:
            number = int(match.group(1))
            if number in rows:
                raise ValueError(f"Duplicate progress-table row {number}")
            rows[number] = tuple(map(int, match.groups()[1:]))
        summary = SUMMARY.match(line)
        if summary:
            if total_line is not None:
                raise ValueError("Duplicate overall summary")
            total_line = (int(summary.group(1)), int(summary.group(2)),
                          float(summary.group(3)))

    expected = set(range(1, 14))
    if set(sections) != expected:
        raise ValueError(f"Expected the 12 original sections plus the final video section; got {sorted(sections)}")
    if set(rows) != expected:
        raise ValueError("Progress table must have exactly one entry per section")
    if sum(total for _, total in (sections[number] for number in range(1, 13))) != 138:
        raise ValueError("Original 138 checklist requirements must be preserved exactly")
    if sections[13] != [0, 1]:
        raise ValueError("Final post-deployment video milestone must remain the sole unchecked Section 13 item")
    if not text.rstrip().endswith("No video-related source work, deployment, or live validation is authorized by adding this roadmap entry.**"):
        raise ValueError("Final deferred video milestone must be the last roadmap entry")
    for number, (checked, total) in sections.items():
        table_checked, table_total, table_open = rows[number]
        if (checked, total, total - checked) != (
            table_checked, table_total, table_open
        ):
            raise ValueError(f"Progress table does not match section {number} checkboxes")

    done = sum(x for x, _ in sections.values())
    all_items = sum(x for _, x in sections.values())
    expected_percent = round(100 * done / all_items, 1)
    if total_line != (done, all_items, expected_percent):
        raise ValueError(f"Summary must be {done}/{all_items} ({expected_percent:.1f}%)")
    if "source implementation" not in text.lower():
        raise ValueError("The source-versus-production distinction is missing")
    if "production deployment and acceptance pending" not in text.lower():
        raise ValueError("Do not conflate source checkmarks with live acceptance")
    return {number: tuple(counts) for number, counts in sections.items()}


def main() -> int:
    try:
        sections = validate(ROADMAP.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"FAIL: Continuous Research roadmap: {exc}", file=sys.stderr)
        return 1
    total = sum(c[1] for c in sections.values())
    done = sum(c[0] for c in sections.values())
    print(f"PASS: Continuous Research checklist: {done}/{total} checked, "
          f"{total-done} open across 13 sections. Source only; not deployed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
