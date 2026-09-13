#!/usr/bin/env bash
# Runs the CanopyLens backend and frontend development servers together.
# Vite proxies /api to the backend, so open http://localhost:5173 only.
#
#   ./scripts/dev.sh                # both
#   ./scripts/dev.sh --backend-only
#   ./scripts/dev.sh --frontend-only
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RUN_BACKEND=1
RUN_FRONTEND=1
for arg in "$@"; do
  case "$arg" in
    --backend-only) RUN_FRONTEND=0 ;;
    --frontend-only) RUN_BACKEND=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

VENV_PY=".venv/bin/python"
if [ "$RUN_BACKEND" -eq 1 ] && [ ! -x "$VENV_PY" ]; then
  echo "No virtual environment found. Run ./scripts/setup.sh first." >&2
  exit 1
fi

pids=()
cleanup() {
  echo
  echo "Shutting down development servers..."
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ "$RUN_BACKEND" -eq 1 ]; then
  echo "Starting API on http://127.0.0.1:8000 ..."
  "$VENV_PY" -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000 &
  pids+=($!)
fi

if [ "$RUN_FRONTEND" -eq 1 ]; then
  echo "Starting web client on http://localhost:5173 ..."
  (cd frontend && npm run dev) &
  pids+=($!)
fi

echo
echo "Open http://localhost:5173 - press Ctrl+C to stop."
echo

# Exit as soon as either server dies, so a crash is not silently ignored.
wait -n "${pids[@]}"
