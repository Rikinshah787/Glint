#!/usr/bin/env bash
# Try Glint's UI on fictional demo data, in your browser. No permissions, no API key, and nothing is
# read from your Mac: the engine runs on a temporary folder with capture off and the Keychain unused.
#
#   scripts/demo.sh [--port 47408] [--keep] [--no-open]
#
# Ctrl-C stops it and deletes the temporary folder (unless --keep).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${GLINT_DEMO_PORT:-47408}"
KEEP=0
OPEN=1
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --no-open) OPEN=0; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done
[ -x "$PY" ] || { echo "Missing $ROOT/.venv. Run scripts/setup.sh first."; exit 1; }

port_free() { "$PY" -c 'import socket,sys; s=socket.socket(); s.bind(("127.0.0.1", int(sys.argv[1])))' "$1" 2>/dev/null; }
if ! port_free "$PORT"; then
  PORT="$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
fi
MCP_PORT=$((PORT + 1))
port_free "$MCP_PORT" || MCP_PORT=""

DEMO_HOME="$(mktemp -d "${TMPDIR:-/tmp}/glint-demo.XXXXXX")"
PID=""
cleanup() {
  [ -n "$PID" ] && kill "$PID" 2>/dev/null || true
  if [ "$KEEP" = 1 ]; then echo "Demo data kept in $DEMO_HOME"; else rm -rf "$DEMO_HOME"; fi
}
trap cleanup EXIT INT TERM

# Reuse the embedding model if Glint is installed (read-only); otherwise it is downloaded (~90 MB).
MODELS="$HOME/Library/Application Support/Glint/models"
[ -d "$MODELS" ] && ln -s "$MODELS" "$DEMO_HOME/models"

echo "Seeding a fictional week of work…"
"$PY" "$ROOT/engine/demo.py" seed --home "$DEMO_HOME" >/dev/null
"$PY" "$ROOT/engine/demo.py" serve --home "$DEMO_HOME" --port "$PORT" ${MCP_PORT:+--mcp-port "$MCP_PORT"} \
  >"$DEMO_HOME/engine.log" 2>&1 &
PID=$!
for _ in $(seq 1 100); do
  curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && break
  kill -0 "$PID" 2>/dev/null || { cat "$DEMO_HOME/engine.log"; exit 1; }
  sleep 0.2
done
URL="http://127.0.0.1:$PORT/"
echo
echo "  Glint demo: $URL"
echo "  Everything you see is fictional (Maya Chen, a PM at Northwind)."
echo "  Ask works if Apple Intelligence is on, or with ANTHROPIC_API_KEY / OPENAI_API_KEY set."
[ -n "$MCP_PORT" ] && echo "  MCP server: http://127.0.0.1:$MCP_PORT/mcp (create a client token in Settings)"
echo "  Ctrl-C to stop."
[ "$OPEN" = 1 ] && open "$URL"
wait "$PID"
