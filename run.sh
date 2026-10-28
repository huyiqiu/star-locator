#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -n "${STAR16_PYTHON:-}" ]; then
  PYTHON_BIN="$STAR16_PYTHON"
elif [ -x .venv/bin/python ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="python3"
fi
exec "$PYTHON_BIN" -m uvicorn backend.app:app --host 127.0.0.1 --port "${STAR16_PORT:-8766}"
