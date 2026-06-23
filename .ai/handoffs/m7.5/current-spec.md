# Current Spec — M7.5: Aggregated-answers read view + copy-to-clipboard

Tool: Claude Code (handoff package)
Worktree: a NEW worktree branched off M7 (see "Branch & dependency" below) — NOT working-1
Target model — Phase 1 (tests): **Opus 4.8** · Phases 2–3 (implementation): **Sonnet 4.6**
Started: 2026-06-23
Status: ready for handoff — Phase 1 (tests)

Source plan: `/home/russ/.claude/plans/can-we-plan-7-5-parallel-scroll.md` (background only;
this spec is authoritative). Roadmap: `docs/PLANS.md` M7 ("aggregated-answers view +
copy-to-clipboard in the SPA"). M7 engine handoff (background): `.ai/active/current-spec.md`.

---

## Branch & dependency (READ FIRST)

M7.5 **depends on M7** (`feature/m7-flexible-aggregation`), which is still being implemented.
Do not start until M7 has landed Phase 4 (`_collect_rollup_puzzles`) and Phase 6 (`source_mode`
in the board payload). Then:

- `git switch -c feature/m7.5-aggregated-answers <M7-branch-or-main-after-merge>` — branch from
  M7's final state (per `docs/WORKTREES.md`, origin/main is intentionally stale; branch from the
  local M7 branch or local `main` after M7 merges). Work in a fresh worktree, not `working-1`
  (which still holds the in-progress M7 handoff in `.ai/active/`).
- **Reconcile against git reality before trusting this spec.** M7 was mid-flight when this was
  written. `grep -n "_collect_rollup_puzzles\|def refresh_meta\|source_mode" bot/core/puzzle_service.py`
  and read the actual shape. If `_collect_rollup_puzzles` differs from what Phase 2 below assumes,
  adapt the refactor to the real code — keep M7's existing tests green.

## Before you start (executing model — READ THIS)

- **Execute only the current phase.** Commit at each phase boundary; do not skip ahead.
- **Required background skills:** before writing tests invoke `superpowers:test-driven-development`
  and `superpowers:writing-plans`. On ANY unexpected failure invoke
  `superpowers:systematic-debugging` BEFORE changing anything. Before claiming a phase done invoke
  `superpowers:verification-before-completion` and paste the acceptance command output into your
  report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix,
  write `.ai/active/blocker.md` (what failed, exact output, your diagnosis) and STOP. Do not add
  scaffolding to route around a failing assumption.
- **A test failing because a method/attribute doesn't exist yet is the RIGHT reason.** An
  ImportError, a missing DB column, an unknown field, or an `AttributeError` on a real object
  means a *wrong assumption in the test* — fix the test, don't add machinery.

## Test command (the ONLY runner — pipenv, repo root on sys.path)

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

Filter a single file during iteration:
```bash
timeout 120 pipenv run python -m pytest tests/test_select_rollup_members.py -q > /tmp/test.log 2>&1
```
A killed run exits 124; read `/tmp/test.log`. The final gate is the full `tests/ -q` run (no
filter), so every new test file must run cleanly under that same mode (no cross-file helper
imports — see anti-patterns). Frontend gate: `cd frontend && npx tsc --noEmit`.

---

## Goal

Add the read counterpart to M7: a dashboard surface that shows a meta's aggregated answers (in
whatever membership the rollup's `source_mode` defines) with one-click copy-to-clipboard, reusing
M7's engine selection logic so the view matches exactly what is written to the Google Sheet.

## In scope (this handoff)

1. **Selector extraction** — a pure `PuzzleService.select_rollup_members(hunt, hunt_round, rollup)`
   that returns the ordered `list[PuzzleData]` per `source_mode` with **no Google-Sheet
   side-effects**; refactor M7's `_collect_rollup_puzzles` to call it then render.
2. **Read endpoint** — `GET /api/rollups/<int:round_id>/answers` (read-only, auth-gated) returning
   `{"mode": <source_mode>, "answers": [{"name", "solution"}, ...]}`.
