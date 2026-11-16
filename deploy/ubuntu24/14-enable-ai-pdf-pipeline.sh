#!/usr/bin/env bash
# Existing Hestia site ONLY. Enables private, sandboxed PDF processing and 1GiB uploads.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="${1:---plan}"
[[ $# -eq 1 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --apply ) ]] ||
  fail 'Usage: 14-enable-ai-pdf-pipeline.sh --check|--plan|--apply'
[[ "$mode" == --check ]] && { echo 'PASS: PDF pipeline installer available'; exit 0; }
[[ $(id -u) == 0 ]] || fail 'Run as root on Ubuntu'
. /etc/os-release
[[ "$ID" == ubuntu ]] || fail 'This is an Ubuntu/Hestia update only'
base="$(cd "$(dirname "$0")" && pwd)"
source="$base/../../CMS"
docroot=/home/sirusweb/web/sirussofts.com/public_html
private=/home/sirusweb/web/sirussofts.com/private/sirus-cms
nginx_conf=/home/sirusweb/conf/web/sirussofts.com/nginx.ssl.conf_sirusupload
upload_ini="$docroot/.user.ini"
service=/etc/systemd/system/sirus-ai-pdf-worker.service
timer=/etc/systemd/system/sirus-ai-pdf-worker.timer
[[ -d "$private/data" && -f "$private/data/cms.sqlite3" && -d "$docroot" ]] ||
  fail 'Existing CMS and private database missing'
[[ -x /usr/local/hestia/bin/v-list-web-domain ]] ||
  fail 'Hestia is not installed'
/usr/local/hestia/bin/v-list-web-domain sirusweb sirussofts.com >/dev/null ||
  fail 'Reviewed Hestia website missing'
for file in "$source/bin/ai-staged-pdf-worker.php" "$source/app/ai_pdf_pipeline.php" \
  "$base/sirus-ai-pdf-worker.service" "$base/sirus-ai-pdf-worker.timer" \
  "$base/sirus-ai-upload.nginx.conf" "$base/sirus-ai-upload.user.ini"; do
  [[ -f "$file" && ! -L "$file" ]] || fail "Missing or linked reviewed source: $file"
done
for file in "$upload_ini" "$nginx_conf" "$service" "$timer"; do
  [[ ! -L "$file" ]] || fail "Refusing linked destination: $file"
done
[[ -x /usr/bin/pdftotext && -x /usr/bin/timeout && -x /usr/bin/flock ]] ||
  fail 'Poppler pdftotext, timeout and flock required (apt install poppler-utils)'
[[ -x /usr/bin/php8.3 ]] || fail 'PHP 8.3 CLI missing'
php8.3 -m | grep -qi pdo_sqlite || fail 'PHP 8.3 SQLite support missing'
php8.3 -m | grep -qi mbstring || fail 'PHP 8.3 mbstring support missing'
php8.3 -l "$source/bin/ai-staged-pdf-worker.php" >/dev/null
php8.3 -l "$source/app/ai_pdf_pipeline.php" >/dev/null
[[ -f "$docroot/app/ai_pdf_pipeline.php" && -f "$docroot/bin/ai-staged-pdf-worker.php" ]] ||
  fail 'First deploy updated CMS code with 08-upgrade-cms-hestia.sh'
nginx -t >/dev/null || fail 'Current Nginx configuration invalid'
apache2ctl -t >/dev/null || fail 'Apache configuration invalid'
for f in /home/sirusweb/conf/web/sirussofts.com/nginx.conf_siruscms \
         /home/sirusweb/conf/web/sirussofts.com/nginx.ssl.conf_siruscms; do
  [[ -f "$f" && ! -L "$f" ]] || fail 'Private CMS Nginx guard missing'
  cmp -s "$f" "$base/sirus-cms-protect.nginx.conf" ||
    fail 'CMS Nginx private-file guard has drifted'
done
available="$(df -PB1 "$private" | awk 'NR==2{print $4}')"
[[ "$available" =~ ^[0-9]+$ && "$available" -gt 6442450944 ]] ||
  fail 'Keep at least 6 GiB free before enabling large PDF ingestion'
for f in "$service" "$timer"; do
  if [[ -e "$f" ]]; then
    cmp -s "$f" "$base/$(basename "$f")" ||
      fail "Existing service differs; do not overwrite: $f"
  fi
done
if [[ -e "$nginx_conf" ]]; then
  cmp -s "$nginx_conf" "$base/sirus-ai-upload.nginx.conf" ||
    fail 'Existing site-specific upload config differs; manual review required'
fi
echo 'PASS: Existing CMS, private storage, parser binaries and sandbox unit sources verified'
echo '1 GiB per PDF, 1 GiB combined batch; PHP 1024M/1100M and site-only Nginx 1100m'
echo 'The apply step backs up .user.ini, installs a sandboxed systemd timer and never touches old PDFs or the CMS database.'
echo 'Nginx will reject duplicate upload-size directives; no global configuration edits.'
if [[ "$mode" == --plan ]]; then exit 0; fi
[[ -t 0 && -t 1 ]] || fail 'Interactive terminal required'
read -r -p 'Type ENABLE PRIVATE PDF PIPELINE to activate: ' confirm
[[ "$confirm" == 'ENABLE PRIVATE PDF PIPELINE' ]] || fail 'Confirmation mismatch'
backup="$(mktemp -d /root/sirus-ai-pdf-backup-XXXXXXXX)"
chmod 0700 "$backup"
if [[ -f "$upload_ini" ]]; then cp -p "$upload_ini" "$backup/user.ini"; fi
had_nginx=0
[[ -f "$nginx_conf" ]] && had_nginx=1
if [[ "$had_nginx" -eq 0 ]]; then
  install -m 0644 -o root -g root "$base/sirus-ai-upload.nginx.conf" "$nginx_conf"
fi
if ! nginx -t; then
  [[ "$had_nginx" == 0 ]] && rm -f "$nginx_conf"
  fail 'Nginx upload snippet conflicts with an existing directive; existing configuration restored'
fi
nginx_dump="$(nginx -T 2>&1)" || fail 'Could not inspect active Nginx'
if ! grep -Fq "# configuration file $nginx_conf:" <<< "$nginx_dump"; then
  [[ "$had_nginx" == 0 ]] && rm -f "$nginx_conf"
  fail 'Hestia did not load the HTTPS upload snippet'
fi
# Merge upload-related keys only; preserve other site PHP overrides.
python3 - "$upload_ini" <<'PY'
import os,re,sys,tempfile,pwd
p=sys.argv[1]
original=open(p,encoding="utf-8").read() if os.path.isfile(p) else ""
managed={'upload_max_filesize':'1024M','post_max_size':'1100M',
         'max_input_time':'1200','max_file_uploads':'10'}
pattern=re.compile(r'^\s*('+ '|'.join(managed) +r')\s*=',re.I)
kept=[line for line in original.splitlines() if not pattern.match(line)]
kept+=["; SirusSofts AI per-site large PDF uploads (managed)"]
kept += [key+" = "+value for key,value in managed.items()]
fd,tmp=tempfile.mkstemp(prefix='.user.ini-',dir=os.path.dirname(p),text=True)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as out:
        out.write('\n'.join(kept)+'\n')
    uid=pwd.getpwnam('sirusweb').pw_uid
    gid=pwd.getpwnam('sirusweb').pw_gid
    os.chown(tmp,uid,gid);os.chmod(tmp,0o640)
    os.replace(tmp,p)
finally:
    if os.path.exists(tmp):os.unlink(tmp)
PY
install -m 0644 -o root -g root "$base/sirus-ai-pdf-worker.service" "$service"
install -m 0644 -o root -g root "$base/sirus-ai-pdf-worker.timer" "$timer"
systemctl daemon-reload
if ! systemctl enable --now sirus-ai-pdf-worker.timer; then
  systemctl disable --now sirus-ai-pdf-worker.timer || true
  if [[ -f "$backup/user.ini" ]]; then
    cp -p "$backup/user.ini" "$upload_ini"
  else
    rm -f "$upload_ini"
  fi
  [[ "$had_nginx" == 0 ]] && rm -f "$nginx_conf"
  nginx -t && systemctl reload nginx || true
  fail 'PDF timer could not be started; old upload settings restored'
fi
systemctl reload nginx
systemctl reload php8.3-fpm
nginx -t >/dev/null || fail 'Nginx syntax failure after PDF pipeline activation'
status="$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 10 \
  https://sirussofts.com/.user.ini || true)"
[[ "$status" == 404 ]] || fail "Site .user.ini is publicly accessible or unavailable incorrectly (HTTP $status)"
echo "PASS: Site-scoped 1 GiB uploads and sandboxed PDF queue enabled; backup: $backup"
echo 'Existing staged PDFs are automatically queued. Check systemctl status sirus-ai-pdf-worker.timer'
echo 'Nginx size 1100m, PHP 1024M/1100M configured. Verify via a real 350 MB admin upload.'
