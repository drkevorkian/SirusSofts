#!/usr/bin/env python3
"""Local-only, read-only probe of signed PDF bridge. Prints no credentials."""
import argparse
import hashlib
import hmac
import json
import secrets
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

import client
import pdf_admin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/etc/sirus-gentoo-node/config.json")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        print("PASS: local Gentoo signed PDF probe module")
        return
    cfg = client.validate_config(json.loads(Path(args.config).read_text()))
    key = client.load_key(cfg["key_file"])
    body = json.dumps({"op": "status", "node_id":cfg["node_id"],
                       "sent_at":int(time.time()),"nonce":secrets.token_hex(16),
                       "limit":1,"offset":0},separators=(",",":")).encode()
    signature = hmac.new(key,pdf_admin.DOMAIN+body,hashlib.sha256).hexdigest()
    request = Request("http://127.0.0.1:18080/internal/pdf-admin",
                      method="POST",data=body,
                      headers={"Content-Type":"application/json",
                               "X-Sirus-Pdf-Signature":signature})
    with urlopen(request,timeout=7) as response:
        doc=json.loads(response.read(120000))
    if doc.get("ok") is not True or doc.get("operation") != "status":
        raise RuntimeError("Signed Gentoo PDF probe was not accepted")
    print("PASS: Signed Gentoo PDF bridge reaches existing SQLite queue; manuals:",
          doc.get("total"),"workers:",doc.get("workers"))


if __name__ == "__main__":
    try: main()
    except Exception as error:
        print("FAIL: PDF bridge local probe:",type(error).__name__,str(error),file=sys.stderr)
        raise SystemExit(1)
