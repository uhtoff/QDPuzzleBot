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

## 3. Copy `config.json` into the worktree

`config.json` is **gitignored**, so it does not come across with the worktree. But
`bot/store/__init__.py` opens a MySQL connection **at import time** when
`config.storage == 'mysql'`, so without it every test fails to even collect
(`1045 Access denied for user ''@'localhost'`). Copy the working config from the repo
root:

```bash
cp /home/russ/projects/puzzbot/config.json .claude/worktrees/<name>/config.json
```

It points at the local test DB (`puzzlebot_test`). The copy stays gitignored.

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
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1   # expect all green
```

## Tearing down

```bash
git worktree remove .claude/worktrees/<name>      # add --force if it has uncommitted junk
git branch -d feature/<name>                       # once merged
```
