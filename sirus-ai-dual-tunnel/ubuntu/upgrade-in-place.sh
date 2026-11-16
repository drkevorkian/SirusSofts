#!/usr/bin/env bash
# In-place Ubuntu AI gateway/frontend update, not a first-time installer.
# CMS code must be upgraded with 08-upgrade-cms-hestia.sh before --apply.
set -Eeuo pipefail
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 ]] || fail 'Usage: bash ubuntu/upgrade-in-place.sh --plan|--apply'
mode="$1"
[[ "$mode" == --plan || "$mode" == --apply ]] || fail 'Unknown upgrade mode'
[[ "$(id -u)" -eq 0 ]] || fail 'Run as root on existing Ubuntu Hestia host'
. /etc/os-release
[[ "$ID" == ubuntu ]] || fail 'Ubuntu required'
source_dir="$(cd "$(dirname "$0")/../.." && pwd)"
src="$source_dir/sirus-ai-dual-tunnel/ubuntu"
live="/opt/sirus-ai-dual/ubuntu"
ssl="/home/sirusweb/conf/web/sirussofts.com/nginx.ssl.conf_sirusai"
cms="/home/sirusweb/web/sirussofts.com/public_html"
db="/var/lib/sirus-ai-dual/events.sqlite3"
files=(gateway.py web/index.html web/app.js web/styles.css
       web/render_math.js web/math.css web/temml.min.js web/TEMML_LICENSE.txt)
[[ -d "$live" && ! -L "$live" && -f "$ssl" && ! -L "$ssl" && -f "$db" && ! -L "$db" ]] ||
  fail 'Existing AI gateway, Hestia snippet or SQLite missing/linked'
[[ -f /etc/systemd/system/sirus-ai-gateway.service ]] ||
  fail 'Existing output service missing'
for file in "${files[@]}"; do
  [[ -f "$src/$file" && ! -L "$src/$file" ]] || fail "Source missing/linked: $file"
  if [[ -e "$live/$file" || -L "$live/$file" ]]; then
    [[ ! -L "$live/$file" ]] || fail "Installed gateway component is a symlink: $file"
  fi
done
python3 -m py_compile "$src/gateway.py"
nginx -t || fail 'Existing nginx configuration invalid'
apache2ctl -t || fail 'Existing Apache configuration invalid'
git -C "$source_dir" diff --quiet && git -C "$source_dir" diff --cached --quiet ||
  fail 'Tracked source has uncommitted edits'
expected="$(printenv SIRUS_EXPECTED_COMMIT || true)"
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] ||
  fail 'Set SIRUS_EXPECTED_COMMIT to the full approved commit SHA'
[[ "$(git -C "$source_dir" rev-parse HEAD)" == "$expected" ]] ||
  fail 'Checkout does not match approved commit'
if [[ "$mode" == --plan ]]; then
  echo 'PASS: Ubuntu output gateway / AI frontend in-place upgrade preflight'
  echo 'Does not install or change Hestia, SSH, TLS keys, tunnels or SQLite.'
  echo "Before --apply: upgrade CMS with deploy/ubuntu24/08-upgrade-cms-hestia.sh"
  echo "Frontend: $live/web ; gateway: $live/gateway.py"
  echo "Hestia snippet: $ssl ; must enable public UI only after guarded CMS API is live"
  echo 'Will restart sirus-ai-gateway and reload Nginx after preflight checks.'
  exit 0
fi
[[ -f "$cms/app/ai_api.php" && -f "$cms/app/ai_workspace.php" ]] ||
  fail 'New guarded CMS API not installed; upgrade CMS first'
