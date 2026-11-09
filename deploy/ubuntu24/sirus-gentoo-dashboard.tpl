# SirusSofts Hestia-owned dashboard proxy. No Gentoo public IP exposure.
server {
    listen %ip%:%proxy_port%;
    include %home%/%user%/conf/web/%domain%/nginx.forcessl.conf*;
    server_name %domain_idn% %alias_idn%;
    error_log /var/log/%web_system%/domains/%domain%.error.log error;
    location ~ /\.(?!well-known/) { deny all; return 404; }
    # Hestia's nginx.conf_letsencrypt include supplies ACME challenge location.
    # Do not add a ^~ location here: it would shadow Hestia's challenge response.
    location / { return 308 https://$host$request_uri; }
    include %home%/%user%/conf/web/%domain%/nginx.conf_*;
}
