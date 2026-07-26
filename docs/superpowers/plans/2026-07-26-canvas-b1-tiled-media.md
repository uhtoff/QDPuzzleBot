# Canvas B1 — Tiled Media Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an image far too large for a browser to decode viewable, by deriving a WebP
tile pyramid at upload time and serving it through an authenticated, hunt-scoped route.

**Architecture:** `bot/core/image_tiler.py` is pure — a source path and a destination
directory in, `(width, height, levels)` out, no DAOs and no Quart, so it unit-tests against
nothing but a file on disk. `bot/core/media_tiling.py` is the thin async orchestrator that
runs the tiler in an executor and moves the `media` row through
`pending → ready | failed`. The upload route records dimensions for every image and requests
tiling only for images above the direct-decode ceiling. Region coordinates stay normalised
0–1, so tiling never reaches the data model.

**Tech Stack:** Python 3.12, `pyvips[binary]` (bundled libvips 8.18.4), Quart, MySQL,
pytest.

**Authoritative spec:** `docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md`,
section "Tiling". This plan implements **B1 only**. B2 (the canvas itself — `hunt_canvases`,
`canvas_regions`, the viewer, draw-to-create) is a separate plan and depends on this one.

**Scope honesty:** B1 is infrastructure. It ships no new UI, and nothing in the dashboard
consumes tiles until B2. It is verified by tests plus a manual tile fetch, not by looking at
the app.

---

## Measured facts this plan is built on

All measured on `images/extravaganza-14400x14400.png` (14400×14400, 207 MP, 16.5 MB) on a
12-core box. Do not re-litigate these; they are why the constants are what they are.

| what | result |
|---|---|
| `pyvips[binary]` 3.1.1 | bundles libvips 8.18.4 — pip-only, no root, no system packages |
| lossless WebP pyramid | 6 levels, 940 tiles, **9.6 MB** |
| lossy WebP Q82 pyramid | 11 MB — *larger* than lossless, and inexact |
| `VIPS_CONCURRENCY=1` | 13.6 s, 224 MB peak |
| **`VIPS_CONCURRENCY=2`** | **8.1 s, 340 MB peak** ← chosen |
| `VIPS_CONCURRENCY=4` | 6.7 s, 563 MB peak |
| unset (12 threads) | 6.9 s, 982 MB peak |

Two API constraints found by testing, not by reading docs:

- **pyvips 3.1.1 has no `concurrency_set`/`concurrency_get`.** The `VIPS_CONCURRENCY`
  environment variable is the only lever, and libvips reads it at initialisation — so it must
  be set *before* `import pyvips`. This is why `image_tiler.py` sets it at module scope and
  imports pyvips lazily inside the function.
- **A corrupt image raises `pyvips.error.Error` and creates no output directory.** So the
  failure path needs no cleanup of a partial pyramid from libvips itself — but it does still
  need to remove a stale pyramid from an earlier successful run before rebuilding.

## File structure

**Create:**
- `migrations/032_media_tiles.sql` — `media.width`, `height`, `tile_status`, `tile_levels`.
- `bot/core/image_tiler.py` — pure tiling. Constants, `probe_dimensions`, `needs_tiling`,
  `tiles_relpath`, `tile_relpath`, `build_pyramid`.
- `bot/core/media_tiling.py` — async orchestration: executor hand-off, row status
  transitions, stale-pyramid removal.
- `tests/test_image_tiler.py` — the pure tiler.
- `tests/test_media_tiling.py` — orchestration and status transitions.
- `tests/test_media_tile_route.py` — the serving route.

**Modify:**
- `Pipfile` — add `pyvips`.
- `bot/store/media_data.py` — four new fields.
- `bot/store/mysqldb.py:943-970` — `MySQLMediaDb.create` takes dimensions; add
  `update_tiles`.
- `bot/web/api.py:239-278` — `upload_media` records dimensions and requests tiling.
- `bot/web/api.py:196-236` — add the tile route directly after `media_bytes`.

---

## Task 1: Add the pyvips dependency

**Files:**
- Modify: `Pipfile`

- [ ] **Step 1: Add the package**

In `Pipfile`, under `[packages]`, after `python-slugify = "*"`:

```toml
pyvips = {extras = ["binary"], version = "*"}
```

The `binary` extra is load-bearing: it pulls a wheel with libvips bundled, so no `apt`
package and no root is needed on the Plesk host. Without it, pyvips expects a system libvips
and fails at import.

- [ ] **Step 2: Install and lock**

Run: `timeout 600 pipenv install > /tmp/pipenv-install.log 2>&1`
Expected: exit 0. `Pipfile.lock` gains `pyvips`.

Note: your working tree may already carry an unrelated transitive `pyasn1` 0.6.3→0.6.4 bump
in `Pipfile.lock`. That is a pre-existing user change; leave it, it rides along with this
lock update.

- [ ] **Step 3: Verify the bundled library loads**

Run:
```bash
pipenv run python -c "import pyvips; print(pyvips.__version__, pyvips.base.version(0), pyvips.base.version(1))"
```
Expected: a version plus `8 18` (or newer). If this raises `OSError: cannot load library`,
the `binary` extra did not take — do not proceed.