3. **Frontend** — a lazy `useRollupAnswers` query hook + an inline per-meta panel on `BoardGroup`
   with a name+answer list and a "Copy answers" button (copies `name\tsolution`, one per line).

## Out of scope (do NOT do)

- **No writers.** `source_mode` / nest / pool setters are M7 Phases 5–6, not here.
- **No top-level "Answers" view** or cross-hunt harvesting — the surface is inline per-meta only.
- **Do NOT create a rollup row on the read path**, and **do NOT call `refresh_meta`** from the
  endpoint. Reads never write.
- **Do NOT change the Google-Sheet renderers** (`add_metapuzzle_data` /
  `add_metametapuzzle_data`) or M7's source_mode routing behaviour — only *extract* the selection
  half so both the render path and the read path share it.

## Relevant files

- `bot/core/puzzle_service.py` — extract `select_rollup_members`; refactor `_collect_rollup_puzzles`
  to reuse it. `collect_subtree_puzzles` already exists (M7 Phase 3). **(write)**
- `bot/web/api.py` — add the `GET /api/rollups/<round_id>/answers` route (read blueprint,
  `@login_required`); reuse the `_load_visible_hunt` visibility-gate pattern. **(write)**
- `bot/web/data.py` — add a thin `get_rollup_answers(...)` assembly helper if the route logic is
  more than a few lines; mirror `build_board` / `get_rollups_by_round`. **(maybe write)**
- `bot/store/rollup_data.py` — `RollupData` (`source_mode`, `edge_type_filter`). **(read)**
- `bot/store/puzzle_data.py` — `PuzzleData` (`name`, `solution`, `tags`, `tag_edges`). **(read)**
- `bot/store/mysqldb.py` — `RollupsJsonDb.get_by_attr(round_id=...)`, `RoundJsonDb`,
  `PuzzleJsonDb.get_all_from_round`. **(read)**
- `tests/test_refresh_meta.py` — the seam to copy (`_hunt`, `_svc_with_sheets`, `_patched`,
  `make_puzzle`, `make_round`). **(read)**
- `tests/test_rollup_source_mode.py` — M7's source_mode routing tests; must stay green after the
  refactor. **(read)**
- `tests/test_build_board.py` (and any existing `tests/test_*api*`/web-route test) — the web-test
  convention to mirror for the endpoint test. **(read first)**
- `frontend/src/types.ts`, `frontend/src/api.ts`,
  `frontend/src/components/BoardGroup.tsx` (+ optional `frontend/src/components/AggregatedAnswers.tsx`). **(write)**

## Constraints

- Logic belongs in `bot/core/` (the selector) and the thin read edge in `bot/web/`. Don't refactor
  unrelated code; don't touch files outside the list; leave any dirty `Pipfile.lock` out of commits.
- **Signature changes:** `select_rollup_members` is NEW. If you change `_collect_rollup_puzzles`'s
  signature, `grep -rn "_collect_rollup_puzzles" bot/ tests/` and update every call site + the M7
  tests that assert on it. `PuzzleData()`/`RollupData()` are built by hand in tests — any new field
  needs a default.
- **Read path purity:** the endpoint must perform zero writes — no `commit`, no `refresh_meta`, no
  `_find_or_create_rollup`. Use `RollupsJsonDb.get_by_attr(round_id=...)`, and if it returns None,
  construct an in-memory `RollupData(round_id=round_id, source_mode="round")` (the default) — do NOT
  persist it.

## Test constraints (anti-patterns — inlined; the executing agent does not read globals)

- **Reuse the exact seams; invent none.** Copy `tests/test_refresh_meta.py`'s `_hunt()`,
  `_svc_with_sheets()`, `_patched()` and `conftest.make_puzzle` / `make_round`. Do NOT build a new
  mock harness.
