#!/usr/bin/env bash
# Install/update only the credential-isolated, read-only GitHub source indexer.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="${1:---check}"
[[ $# -le 1 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
  fail 'Usage: 12-install-github-source-index.sh --check|--plan|--install'
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/github-source-index.py" --check
python3 - "$here/github-source-index.py" <<'PY'
import ast,sys
ast.parse(open(sys.argv[1],encoding="utf8").read())
PY
if [[ "$mode" == --check ]]; then echo 'PASS source indexer preflight'; exit 0; fi
[[ "$(id -u)" == 0 ]] || fail 'Ubuntu root required'
. /etc/os-release
[[ "${ID:-}" == ubuntu ]] || fail 'Ubuntu only'
id sirusweb >/dev/null || fail 'Hestia sirusweb missing'
root=/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes
secret=/etc/sirussofts/github.token
[[ -d "$root" && ! -L "$root" ]] || fail 'CMS private node directory unavailable'
[[ -f "$secret" && ! -L "$secret" && "$(stat -c %U:%G "$secret")" == root:root &&
   "$(stat -c %a "$secret")" == 600 ]] || fail 'Existing private GitHub importer token unavailable'
exe=/usr/local/libexec/sirus-github-source-index.py
systemd=/etc/systemd/system
for name in sirus-github-source-index.service sirus-github-source-index.timer; do
  dest="$systemd/$name"
  [[ ! -L "$dest" ]] || fail 'Unsafe systemd symlink'
  if [[ -f "$dest" ]] && ! cmp -s "$dest" "$here/$name"; then
    fail "Existing service differs: $name"
  fi
done
[[ ! -L "$exe" ]] || fail 'Unsafe executable symlink'
if [[ "$mode" == --plan ]]; then
  echo 'Runs a fixed GitHub API source fingerprint check as sirusweb.'
  echo "Token retained at $secret; never copied into a web file."
  echo "Private index stored in $root/source_index.json"
  echo 'Compares source only; no git checkout updates, package changes or Gentoo keys.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive console required'
read -r -p 'Type ENABLE SOURCE CHECKS: ' approved
[[ "$approved" == 'ENABLE SOURCE CHECKS' ]] || fail 'Operator declined'
install -d -o root -g root -m 0755 /usr/local/libexec
if [[ -f "$exe" ]]; then cp -p "$exe" "$exe.before-source-check-$(date +%s)"; fi
install -o root -g root -m 0755 "$here/github-source-index.py" "$exe"
for name in sirus-github-source-index.service sirus-github-source-index.timer; do
  if [[ ! -f "$systemd/$name" ]]; then
    install -o root -g root -m 0644 "$here/$name" "$systemd/$name"
  fi
done
systemctl daemon-reload
systemctl enable --now sirus-github-source-index.timer
systemctl start sirus-github-source-index.service
echo 'PASS source indexer installed. Check systemctl status sirus-github-source-index.service'
