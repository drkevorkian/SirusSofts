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

## Reliable in-place updates when WebSocket Upgrade is unavailable

The dashboard now has two read-only delivery modes that use the **same**
private Gentoo metrics and signed Ubuntu job-history collector:

- **Live: connected (WebSocket)**: preferred, persistent WebSocket.
- **Live: updating in place (HTTPS fallback)**: the browser receives a
  fresh authenticated JSON snapshot every five seconds. It updates CPU,
  RAM, disk, running services, GPU readings and task statuses without
  a document navigation or JavaScript reload. The browser periodically
  retries the WebSocket and switches back automatically when it works.

The fallback deliberately uses the already installed,
HTTP Basic-authenticated `/Gentoo/live-token?snapshot=1` endpoint so
neither Hestia nor the Ubuntu SSH tunnel requires a new public path,
certificate, proxy template or SSH restart. Without valid credentials
it returns HTTP 401. The response contains the same node-scoped data
already shown on the authenticated dashboard, not a control API.

The existing guarded `gentoo-node/install-dashboard-live.sh --install`
installs the updated dashboard Python and browser JavaScript files, and
restarts only `sirus-gentoo-dashboard`.

If both delivery modes fail, the dashboard reports an error in its
**Live** status rather than repeatedly refreshing the document. Do not
interpret a successful OpenRC restart as proof that public browser
delivery is working: verify the page actually changes its metrics.

## WebSocket handshake authentication

The browser now obtains the existing short-lived signed token using the
Basic-authenticated `/live-token` route, then offers two WebSocket
subprotocol values: `sirus-live` and `sirus-auth.<signed-token>`.
The Gentoo server verifies the token and the exact HTTPS Origin **before**
replying with `HTTP/1.1 101 Switching Protocols`, selecting the fixed
`sirus-live` protocol. No credential or signed token appears in the URL
and no arbitrary WebSocket commands are accepted. Invalid tokens get HTTP
403 rather than an ambiguous upgrade followed by an immediate disconnect.

This eliminates the fragile post-upgrade first-message authentication
path. A real Nginx integration test uses the existing production Hestia
live proxy location (with only the backend port changed to an ephemeral
test socket), then verifies authentication, HTTP 101, an immediate
`ready` frame, ongoing CPU/task snapshots, and 403 for invalid tokens.

The existing Ubuntu Nginx config automatically forwards the
`Sec-WebSocket-Protocol` header unless deliberately overridden, so
this change requires **only** updating the Gentoo dashboard with
`install-dashboard-live.sh`. Do not restart the SSH tunnel.

## Browser handshake compatibility

The Gentoo dashboard uses Python's standard `BaseHTTPRequestHandler`.
Without an explicit `protocol_version`, that class answers HTTP/1.1
requests with **HTTP/1.0**, including WebSocket upgrade requests. Chrome
can close such attempted upgrades immediately even when the status
contains `101`. The live dashboard now explicitly responds with
`HTTP/1.1 101 Switching Protocols`. Its authenticated ordinary HTTP
endpoints also have explicit `Content-Length`, allowing HTTP/1.1
keep-alive correctly. A socket-level regression test asserts the exact
status line and verifies the first `ready` and live snapshot frames,
rather than checking that a response contains `101` somewhere.

After upgrading the **Gentoo dashboard only**, the Ubuntu `/Gentoo/live`
proxy and the existing reverse SSH tunnel do not need reinstallation.
Verify in Chrome Network → WS that `/Gentoo/live` has status 101 and
receives `ready` and `snapshot` frames. If the connection still
terminates immediately, inspect the failing request's actual HTTP
status and response headers before changing any firewall or SSH setup.

## Install on Gentoo (local terminal, existing installation)

After tested changes are committed to `main`, use the
**same production branch** in your Gentoo checkout; do not run the first-time
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
`deploy/ubuntu24/nginx.ssl.conf_sirusgentoolive` proxies the three
fixed live-dashboard endpoints `/Gentoo/live`,
`/Gentoo/live-token`, and `/Gentoo/dashboard_live.js`
through the existing private loopback tunnel
`127.0.0.1:18480`. It leaves the existing `/Gentoo/` page route,
TLS certificates, site CMS and Gentoo SSH configuration untouched.

On Ubuntu root:

```sh
cd /root/SirusSofts
git pull --ff-only origin main
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
