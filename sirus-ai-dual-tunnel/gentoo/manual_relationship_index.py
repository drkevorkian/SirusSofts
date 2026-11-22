#!/usr/bin/env python3
"""Private incremental citation index for approved original-PDF relationships.

A part-number mention is evidence of printed text, NEVER compatibility.
The derived database is not a source of verified claims or training material.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from manual_rag import connect_source
from manual_relationships import SOURCE_ID, _extract

MAX_BATCH = 16
MAX_EXCERPTS_PER_DOC = 20000
MAX_MENTIONS_PER_DOC = 25000
MAX_RESULTS = 100
KINDS = frozenset(("part", "document_reference", "procedure_heading", "system_heading"))
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/()-]{0,95}\Z")


class IndexErrorSafe(ValueError):
    pass


def open_index(path, *, writable=False):
    path = Path(path)
    if path.is_symlink():
        raise IndexErrorSafe("Symlinked relationship index refused")
    if writable:
        if not path.parent.is_dir() or path.parent.is_symlink():
            raise IndexErrorSafe("Private index directory unavailable")
        con = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
    else:
        if not path.is_file():
            raise IndexErrorSafe("Relationship index not built")
        con = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=10)
        con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    return con


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS rel_docs(
      sha TEXT PRIMARY KEY, filename TEXT NOT NULL,
      updated_at INTEGER NOT NULL, excerpt_count INTEGER NOT NULL,
      complete INTEGER NOT NULL CHECK(complete IN (0,1)));
    CREATE TABLE IF NOT EXISTS rel_mentions(
      id TEXT PRIMARY KEY, sha TEXT NOT NULL,
      page INTEGER NOT NULL, part INTEGER NOT NULL,
      kind TEXT NOT NULL, token TEXT NOT NULL,
      quote TEXT NOT NULL, quote_sha256 TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS rel_mentions_lookup
      ON rel_mentions(kind,token,sha,page,part);
    CREATE INDEX IF NOT EXISTS rel_mentions_document ON rel_mentions(sha);
    CREATE TABLE IF NOT EXISTS rel_state(
      id INTEGER PRIMARY KEY CHECK(id=1),
      cursor TEXT NOT NULL DEFAULT '');
    INSERT OR IGNORE INTO rel_state(id,cursor) VALUES(1,'');
    """)


