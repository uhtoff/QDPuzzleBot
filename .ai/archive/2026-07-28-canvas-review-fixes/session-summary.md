# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-07-28
Status: ready for handoff — docs: mark the six canvas-review bugs as landed @ 7b9fb89

## This task's outcome

All six canvas-epic-review bugs fixed across three commits:

- `303becf` — Phase 1: 5 new failing backend tests (TDD red).
- `2a734da` — Phase 2: `!clue` hunt guard, cross-hunt media guard on `POST /api/evidence`,
  resolved crop id forwarded to `sync_region_image` in `update_region`.
- `98c1f99` — Phase 3: paste double-fire guard, `<CanvasView>` re-keyed per canvas, `MarkupCanvas`
  surfaces mutation failures via `notifyError`.

Final verification: full backend suite 1513 passed (baseline 1508 + 5 new), `ruff check` clean,
`basedpyright bot/` 0/0/0, `frontend: npx tsc --noEmit` clean, `npm run build` clean.

**Frontend fixes are typecheck-verified only — no browser confirmation done.** Manual checks
still needed before calling this fully done:
- Paste an image into the puzzle-images drawer AND into an evidence-panel cell — confirm only
  one upload fires each time (previously both could fire on one paste).
- Switch between two canvases in the same hunt — confirm zoom/pan/offset resets instead of
  carrying over from the previous canvas.
- Trigger a markup save failure (e.g. draw on an archived hunt) — confirm a red `notifyError`
  toast appears instead of the ink silently vanishing on refetch.

## Deviation from the spec, user-approved

Phase 2's cross-hunt media guard broke a **pre-existing** test,
`test_add_evidence_web.py::TestSourcelessAndImageEvidence::test_accepts_an_image_with_no_note`,
which posted an unmocked `media_id: 7` — before the fix, `add_evidence` never looked media up,
so an arbitrary id passed straight through. The spec said "do not change any existing test";
this was a genuine spec gap (the guard's whole point is to query media that didn't need mocking
before). Asked the user via AskUserQuestion; they approved the minimal fix — added a
`patch("bot.store.MediaJsonDb", ...)` returning a hunt-10 row for id 7, in the same commit as
the production fix (`2a734da`). No other spec deviations.

## Prior context (Phase 1 test-writing)

Each of the 3 backend test files, run individually, confirmed 5 new tests fail for the
documented reasons, zero collection errors, zero pre-existing regressions (13/17/34 pre-existing
tests still passed at that point). One test-setup gap fixed in Phase 1 itself (not production
code): `test_clue_outside_a_hunt_sends_a_friendly_error` needed
`cog.service.refresh_evidence_safe = AsyncMock()` added — `make_cog()` only auto-mocks
`refresh_informs_targets`, so without it the test hit an unrelated `TypeError` in
`asyncio.create_task` before reaching the intended `assert_not_called()`.

## Goal

Fix the six verified bugs from the 2026-07-28 review of the canvas epic
(`dfe1fb5^..HEAD`, ~10.8k lines landed 2026-07-26 → 07-28: image evidence, hunt-level
sheets, tiled canvas + regions, OCR, markup, puzzle images). The spec in
`.ai/active/current-spec.md` is authoritative; this file is context and the review record.

## Context the executing agent needs

- Branch: `main`, clean at `4da3bb6`. The canvas epic was merged to main; the old
  "staging is the active branch" note no longer applies to this task.
- Backend suite baseline: **1508 passed** (`timeout 300 pipenv run python -m pytest tests/ -q`).
- There is NO frontend test runner — frontend gate is `npx tsc --noEmit` + `npm run build` only.
- The three backend bugs were each verified directly in source during the review, not just
  reported by a subagent. The three frontend bugs were verified structurally (the missing
  `key`, the never-reset `fitted` ref, the unguarded document paste listener, the
  `void commit*` calls).

## The review record (full findings, 2026-07-28)

