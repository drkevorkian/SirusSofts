#!/usr/bin/env python3
"""Offline, read-only access to SHA-256 verified private research snapshots.

The only supported mutation remains the separate full snapshot/restore-copy
workflow. Archived drafts, original citations, human review and immutable audit
history are never pruned, promoted into RAG, or sent over the network here.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from research_archive import _private_dir, verify_snapshot, _ARCHIVE_NAME

SOURCE_ID = re.compile(r"(?:pdf:[a-f0-9]{64}|research:[a-f0-9]{32})\Z")
JOB_ID = re.compile(r"[a-f0-9]{32}\Z")
MAX_SNAPSHOTS = 64


def _limit(value, label, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid " + label)
    return value


@contextmanager
def _readonly(snapshot):
    # Snapshot verification takes place before this URI is constructed.
    # immutable=1 prevents WAL/SHM creation against an archived copy.
    uri = "file:" + quote(str(snapshot), safe="/") + "?mode=ro&immutable=1"
    db = sqlite3.connect(uri, uri=True, timeout=15)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        yield db
    finally:
        db.close()


def catalog(archive_directory):
    """Verify every snapshot in a small private directory; never silently skip bad ones."""
    directory = _private_dir(archive_directory)
    snapshots = sorted(path for path in directory.iterdir()
                       if _ARCHIVE_NAME.fullmatch(path.name))
    if len(snapshots) > MAX_SNAPSHOTS:
        raise ValueError("Too many snapshots; select a smaller private archive directory")
    # A crash may leave an orphan manifest. Never claim the set is complete.
    manifests = {path.name[:-5] for path in directory.iterdir()
                 if path.name.endswith(".sqlite3.json")
                 and _ARCHIVE_NAME.fullmatch(path.name[:-5])}
    if manifests - {path.name for path in snapshots}:
        raise ValueError("Archive manifest has no corresponding SQLite snapshot")
    rows = []
    for snapshot in snapshots:
        checked = verify_snapshot(snapshot)  # hash, schema, row counts, integrity
        rows.append({"archive":checked["archive"],
                     "sha256":checked["sha256"], "bytes":checked["bytes"],
                     "sources":checked["tables"].get("cr_sources", 0),
                     "jobs":checked["tables"].get("cr_jobs", 0),
                     "passes":checked["tables"].get("cr_results", 0),
                     "audit_events":checked["tables"].get("cr_events", 0),
                     "verified":True})
    return {"kind":"private-archive-catalog-v1", "snapshots":rows,
            "total":len(rows), "complete_scan":True, "no_live_writes":True}


def locate_source(archive_directory, source_id):
    """Find every verified cold snapshot containing one exact source ID.

    Individual snapshots are independent points in time; identical old pass
    rows in successive snapshots are not counted as independent evidence.
    """
    if not isinstance(source_id,str) or not SOURCE_ID.fullmatch(source_id):
        raise ValueError("Invalid original source identifier")
    listing=catalog(archive_directory)  # verifies every file, fails on corruption
    directory=Path(archive_directory)
    matches=[]
    for row in listing["snapshots"]:
        with _readonly(directory/row["archive"]) as db:
            source=db.execute("""SELECT id,kind,revision,content_sha,
                extraction_status,eligible,present FROM cr_sources
                WHERE id=?""",(source_id,)).fetchone()
            if source is None:
                continue
            count=db.execute("""SELECT count(*) FROM cr_jobs j
                JOIN cr_sections s ON s.slot=j.slot AND s.revision=j.revision
                WHERE s.source_id=?""",(source_id,)).fetchone()[0]
            passes=db.execute("""SELECT count(*) FROM cr_results r
                JOIN cr_jobs j ON j.id=r.job_id
                JOIN cr_sections s ON s.slot=j.slot AND s.revision=j.revision
                WHERE s.source_id=?""",(source_id,)).fetchone()[0]
            matches.append({"archive":row["archive"],
                            "archive_sha256":row["sha256"],
                            "source":dict(source),
                            "jobs":count,"passes":passes})
    return {"kind":"private-cold-source-locator-v1",
            "source_id":source_id,"snapshots_checked":listing["total"],
            "matches":matches,"count":len(matches),
            "repeated_snapshots_not_independent_evidence":True,
            "complete_scan":True,"no_live_writes":True}


def locate_job(archive_directory, job_id):
    """Find exactly one historic job across snapshots, without exposing notes."""
    if not isinstance(job_id,str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid historic job identifier")
    listing=catalog(archive_directory)
    directory=Path(archive_directory)
    matches=[]
    for row in listing["snapshots"]:
        with _readonly(directory/row["archive"]) as db:
            job=db.execute("""SELECT j.id,j.cycle,j.state,j.pass_completed,
                j.passes,s.source_id,s.source_revision,s.page,s.part
                FROM cr_jobs j JOIN cr_sections s
                ON s.slot=j.slot AND s.revision=j.revision WHERE j.id=?""",
                (job_id,)).fetchone()
            if job is not None:
                matches.append({"archive":row["archive"],
                                "archive_sha256":row["sha256"],
                                "job":dict(job)})
    return {"kind":"private-cold-job-locator-v1",
            "job_id":job_id,"snapshots_checked":listing["total"],
            "matches":matches,"count":len(matches),
            "complete_scan":True,"no_live_writes":True}


def source_history(snapshot, source_id, *, offset=0, audit_offset=0,
                   show_private_drafts=False):
    """Return independently paged source history from exactly one verified snapshot.

    Default is citation/hash metadata only. Explicit show_private_drafts=True
    returns at most eight unreviewed outputs for trusted local operator use.
    """
    if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
        raise ValueError("Invalid original source identifier")
    _limit(offset, "pass offset", 1000000)
    _limit(audit_offset, "audit offset", 1000000)
    if type(show_private_drafts) is not bool:
        raise ValueError("Draft output selection must be explicit")
    verified = verify_snapshot(snapshot)
    snapshot = Path(snapshot)
    with _readonly(snapshot) as db:
        available = {row["name"] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"cr_sources", "cr_sections", "cr_jobs",
                    "cr_results", "cr_events"}
        if not required <= available:
            raise ValueError("Archived history tables are incomplete")
        source = db.execute("""SELECT id,kind,title,revision,content_sha,
            extraction_status,reviewed,pages,extracted_parts,missing_pages,
            eligible,excluded,priority,present FROM cr_sources WHERE id=?""",
            (source_id,)).fetchone()
        if source is None:
            raise ValueError("Source not present in this historical snapshot")
        scope = """FROM cr_jobs j JOIN cr_sections s
            ON s.slot=j.slot AND s.revision=j.revision
            WHERE s.source_id=?"""
        jobs_total = db.execute("SELECT count(*) " + scope,
                                (source_id,)).fetchone()[0]
        count = db.execute("""SELECT count(*) FROM cr_results r
            JOIN cr_jobs j ON j.id=r.job_id JOIN cr_sections s
            ON s.slot=j.slot AND s.revision=j.revision
            WHERE s.source_id=?""",(source_id,)).fetchone()[0]
        fields = """r.job_id,r.pass_number,r.sha256,r.review_state,
            r.created,r.duration_ms,r.output_chars,j.cycle,j.state,
            s.page,s.part,s.revision,s.source_revision,s.current"""
        if show_private_drafts:
            fields += ",r.notes"
        passes = db.execute("SELECT " + fields + """
            FROM cr_results r JOIN cr_jobs j ON j.id=r.job_id
            JOIN cr_sections s ON s.slot=j.slot AND s.revision=j.revision
            WHERE s.source_id=? ORDER BY r.created DESC,r.job_id DESC,
            r.pass_number DESC LIMIT 8 OFFSET ?""",
            (source_id,offset)).fetchall()
        audit_scope = """FROM cr_events e WHERE e.subject=?
            OR EXISTS (SELECT 1 FROM cr_jobs j JOIN cr_sections s
              ON s.slot=j.slot AND s.revision=j.revision
              WHERE j.id=e.subject AND s.source_id=?)"""
        audit_total = db.execute("SELECT count(*) " + audit_scope,
                                 (source_id,source_id)).fetchone()[0]
        events = db.execute("""SELECT e.id,e.happened,e.kind,e.subject,e.detail
            """ + audit_scope + """ ORDER BY e.id DESC LIMIT 20 OFFSET ?""",
            (source_id,source_id,audit_offset)).fetchall()
        # Return reviewer state with source-revision and immutable quotation
        # hash. A 'supported' label is a HUMAN quote check, not an assertion
        # that a service instruction is independently safe or current now.
        review = []
        if {"cr_evidence","cr_claims","cr_claim_versions"} <= available:
            review = db.execute("""SELECT v.claim_id,v.version,v.state,
                v.quote_sha,v.source_revision,v.applicability,
                v.reviewed_by,v.created,e.page,e.part
                FROM cr_claim_versions v JOIN cr_claims c
                  ON c.id=v.claim_id
                JOIN cr_evidence e ON e.id=c.evidence_id
                WHERE e.source_id=?
                ORDER BY v.created DESC,v.claim_id DESC,v.version DESC
                LIMIT 12""",(source_id,)).fetchall()
    return {"kind":"private-cold-source-history-v1",
            "archive":verified["archive"],
            "archive_sha256":verified["sha256"],"snapshot_verified":True,
            "source":dict(source),
            "jobs_total":jobs_total,"pass_results_total":count,
            "pass_offset":offset,"pass_has_more":offset+len(passes)<count,
            "passes":[dict(row) for row in passes],
            "audit_total":audit_total,"audit_offset":audit_offset,
            "audit_has_more":audit_offset+len(events)<audit_total,
            "audit_events":[dict(row) for row in events],
            "claim_versions":[dict(row) for row in review],
            "drafts_included":show_private_drafts,
            "unreviewed_draft_output":True,
            "verified_service_procedures":False,
            "no_live_writes":True,"training_export":False}


def job_provenance(snapshot, job_id, *, show_private_drafts=False):
    """Inspect one historic job without trusting a title or implicit source mapping."""
    if not isinstance(job_id,str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid historical job ID")
    if type(show_private_drafts) is not bool:
        raise ValueError("Draft output selection must be explicit")
    checked=verify_snapshot(snapshot)
    with _readonly(Path(snapshot)) as db:
        row=db.execute("""SELECT j.id,j.cycle,j.slot,j.revision,
            j.state,j.pass_completed,j.passes,j.attempts,j.created,j.updated,
            s.source_id,s.page,s.part,s.source_revision,s.current
            FROM cr_jobs j JOIN cr_sections s
              ON s.slot=j.slot AND s.revision=j.revision
            WHERE j.id=?""",(job_id,)).fetchone()
        if not row:
            raise ValueError("Job not found in this private snapshot")
        fields = ("pass_number,sha256,review_state,created,duration_ms,"
                  "output_chars")
        if show_private_drafts:
            fields += ",notes"
        # Max 100 passes by the scheduler, but refuse oversized malformed
        # snapshot counts rather than silently truncating provenance.
        count=db.execute("SELECT count(*) FROM cr_results WHERE job_id=?",
                         (job_id,)).fetchone()[0]
        if count>100:
            raise ValueError("Historical job has more than 100 checkpointed passes")
        passes=db.execute("SELECT "+fields+"""
            FROM cr_results WHERE job_id=? ORDER BY pass_number""",
            (job_id,)).fetchall()
        events=db.execute("""SELECT id,happened,kind,subject,detail
            FROM cr_events WHERE subject=? ORDER BY id DESC LIMIT 100""",
            (job_id,)).fetchall()
    return {"kind":"private-cold-job-provenance-v1",
            "archive":checked["archive"],
            "archive_sha256":checked["sha256"],"snapshot_verified":True,
            "job":dict(row),"pass_results":[dict(r) for r in passes],
            "audit_events":[dict(r) for r in events],
            "audit_events_limited_to":100,"drafts_included":show_private_drafts,
            "verified_service_procedures":False,"no_live_writes":True}


def retention_preview(snapshot, *, before_unix):
    """Offline-only eligibility *estimates*. No pruning or approval token."""
    if type(before_unix) is not int or not 0 < before_unix < 2**40:
        raise ValueError("Invalid exclusive cutoff timestamp")
    checked=verify_snapshot(snapshot)
    with _readonly(Path(snapshot)) as db:
        row=db.execute("""SELECT count(*) jobs,
            COALESCE(sum(j.pass_completed),0) checkpointed_passes,
            count(DISTINCT s.source_id) sources
            FROM cr_jobs j JOIN cr_sections s
            ON s.slot=j.slot AND s.revision=j.revision
            JOIN cr_cycles c ON c.id=j.cycle
            WHERE c.state='completed' AND j.updated<?
              AND s.current=0
              AND j.state IN ('completed','failed','superseded')""",
            (before_unix,)).fetchone()
        active=db.execute("""SELECT count(*) FROM cr_jobs
            WHERE state IN ('queued','leased')""").fetchone()[0]
        reviews=db.execute("""SELECT count(*) FROM cr_claim_versions""").fetchone()[0] \
            if "cr_claim_versions" in {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")} else 0
    return {"kind":"private-retention-preview-v1",
            "archive":checked["archive"],
            "archive_sha256":checked["sha256"],
            "cutoff_exclusive":before_unix,
            "historical_closed_revision_jobs":row["jobs"],
            "historical_checkpointed_passes":row["checkpointed_passes"],
            "distinct_source_ids":row["sources"],
            "active_jobs_in_snapshot":active,
            "historical_review_versions":reviews,
            "archive_verified":True,
            "pruning_permitted":False,
            "reason":"Offline estimate only: active/immutable cross-references, "
                     "live state, archive recovery and retention dependencies "
                     "must be independently verified before any deletion.",
            "no_live_writes":True}


def main():
    parser=argparse.ArgumentParser(description="Private offline cold-history inspection")
    command=parser.add_subparsers(dest="command",required=True)
    listing=command.add_parser("catalog")
    listing.add_argument("--directory",type=Path,required=True)
    source_locator=command.add_parser("locate-source")
    source_locator.add_argument("--directory",type=Path,required=True)
    source_locator.add_argument("--source-id",required=True)
    job_locator=command.add_parser("locate-job")
    job_locator.add_argument("--directory",type=Path,required=True)
    job_locator.add_argument("--job-id",required=True)
    source=command.add_parser("source-history")
    source.add_argument("--archive",type=Path,required=True)
    source.add_argument("--source-id",required=True)
    source.add_argument("--offset",type=int,default=0)
    source.add_argument("--audit-offset",type=int,default=0)
    source.add_argument("--include-private-drafts",action="store_true")
    job=command.add_parser("job-provenance")
    job.add_argument("--archive",type=Path,required=True)
    job.add_argument("--job-id",required=True)
    job.add_argument("--include-private-drafts",action="store_true")
    preview=command.add_parser("retention-preview")
    preview.add_argument("--archive",type=Path,required=True)
    preview.add_argument("--before-unix",type=int,required=True)
    args=parser.parse_args()
    if args.command=="catalog":
        data=catalog(args.directory)
    elif args.command=="locate-source":
        data=locate_source(args.directory,args.source_id)
    elif args.command=="locate-job":
        data=locate_job(args.directory,args.job_id)
    elif args.command=="source-history":
        data=source_history(args.archive,args.source_id,offset=args.offset,
                            audit_offset=args.audit_offset,
                            show_private_drafts=args.include_private_drafts)
    elif args.command=="job-provenance":
        data=job_provenance(args.archive,args.job_id,
                            show_private_drafts=args.include_private_drafts)
    else:
        data=retention_preview(args.archive,before_unix=args.before_unix)
    print(json.dumps(data,sort_keys=True))


if __name__=="__main__":
    main()
