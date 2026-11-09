# Gentoo HTTPS Web Gateway and Keepalive

**Roles:** Ubuntu (74.208.40.187) remains the public HestiaCP website,
TLS gateway and authoritative task database. Gentoo performs compute
work and hosts apps. The tunnel connects Gentoo OUTBOUND to Ubuntu;
Gentoo requires no public port and can have a changing private IP.

    Browser HTTPS 443 -> Ubuntu gentoo.sirussofts.com
     -> 127.0.0.1:18480 (Ubuntu)
     -> pinned persistent SSH reverse tunnel
     -> 127.0.0.1:18080 (Gentoo private dashboard)

    Browser HTTPS 443 -> Ubuntu worldcom.sirussofts.com (later)
     -> 127.0.0.1:18481 (Ubuntu)
     -> same pinned SSH connection
     -> 127.0.0.1:18081 (Gentoo native webserver)
     -> optional 127.0.0.1:19000 (future WorldCom backend)

The dashboard uses password-protected HTTP Basic authentication **over
the existing public HTTPS endpoint**. Credentials are stored only as a
salted PBKDF2 hash in private Gentoo settings. It shows CPU/RAM/disk
stats, GPU readings, an actual 24-hour local CPU history (private
SQLite samples every minute) and the last 40 previously assigned
tasks. Job history comes from a node-scoped HMAC-authenticated query
to Ubuntu. Other nodes' job history and secrets cannot be requested.

Existing compute service: sirus-gentoo-node (unchanged role).
New independent OpenRC services:
- sirus-gentoo-dashboard (localhost 18080).
- sirus-gentoo-tunnel (OpenSSH reverse port forwarding).
The tunnel uses SSH ServerAliveInterval=15 and CountMax=3, and reconnects
automatically after transport loss with bounded backoff. Both reversed
ports bind only to Ubuntu 127.0.0.1. The dedicated Ubuntu SSH user has
no interactive shell, password login or local forwarding.

## Installation (no Git required on Gentoo)

Using your signed-in GitHub browser, select repository drkevorkian/SirusSofts,
branch feature/cms-vhost-distributed-nodes, then Code -> Download ZIP.
Extract ZIP to Downloads, enter the extracted folder in Terminal and run:

    bash gentoo-node/install-web-gateway.sh --check
    sudo bash gentoo-node/install-web-gateway.sh --plan
    sudo bash gentoo-node/install-web-gateway.sh --install

Type INSTALL GENTOO GATEWAY at the prompt. Choose a unique private
dashboard password (at least 14 characters). The installer backs up
the existing worker program and updates it with a signed history
query. It does NOT rotate your existing node authentication key,
replace its config or change Gentoo graphics drivers. It starts the
private dashboard but does NOT start the SSH tunnel before enrollment.
It prints its NEW SSH PUBLIC key (never reveal the private SSH key).

## Ubuntu installation and key enrollment

Ubuntu already has /root/SirusSofts as a Git checkout. On Ubuntu:

    cd /root/SirusSofts
    git fetch origin feature/cms-vhost-distributed-nodes
    git checkout PINNED_TESTED_COMMIT
    bash deploy/ubuntu24/11-install-gentoo-gateway.sh --check
    bash deploy/ubuntu24/11-install-gentoo-gateway.sh --plan

Send Gentoo's PUBLIC tunnel key to Ubuntu securely, using existing
approved SSH access if available:

    # On Gentoo root, only the .pub file (not the private key):
    scp /etc/sirus-gentoo-node/tunnel_ed25519.pub root@74.208.40.187:/root/gentoo-web.pub

Alternatively, paste the one-line public key into that file in an
authenticated Ubuntu PuTTY session. Then Ubuntu root runs:

    bash deploy/ubuntu24/11-install-gentoo-gateway.sh --install --pubkey-file /root/gentoo-web.pub

Type CONFIGURE GENTOO GATEWAY. This adds an SSH key-only user
sirus-tunnel, validates remote-only forwarding to two fixed loopback
ports, installs four custom Hestia templates, checks sshd configuration
and reloads SSH. It does not create domains or touch the root CMS.
A backup of the old SSH config remains available.

