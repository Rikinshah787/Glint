#!/usr/bin/env bash
# Package dist/Glint.app (from scripts/package.sh) into dist/Glint-<version>-arm64.dmg, then verify it:
# mount, copy the app out, check its signature, and boot the bundled engine headless against /api/status.
#
#   scripts/make-dmg.sh                    build + verify
#   GLINT_SKIP_VERIFY=1 scripts/make-dmg.sh
#   GLINT_DEVELOPER_ID=... GLINT_NOTARY_PROFILE=... scripts/make-dmg.sh   also sign, notarize, staple the DMG
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
APP="$ROOT/dist/Glint.app"
DMG="$ROOT/dist/Glint-$VERSION-arm64.dmg"
VERIFY_PORT="${GLINT_VERIFY_PORT:-47410}"
[ -d "$APP" ] || { echo "Missing $APP. Run scripts/package.sh first."; exit 1; }

WORK="$(mktemp -d)"
MNT=""
ENGINE_PID=""
cleanup() {
  [ -n "$ENGINE_PID" ] && kill "$ENGINE_PID" 2>/dev/null || true
  [ -n "$MNT" ] && hdiutil detach -quiet "$MNT" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# ------------------------------------------------------------------ build
echo "==> Building $(basename "$DMG")"
STAGE="$WORK/stage"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Glint.app"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/READ ME FIRST.txt" <<TXT
Glint $VERSION - local memory and AI assistant for your Mac
https://github.com/Rikinshah787/Glint

INSTALL
  Drag Glint onto the Applications folder, then open it from Applications.

FIRST LAUNCH (builds that are not notarized)
  macOS may say Glint "cannot be opened" or "is damaged" because the download is not
  notarized by Apple. Either:
    - Control-click Glint in Applications, choose Open, then Open again; or
    - System Settings > Privacy & Security > scroll down > "Open Anyway"; or
    - in Terminal:  xattr -dr com.apple.quarantine /Applications/Glint.app

  Then grant what Glint asks for: Accessibility (to read the focused window),
  Screen Recording (optional, visual context) and Microphone (hold-to-dictate).

  The local embedding model (~90 MB) downloads on first use. All memory stays in
  ~/Library/Application Support/Glint on this Mac.

Requires an Apple silicon Mac with macOS 13.4 or later. On-device AI needs macOS 26
with Apple Intelligence enabled; otherwise add a Claude or OpenAI key in Settings.
TXT

rm -f "$DMG"
hdiutil create -quiet -volname "Glint $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDRW -ov "$WORK/rw.dmg"
# Volume icon (best effort: SetFile ships with the Xcode command line tools)
if command -v SetFile >/dev/null 2>&1 || xcrun -f SetFile >/dev/null 2>&1; then
  RW_MNT="$WORK/rw"
  mkdir -p "$RW_MNT"
  hdiutil attach -quiet -nobrowse -noautoopen -mountpoint "$RW_MNT" "$WORK/rw.dmg"
  cp "$APP/Contents/Resources/Glint.icns" "$RW_MNT/.VolumeIcon.icns"
  xcrun SetFile -a C "$RW_MNT" 2>/dev/null || SetFile -a C "$RW_MNT" 2>/dev/null || true
  hdiutil detach -quiet "$RW_MNT"
fi
hdiutil convert -quiet "$WORK/rw.dmg" -format ULMO -o "$DMG"

if [ -n "${GLINT_DEVELOPER_ID:-}" ]; then
  codesign --force --sign "$GLINT_DEVELOPER_ID" --timestamp "$DMG"
  if [ -n "${GLINT_NOTARY_PROFILE:-}" ]; then
    echo "==> Notarizing the DMG (profile: $GLINT_NOTARY_PROFILE)"
    xcrun notarytool submit "$DMG" --keychain-profile "$GLINT_NOTARY_PROFILE" \
    ${GLINT_NOTARY_KEYCHAIN:+--keychain "$GLINT_NOTARY_KEYCHAIN"} --wait
    xcrun stapler staple "$DMG"
  fi
fi
(cd "$(dirname "$DMG")" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
echo "$DMG  ($(du -h "$DMG" | awk '{print $1}'))"

[ "${GLINT_SKIP_VERIFY:-}" = "1" ] && exit 0

# ------------------------------------------------------------------ verify
echo "==> Verifying"
hdiutil verify -quiet "$DMG"
MNT="$WORK/mnt"
mkdir -p "$MNT"
hdiutil attach -quiet -nobrowse -noautoopen -readonly -mountpoint "$MNT" "$DMG"
[ -L "$MNT/Applications" ] || { echo "FAIL: Applications symlink missing"; exit 1; }
ditto "$MNT/Glint.app" "$WORK/Glint.app"
hdiutil detach -quiet "$MNT"; MNT=""
T="$WORK/Glint.app"
codesign --verify --deep --strict "$T" && echo "signature: ok"
plutil -extract CFBundleShortVersionString raw "$T/Contents/Info.plist" | grep -qx "$VERSION" && echo "version: $VERSION"
spctl --assess --type execute "$T" 2>/dev/null && echo "gatekeeper: accepted" \
  || echo "gatekeeper: not accepted (expected unless signed with Developer ID and notarized)"

# Boot the bundled engine headless with a throwaway data dir. A stub `security` keeps the check
# away from the login keychain (the engine looks up API keys there).
mkdir -p "$WORK/home" "$WORK/stub"
printf '#!/bin/sh\nexit 44\n' > "$WORK/stub/security"; chmod +x "$WORK/stub/security"
TOKEN="verify-$$"
env -i HOME="$HOME" PATH="$WORK/stub:/usr/bin:/bin:/usr/sbin:/sbin" \
  GLINT_HOME="$WORK/home" GLINT_NO_CAPTURE=1 GLINT_PORT="$VERIFY_PORT" GLINT_TOKEN="$TOKEN" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  "$T/Contents/Resources/python/bin/python3" -W ignore "$T/Contents/Resources/engine/engine.py" \
  > "$WORK/engine.out" 2> "$WORK/engine.err" &
ENGINE_PID=$!
STATUS=""
for _ in $(seq 1 120); do
  STATUS="$(curl -fsS -m 2 -H "X-Glint-Token: $TOKEN" "http://127.0.0.1:$VERIFY_PORT/api/status" 2>/dev/null || true)"
  [ -n "$STATUS" ] && break
  kill -0 "$ENGINE_PID" 2>/dev/null || break
  sleep 0.5
done
if [ -z "$STATUS" ] || ! printf '%s' "$STATUS" | grep -q '"memory"'; then
  echo "FAIL: engine did not answer /api/status"; tail -20 "$WORK/engine.err"; exit 1
fi
echo "engine: /api/status ok -> $(printf '%s' "$STATUS" | head -c 160)..."
curl -fsS -m 5 -o /dev/null -H "X-Glint-Token: $TOKEN" "http://127.0.0.1:$VERIFY_PORT/" && echo "engine: web UI served"
kill "$ENGINE_PID"; wait "$ENGINE_PID" 2>/dev/null || true; ENGINE_PID=""
if find "$T/Contents/Resources" -newer "$WORK/engine.out" -name '*.pyc' | grep -q .; then
  echo "FAIL: the engine wrote bytecode into the bundle"; exit 1
fi
codesign --verify --deep --strict "$T" && echo "signature after run: ok"
echo "==> DMG verified"