- **NEVER write a loop that resolves state "until" a condition.** If you think you need one, STOP
  and write `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says to.
- **Confirm every column/attribute/payload key exists before use.** `RollupData.source_mode` /
  `.edge_type_filter` (`rollup_data.py`); `PuzzleData.name` / `.solution` / `.tag_edges`
  (`puzzle_data.py`); `tag_edges.get(round_id, 'feeds-meta')` is the M7 read shape.
- **The selector test must prove NO render side-effect** — after calling `select_rollup_members`,
  assert BOTH `gsheet.add_metapuzzle_data.assert_not_awaited()` AND
  `gsheet.add_metametapuzzle_data.assert_not_awaited()`. That is the whole point of the extraction.
- **Assert behaviour, not just structure.** Assert the exact puzzle-id set returned per mode (e.g.
  `[p.id for p in members] == [2]`), not just that a list came back.
- **A test file must define or import every helper it calls** — import only from `tests.conftest`
  or define module-local helpers with UNIQUE names. The parallel gate shards by file; a cross-file
  helper becomes an "undefined name" fatal. Do not rely on a helper defined in a sibling test file.
- **For the endpoint test, OPEN the existing web-test convention first.** Read `tests/test_build_board.py`
  and any existing API-route test to see how routes/auth are exercised (test client? direct call to
  the view? mocked `data.*`?). Mirror it exactly. If NO route is tested anywhere in the suite, keep
  the route a 3-line wrapper and unit-test the assembly helper directly instead (and note this in
  your report) — do NOT invent a new Quart test-client harness without precedent; write `blocker.md`
  if unsure.
- **Wrap every run in `timeout`, write to `/tmp/test.log`, single statement** (no `; echo` tail).

---

## Phase 1 — Write ALL failing tests (Opus 4.8). NO production code.

Create the test files below. Each must FAIL for the right reason (the new method/route doesn't
exist yet → `AttributeError`/`ImportError`/404) — NOT a harness fatal. Commit at the end of this
phase with no production changes.

### File `tests/test_select_rollup_members.py` — pure selector, no render (mocked cog)

Copy `_hunt`, `_svc_with_sheets`, `_patched`, and import `make_puzzle`/`make_round` from
`tests.conftest` (define `_hunt`/`_svc_with_sheets`/`_patched` module-local — do NOT import from
the sibling test file). Then:

```python
class TestSelectRollupMembers:
    @pytest.mark.asyncio
    async def test_round_mode_returns_feeds_meta_members_without_rendering(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        rollup = RollupData(id=9, hunt_id=10, round_id=5, source_mode="round")
        feeder = make_puzzle(id=2, name="Feeder", solution="ANS")
        feeder.tag_edges = {5: "feeds-meta"}
        with _patched(rollup_existing=rollup) as (pdb, _h, _r, _ru):
            pdb.get_all_from_round.return_value = [feeder]
            members = await svc.select_rollup_members(_hunt(), meta_round, rollup)
        assert [p.id for p in members] == [2]
        gsheet.add_metapuzzle_data.assert_not_awaited()
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subtree_mode_returns_branch_solved_puzzles_without_rendering(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        rollup = RollupData(id=9, hunt_id=10, round_id=5, source_mode="subtree")
        child = make_round(id=6); child.parent_group_id = 5
        solved = make_puzzle(id=2, name="Branch", solved=True, solution="X")
        with _patched(rollup_existing=rollup) as (pdb, _h, rdb, _ru):
            rdb.get_all.return_value = [meta_round, child]
            pdb.get_all_from_round.side_effect = lambda rid: {5: [], 6: [solved]}[rid]
            members = await svc.select_rollup_members(_hunt(), meta_round, rollup)
        assert [p.id for p in members] == [2]
        gsheet.add_metapuzzle_data.assert_not_awaited()
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_explicit_mode_returns_only_aggregates_into_members(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        rollup = RollupData(id=9, hunt_id=10, round_id=5, source_mode="explicit")
        pooled = make_puzzle(id=2, name="Pooled", solution="P"); pooled.tag_edges = {5: "aggregates-into"}
        other = make_puzzle(id=3, name="Feeder", solution="F"); other.tag_edges = {5: "feeds-meta"}
        with _patched(rollup_existing=rollup) as (pdb, _h, _r, _ru):
            pdb.get_all_from_round.return_value = [pooled, other]
            members = await svc.select_rollup_members(_hunt(), meta_round, rollup)
        assert [p.id for p in members] == [2]
        gsheet.add_metapuzzle_data.assert_not_awaited()
        gsheet.add_metametapuzzle_data.assert_not_awaited()
```

### M7 regression (no new file — confirm green after Phase 2 refactor)

`tests/test_rollup_source_mode.py` and `tests/test_refresh_meta.py` (esp.
`test_calls_add_metapuzzle_data_for_regular_meta`, `test_calls_add_metametapuzzle_data_for_metameta`)
assert the render path still awaits the correct renderer with the correct list. The Phase 2 refactor
must keep them ALL green — they are the guard that the extraction preserved behaviour.

### File `tests/test_rollup_answers_api.py` — read endpoint (mirror the existing web-test convention)

FIRST read `tests/test_build_board.py` and any existing API/route test and mirror its setup exactly
(see the test-constraint about the web-test convention). The intent to cover:

- **happy path:** for a visible hunt with a rollup on `round_id=5`, the endpoint returns
  `{"mode": "<source_mode>", "answers": [{"name": ..., "solution": ...}, ...]}` whose answers match
  `select_rollup_members` output (assert the name/solution pairs).
- **no rollup row:** for a round with no `rollups` row, returns `mode == "round"` and the
  default-mode member list (and writes nothing — assert no `RollupsJsonDb.commit`).
- **auth:** a hunt the user cannot see → 403; an unknown `round_id` → 404.

If the suite has NO precedent for exercising a Quart route, instead unit-test the `data.py`
assembly helper directly and keep the route a thin wrapper — note this choice in your report.

### Phase 1 acceptance

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```
- The new test files COLLECT and RUN under the full (non-filtered) run — no ImportError/collection
  fatal, no "undefined name", no DB-bootstrap error.
- The new tests FAIL, each for the right reason: `AttributeError` on `select_rollup_members` /
  404 (route absent) — NOT a harness fatal. Paste the failing lines.
- **All pre-existing tests still PASS.**
- Commit: `M7.5 phase 1: failing tests for rollup members selector + answers endpoint`.

---

## Phase 2 — Backend: selector extraction + read endpoint (Sonnet 4.6)

1. In `bot/core/puzzle_service.py` add
   `async def select_rollup_members(self, hunt, hunt_round, rollup) -> list[PuzzleData]` that
   branches on `rollup.source_mode` (treat falsy as `"round"`), with NO gsheet calls:
   - `round`: `PuzzleJsonDb.get_all_from_round(hunt_round.id)` filtered to
     `p.tag_edges.get(hunt_round.id, "feeds-meta") == rollup.edge_type_filter`.
   - `subtree`: `await self.collect_subtree_puzzles(hunt_round)` (already solved-only, deduped).
   - `explicit`: `get_all_from_round(...)` filtered to `p.tag_edges.get(hunt_round.id) == "aggregates-into"`.
   Mirror the exact selection M7's `_collect_rollup_puzzles` already does — read that method and lift
   its selection lines verbatim.
2. Refactor `_collect_rollup_puzzles` to `members = await self.select_rollup_members(...)` then await
   the same renderer it used before (`add_metametapuzzle_data` for subtree, else `add_metapuzzle_data`).
   M7's tests must stay green.
3. Add the read route in `bot/web/api.py`:
   `@api_bp.route("/rollups/<int:round_id>/answers")`, `@login_required`. Resolve
   `round → hunt` (via `RoundJsonDb.get_by_attr(id=round_id)` → its `hunt_id` → `data.get_hunt_by_*`)
   and gate with the same visibility check `_load_visible_hunt` uses; load the rollup via
   `RollupsJsonDb.get_by_attr(round_id=round_id)` (None → in-memory default `RollupData`); call
   `select_rollup_members`; jsonify `{"mode": rollup.source_mode or "round", "answers":
   [{"name": p.name, "solution": p.solution} for p in members]}`. No writes.
4. If step 3 is more than ~a few lines, factor the assembly into `data.get_rollup_answers(...)` and
   keep the route thin (matches the codebase's data.py/api.py split).
- **Acceptance:** `tests/test_select_rollup_members.py`, `tests/test_rollup_answers_api.py`,
  `tests/test_rollup_source_mode.py`, `tests/test_refresh_meta.py` all pass; full suite green.
- Commit: `M7.5 phase 2: rollup members selector + read-only answers endpoint`.

## Phase 3 — Frontend: inline per-meta answers + copy (Sonnet 4.6)

1. `frontend/src/types.ts`: add `source_mode?: string` to `BoardGroup` (supplied by M7 Phase 6 —
   confirm it is present in the payload; if M7 did not add it, the panel can still fetch `mode` from
   the endpoint response). Add `RollupAnswers = { mode: string; answers: { name: string; solution: string | null }[] }`.
2. `frontend/src/api.ts`: add `useRollupAnswers(roundId: number, enabled: boolean)` →
   `GET /api/rollups/${roundId}/answers`, **lazy** (`enabled` only when the panel is open) so the
   board doesn't fire one request per meta on load. Copy the shape of the existing `useHunt` hook.
3. `frontend/src/components/BoardGroup.tsx` (extract `AggregatedAnswers.tsx` if it grows) — only for
   groups that are metas (have `meta_code`):
   - A "Copy answers" button + a reveal toggle (`useDisclosure` from `@mantine/hooks`).
   - On open, fetch via `useRollupAnswers`; render a compact `name | answer` list (reuse the
     `Text`/table patterns already in the component); show `mode: <mode> · <n> answers`.
   - Copy via `useClipboard` (`@mantine/hooks`) + `IconCopy` (`@tabler/icons-react`); copied text =
     rows with a non-empty solution joined as `name\tsolution`, one per line; on click,
     `notifications.show({ message: "Copied N answers" })` (pattern already used in `BoardGroup.tsx`).
- **Acceptance:** `cd frontend && npx tsc --noEmit` clean; full Python suite still green.
- Commit: `M7.5 phase 3: inline aggregated-answers view + copy-to-clipboard`.

---

## Verification (whole task)

- Full suite green at every phase boundary: `timeout 180 pipenv run python -m pytest tests/ -q`.
- Selector returns the correct id-set per mode with NO renderer await; M7's routing tests stay green.
- Endpoint returns `{mode, answers}`, 403 on a hidden hunt, 404 on unknown round, and writes nothing.
- Frontend typechecks: `cd frontend && npx tsc --noEmit`.
- **Manual end-to-end (needs M7 setters live):** `!source_mode <code> subtree`, solve a branch
  feeder, open that meta in the dashboard → the inline list shows the branch's solved answers
  (matching the sheet); "Copy answers" puts `name<TAB>answer` lines on the clipboard and shows the
  "Copied N answers" confirmation.

## Risks (carry forward)

1. **M7 not yet merged** — biggest risk. `_collect_rollup_puzzles`/`refresh_meta`/board-`source_mode`
   may differ from this spec's assumptions. Reconcile against the real code first; if the seam is
   absent, M7 isn't done — write `blocker.md` and stop rather than re-building M7's engine here.
2. **Web-test convention** — if the suite has no route-test precedent, test the assembly helper and
   keep the route thin; do not invent a Quart test-client harness without precedent.
3. **Read-path purity** — easy to accidentally `commit` a default rollup or trigger a refresh. The
   endpoint must never write. The "no rollup row" test guards this (`assert no commit`).
