#!/bin/bash
# Build, sign, package, and notarize Featherweight into a distributable DMG.
#
# Requires a paid Apple Developer Program membership for signing and
# notarization. Without the env vars below it still produces an
# ad-hoc-signed DMG for local testing.
#
# Environment:
#   CODESIGN_IDENTITY  "Developer ID Application: Name (TEAMID)" — required
#                      for distribution; defaults to "-" (ad-hoc).
#   VERSION            Version string baked into the bundle and DMG name.
#   ASC_KEY_FILE       Path to an App Store Connect API key (.p8) file.
#   ASC_KEY_ID         The API key's Key ID.
#   ASC_ISSUER_ID      The API key's Issuer ID (UUID).
#
# Notarization runs only when CODESIGN_IDENTITY is real and all ASC_* vars
# are set.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Featherweight"
VERSION="${VERSION:-1.0.0}"
CODESIGN_IDENTITY="${CODESIGN_IDENTITY:--}"
DMG="$PROJECT_DIR/$APP_NAME-$VERSION.dmg"

# Build + sign the .app (package.sh reads the same env vars)
"$PROJECT_DIR/package.sh"

echo "=== Creating DMG ==="
rm -f "$DMG"
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGING_DIR"' EXIT
cp -R "$PROJECT_DIR/$APP_NAME.app" "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING_DIR" -ov -format UDZO "$DMG"

if [ "$CODESIGN_IDENTITY" != "-" ]; then
    echo "=== Signing DMG ==="
    codesign --force --sign "$CODESIGN_IDENTITY" --timestamp "$DMG"
fi

if [ "$CODESIGN_IDENTITY" != "-" ] && [ -n "${ASC_KEY_FILE:-}" ] && [ -n "${ASC_KEY_ID:-}" ] && [ -n "${ASC_ISSUER_ID:-}" ]; then
    echo "=== Notarizing (this can take a few minutes) ==="
    xcrun notarytool submit "$DMG" \
        --key "$ASC_KEY_FILE" \
        --key-id "$ASC_KEY_ID" \
        --issuer "$ASC_ISSUER_ID" \
        --wait
    echo "=== Stapling ticket ==="
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
else
    echo "Skipping notarization (set CODESIGN_IDENTITY, ASC_KEY_FILE, ASC_KEY_ID, ASC_ISSUER_ID to notarize)"
fi

echo ""
echo "DMG created at: $DMG"
