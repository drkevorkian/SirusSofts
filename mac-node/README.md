# SirusSofts Mac research node (Intel and Apple Silicon)

This is the first functional macOS node, compatible with Python 3.9+ on supported
macOS releases and intended for Macs from the 2017 generation through current
Intel/Apple Silicon models. Hardware feature detection is dynamic; no CPU or GPU
model is hardcoded. Actual legacy OCLP behavior is not guaranteed.

## Features implemented

- Signed TLS outbound node heartbeat and authenticated fixed-type work polling,
  matching existing SirusSofts HMAC coordinator behavior.
- Detailed administrator-only /mac dashboard, with links from Infrastructure.
- Intel/Apple Silicon CPU, RAM, GPU identity, disk, battery, and power telemetry;
  GPU utilization and temperature explicitly unknown unless measured.
- launchd user LaunchAgent, background execution without any GUI window; capped
  number of fixed queued jobs. --once and --probe diagnostics.
- Idle-sleep prevention through caffeinate -i; **does not defeat lid closure**.
- Fixed bounded SHA-256 worker self-test and hardware health snapshot.

## Not yet implemented

An optional menu-bar control app and local PDF text-extraction staging now
exist in source, but neither is confirmed on all hardware. Still missing:
privileged helper, automatic safe process reclamation, automatic transfer into
Gentoo's approved knowledge index, enforced CPU quota, precise GPU telemetry,
90% total-system resource reservation, and closed-lid headless operation.
The 90% CPU setting is a policy target awaiting a scheduler. Nothing in this
package runs as root or starts until explicitly configured.

## Complete installation from GitHub ZIP (no preexisting checkout)

These instructions are for Terminal on **the Mac**, not Ubuntu or Gentoo.
Review the downloaded source before installing. For a repeatable install,
pin to a reviewed commit rather than using a moving main branch.

### 1. Download and unpack the entire SirusSofts main branch

```sh
cd "$HOME/Downloads"
curl -fL --retry 3 \
  https://github.com/drkevorkian/SirusSofts/archive/refs/heads/main.zip \
  -o SirusSofts-main.zip
unzip -q SirusSofts-main.zip
cd SirusSofts-main
```

If `SirusSofts-main/` already exists, rename or remove the old extracted
folder **only after preserving any local modifications**, or extract to a
fresh download directory. Do not merge archives into an old checkout.

Alternative for Macs with Git installed:

```sh
cd "$HOME/Downloads"
git clone https://github.com/drkevorkian/SirusSofts.git
cd SirusSofts
```

### 2. Verify prerequisites and preview

```sh
python3 --version
sh mac-node/install-macos.sh --plan
```

Python 3.9+ is required. The menu app is optional and requires the Xcode
Command Line Tools (Swift compiler). The PDF stage is optional and requires
Poppler's `pdftotext`. No Docker or root-level agent is required.

### 3. Install the Mac worker into your account

```sh
sh mac-node/install-macos.sh --install
```

This copies `agent.py` and `research.py` to
`~/Library/Application Support/SirusMacNode/`, creates a user LaunchAgent
under `~/Library/LaunchAgents/`, and, on first installation, creates
`~/.config/sirus-mac-node/config.json` and a **private binary 32-byte key**
at `~/.config/sirus-mac-node/heartbeat.key`.
It deliberately does not start the service, ask for sudo, or change the
system sleep policy. A reinstall preserves an existing configuration and key.

### 4. Inspect the configuration and test hardware locally

```sh
cat "$HOME/.config/sirus-mac-node/config.json"
python3 mac-node/agent.py \
  --config "$HOME/.config/sirus-mac-node/config.json" --probe
```

Check that the `key_file` points to the generated file for **this user's**
home folder, and `node_id` is unique across all Mac nodes. Never print the
key or commit it to GitHub. The installer fills `key_file` automatically
for a new config; optional research-directory placeholders need adjustment
before enabling research.

### 5. Enroll this Mac node on the Ubuntu VPS (required for online reporting)

**Important:** First upgrade the Ubuntu CMS using the repository's reviewed
Hestia upgrade procedure so it recognizes `os=macos` / `init=launchd`.
A GitHub push or ZIP download does not update your live Ubuntu installation.
Do not run an unreviewed CMS upgrade on the production website.

Your Mac and Ubuntu need the same private HMAC key. The CMS expects the key
in its private node-key directory, named `<node_id>.key`, owned by `sirusweb`.
Using an authenticated SSH session and the server's actual trusted hostname,
one possible transfer from the **Mac** is:

```sh
scp "$HOME/.config/sirus-mac-node/heartbeat.key" \
  root@YOUR_UBUNTU_SSH_HOST:/root/mac-research-01.key
```

Then, on **Ubuntu**, after verifying the node ID and private key directory:

