#!/bin/sh
# Non-root per-user launchd service, no system mutation or automatic root grants.
set -eu
[ "$(uname -s)" = "Darwin" ] || { echo "macOS only" >&2; exit 1; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ACTION="${1:---plan}"
APP="$HOME/Library/Application Support/SirusMacNode"
CFG="$HOME/.config/sirus-mac-node"
PLIST="$HOME/Library/LaunchAgents/com.sirussofts.mac-node.plist"
PYTHON=$(command -v python3 || true)
[ -n "$PYTHON" ] || { echo "Python 3 required" >&2; exit 1; }
case "$ACTION" in
  --plan) echo "Would install non-root worker into: $APP"; echo "Would register user LaunchAgent: $PLIST"; echo "Would not grant root or bypass lid-close sleep"; exit 0 ;;
  --install|--uninstall) ;;
  *) echo "Usage: $0 --plan|--install|--uninstall" >&2; exit 2 ;;
esac
UID_NUM=$(id -u)
LABEL="com.sirussofts.mac-node"
if [ "$ACTION" = "--uninstall" ]; then
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "LaunchAgent removed. Credentials and data retained."
  exit 0
fi
mkdir -p "$APP" "$CFG" "$HOME/Library/LaunchAgents"
chmod 700 "$CFG"
cp "$ROOT/agent.py" "$APP/agent.py"
cp "$ROOT/research.py" "$APP/research.py"
chmod 600 "$APP/research.py"
chmod 600 "$APP/agent.py"
if [ ! -e "$CFG/config.json" ]; then
  cp "$ROOT/config.example.json" "$CFG/config.json"
  "$PYTHON" - "$CFG/config.json" "$CFG/heartbeat.key" <<'PY'
import json,sys
config,key=sys.argv[1:]
with open(config) as f: data=json.load(f)
data["key_file"]=key
with open(config,"w") as f: json.dump(data,f,indent=2)
PY
  chmod 600 "$CFG/config.json"
  echo "A default config was created. Enroll the key before starting."
fi
if [ ! -e "$CFG/heartbeat.key" ]; then
  /usr/bin/openssl rand 32 > "$CFG/heartbeat.key"
  chmod 600 "$CFG/heartbeat.key"
  echo "New private key created; enroll its binary bytes on Ubuntu before starting."
fi
# Use a trusted fixed plist template and replace only known paths.
"$PYTHON" - "$PLIST" "$PYTHON" "$APP/agent.py" "$CFG/config.json" "$APP" <<'PY'
import plistlib,sys
destination,python,agent,config,workdir=sys.argv[1:]
obj={"Label":"com.sirussofts.mac-node","ProgramArguments":[python,agent,"--config",config],
     "RunAtLoad":True,"KeepAlive":{"SuccessfulExit":False},"WorkingDirectory":workdir,
     "StandardOutPath":workdir+"/stdout.log","StandardErrorPath":workdir+"/stderr.log"}
with open(destination,"wb") as output: plistlib.dump(obj,output)
PY
chmod 600 "$PLIST"
echo "Installed. Confirm config.json key_file, enroll the key on Ubuntu, then run:"
echo "launchctl bootstrap gui/$UID_NUM '$PLIST'"
echo "launchctl kickstart -k gui/$UID_NUM/$LABEL"
