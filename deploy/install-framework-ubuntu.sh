#!/usr/bin/env bash
# Reviewed manual installer: Ubuntu 24.04, nginx, PHP-FPM, SQLite.
# Does NOT run until an administrator explicitly executes it as root.
set -Eeuo pipefail
trap 'echo "ERROR: installer stopped at line $LINENO" >&2' ERR
fail(){ echo "ERROR: $*" >&2; exit 1; }
TOP="$(cd "$(dirname "$0")/.." && pwd)"
MODE="install"
if [[ $# -gt 0 ]]; then
  [[ $# -eq 1 && "$1" == "--check" ]] || fail "Usage: bash deploy/install-framework-ubuntu.sh [--check]"
  MODE="check"
fi
[[ -r /etc/os-release ]] || fail "Missing /etc/os-release"
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || fail "Ubuntu 24.04 LTS required"
[[ -f "$TOP/CMS/index.php" && -f "$TOP/CMS/app/core.php" ]] || fail "Missing CMS checkout"
[[ -f "$TOP/deploy/nginx-sirussofts.conf" && -f "$TOP/deploy/php-sirussofts.pool.conf" ]] || fail "Missing templates"
if [[ "$MODE" == "check" ]]; then
  echo "PASS: Ubuntu 24.04 installer source and OS preflight; no changes made"
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail "Run as root over PuTTY/SSH"
command -v apt-get >/dev/null || fail "apt-get required"
command -v git >/dev/null || fail "git required"
command -v systemctl >/dev/null || fail "systemctl required"
COMMIT="$(git -C "$TOP" rev-parse --verify HEAD)"
[[ "$COMMIT" =~ ^[a-f0-9]{40}$ ]] || fail "Cannot pin Git release SHA"
ROOT=/srv/sirussofts
RELEASE="$ROOT/releases/$COMMIT"
CURRENT="$ROOT/current"
PRIVATE=/var/lib/sirussofts/cms
NGINX=/etc/nginx/sites-available/sirussofts.conf
FPM=/etc/php/8.3/fpm/pool.d/sirussofts.conf
# Refuse unmanaged or customized configurations, never overwrite them.
if [[ -e "$NGINX" ]] && ! cmp -s "$NGINX" "$TOP/deploy/nginx-sirussofts.conf"; then
  fail "Existing nginx vhost differs: $NGINX"
fi
if [[ -e "$FPM" ]] && ! cmp -s "$FPM" "$TOP/deploy/php-sirussofts.pool.conf"; then
  fail "Existing PHP-FPM pool differs: $FPM"
fi
if [[ -e "$CURRENT" && ! -L "$CURRENT" ]]; then
  fail "Current release path is not a symlink"
fi
if [[ -L "$CURRENT" ]]; then
  current_target="$(readlink -f "$CURRENT")"
  case "$current_target" in "$ROOT/releases/"*) ;; *) fail "Unmanaged release symlink";; esac
fi
if [[ -e /etc/nginx/sites-enabled/sirussofts.conf &&
      ! -L /etc/nginx/sites-enabled/sirussofts.conf ]]; then
  fail "Unmanaged enabled nginx site"
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    nginx ca-certificates git python3 php8.3-cli php8.3-fpm \
    php8.3-sqlite3 php8.3-mysql php8.3-mbstring php8.3-xml \
    sqlite3 wireguard-tools certbot python3-certbot-nginx
if ! id sirusweb >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/sirussofts \
    --shell /usr/sbin/nologin sirusweb
fi
install -d -m 0755 -o root -g root "$ROOT" "$ROOT/releases"
install -d -m 0700 -o sirusweb -g sirusweb /var/lib/sirussofts "$PRIVATE" "$PRIVATE/data"
if [[ ! -d "$RELEASE" ]]; then
  stage="$(mktemp -d "$ROOT/releases/.stage.XXXXXXXX")"
  trap 'rm -rf -- "$stage"' EXIT
  cp -a "$TOP/CMS" "$stage/CMS"
  find "$stage/CMS" -name '*.php' -type f -print0 | xargs -0 -n1 php -l >/dev/null
  php "$stage/CMS/tests/storage_registry_test.php"
  php "$stage/CMS/tests/smoke.php"
  mv -- "$stage" "$RELEASE"
  trap - EXIT
fi
[[ -f "$RELEASE/CMS/index.php" ]] || fail "Incomplete existing release"
install -m 0644 -o root -g root "$TOP/deploy/php-sirussofts.pool.conf" "$FPM"
install -m 0644 -o root -g root "$TOP/deploy/nginx-sirussofts.conf" "$NGINX"
ln -sfn "$NGINX" /etc/nginx/sites-enabled/sirussofts.conf
php-fpm8.3 -t
nginx -t
if [[ ! -f "$PRIVATE/data/cms.sqlite3" ]]; then
  echo "Initialize CMS as sirusweb; enter admin credentials interactively:"
  runuser -u sirusweb -- env SIRUS_CMS_PRIVATE_ROOT="$PRIVATE" \
      php "$RELEASE/CMS/bin/setup.php"
  [[ -f "$PRIVATE/data/cms.sqlite3" ]] || fail "CMS initialization incomplete"
fi
# Upgrade the existing private SQLite schema before making a new CMS release active.
runuser -u sirusweb -- env SIRUS_CMS_PRIVATE_ROOT="$PRIVATE" \
    php "$RELEASE/CMS/bin/migrate-schema.php"
[[ ! -e "$ROOT/.current.new" && ! -L "$ROOT/.current.new" ]] || fail "Stale release staging link"
ln -s "$RELEASE" "$ROOT/.current.new"
mv -Tf "$ROOT/.current.new" "$CURRENT"
systemctl enable --now php8.3-fpm nginx
systemctl reload php8.3-fpm
systemctl reload nginx
echo "Installed SirusSofts CMS release $COMMIT, with private DB storage."
echo "HTTPS and DNS still need manual provisioning; none performed by this installer."
echo "When DNS is ready, review: certbot --nginx -d sirussofts.org -d www.sirussofts.org -d sirussofts.com -d www.sirussofts.com"
echo "No cPoint, WorldCom or Sentinel Trader application was deployed."
