# Image Links in Discord Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deep links to images, source-evidence images in a puzzle's strip, a "Post to
Discord" button in the mark-up editor, and an Images list in each puzzle's pinned info message.

**Architecture:** Pure helpers on both sides (`bot/utils/urls.py` deep link,
`bot/core/pin_images.py` field text, `frontend/src/lib/deepLink.ts`, `stripItems.ts`,
`markupSnapshot.ts`) carry the logic and the tests. A new route
`POST /api/media/<media_id>/post` reuses the chat route's gates and `channel_relay.send_as`
(extended with a file). Pin refreshes hang off the existing best-effort `web_refresh_info`.

**Tech Stack:** Python 3.12, Quart, discord.py, pytest (`asyncio_mode = auto`); React 18 +
Mantine 7 + TanStack Query; vitest (node env, no DOM).

**Spec:** `docs/superpowers/specs/2026-11-27-image-links-in-discord-design.md`

## Global Constraints

- No schema change, no migration.
- Deep link: `{website_url}/hunts/{uid}?puzzle={puzzleId}&image={mediaId}`, both params optional.
- Post content: user text (≤ 1500 chars, optional, must not start with `!`), blank line,
  `[Open in dashboard](<deep link>)`; total ≤ 2000. Posted via `channel_relay.send_as`,
  mentions suppressed.
- Snapshot: longest side ≤ **4096 px**; PNG, else JPEG q0.9 on white if PNG > **9 MiB**,
  else refuse "Image too large to post". Server accepts PNG/JPEG ≤ 10 MiB.
- Pin Images field: own images `• [Image N](link)` in strip order, then source evidence
  oldest first `• [Evidence: <note ≤40 chars…>](link)` / `• [Evidence N](link)`; final
  `• +N more on the dashboard` → `?puzzle={id}`. Budget `min(1024, 6000 − other embeds' chars − 50)`.
  No images, no `website_url`, or budget too small for the "+N more" line → no Images embed.
- Error codes: reuse `not_found_or_forbidden` 404, `forbidden` 403, `archived` 403,
  `send_failed` 502, `command` 400; add `bad_image` 400, `not_on_puzzle` 404.
- CI: `pipenv run basedpyright bot/` 0 errors; `cd frontend && npm run build` passes.
- Python tests: `timeout 180 pipenv run python -m pytest <files> -q > /tmp/t.log 2>&1`.
  Frontend: `cd frontend && timeout 120 npx vitest run <files> > /tmp/v.log 2>&1`.

## Review Focus

1. **Posting into a channel by naming a puzzle the image isn't on** (client ids are
   untrusted) → 404 `not_on_puzzle`, nothing sent. Tests in Task 4.
2. **A meta with many minis and many images** → pin still accepted (≤ 6000 chars). Test in Task 2.
3. **Evidence note containing `]`, `)`, newlines or Markdown** breaks the pin link → escaped
   and collapsed. Test in Task 2.
4. **Deep link to an image since removed** → drawer opens with a notice, no crash, params
   cleared. Test of the pure resolver in Task 5; manual check.
5. **Webhook deleted mid-post** → retry sends the file again (a consumed `discord.File`
   cannot be resent). Test in Task 4.

---

### Task 1: Deep-link builders

**Files:** Modify `bot/utils/urls.py`; create `frontend/src/lib/deepLink.ts`,
`frontend/src/lib/deepLink.test.ts`; test `tests/test_urls_image_link.py` (new).

**Interfaces — Produces:**
- Py: `image_link(base: str, hunt_uid: str, puzzle_id: int | None = None, media_id: int | None = None) -> str | None` — `None` when `base` is empty; strips a trailing `/` from base.
- TS: `parseBoardLink(search: string): { puzzleId: number | null; mediaId: number | null }` (ignores non-integer values) and `boardLink(uid: string, opts: { puzzleId?: number; mediaId?: number }): string` (path + query, relative).

- [ ] **Step 1: Failing tests.** Py: `image_link("https://x.com/", "abc", 5, 9) == "https://x.com/hunts/abc?puzzle=5&image=9"`; only puzzle → `?puzzle=5`; only media → `?image=9`; neither → no `?`; `image_link("", "abc", 5) is None`. TS: `parseBoardLink("?puzzle=5&image=9")` → `{5, 9}`; `"?puzzle=x"` → nulls; `boardLink("abc", {mediaId: 9})` → `"/hunts/abc?image=9"`.
- [ ] **Step 2:** run, FAIL. **Step 3:** implement. **Step 4:** run, PASS.
- [ ] **Step 5: Commit** — `feat(links): deep-link builders for images`

