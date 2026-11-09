#!/usr/bin/env bash
# Reviewed Hestia-compatible worker install; no Nginx/PHP changes.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="--check"; if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
[[ $# -eq 0 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
  fail 'Usage: ubuntu-worker/install.sh --check|--plan|--install'
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 "$HERE/worker.py" --check
if [[ "$mode" == --check ]]; then
  echo 'PASS legacy Ubuntu worker is deprecated; Gentoo now executes queued work'
  exit 0
fi
if [[ "$mode" == --plan ]]; then
  echo 'DEPRECATED: Ubuntu is the CMS coordinator, not a compute worker.'
  echo 'DO NOT install sirus-ubuntu-worker.service. Use gentoo-node/ on Gentoo.'
  exit 0
fi
fail 'Ubuntu work executor was retired: all queued work must run on Gentoo'
[[ "$(id -u)" == 0 ]] || fail 'Root required for service config'
id sirusweb >/dev/null || fail 'Expected Hestia website user missing'
WEB=/home/sirusweb/web/sirussofts.com
PRIVATE="$WEB/private/sirus-cms"
[[ -d "$WEB" && ! -L "$WEB" && -d "$PRIVATE" && ! -L "$PRIVATE" ]] ||
  fail 'Existing CMS domain private area missing'
[[ -f "$PRIVATE/data/cms.sqlite3" ]] || fail 'CMS must be installed before worker'
if [[ "$mode" == --plan ]]; then
  echo "Worker runs as sirusweb using $PRIVATE/nodes/work.sqlite3"
  echo 'Allowed work: health_snapshot and site_health only. No arbitrary URLs/commands.'
  echo 'Creates private key/registry folders and persistent systemd service.'
  echo 'No files modified.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive operator session required'
read -r -p 'Type ENABLE UBUNTU WORKER to install and start it: ' answer
[[ "$answer" == 'ENABLE UBUNTU WORKER' ]] || fail 'Operator declined'
SERVICE=/etc/systemd/system/sirus-ubuntu-worker.service
[[ ! -e "$SERVICE" && ! -L "$SERVICE" ]] || fail 'Worker unit exists; review before replacing'
install -d -o sirusweb -g sirusweb -m 0700 "$PRIVATE/nodes" "$PRIVATE/nodes/keys"
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$HERE/worker.py" /usr/local/libexec/sirus-ubuntu-worker.py
install -o root -g root -m 0644 "$HERE/sirus-ubuntu-worker.service" "$SERVICE"
systemctl daemon-reload
systemctl enable --now sirus-ubuntu-worker.service
systemctl is-active --quiet sirus-ubuntu-worker.service || fail 'Worker inactive'
echo 'PASS Ubuntu job worker running; provision per-node key separately.'
