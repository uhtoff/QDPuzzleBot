# Canvas epic review — `dfe1fb5^..HEAD` on `main` (2026-07-28)

Scope requested: the last 3-4 days of additions (the canvas work and everything attendant),
specifically duplication and reuse-not-reinvention, plus natural extensions.

The range turned out to be the whole epic: **~10,816 insertions across 73 files**, all landed
2026-07-26 → 2026-07-28 — image evidence + media store, hunt-level working sheets, the tiled
canvas + regions, OCR preview, media markup, and puzzle images. Migrations 030–035.

Method: three parallel review agents (backend `bot/`, frontend `frontend/src/`, tests +
migrations), each grounded against pre-existing code before judging reuse. The six bugs in
Part 1 were then **verified directly in source by the controller**; agent-reported items not
independently re-verified are marked (agent-reported). No code was changed at review time.

**Overall verdict: a well-built batch.** Architecture-level reuse is genuinely good — the new
code follows the repo's established seams (lock registry, `_resolve_visible_hunt` family,
`PuzzleService.create_puzzle`, pure-module + async-orchestrator split, `message_media` path
safety). Migrations are careful and consistent. Tests are above the suite's average standard
(mutation-proved guards, anti-vacuity docstrings). The duplication that exists is mostly
*within* the new code — things built three times in three sittings — and the recurring
pattern is that **the second and third copies cite the first in a comment and copy anyway**
("same arithmetic as CanvasView.zoomAt", "same reasoning as useUploadMedia above",
"authorised exactly as /api/media/\<id> is").

**Implementation status:** all six Part-1 bugs landed 2026-07-28 (`303becf`, `2a734da`,
`98c1f99`). Backend fixes are test-verified (full suite 1513 passed, ruff + basedpyright clean);
frontend fixes are typecheck-verified only (`tsc --noEmit` + `npm run build` clean) — no browser
confirmation done yet. Everything in Parts 2–4 is unscheduled.

---

## Part 1 — Verified bugs (6) — HANDED OFF

| # | Where | Defect |
|---|-------|--------|
| 1 | `bot/cogs/puzzles.py:1626` (`clue()`) | `!clue` in a non-hunt channel crashes: `get_hunt` returns `None` and the rewrite dropped the old guard. With an attachment it hits `hunt.id` in `_store_clue_attachment`; without one it passes `hunt=None` to `log_clues`. Every sibling command touched in this range checks. (`!clues` is safe — it requires a puzzle channel.) |
| 2 | `bot/web/api.py:541` (`add_evidence`) | Cross-hunt `media_id` check missing on the create path: body `media_id` goes straight to `log_clues`. `update_evidence` (`actions.py:2050`) holds and documents the same invariant ("could be pointed at another hunt's private upload"). |
| 3 | `bot/core/canvas.py:122` (`update_region`) | Passes the **unresolved** `media_id` to `sync_region_image` while the DB write two lines up resolves `existing.crop_media_id if media_id is None else media_id`. When a re-crop is suppressed (ink guard, or crop failure), the sync deletes the strip row from the old puzzle and bails at `media_id is None` — the crop exists in the DB but vanishes from the new puzzle's strip. |
| 4 | `frontend/src/components/PuzzleImages.tsx:159-167` + `EvidencePanel.tsx:523` | One paste, two uploads: document-level listener plus per-cell `onPaste`, no `defaultPrevented` check; `isTextEntryTarget` doesn't catch it because the cell is a `Stack`. |
| 5 | `frontend/src/components/CanvasPanel.tsx:138` + `CanvasView.tsx:96` | `<CanvasView>` has no `key` and the `fitted` ref never resets — switching canvases reuses the previous image's scale/offset (and `drawing`/`pinnedPuzzleId`/`drawRect`). Fix: `key={canvas.id}`. |
| 6 | `frontend/src/components/MarkupCanvas.tsx` | Every mutation failure swallowed: `void commitStroke(...)` / `void commitText()` with no catch (`:228`, `:301`, `:418`, `:420`), `deleteMark.mutate` with no `onError` (`:252`, `:353`, `:390`). On an archived hunt (403) or over-cap stroke, ink appears then silently vanishes on refetch. Everything else uses `notifyError`. |

