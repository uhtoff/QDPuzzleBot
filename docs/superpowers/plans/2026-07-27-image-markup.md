# Image Markup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user draw purely-visual markup — freehand strokes, lines, arrows, a highlighter, and text boxes — on any stored image, starting with evidence images.

**Architecture:** Markup rows are keyed on `media_id` and stored in normalised 0–1 coordinates, so one table and one React component serve every image surface. A new pure validator (`bot/core/markup_shape.py`) mirrors `canvas_shape.py`; a thin service (`bot/core/markup.py`) mirrors `canvas.py`; routes mirror the existing region routes, including their permission bar and `_push_board()` WS push. The frontend adds two new components and touches existing ones only to add an entry point.

**Tech Stack:** Python 3.12, Quart, MySQL (`migrations/NNN_*.sql` + `migrate.py`), pytest; React + TypeScript + Mantine + react-query.

**Source spec:** `docs/superpowers/specs/2026-07-27-canvas-markup-design.md`

**Scope note:** This plan covers markup and its evidence-image entry point (spec stages 1, 2 and the `EvidencePanel` half of 5). Puzzle images — `puzzle_images`, `canvas_regions.crop_media_id`, `locates` crops, the `canvas.py` sync, and the `PuzzleDrawer` strip — are a **second plan** written after this one ships. Markup works standalone on evidence images without any of it.

---

## Conventions for every task

- Tests: `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`, then read `/tmp/test.log`. A timeout kill exits 124.
- Lint/type: `pipenv run ruff check` and `pipenv run basedpyright bot/` (must be 0 errors).
- Frontend: `cd frontend && npx tsc --noEmit` and `npm run build`.
- DAOs are patched **at the service's own import site** (`patch.object(markup, "MediaMarkupJsonDb", ...)`), because the service does `from bot.store import ...` at module load.
- Commit after every task.

## File structure

| File | Responsibility |
|---|---|
| `migrations/034_media_markup.sql` (create) | The `media_markup` table |
| `bot/core/markup_shape.py` (create) | Pure validation of a mark's geometry, text and style |
| `bot/store/markup_data.py` (create) | `MediaMarkupData` dataclass |
| `bot/store/mysqldb.py` (modify) | `MySQLMediaMarkupDb` DAO |
| `bot/store/__init__.py` (modify) | Bind `MediaMarkupJsonDb` |
| `bot/core/markup.py` (create) | Service: validate, then read/write through the DAO |
| `bot/web/data.py` (modify) | `mark_to_json`; media dimensions on the evidence payload |
| `bot/web/api.py` (modify) | `GET /api/media/<id>/markup` |
| `bot/web/actions.py` (modify) | `_media_ctx` + POST/PATCH/DELETE markup routes |
| `frontend/src/types.ts` (modify) | `MediaMark`, `MarkStyle`; evidence media dimensions |
| `frontend/src/api.ts` (modify) | `useMarkup`, `useCreateMark`, `useUpdateMark`, `useDeleteMark` |
| `frontend/src/components/MarkupCanvas.tsx` (create) | Image + SVG overlay, tools, zoom/pan |
| `frontend/src/components/MarkupModal.tsx` (create) | Full-screen wrapper |
| `frontend/src/components/EvidencePanel.tsx` (modify) | "Mark up" entry point |

---

### Task 1: The migration

**Files:**
- Create: `migrations/034_media_markup.sql`

Note: `033_canvases.sql` is the current highest. If another worktree has already taken `034`, renumber to the next free number before committing — parallel worktrees have collided on this before.

- [ ] **Step 1: Write the migration**

```sql
-- 034_media_markup.sql
-- Purely visual annotation on a stored image: freehand strokes, lines/arrows
-- and text boxes. Keyed on media rather than on a canvas or an evidence row, so
-- one table serves every image surface, and coordinates are normalised 0-1 like
-- canvas_regions so a mark survives any change of resolution.
--
-- Markup carries NO meaning: it is never linked to a puzzle, never spawns an
-- evidence row, and is never cropped or OCR'd. That is what keeps canvas.py's
-- region/evidence coupling out of this feature entirely.

CREATE TABLE IF NOT EXISTS `media_markup` (
  `id`         INT(11)     NOT NULL AUTO_INCREMENT,
  `media_id`   INT(11)     NOT NULL,
  -- 'pen' | 'line' | 'text'. A highlighter is a pen with a fat, low-opacity
  -- style, and an arrow is a line with a style flag: neither is a kind, because
  -- nothing server-side treats them differently.
  `kind`       VARCHAR(16) NOT NULL,
  -- JSON: {"points":[[x,y],...]} for pen/line, {"points":[[x,y]],"size":0.04}
  -- for text. Written only by markup_shape.parse_mark.
  `shape`      TEXT        NOT NULL,
  -- JSON: {"colour":"#e03131","width":0.004,"opacity":1,"arrow":false}
  `style`      TEXT        NOT NULL,
  `text`       TEXT        DEFAULT NULL,
  `created_at` DATETIME    NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_media_markup_media` (`media_id`),
  -- CASCADE, not SET NULL: ink without its image is meaningless, and media
  -- already cascades from hunts, so a deleted hunt leaves no orphan marks.
  CONSTRAINT `MediaMarkupMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

- [ ] **Step 2: Apply it locally**

Run: `pipenv run python migrate.py migrate`
Expected: `034_media_markup.sql` reported as applied. Then `pipenv run python migrate.py status` lists it as applied.

- [ ] **Step 3: Commit**

```bash
git add migrations/034_media_markup.sql
git commit -m "feat(markup): add the media_markup table"
```

---

### Task 2: `markup_shape.py` — geometry

**Files:**
- Create: `bot/core/markup_shape.py`
- Test: `tests/test_markup_shape.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Pure validation of a mark's geometry, text and style.

Mirrors tests/test_canvas_shape.py: no DB, no Quart, no image.
"""
import json

import pytest

from bot.core.markup_shape import MarkupRejected, parse_mark, parse_style


def test_a_pen_stroke_round_trips_its_points():
    kind, shape, text = parse_mark({"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]})
    assert kind == "pen"
    assert json.loads(shape)["points"] == [[0.1, 0.2], [0.3, 0.4]]
    assert text is None


def test_a_line_needs_exactly_two_points():
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "line", "points": [[0.1, 0.2]]})
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "line", "points": [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]})


def test_a_zero_length_stroke_is_rejected():
    """A stray click must not persist as an invisible mark."""
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "pen", "points": [[0.5, 0.5], [0.5, 0.5]]})


def test_too_many_points_are_rejected_not_truncated():
    """A silently shortened stroke is a WRONG stroke, and an uncapped TEXT blob
    is the actual hazard. Two assertions, so the cap is pinned at its edge:
    exactly MAX_POINTS is fine, one more is refused."""
    from bot.core.markup_shape import MAX_POINTS
    ok = [[i / (MAX_POINTS * 2), 0.5] for i in range(MAX_POINTS)]
    assert json.loads(parse_mark({"kind": "pen", "points": ok})[1])["points"]
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "pen", "points": ok + [[0.9, 0.9]]})


def test_coordinates_must_be_in_range_and_not_bools():
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "pen", "points": [[0.1, 0.2], [1.4, 0.4]]})
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "pen", "points": [[True, 0.2], [0.3, 0.4]]})


def test_a_text_mark_carries_its_string_and_size():
    kind, shape, text = parse_mark(
        {"kind": "text", "points": [[0.2, 0.3]], "text": "  answer  ", "size": 0.05})
    assert kind == "text"
    assert json.loads(shape) == {"points": [[0.2, 0.3]], "size": 0.05}
    assert text == "answer"


def test_empty_text_is_rejected():
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "text", "points": [[0.2, 0.3]], "text": "   ", "size": 0.05})


def test_an_unknown_kind_is_rejected():
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "polygon", "points": [[0.1, 0.2], [0.3, 0.4]]})


def test_a_style_round_trips():
    assert json.loads(parse_style(
        {"colour": "#E03131", "width": 0.004, "opacity": 0.4, "arrow": True})) == {
        "colour": "#e03131", "width": 0.004, "opacity": 0.4, "arrow": True}


def test_a_bad_colour_is_rejected():
    """The colour is echoed into an SVG attribute, so it is validated rather
    than trusted."""
    with pytest.raises(MarkupRejected):
        parse_style({"colour": "red; onload=alert(1)", "width": 0.004})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_markup_shape.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.core.markup_shape'`.

- [ ] **Step 3: Write the implementation**

```python
"""Validation for one mark drawn on an image.

