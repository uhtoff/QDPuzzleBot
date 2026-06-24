# Current Spec — Post-parity duplication & smell cleanup

> Authoritative task spec. The ordered execution plan is the numbered **Phases** below.
> All `file:line` refs are **as of `e01a427`** (working-2 fast-forwarded to main on 2026-06-24);
> they will drift as edits land — re-grep before trusting a line number.

## Goal

Clean up the duplication, divergence, and smells left by the ~24h web↔bang parity push,
without redesigning anything. Restore the project invariant: **business logic lives once in
`bot/core/puzzle_service.py` and both surfaces (Discord bang-commands in `bot/cogs/puzzles.py`,
web in `bot/web/actions.py`) call it.** Fix the latent correctness bugs the pace exposed.

## In scope

- 🔴 Correctness bugs (Phase 1)
- 🟠 Service-layer dedup of bang↔web residue (Phases 2–3)
- ⚪ God-cog cleanups (Phase 4)
- 🟡 Frontend UI-wiring dedup (Phase 5)
- ⚪ Test-fixture consolidation (Phase 6)

## Out of scope (decided 2026-06-24 — tracked as future work, do NOT do here)

- **Extracting `archive_channel`'s 82-line orchestration** (`puzzles.py:2440-2521`) into the service.
- **Unifying the two frontend optimistic-update models** (React-Query optimistic solve in
  `api.ts:242-264` vs the hand-rolled `pendingIds` retag in `DragBoard.tsx:267-294`) and the
  inline anonymous cache type at `api.ts:244-246`.
- Anything touching DB schema, enum backing values, or migration history (none required here).

## Settled decisions (from the thrash-out)

1. **Answer casing → uppercase everywhere.** The canonical `.strip().upper()` normalization moves
   into the service solve-family methods; both surfaces inherit it. (Discord already uppercased;
   web did not — web was the divergent one.)
2. **Confirm UX → light modal for reversible, typed for deletes.** Add a non-typed `<ConfirmModal>`
   sibling of `TypedConfirmModal`; route the reversible `window.confirm` calls through it; keep
   `TypedConfirmModal` (type-to-confirm) only for hard deletes.
3. **Note attribution stays surface-specific** via an `attribution` param to `service.add_note(...)`
   — Discord passes the jump_url form, web passes the `… (web)` form. No canonical format imposed.
4. **Solved-state truthiness → normalize to the boolean `puzzle.solved`** across the solve family.

## Relevant files

- `bot/cogs/puzzles.py` (god-cog), `bot/cogs/puzzles_gsheet.py`
- `bot/core/puzzle_service.py`, `bot/core/constants.py`
- `bot/web/actions.py`, `bot/web/data.py`
- `frontend/src/api.ts`, `frontend/src/util.ts`, `frontend/src/types.ts`
- `frontend/src/components/{DragBoard,BoardGroup,PuzzleTable,PuzzleEditCells,ReconcilePanel,
  CreateControls,TypedConfirmModal,EvidencePanel}.tsx`
- `tests/conftest.py` + the per-area test files

## Constraints

- TDD: where a service method is added/changed, write the failing test first (matches repo style —
  see existing `tests/test_solve_equivalence.py`, `test_*_service.py`). Mechanical cleanups
  (dead-code removal, frontend extraction) don't need a new test but must keep the suite green.
- Before deleting any "dead" code, **prove it dead** (`grep -rn` for callers across `bot/`,
  `tests/`, `frontend/`) and note the evidence in the commit.
- Keep diffs narrow and per-phase; one coherent commit per phase (sub-phases may be separate commits).
- Do not add new `web_*` twins. Do not redefine constants — import from `bot/core/constants.py`.
- Presentation stays on each surface (embeds/toasts); only business logic moves to the service.

## Verification commands

