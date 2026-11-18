#!/usr/bin/env bash
# Regenerate docs/images from the fictional demo data: README screenshots (light + dark, 1440x900 @2x),
# the animated walkthrough (demo.gif + demo.webp) and the 1280x640 social preview. Idempotent.
#
#   scripts/screenshots.sh [--only home,graph] [--themes light] [--no-gif] [--no-social]
#
# Needs: .venv (scripts/setup.sh), Node 22+, Google Chrome or Chromium (or CHROME=/path), Pillow
# (installed into .venv if missing). Uses a temporary GLINT_HOME; never touches your Glint data.
# Shots are listed in scripts/screenshots.mjs (SHOTS); edit there after UI changes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
OUT="$ROOT/docs/images"
PORT="${GLINT_DEMO_PORT:-47408}"
DRIVER_ARGS=()
GIF=1
SOCIAL=1
while [ $# -gt 0 ]; do
  case "$1" in
    --only) DRIVER_ARGS+=(--only "$2"); shift 2 ;;
    --themes) DRIVER_ARGS+=(--themes "$2"); shift 2 ;;
    --no-gif) GIF=0; shift ;;
    --no-social) SOCIAL=0; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

[ -x "$PY" ] || { echo "Missing $ROOT/.venv. Run scripts/setup.sh first."; exit 1; }
command -v node >/dev/null || { echo "Node 22+ is required (brew install node)."; exit 1; }
node -e 'process.exit(typeof WebSocket === "function" ? 0 : 1)' || { echo "Node 22+ is required (global WebSocket)."; exit 1; }
"$PY" -c 'import PIL' 2>/dev/null || "$PY" -m pip install -q Pillow

port_free() { "$PY" -c 'import socket,sys; s=socket.socket(); s.bind(("127.0.0.1", int(sys.argv[1])))' "$1" 2>/dev/null; }
port_free "$PORT" || PORT="$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
MCP_PORT=$((PORT + 1))
port_free "$MCP_PORT" || MCP_PORT=""

# Render as if it were late afternoon (a full "today") by picking a UTC offset where it is ~17:00 now.
ZONE="${GLINT_SHOTS_TZ:-$("$PY" -c '
import time
g = time.gmtime(); off = round(17.3 - (g.tm_hour + g.tm_min / 60)); off = (off + 12) % 24 - 12
print("UTC" if off == 0 else f"Etc/GMT{-off:+d}")')}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/glint-shots.XXXXXX")"
DEMO_HOME="$WORK/home"
mkdir -p "$DEMO_HOME"
PID=""
cleanup() { [ -n "$PID" ] && kill "$PID" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT INT TERM
MODELS="$HOME/Library/Application Support/Glint/models"
[ -d "$MODELS" ] && ln -s "$MODELS" "$DEMO_HOME/models"

echo "Seeding demo data (TZ=$ZONE)…"
TZ="$ZONE" "$PY" "$ROOT/engine/demo.py" seed --home "$DEMO_HOME" >/dev/null
TZ="$ZONE" "$PY" "$ROOT/engine/demo.py" serve --home "$DEMO_HOME" --port "$PORT" --live \
  ${MCP_PORT:+--mcp-port "$MCP_PORT"} >"$WORK/engine.log" 2>&1 &
PID=$!
for _ in $(seq 1 100); do
  curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && break
  kill -0 "$PID" 2>/dev/null || { cat "$WORK/engine.log"; exit 1; }
  sleep 0.2
done
sleep 2  # let feature startup hooks (graph catch-up, MCP) settle

mkdir -p "$OUT"
[ "$GIF" = 1 ] && DRIVER_ARGS+=(--frames "$WORK/frames")
[ "$SOCIAL" = 1 ] && DRIVER_ARGS+=(--social)
echo "Capturing into docs/images…"
(cd "$ROOT" && TZ="$ZONE" node "$ROOT/scripts/screenshots.mjs" --url "http://127.0.0.1:$PORT/" --out "$OUT" \
  --tz "$ZONE" ${DRIVER_ARGS[@]+"${DRIVER_ARGS[@]}"})
if [ "$GIF" = 1 ]; then
  echo "Building the walkthrough…"
  "$PY" "$ROOT/scripts/make_gif.py" "$WORK/frames" "$OUT"
fi
echo "Done."
