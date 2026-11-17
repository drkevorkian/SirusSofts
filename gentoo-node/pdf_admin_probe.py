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


def signed_request(cfg, key, op, *, max_bytes=120000, timeout=7, **fields):
    payload={"op": op,"node_id":cfg["node_id"],"sent_at":int(time.time()),
             "nonce":secrets.token_hex(16),**fields}
    body=json.dumps(payload,separators=(",",":")).encode()
    signature=hmac.new(key,pdf_admin.DOMAIN+body,hashlib.sha256).hexdigest()
    request=Request("http://127.0.0.1:18080/internal/pdf-admin",
                    method="POST",data=body,
                    headers={"Content-Type":"application/json",
                             "X-Sirus-Pdf-Signature":signature})
    # Local signed bridge, never an environment-configured HTTP proxy.
    from urllib.request import build_opener, ProxyHandler
    with build_opener(ProxyHandler({})).open(request,timeout=timeout) as response:
        raw=response.read(max_bytes+1)
    if len(raw)>max_bytes:raise RuntimeError("Viewer response exceeded safe size")
    data=json.loads(raw)
    if data.get("ok") is not True or data.get("operation") != op:
        raise RuntimeError("Signed PDF operation was not accepted")
    return data


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--config",default="/etc/sirus-gentoo-node/config.json")
    ap.add_argument("--check",action="store_true")
    ap.add_argument("--view-test",action="store_true",
                    help="render one JPEG page of an approved PDF via the live signed loopback")
    args=ap.parse_args()
    if args.check:
        print("PASS: local Gentoo signed PDF probe module")
        return
    cfg=client.validate_config(json.loads(Path(args.config).read_text()))
    key=client.load_key(cfg["key_file"])
    status=signed_request(cfg,key,"status",limit=1,offset=0)
    print("PASS: Signed Gentoo PDF bridge reaches existing SQLite queue; manuals:",
          status.get("total"),"workers:",status.get("workers"))
    if not args.view_test:return
    approved=signed_request(cfg,key,"status",filter="approved",limit=1,offset=0)
    if not approved.get("documents"):
        raise RuntimeError("No approved manual is available for viewer testing")
    doc=approved["documents"][0]
    sha=doc["sha256"]
    meta=signed_request(cfg,key,"manual_meta",sha256=sha)
    if meta.get("sha256")!=sha or not 1 <= meta.get("pages",0) <= 20000:
        raise RuntimeError("Approved manual metadata failed validation")
    image=signed_request(cfg,key,"manual_page",max_bytes=3400000,timeout=30,
                         sha256=sha,page=1,size="preview")
    import base64
    jpg=base64.b64decode(image.get("base64",""),validate=True)
    if image.get("mime")!="image/jpeg" or image.get("sha256")!=sha or (
       not jpg.startswith(b"\xff\xd8\xff") or not jpg.endswith(b"\xff\xd9")):
        raise RuntimeError("Signed manual image did not contain a valid JPEG")
    print("PASS: Signed read-only viewer rendered approved PDF page 1;",
          "pages:",meta["pages"],"JPEG bytes:",len(jpg),
          "manual SHA:",sha[:12])


if __name__ == "__main__":
    try: main()
    except Exception as error:
        print("FAIL: PDF bridge local probe:",type(error).__name__,str(error),file=sys.stderr)
        raise SystemExit(1)