- Backend tests (focused first, full at phase boundaries):
  `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
- Lint: `pipenv run ruff check` (review F401 on `bot/` before any `--fix`; several are load-bearing).
- Frontend (Phase 5): `cd frontend && npm run build` (`tsc --noEmit && vite build` — no separate
  test/lint runner exists).

---

# Phases (ordered execution plan)

## Phase 1 — 🔴 Correctness bugs (do first; each independently committable)

1a. **`send_not_puzzle_channel` crash guard** — `puzzles.py:1963` does `ctx.channel.category.name`;
    `.category` is `None` in threads/DMs/uncategorised channels → `AttributeError` in the shared
    error path (14 call sites). **Resolved (Q2):** the existing `== get_solved_puzzle_category("")`
    solved-branch is vestigial (`SOLVED_PUZZLES_CATEGORY="solved"`, so it compares against `"-solved"`
    and never matches). Fix the crash AND make the intent work via a suffix check — no hunt name
    needed:
    ```python
    category = ctx.channel.category if ctx.channel else None
    if category is not None and category.name.endswith(f"-{self.SOLVED_PUZZLES_CATEGORY}"):
        await ctx.send(":x: This puzzle appears to already be solved")
    else:
        await ctx.send(":x: This does not appear to be a puzzle channel")
    ```

1b. **`convert_to_meta` / `convert_to_feeder` error handling** — `puzzles.py:1426-1429` swallows
    every non-`already_meta` `RuntimeError` with a bare `return` (silent no-op); the `already_meta`
    arm is dead (line 1418 already guards it). `convert_to_feeder` (1446-1461) has no handler and
    tracebacks. Surface the error to the user on both; make them consistent.

1c. **`!unsolve` delegate to the service** — `puzzles.py:2347-2396` hand-rolls unsolve and has
    diverged from `service.unsolve` (`puzzle_service.py:676-726`, which the web path uses at
    `actions.py:1264`): the service refreshes ALL tagged metas, re-rolls the parent minimeta, and
    unarchives the mini thread; the bang path does none. Replace the body with
    `await self.service.unsolve(...)` (catch `SheetRefreshError` via `_warn_stale_sheets`); keep
    only the embed / `move_to_bottom` / `info(update=True)` presentation.
    *Test:* extend `test_solve_equivalence.py` (or add a cog test) asserting bang `!unsolve` on a
    mini and on a multi-meta puzzle hits the same service path as web.

1d. **Solution casing → service** — move `.strip().upper()` into `service.solve` / `add_partial` /
    `change_solution` / `add_solution` (`puzzle_service.py` ~618). Bang `_normalize_solution`
    (`puzzles.py:2156-2157`) becomes a thin caller or is dropped; web (`actions.py:330`) stops
    relying on its own `.strip()`-only path. *Test:* extend `test_solve_equivalence.py` — solving
    `"abc"` stores `"ABC"` from BOTH surfaces.

## Phase 2 — 🟠 Service-layer dedup (bang↔web residue)

2a. **`set_source_mode` service method** — both bang (`puzzles.py:1253-1257`) and web
    (`actions.py:1030-1042`) inline the same "find rollup by round_id → create if missing → set
    source_mode → commit → refresh_meta" upsert; the validation tuple is duplicated too
    (`1242`/`1019`). Add `PuzzleService.set_source_mode(hunt, meta_round, mode)` reusing the
    existing `_find_or_create_rollup` (`:344`); move the `("round","subtree","explicit")` tuple to
    `constants.py`; both callers delegate. *Test first.*

2b. **`resolve_evidence_names` helper** — `puzzle_service.py:1310-1311` and `data.py:352-356`
    independently build the same `{pid: name}` map with `"Puzzle {pid}"` fallback; the service copy
    calls `get_by_attr(id=pid)` twice per id (the `or None` is a no-op). Extract one pure helper in
    `bot/core/` used by both; single `get_by_attr` per id.

2c. **`service.add_note` / `service.erase_note`** — note mutation is hand-rolled on both edges
    (`puzzles.py:2096-2097` / `2133-2134`; `actions.py:158-159` / `181-182`). Add service methods
    taking an `attribution` param (surface-specific string — see Decision 3); both delegate.
    *Test first.*

2d. **`service.update_puzzle_field` (status/priority/type)** — same validate→setattr→commit shape
    duplicated (`puzzles.py:2021-2028` / `2058-2062` / `2078-2084` vs `actions.py:209-226`). Add one
    service setter validating against `constants.py`; both delegate. *Test first.*

2e. **Collapse `web_create_puzzle` / `web_create_round` twins** — the explicit M3.5 debt
    (`puzzles.py:740`, `:759`). Bang creates already call the service directly (`:542`, `:1355`);
    web routes through the twins, which re-implement welcome-timing (`notifier=None` then
    `_web_send_welcome`) and an extra meta-refresh loop (`:755-756`). Add a `defer_welcome` (or
    explicit-notifier) option to `service.create_round`/`create_puzzle`; have web call the service
    directly + send welcome after; delete the twins.
    **Resolved (Q1):** the extra meta-refresh loop in `web_create_puzzle` (`:755-756`) is **redundant**
    — `service.create_puzzle` already refreshes every assigned tag's meta internally
    (`puzzle_service.py:1201-1208`). Just **drop the loop**; nothing moves into the service. The
    `web_create_round` welcome-deferral (`notifier=None` → `_web_send_welcome` once `meta_code` is set)
    IS genuine and web-specific — **preserve it** (web does `service.create_round(notifier=None)` then
    sends the welcome). So 2e is: delete the two wrapper shells, web calls the service directly +
    keeps the round welcome-deferral two-step.

2f. **`service.remove_sheet_by_index`** — bang (`puzzles.py:945`) reaches into the private
    `service._delete_additional_sheet` by index with its own bounds check; web uses the public
    `service.remove_sheet(...)` by id (`actions.py:304`), which carries the `sheet_not_found` guard.
    Add a public by-index method (or have bang resolve id → call the public one). *Test first.*

## Phase 3 — 🟠 Lower-frequency dedup (medium risk — keep conservative) — ⏸️ DEFERRED (not done; user-approved follow-up at 2026-06-24 merge)

3a. **Duplicate `_safe_delete_channel`** — near-identical copies in `puzzle_service.py:776` and
    `puzzles.py:2657`. The cog copy is reachable only via the legacy `delete_hunt` →
    `_delete_puzzles` path. Confirm those cog helpers are otherwise dead, then have `delete_hunt`
    use the service copy and remove the cog's `_safe_delete_channel` (+ dead
    `delete_puzzle_data`/`_delete_puzzles` if proven unused). If the deadness proof is shaky, leave
    it and just note it.

3b. **`sync_meta` tag-mutation** — `puzzles.py:1124-1127` copies `move_to_meta`'s tag rule
    (`puzzle_service.py:463-483`). Factor the tag-only mutation into a shared seam both can call
    (`sync_meta` deliberately skips the channel move). Low blast radius (bang-internal).

## Phase 4 — ⚪ God-cog cleanups (`puzzles.py`, `puzzles_gsheet.py`)

4a. **Strip leftover diagnostics** — `print()` on every command in `cog_before_invoke`
    (`puzzles.py:164`); `puzzles_gsheet.py:48` (`on_ready` print). Delete or downgrade to
    `logger.debug`. **Resolved (Q3):** delete the `!read` command (`puzzles_gsheet.py:50-52`)
    outright — it's an ungated `@commands.command()` whose whole body `print()`s a live Sheets
    `.get()` to stdout, with zero references anywhere in `bot/`/`docs/`/`tests/`.

4b. **Dead branch in `_round_impl`** — the convert-to-meta special case (`puzzles.py:1330-1346`) is
    unreachable since convert now delegates to `service.convert_to_meta`; the docstring (1304-1311)
    is stale. Confirm no caller passes `make_category=False`/`make_puzzle=False`, then delete the
    block, fix the docstring, and drop the now-unused params.

4c. **Consolidate the four "not a puzzle channel" guards** — the gate exists in 4 inconsistent forms
    with divergent wording AND conditions (helper `send_not_puzzle_channel` 14 sites; inline
    `channel_type=="Hunt"` at `1146/1168/1190/1275`; lowercase variant at `2019/2030/2044`;
    `"doesn't appear to be a puzzle."` at `1416/1448`). Funnel through the (now-fixed)
    `send_not_puzzle_channel` or a `require_puzzle(ctx)` helper. Extract `resolve_meta_round_or_reply`
    for the 5× repeated meta-code lookup + reply.

4d. **Normalize solved-state checks** — replace the three idioms (`.solved`,
    `not .solved and not .solution`, `== 0`) across the solve family with the single boolean
    `puzzle.solved` (Decision 4).

## Phase 5 — 🟡 Frontend dedup

5a. **Shared notify helpers** in `util.ts`: `notifyError(e)`, `notifySuccess(msg)`,
    `showSheetWarning(data)`. Replace the 12 verbatim red-toast `onError` handlers and the 4 pasted
    `sheet_warning` blocks; fix the uneven coverage (6 `api.ts` endpoints return `sheet_warning` but
    only some surface it — e.g. rename never does). Consider a default `onError` on the api.ts
    mutation hooks.

5b. **Extract `<PuzzleRowActions>` + `useMiniExpansion()`** — the row-actions + minis-expansion cell
    is duplicated near-verbatim (`BoardGroup.tsx:316-353` ≈ `PuzzleTable.tsx:218-259`) and already
    drifting (`IconCircleCheck` 16 vs 17, `IconNote` 15 vs 16, `gap` 2 vs 4); both redeclare
    `expandedMinis`/`toggleMinis`/`activityFor`. Extract a shared component + hook; reuse in both.
    Lift the repeated `SelectOption` type to `types.ts` and the `META` badge to a `<MetaBadge>`.

5c. **Light `<ConfirmModal>`** (non-typed) sibling of `TypedConfirmModal`; route the reversible
    `window.confirm` calls (make-minimeta, convert-to-meta, clear-all-metas, multi-meta move —
    `DragBoard.tsx:157,167,368,382`) through it. Keep `TypedConfirmModal` for hard deletes
    (Decision 2). Collapse the repeated `mutate → green/yellow/red toast → close modal` handler
    skeleton (`DragBoard.tsx:296-343`, `BoardGroup.tsx:72-129`) into a small `runMutationWithToasts`.

## Phase 6 — ⚪ Test-fixture consolidation

6a. **Add shared factories to `tests/conftest.py`**: `make_hunt`, `make_service` (`PuzzleService` +
    mocked GoogleSheets cog), `make_gsheet_cog`, `make_cog`, `make_ctx`. Retire the ~15 ad-hoc
    service/cog factories, the 17 local hunt-builders, and the byte-identical `_make_cog`/`_make_ctx`
    in `test_evidence_commands.py` / `test_minimeta_commands.py`.

6b. **Migrate `test_reconcile.py`** off its `SimpleNamespace` hunt onto the real `make_hunt` (so it
    exercises real `HuntData` attribute presence).

## Verification (final, before wrap)

- `timeout 180 pipenv run python -m pytest tests/ -q` — full suite green.
- `pipenv run ruff check` — clean.
- `cd frontend && npm run build` — type-check + build clean.
- Re-grep for: leftover `web_*` create twins, any remaining `window.confirm`, duplicate
  `_safe_delete_channel`, `notifications.show({ ... color: "red" })` raw handlers.

## Rollback / recovery notes

- Each phase is an independent commit on `working-2`; revert a single phase commit if it regresses.
- Phases 1, 5, 6 are independent of each other; 2 and 3 are independent per sub-item. 1c and 1d
  share `test_solve_equivalence.py` — sequence them.
- Phase 2e is fully specified (Q1 resolved): drop the redundant web meta-refresh loop, preserve the
  round welcome-deferral. No open investigation remains.
