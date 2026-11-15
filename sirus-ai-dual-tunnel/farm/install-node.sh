#!/usr/bin/env bash
# SirusSofts AI farm enrollment: phase-0 preflight only.
# Production enrollment blocked until controller, auth and rollback reviewed.
set -Eeuo pipefail
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
[[ $# -ge 1 ]] || fail 'Usage: install-node.sh --check [node-id] | --plan | --install'
mode="$1"
base="$(cd "$(dirname "$0")" && pwd)"
case "$mode" in
  --plan)
    [[ $# -eq 1 ]] || fail '--plan accepts no other arguments'
    cat <<'INFO'
SirusSofts farm node deployment, planned:
1. Detect CPU, RAM, GPU and available inference endpoints locally.
2. Choose unique node/site IDs; preserve existing CPU/GPU services.
3. Generate NEW per-node keypair and one-time enrollment request.
4. Require Ubuntu coordinator admin approval before activating remote access.
5. Connect OUTBOUND over authenticated, host-key-pinned TLS/SSH.
6. Advertise only reviewed workers and permitted models.
7. Provide reversible OpenRC/systemd service with a dry-run repair path.
No keys, network tunnels, firewall changes or services created by --plan.
The production --install path is NOT YET IMPLEMENTED.
INFO
    ;;
  --check)
    [[ $# -le 2 ]] || fail '--check accepts optional node-id'
    command -v python3 >/dev/null || fail 'python3 required'
    [[ -f "$base/node-preflight.py" ]] || fail 'node-preflight.py missing'
    if [[ $# -eq 2 ]]; then
      python3 "$base/node-preflight.py" --node-id "$2"
    else
      python3 "$base/node-preflight.py"
    fi
    ;;
  --install)
    fail 'Production enrollment is blocked until the authenticated Ubuntu controller, node identity and rollback installer are implemented and reviewed.'
    ;;
  *) fail 'Usage: install-node.sh --check [node-id] | --plan | --install';;
esac
