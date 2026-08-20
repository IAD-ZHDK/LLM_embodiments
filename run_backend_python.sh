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

if [[ -z "${LLM_EMBODIMENT_PROFILE:-}" ]]; then
  case "$(uname -s)" in
    Darwin)
      export LLM_EMBODIMENT_PROFILE="mac"
      ;;
    Linux)
      if [[ -e "/dev/hailo0" || -e "/dev/hailo1" || -e "/dev/hailort0" || -e "/dev/hailort" ]]; then
        export LLM_EMBODIMENT_PROFILE="pi"
      else
        export LLM_EMBODIMENT_PROFILE="linux"
      fi
      ;;
  esac
fi

if [[ -n "${LLM_EMBODIMENT_PROFILE:-}" ]]; then
  echo "Using config profile: ${LLM_EMBODIMENT_PROFILE}"
fi

backend_pid=""

cleanup() {
  if [[ -n "$backend_pid" ]] && kill -0 "$backend_pid" 2>/dev/null; then
    kill -TERM "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
}

trap cleanup INT TERM HUP

python3 -m backend_python.server &
backend_pid="$!"
wait "$backend_pid"
exit_code="$?"

trap - INT TERM HUP
exit "$exit_code"