---

## Part 2 — Duplication and reinvention

### 2.1 Backend

**Worth fixing (in rough value order):**

- **The media → hunt → visibility chain is written four times.** `api.py:229-238`
  (`media_bytes`), `:271-282` (`media_tile`), `:319-328` (`media_markup`), and
  `actions._media_ctx` (~2154, the good version — it exists as a helper and adds the archived
  check). Each `api.py` docstring asserts "authorised exactly as /api/media/\<id> is" — an
  assertion currently held by copy-paste. Extract `_load_visible_media(media_id)` beside
  `_load_visible_hunt` (`api.py:102`).
- **"Save bytes + create media row" exists three times with three behaviours.** `api.py:363-390`
  (sniff/save, `probe_dimensions`, `needs_tiling`, maybe tile), `actions._crop_region_media:2223`
  (crop dims, `tile_status="none"`), `cogs/puzzles._store_clue_attachment:1600` (**no dimensions,
  no tiling** — so a `!clue` image never gets a pyramid and reports 0×0 to the frontend).
  Extract `media_store.ingest(hunt_id, filename, raw, origin)`; the divergence is itself a bug.
- **Upload validation duplicated with byte-identical user-facing strings.** `api.py:418-423`
  (`ocr_evidence_preview`) vs `media_store.py:87/91` — "Image too large (limit {n} bytes)." and
  "Not a supported image. Allowed: PNG, JPEG, GIF, WebP." Extract
  `media_store.validate_image_bytes(raw) -> content_type` raising `MediaRejected`. Bonus fix:
  OCR preview checks `sniff_image_type(...) is None` rather than membership, so it accepts
  sniffable types outside `ALLOWED_IMAGE_TYPES`.
- **Cross-hunt media guard: three copies, two error keys, one omission.** `actions.py:2052`
  (`cross_hunt_media`), `:2276` (`unknown_media`), `:2539` (`cross_hunt_media`), omission =
  bug #2. Extract `_require_hunt_media(media_id, hunt_id)` for all four sites.
- **`_push_board` (`actions.py:2246`) duplicates pre-existing `_notify_board` (`actions.py:73`).**
  (agent-reported) It re-looks-up the hunt row, the cog, and (via `WebNotifier`) the guilds map
  to end at the same `events.publish`; all ten `actions.py` call sites already hold
  `ctx["hunt"]["uid"]` and `ctx["guild_snowflake"]`. Only the `api.py:48` tiling caller needs the
  id-only form. Secondary smells: `await`ed everywhere but only does `create_task`; no
  `try/except`, so a push failure 500s the region write (`_notify_board` catches).
- **DAO layer:** ~25 new copies of the 7-line cursor/execute/commit pattern across the five new
  DAO classes (`mysqldb.py:346-760`), and four byte-identical `delete` overrides
  (`mysqldb.py:472, 558, 677, 754`) that exist only because `_MySQLBaseDb.delete` (`:73`) is
  missing a `commit()`. Convention-following rather than regression, but add
  `_exec/_fetch_one/_fetch_all` to the base and fix base `delete()`.

**Smaller (agent-reported):** `markup.py:58` hardcodes `[:500]` where `markup_shape.MAX_TEXT`
exists; function-local `import json as _json` in `data.py:185/:203` despite the top-level import
(and two locals in `actions.py` that should be one module-level import); `data.py:730-745`
re-derives source-name fallback strings (`f"Sheet {id}"`, `f"Region {id}"`) that
`puzzle_service.evidence_source_label` owns; defensive `getattr(i, "source_region_id", None)` ×5
on a dataclass that declares the field.

