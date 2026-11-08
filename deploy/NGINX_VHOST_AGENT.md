# Restricted Nginx vhost staging agent

This supersedes the historical Plesk-only agent. It does not automatically
deploy projects or run until explicitly installed and enabled by an operator.
The agent takes ONLY action, slug, kind over a group-restricted AF_UNIX socket.
Allowed actions: preview, apply, rollback. No user-supplied commands, paths,
templates, nginx directives or arbitrary script execution. It uses only fixed
OpenSSL validation, nginx -t, and systemctl reload nginx commands.

Initial scope: **CMS content pages only**. Applications are deliberately
blocked until the directory/subdomain app runtime router is implemented and
tested. cPoint, WorldCom and Sentinel Trader are explicitly protected.

Before installation a root-reviewed wildcard certificate must contain BOTH
project.sirussofts.org and project.sirussofts.com; the service validates each
requested slug against /etc/sirussofts/tls/fullchain.pem with OpenSSL and
requires /etc/sirussofts/tls/privkey.pem. Do not store TLS keys in Git.
Both root domains must have working HTTPS separately.

Install the Python file root-owned at /opt/sirussofts/nginx_vhost_agent.py and
service unit as /etc/systemd/system/sirus-nginx-vhost.service after inspecting
the systemd sandbox and socket directory ownership. The service is NOT
integrated into the Ubuntu CMS installer yet and is not activated anywhere.

Generated server blocks are written only to /etc/nginx/sites-enabled/sirussofts-project-*.conf, which the stock Ubuntu Nginx http context includes. Private ownership metadata is stored in /etc/nginx/sirussofts-managed/managed.json. Never enable the service until the default nginx includes, sandbox paths, socket permissions, certificate chain and rollback behavior are reviewed on staging. A page
subdomain does not create or publish a CMS project; CMS publication is separate.
