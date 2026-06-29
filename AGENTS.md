# QDPuzzleBot — Project Agent Instructions

Project-specific guidance for AI agents working in this repo. Global working-style
rules still apply (see your global `CLAUDE.md` / `AGENTS.md`); this file adds the
project specifics and pins the commands/seams. When the two conflict, the user's
explicit instructions win, then this file, then global defaults.

## Integration branch

Integration branch: `staging` (not `main` — main is intentionally behind).

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
- **meta vs capstone** — *derived from the incoming edge, never declared.* A **meta** is
  a target puzzle that is **fed** (receives `feeds` edges — feeders' answers); a
  **capstone** is a target puzzle that is **informed** (receives `informs` edges —
  information). Same group machinery, solved the same way — so **informing a puzzle makes
  it a capstone**, with no separate kind and no precondition (strip the last `informs`
  edge and it stops being one). The asymmetry: `feeds` carries one answer, shared with
  every group it feeds, whereas `informs` carries **distinct information per target**, so
  one puzzle can inform several capstones differently.

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
  they drift. Delivered so far: M0–M10, M3.5, M7.5 (all done). **M9b (capstones/reveal)
  is next on the critical path.** M11 (feeder-as-thread) is deferred.
- Don't redo already-shipped hardening listed in PLANS.md Part 3 (Sheets retry/backoff,
  deferred renames, FK cascades, etc.).
- For substantial tasks, durable handoff state lives under `.ai/active/` (see global
  instructions); treat `.ai/active/current-spec.md` as authoritative when present.
- **Handoff model tiers** (used by the `handoff` skill's phase/model split): Phase 1
  (write all failing tests — test *design*) runs on **Opus 4.8**; Phase 2+ (mechanical
  implementation) runs on **Sonnet 4.6**. Use this split unless a handoff says otherwise.
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
  gitignored `config.json` (local `puzzlebot_test`) provides creds. The connection is
  needed at *import* time only — individual tests patch the DAOs (below) and don't run
  real queries.

### Test harness

The suite is fast and fully mocked — **no Discord, no live DB queries.** Pin these exact
shapes (config in `pytest.ini`); don't invent harness machinery.

- **Async:** `pytest.ini` sets `asyncio_mode = auto`, so `async def test_*` runs without a
  decorator. Await the service call directly; assert with `AsyncMock` methods
  (`assert_awaited_once()`, `assert_awaited_once_with(...)`, `assert_not_awaited()`).
- **Factories, not fixtures:** `tests/conftest.py` exports plain helper functions —
  `make_hunt()`, `make_round()` (a `TagGroupData`), `make_puzzle()`, `make_ctx()`,
  `make_cog()`. Import and call them; they are **not** pytest fixtures.
- **Mock Discord by hand:** `MagicMock()` for sync attributes, `AsyncMock()` for awaitables.
  A guild is `MagicMock()` with `.categories = [...]`; `category.clone`, `channel.edit`,
  `channel.create_thread`, `guild.create_text_channel` are all `AsyncMock()`. Set
  `.name`/`.position`/`.id` explicitly (a bare `MagicMock().name` is a Mock, not a string).
- **Patch DAOs at the *service* import site, not `bot.store`:** e.g.
  `patch("bot.core.puzzle_service.PuzzleJsonDb")`, `...TagGroupJsonDb`, `...HuntJsonDb`,
  `...RollupsJsonDb`, `...TagGroupCategoryDb` — because `puzzle_service` imports the names
  into its own namespace (`bot/store/__init__.py` re-exports them).
- **Simulate INSERT id assignment:** the real `commit()` sets `obj.id = cursor.lastrowid`;
  mirror it with `pdb.commit.side_effect = lambda o: setattr(o, "id", 1)`.
- **Adding a dataclass field** (e.g. `layout_mode`) needs only the field + a SQL migration;
  the generic `commit()` in `mysqldb.py` serialises every `__dict__` field by name — no
  DAO code change. (Per the migration→deploy→code rule below, ship the migration first.)
- Canonical end-to-end examples to mirror: `tests/test_create_service.py` (create/delete via
  `puzzle_service`) and `tests/test_category_layout.py` (mocked split/move/clone).

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

## Command reference & help text

- **`docs/commands.md` is the canonical command reference** — every chat command (in
  `bot/cogs/puzzles.py` + `bot/cogs/utility.py`) with its arguments, run context and
  permissions. Read it to find the right command or its bang/slash surface.
- Per-command **help text lives in the docstrings** in `bot/cogs/*.py` and is what
  `!help <command>` shows in Discord. House style: a one-line summary, then
  `Usage:` / `Example:` lines, then aliases/permissions. The `!` prefix is hard-coded in
  examples (discord.py's default help does **not** substitute a `{prefix}` placeholder).
- **Keep both in sync with the code.** When you add, remove, rename, or change a command's
  arguments or permissions, update (a) its docstring and (b) the row in `docs/commands.md`;
  for hybrid/slash commands keep the `description=` consistent with the summary (≤100 chars).

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
