#!/usr/bin/env bash
# Create the Python environment (.venv) that Glint's engine runs in. Needs Python 3.10+.
# Override the interpreter with GLINT_PYTHON=/path/to/python3.x scripts/setup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }
PY="${GLINT_PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null && ok "$c"; then PY="$(command -v "$c")"; break; fi
  done
fi
if [ -z "$PY" ] && command -v uv >/dev/null; then
  uv python install 3.12 >/dev/null && PY="$(uv python find 3.12)"
fi
[ -n "$PY" ] && ok "$PY" || { echo "Glint needs Python 3.10+ (try: brew install python@3.12, or install uv: https://docs.astral.sh/uv/)"; exit 1; }
echo "Using $("$PY" --version) at $PY"
"$PY" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -q --upgrade pip
"$ROOT/.venv/bin/pip" install -q -r "$ROOT/engine/requirements.txt"
echo "Python environment ready: $ROOT/.venv"
