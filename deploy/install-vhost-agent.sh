#!/usr/bin/env bash
# One-time, non-destructive root installer for restricted Plesk vhost bridge.
# Does not alter a domain, web document root, certificate, or existing application.
set -Eeuo pipefail
[[ \${EUID:-1} == 0 ]] || { echo 'Run as root from your PuTTY SSH session.' >&2; exit 1; }
HERE=$(cd -- "$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)
ROOT=/var/www/vhosts/sirussofts.com/httpdocs
[[ -d /usr/local/psa || -f /etc/psa/psa.conf ]] || { echo 'Plesk not detected. No changes made.' >&2; exit 1; }
command -v plesk >/dev/null || { echo 'Plesk CLI unavailable. No changes made.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Python 3 required.' >&2; exit 1; }
command -v systemctl >/dev/null || { echo 'systemd required.' >&2; exit 1; }
[[ -d "$ROOT" && ! -L "$ROOT" ]] || { echo 'Site document root missing or unsafe.' >&2; exit 1; }
WEB_UID=$(stat -c '%u' "$ROOT")
WEB_USER=$(stat -c '%U' "$ROOT")
[[ "$WEB_UID" != 0 && "$WEB_USER" != UNKNOWN ]] || { echo 'Plesk site root needs a non-root owner.' >&2; exit 1; }
# Only install the agent if the actual SirusSofts CMS site has been cut over.
[[ -f "$ROOT/index.php" && -f "$ROOT/app/core.php" ]] || { echo 'CMS not installed as site root; agent NOT enabled. Preserve cPoint.' >&2; exit 1; }
python3 -m py_compile "$HERE/vhost_agent.py"
install -d -o root -g root -m 0755 /opt/sirusdeploy
install -o root -g root -m 0644 "$HERE/vhost_agent.py" /opt/sirusdeploy/vhost_agent.py
install -o root -g root -m 0644 "$HERE/sirus-vhost.service" /etc/systemd/system/sirus-vhost.service
systemctl daemon-reload
systemctl enable --now sirus-vhost.service
systemctl is-active --quiet sirus-vhost.service || { echo 'Agent failed to start; inspect journalctl -u sirus-vhost.service' >&2; exit 1; }
echo "SirusSofts restricted vhost agent installed for site owner $WEB_USER."
echo 'Unix socket /run/sirus-vhost/agent.sock; no internet listener; no vhosts created.'