**The Gentoo task history endpoint also requires a CMS code upgrade**.
After the tested Git revision is checked out on Ubuntu, use the existing
guarded deploy/ubuntu24/08-upgrade-cms-hestia.sh with --plan and
--apply, --user sirusweb --domain sirussofts.com. Type only
UPGRADE CMS in its interactive prompt. This preserves CMS SQLite,
themes, accounts, projects and cPoint. Run the established
07-audit-cms-routing.sh --live afterward.

For a first-run **bootstrap** error about an unknown ED25519 host key, see [SSH-HOST-KEY-RECOVERY.md](SSH-HOST-KEY-RECOVERY.md). The bootstrap login and the persistent tunnel have **separate** trust stores.

## Pin Ubuntu SSH host key and start the reverse tunnel

On the Ubuntu root terminal, obtain the actual server fingerprint:

    ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub

On Gentoo, supply that independently verified SHA256 fingerprint:

    sudo bash gentoo-node/install-web-gateway.sh --pin-host --fingerprint 'SHA256:FROM_UBUNTU'

This matches a scanned Ed25519 public host key against the fingerprint
you obtained directly on Ubuntu; no blind trust-on-first-use.
Once it reports PASS, on Gentoo:

    sudo bash gentoo-node/install-web-gateway.sh --enable

Ubuntu verification commands:

    curl -i --max-time 5 http://127.0.0.1:18480/healthz
    ss -lnt | grep -E '127.0.0.1:1848[01]'

Expect an HTTP 200 response with "OK" body, and both listener addresses
strictly bound to 127.0.0.1, not 0.0.0.0. SSH key/connection issues
must be diagnosed without disabling known_hosts verification.

## Hestia subdomain: DNS and Let's Encrypt

The DNS A record for gentoo.sirussofts.com must point at Ubuntu
74.208.40.187, NOT Gentoo. Check DNS propagation before requesting
TLS. Under Hestia user sirusweb, create that as a distinct web domain
in Hestia UI, with NO unneeded www aliases. Issue a valid SSL
certificate, then use the installed sirus-gentoo-dashboard proxy
template for that domain alone. The corresponding CLI operations are:

    /usr/local/hestia/bin/v-add-web-domain sirusweb gentoo.sirussofts.com
    /usr/local/hestia/bin/v-add-letsencrypt-domain sirusweb gentoo.sirussofts.com
    /usr/local/hestia/bin/v-change-web-domain-proxy-tpl sirusweb gentoo.sirussofts.com sirus-gentoo-dashboard
    nginx -t

Check whether a domain exists BEFORE attempting to create it, and
only enable the reverse proxy after the SSL certificate is valid.
Browser URL: https://gentoo.sirussofts.com/
Dashboard login username: admin; password selected during Gentoo
installation. An HTTP 401 challenge before login is expected.
Hestia custom templates survive generated-vhost rebuilds; do not
modify the existing primary-site vhost.

## Running WorldCom Native (future)

Gentoo's second tunnel forwards Ubuntu 127.0.0.1:18481 to
Gentoo 127.0.0.1:18081. The provided file
gentoo-node/nginx-worldcom.example.conf is a loopback-only native
Gentoo Nginx example. It routes the HTTP Host
worldcom.sirussofts.com to a hypothetical local app port 19000. The
actual WorldCom application code and backend port have NOT been
provisioned; select the real port when integrating it, and configure
its app worker count on the Gentoo machine (rather than on
Ubuntu's single-core VPS).

Create worldcom.sirussofts.com as a SECOND Hestia domain with a
DNS A record to Ubuntu and its own valid certificate. Apply the
sirus-gentoo-app template only to it. Other app subdomains can use the
same tunnel port through Host-based Gentoo Nginx virtual hosts.

None of these source commits remotely configures DNS, Hestia or the
physical Gentoo computer. The scripts install only when you execute
them on the respective hosts, and live connectivity must be verified.
