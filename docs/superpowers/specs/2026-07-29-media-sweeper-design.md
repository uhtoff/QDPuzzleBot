# Media sweeper — design

Date: 2026-07-29
Status: designed, not started

## Goal

Nothing in puzzbot ever deletes a `media` row or its on-disk file individually — replaced
evidence images are kept by documented decision, `delete_canvas` rmtrees a canvas's tile
pyramid but leaves the row and original file, every re-crop creates a new row, and
detaching a puzzle image (`puzzle_images.detach`) only deletes the join-table row. This is
individually justified per call site but collectively an unbounded disk leak on the Plesk
host, flagged as unscheduled work in `docs/reviews/2026-07-28-canvas-epic-review.md`
("No media GC").

This adds a manually-triggered sweep that finds and deletes **orphaned** media — rows
nothing points at any more — reclaiming disk without ever touching an image still in use.

## What counts as orphaned

A `media` row is orphaned when **none** of the following reference it, and it is **older
than a 24-hour grace period**:

- `evidence_items.media_id`
- `hunt_canvases.media_id`
- `puzzle_images.media_id`
- `canvas_regions.crop_media_id`

These are the only FKs into `media` (confirmed by grep across `migrations/*.sql`). The
grace period exists because `bot/core/media_ingest.py`'s `ingest_image_bytes` creates the
`media` row *before* the caller attaches it to anything (a puzzle image, an evidence item,
a canvas, a region crop) — without it, a sweep run mid-upload could delete an image whose
attach step just hasn't happened yet.

Deleting an orphaned row is safe under the existing FK cascade rules (already correct,
verified in `migrations/030`, `033`, `034`, `035`):
- `media_markup.media_id` — `ON DELETE CASCADE` (ink goes with its image)
- `hunt_canvases.media_id` — `ON DELETE CASCADE` (can't happen here: a row referenced by
  a canvas isn't orphaned by definition)
- `puzzle_images.media_id` — `ON DELETE CASCADE` (same reasoning)
- `evidence_items.media_id`, `canvas_regions.crop_media_id` — `ON DELETE SET NULL` (same
  reasoning)

Sweeping also happens to clean up `delete_canvas`'s known leftover: it removes the tile
directory but never the media row, so after a canvas delete that row is immediately
orphaned and gets picked up by the next sweep.

## Scope (per your decisions)

- **Trigger:** manual only — a Discord command pair and a web dashboard panel, both
  calling the same `bot/core/` service function. No cron/scheduled sweep.
- **Grace period:** fixed at 24 hours, a module constant — not a per-hunt setting.
- **Blast radius:** per-hunt, like other maintenance commands (`!reconcile`,
  `!rebuild_unassigned`) — never a cross-hunt sweep in one call.
- **Cleanup depth:** full — media row, original file, and tile pyramid directory if one
  exists (same removal `delete_canvas` already does for tiles).

Explicitly out of scope: an age-threshold setting, a per-image "protect from GC" flag,
automatic/scheduled sweeping. None of these are needed for the stated problem (reclaim
leaked disk on request) — add them later only if a real need shows up.

## Backend

### `bot/store/mysqldb.py` — `MySQLMediaDb`

- `get_orphaned(hunt_id, older_than: datetime) -> list[MediaData]` — one query, `media`
  LEFT JOINed against the four referencing tables on their FK-to-media columns, filtered to
  `hunt_id = %s AND created_at < %s AND` all four join columns `IS NULL`.
- `delete(media_id) -> None` — deletes the row. (Currently doesn't exist at all — noted as
  a gap in the review doc.)

### `bot/core/media_gc.py` (new, ctx-free service module, style matches `puzzle_images.py`)

```python
GRACE_PERIOD = timedelta(hours=24)

@dataclass
class SweepStats:
    count: int
    bytes_freed: int

def orphaned_media(hunt_id: int) -> list[MediaData]: ...
def sweep(hunt_id: int) -> SweepStats: ...
```

`sweep` iterates `orphaned_media(hunt_id)` and, per row: best-effort `shutil.rmtree` the
tile pyramid dir if `tile_status != "none"` (mirrors `delete_canvas`'s
`ignore_errors=True` + logged-exception pattern — a stuck tile dir must not block the
delete), best-effort `unlink` the original file, then `MediaJsonDb.delete(media_id)`.
Returns the count and summed `size` of everything actually removed from the DB (a file/tile
failure doesn't stop the row from being counted — the row is gone either way).

No new lock: `bot/core/locks.py`'s registry (`puzzle_lock`, `puzzles_lock`,
`hunt_create_lock`, `hunt_sheet_lock`, `rollup_lock`) has no hunt-wide or media lock, and
`!reconcile`'s `delete_orphans` — the closest existing precedent for a confirmed bulk
delete — doesn't take one either. A stats/sweep mismatch (something gets attached between
the report and the sweep) just means that row survives to the next run: not a correctness
issue, since `sweep` re-runs `orphaned_media` itself rather than trusting the earlier
report.

