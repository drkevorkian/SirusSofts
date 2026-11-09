# SirusSofts Hestia-owned app proxy. No Gentoo public IP exposure.
server {
    listen %ip%:%proxy_port%;
    include %home%/%user%/conf/web/%domain%/nginx.forcessl.conf*;
    server_name %domain_idn% %alias_idn%;
    error_log /var/log/%web_system%/domains/%domain%.error.log error;
    location ~ /\.(?!well-known/) { deny all; return 404; }
    location ^~ /.well-known/acme-challenge/ { root %docroot%; }
    location / { return 308 https://$host$request_uri; }
    include %home%/%user%/conf/web/%domain%/nginx.conf_*;
}
