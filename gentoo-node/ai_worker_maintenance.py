#!/usr/bin/env python3
"""Root-console-only, allowlisted OpenRC GPU inference lifecycle tool.

No HTTP integration, no GPU driver reset, no arbitrary service or shell command.
Every mutation needs an interactive local terminal and audit file. Only the
existing named GPU workers may be managed; the dashboard remains unprivileged.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

WORKERS = {"amd-vulkan": "sirus-ai-amd", "nvidia-cuda": "sirus-ai-nvidia"}
SERVICE_BINARY = "/sbin/rc-service"
AUDIT = Path("/var/log/sirus-gpu-maintenance.jsonl")
STATUS = {"running": "running", "stopped": "stopped", "unavailable": "unavailable"}

class MaintenanceError(ValueError):
    pass

def state(service, *, runner=subprocess.run):
    if service not in WORKERS.values():
        raise MaintenanceError("Service not allowlisted")
    try:
        result = runner([SERVICE_BINARY, service, "status"],
                        capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode == 0:
        return "running"
    # A nonzero rc-service status may mean inactive OR an error: do not infer
    # an inactive service from the return code alone.
    combined = (result.stdout + result.stderr).lower()[:3000]
    if "stopped" in combined or "inactive" in combined or "crashed" in combined:
        return "stopped"
    return "unavailable"

def plan(worker, *, runner=subprocess.run):
    if worker not in WORKERS:
        raise MaintenanceError("Unknown worker")
    service = WORKERS[worker]
    current = state(service, runner=runner)
    return {"worker": worker, "service": service, "state": current,
            "release_method": "graceful_openrc_stop",
            "root_console_required": True,
            "website_enabled": False,
            "driver_reset_enabled": False,
            "warnings": [
                "Check active inference jobs and preserve output before stopping",
                "A clean service stop may release VRAM but is not guaranteed",
                "OpenRC status does not establish absence of all GPU clients",
                "Stopping GPU inference can affect active chat requests",
                "No GPU reset, PCI device operation or forced termination is provided"]}

def append_audit(record, *, path=AUDIT):
    """Root-only, append-only open flags and fsync. No symlink traversal."""
    path=Path(path)
    if path.is_symlink():
        raise MaintenanceError("Refusing audit log symlink")
    flags=os.O_WRONLY|os.O_APPEND|os.O_CREAT
    flags |= getattr(os,"O_NOFOLLOW",0)
    descriptor=os.open(path, flags, 0o600)
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o077:
            raise MaintenanceError("Audit log must be root-owned mode 0600")
        data=(json.dumps(record, separators=(",",":"), sort_keys=True)+"\n").encode()
        if len(data)>1024:
            raise MaintenanceError("Audit record too large")
        if os.write(descriptor,data)!=len(data):
            raise MaintenanceError("Audit write incomplete")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

def change(worker, action, *, runner=subprocess.run, audit=append_audit,
           euid=os.geteuid, isatty=os.isatty, input_func=input):
    if worker not in WORKERS or action not in ("stop","start"):
        raise MaintenanceError("Unknown worker or action")
    if euid()!=0:
        raise MaintenanceError("Root console required; website has no privilege")
    if not isatty(0) or not isatty(1):
        raise MaintenanceError("Interactive local console required")
    service=WORKERS[worker]
    before=state(service,runner=runner)
    if before=="unavailable":
        raise MaintenanceError("Service status unavailable; refuse action")
    if action=="stop" and before!="running":
        raise MaintenanceError("Worker is not running")
    if action=="start" and before!="stopped":
        raise MaintenanceError("Worker is not stopped")
    phrase=f"{action.upper()} {service}"
    if input_func(f"Type {phrase} to confirm (active requests may be interrupted): ")!=phrase:
        raise MaintenanceError("Confirmation declined")
    event={"at":int(time.time()),"worker":worker,
           "service":service,"action":action,"state_before":before}
    # If we cannot journal intention, do not touch the service.
    audit({**event,"phase":"requested"})
    try:
        result=runner([SERVICE_BINARY,service,action],
                      capture_output=True,text=True,timeout=30,check=False)
    except (OSError,subprocess.SubprocessError) as err:
        audit({**event,"phase":"failed","failure":"OpenRC invocation unavailable"})
        raise MaintenanceError("OpenRC command unavailable") from err
    after=state(service,runner=runner)
    good=result.returncode==0 and after==("stopped" if action=="stop" else "running")
    audit({**event,"phase":"completed" if good else "failed","state_after":after})
    if not good:
        raise MaintenanceError("Service state did not reach requested target")
    return {"ok":True,**event,"state_after":after,
            "note":"VRAM release is unverified; other GPU clients may remain"}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker",required=True,choices=sorted(WORKERS))
    parser.add_argument("--action",required=True,choices=("plan","stop","start"))
    args=parser.parse_args(argv)
    try:
        result=plan(args.worker) if args.action=="plan" else change(args.worker,args.action)
    except MaintenanceError as err:
        print(f"REFUSED: {err}",file=sys.stderr)
        return 2
    print(json.dumps(result,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