php -l "$cms/app/ai_api.php" >/dev/null || fail 'Installed CMS API syntax invalid'
php -l "$cms/app/ai_workspace.php" >/dev/null || fail 'Installed CMS workspace syntax invalid'
[[ -t 0 && -t 1 ]] || fail 'Interactive terminal required'
read -rp 'Type UPGRADE UBUNTU AI to back up gateway and update frontend: ' answer
[[ "$answer" == 'UPGRADE UBUNTU AI' ]] || fail 'Confirmation mismatch'
release="/root/sirus-ai-upgrade-backups/release-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p -m 0700 "$release"
chmod 0700 "$release"
cp -a "$live" "$release/previous-ubuntu"
cp -p "$ssl" "$release/nginx.ssl.conf_sirusai"
python3 - "$db" "$release/events.sqlite3" <<'PY'
import sqlite3,sys
src=sqlite3.connect(f'file:{sys.argv[1]}?mode=ro',uri=True)
dst=sqlite3.connect(sys.argv[2])
try: src.backup(dst)
finally: dst.close();src.close()
PY
chmod 0600 "$release/events.sqlite3"
stage="$(mktemp -d /opt/sirus-ai-dual/ubuntu-stage.XXXXXXXX)"
previous=""
ok=0
rollback(){
  if [[ "$ok" != 1 ]]; then
    echo 'ERROR: Restoring Ubuntu gateway and Nginx snippet' >&2
    cp -p "$release/nginx.ssl.conf_sirusai" "$ssl" || true
    if [[ -n "$previous" && -d "$previous" ]]; then
      mv "$live" "$release/failed-ubuntu" 2>/dev/null || true
      mv "$previous" "$live" || true
    fi
    nginx -t >/dev/null && systemctl reload nginx || true
    systemctl restart sirus-ai-gateway || true
    echo "Private recovery: $release" >&2
    echo 'Live event SQLite untouched; snapshot is for manual recovery only.' >&2
  fi
  if [[ -d "$stage" ]]; then rm -rf -- "$stage"; fi
}
trap rollback EXIT
cp -a "$live/." "$stage/"
for file in "${files[@]}"; do
  install -m 0644 "$src/$file" "$stage/$file"
done
previous="/opt/sirus-ai-dual/ubuntu-previous-$$"
mv "$live" "$previous"
mv "$stage" "$live"
install -m 0644 -o root -g root "$src/nginx.ssl.conf_sirusai" "$ssl"
nginx -t || fail 'Updated Hestia Nginx snippet invalid'
systemctl restart sirus-ai-gateway || fail 'Output gateway restart failed'
for _ in 1 2 3 4 5 6; do
  curl -fsS --max-time 2 'http://127.0.0.1:18483/healthz' >/dev/null && break
  sleep 1
done
curl -fsS --max-time 3 'http://127.0.0.1:18483/healthz' >/dev/null ||
  fail 'Output gateway health check failed'
systemctl reload nginx || fail 'Nginx reload failed'
status_ui="$(curl -sS --max-time 8 --resolve sirussofts.com:443:127.0.0.1 \
  -o /dev/null -w '%{http_code}' https://sirussofts.com/Gentoo/AI/)"
[[ "$status_ui" == 200 ]] || fail "Public AI interface returned HTTP $status_ui, expected 200"
status_internal="$(curl -sS --max-time 8 --resolve sirussofts.com:443:127.0.0.1 \
  -o /dev/null -w '%{http_code}' https://sirussofts.com/Gentoo/AI/api/in/health)"
[[ "$status_internal" == 404 ]] || fail "Internal inference route exposed or unavailable incorrectly: $status_internal"
curl -fsS --max-time 8 --resolve sirussofts.com:443:127.0.0.1 \
  'https://sirussofts.com/ai/api/state' |
  python3 -c 'import json,sys; v=json.load(sys.stdin); assert v["local_only"] is True and v["role"]=="guest"' ||
  fail 'CMS anonymous local-only state route failed'
ok=1
mkdir -p "$release/old"
mv "$previous" "$release/old/ubuntu"
echo "PASS: Ubuntu AI gateway upgraded; private backup: $release"
echo 'No document-parser cron, admin login, SSH tunnel, GPU or CMS database was changed.'