def _replace(db, sha, row, extracted, complete):
    """Atomic per-document replacement, never mix old/new citations."""
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("DELETE FROM rel_mentions WHERE sha=?", (sha,))
        db.execute("DELETE FROM rel_docs WHERE sha=?", (sha,))
        db.execute("""INSERT INTO rel_docs(sha,filename,updated_at,excerpt_count,complete)
                      VALUES(?,?,?,?,?)""", (sha,str(row["filename"]),
                       int(row["updated_at"]),int(row["excerpts"]),int(complete)))
        db.executemany("""INSERT INTO rel_mentions
               (id,sha,page,part,kind,token,quote,quote_sha256)
               VALUES(?,?,?,?,?,?,?,?)""", extracted)
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def sync(*, source, index, batch=MAX_BATCH, force=False):
    """Batch-refresh current approved excerpts. No source DB writes.

    The ingest catalog's updated_at and excerpts count are revision signals;
    --force can re-read otherwise unchanged catalog entries. A partial scan
    is NOT considered complete and is never silently promoted.
    """
    if type(batch) is not int or not 1 <= batch <= MAX_BATCH:
        raise ValueError("Batch must be 1..16")
    with closing(connect_source(Path(source))) as src, closing(open_index(index,writable=True)) as dst:
        schema(dst)
        approved = {row["sha256"]: row for row in src.execute("""
            SELECT sha256,filename,updated_at,excerpts FROM documents
            WHERE status='approved' AND reviewed=1 AND excerpts>0
            ORDER BY sha256""")}
        cached = {r["sha"]:r for r in dst.execute("SELECT * FROM rel_docs")}
        revoked = sorted(set(cached)-set(approved))
        # Revoke all ineligible catalog items before doing any new work.
        if revoked:
            dst.execute("BEGIN IMMEDIATE")
            try:
                for sha in revoked:
                    dst.execute("DELETE FROM rel_mentions WHERE sha=?", (sha,))
                    dst.execute("DELETE FROM rel_docs WHERE sha=?", (sha,))
                dst.execute("COMMIT")
            except Exception:
                dst.execute("ROLLBACK")
                raise
        pending = [sha for sha,row in approved.items() if (
            force or sha not in cached or
            (str(row["filename"]),int(row["updated_at"]),int(row["excerpts"])) !=
            (cached[sha]["filename"],cached[sha]["updated_at"],cached[sha]["excerpt_count"]))]
        cursor = dst.execute("SELECT cursor FROM rel_state WHERE id=1").fetchone()[0]
        # Stable keyset rotation avoids permanently starving later SHA values.
        pending.sort()
        after=[sha for sha in pending if sha>cursor]
        selected=(after+[sha for sha in pending if sha<=cursor])[:batch]
        refreshed=0; partial=0
        for sha in selected:
            doc=approved[sha]
            excerpts=src.execute("""SELECT page,part,text FROM excerpts WHERE sha256=?
               ORDER BY page,part LIMIT ?""", (sha,MAX_EXCERPTS_PER_DOC+1)).fetchall()
            complete=len(excerpts)<=MAX_EXCERPTS_PER_DOC
            mentions=[]
            for item in excerpts[:MAX_EXCERPTS_PER_DOC]:
                # Every extracted quote must originate from this original
                # approved document and exact page/part.
                for evidence in _extract(sha,item["page"],item["part"],item["text"]):
                    if len(mentions)>=MAX_MENTIONS_PER_DOC:
                        complete=False
                        break
                    key="|".join((sha,str(evidence["page"]),str(evidence["part"]),
                                   evidence["kind"],evidence["token"],
                                   evidence["quote_sha256"]))
                    mention_id=hashlib.sha256(key.encode()).hexdigest()
                    mentions.append((mention_id,sha,evidence["page"],evidence["part"],
                        evidence["kind"],evidence["token"],evidence["quote"],
                        evidence["quote_sha256"]))
                if len(mentions)>=MAX_MENTIONS_PER_DOC:
                    break
            _replace(dst,sha,doc,mentions,complete)
            dst.execute("UPDATE rel_state SET cursor=? WHERE id=1",(sha,))
            refreshed+=1
            partial+=not complete
        return {"approved_documents":len(approved), "cached_documents":
                dst.execute("SELECT COUNT(*) FROM rel_docs").fetchone()[0],
                "refreshed":refreshed,"revoked":len(revoked),
                "partial_refreshed":partial,"remaining":len(pending)-refreshed,
                "complete":len(pending)==refreshed and partial==0 and
                dst.execute("SELECT COUNT(*) FROM rel_docs WHERE complete=0").fetchone()[0]==0}


