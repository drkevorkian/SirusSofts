#!/usr/bin/env bash
# Fresh-host two-stage Hestia install: download/review, then explicitly hash-checked execute.
set -Eeuo pipefail
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="--check"; if [[ $# -gt 0 ]]; then MODE="$1"; shift; fi
HOST=""; EMAIL=""; ACCEPT_SHA=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname) [[ $# -ge 2 ]] || fail '--hostname needs a value'; HOST="$2"; shift 2;;
    --email) [[ $# -ge 2 ]] || fail '--email needs a value'; EMAIL="$2"; shift 2;;
    --sha256) [[ $# -ge 2 ]] || fail '--sha256 needs a value'; ACCEPT_SHA="$2"; shift 2;;
    *) fail "Unexpected option: $1";;
  esac
done
[[ "$MODE" == --check || "$MODE" == --download || "$MODE" == --install ]] ||
  fail 'Usage: 02-install-hestia.sh --check | --download|--install --hostname FQDN --email ADDRESS [--sha256 HASH]'
bash "$HERE/01-preflight.sh" --check
if [[ "$MODE" == "--check" ]]; then
  echo 'PASS Hestia installer staged: Nginx + Apache + PHP-FPM + MariaDB + iptables + Fail2Ban.'
  echo 'No DNS, mail, FTP, API or public deployment is enabled by this script.'
  exit 0
fi
[[ "$HOST" =~ ^[a-z0-9][a-z0-9.-]{3,250}[a-z0-9]$ && "$HOST" == *.* && "$HOST" != *..* ]] ||
  fail 'Provide a valid intended FQDN for the control-panel host'
[[ "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] ||
  fail 'Provide a valid administrator email'
[[ "$(id -u)" -eq 0 ]] || fail 'Root required for installation'
bash "$HERE/01-preflight.sh" --strict
DEST="/root/sirussofts-bootstrap"; SCRIPT="$DEST/hst-install.sh"
[[ ! -L "$DEST" && ! -L "$SCRIPT" ]] || fail 'Unexpected symlink in bootstrap files'
if [[ "$MODE" == "--download" ]]; then
  command -v curl >/dev/null || fail 'Install curl/ca-certificates via apt first'
  install -d -m 0700 -o root -g root "$DEST"
  tmp="$(mktemp "$DEST/.hst-install.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
    --retry 3 --connect-timeout 10 --max-time 90 \
    https://raw.githubusercontent.com/hestiacp/hestiacp/release/install/hst-install.sh -o "$tmp"
  [[ -s "$tmp" ]] && head -n 2 "$tmp" | grep -qi 'bash' ||
    fail 'Downloaded file does not appear to be an installation script'
  chmod 0600 "$tmp"
  mv -f -- "$tmp" "$SCRIPT"
  trap - EXIT
  echo "Official Hestia release installer downloaded to $SCRIPT"
  sha256sum "$SCRIPT"
  echo 'REVIEW this script before --install. Copy the exact SHA-256 to --sha256.'
  echo 'No installer has been executed.'
  exit 0
fi
[[ "$ACCEPT_SHA" =~ ^[a-f0-9]{64}$ ]] || fail 'Supply the reviewed lowercase installer SHA-256'
[[ -f "$SCRIPT" && ! -L "$SCRIPT" ]] || fail 'Run --download first'
actual="$(sha256sum "$SCRIPT" | awk '{print $1}')"
[[ "$actual" == "$ACCEPT_SHA" ]] || fail 'Official installer checksum differs from approved download'
[[ -t 0 && -t 1 ]] || fail 'Interactive SSH/console session required; never install from a webhook'
echo "Hestia will configure Nginx public 80/443, Apache backend, PHP-FPM, MariaDB,"
echo 'its iptables firewall and Fail2Ban. It may require a reboot.'
echo 'Keep provider console access, retain SSH and protect port 8083 after installation.'
echo 'Hestia will ask for credentials interactively; never put passwords on command line.'
read -r -p 'Type INSTALL HESTIA to begin: ' confirmation
[[ "$confirmation" == 'INSTALL HESTIA' ]] || fail 'Operator declined'
exec bash "$SCRIPT" \
  --apache yes --phpfpm yes --multiphp '8.3,8.4' \
  --mysql yes --mysql8 no --postgresql no \
  --vsftpd no --proftpd no --named no --exim no --dovecot no \
  --clamav no --spamassassin no --iptables yes --fail2ban yes \
  --api no --webterminal no --interactive yes \
  --hostname "$HOST" --email "$EMAIL"
