#!/usr/bin/env bash
# Dev build: build/Glint.app runs the engine from this checkout (.venv + engine/), plus the helpers in bin/.
# Usage: scripts/build.sh [--open]. For a self-contained, distributable app use scripts/package.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/build/Glint.app"
IDENTITY="${GLINT_SIGN_IDENTITY:-Glint Local Signing}"
TARGET="arm64-apple-macos${GLINT_MIN_MACOS:-13.4}"

[ -x "$ROOT/.venv/bin/python" ] || { echo "Missing $ROOT/.venv. Run scripts/setup.sh first."; exit 1; }

mkdir -p "$ROOT/bin" "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -target "$TARGET" "$ROOT/mac/capture/GlintCapture.swift" -o "$ROOT/bin/glint-capture"
swiftc -O -target arm64-apple-macos26.0 "$ROOT/mac/llm/GlintLLM.swift" -o "$ROOT/bin/glint-llm" -framework FoundationModels
swiftc -O -target "$TARGET" "$ROOT"/mac/*.swift -o "$APP/Contents/MacOS/Glint" \
  -framework AppKit -framework WebKit -framework Carbon -framework AVFoundation -framework Speech \
  -framework ServiceManagement -framework UserNotifications

"$ROOT/scripts/info-plist.sh" "$APP/Contents/Info.plist" "$ROOT"
[ -f "$APP/Contents/Resources/Glint.icns" ] && [ ! "$ROOT/mac/AppIcon.svg" -nt "$APP/Contents/Resources/Glint.icns" ] \
  || "$ROOT/scripts/make-icon.sh" "$APP/Contents/Resources/Glint.icns"

if security find-certificate -c "$IDENTITY" >/dev/null 2>&1 \
   && codesign --force -s "$IDENTITY" "$ROOT/bin/glint-capture" 2>/dev/null \
  && codesign --force -s "$IDENTITY" "$ROOT/bin/glint-llm" 2>/dev/null \
   && codesign --force --deep -s "$IDENTITY" "$APP" 2>/dev/null; then
  echo "signed with '$IDENTITY'"
else
  codesign --force -s - "$ROOT/bin/glint-capture" >/dev/null
  codesign --force -s - "$ROOT/bin/glint-llm" >/dev/null
  codesign --force --deep -s - "$APP" >/dev/null
  echo "ad-hoc signed. For permissions that survive rebuilds: run scripts/create-signing-identity.sh,"
  echo "then run this build once from Terminal and click 'Always Allow' when macOS asks about the key."
fi
echo "built $APP"
[ "${1:-}" = "--open" ] && open "$APP"
exit 0
