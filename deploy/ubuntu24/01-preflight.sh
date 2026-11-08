#!/usr/bin/env bash
# Read-only checks for a CLEAN Ubuntu 24.04 host before installing HestiaCP.
set -Eeuo pipefail
mode="--check"; if [[ $# -gt 0 ]]; then mode="$1"; fi
[[ $# -le 1 && ( "$mode" == "--check" || "$mode" == "--strict" ) ]] || {
  echo 'Usage: 01-preflight.sh [--check|--strict]' >&2; exit 2;
}
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
note(){ printf '%s\n' "$*"; }
[[ -r /etc/os-release ]] || fail '/etc/os-release unavailable'
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == "24.04" ]] || fail 'This path targets Ubuntu 24.04 only'
case "$(uname -m)" in x86_64|aarch64) ;; *) fail 'Hestia requires x86_64 or arm64';; esac
note 'PASS Ubuntu 24.04 supported architecture.'
note 'This script performs NO package, firewall or server changes.'
if [[ "$mode" == "--check" ]]; then
  note 'PASS read-only bootstrap source check. Use --strict on the actual fresh server.'
  exit 0
fi
[[ "$(id -u)" == 0 ]] || fail 'Strict preflight requires root over SSH or console'
[[ ! -e /usr/local/hestia && ! -e /etc/hestiacp ]] || fail 'Hestia already present: this is not a fresh target'
for name in plesk psa ispconfig virtualmin; do
  if dpkg-query -W "$name" 2>/dev/null | grep -q .; then
    fail "Existing hosting panel package: $name"
  fi
done
command -v ss >/dev/null || fail 'iproute2 ss is required'
if systemctl is-active --quiet nginx || systemctl is-active --quiet apache2; then
  fail 'Existing web server is active. Refuse to install over a configured host.'
fi
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -qi '^Status: active'; then
  fail 'UFW is active. Hestia manages iptables; choose one firewall owner before installation.'
fi
mem_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo)"
[[ "$mem_kb" =~ ^[0-9]+$ && "$mem_kb" -ge 1048576 ]] ||
  fail 'At least 1 GiB RAM is required; 4 GiB recommended'
space_kb="$(df -Pk / | awk 'NR==2 {print $4}')"
[[ "$space_kb" =~ ^[0-9]+$ && "$space_kb" -ge 6291456 ]] ||
  fail 'Need at least 6 GiB free disk before installation; 40 GiB SSD recommended'
for port in 80 443 8080 8443 8083; do
  if ss -H -ltn "sport = :$port" | grep -q .; then
    fail "TCP $port already has a listener. Investigate before installing."
  fi
done
note 'PASS no conflicting HTTP, HTTPS, Apache-backend or panel listeners.'
note "RAM $((mem_kb / 1024)) MiB; free disk $((space_kb / 1024)) MiB."
note 'Review the provider firewall and SSH console access. Keep the current SSH session OPEN.'
note 'Required inbound: SSH (configured port), TCP 80/443. Restrict Hestia TCP 8083.'
note 'Do not open 3306, 8080, 8443, 8787 or private node ports publicly.'
note 'PASS strict clean-host preflight.'