### Task 2: Images field in the pinned info message

**Files:** Create `bot/core/pin_images.py`, `tests/test_pin_images.py`; modify
`bot/cogs/puzzles.py` (`_build_embed_list`, plus a data fetch).

**Interfaces — Consumes:** `image_link` (Task 1). **Produces:**
- `@dataclass(frozen=True) class PinImage: media_id: int; note: str | None; is_evidence: bool`
- `images_field_value(images: list[PinImage], base: str, hunt_uid: str, puzzle_id: int, budget: int) -> str | None` — lines in the given order (caller orders: own by position, then evidence by `created_at`); own numbered `Image 1…`, evidence numbered separately `Evidence 1…` when noteless; note collapsed to one line, truncated to 40 chars with `…`, `[ ] ( ) \ * _ ~ ` |` escaped with `\`; stop before exceeding `budget`, reserving room for the "+N more" line; `None` if no images, no base, or it cannot fit even one line or the "+N more" line.
- `pin_images_for(puzzle_id: int) -> list[PinImage]` — one query for `puzzle_images` (by position), one for evidence with `source_puzzle_id = puzzle_id AND media_id IS NOT NULL` (by `created_at`), via the existing DAOs (`PuzzleImageJsonDb` / `EvidenceJsonDb` or their real names in `bot/store`).
- In `_build_embed_list`: after sheets and minis embeds are built, compute
  `other = sum(len(e) for e in embed_list)` (`discord.Embed.__len__` counts all text), budget =
  `min(1024, 6000 - other - 50)`; if `images_field_value(...)` returns text, insert an embed
  with field name "Images" after the sheets embed. `base` = `guild_data.website_url`
  (same source as `_overview_url`).

- [ ] **Step 1: Failing tests** (`tests/test_pin_images.py`):
  - order: two own + two evidence in → output lines `Image 1`, `Image 2`, `Evidence: …`, `Evidence 2` in that order, each linking `?puzzle=7&image=<id>`.
  - note `"look ] at (this)\nnow *bold*"` → one line, escaped, ≤ 40 chars + `…`.
  - 60 images, budget 1024 → `len(value) <= 1024`, last line `+N more on the dashboard` with N = 60 − shown, linking `?puzzle=7`.
  - budget 30 → `None`; base `""` → `None`; no images → `None`.
  - **Whole message:** a `Puzzles` cog built as in `tests/test_pinned_info_refresh.py`, a meta
    puzzle whose minis embed is ~5500 chars and 30 images → every embed list from
    `_build_embed_list` has `sum(len(e)) <= 6000` (patch `pin_images_for` where looked up in `bot.cogs.puzzles`).
- [ ] **Steps 2–4:** run FAIL, implement, run PASS (also `tests/test_pinned_info_refresh.py tests/test_info_bot.py`).
- [ ] **Step 5: Commit** — `feat(pin): list puzzle and evidence images in the pinned info message`

### Task 3: Refresh the pin when images change

**Files:** Modify `bot/web/actions.py` (puzzle image POST/PATCH/DELETE, evidence PATCH/DELETE),
`bot/web/api.py` (`POST /evidence`), `bot/cogs/puzzles.py` (`clue`/`clues` with an attachment),
`bot/core/canvas.py` (region create/update/delete paths that add/drop a `puzzle_images` row or
informs evidence with media). Test: `tests/test_pin_images_refresh.py` (new).

**Interfaces — Consumes:** `cog.web_refresh_info(guild, hunt, puzzle)` (best-effort, never raises).
Rule: refresh the affected puzzle(s) **after** the DB write commits; on an evidence source change
refresh both old and new source puzzles; evidence without media or without a source puzzle →
no refresh. Locate each route's existing `_route_ctx`/guild/hunt objects; follow how
`bot/web/actions.py` already calls `web_refresh_info` (e.g. around its line 376).

- [ ] **Step 1: Failing tests** — one per trigger, patching `web_refresh_info` as an `AsyncMock` on the cog the route resolves, using the fixtures in `tests/test_puzzle_images_web.py` / `tests/test_evidence_web.py` / `tests/test_add_evidence_web.py`: attach, reorder, detach → awaited once with that puzzle; evidence delete with media+source → awaited; evidence re-point source A→B → awaited for A and B; evidence without media → not awaited. `!clue` with an attachment → awaited (pattern from `tests/test_evidence_commands.py`).
- [ ] **Steps 2–4:** run FAIL, implement, run PASS; run the five existing test files named above too.
- [ ] **Step 5: Commit** — `feat(pin): refresh pinned info when a puzzle's images change`

### Task 4: Relay with a file, and the post route

