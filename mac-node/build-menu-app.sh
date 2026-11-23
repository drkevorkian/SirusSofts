#!/bin/sh
set -eu
[ "$(uname -s)" = Darwin ] || { echo "macOS required" >&2; exit 1; }
command -v swiftc >/dev/null 2>&1 || { echo "Install Xcode Command Line Tools (swiftc)." >&2; exit 1; }
SRC=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
DEST="$HOME/Applications/SirusMacNode.app/Contents"
mkdir -p "$DEST/MacOS"
swiftc -O "$SRC/MenuApp.swift" -o "$DEST/MacOS/SirusMacNode"
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
