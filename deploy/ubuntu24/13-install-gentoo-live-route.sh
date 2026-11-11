#!/usr/bin/env bash
# Add ONLY the WebSocket location for /Gentoo/live on the existing Hestia HTTPS site.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="${1:---check}"
[[ $# -le 1 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
  fail 'Usage: 13-install-gentoo-live-route.sh --check|--plan|--install'
here="$(cd "$(dirname "$0")" && pwd)"
source="$here/nginx.ssl.conf_sirusgentoolive"
[[ -f "$source" && ! -L "$source" ]] || fail 'Reviewed route fragment missing'
grep -Fq 'location = /Gentoo/live {' "$source" || fail 'Expected fixed Gentoo location missing'
grep -Fq 'proxy_pass http://127.0.0.1:18480/live;' "$source" ||
  fail 'Gentoo socket target must use existing localhost SSH reverse tunnel'
if [[ "$mode" == --check ]]; then
  echo 'PASS fixed HTTPS Gentoo WebSocket route available; no files changed'
  exit 0
fi
[[ "$(id -u)" == 0 ]] || fail 'Ubuntu root required'
. /etc/os-release
[[ "${ID:-}" == ubuntu ]] || fail 'Ubuntu required'
conf=/home/sirusweb/conf/web/sirussofts.com
route="$conf/nginx.ssl.conf_sirusgentoolive"
[[ -d "$conf" && ! -L "$conf" ]] || fail 'Hestia CMS domain directory missing'
[[ ! -L "$route" ]] || fail 'Unsafe existing Nginx include symlink'
if [[ -f "$route" ]] && ! cmp -s "$route" "$source"; then
  fail 'Custom Gentoo live route already exists with different content; review manually'
fi
nginx -t || fail 'Existing Nginx configuration invalid'
if [[ "$mode" == --plan ]]; then
  echo "Existing HTTPS site: sirussofts.com"
  echo "Reviewed additional custom include: $route"
  echo 'Single location /Gentoo/live -> 127.0.0.1:18480/live'
  echo 'Preserves /Gentoo/, HTTPS certs, Hestia templates and SSH tunnel.'
  echo 'gentoo.sirussofts.com template already supports Upgrade passthrough.'
  echo 'The dashboard itself requires a short-lived authenticated socket token.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive PuTTY session required'
read -r -p 'Type ENABLE GENTOO LIVE SOCKET: ' confirm
[[ "$confirm" == 'ENABLE GENTOO LIVE SOCKET' ]] || fail 'Operator declined'
new_file=0
if [[ ! -f "$route" ]]; then
  install -o root -g root -m 0644 "$source" "$route"
  new_file=1
fi
if ! nginx -t; then
  (( new_file == 0 )) || rm -f -- "$route"
  fail 'New Nginx route rejected; new include removed without reloading'
fi
if ! nginx -T 2>&1 | grep -Fq "# configuration file $route:"; then
  (( new_file == 0 )) || rm -f -- "$route"
  fail 'Existing Hestia HTTPS vhost did not load the reviewed socket include'
fi
if ! systemctl reload nginx; then
  (( new_file == 0 )) || rm -f -- "$route"
  nginx -t >/dev/null && systemctl reload nginx || true
  fail 'Nginx reload failed; reviewed socket include rolled back'
fi
echo 'PASS /Gentoo/live WebSocket proxy installed. Existing tunnel unchanged.'
