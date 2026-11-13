# SirusSofts live WebSocket architecture (first integration)

Goal: **no document reloads to view changing infrastructure state**.
The existing SPA navigation in `CMS/assets/cms.js` remains in place.
The new `CMS/assets/live.js` keeps a single persistent WebSocket alive across
CMS page transitions, refreshes its short-lived proof after reconnection,
and re-renders the current page using browser DOM APIs.

## Data path

```text
Gentoo signed HTTPS jobs / Ubuntu restricted worker
                      |
               private SQLite (CMS)
                      |
          realtime/server.py (sirusweb)
             127.0.0.1:18482 only
                      |
    Hestia TLS proxy /admin/live-socket
                      |
          browser wss://sirussofts.com
                      |
      live jobs, host connectivity, history
```

The socket service reads the existing node registry and job broker in
**read-only SQLite mode**, bounded to 40 jobs and 32 nodes. Changes are
detected every 2 seconds and sent only when data has changed; this is a
WebSocket-delivered near-real-time snapshot, not a database push trigger.
No browser page refresh and no HTTP polling loop. Socket peers cannot
submit jobs, execute commands, write to SQLite, or access raw credentials.

Browser job forms use authenticated CSRF-protected POST requests and receive
a JSON job ID without navigation. Their results appear via the WebSocket.
The client also dispatches `sirus:live` (CustomEvent.detail contains the
snapshot) to allow other admin components to subscribe without refreshing.

### Security

- HTTPS/WSS only, exact expected `Origin` and path.
- CMS administrator session + CSRF POST is required to receive a fresh
  45-second HMAC bearer token.
- The socket expects that token as its **first frame**, never in a URL.
- Dedicated 32-byte shared key stays outside `public_html`, owned by
  `sirusweb`, mode 0600. Do not print it, copy it to Gentoo or git.
- Read-only daemon runs as `sirusweb` with systemd sandboxing.
- Connection closes after 10 minutes (client reconnects with a new token),
  after client close frames, or upon invalid post-auth messages.
- No external interfaces are exposed; Nginx proxies the local port.
- Only CMS administrators are eligible; public CMS and viewer sessions
  do not receive privileged live events.

## Install on the running Ubuntu VPS

1. Merge this branch into the same **deployment branch** used by Ubuntu:
   `feature/cms-vhost-distributed-nodes`. Confirm `git status --short`
   and `git pull --ff-only origin feature/cms-vhost-distributed-nodes`.
2. Run tests:
   `python3 -m unittest discover -s realtime/tests -p 'test_*.py' -v`,
   `bash realtime/install-ubuntu.sh --check`.
3. Use the existing guarded CMS upgrade:
   `bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com`.
   After reviewing local diffs, apply with `--apply`. This preserves CMS
   SQLite/admin credentials and creates the reviewed rollback snapshot.
4. `bash realtime/install-ubuntu.sh --plan` then interactive
   `bash realtime/install-ubuntu.sh --install`. The installer creates the
   HMAC key once, installs the systemd service and the Hestia HTTPS-only
   custom include, runs `nginx -t`, then reloads Nginx. It does **not**
   edit Hestia-generated vhost configs or the Gentoo SSH tunnel.
5. Verify `systemctl status sirus-live-socket --no-pager`,
   `nginx -t`, and visit `https://sirussofts.com/admin/updates`.
   Live updates should read **connected** after authentication. Submit an
   Ubuntu or Gentoo preview; watch queued → running → done/failed without
   refreshing. Check socket logs with
   `journalctl -u sirus-live-socket -n 60 --no-pager`.

The socket service requires an existing CMS private work DB. If its schema
or Hestia include differs from the expected deployment, stop and inspect
before installing. The installer does not install packages or grant any sudo.

## Private notification event journal

A trusted server-side producer can require `CMS/app/live_events.php` and
call `cms_live_publish('mail.received', ['unread' => 2])` or
`cms_live_publish('message.received', ['message_id' => '...'])`.
The publisher only accepts fixed topic names, caps JSON metadata to 2 KiB,
stores its bounded journal under the private CMS nodes directory, and does
not expose an HTTP publishing endpoint. The WebSocket reads the latest 30
records and the browser emits a `sirus:notification` event once per new ID.

No actual mailbox or chat service is wired to the publisher yet. The
administrator notification banner simply reports arrival metadata.
Use separate topic-specific authorization before exposing mail or message
events to ordinary user accounts; do not send message bodies here.

## Future event topics

The WebSocket transport is intended to carry site messages, mail indicators,
project deploy status, resource telemetry and alerts. **Those producers and
mail/message subscriptions are not implemented in this first PR.** Add
individual topic authorization and payload validation before broadcasting
those events. Avoid sending entire mailbox contents in notifications.

Current consumers: Update Center and infrastructure job status, plus node
online indicators. Other admin pages can listen to `sirus:live` without
invasive navigation.

## Known limits

- Two-second database change detection, not zero-latency event publication.
- A page loaded offline remains usable; reconnect backs off to 30 seconds.
- Snapshots intentionally show only the newest 40 jobs.
- Socket transport is restricted to administrators; separate access
  scopes must be designed before supporting public/user messages.
