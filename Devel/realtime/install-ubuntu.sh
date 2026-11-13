#!/usr/bin/env bash
# Deliberately separate from the CMS site upgrade and Gentoo SSH tunnel.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="${1:---check}"
[[ $# == 0 || $# == 1 ]] || fail 'Usage: realtime/install-ubuntu.sh --check|--plan|--install'
[[ "$mode" == --check || "$mode" == --plan || "$mode" == --install ]] ||
    fail 'Unknown mode'
here="$(cd "$(dirname "$0")" && pwd)"
python3 -B "$here/server.py" --check
python3 - <<PY
import ast
from pathlib import Path
ast.parse(Path("$here/server.py").read_text())
PY
if [[ "$mode" == --check ]]; then
    echo 'PASS WebSocket server syntax; no files changed'
    exit 0
fi
[[ $(id -u) == 0 ]] || fail 'Run as Ubuntu root'
. /etc/os-release
[[ "${ID:-}" == ubuntu ]] || fail 'Ubuntu required'
id sirusweb >/dev/null || fail 'Expected Hestia account missing'
root=/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes
conf=/home/sirusweb/conf/web/sirussofts.com
[[ -d "$root" && ! -L "$root" && -d "$conf" && ! -L "$conf" ]] ||
    fail 'Expected existing Hestia CMS private data missing'
[[ -f "$root/work.sqlite3" && ! -L "$root/work.sqlite3" ]] ||
    fail 'Work broker unavailable'
[[ -f /home/sirusweb/web/sirussofts.com/public_html/app/admin.php ]] ||
    fail 'Deployed CMS code missing'
key="$root/live.key"
unit=/etc/systemd/system/sirus-live-socket.service
exe=/usr/local/libexec/sirus-live-socket.py
route="$conf/nginx.ssl.conf_siruslive"
[[ ! -L "$key" && ! -L "$unit" && ! -L "$exe" && ! -L "$route" ]] ||
    fail 'Unsafe symlink in deployment path'
[[ ! -e "$unit" ]] || cmp -s "$unit" "$here/sirus-live-socket.service" ||
    fail 'Existing systemd service differs from reviewed template'
[[ ! -e "$route" ]] || cmp -s "$route" "$here/nginx.ssl.conf_siruslive" ||
    fail 'Existing Nginx route differs from reviewed template'
[[ ! -e "$key" ]] || [[ -f "$key" && $(stat -c %U "$key") == sirusweb &&
    $(stat -c %a "$key") == 600 && $(stat -c %s "$key") == 32 ]] ||
    fail 'Existing WebSocket authentication key is unsafe'
if [[ "$mode" == --plan ]]; then
    echo "Private HMAC key: $key (preserved if already present)"
    echo "Worker executable: $exe"
    echo "TLS-only Hestia Nginx include: $route"
    echo 'Binds ONLY to 127.0.0.1:18482; no Gentoo tunnel or CMS database changes.'
    exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive terminal required'
read -r -p 'Type INSTALL LIVE SOCKET: ' response
[[ "$response" == 'INSTALL LIVE SOCKET' ]] || fail 'Operator declined'
install -d -o root -g root -m 0755 /usr/local/libexec
if [[ ! -f "$key" ]]; then
    # Random key is shared by CMS PHP and the unprivileged WebSocket server.
    python3 - "$key" <<'PY'
import os, pathlib, sys
p = pathlib.Path(sys.argv[1])
fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "wb") as stream:
    stream.write(os.urandom(32))
PY
    chown sirusweb:sirusweb "$key"
fi
if [[ -f "$exe" ]]; then
    cp -p -- "$exe" "$exe.before-live-$(date +%s)"
fi
install -o root -g root -m 0755 "$here/server.py" "$exe"
if [[ ! -f "$unit" ]]; then
    install -o root -g root -m 0644 "$here/sirus-live-socket.service" "$unit"
fi
if [[ ! -f "$route" ]]; then
    install -o root -g root -m 0644 "$here/nginx.ssl.conf_siruslive" "$route"
fi
if ! nginx -t; then
    echo 'Nginx rejected the WebSocket include. Not reloading Nginx.' >&2
    fail 'Restore or remove only the new include after reviewing Nginx output'
fi
nginx -T 2>&1 | grep -Fq "# configuration file $route:" ||
    fail 'Hestia HTTPS vhost did not load the new WebSocket include'
systemctl daemon-reload
systemctl enable --now sirus-live-socket.service
systemctl restart sirus-live-socket.service
systemctl is-active --quiet sirus-live-socket.service ||
    fail 'WebSocket server failed to start; check journalctl -u sirus-live-socket'
systemctl reload nginx
echo 'PASS live WebSocket server installed; HTTPS proxy loaded.'
echo 'CMS must contain new /admin/live-token endpoint and /assets/live.js.'