The durable, complete write-up (including fine-as-is items and agent-report detail) is
`docs/reviews/2026-07-28-canvas-epic-review.md` — update its "Implementation status" line
when the six bugs land. The sections below are the working summary for this task.

### Verified bugs (= this task's scope)

1. `!clue` outside a hunt crashes — `bot/cogs/puzzles.py:1626` (`get_hunt` → None, guard
   dropped in the image-evidence rewrite; every touched sibling command checks).
2. `POST /api/evidence` missing the cross-hunt `media_id` guard — `bot/web/api.py:541`;
   `update_evidence` (`actions.py:2050`) holds and documents the same invariant.
3. `canvas.py:122` passes unresolved `media_id` to `sync_region_image` while the DB write
   resolves it — a relinked locates region with a suppressed re-crop drops off the new
   puzzle's image strip.
4. Double paste-fire: `PuzzleImages.tsx:159` document listener + `EvidencePanel.tsx:523`
   cell handler, no `defaultPrevented` check.
5. `CanvasView` not re-keyed per canvas (`CanvasPanel.tsx:138`; `fitted` ref at
   `CanvasView.tsx:96` never resets) — switching canvases inherits the old zoom/offset.
6. `MarkupCanvas` swallows mutation failures (`void commitStroke/commitText`, `deleteMark.mutate`
   with no `onError`) — ink silently vanishes on refetch instead of a `notifyError` toast.

### Backlog — duplication extractions (NOT this task; next quality pass)

Reported by review subagents; spot-checked but not all independently re-verified.
The recurring pattern: the 2nd/3rd copy *cites* the 1st in a comment and copies anyway.

Backend (worth doing first):
- `_load_visible_media()` — the media→hunt→visibility chain is written 4× (`api.py:229`,
  `:271`, `:319`; `actions._media_ctx` ~2154 is the good version).
- `media_store.validate_image_bytes()` — upload validation + byte-identical user strings
  duplicated (`media_store.py:87/91` vs `api.py:419/421`); also fixes OCR-preview accepting
  types outside `ALLOWED_IMAGE_TYPES`.
- `media_store.ingest()` — save-bytes+create-row exists 3× with 3 behaviours (`api.py:363`,
  `actions._crop_region_media:2223`, `cogs/puzzles._store_clue_attachment:1600`); the cog
  copy skips dimensions/tiling, so `!clue` images never tile and report 0×0.
- `_push_board` (`actions.py:2246`) duplicates pre-existing `_notify_board` (`actions.py:73`)
  with extra lookups; all ten actions.py call sites already hold uid+snowflake.
- `_MySQLBaseDb`: add `_exec/_fetch_one/_fetch_all`; fix base `delete()` to commit (four
  identical overrides exist only for that); ~25 copies of the 7-line cursor pattern.
