#!/usr/bin/env bash
# Write Glint's Info.plist. Shared by scripts/build.sh (dev) and scripts/package.sh (release).
# Usage: scripts/info-plist.sh <out.plist> [project_root]
#   project_root: dev builds only — the checkout whose .venv/engine the app runs (GlintProjectRoot).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:?usage: info-plist.sh <out.plist> [project_root]}"
PROJECT_ROOT="${2:-}"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILD="${GLINT_BUILD_NUMBER:-1}"
MIN_MACOS="${GLINT_MIN_MACOS:-13.4}"

{
cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Glint</string>
  <key>CFBundleDisplayName</key><string>Glint</string>
  <key>CFBundleIdentifier</key><string>dev.glint.Glint</string>
  <key>CFBundleExecutable</key><string>Glint</string>
  <key>CFBundleIconFile</key><string>Glint</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHumanReadableCopyright</key><string>MIT License. https://github.com/Rikinshah787/Glint</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Glint uses the microphone only while you hold Control+Option to dictate.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>Glint can transcribe dictation on this Mac when no OpenAI key is set.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
PLIST
[ -n "$PROJECT_ROOT" ] && echo "  <key>GlintProjectRoot</key><string>$PROJECT_ROOT</string>"
echo "</dict></plist>"
} > "$OUT"
plutil -lint -s "$OUT"
