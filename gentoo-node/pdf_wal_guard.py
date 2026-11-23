#!/usr/bin/env python3
"""Preserve WAL sidecars for read-only readers without giving directory write.

Python's sqlite3.Connection does not expose SQLITE_FCNTL_PERSIST_WAL; use a
second native SQLite handle and close it only after the Python writer closes.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
from pathlib import Path
import pwd
import sqlite3
import subprocess

_SQLITE_OPEN_READWRITE = 0x2
_SQLITE_FCNTL_PERSIST_WAL = 10


def _library():
    name = ctypes.util.find_library("sqlite3")
    if not name:
        raise RuntimeError("SQLite library unavailable for persistent WAL")
    lib = ctypes.CDLL(name)
    lib.sqlite3_open_v2.argtypes = (
        ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_char_p)
    lib.sqlite3_open_v2.restype = ctypes.c_int
    lib.sqlite3_exec.argtypes = (
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p))
    lib.sqlite3_exec.restype = ctypes.c_int
    lib.sqlite3_file_control.argtypes = (
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
    lib.sqlite3_file_control.restype = ctypes.c_int
    lib.sqlite3_close.argtypes = (ctypes.c_void_p,)
    lib.sqlite3_close.restype = ctypes.c_int
    return lib


class WalGuard:
    """Final SQLite writer handle requests persistent WAL/SHM at clean close."""

    def __init__(self, path):
        self._lib = _library()
        self._handle = ctypes.c_void_p()
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("SQLite guard requires an existing regular database")
        rc = self._lib.sqlite3_open_v2(
            os.fsencode(path), ctypes.byref(self._handle),
            _SQLITE_OPEN_READWRITE, None)
        if rc:
            self.close()
            raise RuntimeError(f"SQLite WAL guard open failed ({rc})")
        try:
            rc = self._lib.sqlite3_exec(
                self._handle, b"SELECT count(*) FROM sqlite_master",
                None, None, None)
            if rc:
                raise RuntimeError(f"SQLite WAL guard read failed ({rc})")
            enabled = ctypes.c_int(1)
            rc = self._lib.sqlite3_file_control(
                self._handle, b"main", _SQLITE_FCNTL_PERSIST_WAL,
                ctypes.byref(enabled))
            if rc or enabled.value != 1:
                raise RuntimeError(f"SQLite persistent WAL unavailable ({rc})")
        except BaseException:
            self.close()
            raise

    def close(self):
        handle = getattr(self, "_handle", None)
        if handle is not None and handle.value:
            self._handle = ctypes.c_void_p()
            rc = self._lib.sqlite3_close(handle)
            if rc:
                raise RuntimeError(f"SQLite WAL guard close failed ({rc})")


class PersistentWalConnection(sqlite3.Connection):
    """Close Python database first, SQLite WAL-preservation handle last."""

    def enable_wal_guard(self, path):
        self._wal_guard = WalGuard(path)

    def close(self):
        guard = getattr(self, "_wal_guard", None)
        try:
            super().close()
        finally:
            if guard is not None:
                self._wal_guard = None
                guard.close()


def open_persistent_wal(path, *, timeout=30):
    path = Path(path)
    # Check before sqlite3.connect: SQLite may create or modify a symlink target
    # before WalGuard gets a chance to reject it. New regular DBs are allowed.
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("SQLite WAL path must be a regular file, not a symlink")
    db = sqlite3.connect(
        path, timeout=timeout, isolation_level=None,
        factory=PersistentWalConnection)
    try:
        mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode.lower() != "wal":
            raise RuntimeError("Expected SQLite WAL mode")
        db.enable_wal_guard(path)
        return db
    except BaseException:
        db.close()
        raise


def grant_reader_sidecars(path, reader="sirusai"):
    """Only exact sidecars receive read ACLs; DB ownership stays with writer."""
    try:
        pwd.getpwnam(reader)
    except KeyError:
        # The independent PDF service can be installed before the AI account.
        return False
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm")]
    if any(p.is_symlink() or not p.is_file() for p in sidecars):
        raise RuntimeError("SQLite WAL sidecar missing or unsafe")
    for sidecar in sidecars:
        subprocess.run(
            ("setfacl", "-m", f"u:{reader}:r--", os.fspath(sidecar)),
            check=True, stdout=subprocess.DEVNULL)
    return True
