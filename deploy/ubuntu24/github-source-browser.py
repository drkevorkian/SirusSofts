#!/usr/bin/env python3
"""Read-only GitHub source gateway over a private Unix socket.

Only repository IDs stored in CMS SQLite may be browsed. PHP checks
authenticated viewer grants before connecting. This process alone holds
GitHub credentials; no token is returned to PHP or stored in the web root.
"""
import argparse
import base64
import json
import os
import re
import socketserver
import sqlite3
import sys
import threading
import time
from collections import deque
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

MAX_FILE = 192 * 1024
MAX_RESPONSE = 850000
SEGMENT = re.compile(r"^[A-Za-z0-9._@+ -]{1,120}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_REQUESTS_PER_MINUTE = 180

def safe_path(value):
    if not isinstance(value,str) or len(value)>400 or "\0" in value or "\\" in value:
        return False
    if value == "":return True
    if value.startswith("/") or value.endswith("/") or "//" in value:return False
    return all(segment not in (".","..") and SEGMENT.fullmatch(segment)
               for segment in value.split("/"))

def api_request(full_name, branch, path, token, fetch=None):
    if not REPO.fullmatch(full_name) or not safe_path(path):
        raise ValueError("Invalid GitHub source route")
    if not isinstance(branch,str) or len(branch)>120 or not branch or "\0" in branch:
        raise ValueError("Invalid default branch")
    owner, repo = full_name.split("/",1)
    base=f"https://api.github.com/repos/{quote(owner,safe='')}/{quote(repo,safe='')}/contents"
    if path:
        base+="/" + "/".join(quote(segment,safe="") for segment in path.split("/"))
    url=base+"?"+urlencode({"ref":branch})
    if fetch is None:
        def fetch(url, token):
            req=Request(url,headers={
                "Authorization":"Bearer "+token,
                "Accept":"application/vnd.github+json",
                "X-GitHub-Api-Version":"2022-11-28",
                "User-Agent":"SirusSofts-SourceBrowser/0.4"
            })
            try:
                with urlopen(req,timeout=12) as response:
                    raw=response.read(1200000+1)
            except HTTPError as err:
                if err.code in (403,404):
                    raise ValueError("Repository file not found or source credential lacks access") from None
                raise RuntimeError(f"GitHub source request failed (HTTP {err.code})") from None
            except (URLError,TimeoutError):
                raise RuntimeError("GitHub source API unavailable") from None
            if len(raw)>1200000:
                raise RuntimeError("GitHub source response too large")
            try:return json.loads(raw)
            except (ValueError,UnicodeDecodeError):
                raise RuntimeError("Invalid GitHub source response")
    obj=fetch(url,token)
    if isinstance(obj,list):
        if len(obj)>1000:raise RuntimeError("Directory listing too large")
        entries=[]
        for entry in obj:
            if not isinstance(entry,dict):continue
            name,child,kind=entry.get("name"),entry.get("path"),entry.get("type")
            if not isinstance(name,str) or kind not in ("dir","file") or not safe_path(child):continue
            if (path and not child.startswith(path+"/")) or (not path and "/" in child):
                continue
            entries.append({"name":name,"path":child,"type":kind,
                            "size":min(max(0,int(entry.get("size") or 0)),1_000_000_000)})
        return {"ok":True,"kind":"directory","entries":entries}
    if not isinstance(obj,dict) or obj.get("type")!="file":
        raise ValueError("Requested item is not a regular file or directory")
    size=obj.get("size")
    if not isinstance(size,int) or size<0 or size>MAX_FILE:
        raise ValueError("File is too large for safe text preview")
    if obj.get("encoding")!="base64" or not isinstance(obj.get("content"),str):
        raise ValueError("Source is unavailable as a text preview")
    try:content=base64.b64decode(obj["content"],validate=False)
    except (ValueError,TypeError):
        raise ValueError("Invalid GitHub file encoding")
    if len(content)>MAX_FILE or b"\x00" in content:
        raise ValueError("Binary or oversized files cannot be previewed")
    try:text=content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Only UTF-8 source files are previewed")
    return {"ok":True,"kind":"file","content":text}

class Gateway:
    def __init__(self,db_path,token):
        self.db_path,self.token=db_path,token
        self.lock=threading.Lock()
        self.requests=deque()
    def request(self,repo_id,path):
        if not isinstance(repo_id,int) or isinstance(repo_id,bool) or repo_id<1 or not safe_path(path):
            raise ValueError("Invalid repository or path")
        now=time.monotonic()
        with self.lock:
            while self.requests and self.requests[0]<now-60:
                self.requests.popleft()
            if len(self.requests)>=MAX_REQUESTS_PER_MINUTE:
                raise RuntimeError("Source browsing rate limit reached")
            self.requests.append(now)
        with sqlite3.connect("file:"+self.db_path+"?mode=ro",uri=True,timeout=5) as db:
            row=db.execute("SELECT full_name,default_branch FROM cms_github_repositories WHERE github_id=?",
                           (repo_id,)).fetchone()
        if not row:raise ValueError("Repository is not imported")
        return api_request(row[0],row[1],path,self.token)

def make_handler(gateway):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request=self.rfile.readline(2049)
            try:
                if len(request)>2048 or not request.endswith(b"\n"):
                    raise ValueError("Invalid source request size")
                obj=json.loads(request)
                if not isinstance(obj,dict) or set(obj)!={"repo_id","path"}:
                    raise ValueError("Invalid source request")
                response=gateway.request(obj["repo_id"],obj["path"])
            except (ValueError,RuntimeError,sqlite3.Error,TypeError) as e:
                response={"ok":False,"error":str(e)[:160]}
            raw=json.dumps(response,ensure_ascii=True,separators=(",",":")).encode("ascii")+b"\n"
            if len(raw)>MAX_RESPONSE:
                raw=b'{"ok":false,"error":"Preview exceeds response limit"}\n'
            self.wfile.write(raw)
    return Handler

class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True
    request_queue_size=12

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--db")
    parser.add_argument("--token-file")
    parser.add_argument("--socket")
    args=parser.parse_args(argv)
    if args.check:
        print("PASS Private source browser syntax and components available")
        return 0
    if not all((args.db,args.token_file,args.socket)):
        parser.error("--db, --token-file, --socket required")
    if args.socket!="/run/sirussofts-source/bridge.sock":
        raise RuntimeError("Unexpected source socket location")
    if args.db!="/home/sirusweb/web/sirussofts.com/private/sirus-cms/data/cms.sqlite3":
        raise RuntimeError("Unexpected source database location")
    with open(args.token_file,encoding="utf-8") as fp:token=fp.read().strip()
    if len(token)<20 or "\n" in token:raise RuntimeError("Invalid source gateway credential")
    if os.path.exists(args.socket):
        raise RuntimeError("Socket exists; do not replace an active source gateway")
    os.umask(0o077)
    server=Server(args.socket,make_handler(Gateway(args.db,token)))
    os.chmod(args.socket,0o600)
    try:server.serve_forever(poll_interval=.5)
    finally:
        server.server_close()
        try:os.unlink(args.socket)
        except FileNotFoundError:pass
    return 0

if __name__=="__main__":
    try:sys.exit(main())
    except (OSError,ValueError,RuntimeError) as err:
        print("FAIL source gateway: "+str(err),file=sys.stderr)
        sys.exit(1)
