# Image links in Discord — design

Date: 2026-11-27. Status: design, not yet planned. Rides alongside the mark-up margin
(`2026-11-27-markup-margin-design.md`); uses its margin when snapshotting, but either can
ship first (without the margin, a snapshot is simply the image's own size).

## Intent

During a hunt people constantly screenshot an image, scribble on it and paste it into
Discord to say "look at this". The dashboard already holds the images and a mark-up
editor; it should be one click to put the marked-up picture, and a link back to it, in the
puzzle's channel. And a puzzle channel's pinned info message should list its images the way
it lists its extra sheets, so anyone in the channel can open them.

This delivers part (1) of the roadmap item "Evidence in Discord" (`docs/PLANS.md`) and
extends it to the puzzle's own images. Parts (2) and (3) — pinning `!clue` and the one
pinned "Evidence" message per channel — are **out of scope** and stay on the roadmap.

## Goals

1. **Deep link to an image:** a dashboard URL that opens the hunt board on a puzzle's
   drawer with a given image open in the mark-up editor.
2. **Evidence images in the strip:** a puzzle's image strip also shows evidence images
   whose **source** is that puzzle, marked as evidence (user, 2026-11-27).
3. **Post to Discord** from the mark-up editor: your message (optional) + the deep link +
   a PNG snapshot of the image with its current marks, posted under your name.
4. **Images in the pinned info message:** links to the puzzle's own images, then its
   evidence images.

## Non-goals

- Anything going from the strip into evidence. Adding/pasting to the strip always makes
  the puzzle's own image (user, 2026-11-27).
- Showing evidence in its **target's** strip (the capstone's roll-up already shows it).
- Merging `puzzle_images` and `evidence_items`, or any schema change.
- Image previews in the pinned message (links only, user).
- Pinning `!clue`, a pinned Evidence message, re-rendering pins from evidence edits beyond
  the Images list.
- Posting as the bot. Posting from places other than the mark-up editor.

## Anti-goals

- A post that doesn't match what the poster sees on screen.
- A way to post into a channel the user could not post in from Discord.
- A pinned message that breaks (Discord rejects it) because a puzzle has many images.

## Constraints and what exists

