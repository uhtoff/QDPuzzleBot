# Puzzle Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a puzzle an ordered set of images — fed by its `locates` regions' crops and by direct upload — shown in `PuzzleDrawer`, each openable in the markup view.

**Architecture:** A materialised `puzzle_images` table (the list mixes two sources and has a user-chosen order, so it cannot be a query over `canvas_regions`). `canvas.py` gains a region→`puzzle_images` sync mirroring the evidence coupling that already lives beside it. `canvas_regions.crop_media_id` records a region's crop so it can be found — and so a crop carrying markup is never silently regenerated.

**Tech Stack:** Python 3.12, Quart, MySQL, pytest; React + TypeScript + Mantine + react-query + `@dnd-kit`.

**Source spec:** `docs/superpowers/specs/2026-07-27-canvas-markup-design.md` (the "Puzzle images" section).

**Depends on:** the image-markup plan, complete and committed (`8dc986b` → `d722a28`). `MarkupModal` is reused unchanged.

---

## Conventions for every task

- Tests: `timeout 300 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`; read the log. Timeout-kill exits 124.
- `pipenv run ruff check` clean; `pipenv run basedpyright bot/` 0 errors.
- Frontend: `cd frontend && npx tsc --noEmit && npm run build`. **No frontend test runner exists** — never claim behavioural verification from a green build.
- Full suite baseline: **1472 passed**.
- Patch DAOs at the service's own import site (`patch.object(module, "XJsonDb", ...)`).
- Commit ONLY the files each task names. `.ai/active/session-summary.md` is dirty by design — never stage it.

## File structure

| File | Responsibility |
|---|---|
| `migrations/035_puzzle_images.sql` (create) | `puzzle_images` + `canvas_regions.crop_media_id` |
| `bot/store/puzzle_image_data.py` (create) | `PuzzleImageData` dataclass |
| `bot/store/canvas_data.py` (modify) | `crop_media_id` on `CanvasRegionData` |
| `bot/store/mysqldb.py` (modify) | `MySQLPuzzleImageDb`; `crop_media_id` in the region DAO |
| `bot/store/__init__.py` (modify) | Bind `PuzzleImageJsonDb` |
| `bot/core/puzzle_images.py` (create) | Attach / reorder / detach / list |
| `bot/core/canvas.py` (modify) | The region ↔ `puzzle_images` sync |
| `bot/web/actions.py` (modify) | `locates` crops, the regenerate-under-ink guard, the image routes |
| `bot/web/data.py` (modify) | `puzzle_image_to_json` |
| `frontend/src/types.ts`, `api.ts` (modify) | `PuzzleImage`, hooks |
| `frontend/src/components/PuzzleImages.tsx` (create) | The strip: thumbnails, upload, drag reorder, remove |
| `frontend/src/components/PuzzleDrawer.tsx` (modify) | Mount the strip |

---

### Task 1: Migration

**Files:** Create `migrations/035_puzzle_images.sql`

Check no `035_*` exists first (parallel worktrees have collided before); renumber if so.

```sql
-- 035_puzzle_images.sql
-- A puzzle carries an ordered set of images, from two sources: a locates
-- region's crop ("this section IS the puzzle"), and a direct upload.
--
-- Materialised rather than derived from canvas_regions, because the two
-- sources mix in ONE ordered list and you cannot order a list where half the
-- entries exist only as a query result.
--
-- Deliberately NOT evidence_items: a locates region is not a clue about
-- anything, and one row per region on a big sheet would swamp the evidence
-- table. The puzzle's own picture belongs to the puzzle.

CREATE TABLE IF NOT EXISTS `puzzle_images` (
  `id`               INT(11)  NOT NULL AUTO_INCREMENT,
  `puzzle_id`        INT(11)  NOT NULL,
  `media_id`         INT(11)  NOT NULL,
  -- Set when this image came from a locates region; NULL for a direct upload,
  -- which is what keeps uploads untouched by any region change.
  `source_region_id` INT(11)  DEFAULT NULL,
  `position`         INT(11)  NOT NULL DEFAULT 0,
  `created_at`       DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  -- One region yields at most one image row: this is what makes the sync
  -- idempotent. MySQL permits many NULLs in a unique index, so direct uploads
  -- are unaffected.
  UNIQUE KEY `uniq_puzzle_images_region` (`source_region_id`),
  KEY `idx_puzzle_images_puzzle` (`puzzle_id`),
  CONSTRAINT `PuzzleImagePuzzleID` FOREIGN KEY (`puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `PuzzleImageMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  -- CASCADE, matching evidence_items.source_region_id (033): a section image
  -- is created BY the link and is meaningless without the region.
  CONSTRAINT `PuzzleImageRegionID` FOREIGN KEY (`source_region_id`)
      REFERENCES `canvas_regions` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- A region's crop, so it can be FOUND. Without this the only record of a crop
-- is the media_id on an informs region's evidence row, and a locates region
-- would have no route back to its own crop at all. SET NULL, not CASCADE: a
-- region outlives its crop.
ALTER TABLE `canvas_regions`
  ADD COLUMN `crop_media_id` INT(11) DEFAULT NULL,
  ADD KEY `idx_canvas_regions_crop` (`crop_media_id`),
  ADD CONSTRAINT `CanvasRegionCropMediaID` FOREIGN KEY (`crop_media_id`)
      REFERENCES `media` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;
```

