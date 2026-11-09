#!/usr/bin/env bash
# Pin the Ubuntu VPS SSH host key for the *interactive Gentoo user*.
# The separate sirusnode tunnel key is managed by install-web-gateway.sh --pin-host.
set -Eeuo pipefail
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage: bash pin-bootstrap-ssh-host.sh --fingerprint SHA256:VERIFIED_FROM_UBUNTU [--port 22]
On Ubuntu, get the fingerprint independently:
  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
Run as the Gentoo login user (not sudo). Never turn off strict host checking.
USAGE
}
expected="" port=22
while (($#)); do
  case "$1" in
    --fingerprint) (($# >= 2)) || fail "Missing fingerprint"; expected="$2"; shift 2;;
    --port) (($# >= 2)) || fail "Missing port"; port="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) fail "Unknown option: $1";;
  esac
done
[[ $expected =~ ^SHA256:[A-Za-z0-9+/]{20,70}$ ]] || { usage; fail "A verified SHA256 fingerprint is required"; }
[[ $port =~ ^[0-9]+$ ]] && ((10#$port>=1 && 10#$port<=65535)) || fail "Invalid port"
[[ $(id -u) -ne 0 ]] || fail "Run as your normal Gentoo login user, not root/sudo"
for cmd in ssh-keyscan ssh-keygen mktemp; do command -v "$cmd" >/dev/null || fail "Missing $cmd"; done
host=74.208.40.187
if [[ $port == 22 ]]; then target="$host"; else target="[$host]:$port"; fi
umask 077
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
known="$HOME/.ssh/known_hosts"
[[ ! -L $known ]] || fail "Refusing symlink known_hosts: $known"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
ssh-keyscan -T 8 -p "$port" -t ed25519 "$host" 2>/dev/null > "$tmp" || fail "Cannot fetch ED25519 host key"
[[ -s $tmp ]] || fail "Server supplied no ED25519 host key"
actual="$(ssh-keygen -lf "$tmp" -E sha256 | awk 'NR==1 {print $2}')"
[[ $actual == "$expected" ]] || fail "HOST KEY MISMATCH: expected $expected, received $actual; no changes made"
if [[ -e $known ]]; then
  [[ -f $known ]] || fail "known_hosts is not a regular file"
  if ssh-keygen -F "$target" -f "$known" >/dev/null; then
    echo "An entry already exists for $target. Verify or resolve it manually; no changes made."
    exit 0
  fi
fi
cat "$tmp" >> "$known"
chmod 600 "$known"
echo "PASS: ED25519 Ubuntu host key independently verified and recorded for $USER"
echo "You may rerun SirusSofts-Gentoo-Web-Bootstrap.sh."
echo "NOTE: the sirusnode reverse tunnel uses a SEPARATE pinned key file."
