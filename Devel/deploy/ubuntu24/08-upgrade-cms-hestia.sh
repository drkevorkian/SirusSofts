#!/usr/bin/env bash
# Hestia-managed existing CMS code upgrade. Existing SQLite remains in place.
# This is NOT a first-run installer; it does NOT update Hestia vhosts or DNS.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="--plan"; if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
user="" domain=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) [[ $# -ge 2 ]] || fail '--user needs a value'; user="$2"; shift 2;;
    --domain) [[ $# -ge 2 ]] || fail '--domain needs a value'; domain="$2"; shift 2;;
    *) fail "Unexpected argument: $1";;
  esac
done
[[ "$mode" == --check || "$mode" == --plan || "$mode" == --apply ]] ||
  fail 'Usage: 08-upgrade-cms-hestia.sh --check|--plan|--apply --user sirusweb --domain sirussofts.com'
if [[ "$mode" == --check ]]; then
  echo 'PASS Hestia CMS upgrade helper is available; --check performs no changes.'
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail 'Root required for reviewed CMS code deployment'
[[ "$user" == sirusweb && "$domain" == sirussofts.com ]] ||
  fail 'Live upgrade is restricted to the reviewed sirusweb/sirussofts.com installation'
HERE="$(cd "$(dirname "$0")" && pwd)"
TOP="$(cd "$HERE/../.." && pwd)"
SOURCE="$TOP/CMS"
ROOT="/home/$user/web/$domain"
DOCROOT="$ROOT/public_html"
CONF="/home/$user/conf/web/$domain"
PRIVATE="$ROOT/private/sirus-cms"
DB="$PRIVATE/data/cms.sqlite3"
GUARD="$HERE/sirus-cms-protect.nginx.conf"
managed="index.php api.php node-heartbeat.php node-work.php app assets bin .htaccess"
[[ -x /usr/local/hestia/bin/v-list-web-domain ]] || fail 'Hestia missing'
/usr/local/hestia/bin/v-list-web-domain "$user" "$domain" >/dev/null ||
  fail 'Expected Hestia primary site not present'
for dirname in "$ROOT" "$DOCROOT" "$CONF" "$PRIVATE" "$PRIVATE/data"; do
  [[ -d "$dirname" && ! -L "$dirname" ]] || fail "Unsafe or missing directory: $dirname"
done
[[ "$(stat -c %U "$DOCROOT")" == "$user" ]] ||
  fail 'Unexpected public_html owner'
[[ -f "$DB" && ! -L "$DB" ]] || fail 'Existing private CMS database missing or symlinked'
for name in $managed; do
  [[ -e "$SOURCE/$name" && ! -L "$SOURCE/$name" ]] ||
    fail "Missing or linked source CMS path: $name"
  if [[ -e "$DOCROOT/$name" || -L "$DOCROOT/$name" ]]; then
    [[ ! -L "$DOCROOT/$name" ]] || fail "Linked installed CMS path: $name"
  elif [[ "$name" != node-work.php ]]; then
    fail "Missing installed CMS path: $name"
  fi
done
for name in nginx.conf_siruscms nginx.ssl.conf_siruscms; do
  file="$CONF/$name"
  [[ -f "$file" && ! -L "$file" && "$(stat -c '%U:%G' "$file")" == root:root ]] ||
    fail "Missing or unsafe Hestia CMS protection: $file"
  cmp -s "$file" "$GUARD" || fail "Hestia CMS security guard has drifted: $file"
done
nginx -t >/dev/null || fail 'Nginx configuration invalid'
apache2ctl -t >/dev/null || fail 'Apache configuration invalid'
# Both protocol guards must actually be included by the generated Hestia vhosts.
dump="$(nginx -T 2>&1)" || fail 'Could not inspect Nginx includes'
for name in nginx.conf_siruscms nginx.ssl.conf_siruscms; do
  grep -Fq "# configuration file $CONF/$name:" <<< "$dump" ||
    fail "Hestia Nginx is not loading $name"
done
command -v sqlite3 >/dev/null || fail 'sqlite3 CLI required for WAL-consistent snapshot'
command -v php8.3 >/dev/null || fail 'PHP 8.3 CLI missing'
command -v curl >/dev/null || fail 'curl required for live HTTPS validation'
live_code="$(curl --noproxy '*' --silent --show-error --max-time 12 \
  --output /dev/null --write-out '%{http_code}' "https://$domain/" || true)"
if [[ "$live_code" != 200 ]]; then
  fail "Existing CMS homepage does not return HTTPS 200 (got $live_code). Diagnose the live site before another upgrade."
fi
for name in index.php api.php node-heartbeat.php node-work.php app assets bin .htaccess; do
  if [[ "$name" == *php ]]; then php8.3 -l "$SOURCE/$name" >/dev/null; fi
done
while IFS= read -r -d '' source_file; do
  php8.3 -l "$source_file" >/dev/null || fail "Invalid PHP syntax in $source_file"
done < <(find "$SOURCE/app" "$SOURCE/bin" -type f -name '*.php' -print0)
if [[ "$mode" == --plan ]]; then
  echo "PASS: Existing CMS, Hestia HTTP/HTTPS guards and private SQLite verified."
  echo "Public code source: $SOURCE"
  echo "Installed code root: $DOCROOT"
  echo "Database preserved: $DB"
  echo 'The apply operation creates a consistent SQLite backup and private code rollback copy.'
  echo 'Managed paths only: index.php api.php node-heartbeat.php node-work.php app assets bin .htaccess'
  echo 'Review source changes before --apply. No files were modified by --plan.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive PuTTY/console session required'
echo 'WARNING: This replaces the managed CMS code paths, not any content in SQLite.'
echo 'Existing custom changes inside managed code/assets directories will be replaced.'
read -r -p 'Type UPGRADE CMS to create a backup and replace website code: ' confirmation
[[ "$confirmation" == 'UPGRADE CMS' ]] || fail 'Upgrade declined'
RELEASES="$PRIVATE/releases"
[[ ! -L "$RELEASES" ]] || fail 'Unexpected releases symlink'
install -d -m 0700 -o root -g root "$RELEASES"
release="$(mktemp -d "$RELEASES/release-XXXXXXXX")"
chmod 0700 "$release"
install -d -m 0700 "$release/stage" "$release/previous"
echo "Private recovery directory: $release"
# SQLite .backup uses a consistent snapshot even when the site uses WAL mode.
# Open the LIVE WAL database as its website owner, NOT as root: a root
# SQLite connection could create root-owned -wal/-shm files alongside it.
tmp_snapshot="$(runuser -u "$user" -- mktemp "$PRIVATE/data/.cms-before-XXXXXXXX.sqlite3")" ||
  fail 'Could not create restricted SQLite staging snapshot'
runuser -u "$user" -- sqlite3 "$DB" ".backup '$tmp_snapshot'" ||
  fail 'Could not capture a consistent SQLite snapshot'
runuser -u "$user" -- sqlite3 "$tmp_snapshot" 'PRAGMA integrity_check;' | grep -qx ok ||
  fail 'SQLite snapshot integrity check failed'
mv -- "$tmp_snapshot" "$release/cms-before.sqlite3"
chown root:root "$release/cms-before.sqlite3"
chmod 0600 "$release/cms-before.sqlite3"
for name in $managed; do
  cp -a -- "$SOURCE/$name" "$release/stage/$name"
  chown -R -- "$user:$(id -gn "$user")" "$release/stage/$name"
done
swapping=1
rollback_on_exit(){
  status=$?
  if [[ "$status" -ne 0 && "$swapping" == 1 ]]; then
    echo 'ERROR during upgrade; restoring previously installed CMS code.' >&2
    for name in $managed; do
      if [[ -e "$release/previous/$name" ]]; then
        rm -rf -- "$DOCROOT/$name"
        mv -- "$release/previous/$name" "$DOCROOT/$name" ||
          echo "RECOVERY REQUIRED: $name could not be restored" >&2
      elif [[ "$name" == node-work.php ]]; then
        rm -f -- "$DOCROOT/node-work.php"
      fi
    done
    systemctl reload php8.3-fpm >/dev/null 2>&1 ||
      echo 'RECOVERY REQUIRED: PHP 8.3-FPM reload failed' >&2
    echo "Consistent pre-upgrade SQLite snapshot is at $release/cms-before.sqlite3" >&2
    echo 'Database was NOT automatically overwritten; review any migrations.' >&2
  fi
}
trap rollback_on_exit EXIT
for name in $managed; do
  if [[ -e "$DOCROOT/$name" ]]; then
    mv -- "$DOCROOT/$name" "$release/previous/$name"
  fi
  mv -- "$release/stage/$name" "$DOCROOT/$name"
done
runuser -u "$user" -- php8.3 "$DOCROOT/bin/migrate-schema.php" ||
  fail 'Schema migration failed; prior code will be restored'
nginx -t >/dev/null && apache2ctl -t >/dev/null ||
  fail 'Webserver configuration check failed; prior code will be restored'
# Clear PHP opcache following the on-disk code swap.
systemctl reload php8.3-fpm || fail 'PHP 8.3-FPM reload failed; prior code will be restored'
live_code=''
for retry in 1 2 3; do
  live_code="$(curl --noproxy '*' --silent --show-error --max-time 12 \
    --output /dev/null --write-out '%{http_code}' "https://$domain/" || true)"
  [[ "$live_code" == 200 ]] && break
  sleep 2
done
[[ "$live_code" == 200 ]] ||
  fail "Live CMS homepage returned HTTPS $live_code after the swap. Prior code will be restored."
swapping=0
echo 'PASS: CMS application files upgraded; existing database and administrator credentials retained.'
echo "Old code and consistent SQLite snapshot are stored privately: $release"
echo "Run: bash deploy/ubuntu24/07-audit-cms-routing.sh --live --user $user --domain $domain"
