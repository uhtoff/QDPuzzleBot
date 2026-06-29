# Codebase Correctness Review - 2026-06-29

> **ARCHIVED 2026-06-29 — all findings resolved.** This review ran against the
> `main` checkout; the fixes landed on `staging` (hundreds of commits ahead).
> Verified on `staging`:
> - #1 PATCH/DELETE evidence now hunt-visibility checked + archived-guarded + cross-hunt target rejected (`bot/web/actions.py:1608-1625`).
> - #2 Target puzzle deletion rejected at the service layer (`bot/core/puzzle_service.py:1119`), surfaced as `cannot_delete_target` in the web handler.
> - #3 Note-only edits and target changes now refresh old+new target contributions; delete runs `unlink_informs_for_evidence`.
> - #4 The stale `_organiser_hunt` patch is gone from `tests/test_edge_web.py`.
>
> Remaining (cosmetic, non-blocking): `PuzzleDrawer.tsx` still shows "Delete
> puzzle..." for target puzzles — backend rejects it, so it's UX-only.
>
> Original review preserved below for the record.

## Scope

Read-only review of the current `main` checkout, focused on correctness across the web dashboard, service layer, evidence board, tag groups, and destructive flows.

No code changes were made during the review.

## Findings

### 1. Evidence PATCH/DELETE are not hunt-visibility checked

Severity: High

`bot/web/actions.py:1633` fetches evidence by raw `item_id`, then updates or deletes it without using the visibility helpers used by the list/create paths. `PATCH` also lacks the archived-hunt guard present on `DELETE`.

Impact:

- A logged-in user who can guess an evidence id can mutate evidence outside their visible hunt.
- `PATCH` accepts any `target_puzzle_id`; `migrations/009_evidence.sql` only indexes the column and does not enforce same-hunt routing.
- This can cross-link evidence from one hunt to another hunt's puzzle.

Recommendation:

- Add an evidence-to-visible-hunt resolver equivalent to `_visible_hunt_for_puzzle`.
- Reject archived hunts for both PATCH and DELETE.
- Validate that any non-null `target_puzzle_id` belongs to the same hunt as the evidence item.

### 2. Target puzzles can be deleted as ordinary puzzles

Severity: High

`bot/web/actions.py:1144` does not reject `puzzle.is_target`, and `bot/core/puzzle_service.py:1151` deletes the puzzle row without deleting or repairing the owning `tag_group.target_puzzle_id`.

The arrange-board UI hides delete for target puzzles, but `frontend/src/components/PuzzleDrawer.tsx:348` exposes `Delete puzzle...` for any organiser-visible puzzle.

Impact:

- Deleting a meta/capstone/runaround target can leave a tag group whose target puzzle no longer exists.
- The broken group can then fail rollup refreshes and board rendering assumptions that expect `target_puzzle_id` to resolve.

Recommendation:

- Reject direct puzzle deletion for target puzzles at the service layer.
- Require users to delete the whole tag group or explicitly convert/demote the target first.
- Hide or disable drawer deletion for target puzzles as a secondary UI guard.

### 3. Evidence edits/deletes leave target contribution rollups stale

Severity: Medium

`bot/web/actions.py:1652` refreshes only the new target when `target_puzzle_id` changes. Note-only edits do not refresh the current target contribution table, and changing target does not refresh the old target after the evidence row is removed from it.

`bot/web/actions.py:1686` deletes the evidence item and refreshes only the Evidence tab, not the target contribution table. It also does not call the cleanup path that removes auto-added `informs` edges when no routed evidence remains.

Impact:

- Target contribution tables can keep old note content after note edits.
- Moving or deleting evidence can leave stale contribution rows on the old target.
- Auto-added `informs` edges can remain after the last routed evidence item is deleted.

Recommendation:

- On note edit, refresh the current target if present.
- On target change, refresh both old and new targets.
- On delete, refresh the old target and run the same `unlink_informs_for_evidence` cleanup used by reassignment.

### 4. Full pytest is red because `test_edge_web.py` patches a removed helper

Severity: Medium

`tests/test_edge_web.py:71` patches `bot.web.actions._organiser_hunt`, but that symbol no longer exists.

Impact:

- The full test suite reports failure even though the failing file never reaches the route assertions.
- This can hide real regressions in the web parity tests.

Recommendation:

- Remove the stale patch or replace it with the current visibility/permission helper seams used by `bot/web/actions.py`.
- Keep the existing route assertions; the fixture setup is the failing part.

## Verification Evidence

Commands run during the review:

```bash
timeout 60 pipenv run ruff check > /tmp/puzzbot-ruff.log 2>&1
timeout 120 npm --prefix frontend run build > /tmp/puzzbot-frontend-build.log 2>&1
timeout 180 pipenv run python -m pytest tests/test_evidence_service.py tests/test_evidence_commands.py tests/test_solve_equivalence.py -q > /tmp/puzzbot-focused-noedge-pytest.log 2>&1
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/puzzbot-pytest.log 2>&1
```

Results:

- Ruff passed: `All checks passed!`
- Frontend build passed: `tsc --noEmit && vite build`
- Focused pytest excluding stale `test_edge_web.py` passed: `89 passed in 0.39s`
- Full pytest failed only on `tests/test_edge_web.py` setup errors: `650 passed, 24 errors`
- Initial sandboxed pytest failed before collection because local MySQL access was blocked at import time; a DB-capable rerun was used for the results above.

## Current Git State At Review Time

`main` was clean and ahead of `origin/main` by 592 commits when the review began:

```text
## main...origin/main [ahead 592]
```
