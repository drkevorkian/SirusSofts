#!/usr/bin/env python3
"""Offline PDF staging on a Mac worker; never transmits document contents.

Produces bounded, per-document SHA-256 and extracted-text artifacts for an
operator-reviewed ingestion step. No root, browser, download, or remote scripts.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

MAX_PDFS = 50
MAX_FILE = 64 * 1024 * 1024
MAX_TEXT = 2 * 1024 * 1024


def digest_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def valid_source(source):
    path = Path(source).expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError("Research source must be a real absolute directory")
    return path.resolve(strict=True)


def valid_destination(destination, source):
    target = Path(destination).expanduser()
    if not target.is_absolute() or target.is_symlink():
        raise ValueError("Research destination must be absolute and not a symlink")
    # Resolve macOS /var -> /private/var aliases on BOTH sides.
    origin = Path(source).expanduser().resolve(strict=True)
    real = target.resolve(strict=False)
    if real == origin or origin in real.parents or real in origin.parents:
        raise ValueError("Research input and output directories must be disjoint")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    real = target.resolve(strict=True)
    if real == origin or origin in real.parents or real in origin.parents:
        raise ValueError("Research input and output directories must be disjoint")
    return real


def scan(source, destination, *, converter=None):
    src = valid_source(source)
    dst = valid_destination(destination, src)
    converter = converter or shutil.which("pdftotext")
    if not converter or not Path(converter).is_file():
        raise RuntimeError("Install poppler pdftotext to extract PDFs")
    processed, skipped, errors = 0, 0, 0
    # No links: do not follow a tree outside the configured input directory.
    for path in sorted(src.rglob("*")):
        if processed + skipped + errors >= MAX_PDFS:
            break
        if path.suffix.lower() != ".pdf":
            continue
        if path.is_symlink() or not path.is_file():
            skipped += 1
            continue
        try:
            size = path.stat().st_size
            if size < 1 or size > MAX_FILE:
                skipped += 1
                continue
            sha = digest_file(path)
            output = dst / (sha + ".json")
            if output.is_file() and not output.is_symlink():
                skipped += 1
                continue
            # Same filesystem, restricted temp ownership, atomic final rename.
            fd, tmpname = tempfile.mkstemp(prefix=".pdf-extract-", dir=dst)
            os.close(fd)
            try:
                cmd = subprocess.run([converter, "-enc", "UTF-8", str(path), tmpname],
                                     capture_output=True, timeout=50, check=False)
                if cmd.returncode or os.stat(tmpname).st_size > MAX_TEXT:
                    errors += 1
                    continue
                text = Path(tmpname).read_text(encoding="utf8", errors="replace")
                if not text.strip():
                    errors += 1
                    continue
                record = {"schema": "sirus-mac-staging-v1",
                          "sha256": sha, "source_name": path.name[:160],
                          "source_bytes": size, "extracted_at": int(time.time()),
                          "extraction": "pdftotext", "approved": False,
                          "text": text}
                fd2, pending = tempfile.mkstemp(prefix=".pdf-stage-", dir=dst)
                try:
                    with os.fdopen(fd2, "w", encoding="utf8") as out:
                        json.dump(record, out, ensure_ascii=False)
                        out.write("\n")
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(pending, output)
                    processed += 1
                finally:
                    if os.path.exists(pending):
                        os.unlink(pending)
            finally:
                if os.path.exists(tmpname):
                    os.unlink(tmpname)
        except (OSError, UnicodeError, subprocess.SubprocessError):
            errors += 1
    return {"processed": processed, "skipped": skipped, "errors": errors,
            "output": str(dst), "staging_only": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if not config.get("research_enabled"):
        parser.error("Enable research_enabled in local configuration first")
    result = scan(config["research_source_dir"], config["research_output_dir"])
    print(json.dumps(result))


if __name__ == "__main__":
    main()