Pure: no DB, no Quart, no image. Coordinates are normalised 0-1, the same
choice canvas_shape makes and for the same reason — a mark survives any change
of resolution, and the SVG overlay can use the stored numbers directly.

Deliberately NOT folded into canvas_shape: that module's rectangle rules are
load-bearing for cropping (rect_bounds feeds crop_bytes), and growing branches
there to accommodate ink would put the crop path one refactor away from a bug.
Duplicating a six-line _coord is the cheaper mistake.
"""
import json
import re

PEN = "pen"
LINE = "line"
TEXT = "text"
KINDS = (PEN, LINE, TEXT)

#: A stroke longer than this is refused outright rather than truncated: a
#: silently shortened stroke is a wrong stroke, and the frontend already thins
#: points to ~0.001 apart, so a legitimate drag lands in the low hundreds.
MAX_POINTS = 2000
MAX_TEXT = 500

_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


class MarkupRejected(Exception):
    """A mark was malformed. The message is safe to show a user."""


def _coord(value):
    """One normalised coordinate as a float in 0-1.

    bool is excluded explicitly: it is a subclass of int, so a plain numeric
    check would accept True and place a mark at the image's far corner.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarkupRejected("Coordinates must be numbers.")
    out = float(value)
    if not (0.0 <= out <= 1.0):
        raise MarkupRejected("Coordinates must be between 0 and 1.")
    return out


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarkupRejected(f"{name} must be a number.")
    out = float(value)
    if not (low <= out <= high):
        raise MarkupRejected(f"{name} is out of range.")
    return out


def _points(raw, least, most):
    if not isinstance(raw, list) or not (least <= len(raw) <= most):
        raise MarkupRejected("That mark has the wrong number of points.")
    out = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise MarkupRejected("Each point needs an x and a y.")
        out.append([_coord(point[0]), _coord(point[1])])
    return out


def parse_mark(payload) -> tuple[str, str, str | None]:
    """Validate a mark and return (kind, shape JSON ready to store, text).

    Text travels back separately because it lives in its own column, where it
    can be searched and edited without rewriting the geometry.
    """
    if not isinstance(payload, dict):
        raise MarkupRejected("A mark must be an object.")
    kind = payload.get("kind")
    if kind not in KINDS:
        raise MarkupRejected(f"Kind must be one of: {', '.join(KINDS)}.")
    if kind == TEXT:
        points = _points(payload.get("points"), 1, 1)
        size = _number(payload.get("size"), "Text size", 0.001, 1.0)
        text = (payload.get("text") or "").strip()[:MAX_TEXT]
        if not text:
            raise MarkupRejected("A text box needs some text.")
        return kind, json.dumps({"points": points, "size": size}), text
    least, most = (2, 2) if kind == LINE else (2, MAX_POINTS)
    points = _points(payload.get("points"), least, most)
    # A stray click produces a run of identical points; it would store as an
    # invisible mark that still has to be found and erased.
    if all(p == points[0] for p in points):
        raise MarkupRejected("That mark has no length.")
    return kind, json.dumps({"points": points}), None


def parse_style(payload) -> str:
    """Validate a mark's style and return it as JSON ready to store.

    The colour is echoed straight into an SVG attribute, so it is matched
    against a strict six-digit hex pattern rather than trusted.
    """
    if not isinstance(payload, dict):
        raise MarkupRejected("A style must be an object.")
    colour = payload.get("colour")
    if not isinstance(colour, str) or not _COLOUR.match(colour):
        raise MarkupRejected("Colour must be a hex value like #e03131.")
    return json.dumps({
        "colour": colour.lower(),
        "width": _number(payload.get("width", 0.004), "Width", 0.0005, 0.2),
        "opacity": _number(payload.get("opacity", 1), "Opacity", 0.05, 1.0),
        "arrow": bool(payload.get("arrow", False)),
    })
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_markup_shape.py -q > /tmp/test.log 2>&1`
Expected: PASS, 10 tests.

- [ ] **Step 5: Prove the point cap is load-bearing by mutation**

Temporarily change `MAX_POINTS = 2000` to `MAX_POINTS = 10_000` and re-run.
Expected: `test_too_many_points_are_rejected_not_truncated` FAILS at the `pytest.raises` line (not at the earlier assert). Restore `2000` by editing the line back — **not** with `git checkout`, which would take the whole file.

- [ ] **Step 6: Lint, typecheck and commit**

```bash
pipenv run ruff check
pipenv run basedpyright bot/
git add bot/core/markup_shape.py tests/test_markup_shape.py
git commit -m "feat(markup): validate a mark's geometry, text and style"
```

---

### Task 3: The dataclass and DAO

**Files:**
- Create: `bot/store/markup_data.py`
- Modify: `bot/store/mysqldb.py` (add a class after `MySQLCanvasRegionDb`)
- Modify: `bot/store/__init__.py:149` area (bind the new DAO alongside `CanvasRegionJsonDb`)

Note: this project's DAO SQL is not unit-tested (no test hits MySQL); the DAOs are exercised through the service and route tests with mocks. That is the existing convention — follow it, and treat this task as inspection-verified.

- [ ] **Step 1: Write the dataclass**

```python
from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class MediaMarkupData:
    id: int = 0
    media_id: int = 0
    # 'pen' | 'line' | 'text'
    kind: str = ""
    # JSON as written by markup_shape.parse_mark — the only thing that builds it.
    shape: str = ""
    # JSON as written by markup_shape.parse_style.
    style: str = ""
    text: Optional[str] = None
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        m = cls()
        for attr in m.__dict__:
            if attr in row:
                setattr(m, attr, row[attr])
        # Naive UTC in the column, aware everywhere else (see MediaData).
        if m.created_at is not None:
            m.created_at = m.created_at.replace(tzinfo=datetime.timezone.utc)
        return m
