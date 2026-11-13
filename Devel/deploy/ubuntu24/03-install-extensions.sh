#!/usr/bin/env bash
# Curated PHP and Apache modules; intentionally never enable all available modules.
set -Eeuo pipefail
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
mode="--check"; if [[ $# -gt 0 ]]; then mode="$1"; fi
[[ $# -le 1 && ( "$mode" == --check || "$mode" == --install ) ]] ||
  fail 'Usage: 03-install-extensions.sh [--check|--install]'
if [[ "$mode" == --check ]]; then
  echo 'PASS extensions plan: PHP 8.3/8.4 PDO_SQLITE, MariaDB, sodium, GD, cURL, XML, mbstring, intl, zip, bcmath, opcache, soap, plus Apache proxy/rewrite/TLS modules.'
  echo 'No installations, Apache module changes or service restarts.'
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail 'Root required'
[[ -x /usr/local/hestia/bin/v-list-sys-config ]] || fail 'Install and verify Hestia first'
command -v apt-get >/dev/null || fail 'apt-get unavailable'
export DEBIAN_FRONTEND=noninteractive
required=(ca-certificates curl git jq unzip zip sqlite3 openssl imagemagick apache2-utils)
found=0
for version in 8.3 8.4; do
  if command -v "php$version" >/dev/null || [[ -d "/etc/php/$version/fpm" ]]; then
    found=1
    for package in cli fpm cgi common curl gd intl mbstring mysql sqlite3 xml zip bcmath soap; do
      required+=("php$version-$package")
    done
    for package in imagick redis apcu; do
      if apt-cache show "php$version-$package" >/dev/null 2>&1; then
        required+=("php$version-$package")
      fi
    done
  fi
done
[[ "$found" -eq 1 ]] || fail 'No requested PHP 8.3/8.4 runtime detected'
apt-get update
apt-get install -y --no-install-recommends "${required[@]}"
for module in rewrite headers expires deflate ssl proxy proxy_http proxy_fcgi setenvif remoteip reqtimeout; do
  a2enmod "$module"
done
apache2ctl configtest
nginx -t
for version in 8.3 8.4; do
  if command -v "php$version" >/dev/null; then
    "php$version" -r '
      foreach(["PDO","pdo_sqlite","pdo_mysql","sodium","openssl","curl","mbstring","xml","intl","gd","zip"] as $name) {
        if(!extension_loaded($name)) { fwrite(STDERR,"Missing PHP module: $name\n"); exit(1); }
      }
    '
    systemctl is-active --quiet "php$version-fpm" ||
      fail "PHP $version FPM did not start"
  fi
done
systemctl reload apache2
systemctl reload nginx
echo 'PASS curated PHP extensions and Apache modules. No project activated.'
