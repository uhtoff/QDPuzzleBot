# Current Spec — Milestone D: Sheet Generation

Tool: Claude Code (executor)
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2
Target model — Phase 1 (test authoring, per task): **Opus**
Target model — Phase 2+ (implementation, per task): **Sonnet**
Started: 2026-06-25
Status: in progress — Task 1 done; Phase-1 failing tests authored & committed (@ 850bd86);
        next = Sonnet implementation pass (see session-summary.md "Phase-1 tests authored")

> **This is a thin pointer package.** The authoritative, near-verbatim execution plan is
> already written and committed in this worktree. Do not re-derive it here.
>
> **AUTHORITATIVE PLAN (read first, execute task-by-task):**
> `docs/superpowers/plans/2026-06-25-milestone-d-sheet-generation.md`
> **DESIGN SPEC (read for the "why"):**
> `docs/superpowers/specs/2026-06-25-milestone-d-sheet-generation-design.md`
> **Parent design:** `docs/superpowers/specs/2026-06-24-taggroup-edges-and-commands-design.md`
>
> The plan has 12 tasks, each already structured test → implementation → commit with
> near-verbatim test skeletons, real method bodies, exact acceptance commands, and per-task
> commit messages. Execute it with **`superpowers:subagent-driven-development`** (the plan's
> own stated sub-skill) or `superpowers:executing-plans`. This file adds only the executor
> framing the plan assumes you already have.

## Before you start (executing model)

- Execute **one task at a time, in order** (Task 1 → Task 12). Each task is its own commit.
- **Model split (per task):** author the failing test(s) on **Opus** — test *design* is where
  weaker models invent harness machinery, guess columns/payload keys, or dig deeper instead of
  stopping. Do the mechanical implementation steps on **Sonnet**. (The plan interleaves test and
  impl within each task; the split is per-step, not a global Phase 1/Phase 2 barrier.)
- On any **unexpected** failure, invoke `superpowers:systematic-debugging` BEFORE changing
  anything. A test failing because a class/method doesn't exist yet is the *right* reason; a
  runtime fatal / missing column / unknown field means a **wrong assumption in the test** — fix
  the test, don't add scaffolding to route around it.
- Before claiming a task done, invoke `superpowers:verification-before-completion` and paste the
  acceptance command output into `.ai/active/session-summary.md`.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix,
  write `.ai/active/blocker.md` (command + output + hypothesis) and STOP.

## Goal

Generate every puzzle tab in code (retire the hidden "Tab template" / "Meta Tab template" and
the `duplicateSheet` / `copy_puzzle` copy model); render one unified contribution table per
target as a real native Sheets Table grown by an incremental column-A key reconcile; preserve
solver data on promotion via column inserts; rename "Unassigned Puzzles" → "Unassigned Feeders"
with a **feeds-only** membership test sharing the same generator + reconciler.

## In scope

The 12 tasks in the authoritative plan. Logic (row-keying, desired rows, diff) in `bot/core/`;
request-builder methods on the `GoogleSheets` cog tested by capturing `batchUpdate` request
shapes (no live Google), mirroring `tests/test_aggregate_to_tab.py`.

## Out of scope

