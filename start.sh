#!/bin/bash
# Build the dashboard SPA, then start the bot (which serves the build).
#
# One-shot usage (foreground):   ./start.sh
# Background, like before:        nohup ./start.sh > bot.log 2>&1 &
# Restart (stop a running one):   ./start.sh --restart
#
# A failed frontend build is non-fatal: the bot still starts and serves the
# previous dist (if any), so a broken UI build never takes the bot down.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# Guard against double-starting: a second instance fights over the Discord token
# and the web port (8099), and is the usual cause of "can't kill the bot".
existing=$(pgrep -f "python run.py" || true)
if [ -n "$existing" ]; then
  if [ "${1:-}" = "--restart" ]; then
    echo "[start] Stopping existing bot (PIDs: $existing)..."
    kill $existing 2>/dev/null || true
    for _ in $(seq 1 10); do
      pgrep -f "python run.py" >/dev/null || break
      sleep 1
    done
    if pgrep -f "python run.py" >/dev/null; then
      echo "[start] Still running; forcing with SIGKILL..."
      pkill -9 -f "python run.py" || true
      sleep 1
    fi
  else
    echo "[start] A bot instance is already running (PIDs: $existing)."
    echo "[start] Use './start.sh --restart' to replace it, or kill it first."
    exit 1
  fi
fi

if [ -d frontend ]; then
  echo "[start] Building dashboard frontend..."
  (
    cd frontend
    [ -d node_modules ] || npm ci
    npm run build
  ) || echo "[start] WARNING: frontend build failed; serving previous dist (if any)."
fi

echo "[start] Starting bot..."
exec pipenv run python run.py
