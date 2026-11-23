#!/bin/sh
set -eu
[ "$(uname -s)" = Darwin ] || { echo "macOS required" >&2; exit 1; }
command -v xcrun >/dev/null 2>&1 || { echo "Install Xcode Command Line Tools (xcrun)." >&2; exit 1; }
SRC=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
DEST="$HOME/Applications/SirusMacNode.app/Contents"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
mkdir -p "$TMP/MacOS" "$TMP/module-cache"
COMPILER=$(xcrun --find swiftc) || exit 1
SDK=$(xcrun --sdk macosx --show-sdk-path) || exit 1
if ! "$COMPILER" -sdk "$SDK" -module-cache-path "$TMP/module-cache" -O "$SRC/MenuApp.swift" -o "$TMP/MacOS/SirusMacNode"; then
  echo "Swift compilation failed; no executable published. Check selected Command Line Tools:" >&2
  echo "  xcode-select -p ; xcrun --find swiftc ; xcrun --sdk macosx --show-sdk-path" >&2
  echo "Duplicate SwiftBridging modules indicate a toolchain/SDK conflict; do not edit system SDK files." >&2
  exit 1
fi
mkdir -p "$DEST/MacOS"
cp "$TMP/MacOS/SirusMacNode" "$DEST/MacOS/SirusMacNode"
cat > "$DEST/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>SirusMacNode</string>
<key>CFBundleIdentifier</key><string>com.sirussofts.mac-node.menu</string>
<key>CFBundleName</key><string>SirusMacNode</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSUIElement</key><true/>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
echo "Built $HOME/Applications/SirusMacNode.app"
echo "Open it using Finder or: open '$HOME/Applications/SirusMacNode.app'"
