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
#     # This script auto-sets XDG_RUNTIME_DIR so the --user restart reaches the
#     # user bus from the non-login Plesk hook (linger must be on — README § 6).
#
# With RESTART_CMD set, systemd handles (re)building via the unit's
# ExecStart=.../start.sh, so behaviour is the same either way.
# ──────────────────────────────────────────────────────────────────────────
set -uo pipefail

# ─── Environment repair (Plesk Git hook / non-login shell) ──────────────────
# Plesk runs this hook with a stripped, sometimes wrong environment:
#   • PATH may omit /usr/bin & /bin — so even readlink/dirname aren't found;
#   • HOME may be unset OR set to "/", so ~/.local/bin and pyenv resolve to
#     junk (symptom: `//.local/bin/pipenv: ...: bad interpreter`). Note HOME="/"
#     is not empty, so a `${HOME:-default}` fallback does NOT catch it.
# Repair both BEFORE running any external command (including the cd below).
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
if [ -z "${HOME:-}" ] || [ "$HOME" = "/" ]; then
  HOME="$(getent passwd "$(id -un)" | cut -d: -f6)"
  export HOME
fi

cd "$(dirname "$(readlink -f "$0")")"

# pipenv often lives on a pyenv shim or ~/.local/bin (pip --user) that a login
# shell adds but this hook doesn't — prepend them on top of the repaired PATH.
# (Same bootstrap as start.sh.) HOME is correct now, so these resolve right.
export PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
export PATH="$PYENV_ROOT/shims:$HOME/.local/bin:$PATH"

# A `systemctl --user` RESTART_CMD needs XDG_RUNTIME_DIR to find the per-user bus,
# but a Plesk Git hook / non-login shell (su -c) doesn't set it — the restart then
# dies with "Failed to connect to bus: No such file or directory" even though the
# unit is healthy. Default it to the standard per-user path when unset. (Linger
# must be enabled so /run/user/<uid> exists — see README § "user systemd service".)
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Test that pipenv actually RUNS (not just `command -v`): a pip --user pipenv
# with a stale pyenv shebang exists on PATH but dies with "bad interpreter", so
# existence alone isn't enough — fall through to `python3 -m pipenv` if so.
if pipenv --version >/dev/null 2>&1; then
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
