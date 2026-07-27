# Image evidence + the hunt Canvas

Date: 2026-07-26
Status: Plan A shipped. Plan C shipped. Plan B amended 2026-07-26 to serve a canvas as a
tile pyramid (see "Tiling") after the real motivating image proved the single-`<img>` viewer
unworkable; Plan B now splits into B1 (tiled media) and B2 (the canvas).

## Goal

Three related capabilities, the first two driven by Edric's hunt this year:

1. **Evidence can be a picture.** Today `evidence_items` carries a text `note` and nothing
   else. A screenshot, a crop, a photo of a physical component — none of it can be logged
   as evidence, so it lives in Discord scrollback and is lost to the contribution tables.
2. **A hunt can hold a big image you mark up.** Draw rectangles on it, link each rectangle
   to a puzzle, and render the whole image tinted by progress so you can see at a glance
   what has been claimed, what is solved, and which parts of the art nobody has touched.
3. **A working sheet need not belong to a puzzle.** Hunts hide things in alt text and in
   odd corners of their websites, with no puzzle to attach them to. You want a scratch
   sheet before you know what the thing is — and the option of naming that sheet as where
   a piece of evidence came from.

The second is the one general-purpose tools get wrong. Excalidraw and friends do far more
(full drawing app) and far less (no idea what a puzzle is) than this needs. The value here
is precisely the link between a region of the picture and a live puzzle record.

## Decisions (locked)

- **One design, three implementation plans.** Plan A (image evidence) ships independently
  of Plan B (canvas) and Plan C (hunt-level sheets); each is useful on its own. All three
  hang off the same generalisation: an evidence item's source becomes optional.
- **"Canvas", never "board".** `board` already means the group/puzzle summary throughout
  this codebase (`HuntBoard`, `data.board`, `notifier.board_changed`, the "By group" view).
  Reusing it for the picture would make the word denote two things.
- **A region is both kinds of thing, distinguished by its link role.** `locates` means
  "this part of the image *is* that puzzle"; `informs` means "this part of the image is a
  *clue for* that puzzle". Reuses the vocabulary already in the domain model.
- **Rectangles only**, but geometry is stored as a shape blob so other shapes are a later
  UI change, not a migration.
- **Coordinates are normalised 0–1**, never pixels.
- **A canvas image is served as a tile pyramid, not as one `<img>`.** Amended 2026-07-26
  after measuring the real motivating image — see "Tiling" below. Normalised coordinates
  are what make this a frontend-only concern.
- **Many canvases per hunt, flat.** No round/TagGroup scoping.
- **Draw-to-create.** Drawing a box can create a brand-new puzzle through the existing
  `puzzle_service` create path (Discord channel, sheet tab, tagged into a chosen round).
- **Separate tables; informs-regions spawn real evidence rows**, so routing, the evidence
  panel, and the contribution table all work unchanged.
- **Evidence sources become optional and four-way.** An evidence item's source is a puzzle,
  a canvas region, a hunt-level working sheet, or nothing. Sourceless evidence is wanted in
  its own right, not merely as a consequence of canvas support.
- **A working sheet need not belong to a puzzle.** Things turn up in a hunt's alt text or
  loose on its website with no puzzle to pin them to; you want somewhere to work on them
  before you know what they are.
- **Every image-bearing thing carries optional free text**, with transcription as the
  headline use. One field, not a separate `transcription` column.
- **No image thumbnails in Google Sheets.** A contribution cell shows the transcription
  plus a hyperlink back to the dashboard. No unauthenticated media URLs are created.
- **Discord gets image clues in, and a link out.** `!clue` accepts an attachment; `!canvas`
  posts a link. No server-side image compositing.

## Out of scope (deliberate)

Polygons, freehand, and any second drawing tool. Server-side compositing of the canvas
*with its overlay* — the tint and the regions are drawn by the browser, and no rendered
PNG of the marked-up canvas is ever produced. (Deriving tiles from the source image is
*not* in this exclusion; see "Tiling".) Per-round canvas scoping. Public/tokenised media
URLs. Undo history beyond delete. OCR or any automatic transcription. All are additive
later; none are load-bearing now.

## Known bugs