**Fine as is (deliberate/documented):** `_coord` duplicated between `canvas_shape.py:18` and
`markup_shape.py:33` — the `markup_shape` docstring argues the case convincingly; `import_dict`
copied into the five new dataclasses — matches the seven pre-existing copies (a `TimestampedRow`
mixin would retire all twelve, someday); `get_hunt_sheets` existing in both `data.py:424` and
`mysqldb.py:206` — that's the repo's read-pool/write-path split, though note the
`ORDER BY id ASC` rule (bang `!list_sheets` numbering) is now asserted in two places.

### 2.2 Frontend

**Worth fixing:**

- **Zoom/pan maths duplicated between `CanvasView` and `MarkupCanvas` (~60-70 lines).**
  `zoomAt` (`CanvasView:148-157` vs `MarkupCanvas:112-121`, identical algebra, different clamp
  bounds), the non-passive `wheel` effect (`:168-184` vs `:126-141`, **comments copied verbatim**
  down to "0.0015 … ~1.16x per detent"), the `ResizeObserver` measure effect, the drag-pan
  `{sx,sy,ox,oy}` ref idiom, the clamped normalise pair. Extract `hooks/useZoomPan.ts`
  parameterised by min/max scale. This is also the enabler for unit-testing the maths and for
  a future markup-on-canvas layer.
- **`CanvasView.tintFor` (`:13-32`) reimplements `util.statusColor` (`util.ts:148-160`).**
  Proven cost: the Unsolved-shows-green bug was fixed **twice, two minutes apart** (`cdcfa5b`
  canvas, `d90d0f2` util — the second commit's body: "statusColor had the same substring flaw
  the canvas tint did"), and the copies have already re-diverged (`needs …` vs `working` as the
  in-progress substring). Extract one `statusTone(status, solved)` + `TONE_MANTINE`/`TONE_HEX`
  maps; derive `TINT_LEGEND` from `TONE_HEX` (currently also a dead export).
- **Two hand-rolled multipart paths in `api.ts`.** `useUploadMedia` (`:203-234`) and
  `useOcrEvidenceImage` (`:980-1001`) each re-implement `apiSend`'s tail (FormData, credentials,
  CSRF, 401→`AuthError`, error decode); the inline error type literal appears three times.
  Extract `apiUpload<T>(path, file)`.
- **OCR "replace existing note?" flow copied.** `EvidencePanel.extractClueText` (`:105-123`) and
  `RegionModal.extractText` (`:110-128`) are the same 18-line routine plus twin `ConfirmModal`s
  (`EvidencePanel:397-407`, `RegionModal:293-303`). Extract a `useOcrIntoField` hook or a shared
  confirm component.
- **Two error-reporting conventions now exist.** Pre-existing components (and the new
  `HuntInfoPanel`/`EvidencePanel`/`PuzzleImages`) use `util.notifyError`; `RegionModal` invented
  local `err` state with eight `setErr` sites + inline `<Alert>` (`:197`), `CanvasPanel` the same
  ×4, and `e instanceof Error ? e.message : "…"` — literally `notifyError`'s body — is re-inlined
  five times. Inline alerts in a modal are defensible, but then it should be one shared
  `useFormError`, not two ad-hoc copies.

**Smaller (agent-reported):** `puzzlesById/puzzleOptions` util — 5-6 sites flatten
`detail.board` into a dedup map (`EvidencePanel` does it twice in adjacent memos; `RegionModal`'s
is O(n²)); `<SheetList>` — `HuntInfoPanel:104-176` reproduces `PuzzleDrawer:258-313` (~45 lines,
identical `submitSheet`); `useElementSize()` from the already-installed `@mantine/hooks` instead
of two hand-rolled `ResizeObserver` effects; no `pointercancel` handling in either canvas (stuck
drag on OS-interrupted touch; mitigated by `setPointerCapture`); region `pointerdown` opens the
modal with no drag threshold, so a large region can't be panned from (dnd-kit's 5px convention
fits); `MarkupCanvas` toolbar zoom anchors at (0,0) (`:285`) and walks the image cornerward —
`CanvasView.zoomBy` anchors centre; dead `rect` computation at `MarkupCanvas:170`; `RegionLink.shape`
optional in the type though create requires it — split `RegionCreate`/`RegionPatch`; `SortableTh`
(`EvidencePanel:417-441`) may duplicate whatever `PuzzleTable` does — check before a third copy;
`EvidencePanel.onReplaceImage` `useCallback` depends on unstable mutation result objects,
defeating `EvidenceRow`'s `memo` — the very hazard its own comment at `:473-477` warns about
(deps should be `.mutateAsync`; same lower-impact pattern `PuzzleImages:144`).