```sh
install -o sirusweb -g sirusweb -m 0600 \
  /root/mac-research-01.key \
  /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/keys/mac-research-01.key
wc -c /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/keys/mac-research-01.key
rm -f /root/mac-research-01.key
```

`wc -c` should report exactly **32 bytes**. Do not paste the key into chats,
logs, websites, or the repository. If root SSH is disabled, use your usual
authorized non-root SSH account and a separately approved privilege elevation
to move the key inside the Ubuntu host. Never enable root SSH solely for this.

### 6. Start the background service on the Mac

Only **after** the updated CMS is deployed, the key is enrolled, and the Mac
probe succeeds, run these commands on the **Mac**:

```sh
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.sirussofts.mac-node.plist"
launchctl kickstart -k "gui/$(id -u)/com.sirussofts.mac-node"
launchctl print "gui/$(id -u)/com.sirussofts.mac-node"
```

Then sign in as SirusSofts administrator and open:

- https://sirussofts.com/admin/infrastructure
- https://sirussofts.com/mac

The Mac should appear after a successful signed heartbeat. The CMS may show
stale/offline data until the heartbeat is accepted; do not interpret a
registered node alone as a live or working worker.

### 7. Optional native menu-bar application

With Apple's Swift compiler installed:

```sh
sh mac-node/build-menu-app.sh
open "$HOME/Applications/SirusMacNode.app"
```

The menu can start/stop the LaunchAgent, open its configuration file, and
open the web dashboard. Quitting the menu app does not terminate the background
worker. A Mac with OpenCore Legacy Patcher may require extra compatibility
validation.

### 8. Verify operation, recover, and uninstall

```sh
tail -n 60 "$HOME/Library/Application Support/SirusMacNode/stderr.log"
launchctl print "gui/$(id -u)/com.sirussofts.mac-node"
python3 -m unittest discover -s mac-node/tests -v
```

To stop and remove background autostart without deleting credentials:

```sh
sh mac-node/install-macos.sh --uninstall
```

Re-run `--plan` and `--install` from the newly downloaded source to upgrade.
The installer intentionally does **not** copy server code or start a root
process. Mac closed-lid sleep is not defeated by `caffeinate -i`.

## Operational constraints

launchd GUI-domain agents normally require an active login session; this is
not a pre-login LaunchDaemon. Never operate under a root-wide service account.
A closed lid ordinarily sleeps the Mac regardless of caffeinate; use supported
clamshell operation with appropriate hardware, and confirm wake/thermal
behavior before calling it always-on.

The Mac agent can run on newer Apple Silicon Macs but the legacy Intel Mac
has no guaranteed Metal inference support on Sonoma/OCLP. No GPU compute
assumptions are made. Only the existing known fixed work types are run.

## Optional native Mac menu-bar control

Build locally with `sh mac-node/build-menu-app.sh` (requires Apple Swift
compiler / Xcode Command Line Tools). Open `~/Applications/SirusMacNode.app`.
It controls launchd start/stop, opens the configured file, and links to /mac.
Closing the menu app does not stop the worker. Compilation on the 2017 OCLP
MacBook and newer Apple Silicon Macs still requires real-hardware validation.

## Local process inventory and approved application quit

`python3 mac-node/process_manager.py --list` reports a bounded per-process
RSS inventory; it is never uploaded to Ubuntu. A local interactive user may
request graceful application quit only after adding the exact bundle identifier
to config.json's `allowlisted_quit_bundle_ids` array. Example:

`python3 mac-node/process_manager.py --quit-bundle-id com.example.editor`

This prompts for literal 'yes' and uses macOS application quit, allowing a
save-changes prompt. There is no automatic killing and no privileged action.

## Local offline PDF research staging

Optional `research_enabled` with `research_source_dir` and
`research_output_dir` uses locally installed Poppler `pdftotext` to produce
SHA-256 deduplicated, unapproved JSON staging records. The Mac agent launches
a separate local subprocess at most every 10 minutes. Limits: 50 PDFs per
pass, 64 MiB per PDF, 2 MiB extracted text, 50 seconds per conversion.

The source directory and staging directory must be disjoint; symlinks to PDF
files are ignored. All contents remain local. No automatic upload, model
weight training, RAG insertion, or authorization bypass is performed.
Review and integrate staged records using Gentoo's approved ingestion pipeline
after a separate schema/provenance compatibility check.

## November 2026 Mac hardware validation findings

A 2017 OCLP MacBook Air running macOS 14.8.9 exposed a `/var` vs
`/private/var` path alias during a PDF research regression test.
Both paths are now canonicalized before overlap checks, with regression tests.
Its Swift Command Line Tools reported duplicate `SwiftBridging` module maps.
The builder uses the selected `xcrun` compiler, SDK and fresh module cache,
and does not publish an executable when compilation fails. If errors persist,
inspect the installed Xcode/Command Line Tools selection and compatibility;
do not delete Apple SDK headers or disable system security settings.
