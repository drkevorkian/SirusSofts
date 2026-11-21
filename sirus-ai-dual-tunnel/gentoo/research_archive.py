#!/usr/bin/env python3
"""Explicit offline private SQLite archive and non-destructive restore.

Never prunes research, audit, evidence or existing chat tables. No network,
untrusted source text, model execution, system service changes or shell calls.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import time
from urllib.parse import quote

ARCHIVE_TABLES = ("cr_config", "cr_sources", "cr_sections", "cr_cycles",
                  "cr_jobs", "cr_results", "cr_events",
                  "cr_job_cancellations", "cr_evidence",
                  "cr_claim_versions")
ARCHIVE_FORMAT = "sirus-private-research-snapshot-v1"
_ARCHIVE_NAME = re.compile(r"research-[0-9]{14}-[0-9a-f]{16}\.sqlite3\Z")


def _private_dir(directory, *, create=False):
    directory = Path(directory)
    if not directory.is_absolute() or directory.is_symlink():
        raise ValueError("A private, absolute, nonsymlink directory is required")
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Archive directory must be mode 0700 or more restrictive")
    return directory


def _regular_file(path, *, private=False):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Regular absolute file required")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Regular file required")
    if private and info.st_mode & 0o077:
        raise ValueError("Private snapshot files must be mode 0600 or stricter")
    return path


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda:stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_counts(path):
    # URI encoding prevents special characters in absolute paths being
    # interpreted as URI query parameters or alternate SQLite filenames.
    uri="file:"+quote(str(path),safe="/")+"?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=20) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("Snapshot failed SQLite integrity check")
        available={r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "cr_config" not in available or "cr_jobs" not in available:
            raise ValueError("Research schema missing from snapshot")
        return {table:db.execute("SELECT count(*) FROM "+table).fetchone()[0]
                for table in ARCHIVE_TABLES if table in available}


def _sync_dir(path):
    fd=os.open(path, os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def archive_snapshot(source, archive_directory, *, now=None):
    """Consistent online SQLite backup, verified before immutable publication.

    Snapshot is complete input.sqlite3 including the non-research tables.
    The separate source PDF ingest database is NOT part of this archive.
    """
    source=_regular_file(source)
    directory=_private_dir(archive_directory,create=True)
    if source.parent.resolve()==directory.resolve():
        raise ValueError("Archive directory must differ from live DB directory")
    now=time.time() if now is None else now
    if not isinstance(now,(int,float)) or not 0<=now<2**40:
        raise ValueError("Invalid archive timestamp")
    ident="research-"+time.strftime("%Y%m%d%H%M%S",time.gmtime(now))+"-"+secrets.token_hex(8)
    final=directory/(ident+".sqlite3")
    manifest=directory/(ident+".sqlite3.json")
    temporary=directory/("."+ident+".pending")
    temp_manifest=directory/("."+ident+".manifest.pending")
    target_created=False
    try:
        fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_RDWR|
                   getattr(os,"O_NOFOLLOW",0),0o600)
        target_created=True
        os.close(fd)
        uri="file:"+quote(str(source),safe="/")+"?mode=ro"
        with sqlite3.connect(uri,uri=True,timeout=20) as original:
            with sqlite3.connect(str(temporary),timeout=20) as output:
                original.backup(output,pages=256,sleep=0.05)
        with open(temporary,"rb") as stream:
            os.fsync(stream.fileno())
        counts=_snapshot_counts(temporary)
        sha=_hash_file(temporary)
        size=temporary.stat().st_size
        descriptor={"kind":ARCHIVE_FORMAT,"created_unix":int(now),
                    "file":final.name,"sha256":sha,
                    "bytes":size,"tables":counts,
                    "includes_source_pdf_bytes":False,
                    "includes_private_drafts":True,
                    "automatic_pruning":False}
        data=(json.dumps(descriptor,sort_keys=True,separators=(",",":"))+"\n").encode()
        fd=os.open(temp_manifest,os.O_CREAT|os.O_EXCL|os.O_WRONLY|
                   getattr(os,"O_NOFOLLOW",0),0o600)
        with os.fdopen(fd,"wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Never overwrite an existing archive. A crash between renames leaves
        # an unverified orphan rather than a manifest for unverified bytes.
        if final.exists() or manifest.exists():
            raise ValueError("Refusing to overwrite archived history")
        os.rename(temporary,final)
        os.rename(temp_manifest,manifest)
        _sync_dir(directory)
        return verify_snapshot(final)
    finally:
        if target_created and temporary.exists():
            temporary.unlink()
        if temp_manifest.exists():
            temp_manifest.unlink()


def verify_snapshot(snapshot):
    snapshot=_regular_file(snapshot,private=True)
    directory=_private_dir(snapshot.parent)
    if not _ARCHIVE_NAME.fullmatch(snapshot.name):
        raise ValueError("Unrecognized private research archive name")
    manifest=_regular_file(directory/(snapshot.name+".json"),private=True)
    meta=json.loads(manifest.read_text(encoding="utf-8"))
    if (not isinstance(meta,dict) or meta.get("kind")!=ARCHIVE_FORMAT or
        meta.get("file")!=snapshot.name or
        not isinstance(meta.get("sha256"),str) or
        not re.fullmatch(r"[0-9a-f]{64}",meta["sha256"]) or
        type(meta.get("bytes")) is not int or
        type(meta.get("tables")) is not dict):
        raise ValueError("Invalid private snapshot manifest")
    if meta["bytes"]!=snapshot.stat().st_size or _hash_file(snapshot)!=meta["sha256"]:
        raise ValueError("Snapshot hash or size mismatch")
    if _snapshot_counts(snapshot)!=meta["tables"]:
        raise ValueError("Snapshot row counts differ from archive manifest")
    return {"archive":snapshot.name,"sha256":meta["sha256"],
            "bytes":meta["bytes"],"tables":meta["tables"],"verified":True}


def restore_into_new_file(snapshot, destination):
    """Restores only to a missing file in an already-private directory.

    Never replaces a live DB. Stop services before separately promoting a
    verified recovery copy; preserve WAL state and the original source DB.
    """
    verified=verify_snapshot(snapshot)
    destination=Path(destination)
    directory=_private_dir(destination.parent)
    if not destination.is_absolute() or destination.is_symlink():
        raise ValueError("Restore destination must be a new absolute file")
    if destination.exists():
        raise ValueError("Restore refuses to replace an existing file")
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|
               getattr(os,"O_NOFOLLOW",0),0o600)
    try:
        with os.fdopen(fd,"wb") as output,open(snapshot,"rb") as original:
            shutil.copyfileobj(original,output,1024*1024)
            output.flush()
            os.fsync(output.fileno())
        if (_hash_file(destination)!=verified["sha256"] or
            _snapshot_counts(destination)!=verified["tables"]):
            raise ValueError("Restored copy failed verification")
        _sync_dir(directory)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {"recovered_file":destination.name,"sha256":verified["sha256"],
            "verified":True,"replaced_existing":False}


def main():
    parser=argparse.ArgumentParser(description="Private, explicit research SQLite backup")
    actions=parser.add_subparsers(dest="action",required=True)
    create=actions.add_parser("create",help="Back up input.sqlite3 consistently")
    create.add_argument("--source",required=True,type=Path)
    create.add_argument("--directory",required=True,type=Path)
    verify=actions.add_parser("verify",help="Verify hash, schema and SQLite integrity")
    verify.add_argument("--archive",required=True,type=Path)
    restore=actions.add_parser("restore-copy",help="Restore to a NEW private file only")
    restore.add_argument("--archive",required=True,type=Path)
    restore.add_argument("--destination",required=True,type=Path)
    args=parser.parse_args()
    if args.action=="create":
        output=archive_snapshot(args.source,args.directory)
    elif args.action=="verify":
        output=verify_snapshot(args.archive)
    else:
        output=restore_into_new_file(args.archive,args.destination)
    print(json.dumps(output,sort_keys=True))


if __name__=="__main__":
    main()
