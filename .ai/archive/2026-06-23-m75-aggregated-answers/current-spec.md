# Current Spec — M7.5: Aggregated-answers read view + copy-to-clipboard

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Branch: `feature/m7.5-aggregated-answers` (off `main` @ 28e8abd — M7 + M10 merged)
Target model — Phase 1 (tests): **Opus 4.8** · Phases 2–3 (implementation): **Sonnet 4.6**
Started: 2026-06-23
Status: in progress — Phase 1 (tests) DONE; next is Phase 2 (backend)

Source plan: `/home/russ/.claude/plans/can-we-plan-7-5-parallel-scroll.md` (background only;
this spec is authoritative). Roadmap: `docs/PLANS.md` M7 ("aggregated-answers view +
copy-to-clipboard in the SPA").

---

## Reconciled against real M7 code (READ — differs from the original handoff)

M7 is merged. The real engine shape differs from the original handoff's assumptions; this spec
is corrected to match `bot/core/puzzle_service.py` at HEAD:

- The render path method is **`_render_rollup(self, gsheet_cog, hunt_round, rollup, metapuzzle)`**
  (NOT `_collect_rollup_puzzles`). It selects membership by `rollup.source_mode` AND awaits the
  renderer inline (`add_metapuzzle_data` / `add_metametapuzzle_data`). See `puzzle_service.py:77`.
- The new selector signature is **`async def select_rollup_members(self, hunt_round, rollup)`** —
  `hunt` is NOT needed for selection (round/explicit read `PuzzleJsonDb.get_all_from_round`,
  subtree calls `self.collect_subtree_puzzles(hunt_round)` which uses `hunt_round.hunt_id`).
- **No Quart route-test precedent exists** in the suite (web tests call data-layer functions
  directly — see `tests/test_build_board.py`). So Phase 1 does NOT add an HTTP/endpoint test;
  the engine selector test carries the membership logic, and the endpoint stays thin glue
  mirroring the sibling untested routes (`rebuild_rollup`, `set_source_mode` in `actions.py`).
- M7 Phase 6 already surfaces `source_mode` on each board group (`build_board`, default `"round"`
  — confirmed by `tests/test_build_board.py::test_source_mode_*`). The inline control reads it.

## Before you start (executing model — READ THIS)

- **Execute only the current phase.** Commit at each phase boundary.
- **Required background skills:** before writing tests invoke `superpowers:test-driven-development`.
  On ANY unexpected failure invoke `superpowers:systematic-debugging` BEFORE changing anything.
  Before claiming a phase done invoke `superpowers:verification-before-completion` and paste the
  acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix,
  write `.ai/active/blocker.md` (what failed, exact output, diagnosis) and STOP.
- **A test failing because a method/attribute doesn't exist yet is the RIGHT reason.** An
  import error / missing column / unknown field means a wrong test assumption — fix the test.

## Test command (the ONLY runner — pipenv, repo root on sys.path)

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```
Filter during iteration:
```bash
timeout 120 pipenv run python -m pytest tests/test_select_rollup_members.py -q > /tmp/test.log 2>&1
```
A killed run exits 124; read `/tmp/test.log`. The final gate is the full `tests/ -q` run.
Frontend gate: `cd frontend && npx tsc --noEmit`.

---

## Goal

Add the read counterpart to M7: an inline per-meta dashboard surface that shows a rollup's
aggregated answers (in whatever membership the rollup's `source_mode` defines) with one-click
copy-to-clipboard, reusing M7's engine selection so the view matches what is written to the sheet.

## In scope

1. **Selector extraction** — `PuzzleService.select_rollup_members(self, hunt_round, rollup)`
   returning the ordered `list[PuzzleData]` per `source_mode` with NO Google-Sheet side-effects;
   refactor `_render_rollup` to call it then await the renderer.
2. **Read endpoint** — `GET /api/rollups/<int:round_id>/answers` (read-only, auth-gated) returning
   `{"mode": <source_mode>, "answers": [{"name", "solution"}, ...]}`.
3. **Frontend** — a lazy `useRollupAnswers` query hook + an inline per-meta panel on `BoardGroup`
   with a name+answer list and a "Copy answers" button (copies `name\tsolution`, one per line).

## Out of scope (do NOT do)

- **No writers.** `source_mode`/nest/pool setters are M7 (already shipped). Do not touch them.
- **No top-level "Answers" view** or cross-hunt harvesting — inline per-meta only.
- **Do NOT create a rollup row on the read path**, and **do NOT call `refresh_meta`** from the
  endpoint. Reads never write.
- **Do NOT change** the renderers (`add_metapuzzle_data`/`add_metametapuzzle_data`) or M7's
  source_mode routing behaviour — only EXTRACT the selection half so both paths share it.

## Relevant files

- `bot/core/puzzle_service.py` — add `select_rollup_members(hunt_round, rollup)`; refactor
  `_render_rollup` (~76-102) to reuse it. `collect_subtree_puzzles` already exists (~247). **(write)**
- `bot/web/api.py` — add `GET /api/rollups/<round_id>/answers` (read blueprint, `@login_required`).
  Resolve round→hunt + visibility (reuse `data.get_hunt_for_round` + `hunt_visible`, mirroring
  `actions._visible_hunt_for_round`). **(write)**
- `bot/web/data.py` — only if a small round→hunt or serialization helper reads cleaner here. **(maybe)**
- `bot/web/actions.py` — READ ONLY: mirror `rebuild_rollup`/`_visible_hunt_for_round` patterns.
- `bot/store/rollup_data.py` — `RollupData(source_mode, edge_type_filter)`. **(read)**
- `bot/store/puzzle_data.py` — `PuzzleData(name, solution, tags, tag_edges)`. **(read)**
- `tests/test_refresh_meta.py` — the seam (`_svc_with_sheets`, `_patched`). **(read)**
- `tests/test_rollup_source_mode.py`, `tests/test_refresh_meta.py` — M7 routing tests; must stay
  green after the `_render_rollup` refactor. **(read)**
- `tests/test_select_rollup_members.py` — Phase 1 tests (DONE). **(written)**
- `frontend/src/types.ts`, `frontend/src/api.ts`,
  `frontend/src/components/BoardGroup.tsx` (+ optional `AggregatedAnswers.tsx`). **(write)**

## Constraints

- Logic in `bot/core/` (the selector); the read edge in `bot/web/` is thin.
- **Signature changes:** `select_rollup_members` is NEW. When refactoring `_render_rollup`,
  `grep -rn "_render_rollup\|select_rollup_members" bot/ tests/` and keep M7's tests green.
- **Read-path purity:** the endpoint performs ZERO writes — no `commit`, no `refresh_meta`, no
  `_find_or_create_rollup`. Load via `RollupsJsonDb.get_by_attr(round_id=...)`; if None, build an
  in-memory `RollupData(round_id=round_id)` (default `source_mode="round"`) — do NOT persist it.

## Test constraints (anti-patterns — inlined)

- **Reuse the exact seams; invent none.** Copy `_svc_with_sheets`/`_patched` module-local (already
  done in `test_select_rollup_members.py`); import `make_puzzle`/`make_round` from `tests.conftest`.
- **NEVER write a loop that resolves state "until" a condition.** STOP and write `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a step says to.
- **Confirm every attribute exists before use:** `RollupData.source_mode`/`.edge_type_filter`;
  `PuzzleData.name`/`.solution`/`.tag_edges`; `RoundData.parent_group_id`. `tag_edges.get(rid,
  'feeds-meta')` is the M7 read shape.
- **Assert the exact puzzle-id set per mode** (not just "a list came back").
- **A test file defines or imports every helper it calls** (no cross-test-file helpers — the
  parallel gate shards by file).
- **No new Quart test-client harness** — there is no route-test precedent; the selector test is
  the coverage, the route stays thin glue.
- **Wrap every run in `timeout`, write to `/tmp/test.log`, single statement.**

---

## Phase 1 — Failing tests (Opus 4.8). ✅ DONE

`tests/test_select_rollup_members.py` written: 5 tests pinning the id-set per mode (round,
round-edge-filter, subtree, explicit, falsy→round) and asserting neither renderer is awaited.

- **Acceptance (met):** full `tests/ -q` run = **5 failed (all `AttributeError: ... no attribute
  'select_rollup_members'` — the right reason), 147 passed**; new file collects cleanly under the
  non-filtered gate.
- Commit: `M7.5 phase 1: failing tests for rollup members selector`.

---

## Phase 2 — Backend: selector extraction + read endpoint (Sonnet 4.6)

1. In `bot/core/puzzle_service.py` add
   `async def select_rollup_members(self, hunt_round, rollup) -> list[PuzzleData]` that branches on
   `rollup.source_mode` (falsy → `"round"`), with NO gsheet calls. Lift the selection lines
   verbatim from `_render_rollup` (~82-101):
   - `round`: `feeders = PuzzleJsonDb.get_all_from_round(hunt_round.id)` filtered to
     `p.tag_edges.get(hunt_round.id, "feeds-meta") == (rollup.edge_type_filter or "feeds-meta")`.
   - `subtree`: `await self.collect_subtree_puzzles(hunt_round)`.
   - `explicit`: `get_all_from_round(...)` filtered to
     `p.tag_edges.get(hunt_round.id) == "aggregates-into"`.
2. Refactor `_render_rollup` to `members = await self.select_rollup_members(hunt_round, rollup)`
   then await the renderer it used before (`add_metametapuzzle_data` for subtree, else
   `add_metapuzzle_data`). M7's tests (`test_rollup_source_mode.py`, `test_refresh_meta.py`) must
   stay green — they are the guard that the extraction preserved behaviour.
3. Add the read route in `bot/web/api.py` (`@api_bp.route("/rollups/<int:round_id>/answers")`,
   `@login_required`): resolve round→hunt + visibility (use `data.get_hunt_for_round` + the
   `resolve_membership`/`hunt_visible` gate, mirroring `actions._visible_hunt_for_round`); load the
   rollup via `RollupsJsonDb.get_by_attr(round_id=round_id)` (None → in-memory
   `RollupData(round_id=round_id)`); resolve the round via `RoundJsonDb.get_by_attr(id=round_id)`
   (404 if missing); borrow the service (`current_app.bot.get_cog("Puzzles").service`, 503 if
   unavailable), call `select_rollup_members(round, rollup)`; jsonify
   `{"mode": rollup.source_mode or "round", "answers": [{"name": p.name, "solution": p.solution}
   for p in members]}`. No writes.
- **Acceptance:** `tests/test_select_rollup_members.py` passes (5 green); `test_rollup_source_mode.py`
  + `test_refresh_meta.py` stay green; full suite green.
- Commit: `M7.5 phase 2: rollup members selector + read-only answers endpoint`.

## Phase 3 — Frontend: inline per-meta answers + copy (Sonnet 4.6)

1. `frontend/src/types.ts`: confirm `source_mode?: string` is on `BoardGroup` (M7 Phase 6 added it;
   add if absent). Add `RollupAnswers = { mode: string; answers: { name: string; solution: string | null }[] }`.
2. `frontend/src/api.ts`: `useRollupAnswers(roundId: number, enabled: boolean)` →
   `GET /api/rollups/${roundId}/answers`, **lazy** (`enabled` only when the panel is open). Copy
   the `useHunt` hook shape.
3. `frontend/src/components/BoardGroup.tsx` (extract `AggregatedAnswers.tsx` if it grows) — only
   for groups that are metas (have `meta_code`):
   - "Copy answers" button + a reveal toggle (`useDisclosure` from `@mantine/hooks`).
   - On open, fetch via `useRollupAnswers`; render a compact `name | answer` list; show
     `mode: <mode> · <n> answers`.
   - Copy via `useClipboard` (`@mantine/hooks`) + `IconCopy`; copied text = rows with a non-empty
     solution joined as `name\tsolution`, one per line; `notifications.show({message: "Copied N
     answers"})` (pattern already in `BoardGroup.tsx`).
- **Acceptance:** `cd frontend && npx tsc --noEmit` clean; full Python suite still green.
- Commit: `M7.5 phase 3: inline aggregated-answers view + copy-to-clipboard`.

---

## Verification (whole task)

- Full suite green every phase boundary: `timeout 180 pipenv run python -m pytest tests/ -q`.
- Selector returns the correct id-set per mode with NO renderer await; M7's routing tests green.
- Endpoint returns `{mode, answers}`, 403 on a hidden hunt, 404 on unknown round, writes nothing.
- Frontend typechecks: `cd frontend && npx tsc --noEmit`.
- **Manual e2e:** `!source_mode <code> subtree`, solve a branch feeder, open that meta in the
  dashboard → inline list shows the branch's solved answers (matching the sheet); "Copy answers"
  puts `name<TAB>answer` lines on the clipboard with the "Copied N answers" confirmation.

## Risks

1. **Read-path purity** — easy to accidentally `commit` a default rollup or trigger a refresh. The
   endpoint must never write. (No "no-rollup" route test exists, so be deliberate in code review.)
2. **`_render_rollup` refactor regressing M7** — the existing M7 routing tests are the guard; if
   any goes red after the extraction, the lifted selection diverged from the original — diff
   `_render_rollup` before/after and reconcile.
