#!/usr/bin/env bash
# One-time development setup for CanopyLens on macOS/Linux. Safe to re-run.
#
#   ./scripts/setup.sh                    # backend + frontend
#   ./scripts/setup.sh --with-deep-learning   # also CPU torch + DeepForest (~2.5 GB)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

WITH_DL=0
for arg in "$@"; do
  case "$arg" in
    --with-deep-learning) WITH_DL=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "== CanopyLens setup =="

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi
VENV_PY=".venv/bin/python"

echo "Installing backend dependencies..."
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r backend/requirements-dev.txt

echo "Installing the canopylens_ml package (editable)..."
"$VENV_PY" -m pip install -e ./ml

if [ "$WITH_DL" -eq 1 ]; then
  echo "Installing CPU torch + DeepForest (large download)..."
  "$VENV_PY" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  "$VENV_PY" -m pip install -r backend/requirements-ml.txt
fi

echo "Installing frontend dependencies..."
(cd frontend && npm install)

[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example"; }
[ -f frontend/.env ] || { cp frontend/.env.example frontend/.env; echo "Created frontend/.env"; }

echo
echo "Setup complete. Start both servers with:"
echo "    ./scripts/dev.sh"
