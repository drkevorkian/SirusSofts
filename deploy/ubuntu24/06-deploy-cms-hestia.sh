#!/usr/bin/env bash
# One-time, explicit CMS launch into a clean Hestia-managed site.
# No project source, cPoint, external app routes or webhooks are deployed.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="--check"; if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
user=""; domain=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) [[ $# -ge 2 ]] || fail '--user needs a value'; user="$2"; shift 2;;
    --domain) [[ $# -ge 2 ]] || fail '--domain needs a value'; domain="$2"; shift 2;;
    *) fail "Unexpected option: $1";;
  esac
done
HERE="$(cd "$(dirname "$0")" && pwd)"
TOP="$(cd "$HERE/../.." && pwd)"
SOURCE="$TOP/CMS"
GUARD="$HERE/sirus-cms-protect.nginx.conf"
[[ -f "$SOURCE/index.php" && -f "$SOURCE/.htaccess" &&
   -f "$SOURCE/app/core.php" && -f "$SOURCE/bin/setup.php" && -f "$GUARD" ]] ||
  fail 'CMS checkout or hosting guard is incomplete'
[[ "$mode" == --check || "$mode" == --plan || "$mode" == --deploy ]] ||
  fail 'Usage: 06-deploy-cms-hestia.sh --check|--plan|--deploy --user HESTIA_USER --domain sirussofts.org'
if [[ "$mode" == --check ]]; then
  echo 'PASS CMS/Hestia deployment source check; no changes made.'
  exit 0
fi
[[ "$user" =~ ^[a-z][a-z0-9_-]{2,30}$ && "$user" != root &&
   "$user" != admin && "$user" != www-data ]] ||
  fail 'Use a dedicated Hestia website owner (for example sirusweb), not root/admin'
[[ "$domain" == 'sirussofts.org' ]] ||
  fail 'Deploy the single shared CMS to sirussofts.org only; attach .com as a Hestia alias'
[[ "$(id -u)" == 0 ]] || fail 'Root required to inspect Hestia config ownership'
H=/usr/local/hestia/bin
[[ -x "$H/v-list-web-domain" ]] || fail 'Hestia is not installed'
id "$user" >/dev/null 2>&1 || fail 'Hestia website account does not exist'
"$H/v-list-web-domain" "$user" "$domain" >/dev/null ||
  fail 'Create this Hestia website domain first'
SITEROOT="/home/$user/web/$domain"
DOCROOT="$SITEROOT/public_html"
CONF="/home/$user/conf/web/$domain"
SAFEFILE="$CONF/nginx.conf_siruscms"
[[ -d "$DOCROOT" && ! -L "$DOCROOT" &&
   -d "$CONF" && ! -L "$CONF" && -d "$SITEROOT" && ! -L "$SITEROOT" ]] ||
  fail 'Expected Hestia managed directories unavailable or symlinked'
[[ "$(stat -c %U "$DOCROOT")" == "$user" ]] ||
  fail 'Hestia document root is not owned by requested website user'
[[ ! -e "$SAFEFILE" && ! -L "$SAFEFILE" ]] ||
  fail 'An existing SirusSofts Nginx include requires manual review'
for name in index.php api.php node-heartbeat.php app assets bin .htaccess; do
  [[ ! -e "$DOCROOT/$name" && ! -L "$DOCROOT/$name" ]] ||
    fail "Document root already contains $name; refusing overwrite"
done
[[ ! -e "$SITEROOT/private/sirus-cms/data/cms.sqlite3" ]] ||
  fail 'Existing private CMS database: this is not a first-time installation'
if [[ -f "$DOCROOT/index.html" ]]; then
  grep -qiE 'hestia|default web page|congratulations' "$DOCROOT/index.html" ||
    fail 'Existing index.html was not recognized as a default Hestia placeholder'
fi
if [[ "$mode" == --plan ]]; then
  echo "PASS verified dedicated Hestia user, fresh CMS data and document root: $DOCROOT"
  echo "The root Nginx server will include: $SAFEFILE"
  echo 'Only the CMS homepage, assets, code and command-line setup are staged.'
  echo 'Private SQLite goes into the Hestia domain private/ directory, outside public_html.'
  echo 'Attach sirussofts.com and www aliases in Hestia separately; provision TLS after DNS.'
  echo 'No existing files, live server configuration or databases were changed.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive root console required for initial deployment'
read -r -p "Type DEPLOY CMS to initialize $domain under $user: " approved
[[ "$approved" == 'DEPLOY CMS' ]] || fail 'Operator declined'
# Place this guard BEFORE copying any internal PHP directory into the web root.
install -o root -g root -m 0644 "$GUARD" "$SAFEFILE"
if ! nginx -t || ! apache2ctl configtest; then
  rm -f -- "$SAFEFILE"
  fail 'Web server config rejected; removed the custom guard'
fi
nginx_dump="$(nginx -T 2>&1)" || fail 'Cannot inspect active Nginx configuration'
if ! grep -q 'SIRUSSOFTS_PRIVATE_GUARD' <<< "$nginx_dump"; then
  rm -f -- "$SAFEFILE"
  fail 'Hestia does not include nginx.conf_*; no CMS files were exposed'
fi
systemctl reload nginx
# Private domain parent is outside public_html; no separate broad system account.
PRIVATE="$SITEROOT/private"
[[ ! -L "$PRIVATE" && ! -L "$PRIVATE/sirus-cms" ]] ||
  fail 'Unexpected private path symlink'
install -d -m 0700 -o "$user" -g "$(id -gn "$user")" "$PRIVATE" "$PRIVATE/sirus-cms"
if [[ -e "$DOCROOT/index.html" ]]; then
  [[ ! -e "$PRIVATE/sirus-cms/original-index.html" ]] ||
    fail 'Existing homepage backup; manual review required'
  mv -- "$DOCROOT/index.html" "$PRIVATE/sirus-cms/original-index.html"
  chown "$user:$(id -gn "$user")" "$PRIVATE/sirus-cms/original-index.html"
  chmod 0600 "$PRIVATE/sirus-cms/original-index.html"
fi
for name in index.php api.php node-heartbeat.php app assets bin .htaccess; do
  cp -a -- "$SOURCE/$name" "$DOCROOT/$name"
  chown -R -- "$user:$(id -gn "$user")" "$DOCROOT/$name"
done
find "$DOCROOT/app" "$DOCROOT/bin" "$DOCROOT/assets" -type d -exec chmod 0755 {} +
find "$DOCROOT/app" "$DOCROOT/bin" "$DOCROOT/assets" -type f -exec chmod 0644 {} +
for f in index.php api.php node-heartbeat.php .htaccess; do chmod 0644 "$DOCROOT/$f"; done
php8.3 -l "$DOCROOT/index.php"
# Initialize credentials interactively as Hestia's unprivileged site user.
runuser -u "$user" -- php8.3 "$DOCROOT/bin/setup.php"
[[ -f "$PRIVATE/sirus-cms/data/cms.sqlite3" ]] ||
  fail 'CMS initialization incomplete; site code remains staged, inspect before retry'
runuser -u "$user" -- php8.3 "$DOCROOT/bin/migrate-schema.php"
echo 'PASS CMS files and private SQLite staged in a Hestia-managed website.'
echo 'Before routing public DNS: verify /app/core.php and /bin/setup.php return 404 through Nginx.'
echo 'Then add .org/.com aliases, obtain/renew site TLS in Hestia, and verify admin HTTPS.'
echo 'Do not run deploy/install-framework-ubuntu.sh or the old standalone vhost agent on Hestia.'
