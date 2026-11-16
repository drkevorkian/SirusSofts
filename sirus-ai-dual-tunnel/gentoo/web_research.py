#!/usr/bin/env python3
"""Opt-in SearXNG URL discovery. Never fetch result URLs or run model calls."""
import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

PROFILES=("auto","coding","science","physics","time","relativity","finance","mechanics")
ENGINES=("google","bing","yahoo","duckduckgo","brave","mojeek")
DEFAULT_DEPTH=50
MAX_DEPTH=5000
PRIMARY={"nasa.gov","nist.gov","energy.gov","sec.gov","federalreserve.gov",
         "operatorsguides.brp.com","brp.com","docs.python.org",
         "developer.mozilla.org","arxiv.org","pubmed.ncbi.nlm.nih.gov"}
TRACKING={"fbclid","gclid","msclkid","mc_cid","mc_eid","ref"}

def valid_depth(value):
    if type(value) is not int or not 1<=value<=MAX_DEPTH:
        raise ValueError("Depth must be an integer from 1 to 5000")
    return value

def clean_url(raw):
    if not isinstance(raw,str) or len(raw)>2048:return None
    try:
        p=urlsplit(raw.strip())
        if p.scheme.lower()!="https" or p.username or p.password or p.port not in (None,443):
            return None
        host=(p.hostname or "").lower().rstrip(".")
        if host.startswith("www."):host=host[4:]
        if not host or "." not in host or len(host)>253 or not re.fullmatch(r"[a-z0-9.-]+",host):
            return None
        if ".." in host or host.endswith((".local",".internal",".localhost",".test",".invalid",".onion")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:return None
        except ValueError:pass
        q=urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                     if not k.lower().startswith("utm_") and k.lower() not in TRACKING])
        return urlunsplit(("https",host,p.path or "/",q,""))
    except (ValueError,UnicodeError):
        return None

def tier(url):
    host=urlsplit(url).hostname or ""
    if host.endswith(".edu") or ".ac." in host or host.endswith(".gov"):
        return 0,"education/government"
    if any(host==d or host.endswith("."+d) for d in PRIMARY):
        return 1,"curated primary domain"
    if host.endswith((".org",".int")):
        return 2,"organization (independent verification required)"
    return 3,"unverified web source"

def normalize_hit(row):
    if not isinstance(row,dict):return None
    url=clean_url(row.get("url"))
    if not url:return None
    raw_engines=row.get("engines") or [row.get("engine","")]
    raw_engines=raw_engines if isinstance(raw_engines,list) else []
    engines=sorted({e for e in raw_engines if isinstance(e,str) and e})
    level,cls=tier(url)
    return {"url":url,"title":str(row.get("title") or url).strip()[:200],
            "snippet":str(row.get("content") or "").strip()[:1200],
            "engines":engines,"tier":level,"source_class":cls}

def ranked(hits,query):
    terms=set(re.findall(r"[a-z0-9]{3,}",query.lower()))
    def order(hit):
        txt=(hit["title"]+" "+hit["snippet"]).lower()
        score=sum(word in txt for word in terms)
        return (hit["tier"],-score,-len(hit["engines"]),hit["url"])
    return sorted(hits,key=order)

def endpoint_guard(endpoint):
    p=urlsplit(endpoint)
    if p.scheme!="http" or p.hostname not in ("127.0.0.1","::1") or \
       p.username or p.password or p.query or p.fragment or p.path not in ("","/","/search") or \
       p.port is None:
        raise ValueError("SearXNG endpoint must be HTTP loopback with explicit port")
    return endpoint.rstrip("/")

def search_page(endpoint,query,engines,page):
    querystring=urlencode({"q":query,"format":"json","engines":",".join(engines),
                           "pageno":page,"categories":"general","safesearch":1})
    req=Request(endpoint+"?"+querystring,headers={"Accept":"application/json",
                "User-Agent":"SirusSoftsResearch/0.1"})
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*a,**kw):
            raise ValueError("Local search endpoint redirect refused")
    with build_opener(NoRedirect()).open(req,timeout=15) as response:
        if response.status!=200:raise RuntimeError("SearXNG HTTP "+str(response.status))
        body=response.read(2*1024*1024+1)
        if len(body)>2*1024*1024:raise ValueError("Search reply too large")
        return json.loads(body)