```

- [ ] **Step 2: Write the DAO**

Add to `bot/store/mysqldb.py`, immediately after `MySQLCanvasRegionDb` ends, and add `MediaMarkupData` to that file's existing `bot.store.*_data` imports:

```python
class MySQLMediaMarkupDb(_MySQLBaseDb):
    TABLE_NAME = 'media_markup'

    def create(self, media_id: int, kind: str, shape: str, style: str,
               text=None) -> 'MediaMarkupData':
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO media_markup"
                " (media_id, kind, shape, style, text, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (media_id, kind, shape, style, text, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        self.mydb.commit()
        m = MediaMarkupData()
        m.id = new_id or 0
        m.media_id, m.kind, m.shape, m.style, m.text = media_id, kind, shape, style, text
        m.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return m

    def get_by_id(self, mark_id: int):
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("SELECT * FROM media_markup WHERE id = %s", (mark_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return MediaMarkupData.import_dict(row) if row else None

    def get_by_media(self, media_id: int) -> list:
        """Oldest first: paint order IS document order in SVG, so a later mark
        must draw on top of an earlier one."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "SELECT * FROM media_markup WHERE media_id = %s"
                " ORDER BY created_at, id", (media_id,))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [MediaMarkupData.import_dict(r) for r in rows]

    def update(self, mark_id: int, style: str, text) -> None:
        """Only style and text are editable. Geometry is not: a redrawn stroke
        is a new mark, and allowing a shape rewrite would mean re-validating a
        kind the row already committed to."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "UPDATE media_markup SET style = %s, text = %s WHERE id = %s",
                (style, text, mark_id))
        finally:
            cursor.close()
        self.mydb.commit()

    def delete(self, mark_id: int) -> None:
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("DELETE FROM media_markup WHERE id = %s", (mark_id,))
        finally:
            cursor.close()
        self.mydb.commit()
```

- [ ] **Step 3: Bind it in `bot/store/__init__.py`**

Next to the existing `CanvasRegionJsonDb = MySQLCanvasRegionDb(mydb=mydb)` (~line 149), following whatever import/export pattern that file already uses for the canvas DAOs:

```python
    MediaMarkupJsonDb = MySQLMediaMarkupDb(mydb=mydb)
```

- [ ] **Step 4: Verify nothing broke**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: PASS — the same count as before this task (the suite was 1410+ passing at the last canvas commit); no new failures.

- [ ] **Step 5: Lint, typecheck and commit**

```bash
pipenv run ruff check
pipenv run basedpyright bot/
git add bot/store/markup_data.py bot/store/mysqldb.py bot/store/__init__.py
git commit -m "feat(markup): add the media_markup dataclass and DAO"
```

---

### Task 4: The service

**Files:**
- Create: `bot/core/markup.py`
- Test: `tests/test_markup_service.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The markup service: validate, then read/write through the DAO.

The DAO is patched at the service's own import site per docs/agents/python.md —
markup.py does `from bot.store import ...` at module load, binding its own name.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from bot.core import markup
from bot.core.markup_shape import MarkupRejected
from bot.store.markup_data import MediaMarkupData

PEN = {"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]}
STYLE = {"colour": "#e03131", "width": 0.004}


def _mark(mark_id=9, media_id=7, kind="pen"):
    m = MediaMarkupData()
    m.id, m.media_id, m.kind = mark_id, media_id, kind
    m.shape = json.dumps({"points": [[0.1, 0.2], [0.3, 0.4]]})
    m.style = json.dumps({"colour": "#e03131", "width": 0.004, "opacity": 1, "arrow": False})
    return m


@pytest.fixture
def db():
    marks = MagicMock()
    marks.create.return_value = _mark()
    marks.get_by_id.return_value = _mark()
    with patch.object(markup, "MediaMarkupJsonDb", marks):
        yield marks


def test_creating_a_mark_stores_validated_geometry_and_style(db):
    markup.create_mark(media_id=7, mark=PEN, style=STYLE)
    kwargs = db.create.call_args.kwargs
    assert kwargs["media_id"] == 7
    assert kwargs["kind"] == "pen"
    assert json.loads(kwargs["shape"])["points"] == [[0.1, 0.2], [0.3, 0.4]]
    # Defaults are filled in by the validator, not left to the DB.
    assert json.loads(kwargs["style"])["opacity"] == 1.0
    assert kwargs["text"] is None


def test_a_malformed_mark_never_reaches_the_db(db):
    with pytest.raises(MarkupRejected):
        markup.create_mark(media_id=7, mark={"kind": "pen", "points": [[9, 9]]},
                           style=STYLE)
    db.create.assert_not_called()


def test_a_text_mark_stores_its_text_separately(db):
    markup.create_mark(
        media_id=7,
        mark={"kind": "text", "points": [[0.2, 0.3]], "text": "ROT13", "size": 0.05},
        style=STYLE)
    kwargs = db.create.call_args.kwargs
    assert kwargs["kind"] == "text"
    assert kwargs["text"] == "ROT13"


def test_updating_a_mark_from_another_image_is_refused(db):
    """The route checks the parent too, but the service must not depend on it:
    a mark id is client-supplied and this is the only place both ids meet."""
    db.get_by_id.return_value = _mark(media_id=99)
    with pytest.raises(markup.MarkNotFound):
        markup.update_mark(mark_id=9, media_id=7, style=STYLE, text=None)
    db.update.assert_not_called()


def test_updating_a_text_mark_keeps_its_text_when_none_is_given(db):
    """An absent text means 'style only'. Reading it as None would silently
    empty a text box on a colour change."""
    existing = _mark(kind="text")
    existing.text = "ROT13"
    db.get_by_id.return_value = existing
    markup.update_mark(mark_id=9, media_id=7, style=STYLE, text=None)
    assert db.update.call_args.kwargs["text"] == "ROT13"


def test_deleting_a_mark_from_another_image_is_refused(db):
    db.get_by_id.return_value = _mark(media_id=99)
    with pytest.raises(markup.MarkNotFound):
        markup.delete_mark(mark_id=9, media_id=7)
    db.delete.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_markup_service.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.core.markup'`.

- [ ] **Step 3: Write the implementation**

```python
"""Markup on a stored image.

Purely visual: a mark is never linked to a puzzle, never spawns an evidence
row, and is never cropped or OCR'd. That is what keeps this module free of the
region/evidence coupling canvas.py owns — there is nothing here to keep in step
with anything else.

Lock-free by the bot/core convention: callers lock.
"""
import logging

from bot.core.markup_shape import parse_mark, parse_style
from bot.store import MediaMarkupJsonDb

logger = logging.getLogger(__name__)


class MarkNotFound(Exception):
    """No such mark on that image. The message is safe to show a user."""


def list_marks(media_id: int) -> list:
    """Every mark on one image, oldest first — SVG paint order is document
    order, so a later mark must draw over an earlier one."""
    return MediaMarkupJsonDb.get_by_media(media_id)


def create_mark(media_id: int, mark, style):
    """Validate and store one mark."""
    kind, shape, text = parse_mark(mark)
    return MediaMarkupJsonDb.create(
        media_id=media_id, kind=kind, shape=shape,
        style=parse_style(style), text=text)


def _owned(mark_id: int, media_id: int):
    """The mark, if it belongs to that image.

    Checked here and not only in the route: a mark id is client-supplied, and
    this is the one place both ids are in scope.
    """
    existing = MediaMarkupJsonDb.get_by_id(mark_id)
    if existing is None or existing.media_id != media_id:
        raise MarkNotFound("That mark no longer exists.")
    return existing


def update_mark(mark_id: int, media_id: int, style, text=None):
    """Restyle a mark, and retext it when `text` is given.

    Geometry is never updated: a redrawn stroke is a new mark. A None `text`
    means "unchanged" rather than "empty" — reading it as empty would wipe a
    text box every time someone changed its colour.
    """
    existing = _owned(mark_id, media_id)
    MediaMarkupJsonDb.update(
        mark_id, style=parse_style(style),
        text=existing.text if text is None else text.strip()[:500])
    return MediaMarkupJsonDb.get_by_id(mark_id)


def delete_mark(mark_id: int, media_id: int) -> None:
    _owned(mark_id, media_id)
    MediaMarkupJsonDb.delete(mark_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_markup_service.py -q > /tmp/test.log 2>&1`
Expected: PASS, 6 tests.

- [ ] **Step 5: Prove the ownership check is load-bearing by mutation**

In `_owned`, temporarily drop `or existing.media_id != media_id`.
Expected: `test_updating_a_mark_from_another_image_is_refused` and `test_deleting_a_mark_from_another_image_is_refused` both FAIL (no exception raised). Restore the condition by editing it back.

- [ ] **Step 6: Lint, typecheck and commit**

```bash
pipenv run ruff check
pipenv run basedpyright bot/
git add bot/core/markup.py tests/test_markup_service.py
git commit -m "feat(markup): add the markup service"
```

---

### Task 5: Serialisation

**Files:**
- Modify: `bot/web/data.py` (add `mark_to_json` next to `region_to_json`, ~line 178)
- Test: `tests/test_markup_web.py` (created here, extended in Task 6)

- [ ] **Step 1: Write the failing test**

```python
"""Markup serialisation and routes."""
import json
from types import SimpleNamespace

from bot.web import data as web_data


def _mark(**kw):
    row = dict(id=9, media_id=7, kind="pen",
               shape=json.dumps({"points": [[0.1, 0.2], [0.3, 0.4]]}),
               style=json.dumps({"colour": "#e03131", "width": 0.004,
                                 "opacity": 1, "arrow": False}),
               text=None, created_at=None)
    row.update(kw)
    return SimpleNamespace(**row)


def test_a_mark_serialises_its_shape_and_style_as_objects():
    """Both columns are parsed back rather than passed through as strings, so
    the viewer never double-decodes — same contract as region_to_json."""
    out = web_data.mark_to_json(_mark())
    assert out["shape"]["points"] == [[0.1, 0.2], [0.3, 0.4]]
    assert out["style"]["colour"] == "#e03131"
    assert out["kind"] == "pen"
    assert out["text"] is None


def test_a_text_mark_serialises_its_text():
    out = web_data.mark_to_json(_mark(kind="text", text="ROT13"))
    assert out["text"] == "ROT13"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 180 pipenv run python -m pytest tests/test_markup_web.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: module 'bot.web.data' has no attribute 'mark_to_json'`.

- [ ] **Step 3: Write the implementation**

Add to `bot/web/data.py`, directly after `region_to_json`:

```python
def mark_to_json(mark):
    """One markup mark as the dashboard sees it.

    shape and style are parsed back to objects rather than passed through as
    JSON strings, so the viewer never has to double-decode. Both were written
    by markup_shape, which is the only thing that produces those columns.
    """
    import json as _json
    return {
        "id": mark.id,
        "media_id": mark.media_id,
        "kind": mark.kind,
        "shape": _json.loads(mark.shape),
        "style": _json.loads(mark.style),
        "text": mark.text,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `timeout 180 pipenv run python -m pytest tests/test_markup_web.py -q > /tmp/test.log 2>&1`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add bot/web/data.py tests/test_markup_web.py
git commit -m "feat(markup): serialise a mark for the dashboard"
```

---

### Task 6: The routes

**Files:**
- Modify: `bot/web/api.py` (add `GET /media/<int:media_id>/markup` after `media_tile`, ~line 300)
- Modify: `bot/web/actions.py` (add `_media_ctx` near `_canvas_ctx` ~line 2135, and three routes after the region routes ~line 2380)
- Test: `tests/test_markup_web.py` (extend)

The permission bar is the one that already governs `/api/media/<id>`: any member who can see an unarchived hunt. Not organiser — drawing is the same bar as logging evidence.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_markup_web.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt


def _media(id=7, hunt_id=10):
    return SimpleNamespace(id=id, hunt_id=hunt_id, width=1200, height=900,
                           tile_status="none", tile_levels=0)


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"
    cog = MagicMock()
    cog.service.notifier.board_changed = AsyncMock()
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    mdb, hdb = MagicMock(), MagicMock()
    mdb.get_by_id.return_value = _media()
    hdb.get_by_attr.return_value = make_hunt(id=10)

    service = MagicMock()
    service.create_mark.return_value = _mark()
    service.update_mark.return_value = _mark()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.core.markup.create_mark", service.create_mark),
        patch("bot.core.markup.update_mark", service.update_mark),
        patch("bot.core.markup.delete_mark", service.delete_mark),
    ]
    for p in patches:
        p.start()
    yield app, service, ctx, mdb, cog
    for p in patches:
        p.stop()


async def test_creating_a_mark_returns_it_and_pushes_the_board(web):
    app, service, _ctx, _mdb, cog = web
    client = app.test_client()
    resp = await client.post("/api/media/7/markup", json={
        "mark": {"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]},
        "style": {"colour": "#e03131", "width": 0.004},
    })
    assert resp.status_code == 201
    assert (await resp.get_json())["kind"] == "pen"
    assert service.create_mark.call_args.kwargs["media_id"] == 7
    cog.service.notifier.board_changed.assert_awaited()


async def test_a_malformed_mark_is_a_400_not_a_500(web):
    app, service, *_ = web
    from bot.core.markup_shape import MarkupRejected
    service.create_mark.side_effect = MarkupRejected("That mark has no length.")
    client = app.test_client()
    resp = await client.post("/api/media/7/markup", json={
        "mark": {"kind": "pen", "points": [[0.5, 0.5], [0.5, 0.5]]},
        "style": {"colour": "#e03131"},
    })
    assert resp.status_code == 400
    assert "no length" in (await resp.get_json())["error"]


async def test_markup_on_another_hunts_image_is_refused(web):
    """The media row carries the hunt, so this is the one way past media
    visibility — it must 404, not fall through to the current user's hunt."""
    app, service, _ctx, mdb, _cog = web
    mdb.get_by_id.return_value = None
    client = app.test_client()
    resp = await client.post("/api/media/7/markup", json={
        "mark": {"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]},
        "style": {"colour": "#e03131"},
    })
    assert resp.status_code == 404
    service.create_mark.assert_not_called()


async def test_markup_on_an_archived_hunts_image_is_refused(web):
    app, service, ctx, *_ = web
    ctx["hunt"]["archived"] = True
    client = app.test_client()
    resp = await client.post("/api/media/7/markup", json={
        "mark": {"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]},
        "style": {"colour": "#e03131"},
    })
    assert resp.status_code == 403
    service.create_mark.assert_not_called()


async def test_erasing_a_mark_deletes_it_and_pushes_the_board(web):
    app, service, _ctx, _mdb, cog = web
    client = app.test_client()
    resp = await client.delete("/api/media/7/markup/9")
    assert resp.status_code == 200
    service.delete_mark.assert_called_once_with(mark_id=9, media_id=7)
    cog.service.notifier.board_changed.assert_awaited()


async def test_erasing_a_missing_mark_is_a_404(web):
    app, service, *_ = web
    from bot.core.markup import MarkNotFound
    service.delete_mark.side_effect = MarkNotFound("That mark no longer exists.")
    client = app.test_client()
    resp = await client.delete("/api/media/7/markup/9")
    assert resp.status_code == 404


async def test_restyling_a_mark_returns_the_updated_row(web):
    app, service, *_ = web
    client = app.test_client()
    resp = await client.patch("/api/media/7/markup/9",
                              json={"style": {"colour": "#2f9e44"}})
    assert resp.status_code == 200
    assert service.update_mark.call_args.kwargs["mark_id"] == 9
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_markup_web.py -q > /tmp/test.log 2>&1`
Expected: FAIL — 404 on every route (they do not exist yet).

- [ ] **Step 3: Write `_media_ctx` in `bot/web/actions.py`**

Place it directly after `_canvas_ctx`:

```python
async def _media_ctx(media_id):
    """(ctx, media_row, err) for an image the current user may mark up.

    The hunt comes from the media row rather than the URL, which is what stops
    a media id from another hunt being acted on under this hunt's visibility
    check — the same shape /api/media/<id> uses to serve the bytes.

    Marking up is NOT organiser-gated: it is the same bar as logging evidence,
    because marking up the picture is what everyone does during a hunt.
    """
    from bot.store import HuntJsonDb, MediaJsonDb
    media = MediaJsonDb.get_by_id(media_id)
    if media is None:
        return None, None, (jsonify({"error": "media_not_found"}), 404)
    hunt_row = HuntJsonDb.get_by_attr(id=media.hunt_id)
    if hunt_row is None:
        return None, None, (jsonify({"error": "media_not_found"}), 404)
    ctx, err = await _visible_hunt_by_uid(hunt_row.uid)
    if err:
        return None, None, err
    assert ctx is not None
    if ctx["hunt"]["archived"]:
        return None, None, (jsonify({"error": "hunt_archived"}), 403)
    return ctx, media, None
```

- [ ] **Step 4: Write the three mutation routes in `bot/web/actions.py`**

After the region routes:

```python
@actions_bp.route("/media/<int:media_id>/markup", methods=["POST"])
@login_required
async def create_mark(media_id):
    from bot.core import markup as markup_service
    from bot.core.markup_shape import MarkupRejected
    ctx, media, err = await _media_ctx(media_id)
    if err:
        return err
    assert ctx is not None and media is not None
    body = await request.get_json(silent=True) or {}
    try:
        mark = markup_service.create_mark(
            media_id=media_id, mark=body.get("mark"), style=body.get("style"))
    except MarkupRejected as exc:
        return jsonify({"error": str(exc)}), 400
    await _push_board(ctx["hunt"]["id"])
    await _audit("create_mark", hunt_uid=ctx["hunt"]["uid"])
    return jsonify(web_data.mark_to_json(mark)), 201


@actions_bp.route("/media/<int:media_id>/markup/<int:mark_id>", methods=["PATCH"])
@login_required
async def update_mark(media_id, mark_id):
    from bot.core import markup as markup_service
    from bot.core.markup_shape import MarkupRejected
    ctx, media, err = await _media_ctx(media_id)
    if err:
        return err
    assert ctx is not None and media is not None
    body = await request.get_json(silent=True) or {}
    try:
        mark = markup_service.update_mark(
            mark_id=mark_id, media_id=media_id,
            style=body.get("style"), text=body.get("text"))
    except markup_service.MarkNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except MarkupRejected as exc:
        return jsonify({"error": str(exc)}), 400
    await _push_board(ctx["hunt"]["id"])
    return jsonify(web_data.mark_to_json(mark))


@actions_bp.route("/media/<int:media_id>/markup/<int:mark_id>", methods=["DELETE"])
@login_required
async def delete_mark(media_id, mark_id):
    from bot.core import markup as markup_service
    ctx, media, err = await _media_ctx(media_id)
    if err:
        return err
    assert ctx is not None and media is not None
    try:
        markup_service.delete_mark(mark_id=mark_id, media_id=media_id)
    except markup_service.MarkNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    await _push_board(ctx["hunt"]["id"])
    return jsonify({"ok": True})
```

- [ ] **Step 5: Write the read route in `bot/web/api.py`**

After `media_tile`:

```python
@api_bp.route("/media/<int:media_id>/markup")
@login_required
async def media_markup(media_id):
    """Every mark on one image. Authorised exactly as /api/media/<id> is."""
    from bot.core import markup as markup_service
    from bot.store import HuntJsonDb, MediaJsonDb
    from bot.web import data as web_data
    media = MediaJsonDb.get_by_id(media_id)
    if media is None:
        return jsonify({"error": "not_found"}), 404
    hunt_row = HuntJsonDb.get_by_attr(id=media.hunt_id)
    if hunt_row is None:
        return jsonify({"error": "not_found"}), 404
    hunt, guild_or_status = await _load_visible_hunt(hunt_row.uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)
        return jsonify({"error": "not_found_or_forbidden"}), guild_or_status
    return jsonify([web_data.mark_to_json(m)
                    for m in markup_service.list_marks(media_id)])
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_markup_web.py -q > /tmp/test.log 2>&1`
Expected: PASS, 9 tests.

- [ ] **Step 7: Prove the permission bar is load-bearing by mutation**

In `_media_ctx`, temporarily delete the `if ctx["hunt"]["archived"]` branch.
Expected: `test_markup_on_an_archived_hunts_image_is_refused` FAILS (201 instead of 403). Restore by editing the branch back.

- [ ] **Step 8: Run the full suite, lint, typecheck and commit**

```bash
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
pipenv run ruff check
pipenv run basedpyright bot/
git add bot/web/actions.py bot/web/api.py tests/test_markup_web.py
git commit -m "feat(markup): add the markup routes"
```

---

### Task 7: Media dimensions on the evidence payload

The markup overlay needs the image's aspect ratio to lay itself out, and the evidence payload carries only a URL today.

**Files:**
- Modify: `bot/web/data.py` (`get_evidence`, ~line 656–681)
- Test: `tests/test_evidence_web.py` if it exists, else add to `tests/test_media_web.py`

- [ ] **Step 1: Find the serialisation and its existing test**

Run: `grep -n "media_url" bot/web/data.py` and `grep -rn "media_url" tests/ | head`
Use whichever test file already covers `get_evidence`'s media fields; add to it rather than creating a new one.

- [ ] **Step 2: Write the failing test**

```python
async def test_evidence_carries_its_images_dimensions():
    """The markup overlay sizes itself from these; without them it cannot lay
    out at all. Asserted as a pair with DIFFERENT values, so a transposed
    width/height cannot pass."""
    # Build the same fixtures the neighbouring get_evidence tests use, with a
    # media row of width=1200, height=900 attached to the evidence item.
    rows = await web_data.get_evidence(hunt_uid="U1")
    assert rows[0]["media_width"] == 1200
    assert rows[0]["media_height"] == 900
```

Match the surrounding tests' fixture style exactly — read the neighbouring test before writing this one, and set width and height to different values so a swap is caught.

- [ ] **Step 3: Run the test to verify it fails**

Run: `timeout 180 pipenv run python -m pytest <that file> -q -k dimensions > /tmp/test.log 2>&1`
Expected: FAIL — `KeyError: 'media_width'`.

- [ ] **Step 4: Add the fields**

In `get_evidence`, wherever `media_url` is built from the batched media lookup, add alongside it:

```python
        # The markup overlay lays itself out from the image's aspect ratio, so
        # the dimensions travel with the URL. Every media row has them:
        # MediaJsonDb.create records them at upload whether or not the image is
        # later tiled.
        "media_width": media_row.width if media_row else None,
        "media_height": media_row.height if media_row else None,
```

- [ ] **Step 5: Run the test to verify it passes**

Run the same command. Expected: PASS.

- [ ] **Step 6: Run the full suite and commit**

```bash
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
pipenv run ruff check
pipenv run basedpyright bot/
git add bot/web/data.py tests/
git commit -m "feat(markup): carry image dimensions on the evidence payload"
```

---

### Task 8: Frontend types and hooks

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add the types**

In `frontend/src/types.ts`, near `CanvasRegion`:

```ts
export type MarkKind = "pen" | "line" | "text";

export interface MarkStyle {
  colour: string;
  width: number;
  opacity: number;
  arrow: boolean;
}

/** One purely-visual mark on an image. Coordinates are normalised 0-1, so the
 *  SVG overlay uses them directly at any zoom. */
export interface MediaMark {
  id: number;
  media_id: number;
  kind: MarkKind;
  shape: { points: [number, number][]; size?: number };
  style: MarkStyle;
  text: string | null;
}
```

And on the `EvidenceItem` interface (~line 245), beside `media_url`:

```ts
  media_width: number | null;
  media_height: number | null;
```

- [ ] **Step 2: Add the hooks**

In `frontend/src/api.ts`, after the region hooks:

```ts
export function useMarkup(mediaId: number | null) {
  return useQuery({
    queryKey: ["markup", mediaId],
    queryFn: () => apiGet<MediaMark[]>(`/api/media/${mediaId}/markup`),
    enabled: mediaId != null,
  });
}

/** The geometry half of a write. Style travels separately because it is the
 *  only part an existing mark can change. */
type MarkPayload = {
  kind: MarkKind;
  points: [number, number][];
  text?: string;
  size?: number;
};

export function useCreateMark(mediaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mark, style }: { mark: MarkPayload; style: Partial<MarkStyle> }) =>
      apiSend<MediaMark>(`/api/media/${mediaId}/markup`, "POST", { mark, style }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["markup", mediaId] }),
  });
}

