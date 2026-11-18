#!/usr/bin/env bash
# Build a self-contained dist/Glint.app (Apple silicon): native shell + helpers + engine + web UI +
# an embedded, relocatable CPython (python-build-standalone) with the engine's dependencies installed.
# Needs only Xcode command line tools and network access; no .venv, Homebrew or uv required.
#
#   scripts/package.sh                     ad-hoc signed (default)
#   GLINT_DEVELOPER_ID="Developer ID Application: Name (TEAMID)" scripts/package.sh
#                                          hardened runtime + secure timestamp, ready for notarization
#   ... GLINT_NOTARY_PROFILE=glint scripts/package.sh
#                                          also notarizes and staples the app (notarytool keychain profile)
#
# The embedding model (~90 MB) is not bundled: the engine downloads it on first use into
# ~/Library/Application Support/Glint/models.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DIST="$ROOT/dist"
APP="$DIST/Glint.app"
CACHE="${GLINT_CACHE_DIR:-$DIST/.cache}"
PY_VERSION="${GLINT_PY_VERSION:-3.12.16}"
PBS_TAG="${GLINT_PBS_TAG:-20261110}"
MIN_MACOS="${GLINT_MIN_MACOS:-13.4}"          # onnxruntime's dylibs need 13.4
WHEEL_PLATFORM="macosx_13_0_arm64"            # newest wheel tag pip may pick (keeps 13.x support)
IDENTITY="${GLINT_DEVELOPER_ID:-${GLINT_SIGN_IDENTITY:--}}"
export GLINT_MIN_MACOS="$MIN_MACOS"

step() { printf '\n==> %s\n' "$*"; }
[ "$(uname -m)" = "arm64" ] || { echo "package.sh builds an arm64 app and must run on Apple silicon."; exit 1; }

RES="$APP/Contents/Resources"
PY="$RES/python"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES/bin" "$CACHE"