**Files:** Modify `bot/core/channel_relay.py`, `bot/web/api.py`; tests `tests/test_channel_relay.py`,
`tests/test_media_post_web.py` (new, modelled on `tests/test_channel_reply_web.py`).

**Interfaces — Produces:**
- `send_as(channel, member, content: str, file_bytes: bytes | None = None, filename: str | None = None) -> int` — builds a fresh `discord.File(io.BytesIO(file_bytes), filename)` **inside** each `_send` attempt.
- Extract from `post_channel_message` a shared helper
  `async def _member_channel_gate(user, channel_id: int, hunt) -> tuple[channel, member] | tuple[Response, int]`
  covering guild/channel/member lookup, timeout and `view_channel`+`send_messages`; both routes use it. The chat route's behaviour and tests stay unchanged.
- Route `POST /api/media/<int:media_id>/post` (`@login_required`), multipart `file`, `content`, and exactly one of `puzzle_id`/`evidence_id` (else 400). Order: parse → load media (404) → hunt = media's hunt, visible (404) and not archived (403) → resolve channel per spec §3 "Server route" (`not_on_puzzle` 404 on any mismatch, including ids from another hunt) → `_member_channel_gate` → validate bytes with the existing sniffing, PNG/JPEG only, ≤ 10 MiB (`bad_image` 400) → content rules (`command` 400, `too_long` 400) → build content with `image_link(website_url, uid, puzzle_id_for_link, media_id)` (`puzzle_id_for_link` = the puzzle, or the evidence's source puzzle, or None) → `send_as(..., file)` (`send_failed` 502 on `discord.HTTPException`) → 201 `{"id": "<message id>"}`. Filename `<slug>-markup.png|jpg` via `urls.puzzle_url_slug`. Write an access/audit line the way other web mutations do (find it in `bot/web/actions.py`).

- [ ] **Step 1: Failing tests.** Relay: with a file, the hook's `send` receives a `file` kwarg; on `NotFound` the retry receives a **different** `discord.File` object with the same bytes. Route: success via `puzzle_id` (own image) and via `evidence_id` with no source (posts to hunt channel) — `send_as` awaited with content containing `[Open in dashboard](` and the expected link; media not on puzzle → 404 `not_on_puzzle`; evidence/media mismatch → 404; puzzle from another hunt → 404; archived → 403; member lacks `send_messages` → 403; content `"!s X"` → 400; non-image bytes → 400 `bad_image`; both or neither id → 400. Existing `tests/test_channel_reply_web.py` still passes.
- [ ] **Steps 2–4:** run FAIL, implement, run PASS; `pipenv run basedpyright bot/` 0 errors.
- [ ] **Step 5: Commit** — `feat(web): post a marked-up image to the puzzle's channel`

### Task 5: Deep link on the board, evidence in the strip

**Files:** Create `frontend/src/lib/stripItems.ts`, `stripItems.test.ts`; modify
`frontend/src/pages/HuntBoard.tsx`, `frontend/src/components/PuzzleDrawer.tsx`,
`frontend/src/components/PuzzleImages.tsx`.

**Interfaces — Consumes:** `parseBoardLink` (Task 1), `useEvidence`, `useDeleteEvidence`.
**Produces:**
- `type StripItem = { kind: "own"; image: PuzzleImage } | { kind: "evidence"; item: EvidenceItem }`
- `stripItems(own: PuzzleImage[], evidence: EvidenceItem[], puzzleId: number): StripItem[]` — own by `position`, then evidence with `source_puzzle_id === puzzleId && media_id != null` by `created_at`.
- `resolveDeepLink(link, puzzles: {id:number}[], items: StripItem[]): { puzzleId: number | null; mediaId: number | null; notice: string | null }` — notice "That image is no longer on this puzzle" when the media isn't among `items`; puzzle missing → all null + notice.
- `PuzzleImages` gains optional `openMediaId?: number | null` and `onOpened?: () => void` to open the editor once on mount/when set.
- `HuntBoard`: on load, read `useSearchParams`, set `notesPuzzleId`, pass `openMediaId` through `PuzzleDrawer`; `image` without `puzzle` opens a board-level `MarkupModal`; then `setSearchParams({}, { replace: true })`.
- Strip: evidence thumbnails after own ones, not sortable, `Badge` "Evidence", note as `title`; remove → `ConfirmModal` "Delete this evidence? It will also disappear from <target_puzzle_name>'s roll-up." → `useDeleteEvidence`.