- [ ] **Step 4: Check the type checker tolerates an untyped native module**

Run: `timeout 300 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1`
Expected: 0 errors. If it reports `reportMissingTypeStubs` for pyvips, add to `pyproject.toml`
or the existing basedpyright config:

```toml
[[tool.basedpyright.ignore]]
# pyvips ships no stubs; it is a thin ctypes binding over libvips.
```

If the project's config is elsewhere, follow its existing pattern for untyped third-party
modules rather than inventing a new one.

- [ ] **Step 5: Commit**

```bash
git add Pipfile Pipfile.lock
git commit -m "build: add pyvips for canvas image tiling"
```

---

## Task 2: Migration for the tile columns

Per the project's **migration → deploy → code** rule this ships before any code reads the
columns: the generic `commit()` in `mysqldb.py` writes every dataclass field by name, so
adding fields to `MediaData` before the columns exist breaks existing writes.

**Files:**
- Create: `migrations/032_media_tiles.sql`

- [ ] **Step 1: Confirm 032 is free**

Run: `ls migrations/ | tail -4`
Expected: `030_media_and_sourceless_evidence.sql`, `031_hunt_level_sheets.sql` present, no
`032_`. Also check the other worktrees, because parallel branches can both claim a number and
git merges them cleanly without noticing:

```bash
for w in .claude/worktrees/*/; do ls "$w/migrations/" 2>/dev/null | tail -2; done
```

- [ ] **Step 2: Write the migration**

```sql
-- 032_media_tiles.sql
-- Pixel dimensions and tile-pyramid state for stored images.
--
-- This reverses the original canvas design's "no pixel dimensions are stored".
-- That was justified by region coordinates being normalised 0-1 -- still true --
-- but a tile pyramid needs the source dimensions to know how many zoom levels
-- exist, and the browser cannot report the natural size of an image it is never
-- able to decode.

ALTER TABLE `media`
  ADD COLUMN `width`       INT(11)     NOT NULL DEFAULT 0 AFTER `size`,
  ADD COLUMN `height`      INT(11)     NOT NULL DEFAULT 0 AFTER `width`,
  -- none: small enough to serve whole, no pyramid wanted.
  -- pending: tiling queued or running. ready: pyramid on disk. failed: gave up.
  ADD COLUMN `tile_status` VARCHAR(16) NOT NULL DEFAULT 'none' AFTER `height`,
  -- Number of pyramid levels, 0 when there is no pyramid. The deepest level
  -- index is tile_levels - 1 and is native resolution.
  ADD COLUMN `tile_levels` INT(11)     NOT NULL DEFAULT 0 AFTER `tile_status`;
```

Existing rows default to `'none'`/`0`, which is correct: Plan A's evidence screenshots have
no pyramid and need none.

- [ ] **Step 3: Apply it**

Run: `timeout 120 pipenv run python migrate.py > /tmp/migrate.log 2>&1`
Expected: exit 0, log names `032_media_tiles.sql`.

- [ ] **Step 4: Verify the columns landed**

Run:
```bash
pipenv run python -c "
from bot.store import MediaJsonDb
c = MediaJsonDb.mydb.dict_cursor()
c.execute('SHOW COLUMNS FROM media')
print([r['Field'] for r in c.fetchall()])
"
```
Expected: the list includes `width`, `height`, `tile_status`, `tile_levels`.

- [ ] **Step 5: Commit**

```bash
git add migrations/032_media_tiles.sql
git commit -m "migrate: pixel dimensions and tile state on media"
```

---

## Task 3: Carry the new columns on MediaData and the DAO

**Files:**
- Modify: `bot/store/media_data.py`
- Modify: `bot/store/mysqldb.py:943-980`
- Test: `tests/test_media_tiling.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_media_tiling.py`:

```python
"""MediaData / MySQLMediaDb tile-state carrying, and the tiling orchestrator."""
from bot.store.media_data import MediaData


def test_media_data_defaults_to_no_pyramid():
    m = MediaData()
    assert m.width == 0
    assert m.height == 0
    assert m.tile_status == "none"
    assert m.tile_levels == 0


def test_media_data_imports_tile_columns_from_a_row():
    m = MediaData.import_dict({
        "id": 7, "hunt_id": 3, "path": "uploads/3/x.png", "filename": "x.png",
        "content_type": "image/png", "size": 10, "origin": "upload",
        "width": 14400, "height": 14400, "tile_status": "ready", "tile_levels": 6,
    })
    assert (m.width, m.height) == (14400, 14400)
    assert m.tile_status == "ready"
    assert m.tile_levels == 6
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_tiling.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'MediaData' object has no attribute 'width'`.

- [ ] **Step 3: Add the fields**

In `bot/store/media_data.py`, after `origin: str = "upload"`:

```python
    width: int = 0
    height: int = 0
    # none: no pyramid wanted. pending: queued or running. ready: on disk.
    # failed: tiling gave up; the caller falls back to the whole image.
    tile_status: str = "none"
    tile_levels: int = 0
```

