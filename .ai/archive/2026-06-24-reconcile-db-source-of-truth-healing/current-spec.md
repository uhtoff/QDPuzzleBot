# Current Spec — `!reconcile` (DB-as-source-of-truth healing)

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2  (branch `working-2`, at `main` 41efcc7)
Target model per phase: **Phase 1 (tests) → Opus (stronger)**, **Phases 2–6 (impl) → Sonnet (cheaper)**
Started: 2026-06-23
Status: ready for handoff — Phase 1 (tests)

## Authoritative code source

`docs/superpowers/plans/2026-06-23-reconcile.md` (committed on this branch) contains the **full
verbatim code** — every test and every implementation, task by task — plus the confirmed seams. It
is authoritative: **copy code from it**. This spec owns only the phase split, constraints,
anti-patterns, and acceptance commands. Read the plan's "Key seams" section before writing anything.

The design rationale is in `docs/superpowers/specs/2026-06-23-reconcile-design.md` (read-only).

## Before you start (executing model)

- Execute **only the current phase**. Do not look ahead and implement later phases early.
- **Work in this worktree path** (`.claude/worktrees/working-2`), not the main checkout — the same
  relative paths exist in both and point at different branches. All edits, test runs, and commits
  happen here.
- On any **unexpected** failure (not the planned "fails because not implemented yet"), invoke
  `superpowers:systematic-debugging` BEFORE changing anything.
- Before claiming a phase done, invoke `superpowers:verification-before-completion` and paste the
  acceptance command output into your summary.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix, write
  `.ai/active/blocker.md` (what you ran, output, your diagnosis) and STOP.

## Goal

Add an organiser-only `!reconcile` command that makes Discord channels and Google Sheets tabs match
the database (the source of truth), healing partially-applied create/edit operations.

## In scope

- New pure-data module `bot/core/reconcile.py` (report model).
- New ctx-free methods on `PuzzleService` (`bot/core/puzzle_service.py`): `reconcile`,
  `delete_orphans`, and private helpers `_home_category`, `_reconcile_channel`, `_reconcile_tabs`,
  `_collect_orphan_channels`, `_collect_orphan_tabs`.
- New cog command `reconcile` + `_format_reconcile_report` staticmethod on `Puzzles`
  (`bot/cogs/puzzles.py`).
- Possibly one new helper `delete_sheet_tab` on `GoogleSheets` (`bot/cogs/puzzles_gsheet.py`) — ONLY
  if no tab-delete method already exists (Phase 5 checks first).
- New test file `tests/test_reconcile.py`.

## Out of scope (do NOT do)

- Recreating Discord **categories** for rounds — reconcile only *reports* a broken
  `round.category_id` (`round_category_broken` action).
- Rebuilding meta roll-up sheets — leave to the existing `!rebuild` / `refresh_meta`.
- Additional-sheet (`puzzle.additional_sheets`) recreation — explicitly deferred (the plan's Task 3
  note). Do not implement it.
- A web/dashboard entry point.
- Any refactor of existing code beyond adding the new methods/command.

## Relevant files

- `docs/superpowers/plans/2026-06-23-reconcile.md` (read — authoritative code)
- `docs/superpowers/specs/2026-06-23-reconcile-design.md` (read — rationale)
- `bot/core/reconcile.py` (create)
- `bot/core/puzzle_service.py` (modify — add methods near `create_puzzle`/`refresh_meta`; import
  `from bot.core.reconcile import ReconcileReport, ReconcileAction` at top)
- `bot/cogs/puzzles.py` (modify — add command near `rebuild` ~line 655; `ConfirmView` and
  `ORGANISER_ROLES` are already imported/in scope)
- `bot/cogs/puzzles_gsheet.py` (modify — Phase 5 only, conditionally)
- `tests/test_reconcile.py` (create)

## Constraints

- Do not touch files outside the Relevant files list. Do not refactor existing functions.
- Reuse the exact seams named in the plan (`get_or_create_channel`, `clean_name`,
  `solve_divider_position`, `notifier.welcome`, `self._gsheet_cog`, `PuzzleJsonDb.*`,
  `RoundJsonDb.*`, gsheet cog `get_page_name_by_id`/`add_new_puzzle_sheet`/`update_puzzle_info`).
  Invent no new seams.
- No phase changes a constructor signature, so no `new ClassName(` call-site sweep is needed.
- Commit at the end of every phase with the message stated in that phase.

## Test constraints (anti-patterns — do not violate)