- [ ] **Step 1: Failing tests** for `stripItems` (two own out of position order + evidence for this puzzle, another puzzle, and without media → own sorted, then this puzzle's media evidence oldest first) and `resolveDeepLink` (present → no notice; removed image → notice, puzzle kept; unknown puzzle → nulls + notice).
- [ ] **Steps 2–4:** run FAIL, implement, run PASS; `npm run build` passes.
- [ ] **Step 5: Commit** — `feat(dashboard): image deep links and source evidence in the strip`

### Task 6: Snapshot renderer

**Files:** Create `frontend/src/lib/markupSnapshot.ts`, `markupSnapshot.test.ts`.

**Interfaces — Consumes:** `MediaMark` (`types.ts`), the stroke/text constants used by
`MarkupCanvas.tsx` (move any shared ones it needs into this module or `canvasMath.ts` and import
them back — do not duplicate values). **Produces:**
- `snapshotBounds(marks: MediaMark[], imgW: number, imgH: number): { x: number; y: number; w: number; h: number }` — image rect ∪ marks' extents (strokes padded by half their width; text by its wrapped box), image px, rounded out.
- `snapshotScale(w: number, h: number): number` — `min(1, 4096 / max(w, h))`.
- `wrapLines(ctx: Pick<CanvasRenderingContext2D,"measureText">, text: string, maxWidth: number): string[]` — breaks at `\n` always, then word-wraps; a single over-long word stays on its own line.
- `drawMarkup(ctx: SnapshotCtx, img: CanvasImageSource, marks: MediaMark[], imgW: number, imgH: number, opts: { rightEdge: number; fontFamily: string; lineHeight: number }): void` — `rightEdge` = right edge of the box text wraps against, in image-normalised units (1 today, `1 + MARKUP_MARGIN` once the margin ships); draws image then marks in stored order, strokes with round caps/joins, arrowheads as the SVG marker draws them, highlighter = its stored opacity/width.
- `encodeSnapshot(canvas: HTMLCanvasElement): Promise<{ blob: Blob; ext: "png" | "jpg" } | { error: "too_large" }>` (PNG ≤ 9 MiB, else JPEG 0.9 on white, else error).

- [ ] **Step 1: Failing tests** (recording fake ctx with a fixed-width `measureText`): bounds equal the image with no margin marks and grow left/up for a mark at (−0.2, −0.1); `snapshotScale(8192, 100) === 0.5`; `wrapLines` breaks on `\n` and at `maxWidth`; `drawMarkup` draws the image first, a stroke with `lineWidth = width * imgH` (or the editor's actual basis — match `MarkupCanvas`), text `font` size `size * imgH`, and an over-long text line wraps into two `fillText` calls at the given `rightEdge`.
- [ ] **Steps 2–4:** run FAIL, implement, run PASS.
- [ ] **Step 5: Commit** — `feat(markup): render a marked-up image to a snapshot`

### Task 7: Post to Discord button

**Files:** Create `frontend/src/components/PostToDiscordModal.tsx`; modify
`MarkupModal.tsx`, `MarkupCanvas.tsx`, `PuzzleImages.tsx`, `EvidencePanel.tsx`, `api.ts`.

**Interfaces — Consumes:** Tasks 4–6. **Produces:**
- `type PostTarget = { kind: "puzzle_image"; puzzleId: number } | { kind: "evidence"; evidenceId: number }` in `types.ts`.
- `MarkupModal`/`MarkupCanvas` prop `postTarget?: PostTarget`; button "Post to Discord" (`IconBrandDiscord`) shown when set.
- `usePostImage(mediaId: number)` in `api.ts`: `apiUpload` to `/api/media/${mediaId}/post` with `file`, `content`, and `puzzle_id` or `evidence_id`.
- Modal: "Posting to #<name>" (puzzle channel name from board data; "the hunt channel" for sourceless evidence), `Textarea` max 1500, Post/Cancel; on Post: commit any in-progress text mark, render via Task 6 (`rightEdge` per what the editor currently uses), `encodeSnapshot`, upload; success → notify "Posted to #<name>" and close; `too_large` or server error → show it.
- Callers: `PuzzleImages` own → `puzzle_image`, evidence thumbnail → `evidence`; `EvidencePanel` → `evidence`.

- [ ] **Step 1:** Implement. **Step 2:** `npm run build` and `npx vitest run` pass (skipped count unchanged).
- [ ] **Step 3: Manual checklist (user, desktop + Discord):** post from a strip image with a note; from an evidence item in the Evidence view; from a sourceless evidence item (goes to the hunt channel); marks incl. text and an arrow match the screen; link opens the image on another device; pin lists images and updates after add/remove/reorder and after `!clue` with an attachment; a removed image's link shows the notice.
- [ ] **Step 4: Commit** — `feat(markup): post a marked-up image to Discord`