`import_dict` iterates `m.__dict__`, so it picks these up with no further change.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_tiling.py -q > /tmp/test.log 2>&1`
Expected: 2 passed.

- [ ] **Step 5: Widen `create` and add `update_tiles`**

In `bot/store/mysqldb.py`, replace `MySQLMediaDb.create` (currently lines 943-970) with:

```python
    def create(self, hunt_id: int, path: str, filename: str,
               content_type: str, size: int, origin: str,
               width: int = 0, height: int = 0,
               tile_status: str = "none") -> 'MediaData':
        """Insert one media row and return it.

        Dimensions are recorded for every image, tiled or not: they cost one
        cheap header read and the frontend needs them to size a viewport before
        any bytes arrive.
        """
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO media"
                " (hunt_id, path, filename, content_type, size, origin, created_at,"
                "  width, height, tile_status, tile_levels)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)",
                (hunt_id, path, filename, content_type, size, origin, now,
                 width, height, tile_status),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        self.mydb.commit()
        m = MediaData()
        m.id = new_id or 0
        m.hunt_id = hunt_id
        m.path = path
        m.filename = filename
        m.content_type = content_type
        m.size = size
        m.origin = origin
        m.width = width
        m.height = height
        m.tile_status = tile_status
        m.tile_levels = 0
        # Match import_dict's tzinfo-reattachment so a freshly-created row and one
        # read back from the DB serialize identically (same as EvidenceData).
        m.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return m

    def update_tiles(self, media_id: int, tile_status: str,
                     tile_levels: int = 0) -> None:
        """Move one row's pyramid state on. Owned by media_tiling, which is the
        only writer, so the pyramid on disk and the row cannot drift apart."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "UPDATE media SET tile_status = %s, tile_levels = %s WHERE id = %s",
                (tile_status, tile_levels, media_id),
            )
        finally:
            cursor.close()
        self.mydb.commit()
```

- [ ] **Step 6: Commit**

```bash
git add bot/store/media_data.py bot/store/mysqldb.py tests/test_media_tiling.py
git commit -m "feat(store): media carries pixel dimensions and tile state"
```

---

## Task 4: The pure tiler

**Files:**
- Create: `bot/core/image_tiler.py`
- Test: `tests/test_image_tiler.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_image_tiler.py`:

```python
"""The pure tiler: a file on disk in, a pyramid and dimensions out.

No DAOs, no Quart, no mocks — pyvips generates the fixtures too, so these tests
exercise the real encoder rather than a stand-in for it.
"""
import pytest

from bot.core import image_tiler


def _write_image(path, width, height):
    """A gradient, not a flat fill: dzsave omits blank tiles, and a flat image
    would collapse the pyramid to almost nothing and hide a level-count bug."""
    import pyvips
    im = pyvips.Image.xyz(width, height)
    (im[0] + im[1]).cast("uchar").write_to_file(str(path))


def test_probe_dimensions_reads_the_header_without_decoding(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 300, 200)
    assert image_tiler.probe_dimensions(src) == (300, 200)


def test_needs_tiling_is_false_for_a_screenshot():
    assert image_tiler.needs_tiling(1920, 1080) is False


def test_needs_tiling_is_true_for_the_motivating_image():
    assert image_tiler.needs_tiling(14400, 14400) is True


def test_needs_tiling_boundary_is_the_pixel_count_not_a_side():
    # A long thin panorama under the ceiling stays untiled; the rule is area.
    assert image_tiler.needs_tiling(20000, 100) is False


def test_tiles_relpath_is_keyed_by_media_id():
    assert image_tiler.tiles_relpath(hunt_id=3, media_id=42) == "tiles/3/42"


def test_tile_relpath_uses_google_z_y_x_order():
    # Google layout is z/y/x -- row before column. Getting this backwards
    # transposes the whole image, so it is asserted with y != x.
    assert image_tiler.tile_relpath(3, 42, z=5, y=9, x=2) == "tiles/3/42/5/9/2.webp"


def test_build_pyramid_returns_dimensions_and_level_count(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 1024, 1024)
    dest = tmp_path / "tiles"
    width, height, levels = image_tiler.build_pyramid(src, dest)
    assert (width, height) == (1024, 1024)
    # 1024 at tile_size 512 is 2x2 tiles; depth=onetile adds levels until one
    # tile covers the image, so: level 0 (1 tile) and level 1 (2x2).
    assert levels == 2


def test_build_pyramid_writes_a_native_level_tile_at_the_expected_path(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 1024, 1024)
    dest = tmp_path / "tiles"
    _, _, levels = image_tiler.build_pyramid(src, dest)
    deepest = levels - 1
    assert (dest / str(deepest) / "1" / "1.webp").is_file()
    assert (dest / "0" / "0" / "0.webp").is_file()


def test_build_pyramid_writes_a_blank_tile_for_the_route_to_fall_back_on(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 1024, 1024)
    dest = tmp_path / "tiles"
    image_tiler.build_pyramid(src, dest)
    assert (dest / image_tiler.BLANK_TILE_NAME).is_file()


def test_build_pyramid_replaces_a_stale_pyramid(tmp_path):
    """Rebuilding must not leave tiles from a previous, larger image behind."""
    src = tmp_path / "src.png"
    _write_image(src, 1024, 1024)
    dest = tmp_path / "tiles"
    image_tiler.build_pyramid(src, dest)
    stale = dest / "1" / "1" / "1.webp"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"stale")
    _write_image(src, 300, 300)
    image_tiler.build_pyramid(src, dest)
    assert not stale.is_file()


def test_build_pyramid_raises_on_a_corrupt_image_and_leaves_no_pyramid(tmp_path):
    src = tmp_path / "bad.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage" * 10)
    dest = tmp_path / "tiles"
    with pytest.raises(Exception):
        image_tiler.build_pyramid(src, dest)
    assert not dest.exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_image_tiler.py -q > /tmp/test.log 2>&1`
Expected: collection error — `ModuleNotFoundError: No module named 'bot.core.image_tiler'`.

- [ ] **Step 3: Write the tiler**

Create `bot/core/image_tiler.py`:

```python
"""Tile pyramid generation for images too large for a browser to decode.

