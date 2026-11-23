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

No GUI/menu-bar application, process-kill or elevated helper, automatic RAM
reclamation, genuine research/PDF ingestion, CPU quota enforcement, precise
GPU telemetry, 90% total-system resource reservation, or closed-lid headless
operation. The 90% CPU setting is a policy target awaiting a scheduler. Nothing
in this package runs as root or starts until manually installed/configured.

## Install

On the Mac, review source and run:

    sh mac-node/install-macos.sh --plan
    sh mac-node/install-macos.sh --install

Install modern Python 3 if absent; the installer uses the first python3 on
PATH. In ~/.config/sirus-mac-node/config.json set key_file to the actual path
of ~/.config/sirus-mac-node/heartbeat.key. Enroll the binary 32-byte key
on the Ubuntu host under its existing private node key directory, named after
your configured node_id. Transfer only using an authenticated encrypted channel;
do not paste keys into a website/chat/GitHub.

The CMS must be upgraded from the reviewed matching Git commit before the Mac
reports: older CMS versions reject os=macos and init=launchd. After enrollment:

    launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.sirussofts.mac-node.plist"
    launchctl kickstart -k gui/$(id -u)/com.sirussofts.mac-node

Check ~/Library/Application Support/SirusMacNode/stderr.log or run
python3 mac-node/agent.py --config ~/.config/sirus-mac-node/config.json --probe
locally without contacting the network.

To stop background startup:
    sh mac-node/install-macos.sh --uninstall

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
