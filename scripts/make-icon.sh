#!/usr/bin/env bash
# Render the app icon to an .icns. Usage: scripts/make-icon.sh <out.icns>
# Source: mac/AppIcon.svg when it exists, otherwise a built-in placeholder (rounded square + spark).
# Rasterises with AppKit (NSImage reads SVG natively), so it needs only the Xcode command line tools.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:?usage: make-icon.sh <out.icns>}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

SVG="$ROOT/mac/AppIcon.svg"
if [ ! -f "$SVG" ]; then
  echo "note: mac/AppIcon.svg not found; using the placeholder icon" >&2
  SVG="$WORK/placeholder.svg"
  cat > "$SVG" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3b8f78"/><stop offset="1" stop-color="#1f4d41"/>
    </linearGradient>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="185" fill="url(#bg)"/>
  <path d="M512 232 C538 420 604 486 792 512 C604 538 538 604 512 792 C486 604 420 538 232 512 C420 486 486 420 512 232 Z"
        fill="#ffffff"/>
  <circle cx="706" cy="318" r="34" fill="#bff0dd"/>
</svg>
SVG
fi

cat > "$WORK/render.swift" <<'SWIFT'
import AppKit
let args = CommandLine.arguments
guard let image = NSImage(contentsOf: URL(fileURLWithPath: args[1])) else {
    FileHandle.standardError.write("could not read \(args[1])\n".data(using: .utf8)!); exit(1)
}
let px = 1024
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                           bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
image.draw(in: NSRect(x: 0, y: 0, width: px, height: px))
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: args[2]))
SWIFT
swiftc -O "$WORK/render.swift" -o "$WORK/render" 2>/dev/null
"$WORK/render" "$SVG" "$WORK/icon_1024.png"

SET="$WORK/Glint.iconset"
mkdir -p "$SET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$WORK/icon_1024.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z $d $d "$WORK/icon_1024.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
mkdir -p "$(dirname "$OUT")"
iconutil -c icns "$SET" -o "$OUT"
echo "icon: $OUT"
