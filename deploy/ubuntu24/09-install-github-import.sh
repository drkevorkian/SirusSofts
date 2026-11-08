#!/usr/bin/env bash
# Install metadata-only GitHub importer into a LIVE Hestia CMS.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="--check"; if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
[[ $# -eq 0 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
  fail 'Usage: 09-install-github-import.sh --check|--plan|--install'
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/github-cms-import.py"
SERVICE="$HERE/sirus-github-import.service"
TIMER="$HERE/sirus-github-import.timer"
[[ -f "$PY" && -f "$SERVICE" && -f "$TIMER" ]] ||
  fail 'Missing GitHub import source files'
python3 -m py_compile "$PY" || fail 'Invalid Python importer'
python3 "$PY" --check
if [[ "$mode" == --check ]]; then
  echo 'PASS GitHub importer installer available; no changes'
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail 'Use root PuTTY for Hestia service setup'
id sirusweb >/dev/null || fail 'Hestia sirusweb account missing'
DB=/home/sirusweb/web/sirussofts.com/private/sirus-cms/data/cms.sqlite3
[[ -f "$DB" && ! -L "$DB" && "$(stat -c %U "$DB")" == sirusweb ]] ||
  fail 'Existing sirusweb CMS SQLite database is not ready'
SECRET=/etc/sirussofts/github.token
if [[ "$mode" == --plan ]]; then
  echo "Repository import worker will run as: sirusweb"
  echo "Private existing database: $DB"
  if [[ -f "$SECRET" ]]; then echo 'Token file present'; else echo 'Token file absent'; fi
  echo 'Source code is NEVER cloned, executed or published by this importer.'
  echo 'Previously unknown repositories become unpublished CMS projects.'
  echo 'Background timer polls requests every 2 minutes and refreshes every 6 hours.'
  echo 'No changes made.'
  exit 0
fi
[[ -f "$SECRET" && ! -L "$SECRET" ]] || fail 'Create root-owned GitHub credential file first'
[[ "$(stat -c '%U:%G' "$SECRET")" == root:root ]] || fail 'GitHub token must be root-owned'
[[ "$(stat -c %a "$SECRET")" == 600 ]] || fail 'GitHub token needs permissions 0600'
[[ "$(stat -c %a /etc/sirussofts)" == 700 ]] || fail '/etc/sirussofts needs permissions 0700'
[[ -r "$SECRET" && "$(wc -c < "$SECRET")" -ge 20 ]] || fail 'GitHub credential file is empty'
[[ -t 0 && -t 1 ]] || fail 'Interactive root console required'
read -r -p 'Type ENABLE GITHUB IMPORT to install the background service: ' approved
[[ "$approved" == 'ENABLE GITHUB IMPORT' ]] || fail 'Operator declined'
DEST=/usr/local/libexec/sirus-github-import.py
SYSTEMD=/etc/systemd/system
for unit in "$SYSTEMD/sirus-github-import.service" "$SYSTEMD/sirus-github-import.timer"; do
  [[ ! -e "$unit" && ! -L "$unit" ]] ||
    fail "Existing service file requires operator review: $unit"
done
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$PY" "$DEST"
install -o root -g root -m 0644 "$SERVICE" "$SYSTEMD/sirus-github-import.service"
install -o root -g root -m 0644 "$TIMER" "$SYSTEMD/sirus-github-import.timer"
systemctl daemon-reload
systemctl enable --now sirus-github-import.timer
echo 'PASS GitHub import timer installed; token is not in web root or Git checkout.'
echo 'Run first import: systemctl start sirus-github-import.service'
echo 'See result: journalctl -u sirus-github-import.service -n 30 --no-pager'
