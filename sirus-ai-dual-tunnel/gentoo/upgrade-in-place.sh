#!/usr/bin/env bash
# In-place upgrade only. Does not install or alter SSH, GPU workers or tunnels.
set -Eeuo pipefail
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 ]] || fail 'Usage: bash gentoo/upgrade-in-place.sh --plan|--apply'
mode="$1"
[[ "$mode" == --plan || "$mode" == --apply ]] || fail 'Unknown upgrade mode'
[[ "$(id -u)" == 0 ]] || fail 'Run as root on the existing Gentoo host'
[[ -f /etc/gentoo-release ]] || fail 'Gentoo required'
command -v rc-service >/dev/null || fail 'OpenRC required'
source_dir="$(cd "$(dirname "$0")/../.." && pwd)"
src="$source_dir/sirus-ai-dual-tunnel/gentoo/input_server.py"
dest="/opt/sirus-ai-dual/gentoo/input_server.py"
db="/var/lib/sirus-ai-dual/input.sqlite3"
[[ -f "$src" && -f "$dest" && -f "$db" && ! -L "$dest" && ! -L "$db" ]] ||
  fail 'Existing Gentoo AI router/database or source missing'
[[ -e /etc/init.d/sirus-ai-input && -f /etc/sirus-ai-dual/secret ]] ||
  fail 'Expected installed input service and secret missing'
python3 -m py_compile "$src"
command -v curl >/dev/null || fail 'curl required'
[[ -f "$source_dir/sirus-ai-dual-tunnel/gentoo/knowledge.py" ]] ||
  fail 'Missing current Gentoo knowledge dependency'
git -C "$source_dir" diff --quiet && git -C "$source_dir" diff --cached --quiet ||
  fail 'Tracked source has uncommitted edits'
expected="$(printenv SIRUS_EXPECTED_COMMIT || true)"
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] ||
  fail 'Set SIRUS_EXPECTED_COMMIT to the full approved commit SHA'
[[ "$(git -C "$source_dir" rev-parse HEAD)" == "$expected" ]] ||
  fail 'Checkout does not match approved commit'
if [[ "$mode" == --plan ]]; then
  echo "PASS: Gentoo in-place AI router upgrade preflight (read-only)"
  echo "Source: $src"
  echo "Installed: $dest"
  echo "SQLite: $db (never overwritten)"
  echo "Will restart: sirus-ai-input ONLY; no GPU, SSH, tunnel or OpenRC system changes"
  echo "Rollback code and consistent DB snapshot kept under /var/lib/sirus-ai-dual/releases"
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive terminal required'
read -rp 'Type UPGRADE GENTOO AI to back up and replace router code: ' answer
[[ "$answer" == 'UPGRADE GENTOO AI' ]] || fail 'Confirmation mismatch'
release="/var/lib/sirus-ai-dual/releases/release-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p -m 0700 "$release"
chmod 0700 "$release"
cp -p "$dest" "$release/input_server.py"
python3 - "$db" "$release/input.sqlite3" <<'PY'
import sqlite3,sys
src=sqlite3.connect(f'file:{sys.argv[1]}?mode=ro',uri=True)
dst=sqlite3.connect(sys.argv[2])
try: src.backup(dst)
finally: dst.close();src.close()
PY
chmod 0600 "$release/input.sqlite3"
ok=0
rollback(){
  if [[ "$ok" != 1 ]]; then
    echo 'ERROR: Restoring Gentoo input_server.py from private backup' >&2
    cp -p "$release/input_server.py" "$dest" || true
    rc-service sirus-ai-input restart || true
    echo "Recovery directory: $release" >&2
    echo 'The live SQLite database was never replaced; use snapshot only after manual review.' >&2
  fi
}
trap rollback EXIT
install -m 0644 "$src" "${dest}.next"
mv -f "${dest}.next" "$dest"
rc-service sirus-ai-input restart || fail 'Input router restart failed'
healthy=0
for _ in 1 2 3 4 5 6 7 8; do
  if curl -fsS --max-time 2 'http://127.0.0.1:18082/api/in/health' |
      python3 -c 'import sys,json; v=json.load(sys.stdin); assert v["local_only"] is True and "models" in v' 2>/dev/null; then
    healthy=1; break
  fi
  sleep 1
done
[[ "$healthy" == 1 ]] || fail 'New input service failed local-only health check'
ok=1
echo "PASS: Gentoo AI router upgraded, health verified. Backup: $release"
