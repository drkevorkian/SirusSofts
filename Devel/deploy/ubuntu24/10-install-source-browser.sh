#!/usr/bin/env bash
# Explicit, separate GitHub source-view capability for the existing Hestia CMS.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="--check";if [[ $# -gt 0 ]]; then mode="$1";shift;fi
[[ $# == 0 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install || "$mode" == --fix-autostart ) ]] ||
  fail 'Usage: 10-install-source-browser.sh --check|--plan|--install|--fix-autostart'
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/github-source-browser.py"
UNIT="$HERE/sirus-source-browser.service"
[[ -f "$PY" && -f "$UNIT" ]] || fail 'Source browser files absent'
python3 "$PY" --check
if [[ "$mode" == --check ]]; then
  echo 'PASS source browser installation source check; no changes'
  exit 0
fi
[[ "$(id -u)" == 0 ]] || fail 'Root console required'
id sirusweb >/dev/null || fail 'Missing sirusweb account'
DB=/home/sirusweb/web/sirussofts.com/private/sirus-cms/data/cms.sqlite3
[[ -f "$DB" && ! -L "$DB" && "$(stat -c %U "$DB")" == sirusweb ]] ||
  fail 'Existing CMS database unavailable'
TOKEN=/etc/sirussofts/github.token
[[ -f "$TOKEN" && ! -L "$TOKEN" && "$(stat -c '%U:%G:%a' "$TOKEN")" == root:root:600 ]] ||
  fail 'Expected protected root-owned GitHub token not present'
if [[ "$mode" == --plan ]]; then
  echo 'PASS existing website database and private GitHub credential verified.'
  echo 'Service will run as sirusweb, use a private Unix socket, and fetch only read-only GitHub source.'
  echo 'No code repository is cloned or deployed, and the credential is not exposed to PHP.'
  echo 'No server files modified.'
  exit 0
fi
if [[ "$mode" == --fix-autostart ]]; then
  SERVICE=/etc/systemd/system/sirus-source-browser.service
  [[ -f "$SERVICE" && ! -L "$SERVICE" &&
     "$(stat -c '%U:%G' "$SERVICE")" == root:root ]] ||
    fail 'Existing root-owned source browser unit missing'
  # Only modify the exact, previously reviewed static unit.
  cmp -s "$SERVICE" <(python3 -c 'import pathlib,sys; u=pathlib.Path(sys.argv[1]).read_bytes(); old=u.split(b"\n[Install]\n",1)[0]; sys.stdout.buffer.write(old.rstrip(b"\n")+b"\n")' "$UNIT") ||
    fail 'Existing service differs from the original static unit; manual review required'
  install -o root -g root -m 0644 "$UNIT" "$SERVICE"
  systemctl daemon-reload
  systemctl enable --now sirus-source-browser.service
  systemctl is-enabled --quiet sirus-source-browser.service ||
    fail 'Failed to enable source browser on reboot'
  systemctl is-active --quiet sirus-source-browser.service ||
    fail 'Source browser is not active'
  echo 'PASS source browser service enabled for reboot and running.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive installation required'
DEST=/usr/local/libexec/sirus-github-source-browser.py
SERVICE=/etc/systemd/system/sirus-source-browser.service
[[ ! -e "$SERVICE" && ! -L "$SERVICE" ]] ||
  fail 'Existing source browser service requires manual review; do not overwrite'
read -r -p 'Type ENABLE SOURCE BROWSER to activate private source viewing: ' answer
[[ "$answer" == 'ENABLE SOURCE BROWSER' ]] || fail 'Operator declined'
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$PY" "$DEST"
install -o root -g root -m 0644 "$UNIT" "$SERVICE"
systemctl daemon-reload
systemctl enable --now sirus-source-browser.service
systemctl is-active --quiet sirus-source-browser.service ||
  fail 'Source browser failed; check journalctl -u sirus-source-browser.service'
echo 'PASS source gateway enabled. Permission checks live in CMS; token remains root-owned.'
