#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if lsof -ti tcp:3000 >/dev/null 2>&1; then
  echo "Port 3000 is in use; stopping existing listener(s)..."
  lsof -ti tcp:3000 | xargs -r kill -9
fi

if [[ -f "python/venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "python/venv/bin/activate"
fi

exec python3 -m backend_python.server
