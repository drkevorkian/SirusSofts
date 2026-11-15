#!/usr/bin/env bash
# Repair only the SirusSofts AI INPUT port after sirus-live-socket already
# occupies Ubuntu 127.0.0.1:18482. Never change any non-AI service.
set -Eeuo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }

[[ $# -eq 1 && ( "$1" == "--check" || "$1" == "--apply" ) ]] || fail "Usage: bash ubuntu/repair-input-port-18485.sh --check|--apply"
[[ "$(id -u)" == 0 ]] || fail "Run as root on Ubuntu"
. /etc/os-release
[[ "$ID" == ubuntu ]] || fail "Ubuntu required"

sshconf=/etc/ssh/sshd_config
auth=/var/lib/sirus-ai-in/.ssh/authorized_keys
nginxconf=/home/sirusweb/conf/web/sirussofts.com/nginx.ssl.conf_sirusai
for f in "$sshconf" "$auth" "$nginxconf"; do
    [[ -f "$f" && ! -L "$f" ]] || fail "Missing or symlinked installed AI configuration: $f"
done
[[ -f /etc/sirus-ai-dual/users.htpasswd ]] || fail "Existing AI website authentication missing"
systemctl is-active --quiet sirus-live-socket || fail "Existing Live WebSocket service is not running; investigate first"
/usr/sbin/sshd -t || fail "Existing SSH configuration invalid"
nginx -t || fail "Existing Nginx configuration invalid"

# Require exactly the installer-generated AI-specific markers.
# Reject unexpected/partial edits rather than guessing what to overwrite.
state="$(python3 - "$sshconf" "$auth" "$nginxconf" <<'PY'
from pathlib import Path
import sys
pairs = [
    (Path(sys.argv[1]), 'PermitListen 127.0.0.1:18482', 'PermitListen 127.0.0.1:18485'),
    (Path(sys.argv[2]), 'permitlisten="127.0.0.1:18482"', 'permitlisten="127.0.0.1:18485"'),
    (Path(sys.argv[3]), 'proxy_pass http://127.0.0.1:18482/api/in/;', 'proxy_pass http://127.0.0.1:18485/api/in/;')
]
counts = []
for path, old, new in pairs:
    data = path.read_text()
    counts.append((data.count(old), data.count(new)))
if counts == [(1, 0)] * 3:
    print('old')
elif counts == [(0, 1)] * 3:
    print('new')
else:
    raise SystemExit('FAIL: AI configuration differs from expected installer state: ' + str(counts))
PY
)" || fail "AI configuration requires review before changing"

if [[ "$state" == new ]]; then
    echo "PASS: AI INPUT port already migrated to Ubuntu 127.0.0.1:18485"
    exit 0
fi

# Must be unused before remapping. On purpose, this tests Ubuntu loopback,
# not Gentoo's independently-used CPU model port.
python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    try:
        s.bind(('127.0.0.1', 18485))
    except OSError as e:
        raise SystemExit('FAIL: Ubuntu 127.0.0.1:18485 is occupied: ' + str(e))
print('PASS: Ubuntu 127.0.0.1:18485 available')
PY

if [[ "$1" == --check ]]; then
    echo "PLAN: change only sirus-ai-in PermitListen, its authorized_keys permitlisten, and AI HTTPS proxy_pass"
    echo "PRESERVE: sirus-live-socket on 18482, AI output on 18483, original Gentoo tunnels and all other services"
    echo "PASS: AI INPUT migration preflight"
    exit 0
fi

backup="$(mktemp -d /root/sirus-ai-input-port-18485.XXXXXXXX)"
chmod 0700 "$backup"
cp -p -- "$sshconf" "$backup/sshd_config"
cp -p -- "$auth" "$backup/authorized_keys"
cp -p -- "$nginxconf" "$backup/nginx.ssl.conf_sirusai"
changed=0
rollback() {
    code=$?
    if (( code != 0 && changed == 1 )); then
        echo "ERROR: Restoring original AI INPUT port files from $backup" >&2
        cp -p -- "$backup/sshd_config" "$sshconf"
        cp -p -- "$backup/authorized_keys" "$auth"
        cp -p -- "$backup/nginx.ssl.conf_sirusai" "$nginxconf"
        /usr/sbin/sshd -t && systemctl reload ssh || true
        nginx -t && systemctl reload nginx || true
    fi
}
trap rollback EXIT
changed=1

python3 - "$sshconf" "$auth" "$nginxconf" <<'PY'
from pathlib import Path
import sys
pairs = [
    (Path(sys.argv[1]), 'PermitListen 127.0.0.1:18482', 'PermitListen 127.0.0.1:18485'),
    (Path(sys.argv[2]), 'permitlisten="127.0.0.1:18482"', 'permitlisten="127.0.0.1:18485"'),
    (Path(sys.argv[3]), 'proxy_pass http://127.0.0.1:18482/api/in/;', 'proxy_pass http://127.0.0.1:18485/api/in/;')
]
for path, old, new in pairs:
    data = path.read_text()
    if data.count(old) != 1 or new in data:
        raise SystemExit('Unexpected concurrent change in ' + str(path))
    path.write_text(data.replace(old, new))
PY

/usr/sbin/sshd -t || fail "New SSH configuration rejected"
in_check="$(/usr/sbin/sshd -T -C user=sirus-ai-in,host=localhost,addr=127.0.0.1)"
grep -qx 'permitlisten 127.0.0.1:18485' <<< "$in_check" || fail "SSH input PermitListen not effective"
grep -qx 'allowtcpforwarding remote' <<< "$in_check" || fail "SSH input must allow remote forwarding"
nginx -t || fail "New AI HTTPS configuration rejected"
dump="$(nginx -T 2>&1)"
grep -Fq "# configuration file $nginxconf:" <<< "$dump" || fail "Hestia is not loading AI HTTPS include"
systemctl reload ssh || fail "SSH reload failed"
systemctl reload nginx || fail "Nginx reload failed"
systemctl is-active --quiet sirus-live-socket || fail "Existing Live WebSocket service stopped unexpectedly"
changed=0
echo "PASS: Ubuntu AI INPUT migrated to 127.0.0.1:18485."
echo "Backup: $backup"
echo "NEXT: update Gentoo tunnel.py from GitHub, install into /opt/sirus-ai-dual/gentoo/, and restart only sirus-ai-input-tunnel."
