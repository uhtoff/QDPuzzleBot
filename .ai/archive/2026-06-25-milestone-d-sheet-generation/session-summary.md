# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2
Started: 2026-06-25
Status: in progress — refactor(sheets): retire dead template/copy machinery [Milestone D] @ 5423ac0

## Goal

Implement Milestone D (Sheet Generation) for QDPuzzleBot: generate every puzzle tab in code,
render a unified per-target contribution native Table grown by an incremental column-A reconcile,
preserve solver data on promotion, and rename "Unassigned Puzzles" → "Unassigned Feeders" with a
feeds-only membership test.

Authoritative plan: `docs/superpowers/plans/2026-06-25-milestone-d-sheet-generation.md`.
Read `.ai/active/current-spec.md` first — it pins the executor framing (model split, run rules,
two-strikes, constraints). The spec is authoritative; this summary is background.

## Context the executor needs (not obvious from files)

- **Prereqs are LANDED in this worktree.** working-2 = `main` + the D docs/artifact. `main` now
  carries **B2** (derive `is_target`, drop `metapuzzle`/`metameta`, migration 013, `RoundJsonDb`
  → `TagGroupJsonDb`, `get_unassigned` derives target-ness) and **C** (`convert_to_type`, edge
  vocabulary `feeds`/`informs`/`none`, `target_puzzle_id`/`group_code`). No further setup.
- **Task 2 is already DONE.** The template layout was captured and committed as
  `bot/cogs/sheet_layout.py` (commit `f7a4a83`) by reading the live templates through the
  project's own authed Sheets client. The executor only confirms it imports; do NOT re-run the
  capture. Captured geometry (drives Tasks 6 & 9): 3 frozen header rows; static bold labels A1/
  H1/A2/A3; merges B1:G1, I1:Q1, B2:Q2, B3:Q3; bot-dynamic B1/I1/B3; user-owned B2 (flavour); the
  meta tab's old A4/A5 rollup region is REPLACED by the contribution Table.
- **Model split:** author each task's failing test(s) on **Opus** (test design is the failure
  mode for weaker models here); do the implementation steps on **Sonnet**.
- **The plan is near-verbatim** — test skeletons, method bodies, exact commands, commit messages
  are all there. Fill them in mechanically; don't redesign.

## What NOT to touch

- Files outside the plan's File-structure table.
- DB schema / migrations (D adds none).
- Frontend / board UI.
- The committed `bot/cogs/sheet_layout.py` artifact (read only).
- Don't blind-`--fix` ruff F401 on `bot/` modules (load-bearing re-exports / patch-targets).

## Final decisions

- Handoff packaged as a **thin pointer** to the existing plan (keeps its per-task
  test→impl→commit structure for `subagent-driven-development`), not a rewrite into an
  all-tests-first phase split.
- Executor runs in **working-2** (all prereqs + the D plan/spec + the capture artifact live here,
  tree clean).
- Model tiers: **Opus** for test authoring, **Sonnet** for implementation.

## Rejected approaches

- Rewriting the plan into the handoff skill's canonical "Phase 1 = ALL failing tests first"
  shape — rejected because the plan is already a complete per-task TDD package and several
  request-shape tests (Tasks 7/9/10) depend on signatures defined in earlier tasks, so a global
  test-first barrier would be awkward without benefit.

## Next steps

1. Read `.ai/active/current-spec.md`, then the authoritative plan.
2. Start at **Task 1** (baseline: suite GREEN, ruff clean, frontend builds, no new migration).
   If baseline is RED or the DB is unreachable → STOP, write `.ai/active/blocker.md`.
3. Proceed task-by-task to Task 12. Commit at each task boundary with the plan's commit message.
4. Hand the `[INTEGRATION]` checklist (Task 12 Step 4) back to the user if credentials are
   unavailable.

## Task 1 baseline results (2026-06-25, working-2 @ 80c01ab)

- **Suite GREEN:** `timeout 180 pipenv run python -m pytest tests/ -q` → `422 passed in 1.41s`
  (0 failed).
