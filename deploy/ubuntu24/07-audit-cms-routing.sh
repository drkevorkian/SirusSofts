#!/usr/bin/env bash
# Gate for first production DNS cutover: both Hestia HTTP AND HTTPS guards
# must be included and HTTPS must serve only the approved public CMS.
# Read-only: no Hestia settings, DNS, webserver or firewall changes.
set -Eeuo pipefail
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
mode="--check"; if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
user=""; domain=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) [[ $# -ge 2 ]] || fail '--user requires a value'; user="$2"; shift 2;;
    --domain) [[ $# -ge 2 ]] || fail '--domain requires a value'; domain="$2"; shift 2;;
    *) fail "Unknown option: $1";;
  esac
done
[[ "$mode" == "--check" || "$mode" == "--live" ]] ||
  fail 'Usage: 07-audit-cms-routing.sh --check|--live --user OWNER --domain sirussofts.org'
if [[ "$mode" == "--check" ]]; then
  echo 'PASS dual-domain HTTP/HTTPS public and private-route audit available; no changes.'
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail 'Run read-only Nginx inspection as root'
[[ "$user" =~ ^[a-z][a-z0-9_-]{2,30}$ && "$domain" == 'sirussofts.org' ]] ||
  fail 'Invalid Hestia website identity; root domain must be sirussofts.org'
CONF="/home/$user/conf/web/$domain"
for filename in nginx.conf_siruscms nginx.ssl.conf_siruscms; do
  path="$CONF/$filename"
  [[ -f "$path" && ! -L "$path" ]] || fail "Hestia custom guard missing: $path"
  [[ "$(stat -c '%U:%G' "$path")" == "root:root" ]] ||
    fail "Nginx guard is not root-owned: $path"
  cmp -s "$path" "$(dirname "$0")/sirus-cms-protect.nginx.conf" ||
    fail "Nginx guard differs from the reviewed deployment version: $path"
done
nginx -t || fail 'Nginx configuration invalid'
dump="$(nginx -T 2>&1)" || fail 'Cannot inspect effective Nginx configuration'
for filename in nginx.conf_siruscms nginx.ssl.conf_siruscms; do
  path="$CONF/$filename"
  if ! grep -Fq "# configuration file $path:" <<< "$dump"; then
    fail "Hestia is not using its $filename include. Check its selected template."
  fi
done
echo 'PASS HTTP and HTTPS guards are both installed and loaded by Nginx.'
command -v curl >/dev/null || fail 'cURL required for TLS and HTTP assertions'
for hostname in sirussofts.org sirussofts.com; do
  for uri in / /admin/login /app/core.php /app/secret.js /bin/setup.php /bin/secret.css /tests/smoke.php /private/ /db.sqlite3; do
    status="$(curl --noproxy '*' --silent --show-error --max-time 12 \
        --output /dev/null --write-out '%{http_code}' "https://$hostname$uri")" ||
      fail "TLS or HTTP request failed: https://$hostname$uri"
    case "$uri" in
      /|/admin/login)
        [[ "$status" == "200" ]] ||
          fail "Public CMS HTTPS route did not return 200: $hostname$uri -> $status";;
      *)
        [[ "$status" == "403" || "$status" == "404" ]] ||
          fail "PRIVATE CMS route exposed or unavailable: $hostname$uri -> $status";;
    esac
  done
  echo "PASS HTTPS homepage/admin and private file denials on $hostname"
done
echo 'PASS the same reviewed Hestia site serves both root domains securely.'
echo 'Also verify WWW aliases and independent remote firewall tests before public launch.'