- ~~**The tile-seam fix loses real image content, not just cosmetic.**~~ **Fixed
  2026-07-27.** The diagnosis below was right; the proposed fix shape was not available.
  `layout: "google"` writes uniform `tile_size²` tiles and **ignores `overlap` outright** —
  building the same source at `overlap=0` and `overlap=1` gives byte-identical interior
  tiles — so there are no shared border pixels to be had without abandoning the google
  layout and rewriting tile addressing. Fixed client-side instead, in `CanvasView.tsx`:
  every tile is snapped to a shared integer grid, its right/bottom edge computed from the
  same expression as its neighbour's left/top edge, so the two are equal by construction and
  no seam can open at any fractional zoom. Checked across 7 fractional tile scales × 40
  boundaries: the old sizing overdrew up to 1.99px onto the next tile, the new one abuts
  exactly (0px gap or overlap, every pair). Same change clips the image div (`overflow:
  hidden`), because the pyramid's last column and row are padded to a full tile with white
  and were rendering past the image's real edge.

  Original diagnosis, kept for the reasoning:
- **The tile-seam fix loses real image content, not just cosmetic.** `dzsave` is called
  with `"overlap": 0` (`image_tiler.py` ~line 189) — tile files carry no shared border
  pixels. The viewer papers over the resulting hairline seams entirely in CSS: each tile
  `<img>` is sized `Math.ceil(tileDisp) + 1` (`CanvasView.tsx` ~line 341) so it overlaps its
  neighbour by ~1px. But that stretches the *whole tile raster* slightly to fill the extra
  pixel — it doesn't reveal real neighbouring pixels, because none exist in the file. Since
  browsers paint later DOM siblings on top, whichever tile is later in `visible.map()`'s
  order (bottom/right neighbours) has its own real edge content silently overwritten by the
  earlier tile's stretched, interpolated overflow. Net effect: up to ~1px of genuine image
  content is lost at every one of the hundreds of tile boundaries, worst on fine detail
  (hairline rules, small print) that happens to fall near a 512px-aligned edge — which is
  common on a puzzle sheet.
  - **Fix shape:** give `dzsave` a real `overlap` (e.g. `overlap=1`, the libvips-standard
    value for exactly this problem) so tile files genuinely contain a border of true
    neighbouring pixels, then have the viewer crop that border into view instead of
    CSS-stretching a whole tile. That's a coordinated change to both `image_tiler.py` (tile
    dimensions grow by `2 × overlap`, so `_tile_bounds`-style math and the "tiles get no
    `media` rows" tile addressing need to account for it) and `CanvasView.tsx` (source-crop
    instead of blind stretch). Not a one-line change — needs its own pass.

## Future enhancements (requested 2026-07-27, not started)

User feedback on the shipped B1/B2 canvas, not yet designed or scheduled:

- ~~**Explicit "no puzzle" option in the region link control.**~~ **Done 2026-07-27.** A
  third `SegmentedControl` segment, "No puzzle", now precedes the other two. Saving in
  that mode sends `puzzle_id: null` / `role: null` regardless of what the Select still
  holds, which is the existing clearing path (backend already covered by
  `test_clearing_the_link_deletes_the_evidence_row`); the Select keeps its value so
  flipping back restores the choice. An existing *unlinked* region now opens on "No
  puzzle" rather than misreporting itself as "Existing puzzle", but a freshly drawn shape
  still opens on "Existing puzzle" — drawing in order to link is the common case and it
  should not cost an extra click. Original note:
- **Explicit "no puzzle" option in the region link control.** `RegionModal.tsx`'s
  `SegmentedControl` (~line 205) only offers "Existing puzzle" / "Create a new puzzle".
  Leaving a region unlinked is *technically* possible today — the "Existing puzzle" mode's
  `Select` is `clearable` with a "Not linked" placeholder — but there is no direct
  "don't link this" choice, so a user in "Create a new puzzle" mode has no way back to
  unlinked without switching modes and clearing the select. Worth a third segment.
- ~~**Highlight all regions linked to one puzzle.**~~ **Done 2026-07-27.** Two ways in,
  one effect: hovering a region lights up its puzzle's whole set (heavier stroke, stronger
  fill), and a toolbar picker pins a puzzle so the set stays lit while panning around
  looking for the rest of it — hover wins while active. The picker lists only puzzles with
  regions on *this* canvas. Keyed on the puzzle rather than the region, so hovering an
  unlinked region highlights nothing extra instead of lighting up every other unlinked
  region. Original note:
- **Highlight all regions linked to one puzzle.** `CanvasView.tsx` tints each region
  independently by its own linked puzzle's status (`tintFor`, ~line 13); there's no way to
  see, at a glance, which regions on a busy sheet all belong to the same puzzle (e.g. on
  hover/click of one region, or from the puzzle side).
