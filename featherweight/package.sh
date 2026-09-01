#!/bin/bash
# Package Featherweight as a .app bundle.
#
# Environment:
#   CODESIGN_IDENTITY  Signing identity, e.g. "Developer ID Application: Name (TEAMID)".
#                      Defaults to "-" (ad-hoc) for local development builds.
#   VERSION            Bundle version string. Defaults to 1.0.0.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Featherweight"
VERSION="${VERSION:-1.0.0}"
CODESIGN_IDENTITY="${CODESIGN_IDENTITY:--}"
APP_DIR="$PROJECT_DIR/$APP_NAME.app"
CONTENTS="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

echo "=== Building release binary ==="
cd "$PROJECT_DIR"
swift build -c release 2>&1

RELEASE_BIN="$PROJECT_DIR/.build/release/Featherweight"
RESOURCE_BUNDLE="$PROJECT_DIR/.build/release/Featherweight_Featherweight.bundle"

echo "=== Creating .app bundle ==="
rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES"

# Copy binary
cp "$RELEASE_BIN" "$MACOS_DIR/$APP_NAME"

# Copy SwiftPM resource bundle (categories.json, app-leftovers.sh) so
# Bundle.module resolves inside the .app
cp -R "$RESOURCE_BUNDLE" "$RESOURCES/"
# Copy icon
cp "$PROJECT_DIR/AppIcon.icns" "$RESOURCES/"

# Create Info.plist
cat > "$CONTENTS/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Featherweight</string>
    <key>CFBundleIdentifier</key>
    <string>com.walt.featherweight</string>
    <key>CFBundleName</key>
    <string>Featherweight</string>
    <key>CFBundleDisplayName</key>
    <string>Featherweight</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
</dict>
</plist>
PLIST

# Create PkgInfo
echo "APPL????" > "$CONTENTS/PkgInfo"

echo "=== Signing (identity: $CODESIGN_IDENTITY) ==="
SIGN_ARGS=(--force --sign "$CODESIGN_IDENTITY")
if [ "$CODESIGN_IDENTITY" != "-" ]; then
    # Hardened runtime + secure timestamp are required for notarization
    SIGN_ARGS+=(--options runtime --timestamp)
fi
codesign "${SIGN_ARGS[@]}" "$APP_DIR" 2>&1

# Verify
echo "=== Verification ==="
codesign -dvvv "$APP_DIR" 2>&1
echo ""
echo "App size: $(du -sh "$APP_DIR" | cut -f1)"
echo "Binary size: $(du -sh "$RELEASE_BIN" | cut -f1)"
echo ""
echo "App created at: $APP_DIR"
echo ""
echo "To launch: open '$APP_DIR'"
