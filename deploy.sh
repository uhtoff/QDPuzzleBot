#!/bin/bash
# Deploy step — run AFTER a git pull, e.g. from Plesk's Git
# "Additional deployment actions" box, or by hand on the host.
#
#   Plesk → Git → Additional deployment actions:
#       ./deploy.sh
#
# What it does, in order (privilege-free — no sudo needed for the defaults):
#   1. sync Python deps        (pipenv sync — installs exactly Pipfile.lock)
#   2. apply DB migrations      (python migrate.py)   ← golden rule: migrate BEFORE new code runs
#   3. restart the bot          (see RESTART below)
#
# The frontend SPA is rebuilt by start.sh as part of the restart, so we don't
# build it separately here.
#
# ─── RESTART ──────────────────────────────────────────────────────────────
# The default restart relaunches the bot as a DETACHED user process via
# `start.sh --restart` — no root, no systemd, works unattended from the Plesk
# hook. Detaching (setsid) matters: the hook must return, but start.sh execs
# the bot in the foreground, so a non-detached call would hang the deploy.
#
# If instead the bot runs under systemd, override the restart with RESTART_CMD
# (set it in the Plesk deployment environment, or export it before running):
#
#   Root-owned unit + passwordless sudo for just that command:
#     RESTART_CMD='sudo -n systemctl restart puzzbot'
#     # one-time, as root:  /etc/sudoers.d/puzzbot
#     #   <deploy-user> ALL=(root) NOPASSWD: /usr/bin/systemctl restart puzzbot
#
#   User-level unit (survives logout via `loginctl enable-linger <user>`):
#     RESTART_CMD='systemctl --user restart puzzbot'
#
# With RESTART_CMD set, systemd handles (re)building via the unit's
# ExecStart=.../start.sh, so behaviour is the same either way.
# ──────────────────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# Plesk hooks / systemd run with a minimal PATH that omits the dirs an
# interactive login shell adds, so pipenv isn't found even though it works by
# hand. Cover the common cases (pyenv shims, pip --user / pipx), then fall back
# to `python3 -m pipenv`. (Same bootstrap as start.sh.)
export PYENV_ROOT="${PYENV_ROOT:-${HOME:-/root}/.pyenv}"
export PATH="$PYENV_ROOT/shims:${HOME:-/root}/.local/bin:/usr/local/bin:$PATH"
if command -v pipenv >/dev/null 2>&1; then
  PIPENV="pipenv"
elif python3 -m pipenv --version >/dev/null 2>&1; then
  PIPENV="python3 -m pipenv"
else
  echo "[deploy] ERROR: pipenv not found on PATH ($PATH) and 'python3 -m pipenv' unavailable." >&2
  echo "[deploy] Install it (pip3 install --user pipenv) or set PATH for the deploy hook." >&2
  exit 127
fi

# `pipenv sync` installs EXACTLY what's pinned in Pipfile.lock — reproducible,
# and (unlike `install --deploy`) it does NOT re-verify the Pipfile-vs-lock
# hash. That hash check is pipenv-version-sensitive: a host whose pipenv differs
# from the one that generated the lock rejects an otherwise-valid lock. Re-lock
# discipline lives with whoever edits Pipfile: run `pipenv lock` and commit the
# updated Pipfile.lock.
echo "[deploy] Syncing Python deps from Pipfile.lock (pipenv sync)..."
if ! $PIPENV sync; then
  echo "[deploy] ERROR: pipenv sync failed. Is Pipfile.lock committed and valid?" >&2
  exit 1
fi

echo "[deploy] Applying database migrations (migrate.py)..."
if ! $PIPENV run python migrate.py; then
  echo "[deploy] ERROR: migrations failed — NOT restarting the bot." >&2
  echo "[deploy]        Fix the migration and re-deploy; the old bot keeps running." >&2
  exit 1
fi

if [ -n "${RESTART_CMD:-}" ]; then
  echo "[deploy] Restarting bot via RESTART_CMD: $RESTART_CMD"
  # shellcheck disable=SC2086
  exec $RESTART_CMD
fi

# Default: detached user-process restart. setsid + nohup + closed stdin so the
# bot outlives this script (and the Plesk hook), which then returns cleanly.
echo "[deploy] Restarting bot (detached user process via start.sh --restart)..."
setsid nohup ./start.sh --restart > bot.log 2>&1 < /dev/null &
disown 2>/dev/null || true
echo "[deploy] Restart launched in background; tail bot.log / discord.log for status."