- **Reuse the exact helpers/seams; invent none.** All test helpers (`_service`, `_hunt`, `_puzzle`,
  `_guild_with`, `_category`, `_gcog`, `_service_with_gsheet`) are defined IN the plan — copy them
  into `tests/test_reconcile.py`. Do not rely on helpers from sibling test files.
- **This test file must define/import every helper it calls.** The parallel gate shards by file; a
  cross-file helper becomes an "undefined function" fatal. Keep all helpers local to this file.
- **NEVER write a loop that resolves state "until" a condition.** If you think you need one, STOP and
  write `blocker.md`.
- **NEVER modify test infrastructure (`conftest.py`) or stub randomness** unless a step says to.
- **Confirm every attribute/payload key exists before use** — `PuzzleData`/`RoundData` field names
  (`tags`, `channel_id`, `channel_name`, `channel_mention`, `google_page_id`, `archived`,
  `category_id`, `meta_id`) are confirmed in the plan; do not guess others.
- **Assert rendered strings for the formatter** (Phase 6) — the plan's formatter test asserts exact
  substrings (`"channel_create: 1"`, `"1 orphan channel"`, `"Boom"`). Keep those assertions.
- **Wrap every test run in `timeout`, write to a file, single statement** (no `; echo` tail). A run
  that reads as "slow" may be a hang — isolate per file with `timeout`.

Test command (project convention):
```
timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1
```
Read `/tmp/test.log`. A timeout-killed run exits 124.

---

## Phase 1 — write ALL failing tests (+ collection scaffolding). Model: Opus.

Python cannot *collect* a test file that imports a missing module, so Phase 1 creates the trivial
pure-data report model and stub method signatures so the file collects and each behavioural test
fails for the **right reason** (logic not yet written), not at import.

Steps:

1. **Create `bot/core/reconcile.py`** — copy the real implementation verbatim from the plan's
   **Task 1, Step 3** (`ReconcileAction` + `ReconcileReport`). This is pure data, no business logic.

2. **Add `NotImplementedError` stubs** so the test file collects. In `bot/core/puzzle_service.py`,
   add `from bot.core.reconcile import ReconcileReport, ReconcileAction` at the top, then add these
   method signatures to `PuzzleService` (bodies exactly `raise NotImplementedError("reconcile: phase 2+")`):
   - `def _home_category(self, guild, puzzle):`
   - `async def _reconcile_channel(self, guild, hunt, puzzle, report, *, apply):`
   - `async def _reconcile_tabs(self, hunt, puzzle, *, apply):`
   - `async def reconcile(self, guild, hunt, *, apply):`
   - `def _collect_orphan_channels(self, guild, rounds, puzzles, report):`
   - `def _collect_orphan_tabs(self, hunt, puzzles, report):`
   - `async def delete_orphans(self, guild, hunt, report):`

   In `bot/cogs/puzzles.py`, add to the `Puzzles` cog:
   - `@staticmethod\n    def _format_reconcile_report(report, *, applied):` → `raise NotImplementedError("reconcile: phase 6")`
   (Do NOT add the `reconcile` command yet — it is added in Phase 6. The formatter stub is only here
   so the Phase 6 formatter test collects.)

3. **Write `tests/test_reconcile.py`** — copy EVERY test and EVERY helper from the plan, across all
   seven tasks (Task 1 through Task 7 test blocks, plus the helper functions `_service`, `_hunt`,
   `_puzzle`, `_guild_with`, `_category`, `_service_with_gsheet`, `_gcog`). Keep the imports the plan
   shows. Do not write any implementation logic in the SUT beyond the Step-1 model and Step-2 stubs.

**Acceptance (must confirm tests fail for the RIGHT reason):**
```
timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1
```
- The file must **collect cleanly** — no `ModuleNotFoundError`, no `ImportError`, no collection error
  in `/tmp/test.log`.
- The three **report-model tests** (`test_report_*`) PASS (the model is real).
- Every **behavioural test** FAILS with `NotImplementedError` (grep the log: it should show
  `NotImplementedError`, not `AttributeError`/`ImportError`).
- If any test fails with `AttributeError` (missing attribute) or an import/collection error, a stub
  or import is missing — fix the scaffolding, not the tests.

**Commit:**
```bash
git add bot/core/reconcile.py bot/core/puzzle_service.py bot/cogs/puzzles.py tests/test_reconcile.py
git commit -m "reconcile: failing tests + report model + stubs (phase 1)"
```

---

## Phase 2 — channel reconciliation. Model: Sonnet.

Replace the `_home_category` and `_reconcile_channel` stubs with the real implementations from the
plan's **Task 2, Step 3**.

