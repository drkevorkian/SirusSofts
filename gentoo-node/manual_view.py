"""Approved-only, image-only Gentoo PDF viewer.

Invoked solely through the existing signed dashboard bridge. No raw PDF bytes,
file paths, or download endpoints are returned. Poppler renders single pages
into a private temporary directory; all files remain on the dedicated SSD.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import subprocess
import tempfile

SHA_PREFIX = re.compile(r"[a-f0-9]{12}(?:[a-f0-9]{52})?\Z")
MAX_IMAGE = 2_400_000
MAX_PAGES = 20000


class ManualViewError(ValueError):
    pass


def approved_document(db, root: Path, value):
    if not isinstance(value, str) or SHA_PREFIX.fullmatch(value) is None:
        raise ManualViewError("Invalid manual identifier")
    # 12-char prefixes permit cards in already saved historical responses,
    # but only when the approved prefix resolves to exactly one document.
    if len(value) == 12:
        rows = db.execute("""
            SELECT sha256,filename,object_rel,bytes,pages,status,reviewed
              FROM documents WHERE substr(sha256,1,12)=?
              AND status='approved' AND reviewed=1 LIMIT 2""", (value,)).fetchall()
        if len(rows) != 1:
            raise ManualViewError("Unknown or ambiguous approved manual")
        row = rows[0]
    else:
        row = db.execute("""
            SELECT sha256,filename,object_rel,bytes,pages,status,reviewed
              FROM documents WHERE sha256=? AND status='approved'
              AND reviewed=1""", (value,)).fetchone()
        if row is None:
            raise ManualViewError("Manual is not approved")
    sha = row["sha256"]
    rel = f"manuals/objects/{sha[:2]}/{sha}.pdf"
    if row["object_rel"] != rel:
        raise ManualViewError("Invalid stored PDF object path")
    path = root / rel
    if any(p.is_symlink() for p in [path, path.parent, path.parent.parent,
                                   root / "manuals"]):
        raise ManualViewError("PDF object is linked")
    try:
        size = path.stat().st_size
        if not path.is_file() or size != row["bytes"] or size < 32:
            raise ManualViewError("PDF object unavailable")
        with path.open("rb") as f:
            if f.read(5) != b"%PDF-":
                raise ManualViewError("Invalid source PDF header")
    except OSError as e:
        raise ManualViewError("PDF object unreadable") from e
    if not 1 <= row["pages"] <= MAX_PAGES:
        raise ManualViewError("PDF page count unavailable")
    return row, path


def metadata(db, root, payload):
    doc, _ = approved_document(db, root, payload.get("sha256"))
    return {"ok": True, "operation": "manual_meta", "sha256": doc["sha256"],
            "title": doc["filename"][:180], "pages": doc["pages"]}


def render(db, root, payload):
    doc, path = approved_document(db, root, payload.get("sha256"))
    page = payload.get("page")
    if type(page) is not int or not 1 <= page <= doc["pages"]:
        raise ManualViewError("PDF page outside approved document")
    size = payload.get("size", "preview")
    if size not in ("preview", "full"):
        raise ManualViewError("Invalid preview size")
    # Full view is a page-by-page image reader. Never deliver the PDF itself.
    scale = "850" if size == "preview" else "1500"
    with tempfile.TemporaryDirectory(prefix="sirus-manual-page-") as directory:
        prefix = str(Path(directory) / "page")
        cmd = ["pdftoppm", "-f", str(page), "-l", str(page),
               "-singlefile", "-scale-to", scale, "-jpeg",
               "-jpegopt", "quality=73,optimize=y", str(path), prefix]
        try:
            subprocess.run(cmd, check=True, timeout=22,
                           stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            raise ManualViewError("PDF page renderer unavailable") from e
        image = Path(prefix + ".jpg")
        if image.is_symlink() or not image.is_file():
            raise ManualViewError("Rendered page missing")
        if not 200 <= image.stat().st_size <= MAX_IMAGE:
            raise ManualViewError("Rendered page exceeds safe size")
        content = image.read_bytes()
        if not content.startswith(b"\xff\xd8\xff") or not content.endswith(b"\xff\xd9"):
            raise ManualViewError("Invalid rendered JPEG")
    return {"ok": True, "operation": "manual_page", "sha256": doc["sha256"],
            "page": page, "pages": doc["pages"], "title": doc["filename"][:180],
            "mime": "image/jpeg", "base64": base64.b64encode(content).decode("ascii")}