- Frontend / board UI changes (board stays Metapuzzle-only).
- Sheet archive / duplicate mechanics.
- N-level nesting rework, per-puzzle metadata-key pointers.
- `!sync` / membership / convert command behaviour (that was Milestone C).
- **No DB migration** — D adds none (013 is B2's; schema from A+B+C is the substrate).

## Relevant files

Authoritative list with per-file action is in the plan's **File structure** table. Summary:

- `bot/core/contribution.py` — **create** (pure row-key + desired rows).
- `bot/core/unassigned.py` — modify (docstring note only; `diff_rows` already generic).
- `bot/cogs/sheet_layout.py` — **already committed** (Task 2 capture artifact, `f7a4a83`). Read only;
  Task 2 is DONE — just confirm it imports.
- `bot/cogs/puzzles_gsheet.py` — modify (new generator + contribution table + reconciler +
  promotion methods; retire `duplicateSheet` / `copy_puzzle`).
- `bot/store/mysqldb.py` — modify (`get_unassigned` feeds-only test).
- `bot/web/data.py` — modify only if the tab label string is emitted there.
- `tests/test_contribution.py`, `tests/test_contribution_table_requests.py`,
  `tests/test_get_unassigned.py` — **create/extend**.

## Constraints

- Don't touch files outside the plan's list. Don't refactor unrelated code.
- **All growth insert-based** on shared/target tabs — never `updateCells` / `values.update`
  over a range holding solver data.
- Use **`puzzle.is_target`** as the has-target accessor (B2 landed: `metapuzzle`/`metameta`
  columns are gone, migration 013) and **`TagGroupJsonDb`** (renamed from `RoundJsonDb`).
- `is_target` means **has-target**, NOT "is-fed" — the feeds/informs/none distinction lives in
  the edge layer; the contribution rows decide content from edges. Never read `is_target` as
  "this is a fed meta".
- If a task changes a method signature, grep its call sites and update hand-built ones (the cog
  is container/`self`-resolved, so most adapt, but verify).

## Test constraints (inlined — do not rely on global instructions)

- **Reuse exact helpers/seams; invent none.** Request-shape tests mirror
  `tests/test_aggregate_to_tab.py` (`asyncio.run` + `GoogleSheets.__new__` + method-patching).
  DB tests reuse the insert helpers in `tests/test_tag_edges.py` / `tests/conftest.py` — grep
  them first; do **not** invent new fixtures.
- **NEVER write a loop that resolves state "until" a condition.** If you think you need one, STOP
  and write `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says to.
- **Confirm every column / attribute / payload key exists before use** — grep the model,
  migration, or handler. Do not guess field names (e.g. evidence accessor in Task 10 — confirm
  the name in `puzzle_service.py` / `mysqldb.py`; ship `evidence=[]` and note it if absent).
- **A test file must define or import every helper it calls** — never rely on a top-level
  function in a sibling test file. `--filter` hides the gap; the parallel gate shards by file and
  a cross-file helper becomes an "undefined function" fatal. Give each file uniquely-named helpers.
- **When the change is about rendered text, assert the rendered string** (and the *absence* of the
  wrong output), not just structure.
- **Wrap every test run in `timeout`, write output to a file, single statement** (no `; echo`
  tail). A run that reads as "slow" may be a hang; isolate per file. Exit 124 = killed → read the
  log.

## Run rules (project-pinned)

- **Full suite:** `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
- **Single file (iteration):** `timeout 120 pipenv run python -m pytest tests/<file>.py -q > /tmp/test.log 2>&1`
- Use **`pipenv run python -m pytest`** — plain `pytest`/`python` fail (PATH + `sys.path`).
- **Lint:** `pipenv run ruff check` (exit 0). Do NOT blind-`--fix` F401 on `bot/` modules
  (load-bearing re-exports / patch-targets carry `# noqa: F401`).
- Tests open MySQL at import (`bot/store/__init__.py`); a reachable `puzzlebot_test` + gitignored
  `config.json` must exist. If import fails on DB connect → **environment blocker**, write
  `.ai/active/blocker.md`, do NOT stub the DB.

## Phases (the plan's 12 tasks)

Per-task detail (steps, skeletons, acceptance, commit message) lives in the authoritative plan.
This is the index — do not re-expand it here.

- **Task 1** — Baseline (investigation only; no commit). Confirm suite GREEN, ruff clean,
  frontend builds, no new migration. RED baseline or unreachable DB → STOP + `blocker.md`.
- **Task 2** — Capture template layout — **DONE** (`bot/cogs/sheet_layout.py`, `f7a4a83`). Only
  confirm it imports (`pipenv run python -c "import bot.cogs.sheet_layout"`).
- **Task 3** — `bot/core/contribution.py`: `contribution_rows` (feeds/informs/none + evidence). [pure, TDD]
- **Task 4** — Reuse generic `diff_rows` for string keys (+ docstring note). [pure, TDD]
- **Task 5** — `get_unassigned` feeds-only membership + label. [DB test]
- **Task 6** — `_add_contribution_table` (native Table, A=key hidden, B/C/D). [request-shape]
- **Task 7** — `reconcile_table` (generalized append/update/prune by column-A key). [request-shape]
- **Task 8** — `generate_puzzle_tab` (replace `duplicateSheet`; rewire `add_new_puzzle_sheet`).
- **Task 9** — `promote_tab_to_target` (insert-based, preserves solver data; rewire convert).
- **Task 10** — `render_contribution` from the service (collapse the two renderers).
- **Task 11** — Unassigned Feeders rename + real-table retrofit via shared reconciler.
- **Task 12** — Retire templates (`duplicateSheet` / `copy_puzzle`) + `[INTEGRATION]` end-to-end.

**`[INTEGRATION]` steps (Task 12 Step 4; Task 2 already done):** need live Google credentials +
the test hunt spreadsheet. If credentials are unavailable, complete every unit task and hand the
integration checklist back to the user with the exact actions — do NOT block unit work on it.

## Acceptance criteria (whole milestone)

- Full suite GREEN: `timeout 180 pipenv run python -m pytest tests/ -q` → 0 failures.
- `pipenv run ruff check` → exit 0.
- Frontend builds clean.
- No new migration (`ls migrations/` unchanged from C — `013_*` highest).
- `grep -rn "duplicateSheet\|copy_puzzle" bot/cogs/puzzles_gsheet.py` → gone.
- `bot/core/contribution.py` exists; `contribution_rows` covers feeds/informs/none + evidence.
- `reconcile_table` shared by the contribution table and Unassigned Feeders.
- `[INTEGRATION]` checklist (Task 12 Step 4) recorded or handed to the user with commands.
