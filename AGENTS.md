# QDPuzzleBot — Project Agent Instructions

Project-specific guidance for AI agents working in this repo. Global working-style
rules still apply (see your global `CLAUDE.md` / `AGENTS.md`); this file adds the
project specifics and pins the commands/seams. When the two conflict, the user's
explicit instructions win, then this file, then global defaults.

## What this is

A Discord puzzle-hunt manager: **Python / discord.py 2.7.1 + MySQL + Google Sheets**,
plus a **React/Mantine SPA over an embedded Quart API** (`bot/web/`, `frontend/`).
Core data model: `hunts → rounds → puzzles`, with the feeder→meta link expressed via
the many-to-many `tags(puzzle_id, round_id)` table.

## Roadmap & planning

- **`docs/PLANS.md` is the authoritative roadmap** (milestones M0–M11 + improvement
  catalogue). Re-verify its file:line refs against the code before acting — it warns
  they drift. Delivered so far: M0 (schema+migrations), M1 (test seam), M2
  (`aggregate_to_tab`), 7 quick wins. **M3** (`bot/core/` service extraction) is in
  progress; M4 is next on the critical path.
- Don't redo already-shipped hardening listed in PLANS.md Part 3 (Sheets retry/backoff,
  deferred renames, FK cascades, etc.).
- For substantial tasks, durable handoff state lives under `.ai/active/` (see global
  instructions); treat `.ai/active/current-spec.md` as authoritative when present.
- **Source plans / brainstorm outputs live outside the repo** under
  `~/.claude/plans/*.md` (e.g. the M7 source plan
  `can-we-plan-m7-sparkling-newell.md`). They are *background only* — the in-repo
  `docs/PLANS.md` and `.ai/active/` files win when they conflict.

## Running tests

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

- Use **`pipenv run python -m pytest`** — plain `python`/`pytest` aren't on PATH, and the
  bare `pytest` entrypoint doesn't put the repo root on `sys.path` (so `import bot` fails).
- Always wrap in `timeout` and write to a file (a killed run exits 124; read the log).
- `bot/store/__init__.py` opens MySQL **at import**, so tests need a reachable DB; a
  gitignored `config.json` (local `puzzlebot_test`) provides creds.

## Database & migrations

- **Rule: migration → deploy → code.** The generic `commit()` writes every dataclass
  field by name, so adding a column in code before the migration runs crashes existing
  writes (`mysqldb.py`).
- Tracked DDL + runner: `migrations/NNN_*.sql` applied by `migrate.py`
  (`migrate` / `status` / `baseline`). See the README "Database setup" section.
- Don't change schema, enum backing values, or migration history unless the task requires it.

## Architecture notes

- **`bot/core/`** is the ctx-free service layer (introduced in M3). New business logic
  belongs there, called by both the Discord bang-commands and the web dashboard — not
  duplicated. The legacy `web_*` twins in `bot/cogs/puzzles.py` are being collapsed onto
  it; don't add new `web_*` twins.
- Shared role/status/priority constants live once in `bot/core/constants.py` — import
  them, don't redefine.
- `bot/cogs/puzzles.py` is a ~2,700-line god-cog and a contention magnet; keep edits
  focused and prefer moving logic into `bot/core/`.

## Deployment & infrastructure

- **`docs/dashboard.md` is the authoritative deploy/infra reference** for the SPA +
  embedded Quart API (deploy steps, OAuth setup, nginx reverse-proxy config, serving
  model, local dev). Read it before touching deployment.
- **Test-site topology:** nginx on the edge box `souwester` terminates TLS for the
  internal-only hostname `puzzweb.home.meldon.net` and reverse-proxies to the bot box
  `redhat.home.meldon.net`, which runs the bot + embedded Quart on `:8099` (one
  upstream, no CORS — Quart serves both `/api/*` and the built SPA). DB is
  `puzzlebot_test`.
- Quart starts from `setup_hook` only when `web.enabled` is true in `config.json`
  (gitignored; see `config.json.dist` for the `web` block).

## Worktrees

**Convention: reuse the one persistent worktree at `.claude/worktrees/working-1`** rather
than creating a new one per feature — it already has its pipenv venv, `config.json`,
`google_secrets.json`, and `frontend/node_modules`, so there's no setup. Start a feature
with `cd .claude/worktrees/working-1 && git switch -c feature/<name> main`; after merging
to `main`, resync with `git -C .claude/worktrees/working-1 switch -C working-1 main`.

Only create a fresh worktree for genuinely parallel work. Full setup gotchas and the
from-scratch steps are in **`docs/WORKTREES.md`** (branch from local `main` not stale
`origin`; per-worktree pipenv venv; copy the gitignored `config.json` + `google_secrets.json`;
run tests with `pipenv run python -m pytest`).
