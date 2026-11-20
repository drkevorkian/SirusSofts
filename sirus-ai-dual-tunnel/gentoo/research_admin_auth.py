#!/usr/bin/env python3
"""Replay-resistant HMAC proof for privileged CMS -> Gentoo research operations.

This is only for internal control POST routes. Browser identity, session,
rate limits and CSRF are verified separately by the existing CMS. The shared
random key must be provisioned independently and never committed or logged.
"""
import hashlib
import hmac
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from continuous_research import connect

ADMIN_ROUTES=frozenset({
    "/api/in/research/continuous/control",
    "/api/in/research/evidence/control",
})
KEY_FILE=Path("/etc/sirus-ai-dual/research-admin.key")
_NONCE=re.compile(r"[0-9a-f]{32}\Z")
_DIGEST=re.compile(r"[0-9a-f]{64}\Z")
_TIME=re.compile(r"[0-9]{10,11}\Z")
MAX_SKEW=120


def init_schema(path):
    with connect(path) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS cr_admin_nonces (
                nonce TEXT PRIMARY KEY, issued_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS cr_admin_nonce_cleanup
                ON cr_admin_nonces(issued_at);
        """)


def signature(secret, timestamp, nonce, path, raw):
    if not isinstance(secret,bytes) or len(secret)!=32:
        raise ValueError("Invalid administrative bridge secret")
    if path not in ADMIN_ROUTES:
        raise ValueError("Not an administrative control route")
    if not isinstance(raw,bytes):
        raise ValueError("Invalid signed payload")
    message=(b"sirus-research-admin-v1\n"+str(timestamp).encode("ascii")+b"\n"
             +nonce.encode("ascii")+b"\n"+path.encode("ascii")+b"\n"+raw)
    return hmac.new(secret,message,hashlib.sha256).hexdigest()


def load_secret(path=KEY_FILE):
    """Reject symlinks, extra permissions, nonregular files and wrong key size."""
    path=Path(path)
    if path.is_symlink():
        return None
    fd=None
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_size!=32 or
            info.st_mode & 0o077):
            return None
        with os.fdopen(fd,"rb") as handle:
            fd=None
            data=handle.read(33)
        return data if len(data)==32 else None
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def verify(headers, path, raw, db_path, *, key_path=KEY_FILE, now=None):
    """Validate CMS signature and consume nonce atomically in the private DB."""
    if path not in ADMIN_ROUTES or not isinstance(raw,bytes):
        return False
    timestamp=headers.get("X-Sirus-Admin-Time","")
    nonce=headers.get("X-Sirus-Admin-Nonce","")
    received=headers.get("X-Sirus-Admin-Signature","")
    if not all(isinstance(s,str) for s in (timestamp,nonce,received)):
        return False
    if not (_TIME.fullmatch(timestamp) and _NONCE.fullmatch(nonce)
            and _DIGEST.fullmatch(received)):
        return False
    now=int(time.time()) if now is None else int(now)
    if abs(int(timestamp)-now)>MAX_SKEW:
        return False
    key=load_secret(key_path)
    if key is None or not hmac.compare_digest(
            signature(key,timestamp,nonce,path,raw),received):
        return False
    try:
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM cr_admin_nonces WHERE issued_at<?",(now-300,))
            db.execute("INSERT INTO cr_admin_nonces(nonce,issued_at) VALUES(?,?)",
                       (nonce,int(timestamp)))
        return True
    except sqlite3.IntegrityError:
        return False
