# Gentoo dashboard: WebSocket updates without page reloads

## What was wrong

The old `gentoo-node/dashboard.py` inserted
`<meta http-equiv="refresh" content="30">`. This caused a full browser
navigation every 30 seconds. The main CMS WebSocket could not stop it
because the dashboard is an **independent Python web server running on Gentoo**.

This change removes that refresh and gives the actual dashboard a private,
authenticated WebSocket feed:

- CPU, memory, disk and uptime: refreshed in place from the Gentoo metrics
  collector (about every 10 seconds).
- Running service and GPU readings: sent in the same metrics snapshots.
- Previous tasks and statuses: signed node-scoped Ubuntu history fetched by a
  single shared Gentoo collector (about every 4 seconds).
- CPU history graph: updated from local private samples when available.
- No browser document reloads, and no per-browser coordinator polling.
- Socket reconnects with exponential backoff after a disconnect.

The WebSocket requires HTTPS + an exact permitted Origin and a short-lived
signed token minted **only after the dashboard's existing HTTP Basic login**.
Tokens are sent in the first masked socket frame, not the URL. The server
accepts no remote commands, uses a bounded connection pool, and remains on
`127.0.0.1:18080`. No Gentoo public inbound ports are opened.

The existing reverse SSH forwards Ubuntu `127.0.0.1:18480` to this service.
No tunnel, SSH key, node HMAC key, credential, or Gentoo workload is altered.

## Install on Gentoo (local terminal, existing installation)

After the PR is merged into `feature/cms-vhost-distributed-nodes`, use the
**same reviewed branch** in your Gentoo checkout; do not run the first-time
web gateway installer just for this change. With the updated checkout:

```sh
cd /path/to/SirusSofts
bash gentoo-node/install-dashboard-live.sh --check
sudo bash gentoo-node/install-dashboard-live.sh --plan
sudo bash gentoo-node/install-dashboard-live.sh --install
```

The interactive phrase is `UPDATE GENTOO DASHBOARD`.

The installer backs up any previous dashboard files to
`/opt/sirus-gentoo-node/recovery/`, replaces ONLY `dashboard.py`,
`dashboard_live.py` and `dashboard_live.js`, then restarts ONLY the
`sirus-gentoo-dashboard` OpenRC service. It checks that the dashboard
health route is HTTP 200 and anonymous dashboard remains HTTP 401. If
a health check fails, it attempts to restore the old dashboard files.
It does not restart `sirus-gentoo-node` or `sirus-gentoo-tunnel`.

Verify:

```sh
rc-service sirus-gentoo-dashboard status
rc-service sirus-gentoo-tunnel status
curl -i --max-time 5 http://127.0.0.1:18080/healthz
```

## Install /Gentoo/ live socket proxy on Ubuntu (same reviewed checkout)

For the main-site path `https://sirussofts.com/Gentoo/`, Hestia needs a
specific WebSocket Upgrade location. The guarded include:
`deploy/ubuntu24/nginx.ssl.conf_sirusgentoolive` proxies ONLY the
`/Gentoo/live` endpoint through existing private loopback tunnel
`127.0.0.1:18480`. It leaves the existing `/Gentoo/` page route,
TLS certificates, site CMS and Gentoo SSH configuration untouched.

On Ubuntu root:

```sh
cd /root/SirusSofts
git pull --ff-only origin feature/cms-vhost-distributed-nodes
bash deploy/ubuntu24/13-install-gentoo-live-route.sh --check
bash deploy/ubuntu24/13-install-gentoo-live-route.sh --plan
bash deploy/ubuntu24/13-install-gentoo-live-route.sh --install
nginx -t
curl -i --max-time 5 http://127.0.0.1:18480/healthz
```

The interactive phrase is `ENABLE GENTOO LIVE SOCKET`.
The installer validates Nginx configuration before reload and removes
a newly added include if Nginx rejects it. It does not run the main
Hestia/CMS installer.

The separate `gentoo.sirussofts.com` domain's reviewed Hestia
`sirus-gentoo-dashboard.stpl` template already sends
`Upgrade`/`Connection` to the 18480 backend, so it needs no additional
proxy location.

## Browser verification

Open `https://sirussofts.com/Gentoo/` and authenticate as before.
The indicator should change from **Live: connecting** to **Live: connected**.
Leave the page open for longer than 30 seconds; the document must NOT reload.
Submit or complete a node task from the main CMS and watch its state change
in the Gentoo task table without refreshing. CPU/GPU values and CPU
history update in the same page.

If it says **Live: reconnecting**, inspect browser Network → WS for
`/Gentoo/live`, verify the restricted Nginx include is loaded, confirm
the Gentoo dashboard responds through the SSH tunnel, and inspect the
Gentoo OpenRC dashboard service logs. The token route `/Gentoo/live-token`
must return JSON for an authenticated browser, not a login page.

This is a socket-driven read-only dashboard, not a remote package installer.