- **Ruff clean:** `pipenv run ruff check` → `All checks passed!` (exit 0).
- **Frontend builds:** `npm run build` (`tsc --noEmit && vite build`) → exit 0, `✓ built in 2.98s`.
- **No new migration:** `migrations/` highest is `013_drop_role_flags.sql` (B2's); D adds none.
- **Accessor confirmed:** `puzzle.is_target` is the live has-target accessor
  (`bot/store/puzzle_data.py:49`, transient, derived from `tag_group.target_puzzle_id`); used
  throughout `bot/cogs/puzzles_gsheet.py`. `bot/cogs/sheet_layout.py` imports cleanly (Task 2 done).

Baseline is healthy — cleared to start Task 3.

## Phase-1 tests authored (Opus, committed @ 850bd86)

All failing tests for the unit tasks are written and committed. The **implementation pass
(Sonnet)** now makes them green task-by-task; do NOT rewrite the tests to pass — implement the
production code per the plan. Each impl step is in the authoritative plan under its task.

Test files and their RED-until-impl state (confirmed by running them 2026-06-25):

- `tests/test_contribution.py` — **Tasks 3 & 4**. Currently `ModuleNotFoundError:
  bot.core.contribution` (whole file errors at collection until Task 3 creates the module).
  After Task 3's `contribution_rows`, all 5 Task-3 tests + the Task-4 `diff_rows` test go green
  (diff_rows is already key-generic — Task 4 needs only the docstring note, no code).
- `tests/test_get_unassigned.py` — **Task 5** (live MySQL, seeded `hunt_id=10`, subset asserts).
  Currently fails `assert <informer_id> in result_ids` because today's query excludes any member
  of a target-bearing group. Goes green when `get_unassigned` adds `AND t.edge_type = 'feeds'`
  to the membership `NOT EXISTS`.
- `tests/test_contribution_table_requests.py` — **Tasks 6-11** (request-shape, no live Google).
  Tasks 6-10 fail with clean `AttributeError` on the new cog methods. **Task 11's**
  `test_apply_unassigned_changes_delegates_to_reconcile_table` currently reaches `get_sheet()`
  (FileNotFoundError, see below) because the old body still calls Google — it goes clean-green
  once `apply_unassigned_changes` delegates to the patched `reconcile_table`.

Implementation pass order (skip Task 4 code — docstring only; Task 2 done): **3 → 5 → 6 → 7 →
8 → 9 → 10 → 11 → 12**. Commit per task with the plan's commit message. Tasks 8/9/10/11 also
require updating existing test expectations that assert the OLD render/convert/duplicateSheet
bodies (deliberate behaviour change — see each task's "update any test asserting the old body"
note); those edits belong to the impl pass, not Phase-1.

**Progress (2026-06-25):**
- Task 3 ✅ complete @ bd4e1f1 — `bot/core/contribution.py` matches the plan,
  `tests/test_contribution.py` 6/6 green (5 Task-3 + 1 Task-4 diff_rows test), ruff clean.
- Task 4 ✅ complete @ 407e933 — added the shared-use docstring note to `diff_rows`
  (`bot/core/unassigned.py`); its test already passed, no code change needed.
- Task 5 ✅ complete @ a88c118 — `get_unassigned` membership clause gains `AND t.edge_type =
  'feeds'` + feeds-only docstring (`bot/store/mysqldb.py`). `tests/test_get_unassigned.py` green
  (live MySQL); full suite 429 passed, ruff clean.
- Task 6 ✅ complete @ 4cdd7e7 — `CONTRIBUTION_HEADER` constant + `_add_contribution_table()`
  (`bot/cogs/puzzles_gsheet.py`). Creates native Table with key column hidden.
- Task 7 ✅ complete @ 30f6786 — `reconcile_table()` generalized key-anchored reconciler
  (`bot/cogs/puzzles_gsheet.py`). Reads column-A keys, diffs via `diff_rows`, applies updates/
  prunes/appends.
- Task 8 ✅ complete @ 08d7572 — `generate_puzzle_tab()` replaces `duplicateSheet` path
  (`bot/cogs/puzzles_gsheet.py`). Creates blank tab, writes captured header layout + name/url
  hyperlink, adds contribution Table for targets.
- Task 9 ✅ complete @ cfc1a35 — `promote_tab_to_target()` insert-based in-place transform
  (`bot/cogs/puzzles_gsheet.py`). Inserts 4 columns at left, adds contribution Table,
  restores header (flavour B2, progress B3), shifts hyperlink.
- Task 10 ✅ complete @ 801e30d — `render_contribution()` delegates to service layer
  (`bot/cogs/puzzles_gsheet.py`). Excludes target own row, calls `contribution_rows()`,
  reconciles via `reconcile_table()`.
- Task 11 ✅ complete @ 2e75718 — `apply_unassigned_changes()` now delegates to
  `reconcile_table()` (`bot/cogs/puzzles_gsheet.py`). Replaced ~100-line direct Sheets
  implementation with thin wrapper (saves 96 lines).

⚠️ **GAP FOUND & FIXED (2026-06-25):** the Tasks 8-10 commits added the new methods but
**skipped the call-site rewires** — `generate_puzzle_tab` / `promote_tab_to_target` /
`render_contribution` had ZERO production callers; the real flow still ran the old
template/`duplicateSheet`/`copy_puzzle`/`add_metapuzzle_data` machinery. The green suite masked
it (new tests called the methods directly; old tests still asserted old bodies). Now wired:
- Rewire @ 6dc36a2 — Task 8: `add_new_puzzle_sheet` → `generate_puzzle_tab`. Task 10:
  `_render_rollup` → `render_contribution` (renderers collapsed; `source_mode` drives membership
  only; `evidence=[]` — informs *content* rows are a follow-up, accessor is `EvidenceJsonDb` in
  `bot/store/mysqldb.py`).
- Task 9 design change (user decision): the old bidirectional `convert_puzzle_spreadsheet` is
  replaced by a directional pair — `promote_tab_to_group` (promote-in-place via
  `promote_tab_to_target`) and **new** `demote_tab_to_puzzle` (deletes the contribution columns/
  Table; solver grid slides back). Call sites in `puzzle_service.py` updated by direction (909
  demote, 1163 promote). Naming note: `promote_tab_to_target` (mechanism) + `promote_tab_to_group`
  (operation) sit side by side — could be collapsed later if the dual name grates.
- Rollup-rendering tests re-pointed at `render_contribution`; added `demote_tab_to_puzzle`
  request-shape test. Removed now-unused `copy` import.
- **Status: 437 tests pass, ruff clean, frontend builds.**

- Task 12 (code) ✅ @ 5423ac0 — retired dead machinery: removed `copy_puzzle`, `get_grid_size`,
  `update_grid_size`, the `METAPUZZLE_SHEET` constant, and the orphaned `FIRST_PUZZLE_ROW`/
  `FIRST_PUZZLE_COLUMN`/`FIRST_METAPUZZLE_COLUMN` layout constants (all zero-ref after the Task 9
  rewire). **Kept** `add_new_sheet` + `PUZZLE_SHEET` (+ its `duplicateSheet` body) — still used by
  `create_additional_spreadsheet` for "Extra Sheet" tabs (non-puzzle path). Suite 437 passed,
  ruff clean, frontend builds. Net −105 lines.
- **Milestone D code-complete.** Remaining: the live `[INTEGRATION]` pass (Task 12 Step 4) —
  cannot run here (no `google_secrets.json`); see the checklist handed to the user below.

## [INTEGRATION] checklist — for the user (needs live Google credentials)

Run against the test hunt spreadsheet and eyeball each:
1. **Create a feeder puzzle** → its tab is generated in code (no template duplicate); header
   matches the old look (name/url hyperlink, flavour, progress).
2. **Create / convert a meta** → contribution Table appears (col A hidden, col B Type
   filterable); feeders listed with answers.
3. **Add an `informs` member + a `!clue`** → an Information row appears; a bare informs edge
   shows one keyless Information row. NB `evidence=[]` is currently wired, so informs *content*
   rows won't populate until the `EvidenceJsonDb` accessor is plumbed into `_render_rollup`
   (known follow-up).
4. **Promote a puzzle with existing solver content** → grid pushed right (4 cols), not
   overwritten; flavour/progress preserved.
5. **Demote a target back to a puzzle** (`!convert_to_puzzle`) → contribution columns/Table
   removed; solver grid slides back. ⚠️ `demote_tab_to_puzzle` deletes columns A-D which hold a
   native Table — confirm Sheets allows the column delete under a Table (request-shape tested
   only; if Sheets rejects it, a `deleteTable` request must precede the `deleteDimension`).
6. **Unassigned Feeders tab** → an informs/none-only puzzle appears; one with a feeds edge does
   not.
7. **"Extra Sheet for X"** (`create_additional_spreadsheet`) → still works (kept on the old
   `add_new_sheet`/`PUZZLE_SHEET` template path).

## ⚠️ google_secrets.json is ABSENT in this worktree

working-2 has no `google_secrets.json`, so any code path that calls `get_sheet()` /
`bot/utils/gsheets.py` raises `FileNotFoundError`. This does NOT affect the suite (baseline 422
passed; the request-shape tests patch the cog seams and never build a real client) — but it means:

- The `[INTEGRATION]` step (Task 12 Step 4) cannot run here — hand it to the user with creds.
- When implementing, keep using the patched seams the tests provide; do not introduce a real
  `get_sheet()` call into a unit-tested path. (Task 11's messy red is exactly this: the old
  `apply_unassigned_changes` reaches `get_sheet()` until it delegates.)

## If you get stuck

Diagnose with `superpowers:systematic-debugging` BEFORE changing anything. A test failing because
a symbol doesn't exist yet is the right reason; a runtime fatal / missing column / unknown field
means a wrong assumption in the test — fix the test. **Two-strikes:** if one acceptance command
fails twice without a clear understood fix, write `.ai/active/blocker.md` (command + output +
hypothesis) and STOP rather than digging deeper.

## Open questions

- None blocking. Non-blocking: Task 10's evidence accessor name (confirm in `puzzle_service.py` /
  `mysqldb.py`; ship `evidence=[]` and record a follow-up if absent) and Task 11's back-compat
  probe for the old "Unassigned Puzzles" tab name (dev has no live data — new name is fine; record
  the choice).
