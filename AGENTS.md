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

## Domain vocabulary (canonical)

Use these terms; this is the agreed model. (The `rounds` *table* is not the same as a
"round" the *label* — see below.)

- **TagGroup** — a named, typed group of *puzzles*, identified by a tag; puzzles are
  *tagged into* it (it is **not** "a group of tags"). Stored as a row in the `rounds`
  table: `TagGroup` is the umbrella concept, `rounds` is just the table name.
- **Target puzzle** — every TagGroup is built around one target puzzle (`rounds.meta_id`).
  A **TagGroup is solved when its target puzzle is solved** — that derived status is the
  only thing that happens automatically; the target itself is an ordinary puzzle a human
  solves like any other. A TagGroup with **no target** is a view / holding-pen; its
  members are *unassigned* (they contribute to nothing).
- **Two edge types** — how a member contributes to its TagGroup's target (carried on
  `tags.edge_type`):
  - **`feeds`** — the member contributes *its own answer*; available only once that
    member is **solved**.
  - **`informs`** — the member contributes *other information*; can arrive **before /
    without** the member being solved.
  A target may take a mix of both. What the target's sheet shows — an answer list vs a
  dossier of facts — follows from which edge feeds it; it is not a separate mechanism.
- **meta vs capstone** — informal shorthand for *which edge dominates a TagGroup*: a
  **meta** is fed mostly by `feeds` (answers), a **capstone** mostly by `informs`
  (information). They are structurally identical — both are TagGroups with a real target
  puzzle that gets solved. They are **not** separate types, and a capstone is solved the
  same way a meta is.

These are **not** types — do not model them as kinds of TagGroup:

- **round / metapuzzle / runaround** — presentation labels and *scope* (one zone vs the
  whole hunt), not distinct structures. The only thing that differs by label today is the
  welcome message. A *runaround* is just a TagGroup whose membership is "everything" —
  either tag the answers in directly, or nest rounds under it and let the rollup walk the
  subtree.
- **creation order** — *group-first* (make the group + target, tag members as they
  arrive) vs *feeders-first* (create members unassigned, tag them into a group later, via
  the Unassigned workflow). Same end state; only *when* tagging happens.
- **metameta** — a TagGroup whose members are themselves the targets of other groups; a
  rollup/nesting detail, not a kind.

Keep **feeder** reserved for *a feeding puzzle* ("the feeders feed the meta"); use the
verbs **feeds / informs** for the edges so the noun doesn't collide.

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

## Linting

```bash
pipenv run ruff check          # report
pipenv run ruff check --fix    # apply safe auto-fixes
```

- Config in `ruff.toml`, scoped to the pyflakes (`F`) rule set only — the high-signal
  correctness class (undefined names, unused imports/vars, malformed f-strings). Broader
  style rules (`E`/`W`/`UP`/…) are intentionally **not** enabled yet; widen later if wanted.
- **Do not blind-`--fix` `F401` (unused import) on `bot/` modules.** Several production
  modules import names that look unused locally but are load-bearing: package re-exports
  (`bot/web/auth.py` re-exports `ADMIN_ROLES`) and test patch-targets
  (`patch("bot.core.puzzle_service.GuildSettingsDb")`). Those lines carry `# noqa: F401`
  with a reason; `__init__.py` re-exports are ignored wholesale via `per-file-ignores`.
  Review F401 fixes on production code before applying.

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

**Convention: reuse a persistent worktree** (`.claude/worktrees/working-1`,
`.claude/worktrees/working-2`, etc.) rather than creating a new one per feature — each
already has its pipenv venv, `config.json`, `google_secrets.json`, and
`frontend/node_modules`, so there's no setup. Work directly on the worktree's own branch
(`working-1`, `working-2`, …); after merging to `main`, resync with
`git merge --ff-only main` from inside the worktree.

Only create a fresh worktree for genuinely parallel work. Full setup gotchas and the
from-scratch steps are in **`docs/WORKTREES.md`** (branch from local `main` not stale
`origin`; per-worktree pipenv venv; copy the gitignored `config.json` + `google_secrets.json`;
run tests with `pipenv run python -m pytest`).