- **Two image kinds.** `puzzle_images` (migration 035): the puzzle's own picture — upload
  or a canvas *locates* region's crop — ordered by `position`. `evidence_items`: a clue with
  a note, `source_puzzle_id` (nullable; a canvas *informs* region's evidence has a region
  source instead), `target_puzzle_id`, optional `media_id`. They were kept separate on
  purpose (035's header); that stays.
- **Strip:** `frontend/src/components/PuzzleImages.tsx` (in `PuzzleDrawer.tsx`), routes
  `GET/POST/PATCH/DELETE /api/puzzles/<id>/images` in `bot/web/actions.py`.
- **Evidence:** `GET /api/evidence?hunt_uid=` (hunt-wide, `useEvidence(uid)`; each item
  carries `source_puzzle_id`, `note`, `media_id`, `media_url`), `DELETE
  /api/evidence/<id>` (`useDeleteEvidence`), `PATCH /api/evidence/<id>`. Created from the
  dashboard (`POST /api/evidence`), Discord `!clue`/`!clues` (`bot/cogs/puzzles.py`,
  `PuzzleService.log_clues`), and canvas informs regions (`bot/core/canvas.py`).
- **Mark-up editor:** `MarkupModal` → `MarkupCanvas` (`mediaId`, `alt`, `onSave`). Opened
  from `PuzzleImages` and `EvidencePanel`. Marks are an SVG (`viewBox` 0–1, normalised to
  the image) plus an HTML text layer over the `<img>`; they are never baked into the file.
- **Posting as a member:** `POST /api/channels/<id>/messages` → `channel_relay.send_as`
  (relay webhook, member's display name + avatar, mentions suppressed). It enforces:
  login, hunt visible to the user, hunt not archived, member present, not timed out,
  `view_channel` + `send_messages` on that channel. Text only today.
- **Pinned info message:** built by `send_initial_puzzle_channel_messages` in
  `bot/cogs/puzzles.py`; extra sheets are a second embed (`_build_embed_list`). Refreshed
  via `notifier.refresh_info` / `cog.web_refresh_info` (best-effort). Dashboard base URL
  is `guild_data.website_url` (`_overview_url` → `{base}/hunts/{uid}`).
- **Board:** `frontend/src/pages/HuntBoard.tsx` route `/hunts/:uid`, no URL parameters
  today; the drawer is driven by local state `notesPuzzleId`.
- Discord limits: message content 2000 chars; embed field value 1024 chars; webhook upload
  limit 10 MiB on an unboosted server.
- CI gates on `basedpyright bot/`; MariaDB in prod / MySQL 8 in CI (no schema change here).

## Approaches considered

**Snapshot rendering** — the only real choice:
1. **In the browser (chosen):** draw the loaded image and the current marks onto a canvas
   and encode a PNG. Matches the screen by construction, and the browser already has the
   image and marks loaded.
2. **On the server (pyvips):** would need a second renderer for strokes, arrows,
   highlighter and text that must match the SVG/HTML one. Rejected: two engines that drift,
   for no gain.

Everything else follows existing seams (relay webhook for posting, the pin builder for
links), so there is one sensible way.

## Design

### 1. Deep link

`{website_url}/hunts/{uid}?puzzle={puzzleId}&image={mediaId}` — both parameters optional.

- `puzzle` alone: open that puzzle's drawer.
- `puzzle` + `image`: open the drawer and the mark-up editor on that image.
- `image` alone (evidence with no source puzzle): open the mark-up editor over the board.
- After acting on them, remove the parameters from the URL (`replace`), so closing the
  editor or reloading does not reopen it.
- If the puzzle no longer exists, or the image is no longer on it (neither its own image
  nor its source evidence), open what still exists and notify "That image is no longer on
  this puzzle".
- Opening still requires the usual dashboard login and hunt visibility.

A single helper builds this URL on each side (Python for the pin and the post; TS for the
editor's "copy link" use if any), so the format is defined once per language with a test.

### 2. Evidence images in the strip

- `PuzzleImages` shows, after the puzzle's own sortable images, the evidence items with
  `source_puzzle_id === puzzleId` and a `media_id`, oldest first, from the existing
  hunt-wide `useEvidence(uid)` query.
- Each has a small "Evidence" badge and its note as the tooltip/alt. They are not
  draggable (order is not theirs to keep) and are never mixed into the own-images order.
- Clicking opens the same mark-up editor (marks are per media, so marks made here show in
  the Evidence view and vice versa).
- Remove on an evidence thumbnail → `ConfirmModal` "Delete this evidence? It will also
  disappear from <target>'s roll-up." → `useDeleteEvidence`. (Own images keep today's
  detach.)
- The same media appearing as both an own image and evidence on one puzzle shows twice;
  acceptable, not deduplicated (builder's call if trivial).

### 3. Post to Discord

**UI.** A **Post to Discord** button in the mark-up editor's toolbar (hidden when the hunt
is archived — the editor is already read-only/unavailable there). It opens a small modal:
- "Posting to #channel-name" (resolved for display from the context; the server decides
  the real channel).
- Optional message, multiline, max 1500 chars.
- Post / Cancel. On success: notify "Posted to #channel-name", close the modal, leave the
  editor open. On failure: show the server's error.

**Context.** `MarkupModal`/`MarkupCanvas` gain a `postTarget` prop:
`{ kind: "puzzle_image"; puzzleId } | { kind: "evidence"; evidenceId }`. `PuzzleImages`
passes `puzzle_image` for own images and `evidence` for evidence thumbnails;
`EvidencePanel` passes `evidence`. No `postTarget` → no button.

**Snapshot.** In the browser, at the image's natural resolution:
- Canvas bounds = the image, grown to include any marks in the margin (the margin spec's
  −0.25…1.25 range), transparent outside the image.
- Draw the image, then each mark the same way the editor shows it: pen/line/arrow/
  highlighter strokes (colour, width, opacity, arrowheads) and text marks (colour, size
  as a fraction of image height, same font family and line height as the editor's text
  layer). Stroke widths and text sizes are fractions of the image, so they scale exactly.
- *(Design call, 2026-11-27.)* **Text wraps where the editor wraps it.** The editor's text
  marks are `pre-wrap` with no width, so a line breaks at explicit newlines and at the
  right edge of the box they sit in. The renderer word-wraps with `measureText` against
  that same width: from the mark's x to the right edge of the image box, or of the
  image-plus-margin box once the margin spec has shipped (the renderer takes the box's
  right edge as a parameter, so either order of shipping works).
- Include the in-progress text mark if the user is mid-edit (commit it first, as the
  editor's Done button already does).
- Longest side capped at **4096 px** (scale down uniformly above that — Discord previews
  downscale anyway); encode PNG; if over **9 MiB**, re-encode as JPEG quality 0.9 on a
  white background; if still over, refuse with "Image too large to post".
- The renderer lives in a pure-ish module taking a 2D context, so its draw calls can be
  unit-tested with a recording fake (vitest runs without a DOM).

**Server route.** `POST /api/media/<media_id>/post`, multipart: `file` (the snapshot),
`content` (optional), and exactly one of `puzzle_id` / `evidence_id`.
- Validate the snapshot with the existing image sniffing (PNG/JPEG only); size ≤ 10 MiB.
- Resolve and check the context **server-side** (the client ids are untrusted):
  - `puzzle_id`: the media must be one of the puzzle's own images **or** the media of an
    evidence item whose source is that puzzle. Channel = the puzzle's channel.
  - `evidence_id`: the evidence's `media_id` must equal `media_id`. Channel = source
    puzzle's channel, or the hunt's own channel when it has no source puzzle.
  - The hunt is the media's hunt; all ids must belong to it.
- Then exactly the gates of `post_channel_message` (visible hunt, not archived, member,
  not timed out, `view_channel` + `send_messages`). Share that gate code rather than copy
  it. `content` must not start with `!` (same rule).
- Message content: the user's text (if any), a blank line, then
  `[Open in dashboard](<deep link>)`. Total ≤ 2000 chars. The snapshot attached as
  `<puzzle-or-hunt-slug>-markup.png` (or `.jpg`).
- Send via `channel_relay.send_as`, extended with an optional `file: discord.File`
  (keeping the NotFound rebuild-and-retry; the file must be re-created for the retry since
  a sent `discord.File` is consumed). Mentions stay suppressed.
- Errors reuse the chat route's codes (`not_found_or_forbidden` 404, `forbidden` 403,
  `archived` 403, `send_failed` 502) plus `bad_image` 400 and `not_on_puzzle` 404.
- Log an access/audit line as other web mutations do.

### 4. Images in the pinned info message

- A third embed (after additional sheets, before minis — builder may place it with the
  sheets embed if that reads better) with one field **"Images"**:
  - Own images in strip order: `• [Image N](deep link)`.
  - Then source evidence images, oldest first: `• [Evidence: <note>](deep link)` with the
    note collapsed to one line, truncated to 40 chars with "…", and Markdown link
    characters escaped; `• [Evidence N](…)` when the note is empty.
  - Field value ≤ 1024 chars: add lines while they fit, keeping room for a final
    `• +N more on the dashboard` linking to `?puzzle={id}`.
  - *(Design call, 2026-11-27.)* Discord also rejects a message whose embeds total more
    than 6000 characters, and a meta's pin can already carry a long minis list. **The
    Images field is what gives way:** its budget is `min(1024, 6000 − (all other embeds'
    characters) − 50)`, and it shrinks (fewer links, then "+N more") to fit. Sheets and
    minis are never truncated for it. If the budget cannot fit even the "+N more" line, the
    Images embed is left out. The builder takes the remaining budget as an input.
- No images → no embed. `website_url` unset → no embed (relative links do not work in
  Discord).
- Applies to every puzzle channel pin, metas included.
- **Refresh** the puzzle's pin (best-effort, as today) after:
  - strip attach, detach, reorder (`/api/puzzles/<id>/images` POST/PATCH/DELETE);
  - evidence create / delete / re-point (source change) / image replace where the source
    puzzle has (or had) an image — from the dashboard routes, `!clue`/`!clues` with an
    attachment, and canvas region create/update/delete that adds or drops a
    `puzzle_images` row or informs evidence. On a source change, refresh both the old and
    new source puzzles.
- The pin builder needs the puzzle's image ids and its source evidence; fetch both with
  one query each inside the builder's existing data access, not per-image.

## Testing

- **Python (pytest):** deep-link builder; Images field: order (two own images + two
  evidence, assert order), note truncation/escaping, the 1024-char cap with the "+N more"
  line (many images), no embed with zero images or no `website_url`; post route: each
  context check (media not on puzzle → 404; evidence/media mismatch → 404; another hunt's
  ids → 404), gates (archived 403, no send permission 403), `!`-prefixed content 400,
  non-image upload 400, success calls `send_as` with a file and content containing the
  link; `send_as` retry re-creates the file; a meta pin with a long minis list shrinks
  the Images field so the whole message stays ≤ 6000 chars; refresh hooks fire for each trigger listed
  (mock the notifier).
- **Frontend (vitest, node):** deep-link parse/build; strip merge (own images first in
  position order, then source evidence oldest-first, evidence of other puzzles and
  evidence without media excluded); snapshot renderer draw calls against a recording fake
  (bounds grow for a margin mark; stroke width and text size scale with image size; cap at
  4096 longest side; a long text mark wraps at the given right edge, and an explicit
  newline always breaks).
- **Manual (user, desktop + Discord):** post from a strip image and from the Evidence
  view; open the link from Discord on another device; pin shows Images and updates after
  add/remove/reorder and after `!clue` with an attachment; marks in the margin appear in
  the posted snapshot.

## Left to the builder

- Exact embed placement, modal layout, filename slug rules.
- How `HuntBoard` threads the requested image down to `PuzzleImages`/the editor.
- Whether the strip's evidence items come from filtering `useEvidence` client-side or a
  new `?source_puzzle_id=` filter (client-side is fine at hunt scale).
