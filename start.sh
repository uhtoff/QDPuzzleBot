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
#
# Scope the search to the current user (-u). On a shared/split host (e.g. Plesk)
# another user may run their own bot — a different bot ID, token and port — and a
# system-wide match would wrongly block us from starting our own instance.
me=$(id -u)
existing=$(pgrep -u "$me" -f "python run.py" || true)
if [ -n "$existing" ]; then
  if [ "${1:-}" = "--restart" ]; then
    echo "[start] Stopping existing bot (PIDs: $existing)..."
    kill $existing 2>/dev/null || true
    for _ in $(seq 1 10); do
      pgrep -u "$me" -f "python run.py" >/dev/null || break
      sleep 1
    done
    if pgrep -u "$me" -f "python run.py" >/dev/null; then
      echo "[start] Still running; forcing with SIGKILL..."
      pkill -9 -u "$me" -f "python run.py" || true
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
# systemd runs with a minimal PATH that omits the dirs an interactive login shell
# adds, so pipenv isn't found even though it works by hand. Cover the common cases:
# a pyenv shim ($PYENV_ROOT/shims, where pipenv lives on this host) and a
# pip --user / pipx install (~/.local/bin). Then locate pipenv, falling back to
# `python3 -m pipenv` (works when pipenv is installed as a module).
export PYENV_ROOT="${PYENV_ROOT:-${HOME:-/root}/.pyenv}"
export PATH="$PYENV_ROOT/shims:${HOME:-/root}/.local/bin:/usr/local/bin:$PATH"
if command -v pipenv >/dev/null 2>&1; then
  exec pipenv run python run.py
elif python3 -m pipenv --version >/dev/null 2>&1; then
  exec python3 -m pipenv run python run.py
else
  echo "[start] ERROR: pipenv not found on PATH ($PATH) and 'python3 -m pipenv' unavailable." >&2
  echo "[start] Install it (pip3 install --user pipenv) or set PATH in the systemd unit." >&2
  exit 127
fi
