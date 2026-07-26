# Canvas B2a — Data Model, Service and API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hunt can hold canvases; a canvas holds rectangular regions in normalised
coordinates; a region can link to a puzzle as `locates` ("this part of the picture *is* that
puzzle") or `informs` ("this part is a *clue for* that puzzle"), and an `informs` link
produces a real `evidence_items` row so routing, the evidence panel and the contribution table
work with no special cases.

**Architecture:** `bot/core/canvas_shape.py` is pure geometry validation — no DB, no Quart.
`bot/core/canvas.py` owns region CRUD and the one thing that can drift: the coupling between a
region's link role and its evidence row. It is a new module rather than more of
`puzzle_service.py`, which is already ~2,900 lines and is called out as a contention magnet.
Routes are thin.

**Tech Stack:** Python 3.12, Quart, MySQL, pytest.

**Authoritative spec:** `docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md`.
This is **B2a**. B2b (the tiled viewer, `RegionModal`, draw-to-create UI, progress tinting,
`!canvas`) is a separate plan and depends on this one. B1 (tiled media) is done.

**Scope honesty:** B2a ships no UI either. It is verified by tests and by curling the API.
The first thing anyone can *look at* arrives in B2b.

---

## What already exists (do not rebuild)

- `evidence_items.source_puzzle_id` is **already nullable**, and `source_sheet_id` and
  `media_id` already exist (migrations 030, 031).
- `puzzle_service.evidence_source_label(item, sheet_names)`
  (`bot/core/puzzle_service.py:120`) is the rendered-table source-name seam, and its docstring
  already says "Plan B fills this in for canvas regions".
- `puzzle_service.log_clues(hunt, puzzle, notes, media_id=None, source_sheet_id=None)`
  (`:2862`) already accepts `puzzle=None` and already skips informs-edge creation when there
  is no source puzzle.
- `media` rows, the tile pyramid, `GET /api/media/<id>` and the tile route (B1).
- `notifier.board_changed(hunt)` for live push.

## File structure

**Create:**
- `migrations/033_canvases.sql`
- `bot/store/canvas_data.py` — `HuntCanvasData`, `CanvasRegionData`
- `bot/core/canvas_shape.py` — pure shape parse/validate
- `bot/core/canvas.py` — region CRUD + role/evidence coupling
- `tests/test_canvas_shape.py`
- `tests/test_canvas_service.py`
- `tests/test_canvas_web.py`

**Modify:**
- `bot/store/mysqldb.py` — `MySQLHuntCanvasDb`, `MySQLCanvasRegionDb`; extend
  `MySQLEvidenceDb.log` with `source_region_id`
- `bot/store/__init__.py` — export and instantiate both DAOs
- `bot/store/evidence_data.py` — `source_region_id`
- `bot/core/puzzle_service.py:120` — `evidence_source_label` gains the region branch
- `bot/web/api.py` — canvas and region routes

---

## Task 1: Migration 033

**Files:**
- Create: `migrations/033_canvases.sql`

- [ ] **Step 1: Confirm 033 is free**

Run: `ls migrations/ | tail -3 && for w in .claude/worktrees/*/; do ls "$w/migrations/" 2>/dev/null | tail -1; done`
Expected: `032_media_tiles.sql` is the highest anywhere.

- [ ] **Step 2: Write the migration**

```sql
-- 033_canvases.sql
-- Plan B2a. A hunt can hold big images that get marked up: rectangles drawn on
-- the picture, each optionally linked to a puzzle.
--
-- Separate tables rather than more columns on puzzles: a region is not a puzzle,
-- and an informs-region spawns a real evidence_items row so that routing, the
-- evidence panel and the contribution table all work unchanged.

CREATE TABLE IF NOT EXISTS `hunt_canvases` (
  `id`         INT(11)      NOT NULL AUTO_INCREMENT,
  `hunt_id`    INT(11)      NOT NULL,
  `media_id`   INT(11)      NOT NULL,
  `name`       VARCHAR(255) NOT NULL DEFAULT '',
  `note`       TEXT         DEFAULT NULL,
  `created_at` DATETIME     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_hunt_canvases_hunt` (`hunt_id`),
  CONSTRAINT `HuntCanvasHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  -- The image is the canvas: without it there is nothing to mark up, so this
  -- cascades rather than SET NULLing into a canvas that cannot render.
  CONSTRAINT `HuntCanvasMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `canvas_regions` (
  `id`         INT(11)      NOT NULL AUTO_INCREMENT,
  `canvas_id`  INT(11)      NOT NULL,
  -- JSON: {"type":"rect","points":[[x1,y1],[x2,y2]]}, every value 0-1.
  -- A blob, not four columns, so other shapes are a later UI change rather than
  -- a migration. Coordinates are normalised so they survive re-tiling and any
  -- change of image resolution.
  `shape`      TEXT         NOT NULL,
  `label`      VARCHAR(255) NOT NULL DEFAULT '',
  `note`       TEXT         DEFAULT NULL,
  `puzzle_id`  INT(11)      DEFAULT NULL,
  -- 'locates' (this part of the image IS that puzzle) or 'informs' (this part is
  -- a clue FOR that puzzle). NULL exactly when puzzle_id is NULL.
  `role`       VARCHAR(16)  DEFAULT NULL,
  `created_at` DATETIME     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_canvas_regions_canvas` (`canvas_id`),
  KEY `idx_canvas_regions_puzzle` (`puzzle_id`),
  CONSTRAINT `CanvasRegionCanvasID` FOREIGN KEY (`canvas_id`)
      REFERENCES `hunt_canvases` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  -- Deleting a puzzle unlinks the region rather than destroying the annotation:
  -- the rectangle and its transcription are work someone did on the picture and
  -- outlive the puzzle record.
  CONSTRAINT `CanvasRegionPuzzleID` FOREIGN KEY (`puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- A region can be an evidence item's provenance. CASCADE, not SET NULL: unlike a
-- sheet (031) or a media row (030), an informs-region's evidence row is created
-- BY the link and is meaningless without it -- canvas.py owns both sides so they
-- cannot drift apart, and the FK enforces the same thing if anything else ever
-- deletes a region.
ALTER TABLE `evidence_items`
  ADD COLUMN `source_region_id` INT(11) DEFAULT NULL,
  ADD KEY `idx_evidence_source_region` (`source_region_id`),
  ADD CONSTRAINT `EvidenceSourceRegionID` FOREIGN KEY (`source_region_id`)
      REFERENCES `canvas_regions` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;
```

- [ ] **Step 3: Apply and verify**

Run: `timeout 120 pipenv run python migrate.py > /tmp/migrate.log 2>&1`
Expected: exit 0, names `033_canvases.sql`.

Run:
```bash
pipenv run python -c "
from bot.store import HuntJsonDb
c = HuntJsonDb.mydb.dict_cursor()
for t in ('hunt_canvases', 'canvas_regions'):
    c.execute('SHOW COLUMNS FROM ' + t); print(t, [r['Field'] for r in c.fetchall()])
c.execute('SHOW COLUMNS FROM evidence_items'); print('source_region_id' in [r['Field'] for r in c.fetchall()])
"
```
Expected: both tables listed, and `True`.

- [ ] **Step 4: Commit**

```bash
git add migrations/033_canvases.sql
git commit -m "migrate: hunt canvases and canvas regions"
```

---

## Task 2: Pure shape validation

Coordinates arrive from a browser drag and are the one input that can be malformed in ways
that matter later (a region outside the image, a zero-area region that cannot be clicked, a
drag that went up-and-left so x1 > x2).

**Files:**
- Create: `bot/core/canvas_shape.py`
- Test: `tests/test_canvas_shape.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Pure geometry validation for canvas regions. No DB, no Quart."""
import json

import pytest

from bot.core.canvas_shape import ShapeRejected, parse_shape


def test_a_plain_rect_round_trips():
    out = parse_shape({"type": "rect", "points": [[0.1, 0.2], [0.3, 0.4]]})
    assert json.loads(out) == {"type": "rect", "points": [[0.1, 0.2], [0.3, 0.4]]}


def test_a_drag_up_and_left_is_normalised():
    """A drag from bottom-right to top-left is the same rectangle. Storing it
    unnormalised would give negative width everywhere downstream."""
    out = json.loads(parse_shape({"type": "rect", "points": [[0.8, 0.9], [0.2, 0.3]]}))
    assert out["points"] == [[0.2, 0.3], [0.8, 0.9]]


def test_full_extent_is_allowed():
    out = json.loads(parse_shape({"type": "rect", "points": [[0.0, 0.0], [1.0, 1.0]]}))
    assert out["points"] == [[0.0, 0.0], [1.0, 1.0]]


@pytest.mark.parametrize("pts", [
    [[-0.01, 0.2], [0.3, 0.4]],
    [[0.1, 0.2], [1.01, 0.4]],
    [[0.1, -5.0], [0.3, 0.4]],
])
def test_coordinates_outside_the_image_are_rejected(pts):
    with pytest.raises(ShapeRejected):
        parse_shape({"type": "rect", "points": pts})


@pytest.mark.parametrize("pts", [
    [[0.5, 0.2], [0.5, 0.4]],       # zero width
    [[0.1, 0.5], [0.3, 0.5]],       # zero height
])
def test_a_zero_area_region_is_rejected(pts):
    """Un-clickable, and an accident of a stray click rather than a drag."""
    with pytest.raises(ShapeRejected):
        parse_shape({"type": "rect", "points": pts})


@pytest.mark.parametrize("payload", [
    {"type": "circle", "points": [[0.1, 0.2], [0.3, 0.4]]},
    {"type": "rect", "points": [[0.1, 0.2]]},
    {"type": "rect", "points": [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]},
    {"type": "rect", "points": "nope"},
    {"type": "rect"},
    {"points": [[0.1, 0.2], [0.3, 0.4]]},
    "not a dict",
    None,
])
def test_malformed_payloads_are_rejected(payload):
    with pytest.raises(ShapeRejected):
        parse_shape(payload)


def test_non_numeric_coordinates_are_rejected():
    with pytest.raises(ShapeRejected):
        parse_shape({"type": "rect", "points": [["a", 0.2], [0.3, 0.4]]})


def test_booleans_are_not_accepted_as_coordinates():
    """bool is an int subclass, so a naive isinstance check lets True through as
    1.0 and silently produces a full-width region."""
    with pytest.raises(ShapeRejected):
        parse_shape({"type": "rect", "points": [[True, 0.2], [0.3, 0.4]]})
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_canvas_shape.py -q > /tmp/test.log 2>&1`
Expected: `ModuleNotFoundError: No module named 'bot.core.canvas_shape'`.

- [ ] **Step 3: Write it**

```python
"""Validation for a canvas region's geometry.

Pure: no DB, no Quart, no image. Coordinates are normalised 0-1 rather than
pixels, which is what lets a region survive re-tiling, a change of image
resolution, and the whole tile-pyramid viewer without any arithmetic.
"""
import json

# Rectangles only for now. The stored value is a shape blob rather than four
# columns so that adding polygons later is a UI change, not a migration.
RECT = "rect"


class ShapeRejected(Exception):
    """A region's geometry was malformed. The message is safe to show a user."""


def _coord(value):
    """One normalised coordinate as a float in 0-1.

    bool is excluded explicitly: it is a subclass of int, so a plain numeric
    check would accept True and turn it into 1.0 — a full-extent region from a
    payload that clearly meant nothing of the kind.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShapeRejected("Coordinates must be numbers.")
    out = float(value)
    if not (0.0 <= out <= 1.0):
        raise ShapeRejected("Coordinates must be between 0 and 1.")
    return out


def parse_shape(payload) -> str:
    """Validate a shape payload and return it as JSON ready to store.

    Rectangles are normalised so points[0] is the top-left corner: a drag from
    bottom-right to top-left describes the same rectangle, and storing it as
    given would mean negative width for every consumer downstream.
    """
    if not isinstance(payload, dict):
        raise ShapeRejected("A shape must be an object.")
    if payload.get("type") != RECT:
        raise ShapeRejected("Only rectangles are supported.")
    points = payload.get("points")
    if not isinstance(points, list) or len(points) != 2:
        raise ShapeRejected("A rectangle needs exactly two points.")
    parsed = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ShapeRejected("Each point needs an x and a y.")
        parsed.append((_coord(point[0]), _coord(point[1])))
    (x1, y1), (x2, y2) = parsed
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)
    if x1 == x2 or y1 == y2:
        raise ShapeRejected("A region needs a width and a height.")
    return json.dumps({"type": RECT, "points": [[x1, y1], [x2, y2]]})
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_canvas_shape.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Prove the normalisation test is load-bearing**

Temporarily remove the two `min`/`max` lines. Confirm
`test_a_drag_up_and_left_is_normalised` fails, then edit them back. Do not commit the
mutation.

- [ ] **Step 6: Commit**

```bash
git add bot/core/canvas_shape.py tests/test_canvas_shape.py
git commit -m "feat(core): validate canvas region geometry"
```

---

## Task 3: Dataclasses and DAOs

**Files:**
- Create: `bot/store/canvas_data.py`
- Modify: `bot/store/mysqldb.py`, `bot/store/__init__.py`, `bot/store/evidence_data.py`

- [ ] **Step 1: Write the dataclasses**

`bot/store/canvas_data.py`:

```python
from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class HuntCanvasData:
    id: int = 0
    hunt_id: int = 0
    media_id: int = 0
    name: str = ""
    note: Optional[str] = None
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        c = cls()
        for attr in c.__dict__:
            if attr in row:
                setattr(c, attr, row[attr])
        # Naive UTC in the column, aware everywhere else (see MediaData).
        if c.created_at is not None:
            c.created_at = c.created_at.replace(tzinfo=datetime.timezone.utc)
        return c


@dataclass
class CanvasRegionData:
    id: int = 0
    canvas_id: int = 0
    shape: str = ""
    label: str = ""
    note: Optional[str] = None
    puzzle_id: Optional[int] = None
    # 'locates' or 'informs'; NULL exactly when puzzle_id is NULL.
    role: Optional[str] = None
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        r = cls()
        for attr in r.__dict__:
            if attr in row:
                setattr(r, attr, row[attr])
        if r.created_at is not None:
            r.created_at = r.created_at.replace(tzinfo=datetime.timezone.utc)
        return r
```

- [ ] **Step 2: Add `source_region_id` to `EvidenceData`**

In `bot/store/evidence_data.py`, beside `source_sheet_id`:

```python
    # A canvas region as provenance. At most one of source_puzzle_id /
    # source_sheet_id / source_region_id is set; the invariant is owned by the
    # service methods that write each kind, not by a check constraint.
    source_region_id: Optional[int] = None
```

Update the neighbouring comment so it names all three source columns rather than two.

- [ ] **Step 3: Add the DAOs**

In `bot/store/mysqldb.py`, after `MySQLMediaDb`:

```python
class MySQLHuntCanvasDb(_MySQLBaseDb):
    TABLE_NAME = 'hunt_canvases'

    def create(self, hunt_id: int, media_id: int, name: str,
               note=None) -> 'HuntCanvasData':
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO hunt_canvases (hunt_id, media_id, name, note, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (hunt_id, media_id, name, note, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        self.mydb.commit()
        c = HuntCanvasData()
        c.id = new_id or 0
        c.hunt_id, c.media_id, c.name, c.note = hunt_id, media_id, name, note
        c.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return c

    def get_by_id(self, canvas_id: int):
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("SELECT * FROM hunt_canvases WHERE id = %s", (canvas_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return HuntCanvasData.import_dict(row) if row else None

    def get_by_hunt(self, hunt_id: int) -> list:
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "SELECT * FROM hunt_canvases WHERE hunt_id = %s"
                " ORDER BY created_at, id", (hunt_id,))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [HuntCanvasData.import_dict(r) for r in rows]

    def update(self, canvas_id: int, name: str, note) -> None:
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "UPDATE hunt_canvases SET name = %s, note = %s WHERE id = %s",
                (name, note, canvas_id))
        finally:
            cursor.close()
        self.mydb.commit()

    def delete(self, canvas_id: int) -> None:
        """Regions and their evidence rows go with it, by FK cascade."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("DELETE FROM hunt_canvases WHERE id = %s", (canvas_id,))
        finally:
            cursor.close()
        self.mydb.commit()


class MySQLCanvasRegionDb(_MySQLBaseDb):
    TABLE_NAME = 'canvas_regions'

    def create(self, canvas_id: int, shape: str, label: str = "", note=None,
               puzzle_id=None, role=None) -> 'CanvasRegionData':
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO canvas_regions"
                " (canvas_id, shape, label, note, puzzle_id, role, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (canvas_id, shape, label, note, puzzle_id, role, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        self.mydb.commit()
        r = CanvasRegionData()
        r.id = new_id or 0
        r.canvas_id, r.shape, r.label, r.note = canvas_id, shape, label, note
        r.puzzle_id, r.role = puzzle_id, role
        r.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return r

    def get_by_id(self, region_id: int):
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("SELECT * FROM canvas_regions WHERE id = %s", (region_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return CanvasRegionData.import_dict(row) if row else None

    def get_by_canvas(self, canvas_id: int) -> list:
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "SELECT * FROM canvas_regions WHERE canvas_id = %s"
                " ORDER BY created_at, id", (canvas_id,))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [CanvasRegionData.import_dict(r) for r in rows]

    def update(self, region_id: int, shape: str, label: str, note,
               puzzle_id, role) -> None:
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "UPDATE canvas_regions SET shape = %s, label = %s, note = %s,"
                " puzzle_id = %s, role = %s WHERE id = %s",
                (shape, label, note, puzzle_id, role, region_id))
        finally:
            cursor.close()
        self.mydb.commit()

    def delete(self, region_id: int) -> None:
        """Any evidence row sourced from this region goes with it, by FK cascade."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("DELETE FROM canvas_regions WHERE id = %s", (region_id,))
        finally:
            cursor.close()
        self.mydb.commit()

    def get_names_by_ids(self, region_ids) -> dict:
        """{region_id: label-or-canvas-name} for rendered Source columns, in one
        query. A region with no label falls back to its canvas's name, which is
        why this joins rather than reading canvas_regions alone."""
        ids = [int(i) for i in region_ids if i]
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "SELECT r.id AS id,"
                " CASE WHEN r.label <> '' THEN r.label ELSE c.name END AS name"
                " FROM canvas_regions r"
                " JOIN hunt_canvases c ON c.id = r.canvas_id"
                f" WHERE r.id IN ({placeholders})", tuple(ids))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return {r["id"]: r["name"] for r in rows}
```

Add `from .canvas_data import CanvasRegionData, HuntCanvasData` to the imports at the top of
`mysqldb.py`, beside the existing `from .media_data import MediaData`.

- [ ] **Step 4: Widen `MySQLEvidenceDb.log`**

Find `MySQLEvidenceDb.log` and add a `source_region_id=None` keyword, threading it into the
INSERT's column list and parameter tuple exactly as `source_sheet_id` is threaded today. Do
not reorder existing columns.

- [ ] **Step 5: Export the DAOs**

In `bot/store/__init__.py`, mirroring the existing `MediaJsonDb` lines:

```python
from .canvas_data import CanvasRegionData, HuntCanvasData          # noqa: F401
```
add `MySQLHuntCanvasDb, MySQLCanvasRegionDb` to the `from .mysqldb import ...` list, and
beside `MediaJsonDb = MySQLMediaDb(mydb=mydb)`:
```python
    CanvasJsonDb = MySQLHuntCanvasDb(mydb=mydb)
    CanvasRegionJsonDb = MySQLCanvasRegionDb(mydb=mydb)
```

- [ ] **Step 6: Verify against the real schema**

Run:
```bash
pipenv run python -c "
from bot.store import CanvasJsonDb, CanvasRegionJsonDb
print(CanvasJsonDb.get_by_hunt(1))
print(CanvasRegionJsonDb.get_names_by_ids([1, 2]))
"
```
Expected: `[]` and `{}` — proves the SQL parses against the live tables.

Run: `timeout 300 pipenv run python -m pytest tests/ -q` — expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add bot/store/ tests/
git commit -m "feat(store): canvases, regions, and region-sourced evidence"
```

---

## Task 4: The canvas service

The one thing here that can drift is the coupling between a region's link role and its
evidence row. A single module owns both sides.

**Files:**
- Create: `bot/core/canvas.py`
- Modify: `bot/core/puzzle_service.py:120` (`evidence_source_label`)
- Test: `tests/test_canvas_service.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Region CRUD and the region-role <-> evidence-row coupling.

DAOs are patched at the service's import site per docs/agents/python.md.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from bot.core import canvas
from bot.core.canvas_shape import ShapeRejected
from bot.store.canvas_data import CanvasRegionData

RECT = {"type": "rect", "points": [[0.1, 0.2], [0.3, 0.4]]}


def _region(region_id=5, puzzle_id=None, role=None, canvas_id=2):
    r = CanvasRegionData()
    r.id, r.canvas_id = region_id, canvas_id
    r.shape = json.dumps(RECT)
    r.puzzle_id, r.role = puzzle_id, role
    return r


@pytest.fixture
def dbs():
    regions, evidence = MagicMock(), MagicMock()
    with patch.object(canvas, "CanvasRegionJsonDb", regions), \
         patch.object(canvas, "EvidenceJsonDb", evidence):
        yield regions, evidence


def test_creating_an_unlinked_region_stores_normalised_geometry(dbs):
    regions, evidence = dbs
    regions.create.return_value = _region()
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT)
    stored = json.loads(regions.create.call_args.kwargs["shape"])
    assert stored == RECT
    assert regions.create.call_args.kwargs["role"] is None
    # No puzzle, so nothing to log.
    evidence.log.assert_not_called()


def test_a_locates_region_creates_no_evidence(dbs):
    """locates means the region IS the puzzle. There is no fact to record."""
    regions, evidence = dbs
    regions.create.return_value = _region(puzzle_id=9, role="locates")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="locates")
    evidence.log.assert_not_called()


def test_an_informs_region_creates_a_real_evidence_row(dbs):
    """So routing, the evidence panel and the contribution table need no special
    cases: an informs-region IS a piece of evidence about its target."""
    regions, evidence = dbs
    regions.create.return_value = _region(region_id=5, puzzle_id=9, role="informs")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT, note="TRANSCRIPTION",
                         puzzle_id=9, role="informs")
    evidence.log.assert_called_once()
    kwargs = evidence.log.call_args.kwargs
    assert kwargs["source_region_id"] == 5
    assert kwargs["target_puzzle_id"] == 9
    assert kwargs["source_puzzle_id"] is None


def test_an_invalid_role_is_rejected(dbs):
    with pytest.raises(canvas.RegionRejected):
        canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                             puzzle_id=9, role="banana")


def test_a_role_without_a_puzzle_is_rejected(dbs):
    """role is NULL exactly when puzzle_id is NULL."""
    with pytest.raises(canvas.RegionRejected):
        canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT, role="informs")


def test_a_puzzle_without_a_role_is_rejected(dbs):
    with pytest.raises(canvas.RegionRejected):
        canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT, puzzle_id=9)


def test_bad_geometry_propagates(dbs):
    with pytest.raises(ShapeRejected):
        canvas.create_region(canvas_id=2, hunt_id=1,
                             shape={"type": "rect", "points": [[0.5, 0.1], [0.5, 0.9]]})


def test_changing_informs_to_locates_deletes_the_evidence_row(dbs):
    """The evidence row exists BECAUSE of the informs link. Leaving it behind
    would strand a fact about a puzzle the region no longer informs."""
    regions, evidence = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="locates")
    evidence.delete_by_region.assert_called_once_with(5)
    evidence.log.assert_not_called()


def test_relinking_an_informs_region_replaces_its_evidence_row(dbs):
    regions, evidence = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=11, role="informs")
    evidence.delete_by_region.assert_called_once_with(5)
    assert evidence.log.call_args.kwargs["target_puzzle_id"] == 11


def test_clearing_the_link_deletes_the_evidence_row(dbs):
    regions, evidence = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=None, role=None)
    evidence.delete_by_region.assert_called_once_with(5)
    evidence.log.assert_not_called()


def test_locates_to_informs_creates_the_evidence_row(dbs):
    regions, evidence = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="locates")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="informs")
    assert evidence.log.call_args.kwargs["source_region_id"] == 5


def test_updating_a_missing_region_is_rejected(dbs):
    regions, _ = dbs
    regions.get_by_id.return_value = None
    with pytest.raises(canvas.RegionRejected):
        canvas.update_region(region_id=5, hunt_id=1, shape=RECT)
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_canvas_service.py -q > /tmp/test.log 2>&1`
Expected: `ModuleNotFoundError: No module named 'bot.core.canvas'`.

- [ ] **Step 3: Add `delete_by_region` to `MySQLEvidenceDb`**

```python
    def delete_by_region(self, region_id: int) -> None:
        """Remove the evidence row(s) an informs-region spawned.

        Called by canvas.py when a link's role changes, its target changes, or it
        is cleared. The FK cascade covers region DELETE; this covers the cases
        where the region survives but its link does not.
        """
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "DELETE FROM evidence_items WHERE source_region_id = %s", (region_id,))
        finally:
            cursor.close()
        self.mydb.commit()
```

- [ ] **Step 4: Write the service**

```python
"""Canvas regions, and the coupling between a region's link and its evidence.

A new module rather than more of puzzle_service.py, which is already ~2,900
lines and is a contention magnet.

Lock-free by the bot/core convention: callers lock. In particular the
create-puzzle-from-region path must not take a lock the puzzle create path will
take again internally.
"""
import logging

from bot.core.canvas_shape import parse_shape
from bot.store import CanvasRegionJsonDb, EvidenceJsonDb

logger = logging.getLogger(__name__)

LOCATES = "locates"
INFORMS = "informs"
ROLES = (LOCATES, INFORMS)


class RegionRejected(Exception):
    """A region's link was malformed. The message is safe to show a user."""


def _check_link(puzzle_id, role):
    """role is set exactly when puzzle_id is — the schema's stated invariant.

    Enforced here rather than by a check constraint: this schema uses none, and
    a half-set link would otherwise render as a linked region that tints by
    nothing, or an unlinked one that claims a role.
    """
    if puzzle_id is None and role is None:
        return
    if puzzle_id is None or role is None:
        raise RegionRejected("A linked region needs both a puzzle and a role.")
    if role not in ROLES:
        raise RegionRejected(f"Role must be one of: {', '.join(ROLES)}.")


def _log_informs_evidence(hunt_id, region_id, puzzle_id, note, media_id=None):
    """An informs-region IS a piece of evidence about its target.

    Creating a real row means routing, the evidence panel and the contribution
    table all work with no special cases. source_puzzle_id stays NULL: the
    provenance is a picture, not a puzzle, and no informs EDGE is created either
    — an edge joins two puzzles and a region is not one.
    """
    return EvidenceJsonDb.log(
        hunt_id, None, note or "",
        media_id=media_id,
        source_region_id=region_id,
        target_puzzle_id=puzzle_id,
    )


def create_region(canvas_id: int, hunt_id: int, shape, label: str = "",
                  note=None, puzzle_id=None, role=None):
    """Create one region, and its evidence row if it informs a puzzle."""
    _check_link(puzzle_id, role)
    stored = parse_shape(shape)
    region = CanvasRegionJsonDb.create(
        canvas_id=canvas_id, shape=stored, label=label, note=note,
        puzzle_id=puzzle_id, role=role)
    if role == INFORMS:
        _log_informs_evidence(hunt_id, region.id, puzzle_id, note)
    return region


def update_region(region_id: int, hunt_id: int, shape, label: str = "",
                  note=None, puzzle_id=None, role=None):
    """Update one region, keeping its evidence row in step with its link.

    Any change to the link — role, target, or clearing it — drops the old
    evidence row first and recreates it only if the new link still informs. One
    function owning both sides is what stops a region and its evidence drifting
    apart.
    """
    _check_link(puzzle_id, role)
    existing = CanvasRegionJsonDb.get_by_id(region_id)
    if existing is None:
        raise RegionRejected("That region no longer exists.")
    stored = parse_shape(shape)
    link_changed = (existing.puzzle_id != puzzle_id or existing.role != role)
    CanvasRegionJsonDb.update(region_id, shape=stored, label=label, note=note,
                              puzzle_id=puzzle_id, role=role)
    if link_changed and existing.role == INFORMS:
        EvidenceJsonDb.delete_by_region(region_id)
    if link_changed and role == INFORMS:
        _log_informs_evidence(hunt_id, region_id, puzzle_id, note)
    return CanvasRegionJsonDb.get_by_id(region_id)


def delete_region(region_id: int) -> None:
    """Delete a region. Its evidence rows go with it by FK cascade."""
    CanvasRegionJsonDb.delete(region_id)
```

- [ ] **Step 5: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_canvas_service.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 6: Fill in the `evidence_source_label` region branch**

In `bot/core/puzzle_service.py`, `evidence_source_label` gains a region case *before* the
sheet case, and a `region_names` parameter. Every call site
(`puzzle_service.py:786`, `:2935`) passes it; resolve it with a
`resolve_region_names` helper mirroring `resolve_sheet_names`:

```python
def resolve_region_names(region_ids):
    """{region_id: label-or-canvas-name}, in one query."""
    ids = {i for i in region_ids if i}
    if not ids:
        return {}
    return CanvasRegionJsonDb.get_names_by_ids(ids)
```

and in `evidence_source_label`, after the docstring:

```python
    region_id = getattr(item, "source_region_id", None)
    if region_id:
        # The region's own label, else its canvas's name — the DAO's join already
        # applies that fallback, so there is one rule, not two.
        return region_names.get(region_id) or f"Region {region_id}"
```

Add a test to the existing `evidence_source_label` test file asserting a region-sourced item
renders its label, and that a labelless region renders its canvas name.

- [ ] **Step 7: Commit**

```bash
git add bot/core/canvas.py bot/core/puzzle_service.py bot/store/mysqldb.py tests/
git commit -m "feat(core): canvas regions, and their evidence rows"
```

---

## Task 5: The API

**Files:**
- Modify: `bot/web/api.py`
- Test: `tests/test_canvas_web.py`

- [ ] **Step 1: Write the routes**

Permissions per the design: **drawing and linking is any authenticated user who can see the
hunt, while the hunt is unarchived** — everyone marks the picture up during a hunt, same bar
as logging evidence. **Creating or deleting a canvas needs `can_manage`.**

```python
def _canvas_json(canvas_row, regions):
    return {
        "id": canvas_row.id,
        "name": canvas_row.name,
        "note": canvas_row.note,
        "media_id": canvas_row.media_id,
        "regions": [_region_json(r) for r in regions],
    }


def _region_json(region):
    import json as _json
    return {
        "id": region.id,
        "shape": _json.loads(region.shape),
        "label": region.label,
        "note": region.note,
        "puzzle_id": region.puzzle_id,
        "role": region.role,
    }
```

Routes:

- `GET  /api/hunts/<uid>/canvases` — list, regions inline, plus each canvas's media
  `width`/`height`/`tile_status`/`tile_levels` so the viewer can size itself before fetching
  a single tile.
- `POST /api/hunts/<uid>/canvases` — `{media_id, name, note}`; `can_manage`. Verify the
  `media_id` belongs to this hunt before creating, or a canvas could point at another hunt's
  image.
- `PATCH  /api/canvases/<id>` — name/note; `can_manage`.
- `DELETE /api/canvases/<id>` — `can_manage`; **also `rmtree` the media's tile directory** and
  delete the media row, since B1 deliberately left tile sweeping to whoever added a delete
  path.
- `POST   /api/canvases/<id>/regions` — any hunt-visible user, unarchived hunt.
- `PATCH  /api/canvases/<id>/regions/<rid>` — same bar.
- `DELETE /api/canvases/<id>/regions/<rid>` — same bar.

Every mutation calls `board_changed` so a second person annotating sees regions appear live,
and `ShapeRejected`/`RegionRejected` map to 400 with the exception's message.

- [ ] **Step 2: Write the tests**

`tests/test_canvas_web.py`, modelled on `tests/test_media_web.py`'s `web` fixture. Cover:

- unauthenticated request to each route family → 401
- a hunt the user cannot see → 403/404
- creating a canvas without `can_manage` → 403
- creating a canvas whose `media_id` belongs to another hunt → 400
- drawing a region as a non-manager → 201 (the deliberate difference from canvas creation)
- drawing on an archived hunt → 403
- malformed geometry → 400 with a message
- a region PATCH that changes role → the service is called, and `board_changed` is awaited
- `DELETE /api/canvases/<id>` removes the tile directory

- [ ] **Step 3: Gates**

Run: `timeout 600 pipenv run python -m pytest tests/ -q`, `pipenv run ruff check`,
`timeout 300 pipenv run basedpyright bot/`.
Expected: suite green, ruff clean, 0 type errors.

- [ ] **Step 4: Commit**

```bash
git add bot/web/api.py tests/test_canvas_web.py
git commit -m "feat(web): canvas and region API"
```

---

## Deliberately not in B2a

- **All UI.** `CanvasPanel`, `CanvasView`, `RegionModal`, the progress tint, the
  `SegmentedControl` entry, and draw-to-create's frontend are B2b.
- **`POST /api/canvases/<id>/regions/<rid>/create-puzzle`.** Draw-to-create needs the round
  picker and the create path's own arguments; it lands with the UI that drives it in B2b.
- **`!canvas`.** B2b.
- **Polygons, freehand, per-round canvas scoping** — out of scope per the design.

## Self-review against the spec

- Three tables, shape blob, normalised 0–1, many canvases per hunt flat — Task 1, Task 2.
- `locates` vs `informs`; informs spawns a real evidence row with `source_puzzle_id` NULL and
  `target_puzzle_id` set; role change/relink/clear deletes it; region and canvas delete
  cascade — Task 4 (service) and Task 1 (FKs).
- No informs *edge* for a region source — Task 4, `_log_informs_evidence` docstring and the
  absence of any `link_informs_for_evidence` call.
- `evidence_source_label` region branch, label else canvas name — Task 4 Step 6.
- Permissions split (draw = any hunt-visible user; canvas create/delete = `can_manage`) —
  Task 5.
- `board_changed` on region mutations — Task 5.

Gap accepted and deliberate: the design lists `create-puzzle` under B2's API, but it is
deferred to B2b because its arguments (round choice, meta type) are decided by UI that does not
exist yet, and building the endpoint first would mean guessing them.
