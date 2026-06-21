#!/bin/bash
# Local development: run the bot AND the Vite dev server (hot reload) together.
# Ctrl-C stops both. Vite serves the SPA on :5173 and proxies /api to the bot
# on :8099, so frontend edits hot-reload without rebuilding.
#
# Note: for OAuth to work in this mode, point the Discord redirect URI and the
# config oauth_redirect_uri at http://localhost:5173/api/auth/callback.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null; }
trap cleanup INT TERM EXIT

echo "[dev] Starting bot..."
pipenv run python run.py &
pids+=($!)

echo "[dev] Starting Vite dev server..."
( cd frontend && npm run dev ) &
pids+=($!)

wait
