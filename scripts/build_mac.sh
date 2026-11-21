#!/bin/bash
# Build the macOS app: dist/Clip Dashboard.app and dist/Clip-Dashboard-<version>-mac-<arch>.zip
# Usage (from anywhere):  bash scripts/build_mac.sh
# Optional signing and notarisation (see packaging/README.md):
#   CLIPDASH_SIGN_ID="Developer ID Application: Your Name (TEAMID)"   sign with hardened runtime
#   CLIPDASH_NOTARY_PROFILE=clipdash-notary                           notarise + staple (xcrun notarytool store-credentials first)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || { echo "No Python at $PY. Run install.command first or set PYTHON=/path/to/python"; exit 1; }
"$PY" -c "import PyInstaller" 2>/dev/null || { echo "PyInstaller missing: $PY -m pip install -r requirements-dev.txt"; exit 1; }

VERSION=$("$PY" -c "import clipdash; print(clipdash.__version__)")
ARCH=$(uname -m)
APP="dist/Clip Dashboard.app"
echo "== Building Clip Dashboard $VERSION for macOS ($ARCH) =="
"$PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging/clipdash.spec

if [ -n "${CLIPDASH_SIGN_ID:-}" ]; then
  echo "== Signing with: $CLIPDASH_SIGN_ID =="
  # Sign nested binaries first (dylibs, .so, the ffmpeg executable), then the bundle itself.
  find "$APP/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm -u+x \) -print0 | while IFS= read -r -d '' f; do
    if file "$f" | grep -q "Mach-O"; then
      codesign --force --timestamp --options runtime --entitlements packaging/entitlements.plist --sign "$CLIPDASH_SIGN_ID" "$f"
    fi
  done
  codesign --force --timestamp --options runtime --entitlements packaging/entitlements.plist --sign "$CLIPDASH_SIGN_ID" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
fi

ZIP="dist/Clip-Dashboard-$VERSION-mac-$ARCH.zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

if [ -n "${CLIPDASH_SIGN_ID:-}" ] && [ -n "${CLIPDASH_NOTARY_PROFILE:-}" ]; then
  echo "== Notarising (this can take a few minutes) =="
  xcrun notarytool submit "$ZIP" --keychain-profile "$CLIPDASH_NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP"
  rm -f "$ZIP"; ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"     # re-zip so the download carries the ticket
  spctl --assess --type execute --verbose "$APP"
fi
echo "Done: $APP"
echo "      $ZIP"
