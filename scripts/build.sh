#!/usr/bin/env bash
# Build Glint.app (native shell) and the capture helper. Usage: scripts/build.sh [--open]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/build/Glint.app"
IDENTITY="${GLINT_SIGN_IDENTITY:-Glint Local Signing}"

[ -x "$ROOT/.venv/bin/python" ] || { echo "Missing $ROOT/.venv. Run scripts/setup.sh first."; exit 1; }

mkdir -p "$ROOT/bin" "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O "$ROOT/mac/capture/GlintCapture.swift" -o "$ROOT/bin/glint-capture"
swiftc -O "$ROOT"/mac/*.swift -o "$APP/Contents/MacOS/Glint" \
  -framework AppKit -framework WebKit -framework Carbon -framework AVFoundation -framework Speech \
  -framework ServiceManagement -framework UserNotifications

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Glint</string>
  <key>CFBundleDisplayName</key><string>Glint</string>
  <key>CFBundleIdentifier</key><string>dev.glint.Glint</string>
  <key>CFBundleExecutable</key><string>Glint</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Glint uses the microphone only while you hold Control+Option to dictate.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>Glint can transcribe dictation on this Mac when no OpenAI key is set.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>GlintProjectRoot</key><string>$ROOT</string>
</dict></plist>
PLIST

if security find-certificate -c "$IDENTITY" >/dev/null 2>&1 \
   && codesign --force -s "$IDENTITY" "$ROOT/bin/glint-capture" 2>/dev/null \
   && codesign --force --deep -s "$IDENTITY" "$APP" 2>/dev/null; then
  echo "signed with '$IDENTITY'"
else
  codesign --force -s - "$ROOT/bin/glint-capture" >/dev/null
  codesign --force --deep -s - "$APP" >/dev/null
  echo "ad-hoc signed. For permissions that survive rebuilds: run scripts/create-signing-identity.sh,"
  echo "then run this build once from Terminal and click 'Always Allow' when macOS asks about the key."
fi
echo "built $APP"
[ "${1:-}" = "--open" ] && open "$APP"
exit 0