- ~~**Carriage returns in evidence "log a clue".**~~ **Done 2026-07-27.** Three changes,
  not one — the field alone would not have held the line breaks:
  - The entry field is an autosizing `Textarea` (1–8 rows). Enter inserts a break,
    **Ctrl/⌘+Enter logs**; Enter cannot do both.
  - `extractClueText` no longer collapses whitespace, so OCR's line structure survives.
  - The row's *inline editor* was also a single-line `TextInput` that commits on blur, so
    merely clicking a multi-line note to edit it would have flattened it. Now a matching
    `Textarea`, same Ctrl/⌘+Enter commit, Escape still cancels.
  - The read-only note now renders `white-space: pre-wrap`; HTML collapses the breaks
    otherwise. No backend change: the routes only `.strip()`, and the note travels as a
    single string.
- **Carriage returns in evidence "log a clue".** `EvidencePanel.tsx`'s clue note field is a
  single-line `TextInput` that submits on Enter (see the comment above `extractClueText`,
  ~line 96), so OCR results — which do get newlines right — are collapsed to spaces on
  entry. This was a deliberate simplification when the note field was single-purpose; now
  that OCR feeds it, a multi-line `Textarea` (matching `RegionModal`'s notes field) with a
  separate submit affordance (e.g. mod+Enter) would preserve them.
- **Overlapping region shading.** Regions render as independent semi-transparent fills
  (`fillOpacity={tint ? 0.28 : 0}` in `CanvasView.tsx` ~line 374); where two regions
  overlap, their fills simply stack, darkening the overlap with no indication of which
  puzzle(s) own it. Not yet designed — could be flagged as a warning, drawn with a distinct
  hatch, or left as-is if overlaps turn out to be rare in practice.
- **Freehand/line/text-box annotations.** Explicitly out of scope above ("Polygons,
  freehand, and any second drawing tool"). A simple coloured-pen layer (freehand strokes,
  straight lines, text boxes) is a materially different feature from region-linking — it
  would need its own storage shape (stroke paths / text position, not a 0-1 box) and its
  own render layer alongside the region overlay. Feasible, but a separate design pass, not
  a small addition to B2.
- ~~**Replace an evidence row's image in place.**~~ **Done 2026-07-27.** `PATCH
  /api/evidence/<id>` now takes `media_id`; the row's Image cell gains a Replace button
  (Add image when there is none), which uploads through the existing media route and then
  points the row at the new id. Note, target and table position are all kept.
  - **The open question is answered by precedent, not by a new rule:** the old media row
    and its file are left alone. Deleting an evidence item *already* leaves its media
    behind — nothing on this path garbage-collects media — so keeping it is both the
    consistent behaviour and the one that cannot destroy the last copy of an image.
  - The key must be **absent** to mean "no change": treating a missing `media_id` as
    `None` would silently strip the image off every note edit. An explicit `null` detaches.
    Both pinned by tests, and both proved by mutation.
  - New guard: the media must belong to the same hunt. The id comes from the client and
    the media route itself is hunt-visibility gated, so this was the one way to read past
    it. Also mutation-proved.
- **Replace an evidence row's image in place.** Today an evidence item's media can't be
  swapped — `EvidencePanel.tsx`'s row actions (~line 439, `onDelete`) only support deleting
  the whole clue and re-logging a new one. A user attaching a clearer version of the same
  clue (via clipboard paste or file upload, reusing the existing `onCluePaste`/upload path)
  should be able to update just the image and keep the note/history. Needs a decision on
  whether the old `media` row is deleted or orphaned/kept for history.
- ~~**Show the canvas as the source on the web Evidence board.**~~ **Done 2026-07-27.**
  `get_evidence` now batches a `resolve_region_names` lookup alongside the puzzle and
  sheet ones and serialises `source_region_id`/`source_region_name`; the DAO's fallback
  chain runs `label → canvas name → "Canvas (filename)" → "Canvas <id>"`, so the Sheets
  label gets the deeper fallback too. The chain is composed in Python rather than as a SQL
  `CASE`, so which branch a row lands on is testable; mutation-proved by deleting the
  filename branch (reds three tests, on that branch). Showing regions also surfaced two
  adjacent bugs in the board's Source *filter*, fixed at the same time: region-sourced
  rows fell into the `else` and were offered under "No source", and the `none` predicate
  matched them too. Original note:
- **Show the canvas as the source on the web Evidence board.** Two gaps, not one:
  - The web dashboard doesn't show it at all. `get_evidence` (`bot/web/data.py` ~line
    656–681) resolves `source_puzzle_name` and `source_sheet_name` but never looks at
    `source_region_id`, so a canvas-sourced clue's `sourceLabel()`
    (`EvidencePanel.tsx` ~line 39) falls through to the em-dash case — the Google Sheet
    contribution table already gets this right (see next point) but the web board doesn't.
  - Even where it *is* resolved (`evidence_source_label`, `puzzle_service.py` ~line 119,
    and `MySQLCanvasRegionDb.get_names_by_ids`, `mysqldb.py` ~line 1163), the fallback is
    "region label, else `hunt_canvases.name`" — and `name` defaults to `''`
    (`033_canvases.sql` ~line 12), so an unrenamed, unlabelled region renders as a *blank*
    source today, not a filename. Requested behaviour: fall back further to
    `Canvas ({media.filename})` (media's `filename` column,
    `030_media_and_sourceless_evidence.sql` ~line 25) when the canvas has no `name`, and to
    the canvas's `name` when it has been renamed. Needs the same fallback wired into both
    the web board (new gap) and the Sheets label (existing but wrong fallback).

## Relevant files

- `bot/store/evidence_data.py`, `bot/store/mysqldb.py` (`MySQLEvidenceDb`, ~line 784)
- `bot/core/puzzle_service.py` — `log_clues`, `refresh_evidence`,
  `link_informs_for_evidence` / `unlink_informs_for_evidence` (~2730–2890)
- `bot/core/contribution.py` — `contribution_rows`, the pure row builder
- `bot/core/message_media.py` — `media_root`, `safe_filename`, `download_attachment`
- `bot/core/notifier.py` — `board_changed`; `@pushes_board()` in `puzzle_service`
- `bot/web/api.py` — `/api/attachments/<id>` (~line 168), the model for media serving
- `bot/web/auth.py` — `require_organiser`, `user_can_manage`
- Plan C: `bot/store/mysqldb.py` (`MySQLAdditionalSheetsDb`, ~line 303 — `get_by_puzzle`
  only), `bot/cogs/puzzles.py` (`add_sheet` ~1278, `remove_sheet` ~1299,
  `list_additional_sheets` ~1316, `with_puzzle_mutex` ~49, `_PUZZLE_LOCKS`),
  `bot/web/data.py:266` (the inner join to widen), `migrations/000_base_schema.sql:121`
  (the `additional_sheets` table as it stands)
- `frontend/src/pages/HuntBoard.tsx` — the `SegmentedControl` view switcher (~line 111)
- `frontend/src/components/EvidencePanel.tsx`, `frontend/src/types.ts`, `frontend/src/api.ts`
- `docs/commands.md` — command reference, kept in step with docstrings

## Data model

Three new tables. `media` exists so that "upload from the dashboard" and "promote a Discord
attachment" are one code path rather than two sets of duplicated columns.

```sql
media           id, hunt_id, path, content_type, size,
                origin, created_at
hunt_canvases   id, hunt_id, media_id, name, note, created_at
canvas_regions  id, canvas_id, shape, label, note,
                puzzle_id NULL, role NULL, created_at
```

- `media.path` is relative, resolved under `config.web_archive_media_dir` — the same root
  the message bridge already writes to.
- `media.origin` records where the bytes came from (`upload`, `url`, `discord`).
- `media` stores `width`, `height`, `tile_status` and `tile_levels`. **This reverses the
  original decision to store no pixel dimensions** (amended 2026-07-26). That decision was
  justified by normalised coordinates — true for regions, but a tile pyramid needs the
  source dimensions to know how many zoom levels exist and how many tiles occupy the
  bottom one, and the browser cannot report the natural size of an image it is never able
  to decode.
- `canvas_regions.shape` is JSON: `{"type":"rect","points":[[x1,y1],[x2,y2]]}`, all values
  in 0–1.
- `canvas_regions.label` is a short caption shown on the overlay and used as the source
  name in rendered tables; `canvas_regions.note` is the free-text body (typically a
  transcription). Both optional.
- `canvas_regions.role` is `locates` or `informs`, and is NULL exactly when `puzzle_id` is
  NULL.

Changes to `evidence_items`:

- `source_puzzle_id` becomes **nullable**.
- add `source_region_id INT NULL`.
- add `source_sheet_id INT NULL`.
- add `media_id INT NULL`.

At most one of the three source columns is set. This is not enforced by a constraint —
MySQL check constraints on this project's version are not used anywhere else in the schema,
and the invariant is owned by the single service method that writes each kind.

Changes to `additional_sheets`:

- add `hunt_id INT NULL` with a FK to `hunts` and `ON DELETE CASCADE`, so a sheet that
  belongs to no puzzle is still reachable and still gets cleaned up.

Per the project's **migration → deploy → code** rule these ship as migrations first; the
generic `commit()` in `mysqldb.py` writes every dataclass field by name, so adding the
fields to `EvidenceData` before the column exists breaks existing writes.

Migration numbering starts at `030_`. Check for collisions with any parallel worktree
before merging — parallel branches can both claim the same number and git will merge them
cleanly without noticing.

## Tiling

Amended 2026-07-26. The original design served a canvas as a single `<img>` with a CSS
transform for pan and zoom. Edric's extravaganza sheet — the image this feature exists for
— is 14400×14400. That is 207 megapixels, roughly 830 MB decoded as RGBA. Desktop browsers
thrash on it and mobile Safari refuses it outright, so the single-`<img>` viewer cannot
display the one picture that motivated the feature. The hunt distributed 4800×4800 slices
for downloading, but those are download chunks rather than tiles: 23 MP each, with no
overview level, so a fit-to-screen view would still mean decoding all nine.

So the server derives a tile pyramid at upload time.

- **`pyvips`**, installed as `pyvips[binary]` — the wheel bundles libvips (8.18.4), so it
  is a plain pip install needing no root and no system packages on the Plesk host. This is
  the dependency the original design avoided; it is now warranted.
- **`Image.dzsave`** with `layout="google"`, `tile_size=512`, `overlap=0`,
  `depth="onetile"` — one call produces every zoom level down to a single tile.
- **Lossless WebP.** Measured on the real image, lossless is both smaller *and* exact:
  9.6 MB against 11 MB for lossy Q82. Flat white line art is the case where lossless wins
  outright, and this content is small black lettering that must stay readable.
- **`VIPS_CONCURRENCY=2`.** Measured: 13.6 s/224 MB at 1, 8.1 s/340 MB at 2, 6.7 s/563 MB
  at 4, 6.9 s/982 MB unbounded on a 12-core box. Past 2 threads it buys about a second for
  triple the memory, which is the wrong trade on a small VPS.
- Result for the motivating image: **6 zoom levels, 940 tiles, 9.6 MB, in ~8 s.**

Storage sits beside the uploads that Plan A already writes, under the same
`config.web_archive_media_dir` root:

```
uploads/{hunt_id}/{uuid}_{name}.png          ← Plan A, exists
tiles/{hunt_id}/{media_id}/{z}/{y}/{x}.webp  ← new (note: google layout is z/y/x)
```

- Keyed by **`media_id`, not filename**: regeneration is idempotent (remove one directory
  and rebuild) and deleting a `media` row deletes exactly one directory. A uuid-keyed
  layout would leave orphans that could never be swept with confidence.
- A **sibling under the same root**, so the existing `resolve()`-against-root guard in
  `bot/web/api.py` covers tiles with no second traversal check and no second backup target.
  Sharing the root was decided for exactly this.
- **Tiles get no `media` rows.** A pyramid is hundreds of files per canvas, and `media` is
  hunt-scoped and FK-cascaded for user-facing images. Tiles are addressed by convention
  from their source row instead.
- `dzsave` **omits fully blank tiles** and writes a `blank.png` alongside. The tile route
  must treat a missing tile as blank rather than as an error.

**Tiling never runs on the event loop.** Eight seconds of CPU on the loop would stall the
bot's gateway heartbeat, and this project already carries a known deferred issue about
event-loop-blocking Sheets calls. The upload route validates, stores, inserts the row with
`tile_status='pending'` and returns; tiling runs in an executor and flips the row to
`ready` (or `failed`). The frontend shows progress until it is ready.

The existing upload cap already admits the motivating file: `web_attachment_max_bytes`
defaults to 25 MiB and the sheet is 16.5 MB. A larger sheet would not fit, so canvases may
later want their own cap; not changed now.

## Behaviour

### Linking a region

A new module `bot/core/canvas.py` owns this. It does **not** go into `puzzle_service`,
which is already ~2,900 lines and is called out as a contention magnet.

- **role=locates** — the link is all there is. The overlay tints the region by the linked
  puzzle's status.
- **role=informs** — the service creates a real `evidence_items` row with
  `source_region_id` set, `source_puzzle_id` NULL, and `target_puzzle_id` set to the linked
  puzzle. Everything downstream — routing, the evidence panel, the contribution table —
  then works with no special cases.
- **Changing the role, relinking, or clearing the link** deletes that evidence row. A
  single service method owns both sides so the region and its evidence row cannot drift
  apart.
- **Deleting a region or a canvas** cascades to its evidence rows.
- Region mutations call the existing `board_changed` notifier so a second person
  annotating sees regions appear live.

### Creating a puzzle from a region

Draw a box, give a name, pick a round, choose the role. The service calls the existing
`puzzle_service` create path unchanged, then links the region to the returned puzzle. No
duplicate creation logic, and no new "half-real puzzle" concept.

Locking: `bot/core/` is lock-free by convention and callers lock. The canvas routes must
follow the same discipline as the existing create routes, and must not take a lock the
create path will take again internally.

### Sourceless evidence

`source_puzzle_id` being nullable is a feature in its own right: a fact that belongs to the
hunt rather than to any one puzzle. Consequences:

- `MySQLEvidenceDb.log()` widens to accept a NULL source.
- `link_informs_for_evidence` / `unlink_informs_for_evidence` are **skipped** when there is
  no source puzzle. An `informs` edge joins two puzzles; a picture or a bare observation is
  not one.
- `log_clues` currently derives auto-routing from the source puzzle's sole informs target
  (`_sole_informs_target`). With no source there is nothing to derive, so sourceless
  evidence is created untargeted and routed by hand in the panel.
- The evidence panel's source filter and its `source_puzzle_name` sort need a null case.
- `refresh_evidence` renders the hunt-wide Evidence tab with a Source Puzzle column; a null
  source must render as an em dash, not `Puzzle None`. Region-sourced rows render the
  region's label or its canvas name.

### Hunt-level working sheets

A working sheet can belong to the hunt rather than to a puzzle — somewhere to pick at
something found in a page's alt text, or loose on the hunt site, before anyone knows which
puzzle it belongs to (or whether it belongs to one at all).

The groundwork is half-there and half-missing, which is worth knowing before estimating it:

- `additional_sheets.puzzle_id` is **already nullable** (migration 022's cleanup explicitly
  guards `WHERE a.puzzle_id IS NOT NULL`).
- But there is **no `hunt_id` column**, so a puzzle-less row is unreachable. Every read path
  goes through a puzzle: `MySQLAdditionalSheetsDb` exposes only `get_by_puzzle()`, the rows
  load as `puzzle.additional_sheets` through `construct_puzzle`'s `SPECIAL_ATTR`, and
  `!add_sheet` / `!list_sheets` / `!remove_sheet` all require a puzzle channel.
- `bot/web/data.py:266` **inner**-joins `puzzles`, so hunt-level sheets would be invisible
  to the dashboard until that becomes a left join.
- `get_additional_sheet_spreadsheet(ctx, puzzle, hunt)` already accepts a hunt, so the
  which-spreadsheet decision needs no new seam.

So the change is: add `hunt_id`, add a `get_by_hunt()` that returns puzzle-less rows, let
the three sheet commands run in a hunt channel, and widen that join.

Confirmed by reading the command rather than inferring it: `!add_sheet`
(`bot/cogs/puzzles.py:1284`) calls `get_puzzle(ctx)` and returns
`send_not_puzzle_channel(ctx)` when it is None, so it refuses in the hunt general channel
today.

**Locking gap to close as part of this.** `with_puzzle_mutex` serialises on
`_PUZZLE_LOCKS[puzzle.id]` but explicitly no-ops when there is no puzzle in context
(`bot/cogs/puzzles.py:61` — *"If not in a puzzle context, just run"*). A hunt-level
`!add_sheet` therefore runs unserialised, and two people adding one at once would race on
the Sheets API. Needs a hunt-keyed lock mirroring `_PUZZLE_LOCKS`, not a reuse of the
puzzle one.

Such a sheet can then be **named as an evidence source** — `evidence_items.source_sheet_id`,
nullable. This is the low-priority half by the user's own reckoning: sourceless evidence
already covers the common case, and this exists so the option is there when the sheet really
is the provenance. It costs one nullable column and one branch in
`evidence_source_label()`, the seam Plan A already introduces for exactly this purpose.

Like a canvas region, a sheet source creates **no `informs` edge** — an edge joins two
puzzles, and a working sheet is not one.

### Contribution tables

`contribution_rows` keys every row off a member puzzle (`informs:<pid>`), so evidence with
no source puzzle has no member row to hang from. After the member loop, append rows for
sourceless evidence routed to this target, keyed `informs:free:<eid>`.

The builder's `evidence` argument today is a list of `{"id", "source_puzzle_id",
"content"}`. It gains an optional `"source_label"`, and `source_puzzle_id` may be None.
The Source column for a sourceless row is that label — the region's label, else its
canvas's name, else an em dash for evidence with no source at all. Rows with a
`source_puzzle_id` are grouped and rendered exactly as they are now.

The key namespace is distinct from `informs:<pid>`, and the reconciler diffs purely on
column A, so these rows survive re-tagging like every other row.

Explicit consequence: **no `informs` edge is created** for sourceless evidence, so the
target gains a contribution row without gaining a member. This is intended. It also means
such a row cannot be reached by the subtree walk, which follows `feeds` edges only.

Cell content is the evidence `note` (the transcription, where there is one). Where the item
has `media_id`, the cell also carries the dashboard media URL.

Two constraints bite here, both deliberate and both left intact:

- **No `=IMAGE()`.** Google fetches those from its own servers, unauthenticated, and our
  media route requires a Discord session. Creating an unauthenticated URL to satisfy it
  would expose team working to anyone holding the link.
- **No `=HYPERLINK()` either.** `_values_update` writes `valueInputOption="RAW"`
  (`bot/cogs/puzzles_gsheet.py:830`) specifically so a solved answer like `=A1` is stored as
  text instead of becoming a live formula that resolves to `#REF` once the puzzle moves to
  the solved sheet. A formula written under RAW appears verbatim as text. Switching the
  content column to `USER_ENTERED` would reintroduce that bug, because that same column
  carries arbitrary user-written notes.

So the URL goes in as **plain text** to be copied. A genuinely clickable cell would need a
rich-text write path (`updateCells` with `textFormatRuns[].format.link`) rather than
`values().update`, which is a larger change to the roll-up writer than this earns. Noted as
a possible follow-up, not built.

## Web API

- `GET /api/media/<id>` — serves bytes. Modelled directly on `/api/attachments/<id>`,
  including its `resolve()`-against-root traversal guard and inline-vs-download handling.
  Authenticated, hunt-scoped.
- `GET /api/media/<id>/tiles/<z>/<y>/<x>` — serves one tile, authorised against the source
  row's hunt exactly as `/api/media/<id>` is. `z`, `y` and `x` are integers and are never
  interpolated into a path before validation. A tile absent because `dzsave` judged it blank
  returns the blank tile, not a 404.
- `POST /api/hunts/<uid>/media` — multipart upload, or `{"url": ...}` fetched server-side
  via `message_media.download_attachment`, or `{"attachment_id": ...}` to promote an image
  the message bridge has already captured into a `media` row.
- `GET/POST /api/hunts/<uid>/canvases`, `GET/PATCH/DELETE /api/canvases/<id>` — the GET
  returns regions inline.
- `POST /api/canvases/<id>/regions`, `PATCH/DELETE /api/canvases/<id>/regions/<rid>`.
- `POST /api/canvases/<id>/regions/<rid>/create-puzzle`.

Validation:

- Size capped by the existing `web_attachment_max_bytes`.
- Content-type **allowlist**: `image/png`, `image/jpeg`, `image/gif`, `image/webp`.
  **SVG is rejected** — it is a scriptable document, and serving one from our own origin
  would be stored XSS.
- The declared content type is not trusted; sniff the magic bytes.
- Filenames go through the existing `safe_filename`, and stored paths are never taken from
  user input.

Permissions:

- Creating, drawing, linking and editing regions: the same bar as logging evidence today —
  any authenticated user who can see the hunt, and only while the hunt is unarchived (the
  evidence panel's existing `editable = !detail.hunt.archived`). Everyone marks the picture
  up during a hunt.
- Creating a puzzle from a region: exactly what the existing create path requires.
- Uploading or deleting a canvas: `can_manage`.

## Frontend

- A **Canvas** entry in the existing `SegmentedControl` in `HuntBoard.tsx`, alongside By
  group / All puzzles / Evidence. Its width calculation is hard-coded and must be updated.
- `components/CanvasPanel.tsx` — canvas picker and upload.
- `components/CanvasView.tsx` — a tiled viewer. Only the tiles intersecting the viewport at
  the current zoom level are mounted, over an absolutely-positioned SVG overlay at
  `viewBox="0 0 1 1"` and `preserveAspectRatio="none"`, so region coordinates still need no
  arithmetic. Pan is a translate on the tile layer; zoom picks the pyramid level. While
  `tile_status` is `pending` the panel shows progress instead of the image.
- `components/RegionModal.tsx` — label, note, puzzle link, role, and the create-puzzle path.
- Live updates over the existing websocket.

Overlay tinting collapses the seven statuses into four colours so the picture stays
readable:

| colour | statuses |
| --- | --- |
| green | `solved`, `backsolved` |
| amber | `in progress`, `needs extraction`, `needs submission` |
| red | `stuck` |
| grey | `unstarted` |

Untinted image is art nobody has claimed — half the answer to "what have we done so far".
A legend and a claimed-count (`18/34`) sit alongside. The tint is a pure function of the
puzzle, so further lenses (priority, round, staleness) are cheap to add later.

## Discord

- `!clue` / `!clues` accept an attached image. Text becomes optional when an image is
  present. Bytes are fetched with `message_media.download_attachment` and a `media` row is
  created. Outside a puzzle channel the clue is logged sourceless rather than erroring.
- `!canvas` posts a link to the canvas view.
- Both docstrings and the matching rows in `docs/commands.md` are updated in the same
  change, per the house rule. Drawing itself is web-only — a deliberate parity exception in
  the same class as the archiving family.

## Testing

Following the project harness: factories from `tests/conftest.py`, DAOs patched at the
service import site, `AsyncMock` for anything awaited.

- `contribution_rows` with sourceless evidence — pure function, no mocks: key namespace,
  ordering, and coexistence with `informs:<pid>` rows.
- Canvas service: region CRUD; role transitions creating and deleting the evidence row;
  clearing a link; cascade on region and canvas delete.
- Create-puzzle-from-region delegates to the existing create path and links the result.
- `link_informs_for_evidence` is not called for sourceless evidence.
- `refresh_evidence` renders a null source as an em dash.
- Upload validation: oversize rejected, disallowed content type rejected, SVG rejected,
  content-type spoofing caught by sniffing.
- Media route: unauthenticated request rejected; path escaping the media root rejected.
- Tiler: a small image produces the expected level count and tile paths; dimensions and
  `tile_levels` are recorded on the row; a failure flips `tile_status` to `failed` and
  leaves no half-written pyramid.
- Tile route: unauthenticated request rejected; a blank (absent) tile serves the blank tile;
  non-integer or negative `z`/`y`/`x` rejected before any path is built.
- `!clue` with an attachment, and `!clue` outside a puzzle channel.

## Acceptance criteria

1. An evidence item can carry an image, free text, or both, and renders in the evidence
   panel.
2. An evidence item can be logged with no source puzzle, and routes to a target normally.
3. A contribution table shows sourceless evidence with its transcription and, where there
   is an image, the media URL as plain text.
4. `!clue` with an attached screenshot in a puzzle channel produces image evidence.
5. A hunt can hold several canvases; each renders its image with its regions.
5a. A 14400×14400 upload is tiled without blocking the bot, and pans and zooms smoothly in
    the browser at every level — including on a phone, which cannot decode the source at all.
6. Dragging a box creates a region; it can be linked to an existing puzzle as `locates` or
   `informs`, or can create a new puzzle with a Discord channel and sheet tab.
7. An `informs` region produces a real evidence row that appears in the evidence panel and
   in the target's contribution table.
8. The rendered canvas tints regions by puzzle status per the table above, shows a legend
   and a claimed count, and leaves unclaimed art untinted.
9. Two people annotating the same canvas see each other's regions without reloading.
10. No unauthenticated URL serves any uploaded image.
11. `!add_sheet` in a hunt channel creates a working sheet belonging to the hunt, not to a
    puzzle, and two people running it at once do not race.
12. `!list_sheets` in a hunt channel lists the hunt's own sheets, and the dashboard shows
    them.
13. An evidence item can name a hunt-level sheet as its source, and that name renders in
    the evidence panel and in the target's contribution table.

## Build order

Each plan is independently shippable. They share one generalisation — an optional evidence
source — which Plan A introduces and the other two extend through the same
`evidence_source_label()` seam.

**Plan A — image evidence.** `media` table and migrations, upload and serve, nullable
evidence source, `!clue` attachments, evidence panel rendering, contribution and Evidence
tab rendering. Ships and is useful with no canvas at all.

**Plan B — canvas.** Split in two once tiling entered the design; B1 is a clean subsystem
with its own boundary (image in, tiles out, tiles served) and is useful before any canvas
exists, since it is what makes a huge image viewable at all.

- **B1 — tiled media pipeline.** The `pyvips` dependency, `media.width`/`height`/
  `tile_status`/`tile_levels`, the tiler module, off-loop tiling at upload, the tile route.
- **B2 — the canvas.** `hunt_canvases` and `canvas_regions`, the canvas service, the region
  API, the canvas view and region modal, draw-to-create, the progress overlay, `!canvas`.

**Plan C — hunt-level working sheets.** `additional_sheets.hunt_id`, `get_by_hunt()`, a
hunt-keyed command lock, `!add_sheet` / `!list_sheets` / `!remove_sheet` in a hunt channel,
the dashboard's inner join widened to a left join, and `evidence_items.source_sheet_id`
with its branch in `evidence_source_label()`.

Plan C is the smallest of the three and depends on Plan A only for the source-label seam —
the sheet work itself could land first if a hunt needs it sooner.