**Fine as is:** the ~20 new `useMutation` hooks repeat the file's established 6-line shape —
consistent repetition, and a factory would obscure per-hook invalidation nuance; raw `useState`
for modal booleans instead of `useDisclosure` — stylistic drift only. No object-URL leaks; all
listeners/observers correctly torn down.

### 2.3 Tests

- **The ~20-line Quart `web` fixture is copy-pasted in 19 files, 5 new in this batch**
  (`test_canvas_web.py:42`, `test_media_web.py:43`, `test_markup_web.py:51`,
  `test_puzzle_images_web.py:47`, `test_hunt_sheets_web.py:25`). The secret key appears 20×,
  `patch("bot.web.auth.session", …)` 28×, the exact `ctx` dict literal 17× character-for-character.
  A conftest factory (`make_web_app(blueprint, *, hunt_ctx=None, extra_patches=())`) collapses
  ~280 lines repo-wide; the per-file variation (blueprint, visibility patch, DAOs) is all
  parameterisable.
- **`try/finally` missing around patch cleanup** in `test_media_web.py:72` and
  `test_markup_web.py:69` — a setup error after the first `p.start()` leaks `bot.web.auth.session`
  / `bot.store.MediaJsonDb` patches into the rest of the session. The other three new web-test
  files do it right. Cheap, real, worth folding into any nearby work.
- **Smaller:** `_hunt`/`_media` stub near-duplicates across 5 files; `RECT` literal in three
  files (fine in `test_canvas_shape.py` where it *is* the subject); `test_media_web.py` yields a
  bare 3-tuple and `test_markup_web.py` a 5-tuple where house style is `SimpleNamespace`; 30
  redundant `@pytest.mark.asyncio` markers in `test_media_web.py` (`asyncio_mode = auto`);
  mid-file import at `test_markup_web.py:33`; `assert resp.status_code in (403, 404)` at
  `test_media_web.py:120` should pin one code (and one of its two visibility-patch shapes doesn't
  match the real `_load_visible_hunt` contract).
- **Known latent hazard** (from the epic's own handoff, restated here so it isn't lost):
  `test_canvas_web.py`'s fixture only isolates the DAOs someone named — an unpatched DAO reaches
  real MySQL, which already bit once when locates crops landed.

### 2.4 Migrations 030–035 — clean

