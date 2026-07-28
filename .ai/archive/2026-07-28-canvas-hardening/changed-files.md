# Changed Files

Canvas hardening tranche, branch `working-2`, base `0048d9f`.

## Git status

Clean at the end of Phase 5 (all three mutation probes restored by editing back).

Commits, in order:

| Phase | Commit | Subject |
|---|---|---|
| — | `ee10ffc` | docs: land the canvas hardening tranche handoff |
| 1 | `9a1fed9` | test: pin the canvas hardening tranche (failing) |
| 2 | `089a2f6` | fix: reject non-permutation reorders, and set nosniff on attachments |
| 3 | `abe50bb` | feat: cap concurrent tiling, and add a retile route for stuck canvases |
| 4 | `7592e30` | refactor(frontend): extract canvasMath, stabilise deps, add a retile button |

## Files changed

Backend:

- `bot/core/puzzle_images.py` — `BadReorder`; `reorder` now requires a full permutation.
- `bot/core/media_tiling.py` — `MAX_CONCURRENT_TILING = 2`, `_tiling_slots` semaphore held
  only around the `run_in_executor` call.
- `bot/web/actions.py` — `BadReorder` → 400 on the reorder route; new `retile_canvas` route.
- `bot/web/api.py` — `attachment_bytes` sets `X-Content-Type-Options: nosniff`.
- `bot/web/data.py` — comment only; the blocking sleep was verified safe, not changed.

Frontend:

- `frontend/src/lib/canvasMath.ts` (new) + `canvasMath.test.ts` (new) — `clampScale`,
  `zoomAt`, `tileRect`, `normPoint`.
- `frontend/src/components/CanvasView.tsx` — uses canvasMath; new `uid` prop; retile button
  in the pending branch and (for `failed`) in the toolbar.
- `frontend/src/components/MarkupCanvas.tsx` — `toNorm` delegates to `normPoint`; DOM
  structure and the `getBoundingClientRect` read untouched.
- `frontend/src/components/EvidencePanel.tsx`, `PuzzleImages.tsx` — `useCallback` deps on
  `mutateAsync` instead of the mutation objects.
- `frontend/src/api.ts` — `useRetileCanvas`.
- `frontend/src/components/CanvasPanel.tsx` — passes `uid` to `CanvasView`.
- `frontend/package.json` + lockfile — Vitest, `"test": "vitest run"`.

Repo:

- `.gitignore` — `!frontend/src/lib/`, because the Python packaging `lib/` rule was
  swallowing the frontend source directory.

## Tests run

All green at `7592e30` plus the restored mutations:

- `pipenv run python -m pytest tests/ -q` → **1525 passed**.
- `pipenv run ruff check` → All checks passed.
- `pipenv run basedpyright bot/` → 0 errors, 0 warnings.
- `cd frontend && npm test` → 6 passed.
- `cd frontend && npm run build` (runs `tsc --noEmit`) → clean.

The spec predicted a 1508 baseline; the actual baseline was 1513 (1525 − 12 new backend
tests). The spec's figure was simply stale — no tests were removed or skipped.

## Tests not run

No component-level runner exists, so these are **typecheck + inspection only**:

- the `mutateAsync` dependency fixes in `EvidencePanel` / `PuzzleImages`
- the "Regenerate tiles" button and its wiring

## Mutation proofs

Each break was applied, observed red for the right reason, then edited back:

- permutation guard removed → 3 red (both service tests + the route's 400).
- `async with _tiling_slots` removed → `assert 3 == 2`, i.e. three concurrent builds.
- `tileRect` width computed independently → `expected 3072 to be 3071`, a one-pixel seam.

## Current risks

- The tiling semaphore is per-process. The bot and the web app tile in the same process
  today, so the cap holds; it would not bound a second process.
- `TestCanvasRetile::test_an_unknown_canvas_is_a_404` was written before the route existed
  and so passed trivially in Phase 1. It is load-bearing now that the route answers POST.
