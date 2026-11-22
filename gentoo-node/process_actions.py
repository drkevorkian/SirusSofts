"""Explicit narrow process controls; never root, shell, or GPU driver reset.

The caller is the unprivileged Gentoo dashboard user. A PID must have the
requested start identity and belong to that user; critical services are denied.
"""
from __future__ import annotations
import os
from pathlib import Path
import signal
from process_inspector import read_process

class ControlError(ValueError):
    pass

PROTECTED_PROGRAMS = (b"/dashboard.py", b"/client.py", b"/tunnel.py",
                      b"/dashboard_health.py", b"/input_server.py",
                      b"/research_loop.py")

def execute(req, *, proc_root=Path("/proc"), uid=None,
            affinity=os.sched_getaffinity,set_affinity=os.sched_setaffinity,
            pidfd_open=os.pidfd_open,pidfd_signal=signal.pidfd_send_signal):
    if not isinstance(req,dict) or set(req)!={"action","pid","start_ticks","confirmation","cores"}:
        raise ControlError("Invalid process control request")
    action,pid,started=req["action"],req["pid"],req["start_ticks"]
    if action not in ("set_cpu_cores","terminate") or type(pid) is not int or not 2<=pid<=4194304:
        raise ControlError("Invalid action or PID")
    if type(started) is not int or not 1<=started<2**64:
        raise ControlError("Missing process start identity")
    caller=os.geteuid() if uid is None else uid
    if type(caller) is not int or caller<=0:
        raise ControlError("Privileged process changes are forbidden")
    try:
        target=read_process(pid,Path(proc_root),affinity)
        cmdline=(Path(proc_root)/str(pid)/"cmdline").read_bytes()[:4096]
    except (OSError,ValueError,IndexError) as err:
        raise ControlError("Process unavailable or inaccessible") from err
    if (not target or target["start_ticks"]!=started or target["uid"]!=caller
            or target["importance"]=="protected" or pid==os.getpid()
            or any(program in cmdline for program in PROTECTED_PROGRAMS)):
        raise ControlError("Process identity, ownership, or importance rejected")
    marker=("TERMINATE WORKLOAD" if target["importance"]=="workload" else "TERMINATE") if action=="terminate" else (
        "SET CORES WORKLOAD" if target["importance"]=="workload" else "SET CORES")
    if req["confirmation"]!=marker:
        raise ControlError("Explicit importance-aware confirmation required")
    if action=="terminate":
        if req["cores"] is not None:
            raise ControlError("Unexpected CPU count")
        # pidfd prevents signal delivery to a reused numeric PID.
        try:
            handle=pidfd_open(pid,0)
            try:
                fresh=read_process(pid,Path(proc_root),affinity)
                if not fresh or fresh["start_ticks"]!=started or fresh["uid"]!=caller:
                    raise ControlError("Process changed before signal")
                pidfd_signal(handle,signal.SIGTERM,None,0)
            finally:
                os.close(handle)
        except OSError as err:
            raise ControlError("Signal denied or process exited") from err
        return {"ok":True,"action":"terminate","pid":pid,
                "notice":"SIGTERM requested; exit and VRAM release are not guaranteed"}
    cores=req["cores"]
    if type(cores) is not int or not 1<=cores<=256:
        raise ControlError("CPU count must be 1..256")
    allowed=sorted(affinity(pid))
    if not allowed or cores>len(allowed):
        raise ControlError("Cannot expand beyond current CPU affinity")
    fresh=read_process(pid,Path(proc_root),affinity)
    if not fresh or fresh["start_ticks"]!=started or fresh["uid"]!=caller:
        raise ControlError("Process changed before affinity adjustment")
    chosen=set(allowed[:cores])
    try:
        set_affinity(pid,chosen)
    except OSError as err:
        raise ControlError("CPU affinity change denied") from err
    return {"ok":True,"action":"set_cpu_cores","pid":pid,
            "allowed_logical_cpus":sorted(chosen),
            "notice":"Affinity changed; threads and CPU percentage are not capped"}