Pure by design: a source path and a destination directory in, dimensions and a
level count out. No DAOs, no Quart, no discord.py — so it unit-tests against
nothing but a file on disk, and the async orchestration lives next door in
media_tiling.py.

Every constant here was measured on the image this feature exists for
(14400x14400, 207 MP); see the "Tiling" section of
docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md.
"""
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Set BEFORE pyvips is imported anywhere: libvips reads VIPS_CONCURRENCY at
# initialisation, and pyvips 3.1.1 exposes no concurrency_set() to change it
# afterwards. This module is the only importer of pyvips, and it imports it
# lazily inside build_pyramid, so module-scope is early enough.
#
# Measured on the 207 MP image: 13.6 s/224 MB at 1 thread, 8.1 s/340 MB at 2,
# 6.7 s/563 MB at 4, 6.9 s/982 MB unbounded on 12 cores. Past two threads it
# buys about a second for triple the peak memory, which is the wrong trade on a
# small VPS. setdefault, not assignment, so an operator can still override it.
os.environ.setdefault("VIPS_CONCURRENCY", "2")

TILE_SIZE = 512

# Lossless, not lossy. On flat white line art lossless is both smaller (9.6 MB
# against 11 MB at Q82 for the motivating image) and exact — and this content is
# small black lettering that has to stay readable at native zoom, which is
# precisely where lossy compression rings.
TILE_SUFFIX = ".webp[lossless=true]"
TILE_EXT = ".webp"

# dzsave omits a tile that is entirely blank and writes this alongside the
# pyramid for viewers to fall back on. A mostly-white puzzle sheet has many.
BLANK_TILE_NAME = "blank.png"

# Above this many pixels an image gets a pyramid. 16 MP is about where mobile
# Safari stops being willing to decode an image at all, and a 4000x4000 sheet is
# already awkward on a laptop. Below it, a pyramid would be hundreds of files
# serving no purpose — a screenshot is fine as one <img>.
MAX_DIRECT_PIXELS = 16_000_000


def needs_tiling(width: int, height: int) -> bool:
    """Whether an image of this size should get a pyramid.

    Keyed on area rather than on either side: a 20000x100 panorama decodes
    fine, and it is total pixels that exhaust a decoder.
    """
    return width * height > MAX_DIRECT_PIXELS


def tiles_relpath(hunt_id: int, media_id: int) -> str:
    """Directory, relative to media_root(), holding one row's pyramid.

    Keyed by media id, not by the upload's filename: regeneration is then
    idempotent (remove the directory and rebuild) and deleting a media row
    deletes exactly one directory. A uuid-keyed path would leave orphans that
    nothing could ever sweep with confidence.
    """
    return f"tiles/{hunt_id}/{media_id}"


def tile_relpath(hunt_id: int, media_id: int, z: int, y: int, x: int) -> str:
    """One tile's path, relative to media_root().

    z/y/x — row before column — because that is libvips' "google" layout. The
    order is asserted in the tests with y != x, since transposing it would flip
    the whole image about its diagonal and look almost plausible.
    """
    return f"{tiles_relpath(hunt_id, media_id)}/{z}/{y}/{x}{TILE_EXT}"


def probe_dimensions(src: Path) -> tuple[int, int]:
    """Read (width, height) from the header without decoding the pixels."""
    import pyvips
    im = pyvips.Image.new_from_file(str(src), access="sequential")
    return im.width, im.height


def build_pyramid(src: Path, dest_dir: Path) -> tuple[int, int, int]:
    """Write a tile pyramid for `src` into `dest_dir`.

    Returns (width, height, levels). The deepest level index is levels - 1 and
    is native resolution.

    Synchronous and CPU-bound — eight seconds on the motivating image — so
    callers MUST run it off the event loop. media_tiling.tile_media is the
    supported way in.

    Raises on a corrupt or unreadable source. libvips creates no output
    directory when the load fails, so a failure leaves nothing behind; a stale
    pyramid from an earlier successful run is removed first.
    """
    import pyvips

    # dzsave will not overwrite an existing tree, and a rebuild after the source
    # shrank would otherwise leave deeper levels from the previous image in
    # place, silently serving tiles that no longer belong to it.
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    im = pyvips.Image.new_from_file(str(src), access="sequential")
    width, height = im.width, im.height
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    im.dzsave(
        str(dest_dir),
        suffix=TILE_SUFFIX,
        tile_size=TILE_SIZE,
        overlap=0,
        # Keep halving until the whole image fits in a single tile, so level 0 is
        # always a complete thumbnail and the viewer has something to show
        # instantly at any zoom.
        depth="onetile",
        layout="google",
    )
    levels = len([d for d in dest_dir.iterdir() if d.is_dir() and d.name.isdigit()])
    logger.info("tiled %s (%dx%d) into %d levels", src.name, width, height, levels)
    return width, height, levels
```

- [ ] **Step 4: Run them to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_image_tiler.py -q > /tmp/test.log 2>&1`
Expected: 11 passed.

- [ ] **Step 5: Prove the level-count assertion is load-bearing**

Temporarily change `depth="onetile"` to `depth="one"` in `build_pyramid`.

Run: `timeout 180 pipenv run python -m pytest tests/test_image_tiler.py -q > /tmp/test.log 2>&1`
Expected: `test_build_pyramid_returns_dimensions_and_level_count` FAILS (levels becomes 1).
Restore `depth="onetile"` by editing the line back — **not** with `git checkout --`, which
would discard the rest of the file. Re-run and confirm 11 passed. Do not commit the mutation.

- [ ] **Step 6: Commit**

```bash
git add bot/core/image_tiler.py tests/test_image_tiler.py
git commit -m "feat(core): derive a WebP tile pyramid from a large image"
```

---

## Task 5: The async orchestrator

**Files:**
- Create: `bot/core/media_tiling.py`
- Test: `tests/test_media_tiling.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_media_tiling.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from bot.core import media_tiling


def _media(media_id=42, hunt_id=3, path="uploads/3/abc_src.png"):
    m = MediaData()
    m.id, m.hunt_id, m.path = media_id, hunt_id, path
    m.tile_status = "pending"
    return m


async def test_tile_media_marks_the_row_ready_with_its_level_count(tmp_path):
    db = MagicMock()
    with patch.object(media_tiling, "media_root", return_value=tmp_path), \
         patch.object(media_tiling, "build_pyramid",
                      return_value=(14400, 14400, 6)) as build:
        await media_tiling.tile_media(_media(), db)
    build.assert_called_once()
    src, dest = build.call_args[0]
    assert src == tmp_path / "uploads/3/abc_src.png"
    assert dest == tmp_path / "tiles/3/42"
    db.update_tiles.assert_called_once_with(42, "ready", 6)


async def test_tile_media_marks_the_row_failed_and_does_not_raise(tmp_path):
    """A bad upload must not take the request or the bot down with it."""
    db = MagicMock()
    with patch.object(media_tiling, "media_root", return_value=tmp_path), \
         patch.object(media_tiling, "build_pyramid",
                      side_effect=RuntimeError("libvips said no")):
        await media_tiling.tile_media(_media(), db)
    db.update_tiles.assert_called_once_with(42, "failed", 0)


async def test_tile_media_runs_the_tiler_off_the_event_loop(tmp_path):
    """Eight seconds of CPU on the loop would stall the gateway heartbeat, so
    this asserts the executor hand-off rather than trusting it."""
    db = MagicMock()
    seen = {}

    def record(src, dest):
        import threading
        seen["thread"] = threading.current_thread().name
        return (10, 10, 1)

    import threading
    main = threading.current_thread().name
    with patch.object(media_tiling, "media_root", return_value=tmp_path), \
         patch.object(media_tiling, "build_pyramid", side_effect=record):
        await media_tiling.tile_media(_media(), db)
    assert seen["thread"] != main
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_tiling.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.core.media_tiling'`.

- [ ] **Step 3: Write the orchestrator**

Create `bot/core/media_tiling.py`:

```python
"""Async orchestration around the pure tiler.

