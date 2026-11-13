# SirusSofts Update Center — phase 1 (preflight only)

Admin URL after the reviewed CMS deployment:
`https://sirussofts.com/admin/updates`

This is a **separate authenticated admin page**, not the public `/Gentoo/`
hardware dashboard. The existing PHP CSRF check, admin authorization and
audit log apply to every request. PHP only writes a fixed-kind job into the
private broker.

## Update checks

* Ubuntu: `ubuntu-primary`, dispatched to the local, unprivileged
  `ubuntu-worker`, executes `/usr/bin/apt-get -s upgrade` and returns
  bounded package preview.
* Gentoo: `gentoo-home`, dispatched through the **existing signed outbound
  HTTPS job polling** to `sirus-gentoo-node`, executes
  `emerge --pretend --update --deep --newuse @world` (read-only preview).
* No new SSH tunnel is needed. Existing reverse SSH is only for application
  web traffic. The CMS coordinator remains the source of job records.
* The Ubuntu worker now claims **only** `ubuntu-primary` jobs. It must
  never consume `gentoo-home` work; a Gentoo job must have a signed lease.
* Previews show cached repository/Portage metadata. They do **not** refresh
  package indexes, guarantee an upgrade transaction will succeed, or include
  security advisories automatically.

**All checks are previews.** The UI has no install/reboot button. Do not
interpret a reported package count as an applied update.

## Deployment

First merge/review the branch and run CI. The work broker, CMS, Gentoo
agent and Ubuntu worker must all be updated as a coordinated release.

Ubuntu:
1. Review the Git diff against the installed source and run the guarded
   `deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com`
   before using its reviewed `--apply` pathway.
2. Run `bash ubuntu-worker/install.sh --check` and then
   `bash ubuntu-worker/install.sh --plan`. With a verified private work broker,
   use interactive `bash ubuntu-worker/install.sh --install` to install or
   upgrade the restricted `sirus-ubuntu-worker` service. This makes a backup
   of any existing worker executable. The CMS site upgrade **does not**
   update this separate service.
3. Check `node-work.php`, the private work DB and worker service are
   installed. No arbitrary shell commands should be accepted.

Gentoo:
1. Review `gentoo-node/client.py`, back up
   `/opt/sirus-gentoo-node/client.py`, and install the reviewed file
   with the same root ownership/mode as the existing file.
2. Restart `sirus-gentoo-node` through OpenRC after updating the agent.
   Do **not** rotate the HMAC key or restart the SSH tunnel.
3. Test a preview from `/admin/updates` and verify only the intended node
   claims the job. The update center's Gentoo target is currently pinned to
   `gentoo-home`; broader node enrollment is a later phase.

## Required phase 2 before enabling Apply

A privileged **local** updater on each host, entirely separate from PHP:
strict allowlist, no user-provided commands/package names, per-host OS
checks, disk-space gate, snapshot/backup, package-manager lock, preflight
plan hash, two-step explicit authorization, execution timeout & restart
recovery, package log capture, post-upgrade health checks, rollback
instructions and *manual approval for reboots/kernel changes*. Neither
the web worker nor the existing HMAC agent receives root permission.

For package changes that could disconnect the Ubuntu public gateway,
preserve out-of-band SSH access and validate the live primary CMS before
and after the upgrade. Never automatically restart the SSH tunnel, Hestia
or networking services from a website request.