def discover(query,depth=DEFAULT_DEPTH,engines=ENGINES,fetch=None,delay=1.0,
             max_pages=None,profile="auto"):
    depth=valid_depth(depth)
    if profile not in PROFILES:raise ValueError("Unknown expertise")
    if not isinstance(query,str) or not 2<=len(query.strip())<=300:
        raise ValueError("Query must be 2-300 characters")
    if not engines or len(engines)>20 or any(
        not isinstance(e,str) or not re.fullmatch("[a-zA-Z0-9 _-]{2,40}",e)
        for e in engines):raise ValueError("Invalid engine list")
    if not 0<=delay<=30:raise ValueError("Invalid request delay")
    if max_pages is None:max_pages=min(600,max(4,math.ceil(depth/8)+3))
    if type(max_pages) is not int or not 1<=max_pages<=600:raise ValueError("Invalid page budget")
    if fetch is None:raise ValueError("Local SearXNG client must be configured")
    seen={}; unresponsive=set();errors=[];page=0;unchanged=0
    while len(seen)<depth and page<max_pages:
        page+=1
        try:
            payload=fetch(query,engines,page)
            if not isinstance(payload,dict) or not isinstance(payload.get("results",[]),list):
                raise ValueError("Invalid metasearch reply")
            for error in payload.get("unresponsive_engines",[]):
                if isinstance(error,(list,tuple)) and error:
                    unresponsive.add(str(error[0]))
            count=len(seen)
            for row in payload.get("results",[]):
                item=normalize_hit(row)
                if not item:continue
                if item["url"] in seen:
                    other=seen[item["url"]]
                    other["engines"]=sorted(set(other["engines"]+item["engines"]))
                else:seen[item["url"]]=item
            unchanged=unchanged+1 if len(seen)==count else 0
            if unchanged>=3:break
        except Exception as exc:
            errors.append("Page "+str(page)+": "+str(exc)[:160])
            if len(errors)>=3:break
        if len(seen)>=depth or page>=max_pages:break
        if delay:time.sleep(delay)
    return {"query":query,"expertise":profile,"requested_depth":depth,
            "discovered":len(seen),"pages_queried":page,
            "engines_requested":list(engines),"unresponsive_engines":sorted(unresponsive),
            "errors":errors,"complete":len(seen)>=depth,
            "results":ranked(list(seen.values()),query)[:depth]}

def read_config(path):
    p=Path(path)
    if not p.is_absolute() or p.is_symlink() or not p.is_file() or p.stat().st_size>4096:
        raise ValueError("Research configuration must be a small absolute regular file")
    obj=json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj,dict) or obj.get("schema")!="sirus-ai-research-v1":
        raise ValueError("Unknown research configuration format")
    depth=valid_depth(obj.get("depth"))
    names=obj.get("engines")
    if not isinstance(names,list) or not names or len(names)>20 or any(
        name not in ENGINES for name in names) or len(names)!=len(set(names)):
        raise ValueError("Research configuration engine selection invalid")
    return depth,tuple(names)

def main():
    ap=argparse.ArgumentParser(description="SirusSofts private Gentoo multi-engine discovery")
    ap.add_argument("--check",action="store_true")
    ap.add_argument("--query",default="")
    ap.add_argument("--depth",type=int,default=None)
    ap.add_argument("--config",help="Private CMS-exported research settings JSON")
    ap.add_argument("--profile",choices=PROFILES,default="auto")
    ap.add_argument("--engines",default=None)
    ap.add_argument("--searx-url",default="http://127.0.0.1:8888/search")
    ap.add_argument("--max-pages",type=int,default=None)
    ap.add_argument("--delay",type=float,default=1.0)
    ap.add_argument("--output",help="Write new private JSON file, refuse overwrite")
    args=ap.parse_args()
    if args.check:
        print("PASS: default depth 50, max 5000; no network calls or writes")
        return
    endpoint=endpoint_guard(args.searx_url)
    depth,engines=(DEFAULT_DEPTH,ENGINES)
    if args.config:depth,engines=read_config(args.config)
    if args.depth is not None:depth=valid_depth(args.depth)
    if args.engines is not None:
        engines=tuple(x.strip() for x in args.engines.split(",") if x.strip())
    results=discover(args.query,depth,engines,
        fetch=lambda q,e,p:search_page(endpoint,q,e,p),
        delay=args.delay,max_pages=args.max_pages,profile=args.profile)
    output=json.dumps(results,indent=2,ensure_ascii=False)+"\n"
    if args.output:
        dest=Path(args.output)
        if not dest.is_absolute() or dest.is_symlink() or not dest.parent.is_dir():
            raise SystemExit("Output requires private existing absolute directory")
        fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as stream:stream.write(output)
        print("Wrote "+str(len(results["results"]))+" candidate URLs to "+str(dest))
    else:print(output,end="")

if __name__=="__main__":main()
