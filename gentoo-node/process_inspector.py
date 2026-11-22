"""Bounded read-only Gentoo process inventory, including partial NVIDIA VRAM ownership.

Unknown GPU allocations remain unknown; never attribute total VRAM by guessing.
No command execution, process signals, driver reset, or privileges.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import shutil
import subprocess

MAX_PIDS = 4096
MAX_ROWS = 48
PROTECTED = frozenset({
    "init", "openrc", "sshd", "sirus-gentoo-node", "sirus-gentoo-dashboard",
    "sirus-gentoo-tunnel", "nginx", "mariadbd", "mysqld", "dbus-daemon",
    "systemd", "udevd", "login", "agetty", "cron", "crond", "NetworkManager",
    "dhcpcd", "wpa_supplicant", "Xorg", "Xwayland", "kwin_wayland",
    "kwin_x11", "plasmashell", "sddm", "pipewire", "wireplumber"
})

def importance(name, uid, pid):
    """Conservative advisory classification; NEVER authorization to kill."""
    if pid == 1 or uid == 0 or name in PROTECTED:
        return "protected", "System, root, login, display or infrastructure process"
    if any(x in name.lower() for x in ("llama", "ollama", "vllm", "python")):
        return "workload", "Could be an active inference or application worker"
    return "unknown", "Dependencies have not been verified"

def parse_stat(raw):
    start, end = raw.find("("), raw.rfind(")")
    if start < 1 or end <= start:
        raise ValueError("Invalid process stat")
    tail = raw[end+2:].split()
    if len(tail) < 22:
        raise ValueError("Incomplete stat")
    return raw[start+1:end][:80], int(tail[11])+int(tail[12]), int(tail[19])

def parse_status(raw):
    result = {}
    for line in raw.splitlines():
        key, _, value = line.partition(":")
        if key in ("Uid", "VmRSS", "Threads"):
            try:
                result[key] = int(value.split()[0])
            except (ValueError, IndexError):
                pass
    return result

def read_process(pid, proc_root, affinity=os.sched_getaffinity):
    folder = proc_root / str(pid)
    first = parse_stat((folder/"stat").read_text(encoding="utf8",errors="replace"))
    details = parse_status((folder/"status").read_text(encoding="ascii",errors="replace"))
    second = parse_stat((folder/"stat").read_text(encoding="utf8",errors="replace"))
    if second[2] != first[2]:
        return None
    try:
        cpus = sorted(affinity(pid))[:256]
    except (OSError, ValueError):
        cpus = []
    level, reason = importance(first[0],details.get("Uid"),pid)
    return {"pid":pid,"start_ticks":first[2],"name":first[0],
            "uid":details.get("Uid"),"rss_bytes":details.get("VmRSS",0)*1024,
            "cpu_ticks":first[1],"threads":details.get("Threads"),
            "affinity":cpus,"importance":level,"importance_reason":reason,
            "vram_bytes":None,"vram_attribution":"unknown"}

def nvidia_memory(binary=None,runner=subprocess.run):
    """Driver's compute-app query; graphical allocations may be omitted."""
    if binary is None:
        binary=shutil.which("nvidia-smi")
    if not binary or not Path(binary).is_file():
        return None
    try:
        response=runner([binary,"--query-compute-apps=pid,used_gpu_memory",
                         "--format=csv,noheader,nounits"],
                        capture_output=True,text=True,timeout=2,check=False)
        if response.returncode:
            return None
        memory={}
        for line in response.stdout.splitlines()[:MAX_PIDS]:
            cells=[part.strip() for part in line.split(",")]
            if len(cells)!=2 or not all(re.fullmatch(r"[0-9]{1,9}", c) for c in cells):
                continue
            pid, mib = map(int,cells)
            if pid>0:
                memory[pid]=memory.get(pid,0)+mib*1048576
        return memory
    except (OSError,ValueError,subprocess.SubprocessError):
        return None

def gpu_handles(pid, proc_root, limit=128):
    """Report open GPU device handles, not GPU memory ownership.

    Only scan a bounded subset of process FDs; inaccessible processes are
    unknown. A matching handle does not imply active GPU computation.
    """
    directory=Path(proc_root)/str(pid)/"fd"
    devices=set()
    try:
        for count,entry in enumerate(directory.iterdir()):
            if count>=limit:
                return sorted(devices)[:6], "partial"
            try:
                target=os.readlink(entry)
                if re.fullmatch(r"/dev/dri/(?:card|renderD)[0-9]{1,3}", target) or (
                    re.fullmatch(r"/dev/nvidia[0-9]{1,3}", target)):
                    devices.add(target)
            except OSError:
                continue
    except OSError:
        return [], "unavailable"
    return sorted(devices)[:6], "observed"

def inventory(proc_root=Path("/proc"),affinity=os.sched_getaffinity,
              gpu_provider=nvidia_memory):
    try:
        pids=sorted(int(p.name) for p in Path(proc_root).iterdir()
                    if p.name.isascii() and p.name.isdecimal() and int(p.name)>0)
    except OSError:
        return {"processes":[],"scanned":0,"partial":True,
                "gpu_attribution":"unknown","notes":["Cannot read /proc"]}
    rows=[]
    for pid in pids[:MAX_PIDS]:
        try:
            row=read_process(pid,Path(proc_root),affinity)
            if row is not None:
                rows.append(row)
        except (OSError,ValueError,IndexError):
            continue
    owners=gpu_provider()
    if owners is not None:
        for row in rows:
            if row["pid"] in owners:
                row["vram_bytes"]=owners[row["pid"]]
                row["vram_attribution"]="nvidia-compute-only"
    # Probe only the largest-RSS candidates and driver-reported NVIDIA
    # compute PIDs to cap procfs FD operations during each 10-second refresh.
    candidates=sorted(rows,key=lambda x:x["rss_bytes"],reverse=True)[:96]
    for row in rows:
        if row["vram_bytes"] is not None and row not in candidates:
            candidates.append(row)
    for row in candidates[:128]:
        row["gpu_handles"],row["gpu_handle_scan"]=gpu_handles(row["pid"],proc_root)
    for row in rows:
        row.setdefault("gpu_handles",[])
        row.setdefault("gpu_handle_scan","not-scanned")
    for row in rows:
        row["control_allowed"] = (os.geteuid()!=0 and row["uid"]==os.geteuid()
                                  and row["importance"]!="protected"
                                  and row["pid"]!=os.getpid())
    rows.sort(key=lambda x:(x["vram_bytes"] or 0,bool(x["gpu_handles"]),x["rss_bytes"]),reverse=True)
    return {"processes":rows[:MAX_ROWS],"scanned":len(rows),
            "partial":len(pids)>MAX_PIDS or len(rows)<min(len(pids),MAX_PIDS),
            "gpu_attribution":"nvidia-compute-only" if owners is not None else "unknown",
            "notes":["AMD per-process VRAM is not reliably exposed by standard amdgpu sysfs",
                     "NVIDIA graphics allocations and unreported GPU clients may be missing",
                     "GPU device FD scan is bounded and may be incomplete or permission-denied",
                     "Importance labels are advisory, not permission to stop a process"]}