Owns two things the tiler deliberately does not: getting the CPU work off the
event loop, and moving a media row through pending -> ready | failed. Being the
single writer of tile_status is what keeps the row and the pyramid on disk from
drifting apart.
"""
import asyncio
import logging

from bot.core.image_tiler import build_pyramid, tiles_relpath
from bot.core.message_media import media_root
from bot.store.media_data import MediaData

logger = logging.getLogger(__name__)


async def tile_media(media: MediaData, db) -> None:
    """Build `media`'s pyramid, then record the outcome on its row.

    Never raises. Tiling is a derived convenience: if it fails the original
    image is still stored, still served by /api/media/<id>, and the frontend
    falls back to it — so an exception escaping here would take down a request
    (or a background task) over something recoverable.

    `db` is passed in rather than imported so the caller's DAO is the one used
    and the tests need no patching of module globals.
    """
    src = media_root() / media.path
    dest = media_root() / tiles_relpath(media.hunt_id, media.id)
    loop = asyncio.get_running_loop()
    try:
        # run_in_executor, because build_pyramid is seconds of synchronous CPU
        # and this coroutine runs on the same loop as the Discord gateway.
        width, height, levels = await loop.run_in_executor(
            None, build_pyramid, src, dest)
    except Exception:
        logger.exception("tiling failed for media %s (%s)", media.id, media.path)
        db.update_tiles(media.id, "failed", 0)
        return
    db.update_tiles(media.id, "ready", levels)
    logger.info("media %s tiled: %dx%d, %d levels", media.id, width, height, levels)
```

- [ ] **Step 4: Run them to verify they pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_tiling.py -q > /tmp/test.log 2>&1`
Expected: 5 passed.

- [ ] **Step 5: Prove the off-loop test is load-bearing**

Temporarily replace the `await loop.run_in_executor(None, build_pyramid, src, dest)` line
with a direct call `build_pyramid(src, dest)`.

Run: `timeout 120 pipenv run python -m pytest tests/test_media_tiling.py -q > /tmp/test.log 2>&1`
Expected: `test_tile_media_runs_the_tiler_off_the_event_loop` FAILS. Edit the line back and
confirm 5 passed. Do not commit the mutation.

- [ ] **Step 6: Commit**

```bash
git add bot/core/media_tiling.py tests/test_media_tiling.py
git commit -m "feat(core): tile uploaded images off the event loop"
```

---

## Task 6: Record dimensions and request tiling on upload

**Files:**
- Modify: `bot/web/api.py:239-278` (`upload_media`)

- [ ] **Step 1: Extend the upload route**

In `bot/web/api.py`, replace the body of `upload_media` from the `media = MediaJsonDb.create(`
call to its `return` (currently lines 274-278) with:

```python
    from bot.core.image_tiler import needs_tiling, probe_dimensions
    from bot.core.media_tiling import tile_media
    from bot.core.message_media import media_root
    # Dimensions for every image, tiled or not: one cheap header read, and the
    # frontend needs them to lay out a viewport before any bytes arrive.
    try:
        width, height = probe_dimensions(media_root() / saved["path"])
    except Exception:
        # save_image_bytes already sniffed the magic bytes, so this is a valid
        # image of an allowed type that libvips still cannot open. Store it
        # rather than lose the upload; it simply gets no pyramid.
        logger.exception("could not probe dimensions for %s", saved["path"])
        width, height = 0, 0
    tiled = needs_tiling(width, height)
    media = MediaJsonDb.create(
        hunt_id=hunt["id"], path=saved["path"], filename=saved["filename"],
        content_type=saved["content_type"], size=saved["size"], origin=saved["origin"],
        width=width, height=height,
        tile_status="pending" if tiled else "none",
    )
    if tiled:
        # AS BUILT: asyncio.create_task, not Quart's add_background_task, because
        # create_task is what the eight existing fire-and-forget call sites in
        # bot/web use (refresh_evidence_safe and friends) and tile_media follows
        # the same never-raises contract as those *_safe coroutines.
        #
        # The reference set IS a deviation from that pattern and is deliberate:
        # asyncio holds only a weak reference to a running task, and ~8 s of
        # tiling is long enough for a collection to land mid-run. Those refreshes
        # get away with it because they finish in milliseconds.
        task = asyncio.create_task(tile_media(media, MediaJsonDb))
        _tiling_tasks.add(task)
        task.add_done_callback(_tiling_tasks.discard)
    return jsonify({
        "id": media.id, "url": f"/api/media/{media.id}",
        "width": width, "height": height,
        "tile_status": media.tile_status,
    }), 201
```

- [ ] **Step 2: Add the task reference set**

`asyncio` and `logger` are already imported in `bot/web/api.py`; nothing new is needed from
quart. Add near `_WS_AUTH_REFRESH_SECONDS`:

```python
# Strong references to in-flight tiling tasks. asyncio only holds a weak
# reference to a running task, so one with no other referent can be collected
# mid-flight. The refresh_* create_task calls elsewhere get away with it because
# they finish in milliseconds; tiling a large sheet takes seconds, which is long
# enough for a collection to land in the middle of it.
_tiling_tasks: set = set()
```

- [ ] **Step 3: Verify nothing regressed**

Run: `timeout 180 pipenv run python -m pytest tests/ -q -k "media or evidence or upload" > /tmp/test.log 2>&1`
Expected: all pass. Plan A's upload tests still pass because every new `create` argument has
a default.

- [ ] **Step 4: Commit**

```bash
git add bot/web/api.py
git commit -m "feat(web): record image dimensions and tile large uploads"
```

---

## Task 7: Serve a tile

**Files:**
- Modify: `bot/web/api.py` — new route immediately after `media_bytes` (after line 236)
- Test: `tests/test_media_web.py` — a `TestMediaTiles` class

**AS BUILT:** the tests went into `tests/test_media_web.py` rather than a new
`tests/test_media_tile_route.py`. That file already owns the `web` fixture with every patch
this route needs (session, `_load_visible_hunt`, `MediaJsonDb`, `HuntJsonDb`, and `media_root`
at *both* lookup sites), and reusing it beats copying it. `TestMediaTiles` builds a real
pyramid with `image_tiler.build_pyramid` rather than a hand-made directory tree, so the route
and the tiler cannot drift apart on the z/y/x layout while both stay green.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_media_tile_route.py`:

```python
"""The tile route: authorisation, traversal safety, and blank-tile fallback.

