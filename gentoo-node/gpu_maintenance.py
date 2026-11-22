"""Read-only GPU memory accounting and maintenance eligibility, never GPU reset.

The dashboard is not root and never executes OpenRC commands. NVIDIA
process attribution is partial, AMD allocations remain unattributed.
"""
from __future__ import annotations

SERVICE_MAP = {"0x1002": "sirus-ai-amd", "0x10de": "sirus-ai-nvidia"}
MAX_GPU = 16


def _valid_bytes(value):
    return type(value) is int and 0 <= value <= 2**50


def overview(gpus, inventory, services):
    """Provide bounded, cautionary per-GPU diagnostic recommendations.

    A per-process NVIDIA compute allocation does NOT identify a physical
    GPU in multi-GPU setups. Only show an aggregate attribution when there
    is exactly one NVIDIA GPU; never split across multiple NVIDIA GPUs.
    """
    if not isinstance(gpus, list):
        gpus = []
    if not isinstance(inventory, dict):
        inventory = {}
    if not isinstance(services, list):
        services = []
    rows = inventory.get("processes", [])
    if not isinstance(rows, list):
        rows = []
    nvidia_count = sum(1 for x in gpus[:MAX_GPU] if isinstance(x, dict)
                       and x.get("vendor") == "0x10de")
    attributed = []
    for p in rows[:48]:
        if (isinstance(p, dict) and
                p.get("vram_attribution") == "nvidia-compute-only" and
                _valid_bytes(p.get("vram_bytes"))):
            attributed.append((p.get("pid"), p["vram_bytes"]))
    # The process inventory is capped and inaccessible users may be omitted.
    attributed_sum = sum(bytes_ for _, bytes_ in attributed)
    result = []
    for index, gpu in enumerate(gpus[:MAX_GPU]):
        if not isinstance(gpu, dict):
            continue
        vendor = gpu.get("vendor")
        total = gpu.get("vram_total_bytes")
        used = gpu.get("vram_used_bytes")
        valid = (_valid_bytes(total) and _valid_bytes(used) and
                 used <= total and total > 0)
        service = SERVICE_MAP.get(vendor)
        running = service in services if isinstance(service, str) else False
        if vendor == "0x10de" and nvidia_count == 1 and valid:
            known = attributed_sum
            # Bad or stale telemetry is an inconsistency, not negative unknown.
            unattributed = used - known if known <= used else None
            attribution = ("partial" if unattributed is not None else
                           "inconsistent")
        else:
            known = None
            unattributed = None
            attribution = ("unavailable" if vendor == "0x1002" else
                           "ambiguous" if vendor == "0x10de" else "unsupported")
        reasons = ["GPU reset is not exposed by this web application",
                   "Graphics/display and background clients are not proven absent",
                   "A privileged offline hardware-specific preflight is required"]
        if not valid:
            reasons.append("Driver-wide VRAM counters are absent or inconsistent")
        if running:
            reasons.append("The named AI worker service is reported running")
        if attribution == "inconsistent":
            reasons.append("Reported compute allocations exceed driver-wide VRAM usage")
        result.append({
            "index": index,
            "vendor": vendor if isinstance(vendor, str) else "unknown",
            "name": str(gpu.get("name", "GPU"))[:80],
            "vram_used_bytes": used if valid else None,
            "vram_total_bytes": total if valid else None,
            "reported_nvidia_compute_bytes": known,
            "remaining_unattributed_bytes": unattributed,
            "attribution": attribution,
            "worker_service": service,
            "worker_reported_running": running,
            "safe_next_step": (
                "Review the named OpenRC AI service and its active jobs before "
                "considering a supervised stop or model unload"
                if service else
                "Identify the owning GPU client before taking any action"),
            "reset_status": "disabled",
            "reset_blockers": reasons,
            "reset_action_available": False
        })
    return result
