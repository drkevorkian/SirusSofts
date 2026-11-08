#!/usr/bin/env bash
# No writes; report launch readiness and unsafe exposed listeners.
set -Eeuo pipefail
mode="--check"; if [[ $# -gt 0 ]]; then mode="$1"; fi
[[ $# -le 1 && ( "$mode" == "--check" || "$mode" == "--live" ) ]] ||
  { echo 'Usage: 04-audit.sh [--check|--live]' >&2; exit 2; }
if [[ "$mode" == --check ]]; then
  echo 'PASS read-only audit available. Use --live after Hestia installation.'
  exit 0
fi
bad=0
check(){ if "$@" >/dev/null 2>&1; then printf 'PASS %s\n' "$*"; else printf 'FAIL %s\n' "$*"; bad=1; fi; }
for name in nginx apache2 mariadb fail2ban; do check systemctl is-active --quiet "$name"; done
if [[ -x /usr/local/hestia/bin/v-list-sys-config ]]; then
  echo 'PASS Hestia control panel CLI detected'
else
  echo 'FAIL Hestia CLI missing'; bad=1
fi
check nginx -t
check apache2ctl -t
for version in 8.3 8.4; do
  if command -v "php$version" >/dev/null; then
    check "php$version" -r 'exit(extension_loaded("pdo_sqlite") && extension_loaded("sodium") && extension_loaded("pdo_mysql") ? 0:1);'
    if command -v "php-fpm$version" >/dev/null; then check "php-fpm$version" -t; fi
  fi
done
if command -v ufw >/dev/null && ufw status | grep -qi '^Status: active'; then
  echo 'FAIL UFW is active alongside Hestia firewall'; bad=1
fi
echo 'TCP listeners (VERIFY Apache 8080/8443 and MySQL 3306 are NOT publicly reachable):'
ss -ltnp | grep -E '(^State|:(22|80|443|8080|8443|8083|3306)[[:space:]])' || true
echo 'Hestia firewall rules (check default public 8083 ACCEPT has been removed):'
if [[ -x /usr/local/hestia/bin/v-list-firewall ]]; then
  /usr/local/hestia/bin/v-list-firewall || bad=1
  HERE="$(cd "$(dirname "$0")" && pwd)"
  if ! /usr/local/hestia/bin/v-list-firewall json |
       python3 "$HERE/firewall_rules.py" --audit; then
    echo 'FAIL: panel publicly exposed or Hestia firewall audit inconclusive'
    bad=1
  fi
else bad=1; fi
echo 'Hestia panel should be accessed only over an SSH tunnel or approved IP.'
echo 'Verify 2FA enabled and recovery codes stored OFF-server.'
echo 'Test external 80/443 + restricted 8083 from a different machine.'
exit "$bad"
