# First-run SSH host-key failure (Gentoo bootstrap)

If `SirusSofts-Gentoo-Web-Bootstrap.sh` stops with:

```text
No ED25519 host key is known for 74.208.40.187
Host key verification failed.
```

that is a **safe refusal**, not proof the server is down. The bootstrap's
SSH client cannot yet authenticate Ubuntu. Do not use
`StrictHostKeyChecking=no` or blindly accept a scanned key.

1. In an **already trusted** Ubuntu VPS console / PuTTY session, run:

   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
   ```

   Verify that this is the VPS you control. Copy the `SHA256:...`
   fingerprint out of that trusted channel.

2. On Gentoo, run as **c0ldfyr3 (without sudo)** from a checkout of the
   same reviewed repository revision:

   ```bash
   bash gentoo-node/pin-bootstrap-ssh-host.sh --fingerprint 'SHA256:PASTE_ACTUAL_VERIFIED_VALUE'
   ```

   This fetches the public ED25519 host key, compares its SHA256
   fingerprint, and appends it to the current user's SSH known_hosts
   only on a match. If an entry already exists, it refuses to modify
   that entry automatically.

3. Rerun the *same already-reviewed* bootstrap script from Downloads.
   The repo revision check remains in effect. If SSH still refuses,
   inspect whether the bootstrap overrides `UserKnownHostsFile` or runs
   under a different account. Do **not** turn off verification.

## Important: two distinct SSH identities

The login user's `~/.ssh/known_hosts` is **not** the tunnel supervisor's
trusted host file. The persistent OpenRC `sirus-gentoo-tunnel` service runs
as `sirusnode` and reads:

```text
/etc/sirus-gentoo-node/tunnel_known_hosts
```

After Gentoo and Ubuntu gateway installation/enrollment, use the existing
installer to pin that second file (as root, with the same independently
verified fingerprint):

```bash
sudo bash gentoo-node/install-web-gateway.sh --pin-host --fingerprint 'SHA256:PASTE_ACTUAL_VERIFIED_VALUE'
sudo bash gentoo-node/install-web-gateway.sh --enable
```

Do **not** pin the tunnel host until its install prerequisites are complete;
the installer performs those checks. These instructions do not modify DNS,
Hestia, the CMS, or the existing cPoint installation.

## After SSH is trusted

If the bootstrap next reports permission denied, check the Ubuntu login
account and user authentication key rather than altering host-key policy.
If it reports a Git checkout mismatch, re-review and pin the intended
commit; don't skip the revision check.

Once the reverse tunnel is enrolled and running, on Ubuntu verify:

```bash
curl -i --max-time 5 http://127.0.0.1:18480/healthz
ss -lnt | grep -E '127.0.0.1:1848[01]'
```

Both forwarded ports must remain loopback-only.
