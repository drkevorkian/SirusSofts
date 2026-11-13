#!/usr/bin/env bash
# Install the agent only. Does NOT touch nginx/Plesk/CMS.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then echo 'Run as root' >&2; exit 1; fi
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
command -v python3 >/dev/null || { echo 'python3 required' >&2; exit 1; }
command -v git >/dev/null || { echo 'git required' >&2; exit 1; }
if ! id sirusdeploy >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/sirusdeploy --shell /usr/sbin/nologin sirusdeploy
fi
install -d -o root -g sirusdeploy -m 0750 /etc/sirusdeploy /etc/sirusdeploy/keys
install -d -o sirusdeploy -g sirusdeploy -m 0755 /srv/sirussofts /srv/sirussofts/sites
install -d -o sirusdeploy -g sirusdeploy -m 0750 /var/lib/sirusdeploy
install -d -o root -g root -m 0755 /opt/sirusdeploy
install -o root -g root -m 0644 "$DIR/sirus_deploy.py" /opt/sirusdeploy/sirus_deploy.py
if [[ ! -f /etc/sirusdeploy/projects.json ]]; then
  install -o root -g sirusdeploy -m 0640 "$DIR/projects.example.json" /etc/sirusdeploy/projects.json
fi
if [[ ! -f /etc/sirusdeploy/webhook-secret ]]; then
  python3 - <<'PY'
import secrets
from pathlib import Path
Path("/etc/sirusdeploy/webhook-secret").write_text(secrets.token_hex(32) + "\n")
PY
  chown root:sirusdeploy /etc/sirusdeploy/webhook-secret
  chmod 0640 /etc/sirusdeploy/webhook-secret
fi
install -m 0644 "$DIR/sirus-deploy.service" /etc/systemd/system/sirus-deploy.service
python3 /opt/sirusdeploy/sirus_deploy.py check
systemctl daemon-reload
systemctl enable --now sirus-deploy.service
echo 'Installed on localhost:8787; all projects disabled until configured.'
echo 'See deploy/README.md for adding HTTPS proxy, read-only deploy keys and GitHub webhooks.'