## Discord commands (`bot/cogs/puzzles.py`, Maintenance section)

Organiser/admin only, run in a hunt channel — same gating as `!reconcile`.

- **`!media_stats`** — report only: `media_gc.orphaned_media(hunt.id)` count and total size
  (human-readable, e.g. "12 orphaned image(s), 34.2 MB"). No mutation, no confirm.
- **`!media_sweep`** — runs the same report, then `_confirm_destructive` (existing
  `ConfirmView` helper used by `!delete_hunt`/`!reconcile apply orphans`), then
  `media_gc.sweep(hunt.id)` and reports what was actually freed.

Docstrings follow house style (`Usage:` / `Example:` / permissions line) and both rows get
added to `docs/commands.md`'s Maintenance table.

## Web

Mirrors the existing `!reconcile` / `ReconcilePanel` / `useReconcile` shape exactly.

- `GET /api/hunts/<uid>/media/stats` — report, `require_manage`.
- `POST /api/hunts/<uid>/media/sweep` — apply, `require_manage`, `_audit("sweep_media", ...)`.
- `frontend/src/api.ts` — `useMediaStats`/`useMediaSweep` hooks alongside `useReconcile`.
- `frontend/src/components/MediaSweepPanel.tsx` (new) — "Report" button showing orphan
  count/size, "Sweep" button gated behind `TypedConfirmModal` (same as
  `ReconcilePanel`'s orphan-delete confirm).
- Mounted on `HuntBoard.tsx` next to `<ReconcilePanel>`, same `can_manage` gate.

## Docs

- `docs/commands.md` — two new Maintenance rows for `!media_stats` / `!media_sweep`.
- `docs/user-guide.md` — the puzzle-images section (~line 246-253, "Removing an image...")
  and the clue-replacement note (~line 289, "The old picture is kept, not deleted") both
  currently leave the reader with the impression detached/replaced images are permanent
  leftovers with no cleanup story. Add a short note that they're reclaimed by the
  maintenance sweep (organisers: `!media_sweep` or the hunt's Maintenance panel), not
  deleted immediately.

## Testing

- `tests/test_media_gc.py` (new) — orphan detection (referenced by each of the 4 FKs
  individually stays; unreferenced-but-within-grace-period stays; unreferenced-and-old is
  swept), sweep deletes row + file + tile dir, sweep tolerates a missing file/tile dir
  (already-gone-on-disk doesn't block the DB delete).
- Extend `tests/test_puzzles_maintenance.py` (or wherever `!reconcile`'s command test
  lives) with `!media_stats`/`!media_sweep` command tests: permission gate, confirm-then-
  apply flow, report-only leaves rows untouched.
- Extend `tests/test_media_web.py` with the two new routes: `require_manage` gate, stats
  shape, sweep + audit call.

## Acceptance criteria

- `!media_stats` in a hunt channel reports orphan count/size and mutates nothing.
- `!media_sweep` confirms, then deletes exactly the rows `orphaned_media` reported, their
  files, and their tile directories; rows referenced by any of the 4 FKs are never touched;
  rows younger than 24h are never touched even if unreferenced.
- The web Maintenance panel offers the same two operations behind `can_manage` and reports
  the same numbers as the bang commands.
- `docs/commands.md` and `docs/user-guide.md` reflect the new commands/behaviour.
