#!/usr/bin/env bash
# Separate opt-in Nginx vhost service installer. Installs files only.
# Does not enable/start service, configure TLS, touch live projects, or deploy apps.
set -Eeuo pipefail
fail(){ echo "ERROR: $*" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE=install
if [[ $# -gt 0 ]]; then
    [[ $# -eq 1 && "$1" == "--check" ]] || fail "Usage: bash deploy/install-nginx-vhost-agent.sh [--check]"
    MODE=check
fi
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || fail "Ubuntu 24.04 required"
[[ -f "$ROOT/deploy/nginx_vhost_agent.py" &&
   -f "$ROOT/deploy/sirus-nginx-vhost.service" ]] || fail "Missing reviewed service source"
python3 -m py_compile "$ROOT/deploy/nginx_vhost_agent.py"
if [[ "$MODE" == check ]]; then
    echo "PASS: Nginx agent installer source and Python syntax; no changes"
    exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail "Run as root after separate framework installer"
id sirusweb >/dev/null 2>&1 || fail "Dedicated sirusweb account missing"
[[ -f /srv/sirussofts/current/CMS/index.php &&
   -f /srv/sirussofts/current/CMS/app/core.php ]] || fail "CMS not installed"
command -v nginx >/dev/null || fail "Nginx missing"
[[ -d /etc/nginx/sites-enabled ]] || fail "Nginx sites-enabled missing"
TARGET=/opt/sirussofts/nginx_vhost_agent.py
UNIT=/etc/systemd/system/sirus-nginx-vhost.service
if [[ -e "$TARGET" ]] && ! cmp -s "$TARGET" "$ROOT/deploy/nginx_vhost_agent.py"; then
    fail "Existing agent modified; refusing overwrite"
fi
if [[ -e "$UNIT" ]] && ! cmp -s "$UNIT" "$ROOT/deploy/sirus-nginx-vhost.service"; then
    fail "Existing service unit modified; refusing overwrite"
fi
nginx -t
install -d -o root -g root -m 0755 /opt/sirussofts
install -d -o root -g root -m 0700 /etc/nginx/sirussofts-managed
install -d -o root -g root -m 0700 /etc/sirussofts /etc/sirussofts/tls
install -o root -g root -m 0644 "$ROOT/deploy/nginx_vhost_agent.py" "$TARGET"
install -o root -g root -m 0644 "$ROOT/deploy/sirus-nginx-vhost.service" "$UNIT"
systemctl daemon-reload
echo "Installed restricted Nginx vhost service WITHOUT starting it."
echo "Provision a valid wildcard TLS cert and key under /etc/sirussofts/tls."
echo "Review staging nginx -t and rollback tests before: systemctl enable --now sirus-nginx-vhost"
echo "No cPoint, WorldCom, Sentinel Trader, or other app deployed."
