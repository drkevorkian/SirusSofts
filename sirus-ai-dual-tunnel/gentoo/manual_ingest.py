#!/usr/bin/env python3
"""Extract private technical PDF manuals into page-cited, review-gated knowledge.

Never OCR, execute PDF content, call a remote model, or access the internet.
Optional Poppler pdfinfo/pdftotext run only against a local operator-supplied PDF.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

EXPERTISE=("coding","science","physics","time","relativity","finance","mechanics")
MAX_SOURCE_BYTES=100*1024*1024
MAX_OUTPUT_BYTES=16*1024*1024
MAX_RECORDS=3000
MAX_PAGE_TEXT=4800

def pdf_path(value):
    path=Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("PDF must be an absolute regular file, never a symlink")
    if not 32<=path.stat().st_size<=MAX_SOURCE_BYTES:
        raise ValueError("PDF size out of range (32 bytes–100 MiB)")
    with path.open("rb") as stream:
        if not stream.read(8).startswith(b"%PDF-"):
            raise ValueError("File does not have a PDF header")
    return path

def page_count(filename):
    data=subprocess.run(["pdfinfo",str(filename)],check=True,capture_output=True,
                        text=True,timeout=40).stdout
    found=re.search(r"^Pages:\s*(\d+)\s*$",data,re.M)
    if not found:raise ValueError("Poppler could not determine PDF page count")
    count=int(found.group(1))
    if not 1<=count<=10000:raise ValueError("Unsupported page count")
    return count

def page_text(filename,page):
    proc=subprocess.run(["pdftotext","-f",str(page),"-l",str(page),
                         "-layout","-enc","UTF-8",str(filename),"-"],
                        check=True,capture_output=True,text=True,timeout=90)
    return re.sub(r"[ \t]+\n","\n",proc.stdout).strip()

def split_text(text,length=MAX_PAGE_TEXT):
    if length<200:raise ValueError("Invalid page chunk length")
    output=[]
    while text:
        if len(text)<=length:
            output.append(text);break
        split=text.rfind("\n",int(length*.60),length)
        if split<0:split=text.rfind(" ",int(length*.60),length)
        if split<0:split=length
        output.append(text[:split].strip())
        text=text[split:].strip()
    return [x for x in output if x]

def records(filename,profile,start=1,end=None,extract=page_text,count=None):
    if profile not in EXPERTISE:raise ValueError("Unknown expertise")
    filename=pdf_path(filename)
    total=count if count is not None else page_count(filename)
    if not 1<=start<=total or end is not None and not start<=end<=total:
        raise ValueError("Invalid PDF page range")
    last=end if end is not None else total
    digest=hashlib.sha256(filename.read_bytes()).hexdigest()
    manual=filename.name
    missing=[]
    for page in range(start,last+1):
        text=extract(filename,page)
        if not isinstance(text,str):raise ValueError("PDF extractor returned invalid text")
        if not text.strip():
            missing.append(page);continue
        # Repeated page footer/header remains attributable to original page.
        for part,chunk in enumerate(split_text(text),1):
            title=(manual[:90]+" | page "+str(page)+" | part "+str(part))[:160]
            yield {"kind":"sirus-ai-document-v1","profile":profile,
                   "title":title,"source":"","text":chunk,
                   "sha256":hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                   "reviewed":False,
                   "manual_name":manual,"manual_sha256":digest,
                   "pdf_page":page,"page_part":part}
    if missing:
        print("WARNING: No extractable text on pages "+",".join(map(str,missing[:30]))+
              (" ..." if len(missing)>30 else "")+
              ". OCR/diagram review may be required.",file=__import__("sys").stderr)

def create_output(filename,profile,output,start=1,end=None,extract=page_text,count=None):
    target=Path(output)
    if not target.is_absolute() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("JSONL output must use an existing private absolute directory")
    data=[];used=0
    for row in records(filename,profile,start,end,extract,count):
        payload=(json.dumps(row,ensure_ascii=False)+"\n").encode()
        used+=len(payload)
        if used>MAX_OUTPUT_BYTES or len(data)>=MAX_RECORDS:
            raise ValueError("Too many manual chunks; use a smaller --end-page")
        data.append(payload)
    if not data:raise ValueError("No selectable text found; manual may require OCR")
    # O_EXCL prevents overwriting an approved file or a symlink.
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,"wb") as stream:
        for payload in data:stream.write(payload)
    return len(data),used

def main():
    ap=argparse.ArgumentParser(description="Private, page-attributed Can-Am/PDF manual extraction")
    ap.add_argument("--check",action="store_true")
    ap.add_argument("--pdf",default="")
    ap.add_argument("--profile",choices=EXPERTISE,default="mechanics")
    ap.add_argument("--output",default="")
    ap.add_argument("--start-page",type=int,default=1)
    ap.add_argument("--end-page",type=int,default=None)
    args=ap.parse_args()
    if args.check:
        print("PASS: PDF importer is offline and review-gated; Poppler needed for extraction")
        return
    if not args.pdf or not args.output:ap.error("--pdf and --output are required")
    amount,total=create_output(args.pdf,args.profile,args.output,
                               args.start_page,args.end_page)
    print("PASS: {} unapproved, page-attributed excerpts ({} bytes)".format(amount,total))
    print("Review the source PDF, extracted text and any missing diagrams; do not train or")
    print("load this draft into the RAG engine until explicit administrator approval.")

if __name__=="__main__":main()