- [ ] Write it. - [ ] `pipenv run python migrate.py migrate`, then `status` shows it applied. - [ ] Commit: `feat(puzzle-images): add the puzzle_images table and region crop column`.

---

### Task 2: Dataclasses and DAOs

**Files:** create `bot/store/puzzle_image_data.py`; modify `bot/store/canvas_data.py`, `bot/store/mysqldb.py`, `bot/store/__init__.py`

DAO SQL is not unit-tested in this project (no test hits MySQL) — inspection-verified, exercised through the service and route tests. Follow that convention.

- [ ] **Step 1: `PuzzleImageData`**, modelled exactly on `bot/store/canvas_data.py`'s `CanvasRegionData` (same `import_dict`, same tz reattachment):

```python
from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class PuzzleImageData:
    id: int = 0
    puzzle_id: int = 0
    media_id: int = 0
    # Set when this image is a locates region's crop; None for a direct upload.
    source_region_id: Optional[int] = None
    position: int = 0
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        p = cls()
        for attr in p.__dict__:
            if attr in row:
                setattr(p, attr, row[attr])
        if p.created_at is not None:
            p.created_at = p.created_at.replace(tzinfo=datetime.timezone.utc)
        return p
```

- [ ] **Step 2: `crop_media_id` on `CanvasRegionData`** — add `crop_media_id: Optional[int] = None` beside `role`, with a comment saying it is the region's extracted crop, written by the web layer.

- [ ] **Step 3: the region DAO must persist it.** In `MySQLCanvasRegionDb` (`mysqldb.py` ~1137), add `crop_media_id` to `create`'s parameters, INSERT column list and returned object, and to `update`'s SET list. **This is the step most likely to be done wrong:** `update`'s existing signature is positional (`shape, label, note, puzzle_id, role`) and `canvas.py` calls it with keywords — add `crop_media_id=None` as a keyword-only-safe trailing parameter and do NOT reorder the existing ones.

- [ ] **Step 4: `MySQLPuzzleImageDb`**, placed after `MySQLCanvasRegionDb`:

```python
class MySQLPuzzleImageDb(_MySQLBaseDb):
    TABLE_NAME = 'puzzle_images'

    def create(self, puzzle_id: int, media_id: int, source_region_id=None,
               position: int = 0) -> 'PuzzleImageData':
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO puzzle_images"
                " (puzzle_id, media_id, source_region_id, position, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (puzzle_id, media_id, source_region_id, position, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        self.mydb.commit()
        p = PuzzleImageData()
        p.id = new_id or 0
        p.puzzle_id, p.media_id = puzzle_id, media_id
        p.source_region_id, p.position = source_region_id, position
        p.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return p

    def get_by_puzzle(self, puzzle_id: int) -> list:
        """The puzzle's images in display order. `position` first, then id, so
        rows created before anyone reordered still have a stable order."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "SELECT * FROM puzzle_images WHERE puzzle_id = %s"
                " ORDER BY position, id", (puzzle_id,))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [PuzzleImageData.import_dict(r) for r in rows]

    def get_by_id(self, image_id: int):
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("SELECT * FROM puzzle_images WHERE id = %s", (image_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return PuzzleImageData.import_dict(row) if row else None

    def get_by_region(self, region_id: int):
        """At most one row per region — enforced by the unique index."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "SELECT * FROM puzzle_images WHERE source_region_id = %s", (region_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return PuzzleImageData.import_dict(row) if row else None

    def set_positions(self, ordered_ids) -> None:
        """Write a whole ordering at once. Whole-list rather than per-item so a
        half-applied reorder cannot leave the list in an order nobody chose."""
        cursor = self.mydb.dict_cursor()
        try:
            for index, image_id in enumerate(ordered_ids):
                cursor.execute(
                    "UPDATE puzzle_images SET position = %s WHERE id = %s",
                    (index, image_id))
        finally:
            cursor.close()
        self.mydb.commit()

    def delete(self, image_id: int) -> None:
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("DELETE FROM puzzle_images WHERE id = %s", (image_id,))
        finally:
            cursor.close()
        self.mydb.commit()

    def delete_by_region(self, region_id: int) -> None:
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "DELETE FROM puzzle_images WHERE source_region_id = %s", (region_id,))
        finally:
            cursor.close()
        self.mydb.commit()
```

