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
research_modules=(research_loop.py continuous_research.py research_evidence.py research_admin_auth.py research_archive.py research_cold_history.py manual_relationships.py manual_relationship_index.py feasibility_review.py web_source_summary.py web_research.py manual_summary.py)
for file in "${research_modules[@]}"; do
  research_src="$source_dir/sirus-ai-dual-tunnel/gentoo/$file"
  research_dest="/opt/sirus-ai-dual/gentoo/$file"
  [[ -f "$research_src" && ! -L "$research_src" &&
     ( ! -e "$research_dest" || ( -f "$research_dest" && ! -L "$research_dest" ) ) ]] ||
    fail "Missing or unsafe research module: $file"
  python3 -m py_compile "$research_src"
done
command -v curl >/dev/null || fail 'curl required'
[[ -f "$source_dir/sirus-ai-dual-tunnel/gentoo/knowledge.py" ]] ||
  fail 'Missing current Gentoo knowledge dependency'
rag_src="$source_dir/sirus-ai-dual-tunnel/gentoo/manual_rag.py"
rag_dest="/opt/sirus-ai-dual/gentoo/manual_rag.py"
root=/srv/sirussofts/storage
manual_db="$root/databases/pdf_ingest.sqlite3"
[[ -f "$rag_src" ]] || fail 'Missing approved-manual retrieval module'
python3 -m py_compile "$rag_src"
python3 "$rag_src" --check || fail 'Gentoo Python lacks SQLite FTS5'
for cmd in mountpoint setfacl getfacl runuser findmnt blkid; do
  command -v "$cmd" >/dev/null || fail "Missing PDF RAG prerequisite: $cmd"
done
id sirusai >/dev/null || fail 'Existing inference service account missing'
mountpoint -q "$root" || fail 'Dedicated SSD not mounted'
[[ "$(findmnt -M "$root" -n -o FSTYPE)" == ext4 ]] || fail 'Unexpected SSD filesystem'
[[ "$(blkid -s UUID -o value /dev/sdc1)" == "2531d241-3f0d-4fe8-af82-8059328ad3b3" ]] ||
  fail 'Unexpected PDF SSD UUID; refusing RAID0 fallback'
[[ -f "$manual_db" && ! -L "$manual_db" && -d "$root/databases" &&
   ! -L "$root/databases" ]] || fail 'Approved manual database missing'
[[ ! -e "$rag_dest" || ( -f "$rag_dest" && ! -L "$rag_dest" ) ]] ||
  fail 'Existing manual RAG installation is linked or unsafe'
[[ ! -L "$root/rag" ]] || fail 'Unsafe manual FTS index directory'
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
  echo "Adds local research modules and offline relationship index; enables checkpointed 1-100-pass research jobs"
  echo "Will rebuild ONLY a derived FTS5 index of already approved manuals on the SSD"
  echo "Will grant sirusai read access to the ingest SQLite DB and its WAL sidecars"
  echo "Will restart: sirus-ai-input ONLY; no GPU, SSH, tunnel or OpenRC system changes"
  echo "Rollback code and consistent DB snapshot kept under /root/sirus-ai-upgrade-backups"
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive terminal required'
read -rp 'Type UPGRADE GENTOO AI to back up and replace router code: ' answer
[[ "$answer" == 'UPGRADE GENTOO AI' ]] || fail 'Confirmation mismatch'
release="/root/sirus-ai-upgrade-backups/release-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p -m 0700 "$release"
chmod 0700 "$release"
cp -p "$dest" "$release/input_server.py"
for file in "${research_modules[@]}"; do
  if [[ -f "/opt/sirus-ai-dual/gentoo/$file" ]]; then
    cp -p "/opt/sirus-ai-dual/gentoo/$file" "$release/$file"
  fi
done
[[ ! -f "$rag_dest" ]] || cp -p "$rag_dest" "$release/manual_rag.py"
getfacl -p "$root" "$root/databases" "$manual_db" > "$release/manual-acls.txt"
for suffix in '-wal' '-shm'; do
  [[ ! -f "$manual_db$suffix" ]] || getfacl -p "$manual_db$suffix" >> "$release/manual-acls.txt"
done
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
    for file in "${research_modules[@]}"; do
      if [[ -f "$release/$file" ]]; then
        cp -p "$release/$file" "/opt/sirus-ai-dual/gentoo/$file" || true
      else
        rm -f -- "/opt/sirus-ai-dual/gentoo/$file" || true
      fi
    done
    if [[ -f "$release/manual_rag.py" ]]; then
      cp -p "$release/manual_rag.py" "$rag_dest" || true
    else
      rm -f -- "$rag_dest" || true
    fi
    rc-service sirus-ai-input restart || true
    echo "Recovery directory: $release" >&2
    echo 'The live SQLite database was never replaced; use snapshot only after manual review.' >&2
  fi
}
trap rollback EXIT
# Keep live model services untouched while building the new search index.
install -d -o sirusai -g sirusai -m 0700 "$root/rag"
setfacl -m u:sirusai:--x "$root" "$root/databases"
setfacl -m u:sirusai:r-- "$manual_db"
# SQLite WAL/SHM may be recreated while the PDF worker runs.
setfacl -d -m u:sirusai:r-- "$root/databases"
for suffix in '-wal' '-shm'; do
  [[ ! -f "$manual_db$suffix" ]] || setfacl -m u:sirusai:r-- "$manual_db$suffix"
done
runuser -u sirusai -- test -r "$manual_db" ||
  fail 'AI service cannot read reviewed SQLite database'
install -m 0644 "$rag_src" "${rag_dest}.next"
mv -f "${rag_dest}.next" "$rag_dest"
echo 'Indexing approved Gentoo PDFs locally; this may take time for 1,500+ manuals.'
runuser -u sirusai -- python3 -B "$rag_dest" --sync ||
  fail 'Manual full-text index build failed; router remains on old code'
for file in "${research_modules[@]}"; do
  install -m 0644 "$source_dir/sirus-ai-dual-tunnel/gentoo/$file" \
    "/opt/sirus-ai-dual/gentoo/$file.next"
  mv -f "/opt/sirus-ai-dual/gentoo/$file.next" "/opt/sirus-ai-dual/gentoo/$file"
done
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