Numbering collision-free (000–035, no gaps); `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_general_ci` matches house style exactly; zero code-then-`--` single-line comment
hazards (all full-line, per `migrate.py`'s stripping rule); every joined column indexed
(explicitly, or implicitly by the FK); the one destructive dance — 030's FK drop/re-add to change
nullability — preserves CASCADE semantics verbatim and says so; `035`'s
`UNIQUE KEY (source_region_id)` is the right idempotency call and documents the multiple-NULLs
subtlety; `032` explicitly annotates that it reverses `030`'s no-dimensions decision.

Nits: `030:36`'s `DROP FOREIGN KEY` is not re-run-safe after a partial failure at the `MODIFY`
(low probability, documented hazard); explicit-vs-implicit FK index style varies between
030/031 and 033/035 — cosmetic.

---

## Part 3 — Other correctness observations (unscheduled; worth a tracked decision)

- **No media GC.** Nothing ever deletes a media row or file: replaced evidence images kept (by
  documented decision), `delete_canvas` rmtrees tiles but leaves row + original, every re-crop
  creates a new row, no `MySQLMediaDb.delete` exists. Individually justified; collectively an
  unbounded disk leak on the Plesk host. Decide: sweeper, `!media_stats` + manual, or an explicit
  "no GC by design" note in one place instead of five local justifications.
- **`puzzle_images.reorder` accepts a subset** (`puzzle_images.py:43-47`): validates supplied ids
  are owned but not that all owned ids are supplied; `set_positions` renumbers the subset 0..n-1,
  leaving duplicate positions ordered by id-tiebreak. Require the full set or renumber the rest.
- **`data.py:50` pool retry blocks with `time.sleep(0.05)`** — confirm `_run`/`_run_write` only
  ever execute on the executor thread; on the loop this stalls the gateway up to 200 ms per
  contended request, precisely under the tile-burst load it was added to survive.
- **Pre-existing `attachment_bytes` (`api.py:191`) lacks the `X-Content-Type-Options: nosniff`
  header** the new media routes added — the new code made the old gap visible.
- OCR preview type-membership gap — folded into `validate_image_bytes` above.

## Part 4 — Done well (keep as the model)

`media_store` importing `media_root`/`safe_filename` from `message_media` and mirroring
`download_attachment`'s unlink-on-failure; `hunt_sheet_lock` added in the existing registry
with stated deadlock reasoning (`with_hunt_sheet_mutex` inert when a puzzle lock is held);
`create_puzzle_from_region` delegating to `PuzzleService.create_puzzle` under `hunt_create_lock`
rather than inventing a half-puzzle; `_visible_hunt_for_canvas` slotting into the
`_resolve_visible_hunt` family; batched `get_names_by_ids`/`resolve_*_names` mirroring the
puzzle precedent (no N+1); `_region_display_name` pulled out pure and testable;
the tile routes *improving* on the traversal guard they copied (nosniff added);
`imageFromClipboard`/`isTextEntryTarget` factored into `util.ts` before their three call sites
existed; `MarkupModal` as a 21-line wrapper reused twice; dnd-kit setup copied exactly from
`DragBoard` (5px activation constraint); real-encoder test-image factories
(`test_image_tiler._write_image`, `test_ocr._text_png`); mutation-proved guards throughout;
the tile-seam comment (`CanvasView:415-423`) and hooks-before-early-return comment (`:291-294`)
documenting real non-obvious bugs.

## Part 5 — Natural extensions (rough priority order)

1. **Media garbage-collection story** (Part 3's first item — smallest useful step is
   `!media_stats` + a manual sweep command).
2. **Live-collaboration polish**: the outstanding two-browser sync verification, plus a cheap
   "someone added a region" toast — the `_push_board` plumbing already exists.
3. **Markup on canvas regions** (the parked "freehand/line/text on the canvas" enhancement) —
   mostly wiring once `useZoomPan` is extracted.
4. **OCR follow-through**: auto-OCR when a region links as `informs` (populate an empty note);
   batch "OCR all regions" for grid-heavy sheets.
5. **"Regenerate tiles" action** (the open-questions fix for rows stuck `pending` after a crash)
   + a tiling concurrency semaphore (~360 MB peak per job, currently uncapped).
6. **A frontend test runner** (vitest) starting with the pure coordinate maths (`zoomAt`,
   tile-grid snapping, `toNorm`) — exactly the bug class every browser pass catches by hand,
   and `useZoomPan` makes it testable.
7. **`!images` Discord parity** for the puzzle-image strip, per the bang↔web parity principle.

Already parked elsewhere (not restated as new): the informs-edge/capstone question and
overlapping-region shading (design conversations,
`.ai/archive/2026-07-27-canvas-markup-and-puzzle-images/open-questions.md`); the 27 MB `images/`
sample-data dir and the stray scratchpad venv (same file).
