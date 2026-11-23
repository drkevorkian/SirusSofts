#!/bin/sh
# Cocoa Objective-C build avoids SwiftBridging duplicate modules in old CLT.
set -eu
[ "$(uname -s)" = Darwin ] || { echo "macOS required" >&2; exit 1; }
command -v xcrun >/dev/null 2>&1 || { echo "Xcode Command Line Tools required" >&2; exit 1; }
SRC=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
DEST="$HOME/Applications/SirusMacNode.app"
mkdir -p "$HOME/Applications"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
STAGE="$TMP/SirusMacNode.app"
mkdir -p "$STAGE/Contents/MacOS"
CC=$(xcrun --find clang)
SDK=$(xcrun --sdk macosx --show-sdk-path)
"$CC" -fobjc-arc -fno-modules -mmacosx-version-min=10.13 -isysroot "$SDK" \
    -framework Cocoa -O2 "$SRC/MenuApp.m" -o "$STAGE/Contents/MacOS/SirusMacNode"
cat > "$STAGE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>SirusMacNode</string>
<key>CFBundleIdentifier</key><string>com.sirussofts.mac-node.menu</string>
<key>CFBundleName</key><string>SirusMacNode</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSUIElement</key><true/>
</dict></plist>
PLIST
/usr/bin/plutil -lint "$STAGE/Contents/Info.plist" >/dev/null
[ -x "$STAGE/Contents/MacOS/SirusMacNode" ] || exit 1
if [ -e "$DEST" ]; then
    echo "Existing app at $DEST. Move it aside after inspection, then retry." >&2
    exit 1
fi
mv "$STAGE" "$DEST"
echo "Built $DEST"
echo "Launch: open \"$DEST\""
