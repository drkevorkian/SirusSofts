#!/usr/bin/env bash
# Standalone Gentoo node installer. Explicit OpenRC start is separate.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="--check"; if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
[[ $# -eq 0 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
  fail 'Usage: gentoo-node/install-openrc.sh --check|--plan|--install'
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 -m py_compile "$HERE/client.py" || fail 'Invalid Gentoo node syntax'
if [[ "$mode" == --check ]]; then echo 'PASS standalone Gentoo node syntax';exit 0;fi
[[ "$(id -u)" -eq 0 ]] || fail 'Root required for OpenRC installation'
[[ -f /etc/gentoo-release ]] || fail 'This application requires Gentoo'
command -v rc-service >/dev/null || fail 'OpenRC missing'
command -v ssh >/dev/null || fail 'OpenSSH client missing'
if [[ "$mode" == --plan ]]; then
  echo 'Gentoo node agent communicates outbound HTTPS to sirussofts.com.'
  echo 'Installs non-login sirusnode, a private HMAC key and OpenRC unit.'
  echo 'Does NOT start, enable or expose a public SSH port.'
  echo 'No changes made.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive Gentoo console required'
read -r -p 'Type INSTALL GENTOO NODE to install the app: ' answer
[[ "$answer" == 'INSTALL GENTOO NODE' ]] || fail 'Operator declined'
if ! id sirusnode >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/sirusnode --shell /sbin/nologin sirusnode
fi
BASE=/etc/sirus-gentoo-node
APP=/opt/sirus-gentoo-node
install -d -o root -g root -m 0755 "$APP"
install -d -o sirusnode -g sirusnode -m 0700 "$BASE"
install -m 0755 -o root -g root "$HERE/client.py" "$APP/client.py"
[[ ! -L "$BASE/heartbeat.key" ]] || fail 'Key symlink is unsafe'
if [[ ! -e "$BASE/heartbeat.key" ]]; then
  python3 -c 'import os; f=os.open("/etc/sirus-gentoo-node/heartbeat.key",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.write(f,os.urandom(32));os.close(f)'
  chown sirusnode:sirusnode "$BASE/heartbeat.key"
fi
if [[ ! -e "$BASE/config.json" ]]; then
  install -m 0600 -o sirusnode -g sirusnode "$HERE/config.example.json" "$BASE/config.json"
fi
SERVICE=/etc/init.d/sirus-gentoo-node
[[ ! -e "$SERVICE" && ! -L "$SERVICE" ]] || fail 'Existing service needs review'
install -m 0755 -o root -g root "$HERE/openrc/sirus-gentoo-node" "$SERVICE"
echo 'PASS Gentoo app installed, NOT STARTED. Securely enroll its key on Ubuntu.'
echo 'After checking HTTPS connectivity, run rc-service sirus-gentoo-node start'
echo 'Enable after validation: rc-update add sirus-gentoo-node default'
