#!/usr/bin/env python3
"""Read-only original-PDF part, procedure and referenced-document explorer.

Facts are quoted from approved PDF excerpts, never LLM generations. Repeated
identifiers show document mentions, not fitment, compatibility or authority.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re

from manual_rag import connect_source

SOURCE_ID = re.compile(r"pdf:[a-f0-9]{64}\Z")
PART = re.compile(r"(?i)\b(?:part\s*(?:no\.?|number|#)|p/?n)\s*[:#-]?\s*([0-9]{6,10}(?:[-][a-z0-9]{1,4})?)\b")
REFERENCE = re.compile(r"(?i)\b(?:see|refer to|consult)\s+(?:the\s+)?"
    r"((?:service|shop|repair|wiring|electrical|installation)\s+"
    r"(?:manual|diagram|schematic|guide))\b")
HEADING = re.compile(r"(?i)^(?:\d+(?:\.\d+){0,4}[.)]?\s+)?"
    r"(?:remov(?:e|al)|install(?:ation)?|inspect(?:ion)?|adjust(?:ment)?|"
    r"diagnos(?:e|is|tic)|replac(?:e|ement)|test(?:ing)?|procedure)\b"
    r"[^\n]{0,95}$")
MAX_DOCS = 8
MAX_EXCERPTS = 15000
MAX_LINES = 150000
MAX_QUOTE = 180
MAX_MENTIONS = 1200

def _cite(sha, page, part, quote, kind, token):
    return {"source_id": "pdf:"+sha, "page":page, "part":part,
            "kind":kind, "token":token, "quote":quote,
            "quote_sha256":hashlib.sha256(quote.encode("utf8")).hexdigest()}

def _extract(sha,page,part,text):
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line)>MAX_QUOTE:
            continue
        for pattern,kind in ((PART,"part"),(REFERENCE,"document_reference")):
            for match in pattern.finditer(line):
                token=(match.group(1).upper() if kind=="part"
                       else " ".join(match.group(1).lower().split()))
                yield _cite(sha,page,part,line,kind,token)
        if HEADING.fullmatch(line):
            yield _cite(sha,page,part,line,"procedure_heading",line.lower())

def analyze(source_ids, *, source):
    """Bounded original-PDF inspection with explicit incomplete status.

    A document mention of a part is never an assertion of compatibility.
    Missing references are *candidate catalog gaps*, not proof of absence;
    reference phrases might describe a section in the same manual.
    """
    if (not isinstance(source_ids,list) or not 1<=len(source_ids)<=MAX_DOCS
            or len(set(source_ids))!=len(source_ids)
            or any(not isinstance(x,str) or not SOURCE_ID.fullmatch(x)
                   for x in source_ids)):
        raise ValueError("Supply 1-8 unique exact PDF source IDs")
    mentions=[]
    missing=[]
    inspected=0
    inspected_lines=0
    coverage_complete=True
    with closing(connect_source(Path(source))) as db:
        approved=db.execute("""SELECT sha256,filename FROM documents
            WHERE status='approved' AND reviewed=1""").fetchall()
        names={row["sha256"]:row["filename"] for row in approved}
        # Compare only explicitly selected approved documents, never widen
        # silently to an unrelated year, engine, or source.
        for sid in source_ids:
            sha=sid[4:]
            if sha not in names:
                missing.append(sid)
        if missing:
            return {"status":"missing_approved_sources", "missing_sources":missing,
                    "source_ids":source_ids, "complete":False,
                    "mentions":[], "shared_part_mentions":[],
                    "source_summaries":[], "reference_gaps":[]}
        for sid in source_ids:
            sha=sid[4:]
            # LIMIT + 1 detects truncation without silently calling a subset complete.
            excerpts=db.execute("""SELECT page,part,text FROM excerpts
                WHERE sha256=? ORDER BY page,part LIMIT ?""",
                (sha,MAX_EXCERPTS+1)).fetchall()
            if len(excerpts)>MAX_EXCERPTS:
                coverage_complete=False
            for row in excerpts[:MAX_EXCERPTS]:
                inspected+=1
                for raw in row["text"].splitlines():
                    inspected_lines+=1
                    if inspected_lines>MAX_LINES:
                        coverage_complete=False
                        break
                    for item in _extract(sha,row["page"],row["part"],raw):
                        if len(mentions)>=MAX_MENTIONS:
                            coverage_complete=False
                            break
                        mentions.append(item)
                    if len(mentions)>=MAX_MENTIONS:
                        break
                if inspected_lines>MAX_LINES or len(mentions)>=MAX_MENTIONS:
                    break
            if inspected_lines>MAX_LINES or len(mentions)>=MAX_MENTIONS:
                break
    grouped=defaultdict(list)
    per_source=defaultdict(list)
    for item in mentions:
        per_source[item["source_id"]].append(item)
        if item["kind"]=="part":
            grouped[item["token"]].append(item)
    shared=[]
    for part,refs in sorted(grouped.items()):
        distinct={x["source_id"] for x in refs}
        if len(distinct)>=2:
            shared.append({"part_number":part,"source_count":len(distinct),
                           "citations":refs[:24],
                           "warning":"Repeated printed identifier is not interchangeability"})
    summaries=[]
    for sid in source_ids:
        refs=per_source[sid]
        summaries.append({
            "source_id":sid,
            "filename":names[sid[4:]],
            "part_mentions":[x for x in refs if x["kind"]=="part"][:32],
            "procedure_headings":[x for x in refs if x["kind"]=="procedure_heading"][:32],
            "reference_mentions":[x for x in refs if x["kind"]=="document_reference"][:32],
            "scope_verified":False,
            "note":"Extracted headings/identifiers only; not a comprehensive procedural summary"
        })
    gaps=[]
    for item in mentions:
        if item["kind"]!="document_reference":
            continue
        # Absence of exact quoted phrase from other approved filenames is
        # weak catalog evidence; do not assert missing document globally.
        tokens=item["token"].split()
        matches=[sha for sha,name in names.items()
                 if all(t in name.lower().replace("-"," ").replace("_"," ")
                        for t in tokens)]
        if not matches:
            gaps.append({"citation":item,"reference":item["token"],
                         "status":"catalog_title_unmatched",
                         "warning":"Could refer to a section or a differently titled manual"})
        if len(gaps)>=64:
            coverage_complete=False
            break
    return {"status":"partial" if not coverage_complete else "complete_selected_excerpts",
            "complete":coverage_complete, "source_ids":source_ids,
            "inspected_excerpts":inspected,"inspected_lines":inspected_lines,
            "mentions":mentions[:256],
            "total_mentions":len(mentions), "shared_part_mentions":shared[:64],
            "source_summaries":summaries,
            "reference_gaps":gaps,
            "warnings":[
                "Exact part-number repeats do not prove equipment compatibility",
                "No model/year/engine applicability inferred from filenames",
                "Only approved reviewed PDF text; diagrams and inaccessible extracts unexamined",
                "No generated statements can become verified claims or training material"
            ]}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("source_ids",nargs="+")
    args=parser.parse_args(argv)
    try:
        result=analyze(args.source_ids,source=args.source)
    except (ValueError,OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result,sort_keys=True,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
