# Image evidence + the hunt Canvas

Date: 2026-07-26
Status: design — approved direction, pending spec review

## Goal

Two related capabilities, driven by Edric's hunt this year:

1. **Evidence can be a picture.** Today `evidence_items` carries a text `note` and nothing
   else. A screenshot, a crop, a photo of a physical component — none of it can be logged
   as evidence, so it lives in Discord scrollback and is lost to the contribution tables.
2. **A hunt can hold a big image you mark up.** Draw rectangles on it, link each rectangle
   to a puzzle, and render the whole image tinted by progress so you can see at a glance
   what has been claimed, what is solved, and which parts of the art nobody has touched.

The second is the one general-purpose tools get wrong. Excalidraw and friends do far more
(full drawing app) and far less (no idea what a puzzle is) than this needs. The value here
is precisely the link between a region of the picture and a live puzzle record.

## Decisions (locked)

- **One design, two implementation plans.** Plan A (image evidence) ships independently of
  Plan B (canvas) and is useful on its own.
- **"Canvas", never "board".** `board` already means the group/puzzle summary throughout
  this codebase (`HuntBoard`, `data.board`, `notifier.board_changed`, the "By group" view).
  Reusing it for the picture would make the word denote two things.
- **A region is both kinds of thing, distinguished by its link role.** `locates` means
  "this part of the image *is* that puzzle"; `informs` means "this part of the image is a
  *clue for* that puzzle". Reuses the vocabulary already in the domain model.
- **Rectangles only**, but geometry is stored as a shape blob so other shapes are a later
  UI change, not a migration.
- **Coordinates are normalised 0–1**, never pixels.
- **Many canvases per hunt, flat.** No round/TagGroup scoping.
- **Draw-to-create.** Drawing a box can create a brand-new puzzle through the existing
  `puzzle_service` create path (Discord channel, sheet tab, tagged into a chosen round).
- **Separate tables; informs-regions spawn real evidence rows**, so routing, the evidence
  panel, and the contribution table all work unchanged.
- **Evidence sources become optional and three-way.** An evidence item's source is a
  puzzle, a canvas region, or nothing. Sourceless evidence is wanted in its own right, not
  merely as a consequence of canvas support.
- **Every image-bearing thing carries optional free text**, with transcription as the
  headline use. One field, not a separate `transcription` column.
- **No image thumbnails in Google Sheets.** A contribution cell shows the transcription
  plus a hyperlink back to the dashboard. No unauthenticated media URLs are created.
- **Discord gets image clues in, and a link out.** `!clue` accepts an attachment; `!canvas`
  posts a link. No server-side image compositing.

## Out of scope (deliberate)

Polygons, freehand, and any second drawing tool. Server-side PNG rendering of the canvas.
Per-round canvas scoping. Public/tokenised media URLs. Undo history beyond delete. OCR or
any automatic transcription. All are additive later; none are load-bearing now.

## Relevant files

- `bot/store/evidence_data.py`, `bot/store/mysqldb.py` (`MySQLEvidenceDb`, ~line 784)
- `bot/core/puzzle_service.py` — `log_clues`, `refresh_evidence`,
  `link_informs_for_evidence` / `unlink_informs_for_evidence` (~2730–2890)
- `bot/core/contribution.py` — `contribution_rows`, the pure row builder
- `bot/core/message_media.py` — `media_root`, `safe_filename`, `download_attachment`
- `bot/core/notifier.py` — `board_changed`; `@pushes_board()` in `puzzle_service`
- `bot/web/api.py` — `/api/attachments/<id>` (~line 168), the model for media serving
- `bot/web/auth.py` — `require_organiser`, `user_can_manage`
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
- No pixel dimensions are stored. The browser knows the image's natural size, and
  normalised coordinates mean nothing server-side needs it — which is what keeps Pillow out
  of the dependency list.
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
- add `media_id INT NULL`.

Per the project's **migration → deploy → code** rule these ship as migrations first; the
generic `commit()` in `mysqldb.py` writes every dataclass field by name, so adding the
fields to `EvidenceData` before the column exists breaks existing writes.

Migration numbering starts at `030_`. Check for collisions with any parallel worktree
before merging — parallel branches can both claim the same number and git will merge them
cleanly without noticing.

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
has `media_id`, the cell also carries a hyperlink to the dashboard's media view. No
`=IMAGE()`, because Google fetches those unauthenticated and our media route requires a
Discord session — and creating an unauthenticated URL would expose team working to anyone
holding the link.

## Web API

- `GET /api/media/<id>` — serves bytes. Modelled directly on `/api/attachments/<id>`,
  including its `resolve()`-against-root traversal guard and inline-vs-download handling.
  Authenticated, hunt-scoped.
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
- `components/CanvasView.tsx` — an `<img>` with an absolutely-positioned SVG overlay at
  `viewBox="0 0 1 1"` and `preserveAspectRatio="none"`, so normalised coordinates need no
  arithmetic at all. Pan and zoom are a CSS transform on the wrapper.
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
- `!clue` with an attachment, and `!clue` outside a puzzle channel.

## Acceptance criteria

1. An evidence item can carry an image, free text, or both, and renders in the evidence
   panel.
2. An evidence item can be logged with no source puzzle, and routes to a target normally.
3. A contribution table shows sourceless evidence with its transcription and, where there
   is an image, a working link.
4. `!clue` with an attached screenshot in a puzzle channel produces image evidence.
5. A hunt can hold several canvases; each renders its image with its regions.
6. Dragging a box creates a region; it can be linked to an existing puzzle as `locates` or
   `informs`, or can create a new puzzle with a Discord channel and sheet tab.
7. An `informs` region produces a real evidence row that appears in the evidence panel and
   in the target's contribution table.
8. The rendered canvas tints regions by puzzle status per the table above, shows a legend
   and a claimed count, and leaves unclaimed art untinted.
9. Two people annotating the same canvas see each other's regions without reloading.
10. No unauthenticated URL serves any uploaded image.

## Build order

**Plan A — image evidence.** `media` table and migrations, upload and serve, nullable
evidence source, `!clue` attachments, evidence panel rendering, contribution and Evidence
tab rendering. Ships and is useful with no canvas at all.

**Plan B — canvas.** `hunt_canvases` and `canvas_regions`, the canvas service, the API, the
canvas view and region modal, draw-to-create, the progress overlay.