export function useUpdateMark(mediaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ markId, style, text }:
      { markId: number; style: Partial<MarkStyle>; text?: string }) =>
      apiSend<MediaMark>(`/api/media/${mediaId}/markup/${markId}`, "PATCH", { style, text }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["markup", mediaId] }),
  });
}

export function useDeleteMark(mediaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (markId: number) =>
      apiSend<{ ok: boolean }>(`/api/media/${mediaId}/markup/${markId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["markup", mediaId] }),
  });
}
```

Add `MarkKind`, `MarkStyle` and `MediaMark` to the existing `import type { ... } from "./types"` block at the top of `api.ts`.

Note: `useUpdateMark` has **no caller in this plan** — restyling an existing mark has no UI yet, and the route exists because the service and route tests cover it. If that bothers a reviewer, the honest options are to leave it (the backend route is real and tested) or to drop the hook and add it with its UI later. Do not invent a restyle UI to justify it.

- [ ] **Step 3: Typecheck and commit**

```bash
cd frontend && npx tsc --noEmit && cd ..
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(markup): add frontend types and markup hooks"
```

---

### Task 9: `MarkupCanvas` — render and draw

No frontend test runner exists in this repo (`package.json` has no vitest/jest), so Tasks 9–11 are verified by `tsc --noEmit`, `npm run build`, and the manual checklist in Task 12. Do not claim behavioural verification from a green build.

**Files:**
- Create: `frontend/src/components/MarkupCanvas.tsx`

- [ ] **Step 1: Write the component**

```tsx
import { useCallback, useMemo, useRef, useState } from "react";
import { Box, Button, ColorSwatch, Group, Slider, Stack, Text, Textarea } from "@mantine/core";
import {
  IconArrowUpRight, IconEraser, IconHighlight, IconLine, IconPencil,
  IconTextSize, IconArrowBackUp,
} from "@tabler/icons-react";
import type { MarkKind, MarkStyle, MediaMark } from "../types";
import { useCreateMark, useDeleteMark, useMarkup } from "../api";

type Tool = "pen" | "line" | "arrow" | "highlighter" | "text" | "eraser" | null;

const COLOURS = ["#e03131", "#1971c2", "#2f9e44", "#f08c00", "#212529"];

/** Drop points closer together than this. A drag emits hundreds of pointer
 *  events per second; without thinning, one stroke would store thousands of
 *  points and the server would refuse it outright (MAX_POINTS). */
const MIN_STEP = 0.001;

/** Round stored coordinates. Four decimal places is sub-pixel on a 10000px
 *  image and keeps the JSON blob small. */
const round4 = (n: number) => Math.round(n * 10000) / 10000;

function styleFor(tool: Tool, colour: string, width: number): Partial<MarkStyle> {
  // A highlighter is not a kind — it is a fat, low-opacity pen, and the server
  // stores exactly that.
  if (tool === "highlighter") return { colour, width: width * 4, opacity: 0.35 };
  return { colour, width, opacity: 1, arrow: tool === "arrow" };
}

function kindFor(tool: Tool): MarkKind {
  if (tool === "line" || tool === "arrow") return "line";
  if (tool === "text") return "text";
  return "pen";
}

type Props = {
  mediaId: number;
  width: number;
  height: number;
  alt: string;
};

export function MarkupCanvas({ mediaId, width, height, alt }: Props) {
  const marks = useMarkup(mediaId);
  const createMark = useCreateMark(mediaId);
  const deleteMark = useDeleteMark(mediaId);

  const [tool, setTool] = useState<Tool>("pen");
  const [colour, setColour] = useState(COLOURS[0]);
  const [width_, setWidth] = useState(0.003);
  const [stroke, setStroke] = useState<[number, number][]>([]);
  const [textAt, setTextAt] = useState<null | { x: number; y: number }>(null);
  const [draftText, setDraftText] = useState("");
  /** Ids created in this session, newest last — undo pops from here. */
  const mine = useRef<number[]>([]);
  const imgRef = useRef<HTMLImageElement>(null);
  const drawing = useRef(false);

  /** Pointer position as normalised image coordinates. getBoundingClientRect
   *  already reflects any CSS transform, so this stays correct at every zoom. */
  const toNorm = useCallback((e: React.PointerEvent): [number, number] | null => {
    const rect = imgRef.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return null;
    return [
      Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    ];
  }, []);

  const commitStroke = useCallback(async (points: [number, number][]) => {
    if (points.length < 2) return;
    const rounded = points.map(([x, y]) => [round4(x), round4(y)]) as [number, number][];
    const mark = await createMark.mutateAsync({
      mark: { kind: kindFor(tool), points: rounded },
      style: styleFor(tool, colour, width_),
    });
    mine.current.push(mark.id);
  }, [createMark, tool, colour, width_]);

  const onPointerDown = (e: React.PointerEvent) => {
    const at = toNorm(e);
    if (!at || !tool) return;
    if (tool === "eraser") return;                    // handled per-mark below
    if (tool === "text") { setTextAt({ x: at[0], y: at[1] }); return; }
    drawing.current = true;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setStroke([at]);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drawing.current) return;
    const at = toNorm(e);
    if (!at) return;
    setStroke((s) => {
      if (tool === "line" || tool === "arrow") return [s[0], at];
      const last = s[s.length - 1];
      // Thin as we capture, not on commit: the in-progress path is re-rendered
      // on every move, so an unthinned stroke costs frames as well as bytes.
      if (last && Math.hypot(at[0] - last[0], at[1] - last[1]) < MIN_STEP) return s;
      return [...s, at];
    });
  };

  const onPointerUp = () => {
    if (!drawing.current) return;
    drawing.current = false;
    const points = stroke;
    setStroke([]);
    void commitStroke(points);
  };

  const commitText = async () => {
    const body = draftText.trim();
    const at = textAt;
    setTextAt(null);
    setDraftText("");
    if (!body || !at) return;
    const mark = await createMark.mutateAsync({
      mark: { kind: "text", points: [[round4(at.x), round4(at.y)]], text: body, size: 0.04 },
      style: styleFor("text", colour, width_),
    });
    mine.current.push(mark.id);
  };

  const undo = () => {
    const last = mine.current.pop();
    if (last != null) deleteMark.mutate(last);
  };

  const aspect = useMemo(() => (width && height ? width / height : 1), [width, height]);

  return (
    <Stack gap="xs">
      <Group gap="xs">
        <Button size="xs" variant={tool === "pen" ? "filled" : "light"}
                leftSection={<IconPencil size={14} />}
                onClick={() => setTool("pen")}>Pen</Button>
        <Button size="xs" variant={tool === "line" ? "filled" : "light"}
                leftSection={<IconLine size={14} />}
                onClick={() => setTool("line")}>Line</Button>
        <Button size="xs" variant={tool === "arrow" ? "filled" : "light"}
                leftSection={<IconArrowUpRight size={14} />}
                onClick={() => setTool("arrow")}>Arrow</Button>
        <Button size="xs" variant={tool === "highlighter" ? "filled" : "light"}
                leftSection={<IconHighlight size={14} />}
                onClick={() => setTool("highlighter")}>Highlight</Button>
        <Button size="xs" variant={tool === "text" ? "filled" : "light"}
                leftSection={<IconTextSize size={14} />}
                onClick={() => setTool("text")}>Text</Button>
        <Button size="xs" variant={tool === "eraser" ? "filled" : "light"}
                leftSection={<IconEraser size={14} />}
                onClick={() => setTool("eraser")}>Erase</Button>
        <Button size="xs" variant="default" leftSection={<IconArrowBackUp size={14} />}
                onClick={undo}>Undo</Button>
        <Group gap={4}>
          {COLOURS.map((c) => (
            <ColorSwatch key={c} color={c} size={18} style={{ cursor: "pointer" }}
                         onClick={() => setColour(c)}
                         withShadow={c === colour} />
          ))}
        </Group>
        <Slider w={90} min={0.001} max={0.01} step={0.001} value={width_}
                onChange={setWidth} label={null} />
      </Group>

      <Box
        style={{ position: "relative", width: "100%", aspectRatio: String(aspect),
                 touchAction: "none", cursor: tool === "eraser" ? "pointer" : "crosshair" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        <img ref={imgRef} src={`/api/media/${mediaId}`} alt={alt} draggable={false}
             style={{ width: "100%", height: "100%", objectFit: "contain",
                      display: "block", userSelect: "none" }} />

        {/* Normalised coordinates go straight in — the same payoff the region
            overlay gets from viewBox="0 0 1 1". preserveAspectRatio="none"
            because the box above already matches the image's aspect ratio. */}
        <svg viewBox="0 0 1 1" preserveAspectRatio="none"
             style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                      pointerEvents: "none" }}>
          <defs>
            {COLOURS.map((c) => (
              <marker key={c} id={`arrow-${c.slice(1)}`} viewBox="0 0 10 10"
                      refX="8" refY="5" markerWidth="6" markerHeight="6"
                      orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill={c} />
              </marker>
            ))}
          </defs>
          {(marks.data ?? []).map((m) => (
            <MarkShape key={m.id} mark={m} erasing={tool === "eraser"}
                       onErase={() => deleteMark.mutate(m.id)} />
          ))}
          {stroke.length > 1 && (
            <polyline points={stroke.map(([x, y]) => `${x},${y}`).join(" ")}
                      fill="none" stroke={colour}
                      strokeOpacity={tool === "highlighter" ? 0.35 : 1}
                      strokeWidth={tool === "highlighter" ? width_ * 4 : width_}
                      strokeLinecap="round" strokeLinejoin="round" />
          )}
        </svg>

        {textAt && (
          <Box style={{ position: "absolute", left: `${textAt.x * 100}%`,
                        top: `${textAt.y * 100}%`, width: 220, zIndex: 5 }}>
            <Textarea autoFocus autosize minRows={1} maxRows={4} value={draftText}
                      onChange={(e) => setDraftText(e.currentTarget.value)}
                      onKeyDown={(e) => {
                        // Matches EvidencePanel's multi-line notes: Enter breaks
                        // the line, mod+Enter commits, Escape cancels.
                        if (e.key === "Escape") { setTextAt(null); setDraftText(""); }
                        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void commitText();
                      }}
                      onBlur={() => void commitText()}
                      placeholder="Text, then Ctrl/⌘+Enter" />
          </Box>
        )}
      </Box>

      <Text size="xs" c="dimmed">
        Markup is visual only — it is not a clue and does not appear in the evidence table.
      </Text>
    </Stack>
  );
}

function MarkShape({ mark, erasing, onErase }:
  { mark: MediaMark; erasing: boolean; onErase: () => void }) {
  const { colour, width, opacity, arrow } = mark.style;
  const hit = erasing
    ? { pointerEvents: "all" as const, cursor: "pointer" }
    : { pointerEvents: "none" as const };
  if (mark.kind === "text") {
    const [[x, y]] = mark.shape.points;
    return (
      <text x={x} y={y} fill={colour} fillOpacity={opacity}
            fontSize={mark.shape.size ?? 0.04} style={hit}
            onPointerDown={erasing ? onErase : undefined}>
        {mark.text}
      </text>
    );
  }
  return (
    <polyline
      points={mark.shape.points.map(([x, y]) => `${x},${y}`).join(" ")}
      fill="none" stroke={colour} strokeOpacity={opacity} strokeWidth={width}
      strokeLinecap="round" strokeLinejoin="round"
      markerEnd={arrow ? `url(#arrow-${colour.slice(1)})` : undefined}
      style={hit}
      onPointerDown={erasing ? onErase : undefined}
    />
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd frontend && npx tsc --noEmit
```
Expected: no errors. If `@tabler/icons-react` lacks any icon named above, substitute an existing one — check with `grep -o "Icon[A-Za-z]*" src/components/*.tsx | sort -u`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MarkupCanvas.tsx
git commit -m "feat(markup): add the MarkupCanvas drawing surface"
```

---

### Task 10: Zoom and pan

**Files:**
- Modify: `frontend/src/components/MarkupCanvas.tsx`

- [ ] **Step 1: Add zoom state and the wheel listener**

Add these imports: `useEffect` from react. Inside the component, above `toNorm`:

```tsx
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const frameRef = useRef<HTMLDivElement>(null);
  const panning = useRef<null | { sx: number; sy: number; ox: number; oy: number }>(null);

  /** Zoom about an anchor in frame-local pixels, keeping whatever is under the
   *  anchor fixed. Same arithmetic as CanvasView.zoomAt. */
  const zoomAt = useCallback((factor: number, ax: number, ay: number) => {
    setZoom((z) => {
      const next = Math.min(8, Math.max(1, z * factor));
      setPan((p) => ({
        x: ax - ((ax - p.x) * next) / z,
        y: ay - ((ay - p.y) * next) / z,
      }));
      return next;
    });
  }, []);

  // A raw non-passive DOM listener, NOT React's onWheel: the synthetic wheel
  // listener is passive, so preventDefault silently no-ops there and the
  // browser's own page zoom fires underneath. This bit the canvas viewer.
  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;               // plain scroll still scrolls the modal
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.01), e.clientX - rect.left, e.clientY - rect.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);
```

- [ ] **Step 2: Let a drag pan when no tool is selected**

Add a "Pan" toolbar button that sets `tool` to `null`, and extend the three pointer handlers. In `onPointerDown`, before the existing body:

```tsx
    if (!tool) {
      const rect = frameRef.current?.getBoundingClientRect();
      if (!rect) return;
      panning.current = { sx: e.clientX, sy: e.clientY, ox: pan.x, oy: pan.y };
      (e.target as Element).setPointerCapture?.(e.pointerId);
      return;
    }
```

In `onPointerMove`, before the existing body:

```tsx
    if (panning.current) {
      const p = panning.current;
      setPan({ x: p.ox + (e.clientX - p.sx), y: p.oy + (e.clientY - p.sy) });
      return;
    }
```

In `onPointerUp`, before the existing body:

```tsx
    if (panning.current) { panning.current = null; return; }
```

Toolbar addition, next to the other tool buttons:

```tsx
        <Button size="xs" variant={tool === null ? "filled" : "light"}
                onClick={() => setTool(null)}>Pan</Button>
        <Button size="xs" variant="default" onClick={() => zoomAt(1.4, 0, 0)}>+</Button>
        <Button size="xs" variant="default" onClick={() => zoomAt(1 / 1.4, 0, 0)}>−</Button>
        <Button size="xs" variant="default"
                onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>Fit</Button>
```

- [ ] **Step 3: Wrap the image and overlay in the transformed frame**

Replace the outer `<Box style={{ position: "relative", ... }}>` with a clipping frame plus a transformed inner div. The image and the SVG both live inside the transform, so ink scales with the picture:

```tsx
      <Box ref={frameRef}
           style={{ position: "relative", width: "100%", aspectRatio: String(aspect),
                    overflow: "hidden", touchAction: "none",
                    cursor: tool === null ? "grab" : tool === "eraser" ? "pointer" : "crosshair" }}
           onPointerDown={onPointerDown}
           onPointerMove={onPointerMove}
           onPointerUp={onPointerUp}>
        <div style={{ position: "absolute", inset: 0,
                      transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                      transformOrigin: "0 0" }}>
          {/* the existing <img> and <svg> go here, unchanged — toNorm reads the
              image's own getBoundingClientRect, which already includes this
              transform, so no coordinate maths changes */}
        </div>
        {/* the text editor Box stays OUTSIDE the transform: its position is a
            percentage of the frame, and inside it would be scaled twice */}
      </Box>
```

The in-progress text editor's `left`/`top` must be computed from the transform when zoomed: `left: pan.x + textAt.x * frameWidth * zoom`. Simplest correct version — read the frame's width once at open time and store the editor's position in **frame pixels** rather than normalised units:

```tsx
  const [textAt, setTextAt] = useState<null | { x: number; y: number; px: number; py: number }>(null);
```
setting `px`/`py` from `e.clientX - rect.left` / `e.clientY - rect.top` at pointer-down, and rendering the editor at `left: textAt.px, top: textAt.py`. The stored mark still uses the normalised `x`/`y`.

- [ ] **Step 4: Typecheck and build**

```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: both clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MarkupCanvas.tsx
git commit -m "feat(markup): add wheel zoom and drag-pan to MarkupCanvas"
```

---

### Task 11: `MarkupModal` and the evidence entry point

**Files:**
- Create: `frontend/src/components/MarkupModal.tsx`
- Modify: `frontend/src/components/EvidencePanel.tsx` (the image cell, ~line 509–532)

- [ ] **Step 1: Write the modal**

```tsx
import { Modal } from "@mantine/core";
import { MarkupCanvas } from "./MarkupCanvas";

type Props = {
  mediaId: number | null;
  width: number | null;
  height: number | null;
  alt: string;
  onClose: () => void;
};

/** Full-screen wrapper so a marked-up image gets the whole window. Everything
 *  else lives in MarkupCanvas, which is reused by any surface that has a
 *  media id. */
export function MarkupModal({ mediaId, width, height, alt, onClose }: Props) {
  return (
    <Modal opened={mediaId != null} onClose={onClose} fullScreen title="Mark up">
      {mediaId != null && (
        <MarkupCanvas mediaId={mediaId} width={width ?? 0} height={height ?? 0} alt={alt} />
      )}
    </Modal>
  );
}
```

- [ ] **Step 2: Wire it into `EvidencePanel`**

Add the import, one piece of state in the panel component:

```tsx
  const [markupItem, setMarkupItem] = useState<EvidenceItem | null>(null);
```

Render the modal once, next to the panel's other modals:

```tsx
      <MarkupModal
        mediaId={markupItem?.media_id ?? null}
        width={markupItem?.media_width ?? null}
        height={markupItem?.media_height ?? null}
        alt={markupItem?.note ?? "Evidence image"}
        onClose={() => setMarkupItem(null)}
      />
```

In the row's image cell, beside the existing Replace / Add image control, add a button shown only when the row has an image, threading `onMarkup` down the same way `onReplaceImage` already is:

```tsx
          {item.media_url && (
            <Button size="compact-xs" variant="subtle" onClick={() => onMarkup(item)}>
              Mark up
            </Button>
          )}
```

Add `onMarkup: (item: EvidenceItem) => void;` to the row component's props type (alongside `onReplaceImage`, ~line 455) and pass `onMarkup={setMarkupItem}` where `onReplaceImage` is passed (~line 393).

- [ ] **Step 3: Typecheck and build**

```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: both clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MarkupModal.tsx frontend/src/components/EvidencePanel.tsx
git commit -m "feat(markup): mark up an evidence image from the evidence panel"
```

---

### Task 12: Verification pass

The frontend has no test runner, so this is the only thing that verifies the drawing behaves. Do not report the feature as working before it is done.

- [ ] **Step 1: Full backend gates**

```bash
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
pipenv run ruff check
pipenv run basedpyright bot/
```
Expected: all green, 0 pyright errors, and a pass count of at least the pre-task total plus the ~27 tests this plan adds.

- [ ] **Step 2: Deploy to the test site and walk it through**

Deploy to `puzzbot.com` (the Plesk test subscription) as the canvas work was, then check each of these against a real evidence image:

1. Open an evidence row's **Mark up** — the image appears at the right aspect ratio.
2. Draw a pen stroke. It persists after closing and reopening the modal.
3. Draw a **line** and an **arrow** — the arrowhead is at the end you finished on.
4. **Highlight** over text — the underlying image is still readable through it.
5. Place a **text box**, type two lines, commit with Ctrl/⌘+Enter.
6. **Erase** one mark — only that one goes.
7. **Undo** immediately after drawing — the last mark goes.
8. **Ctrl+wheel** zooms about the cursor and the browser's own page zoom does **not** fire. Ink stays glued to the image as you zoom.
9. Draw a stroke **while zoomed in**, then zoom back out — it must sit exactly where you drew it. (A coordinate bug only shows at non-default zoom; this is the single most important check.)
10. **Pan** with the Pan tool, draw again, confirm placement.
11. Second browser on the same evidence image: a mark made in one appears in the other without a reload (the `_push_board` path).
12. Check `discord.log` (not `bot.log`) for tracebacks after the pass.

- [ ] **Step 3: Record the outcome**

Update `.ai/active/session-summary.md` with what was verified and anything found. If a check fails, fix it before declaring the plan done.

---

## Follow-up plan (not this one)

`docs/superpowers/plans/2026-07-2X-puzzle-images.md` — `puzzle_images`, `canvas_regions.crop_media_id`, crops for `locates` regions, the region↔`puzzle_images` sync in `canvas.py`, the puzzle image routes with reordering, and the `PuzzleImages` strip in `PuzzleDrawer` (which reuses `MarkupModal` from this plan unchanged).