- [ ] **Step 5:** bind `PuzzleImageJsonDb` in `bot/store/__init__.py` everywhere its siblings are bound; verify with `pipenv run python -c "from bot.store import PuzzleImageJsonDb; print(PuzzleImageJsonDb)"`.
- [ ] **Step 6:** full suite unchanged at 1472, ruff + basedpyright clean, commit.

---

### Task 3: The `puzzle_images` service

**Files:** create `bot/core/puzzle_images.py`; test `tests/test_puzzle_images_service.py`

TDD. The service is lock-free (callers lock), like every `bot/core` module.

Required behaviour, each with a test:

1. `list_images(puzzle_id)` → DAO order, untouched.
2. `attach_upload(puzzle_id, media_id)` → appends at `position = len(existing)`, `source_region_id=None`. **Test with two attaches** and assert the second lands after the first — one attach cannot prove ordering.
3. `reorder(puzzle_id, ordered_ids)` → calls `set_positions` with exactly those ids. Rejects (`ImageNotFound`) if any id does not belong to that puzzle — the ids come from the client. **Test with three images** asserted as a sequence; two cannot tell a reorder from a reversal.
4. `detach(image_id, puzzle_id)` → deletes, refusing an id belonging to another puzzle.
5. `sync_region_image(region_id, puzzle_id, role, media_id)` — the one the canvas calls:
   - `role == "locates"` and a crop exists → create the row if none, else leave it alone (idempotent, and it must NOT lose its position).
   - any other role, or no puzzle → `delete_by_region(region_id)`.
   - Returns nothing; never raises on a missing crop (a crop failure must not fail the link).

Mutation-prove: the cross-puzzle guard in `reorder`, and the idempotency of `sync_region_image` (make it always create → the "unchanged link keeps its position" test must go red).

Commit: `feat(puzzle-images): add the puzzle images service`.

---

### Task 4: The region → puzzle_images sync in `canvas.py`

**Files:** modify `bot/core/canvas.py`; test `tests/test_canvas_service.py`

