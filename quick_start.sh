#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "AgentEngine Quick Start"
echo "================================================"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Python not found. Please install Python 3.11+."
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")
PY

if [ ! -f ".env" ]; then
  echo ".env not found, creating from .env.example..."
  cp .env.example .env
  echo "Created .env from template. Please edit LLM_API_KEY before running."
  exit 1
fi

mkdir -p logs data

echo "[1/3] Installing dependencies..."
if [ -f "uv.lock" ] && command -v uv >/dev/null 2>&1; then
  uv sync --extra dev
  PY_RUN="uv run --env-file .env python"
  UVICORN_RUN="uv run --env-file .env uvicorn"
else
  "$PYTHON_BIN" -m pip install -e ".[dev]"
  PY_RUN="$PYTHON_BIN"
  UVICORN_RUN="$PYTHON_BIN -m uvicorn"
fi

cleanup_port() {
  port="$1"
  pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
  elif command -v fuser >/dev/null 2>&1; then
    pids="$(fuser "${port}/tcp" 2>/dev/null || true)"
  fi

  if [ -n "$pids" ]; then
    for pid in $pids; do
      echo "  Killing PID ${pid} on port ${port}"
      kill "$pid" 2>/dev/null || true
    done
  fi
}

echo "[2/3] Cleaning up ports..."
cleanup_port 8000
cleanup_port 5173
sleep 1

echo "[3/3] Starting services..."
sh -c "$UVICORN_RUN app.backend.services.web_api:app --host 127.0.0.1 --port 8000 --log-level warning" \
  > logs/backend.log 2>&1 &
BACKEND_PID="$!"
echo "$BACKEND_PID" > logs/quick_start_backend.pid

FRONTEND_PID=""
if [ -f "app/frontend/package.json" ]; then
  if command -v npm >/dev/null 2>&1; then
    (
      cd app/frontend
      if [ ! -d "node_modules" ]; then
        npm install
      fi
      npm run dev
    ) > logs/frontend.log 2>&1 &
    FRONTEND_PID="$!"
    echo "$FRONTEND_PID" > logs/quick_start_frontend.pid
  else
    echo "npm not found; skipping frontend startup."
  fi
fi

echo
echo "================================================"
echo "  Backend:  http://127.0.0.1:8000  (pid ${BACKEND_PID})"
if [ -n "$FRONTEND_PID" ]; then
  echo "  Frontend: http://localhost:5173   (pid ${FRONTEND_PID})"
fi
echo "  Logs:     logs/backend.log and logs/frontend.log"
echo "  Stop:     kill \$(cat logs/quick_start_backend.pid) \$(cat logs/quick_start_frontend.pid 2>/dev/null)"
echo "================================================"
echo

echo "Running smoke test..."
sleep 3
if ! PYTHONIOENCODING=utf-8 sh -c "$PY_RUN scripts/chat_pretty.py general_chat hello"; then
  echo "Smoke test failed. Check .env and logs/backend.log."
fi
