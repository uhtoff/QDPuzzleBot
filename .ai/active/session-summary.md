# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2 (spec authored in the main checkout)
Started: 2026-07-28
Status: ready for handoff — docs: land the canvas hardening tranche handoff @ ee10ffc

## Goal

Canvas hardening tranche: fix the four leftover canvas-review findings, cap/repair tiling
(semaphore + retile route/button), and put the pure canvas coordinate maths under Vitest.
Full phased plan: `.ai/active/current-spec.md` (authoritative).

## Context the executing agent needs

- **Integration target is `staging`, not `main`.** Main is pulled from staging for live.
  Staging is currently a strict ancestor of main — fast-forward it to main first (spec
  "Branching" section has the exact sequence). Never commit to main.
- **#24 (event-loop-blocking Sheets calls) is ALREADY FIXED** (`8319a31` + `f8fcfe9`,
  merge `90125c0`). It was listed in this tranche's original brief; it is out of scope.
  If you find yourself in `puzzles_gsheet.py` or `bot/utils/gsheets.py`, you are lost.
- The four findings come from the "canvas review" backlog recorded in
  `.ai/archive/2026-07-28-canvas-review-fixes/session-summary.md` (background only).
- Model tiers (from AGENTS.md): Phase 1 test design on **Opus 4.8**, Phases 2–5 on
  **Sonnet 4.6**.
- Frontend has NO component-level test runner; Vitest here covers only the new pure
  `canvasMath` module. The retile button and useCallback fixes are typecheck +
  inspection only — report them as such, don't imply test coverage.
- `bot/web/data.py` item is a *verification*, not a fix: prove the blocking sleep only
  runs on `to_thread` workers (grep evidence in the phase report), then pin with a comment.

## Final decisions

- Reorder: reject non-permutations with a new `BadReorder` → HTTP 400 (foreign ids keep
  the existing `ImageNotFound` → 404).
- Tiling cap: `MAX_CONCURRENT_TILING = 2`, module-level `asyncio.Semaphore`, held only
  around the `run_in_executor` call.
- Retile: `POST /api/canvases/<id>/retile`, allowed only from `pending`/`failed` (409
  `not_retileable` otherwise), NOT organiser-gated (same bar as ocr-preview), reuses
  `api.py`'s `_tile_and_push` + `_tiling_tasks` strong-ref idiom, returns 202.
- Vitest: pure-function extraction to `frontend/src/lib/canvasMath.ts` only; all DOM
  reads stay in the components; `MarkupCanvas` DOM structure untouched.
- useCallback deps: destructure `.mutateAsync` (referentially stable in react-query) and
  use it in body + deps.

## Rejected approaches

- Making `reorder` tolerate subsets by appending unlisted rows: the drag UI always sends
  the full list, so lenient semantics would only ever mask a client bug.
- dzsave `overlap=1` for tile seams: already proven a dead end (google layout ignores it)
  — the client-side grid snap is the fix, which is why it's the thing getting pinned by
  Vitest.
- Doing #24: already shipped.

## Progress

- Branching done: `staging` fast-forwarded to `main` (0048d9f); `working-2` was already level.
- **Phase 1 DONE** — 11 red backend tests + Vitest with a red `canvasMath` suite.
- Two env/repo facts found while running Phase 1, both fixed here:
  - the worktree venv was missing `pytesseract` (declared in the Pipfile), so the two
    pre-existing `TestCanvasOcrPreview` tests failed at HEAD. `pipenv sync` fixed it —
    no tracked-file change. Re-sync if a fresh worktree shows the same two failures.
  - `.gitignore`'s Python `lib/` rule also swallowed `frontend/src/lib/`, the path the
    spec pins for `canvasMath.ts`. Added a scoped `!frontend/src/lib/` negation rather
    than committing source with `git add -f`.

## Next steps

1. Phases 2–5 in order, committing per phase, gates per spec.
2. Merge `working-2` → `staging`; user pulls staging → main for live later.

## What NOT to touch

`puzzles_gsheet.py` / `gsheets.py`; `tests/conftest.py`; existing tests; migrations;
`MarkupCanvas` DOM structure; anything under "Out of scope" in the spec.

## If you get stuck

Diagnose with `superpowers:systematic-debugging` first. If an acceptance command fails
twice without a clear understood fix, write `.ai/active/blocker.md` (what ran, what you
saw, best hypothesis) and STOP.