def search(*, source, index, kind, term, source_ids, limit=20, offset=0):
    """Search only current approved originals and an explicit source selection.

    Every result is revalidated against original page/part/quote and
    ingest-catalog revision. This prevents stale index rows becoming evidence
    when a PDF is revoked or updated before the next index sync.
    """
    if kind not in KINDS or not isinstance(term,str) or not KEY.fullmatch(term):
        raise ValueError("Invalid relationship kind or exact term")
    if (not isinstance(source_ids,list) or not 1<=len(source_ids)<=8 or
        len(set(source_ids))!=len(source_ids) or
        any(not isinstance(x,str) or not SOURCE_ID.fullmatch(x) for x in source_ids)):
        raise ValueError("Explicit 1..8 unique source IDs required")
    if type(limit) is not int or not 1<=limit<=MAX_RESULTS or type(offset) is not int or not 0<=offset<=100000:
        raise ValueError("Invalid pagination")
    sha_list=[sid[4:] for sid in source_ids]
    with closing(open_index(index)) as dst, closing(connect_source(Path(source))) as src:
        params=[kind,term,*sha_list]
        markers=",".join("?" for _ in sha_list)
        indexed=dst.execute("SELECT * FROM rel_docs WHERE sha IN ("+markers+")",sha_list).fetchall()
        indexed_by_sha={r["sha"]:r for r in indexed}
        current={r["sha256"]:r for r in src.execute(
            "SELECT sha256,updated_at,filename,excerpts FROM documents WHERE sha256 IN ("+markers+
            ") AND status='approved' AND reviewed=1",sha_list)}
        stale=[sid for sid in source_ids if sid[4:] not in indexed_by_sha or
             sid[4:] not in current or indexed_by_sha[sid[4:]]["updated_at"]!=
             int(current[sid[4:]]["updated_at"]) or
             indexed_by_sha[sid[4:]]["excerpt_count"]!=int(current[sid[4:]]["excerpts"]) or
             indexed_by_sha[sid[4:]]["filename"]!=str(current[sid[4:]]["filename"])]
        if stale:
            return {"status":"stale_or_unindexed", "stale_sources":stale,
                    "matches":[],"complete":False}
        rows=dst.execute("SELECT * FROM rel_mentions WHERE kind=? AND token=? AND sha IN ("+
             markers+") ORDER BY sha,page,part,id LIMIT ? OFFSET ?",
             [*params,limit+1,offset]).fetchall()
        results=[]
        stale_quote=False
        for row in rows[:limit]:
            originals=src.execute("""SELECT text FROM excerpts WHERE sha256=? AND page=?
                AND part=?""",(row["sha"],row["page"],row["part"])).fetchall()
            matched=any(e["quote"]==row["quote"] and
                e["quote_sha256"]==hashlib.sha256(e["quote"].encode()).hexdigest()
                for source_row in originals for e in _extract(
                    row["sha"],row["page"],row["part"],source_row["text"])
                if e["kind"]==row["kind"] and e["token"]==row["token"])
            if not matched:
                stale_quote=True
                break
            results.append({"source_id":"pdf:"+row["sha"],"page":row["page"],
                "part":row["part"],"quote":row["quote"],
                "quote_sha256":row["quote_sha256"],"kind":row["kind"],
                "token":row["token"]})
        if stale_quote:
            return {"status":"stale_original_excerpts", "matches":[],
                    "complete":False,"warning":"Rebuild affected indexed sources"}
        return {"status":"source_checked", "kind":kind,"term":term,
            "matches":results,"offset":offset,"limit":limit,
            "has_more":len(rows)>limit,
            "coverage_complete":all(indexed_by_sha[sid[4:]]["complete"] for sid in source_ids),
            "complete":all(indexed_by_sha[sid[4:]]["complete"] for sid in source_ids),
            "note":"Exact quotation mentions only; no part fitment or procedure correctness inferred"}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--index",type=Path,required=True)
    sub=p.add_subparsers(dest="command",required=True)
    s=sub.add_parser("sync")
    s.add_argument("--batch",type=int,default=MAX_BATCH)
    s.add_argument("--force",action="store_true")
    q=sub.add_parser("search")
    q.add_argument("--kind",required=True,choices=sorted(KINDS))
    q.add_argument("--term",required=True)
    q.add_argument("--source-id",action="append",required=True)
    q.add_argument("--limit",type=int,default=20)
    q.add_argument("--offset",type=int,default=0)
    args=p.parse_args(argv)
    try:
        output=(sync(source=args.source,index=args.index,batch=args.batch,
                     force=args.force) if args.command=="sync" else
                search(source=args.source,index=args.index,kind=args.kind,
                       term=args.term,source_ids=args.source_id,
                       limit=args.limit,offset=args.offset))
    except (OSError,sqlite3.Error,ValueError) as exc:
        p.error(str(exc))
    print(json.dumps(output,sort_keys=True,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