Mirrors the existing media-route tests; see tests/conftest.py for the app and
login fixtures this reuses.
"""
import pytest

from bot.core import image_tiler


@pytest.mark.parametrize("z,y,x", [(-1, 0, 0), (0, -1, 0), (0, 0, -1)])
def test_negative_indices_never_reach_the_filesystem(z, y, x):
    """Belt and braces: <int:...> converters already reject a leading '-', so a
    negative index cannot arrive through routing at all. Asserted anyway
    because the guard is what makes the f-string path construction safe."""
    assert image_tiler.tile_relpath(1, 1, z, y, x).count("..") == 0


async def test_tile_requires_a_session(client):
    resp = await client.get("/api/media/1/tiles/0/0/0")
    assert resp.status_code in (302, 401, 403)


async def test_missing_tile_serves_the_blank_tile(logged_in_client, media_with_pyramid):
    """dzsave omits fully blank tiles, and a mostly-white puzzle sheet has many.
    A 404 there would make the viewer show broken images across empty paper."""
    media, tiles_dir = media_with_pyramid
    deepest = media.tile_levels - 1
    resp = await logged_in_client.get(
        f"/api/media/{media.id}/tiles/{deepest}/99/99")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("image/")


async def test_present_tile_is_served(logged_in_client, media_with_pyramid):
    media, tiles_dir = media_with_pyramid
    resp = await logged_in_client.get(f"/api/media/{media.id}/tiles/0/0/0")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "image/webp"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


async def test_tile_for_an_untiled_media_row_is_not_found(logged_in_client, media_no_pyramid):
    resp = await logged_in_client.get(f"/api/media/{media_no_pyramid.id}/tiles/0/0/0")
    assert resp.status_code == 404
```

The `client`, `logged_in_client`, `media_with_pyramid` and `media_no_pyramid` fixtures must
follow whatever the existing media-route tests use. Before writing them, run:

```bash
grep -rn "def client\|def logged_in_client\|media" tests/conftest.py | head -20
grep -rln "api/media" tests/
```

and reuse the established fixtures and factories rather than inventing new ones. If no
logged-in client fixture exists, copy the pattern from the existing `/api/media/<id>` test
file that `grep -rln "api/media" tests/` finds. `media_with_pyramid` should call
`image_tiler.build_pyramid` on a small generated image inside `tmp_path`, with `media_root`
patched to `tmp_path` — patched at `bot.web.api`'s lookup site, per the project's
patch-where-it-is-looked-up rule.

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_media_tile_route.py -q > /tmp/test.log 2>&1`
Expected: FAIL — 404 from an unregistered route (or fixture errors to resolve first).

- [ ] **Step 3: Write the route**

In `bot/web/api.py`, immediately after `media_bytes` ends (line 236):

```python
@api_bp.route("/media/<int:media_id>/tiles/<int:z>/<int:y>/<int:x>")
@login_required
async def media_tile(media_id, z, y, x):
    """Serve one pyramid tile. Authorised exactly as /api/media/<id> is.

    z/y/x is libvips' "google" layout order — row before column. The <int:>
    converters reject anything that is not a non-negative integer, which is what
    makes building the path by interpolation safe; the resolve()-against-root
    check below is the same defence in depth the other media routes carry.
    """
    from bot.store import HuntJsonDb, MediaJsonDb
    media = MediaJsonDb.get_by_id(media_id)
    if media is None or media.tile_status != "ready":
        return jsonify({"error": "not_found"}), 404
    hunt_row = HuntJsonDb.get_by_attr(id=media.hunt_id)
    if hunt_row is None:
        return jsonify({"error": "not_found"}), 404
    hunt, guild_or_status = await _load_visible_hunt(hunt_row.uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "not_found_or_forbidden"}), guild_or_status
    if z < 0 or z >= media.tile_levels:
        return jsonify({"error": "not_found"}), 404
    from bot.core.image_tiler import BLANK_TILE_NAME, tile_relpath, tiles_relpath
    from bot.core.message_media import media_root
    root = media_root().resolve()
    path = (media_root() / tile_relpath(media.hunt_id, media.id, z, y, x)).resolve()
    if root not in path.parents:            # defense in depth vs path traversal
        return jsonify({"error": "not_found"}), 404
    content_type = "image/webp"
    if not path.is_file():
        # dzsave omits a tile that is entirely blank. That is a legitimate hole
        # in the grid, not an error: a mostly-white sheet has many, and 404ing
        # would pepper the viewer with broken images over empty paper.
        path = (media_root() / tiles_relpath(media.hunt_id, media.id)
                / BLANK_TILE_NAME).resolve()
        if root not in path.parents or not path.is_file():
            return jsonify({"error": "not_found"}), 404
        content_type = "image/png"
    resp = await send_file(path, mimetype=content_type, as_attachment=False)
    # Same reasoning as media_bytes: the stored type is used verbatim and the
    # browser is never allowed to sniff the body into an executable context.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # Tiles are immutable for a given media id — a re-tile writes a new row —
    # so they can be cached hard. This is what makes panning feel instant.
    resp.headers["Cache-Control"] = "private, max-age=86400, immutable"
    return resp
```

- [ ] **Step 4: Run them to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_media_tile_route.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Prove the blank-tile fallback is load-bearing**

Temporarily delete the `if not path.is_file():` fallback block, replacing it with
`if not path.is_file(): return jsonify({"error": "not_found"}), 404`.

Run: `timeout 180 pipenv run python -m pytest tests/test_media_tile_route.py -q > /tmp/test.log 2>&1`
Expected: `test_missing_tile_serves_the_blank_tile` FAILS with 404. Restore the block by
editing it back. Do not commit the mutation.

- [ ] **Step 6: Commit**

```bash
git add bot/web/api.py tests/test_media_tile_route.py
git commit -m "feat(web): serve canvas tiles from an authenticated route"
```

---

## Task 8: End-to-end check on the real image, then the gates

**Files:** none — verification only. Record findings in `.ai/active/session-summary.md`; do
not create an empty commit.

- [ ] **Step 1: Tile the motivating image through the real code path**

```bash
pipenv run python -c "
import asyncio
from pathlib import Path
from unittest.mock import MagicMock
from bot.core import media_tiling
from bot.store.media_data import MediaData
m = MediaData(); m.id, m.hunt_id = 999, 999
m.path = 'probe/extravaganza-14400x14400.png'
db = MagicMock()
import shutil, os
root = media_tiling.media_root()
os.makedirs(root / 'probe', exist_ok=True)
shutil.copy('images/extravaganza-14400x14400.png', root / m.path)
asyncio.run(media_tiling.tile_media(m, db))
print(db.update_tiles.call_args)
"
```
Expected: `call(999, 'ready', 6)`. Then check the output:

```bash
find "$(pipenv run python -c 'from bot.core.message_media import media_root; print(media_root())')/tiles/999/999" -name '*.webp' | wc -l
```
Expected: 940. Clean up `probe/` and `tiles/999/` afterwards.

- [ ] **Step 2: Fetch a tile through the running app**

Start the app per `AGENTS.md`, log in, and request
`/api/media/<id>/tiles/5/9/9` for a real uploaded canvas image. Expected: a WebP image,
`Cache-Control` present. Then request `/api/media/<id>/tiles/5/99/99`. Expected: 200 and the
blank PNG, not a 404.

- [ ] **Step 3: Full suite**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: no new failures against the pre-change baseline. Capture the baseline first if you
do not have it.

- [ ] **Step 4: Lint and type gates**

Run: `pipenv run ruff check`
Expected: clean. Do **not** blind-fix F401 — `bot/store/__init__.py` re-exports are
load-bearing.

Run: `timeout 300 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1`
Expected: 0 errors.

- [ ] **Step 5: Update the handoff and mark B1 done**

Update `.ai/active/session-summary.md` `Status:` to name this task and its commit, and tick
B1 off in `.ai/active/current-spec.md`.

---

## Deliberately not in B1

- **Any UI.** The viewer, the overlay, pan/zoom and draw-to-create are B2.
- **Re-tiling an existing row.** `build_pyramid` is idempotent and removes a stale pyramid,
  but no route calls it a second time. Nothing needs it until a "regenerate" button exists.
- **Sweeping tile directories when a media row is deleted.** `media` rows cascade from
  `hunts`, and no delete path exists for a single media row yet. When B2 adds canvas
  deletion it must `rmtree(tiles_relpath(...))`; noted there, not built here.
- **A separate upload cap for canvases.** `web_attachment_max_bytes` defaults to 25 MiB and
  the motivating file is 16.5 MB, so it already fits. A larger sheet would not.
- **Progressive/partial upload.** The route reads the whole body into memory as Plan A does.
  At a 25 MiB cap that is acceptable; it is the existing behaviour, not a new risk.

## Self-review against the spec

Checked against the "Tiling", "Web API" and "Testing" sections of the design:

- pyvips[binary], dzsave, lossless WebP, 512 px, `depth=onetile`, `layout=google`,
  `VIPS_CONCURRENCY=2` — Task 1, Task 4.
- `tiles/{hunt_id}/{media_id}/{z}/{y}/{x}.webp`, keyed by media id, sibling of `uploads/`,
  no `media` rows for tiles — Task 4.
- Blank-tile fallback — Task 4 (constant), Task 7 (route).
- Off-loop tiling with `pending → ready | failed` — Task 5, Task 6.
- `media.width`/`height`/`tile_status`/`tile_levels` — Task 2, Task 3.
- Tile route authorised as `/api/media/<id>`, indices validated before path construction —
  Task 7.
- Spec test list: tiler level count and paths ✓, dimensions recorded ✓, failure flips to
  `failed` ✓, tile route auth ✓, blank fallback ✓, index validation ✓.

Gap accepted: the spec's "leaves no half-written pyramid" is satisfied by libvips creating no
output on load failure plus the stale-pyramid `rmtree`, rather than by a temp-dir-and-rename.
A mid-write crash (SIGKILL during dzsave) could still leave a partial tree with
`tile_status='pending'`; the row is then never `ready`, so the route 404s and the viewer falls
back to the whole image. Correct behaviour, reached by a different route than the spec
implies.