**Acceptance:**
```
timeout 120 pipenv run python -m pytest tests/test_reconcile.py -k "channel" -q > /tmp/test.log 2>&1
```
Expected: the four `test_channel_*` tests PASS. (Other behavioural tests still fail with
`NotImplementedError` — that is expected until their phase.)

**Commit:** `git commit -am "reconcile: channel relink/move/create logic (phase 2)"`

---

## Phase 3 — Sheets-tab reconciliation. Model: Sonnet.

Replace the `_reconcile_tabs` stub with the plan's **Task 3, Step 3** implementation. Do NOT
implement additional-sheet recreation (out of scope).

**Acceptance:**
```
timeout 120 pipenv run python -m pytest tests/test_reconcile.py -k "tab" -q > /tmp/test.log 2>&1
```
Expected: the four `test_tab_*` tests PASS.

**Commit:** `git commit -am "reconcile: recreate missing Sheets tab from DB (phase 3)"`

---

## Phase 4 — hunt-wide drive + round-category + orphan collection. Model: Sonnet.

Replace the `reconcile`, `_collect_orphan_channels`, `_collect_orphan_tabs` stubs with the plan's
**Task 4, Step 3** implementations.

**VALIDATE THE TWO HEURISTICS against live code before finalising** (plan self-review flags these):
- Orphan-channel exclusion uses the solved-divider marker `———`. Confirm that string is what the
  codebase actually uses: `grep -rn "———" bot/ | head`. Adjust the predicate if the marker differs.
- Orphan-tab exclusion uses the `hidden` sheet property for template/system tabs. Confirm template
  sheets are created hidden: `grep -n "hidden\|template\|HIDDEN" bot/cogs/puzzles_gsheet.py | head`.
  Adjust if needed. If either is genuinely unclear, write `blocker.md` rather than guess.

**Acceptance:**
```
timeout 120 pipenv run python -m pytest tests/test_reconcile.py -k "reconcile_" -q > /tmp/test.log 2>&1
```
Expected: `test_reconcile_collects_orphan_channel`, `test_reconcile_flags_broken_round_category`,
`test_reconcile_failure_isolated_per_puzzle` PASS.

**Commit:** `git commit -am "reconcile: hunt-wide drive + round-category + orphan collection (phase 4)"`

---

## Phase 5 — orphan deletion. Model: Sonnet.

1. **First** run the plan's **Task 5, Step 3a** check:
   `grep -n "def delete_sheet_tab\|deleteSheet\|def delete_tab\|def delete_sheet" bot/cogs/puzzles_gsheet.py`
   - If a tab-delete method exists, use its exact name in `delete_orphans` instead of
     `delete_sheet_tab`.
   - If none exists, add the `delete_sheet_tab` helper from Step 3a to `GoogleSheets`.
2. Replace the `delete_orphans` stub with the plan's **Task 5, Step 3b** implementation.

**Acceptance:**
```
timeout 120 pipenv run python -m pytest tests/test_reconcile.py -k "delete_orphans" -q > /tmp/test.log 2>&1
```
Expected: `test_delete_orphans_*` tests PASS.

**Commit:** `git commit -am "reconcile: orphan deletion post-confirmation (phase 5)"`

---

## Phase 6 — cog command + formatter. Model: Sonnet.

1. Replace the `_format_reconcile_report` stub with the plan's **Task 6, Step 3** implementation.
2. Add the `reconcile` command (plan **Task 6, Step 3**) to the `Puzzles` cog near `rebuild`.

**Acceptance:**
```
timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1
```
Expected: **ALL** `tests/test_reconcile.py` tests PASS (including the Phase 7 convergence test, which
was authored in Phase 1).

**Commit:** `git commit -am "reconcile: !reconcile command + report formatter (phase 6)"`

---

## Final verification (after Phase 6)

```
timeout 180 pipenv run python -m pytest tests/test_reconcile.py tests/test_create_service.py tests/test_gsheet_metadata.py tests/test_sheet_service.py tests/test_confirm_view.py tests/test_sheet_list_guard.py -q > /tmp/test.log 2>&1
```
Expected: all PASS (no regressions). Paste the summary line into `session-summary.md` and bump its
`Status:` to "ready for review — Phase 6 @ <hash>".

## Rollback / recovery notes

Pure additive feature (new module, new methods, new command, optional new gsheet helper). Revert the
phase commits to remove it. `!reconcile` (no args) is read-only; destructive behaviour is gated
behind `apply orphans` + `ConfirmView`.