- Cross-hunt guard helper `_require_hunt_media()` — 3 copies with 2 different error keys
  (after this task's fix, 4 copies).

Frontend:
- `useZoomPan` hook — ~60-70 lines of zoom/pan/wheel/ResizeObserver maths duplicated between
  `CanvasView` and `MarkupCanvas`, comments copied verbatim. Enabler for testing the maths
  and for "markup on canvas" later.
- Merge `CanvasView.tintFor` (:13) into `util.statusColor` (:148) via one `statusTone()` +
  two maps. Proven cost: the Unsolved-shows-green bug was fixed twice 2 minutes apart
  (`cdcfa5b`, `d90d0f2`) and the copies have already re-diverged (`needs …` vs `working`).
- `apiUpload()` in `api.ts` — two hand-rolled multipart paths (`:203`, `:980`) re-implement
  `apiSend`'s error tail; the inline error type literal appears 3×.
- Shared OCR "replace existing note?" flow — 18 lines identical in `EvidencePanel:105` and
  `RegionModal:110` plus twin ConfirmModals.
- One error convention: `RegionModal` (8 `setErr` sites) and `CanvasPanel` (4) invented
  inline-Alert error state where everything else uses `notifyError`.
- Smaller: `puzzlesById/puzzleOptions` util (5-6 flatten sites, one O(n²)); `<SheetList>`
  (HuntInfoPanel vs PuzzleDrawer ~45 lines); `useElementSize` from @mantine/hooks instead
  of two hand-rolled ResizeObservers; `pointercancel` handling; region-click drag threshold;
  `MarkupCanvas` zoom buttons anchoring at (0,0) instead of viewport centre; dead
  `TINT_LEGEND` export; `RegionLink` type splitting.

Tests:
- One conftest factory for the ~20-line Quart `web` fixture — now copy-pasted in 19 files
  (5 new this batch; secret key ×20, session patch ×28, ctx dict ×17 byte-identical).
- `try/finally` missing around patch cleanup in `test_media_web.py:72` and
  `test_markup_web.py:69` fixtures — a setup error leaks global patches across the session.
  Cheap and real; could ride along with any nearby work.
- Smaller: `_hunt`/`_media`/`RECT` stub dedupe; tuple-vs-SimpleNamespace fixture yields;
  30 redundant `@pytest.mark.asyncio` in test_media_web; `assert status in (403, 404)` at
  `test_media_web.py:120` should pin one code.

Migrations 030-035: clean (numbering, charset/engine, indexes, comment style; 030's FK
drop/re-add done correctly). Nits only: 030's `DROP FOREIGN KEY` isn't re-run-safe after a
partial failure; explicit-vs-implicit FK index style varies.

### Other correctness observations (not scheduled, worth a tracked decision)

- **No media GC**: nothing ever deletes media rows/files (replaced images, orphaned crops,
  deleted canvases' originals). Individually justified, collectively an unbounded disk leak.
- `puzzle_images.reorder` accepts a subset, leaving duplicate positions for unlisted rows.
- `data.py:50` pool retry uses blocking `time.sleep(0.05)` — confirm it only ever runs on
  the executor thread, not the loop.
- Pre-existing `attachment_bytes` (`api.py:191`) lacks the `nosniff` header the new media
  routes added.
- `EvidencePanel.onReplaceImage` `useCallback` depends on unstable mutation objects,
  defeating `EvidenceRow`'s `memo` (deps should be `.mutateAsync`); same lower-impact
  pattern in `PuzzleImages.tsx:144`.

### Done well (keep as the model)

`media_store` reusing `message_media` path safety; locks added to the existing registry with
deadlock reasoning; `create_puzzle_from_region` delegating to the real create path;
`imageFromClipboard`/`isTextEntryTarget` factored before their 3 call sites; `MarkupModal` as
a 21-line reused wrapper; batched `get_names_by_ids` mirroring; real-encoder test image
factories; mutation-proved guards; the tile-seam and hooks-ordering comments.

### Natural extensions (future work, rough order)

1. Media garbage-collection story (even `!media_stats` + manual sweep).
2. Two-browser live-sync verification + "someone added a region" toast (push plumbing exists).
3. Markup layer on the canvas itself — unblocked by the `useZoomPan` extraction.
4. Auto-OCR on informs-link (populate empty note); batch "OCR all regions".
5. "Regenerate tiles" action (fix for rows stuck `pending`) + tiling concurrency semaphore
   (~360 MB peak per job, uncapped).
6. Vitest for the pure coordinate maths (`zoomAt`, tile-grid snap, `toNorm`) — exactly the
   bug class every browser pass keeps catching by hand.
7. `!images` Discord parity for the puzzle-image strip (web-dashboard-command-parity rule).

## What NOT to touch

- Anything in the backlog above — this task is the six bugs only.
- `tests/conftest.py`, existing tests, migrations, `MarkupCanvas`'s DOM structure
  (frame → shrink-wrap → img+svg; its comments explain why).

## If you get stuck

Diagnose with `superpowers:systematic-debugging` first. If an acceptance command fails twice
without a clear understood fix, write `.ai/active/blocker.md` (what ran, what you saw, your
best hypothesis) and STOP.
