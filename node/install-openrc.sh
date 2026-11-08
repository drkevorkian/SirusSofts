#!/usr/bin/env bash
# Idempotent install; no service start, autostart, tunnel or VPS changes.
set -euo pipefail
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || fail "Run as root"
[[ -f /etc/gentoo-release ]] || fail "Requires Gentoo"
command -v python3 >/dev/null || fail "Python 3 required"
command -v rc-service >/dev/null || fail "OpenRC required"
SRC="$(cd "$(dirname "$0")" && pwd)"
if ! id sirusnode >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /var/lib/sirus-node --shell /sbin/nologin sirusnode
fi
install -d -m 0755 -o root -g root /opt/sirus-node
install -m 0644 -o root -g root "$SRC/agent.py" /opt/sirus-node/agent.py
install -d -m 0700 -o sirusnode -g sirusnode /etc/sirus-node
install -m 0755 -o root -g root "$SRC/openrc/sirus-node" /etc/init.d/sirus-node
if [[ ! -f /etc/sirus-node/heartbeat.key ]]; then
    python3 - <<'PY'
import os
fd = os.open("/etc/sirus-node/heartbeat.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    os.write(fd, os.urandom(32))
finally:
    os.close(fd)
PY
    chown sirusnode:sirusnode /etc/sirus-node/heartbeat.key
fi
if [[ ! -f /etc/sirus-node/config.json ]]; then
    install -m 0600 -o sirusnode -g sirusnode "$SRC/config.example.json" /etc/sirus-node/config.json
fi
echo "Installed, NOT started. Review node/README.md; provision private VPS key and WireGuard."
echo "Only then: rc-service sirus-node start"