# ------------------------------------------------------------------ native code
step "Compiling Glint $VERSION (minimum macOS $MIN_MACOS)"
swiftc -O -target "arm64-apple-macos$MIN_MACOS" "$ROOT"/mac/*.swift -o "$APP/Contents/MacOS/Glint" \
  -framework AppKit -framework WebKit -framework Carbon -framework AVFoundation -framework Speech \
  -framework ServiceManagement -framework UserNotifications
swiftc -O -target "arm64-apple-macos$MIN_MACOS" "$ROOT/mac/capture/GlintCapture.swift" -o "$RES/bin/glint-capture"
# Apple's on-device model helper needs the macOS 26 SDK; the engine falls back gracefully without it.
if swiftc -O -target arm64-apple-macos26.0 "$ROOT/mac/llm/GlintLLM.swift" -o "$RES/bin/glint-llm" \
     -framework FoundationModels 2>"$CACHE/glint-llm.log"; then
  echo "built glint-llm (FoundationModels)"
else
  echo "warning: glint-llm not built (needs Xcode 26 / macOS 26 SDK); on-device AI disabled in this build"
  sed 's/^/  /' "$CACHE/glint-llm.log" | head -5
fi

"$ROOT/scripts/info-plist.sh" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
"$ROOT/scripts/make-icon.sh" "$RES/Glint.icns"

# ------------------------------------------------------------------ engine + web UI
# Resources mirrors the checkout (engine/, web/, bin/ side by side) so the engine's
# REPO = engine/.. path constants work unchanged inside the bundle.
step "Copying engine and web UI"
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' --exclude 'tests/' --exclude '.DS_Store' \
  "$ROOT/engine/" "$RES/engine/"
rsync -a --delete --exclude '.DS_Store' "$ROOT/web/" "$RES/web/"
cp "$ROOT/LICENSE" "$RES/LICENSE"

# ------------------------------------------------------------------ embedded Python
PBS_FILE="cpython-${PY_VERSION}+${PBS_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}"
step "Embedding Python $PY_VERSION (python-build-standalone $PBS_TAG)"
if [ ! -f "$CACHE/$PBS_FILE" ]; then
  curl -fsSL --retry 3 -o "$CACHE/$PBS_FILE.part" "$PBS_URL/$PBS_FILE"
  mv "$CACHE/$PBS_FILE.part" "$CACHE/$PBS_FILE"
fi
[ -f "$CACHE/SHA256SUMS-$PBS_TAG" ] || curl -fsSL --retry 3 -o "$CACHE/SHA256SUMS-$PBS_TAG" "$PBS_URL/SHA256SUMS"
want="$(awk -v f="$PBS_FILE" '$2 == f {print $1}' "$CACHE/SHA256SUMS-$PBS_TAG")"
have="$(shasum -a 256 "$CACHE/$PBS_FILE" | awk '{print $1}')"
[ -n "$want" ] && [ "$want" = "$have" ] || { echo "checksum mismatch for $PBS_FILE"; rm -f "$CACHE/$PBS_FILE"; exit 1; }
tar -xzf "$CACHE/$PBS_FILE" -C "$RES"          # -> Resources/python

PYBIN="$PY/bin/python3"
PYVER="$("$PYBIN" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
LIB="$PY/lib/python$PYVER"
SITE="$LIB/site-packages"
step "Installing engine dependencies"
# --platform pins wheel selection to macOS 13-compatible builds regardless of the build machine's OS.
"$PYBIN" -I -m pip install --quiet --disable-pip-version-check --no-compile --upgrade \
  --only-binary=:all: --platform "$WHEEL_PLATFORM" --python-version "$PYVER" --implementation cp \
  --target "$SITE" -r "$ROOT/engine/requirements.txt"

step "Pruning the runtime"
# Interpreter extras Glint never uses
( cd "$PY/bin" && ls | grep -vxE "python|python3|python$PYVER" | xargs rm -f )
rm -rf "$PY/include" "$PY/share" "$PY/lib/pkgconfig" "$PY"/lib/itcl* "$PY"/lib/tcl* "$PY"/lib/tk* \
  "$PY"/lib/thread* "$PY"/lib/libtcl* "$PY"/lib/libtk*
rm -rf "$LIB"/{idlelib,tkinter,turtledemo,ensurepip,lib2to3,pydoc_data,test,unittest/test,turtle.py} \
  "$LIB"/config-*-darwin "$LIB"/lib-dynload/_tkinter* "$LIB"/lib-dynload/_test* "$LIB"/lib-dynload/xx*
# Packaging tools and console scripts (their shebangs point at the build machine)
rm -rf "$SITE"/pip "$SITE"/pip-* "$SITE"/bin
# onnxruntime's model-conversion tooling and its sympy dependency are not used for inference
rm -rf "$SITE"/sympy "$SITE"/sympy-* "$SITE"/mpmath "$SITE"/mpmath-* "$SITE"/isympy.py \
  "$SITE"/onnxruntime/{transformers,quantization,tools,datasets}
# Test suites, caches, type stubs
find "$PY" -type d \( -name tests -o -name test -o -name __pycache__ \) -prune -exec rm -rf {} +
find "$PY" -type f \( -name '*.pyc' -o -name '*.pyi' -o -name '*.c' -o -name '*.h' -o -name '*.pxd' \) -delete

step "Precompiling bytecode"
# unchecked-hash .pyc stay valid wherever the bundle is copied; the app sets PYTHONDONTWRITEBYTECODE
# so nothing is ever written into the signed bundle at runtime.
"$PYBIN" -I -m compileall -q -j 0 --invalidation-mode unchecked-hash "$LIB" "$RES/engine" >/dev/null

step "Checking the embedded runtime"
"$PYBIN" -I -c '
import anthropic, openai, fastembed, numpy, mcp, onnxruntime, sqlite3, ssl
print("python", __import__("sys").version.split()[0], "| onnxruntime", onnxruntime.__version__,
      "| numpy", numpy.__version__, "| sqlite", sqlite3.sqlite_version, "| ssl", ssl.OPENSSL_VERSION)'

# ------------------------------------------------------------------ signing
step "Signing ($([ "$IDENTITY" = "-" ] && echo ad-hoc || echo "$IDENTITY"))"
xattr -cr "$APP"
SIGN=(codesign --force --sign "$IDENTITY")
if [ "$IDENTITY" != "-" ] && [ -n "${GLINT_DEVELOPER_ID:-}" ]; then
  SIGN+=(--options runtime --timestamp)
fi
PY_ENT="$CACHE/python.entitlements"
cat > "$PY_ENT" <<'ENT'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
</dict></plist>
ENT
sign() { local out; out="$("${SIGN[@]}" "$@" 2>&1)" || { echo "$out"; return 1; }; }
# Inside-out: every Mach-O in Resources, then the interpreter, helpers, and finally the app.
while IFS= read -r -d '' f; do sign "$f"; done < <(find "$RES" -type f \( -name '*.so' -o -name '*.dylib' \) -print0)
sign --entitlements "$PY_ENT" "$PY/bin/python$PYVER"
for h in "$RES"/bin/*; do sign "$h"; done
sign --entitlements "$ROOT/mac/Glint.entitlements" "$APP"
codesign --verify --deep --strict "$APP"
echo "signature ok"

if [ -n "${GLINT_NOTARY_PROFILE:-}" ] && [ -n "${GLINT_DEVELOPER_ID:-}" ]; then
  step "Notarizing the app (profile: $GLINT_NOTARY_PROFILE)"
  ZIP="$DIST/Glint-notarize.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" --keychain-profile "$GLINT_NOTARY_PROFILE" \
    ${GLINT_NOTARY_KEYCHAIN:+--keychain "$GLINT_NOTARY_KEYCHAIN"} --wait
  xcrun stapler staple "$APP"
  rm -f "$ZIP"
fi

step "Done"
echo "$APP  ($(du -sh "$APP" | awk '{print $1}'))"
