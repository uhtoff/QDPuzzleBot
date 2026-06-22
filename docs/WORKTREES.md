# Spinning up a git worktree for this project

Isolated worktrees are handy for big changes (e.g. the M-milestone refactors). This
project has a few non-obvious gotchas — follow these steps or the worktree will look
broken (wrong base commit, missing deps, DB errors at import, tests "not found").

## 1. Branch from **local `main`**, not `origin`

`origin/main` is typically **far behind** local `main` (the local branch carries many
unpushed commits — schema, test seam, plans, etc.). Tooling that branches from
`origin/<default>` by default (e.g. `EnterWorktree` with its default `baseRef: fresh`)
will give you a worktree missing all of that.

Create the worktree explicitly from local `main` HEAD:

```bash
git worktree add .claude/worktrees/<name> -b feature/<name> main
```

(If using the `EnterWorktree` tool, create it manually as above first, then enter it by
`path` — don't rely on `name`/`fresh`, which branches from stale `origin`.)

Verify HEAD matches local `main`:

```bash
git -C .claude/worktrees/<name> log --oneline -1   # should equal `git log --oneline -1 main`
```

## 2. Each worktree gets its **own pipenv virtualenv** — install deps

`pipenv` keys the virtualenv on the directory name, so a new worktree starts with an
empty env (`pytest`/`pytest could not be found`). Install dev deps once per worktree:

```bash
cd .claude/worktrees/<name>
pipenv install --dev
```

## 3. Copy the gitignored secrets into the worktree

Two **gitignored** credential files do not come across with a worktree, and each
fails *silently in a different way* if missing:

- **`config.json`** — `bot/store/__init__.py` opens MySQL **at import time**, so
  without it even `pytest` collection fails (`1045 Access denied for user ''@'localhost'`).
- **`google_secrets.json`** — the Google service-account key. Without it the
  `GoogleSheets` cog **fails to load** at startup (`FileNotFoundError: google_secrets.json`),
  so `bot.get_cog("GoogleSheets")` is `None` and every meta-sheet rebuild silently
  no-ops — retag/solve still mutate the DB + Discord, so it *looks* fine but sheets
  never update.

Copy both from the repo root:

```bash
cp /home/russ/projects/puzzbot/config.json         .claude/worktrees/<name>/config.json
cp /home/russ/projects/puzzbot/google_secrets.json .claude/worktrees/<name>/google_secrets.json
```

`config.json` points at the local test DB (`puzzlebot_test`). Both copies stay gitignored.

## 4. Run tests with `pipenv run python -m pytest`

Plain `python` / `pytest` aren't on PATH, and the bare `pytest` entrypoint doesn't put
the repo root on `sys.path` (so `import bot` fails). Use the module form so the project
root is importable:

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

Read the log file for results (a timeout-killed run exits 124).

## Quick checklist

```bash
git worktree add .claude/worktrees/<name> -b feature/<name> main
cd .claude/worktrees/<name>
pipenv install --dev
cp /home/russ/projects/puzzbot/config.json ./config.json
cp /home/russ/projects/puzzbot/google_secrets.json ./google_secrets.json
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1   # expect all green
```

## Verifying the bot actually runs from a worktree

Tests prove logic; to confirm the **live bot** works from a worktree (all the bots
here are test bots), run it the normal way:

```bash
./start.sh            # builds the SPA, then `pipenv run python run.py`
```

Prerequisites are exactly the setup above — the worktree's **own** pipenv venv
(`pipenv install --dev`), the frontend deps (`start.sh` runs `npm ci` if
`frontend/node_modules` is absent), and the copied secrets (`config.json` +
`google_secrets.json`). The first run is
**noisy** (pipenv/npm bootstrap output, sometimes pip warnings) — that noise is
non-fatal. Success looks like `discord.log` showing:

```
discord.client: logging in using static token
bot.web: Starting web dashboard on 0.0.0.0:8099
discord.gateway: Shard ID 0 has connected to Gateway
```

`start.sh` guards against double-starts (it won't launch a second instance fighting
over the Discord token / port 8099); use `./start.sh --restart` to replace a running
one. Stop it with Ctrl-C (foreground) or by killing the `python run.py` PID.

## Tearing down

```bash
git worktree remove .claude/worktrees/<name>      # add --force if it has uncommitted junk
git branch -d feature/<name>                       # once merged
```