`canvas.py` currently owns the region↔evidence coupling (`_log_informs_evidence`, and `update_region`'s drop-and-recreate on link change). This adds the parallel coupling for locates, in the same module and by the same rules.

- `create_region(...)`: after creating the row, call `puzzle_images.sync_region_image(...)`. Store the crop on the region as `crop_media_id=media_id` for **both** roles — the column records the region's crop, not the evidence's.
- `update_region(...)`: the existing `link_changed` branch already handles evidence; the sync call goes alongside it and must run on a link change **and not otherwise**.
- An **unchanged** link must not touch the row — same rule the evidence path already follows, and here the cost of getting it wrong is the image losing its place in the user's ordering.

Tests (extend `tests/test_canvas_service.py`, matching its `dbs` fixture style, adding a patched `puzzle_images`):

- a locates link creates the image row;
- an informs link does **not**;
- relinking locates → informs removes it;
- clearing the link removes it;
- a note/label edit with an unchanged link does nothing (mutation-prove this one).

Commit: `feat(puzzle-images): sync a locates region with its puzzle image`.

---

### Task 5: Crops for `locates` regions, and the regenerate guard

**Files:** modify `bot/web/actions.py`; test `tests/test_canvas_web.py`

- `create_region` / `update_region` / `create_puzzle_from_region` currently crop only when the link is becoming **informs** (`_crop_region_media`). Extend the guard to `locates` as well — the crop is what a puzzle image points at.
- **The regenerate guard.** Before cropping in `update_region`, if the existing region already has a `crop_media_id` **and that media has markup rows**, reuse the existing crop instead of making a new one. Ink is drawn on a specific image; regenerating it silently would slide every mark off what it pointed at. Use `bot/core/markup.list_marks(crop_media_id)` to test for ink.
  - Nothing in the UI can trigger a shape change today (`CanvasView` has no resize handles), but `PATCH .../regions/<id>` accepts a `shape`, so this is a latent path being closed pre-emptively.
- Tests: a locates link crops; a locates link with an existing inked crop does **not** re-crop; an informs link still crops as before. Mutation-prove the guard.

Commit: `feat(puzzle-images): crop locates regions, and never re-crop under ink`.

---

### Task 6: Routes

**Files:** modify `bot/web/actions.py`, `bot/web/data.py`; test `tests/test_puzzle_images_web.py`

`puzzle_image_to_json(image, media_url)` in `data.py`: `{id, media_id, media_url, source_region_id, position}`.

Routes, all under the existing puzzle permission bar (any member who can see an unarchived hunt — find the helper the neighbouring puzzle routes use and reuse it, do not invent one):

- `GET /api/puzzles/<id>/images`
- `POST /api/puzzles/<id>/images` — `{"media_id": N}`, appended. **The media must belong to the same hunt as the puzzle** — the id is client-supplied and this is the one way past media visibility. Same guard the evidence image-replace route carries; copy it.
- `PATCH /api/puzzles/<id>/images` — `{"ordered_ids": [...]}`; rejects ids not on this puzzle.
- `DELETE /api/puzzles/<id>/images/<imageId>` — detaches. Deleting a region-sourced row does **not** delete the region; it is a "stop showing this here" action.

Each mutation calls `_push_board(...)`. Tests: the happy path for each, the cross-hunt media guard, the foreign-id reorder rejection, and the archived-hunt refusal. Mutation-prove the cross-hunt guard and the archived bar.

Commit: `feat(puzzle-images): add the puzzle image routes`.

---

### Task 7: Frontend types and hooks

**Files:** modify `frontend/src/types.ts`, `frontend/src/api.ts`; add `@dnd-kit/sortable`

```ts
export interface PuzzleImage {
  id: number;
  media_id: number;
  media_url: string;
  source_region_id: number | null;
  position: number;
}
```

Hooks mirroring the markup ones, keyed `["puzzle-images", puzzleId]`: `usePuzzleImages(puzzleId | null)` (`enabled` when non-null), `useAttachPuzzleImage`, `useReorderPuzzleImages`, `useDetachPuzzleImage`. Each mutation invalidates that key.

`npm i @dnd-kit/sortable` — needed for the strip's drag reorder; `@dnd-kit/core` alone has no sortable list primitive. Commit the lockfile change with it.

Commit: `feat(puzzle-images): add frontend types and hooks`.

---

### Task 8: The `PuzzleImages` strip

**Files:** create `frontend/src/components/PuzzleImages.tsx`; modify `frontend/src/components/PuzzleDrawer.tsx`

A horizontal strip of thumbnails in the drawer, fetched by `usePuzzleImages(puzzle.id)` (its own query — deliberately not folded into the board payload, which is pushed over the WS to every client for every puzzle).

- **Click a thumbnail → `MarkupModal`** for that image (`mediaId`, `alt` = the puzzle's name). Reused unchanged from the markup plan.
- **Add an image** — `FileButton` + the existing media upload path, then `useAttachPuzzleImage`. Copy `EvidencePanel`'s upload call exactly rather than reimplementing it.
- **Drag to reorder** — `DndContext` + `SortableContext` (`horizontalListSortingStrategy`); on drop, `PATCH` the whole ordering.
- **Remove** — with a confirm that says a region-sourced image will come back if the region is still linked (because it will: the sync recreates it on the next link change). Use the existing `ConfirmModal`.
- A region-sourced thumbnail is marked as such (a small badge); a directly uploaded one is not.
- Gate mutations on `editable` (`!detail.hunt.archived`), matching the rest of the drawer.

Mount it in `PuzzleDrawer` beside the existing metadata rows. Keep the addition narrow — that file is already large.

Commit: `feat(puzzle-images): show a puzzle's images in the drawer`.

---

### Task 9: Verification

- [ ] Full suite, ruff, basedpyright, `tsc --noEmit`, `npm run build`.
- [ ] Browser pass (the only thing that verifies the strip):
  1. Draw a region on a canvas, link it as **locates** → the crop appears in that puzzle's drawer.
  2. Open it → markup works; ink persists.
  3. Upload an image directly → appears at the end.
  4. Drag to reorder → **reload** and confirm the order stuck (a reorder that only lives in local state looks identical until you reload).
  5. Relink the region to another puzzle → the image moves; the direct upload stays put.
  6. Change the region's role to informs → the image goes, and an evidence row appears instead.
  7. Remove a directly uploaded image → gone and stays gone.
- [ ] Record results in `.ai/active/session-summary.md`.
