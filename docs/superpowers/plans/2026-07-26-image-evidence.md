# Image Evidence (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an evidence item carry an image, free text, or both, and let an evidence item exist with no source puzzle at all.

**Architecture:** A new `media` table stores one row per uploaded image, with bytes on disk under the media root the Discord message bridge already uses. `evidence_items` gains a nullable `media_id` and its `source_puzzle_id` becomes nullable. Everything downstream that assumed a source puzzle — the contribution-table builder, the Evidence sheet tab, the web serialiser, the evidence panel — gains a null branch. A new `bot/core/media_store.py` owns validation and storage so no HTTP or Discord code touches the filesystem directly.

**Tech Stack:** Python 3 / discord.py 2.7.1, Quart, MySQL (raw SQL DAOs, no ORM), Google Sheets API, React + Mantine + react-query, pytest with `asyncio_mode = auto`.

---

## Spec

`docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md` is authoritative. This plan implements **Plan A only**. Plan B (the Canvas) is a separate plan.

**Deliberately deferred:**

- The `source_region_id` column on `evidence_items` ships with the `canvas_regions` table it points at, in Plan B.
- The spec's media endpoint lists three intake shapes: multipart upload, `{"url": ...}`, and `{"attachment_id": ...}`. **Plan A implements multipart only.** The Discord path is covered better by `!clue` with an attachment (Task 16), which stores the bytes directly, and fetching by URL matters for pasting in a hunt's big canvas image — a Plan B need — not for a screenshot you already have on disk.
- `source_sheet_id` and hunt-level working sheets are Plan C.

## Where this work happens

**Work directly on `staging` in the main checkout (`/home/russ/projects/puzzbot`). Do not create a worktree** — the user wants this branch live on the test site so it can be exercised against real Discord and real Sheets as it lands. `staging` is the integration branch (`main` is intentionally behind).

This overrides the repo's usual persistent-worktree convention (`AGENTS.md` → Worktrees) and any default your execution skill has toward isolating work. Commit each task straight to `staging`.

## Before you start

Read these first — they are house rules this plan assumes:

- `AGENTS.md` — the domain vocabulary, the test command, the **migration → deploy → code** rule.
- `docs/agents/python.md` — pytest and `unittest.mock` gotchas, especially *patch where the name is looked up*.

Run the suite once to confirm a green baseline:

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

Read `/tmp/test.log`. A timeout-killed run exits 124. Do not start until this is green.

## Key conventions you must follow

- **Patch DAOs at the service import site.** `bot/core/puzzle_service.py` does `from bot.store import EvidenceJsonDb`, so tests patch `bot.core.puzzle_service.EvidenceJsonDb`, **never** `bot.store.EvidenceJsonDb`. Patching the source module leaves the service's own reference untouched and the mock silently never engages.
- **Factories, not fixtures.** `tests/conftest.py` exports plain functions — `make_hunt()`, `make_puzzle()`, `make_ctx()`, `make_cog()`. Import and call them.
- **`asyncio_mode = auto`.** `async def test_*` needs no decorator.
- **Anything awaited is an `AsyncMock`**; sync attributes stay `MagicMock`.
- Commit after every task.

## File structure

| File | Responsibility |
| --- | --- |
| `migrations/030_media_and_sourceless_evidence.sql` | new | The `media` table; `evidence_items.media_id`; nullable `source_puzzle_id` |
| `bot/store/media_data.py` | new | `MediaData` dataclass |
| `bot/store/mysqldb.py` | modify | `MySQLMediaDb`; `MySQLEvidenceDb.log` widened |
| `bot/store/__init__.py` | modify | Re-export `MediaData`, instantiate `MediaJsonDb` |
| `bot/store/evidence_data.py` | modify | `media_id` field; `source_puzzle_id` nullable |
| `bot/core/media_store.py` | new | Image sniffing, size cap, path allocation, save |
| `bot/core/contribution.py` | modify | Sourceless evidence rows |
| `bot/core/puzzle_service.py` | modify | `log_clues` without a puzzle; informs-link guards; roll-up and Evidence-tab rendering |
| `bot/web/data.py` | modify | Null-safe evidence serialisation + media fields |
| `bot/web/api.py` | modify | `GET /api/media/<id>`, `POST /api/hunts/<uid>/media`, widened `POST /api/evidence` |
| `bot/cogs/puzzles.py` | modify | `!clue` / `!clues` accept attachments and run outside a puzzle channel |
| `frontend/src/types.ts` | modify | `EvidenceItem` media fields |
| `frontend/src/api.ts` | modify | Upload hook; widened log-clue hook |
| `frontend/src/components/EvidencePanel.tsx` | modify | Thumbnail column, em-dash source, image picker |
| `docs/commands.md` | modify | Updated `!clue` / `!clues` rows |

---

## Task 1: Migration — media table and sourceless evidence

**Files:**
- Create: `migrations/030_media_and_sourceless_evidence.sql`

Ships **before** any code that reads or writes the new columns. The generic `commit()` in `mysqldb.py` serialises every dataclass field by name, so adding a field to `EvidenceData` before its column exists breaks every existing evidence write.

- [ ] **Step 1: Check the migration number is free**

Run: `ls migrations | tail -3`

Expected: highest is `029_tag_group_target_puzzle_index.sql`. If a parallel worktree has already claimed `030`, use the next free number — git merges colliding numbers cleanly without noticing, so this must be checked by eye.

- [ ] **Step 2: Write the migration**

```sql
-- 030_media_and_sourceless_evidence.sql
-- Plan A (image evidence). Two changes:
--
-- 1. A `media` table: one row per stored image, bytes on disk under
--    config.web_archive_media_dir (the same root the Discord message bridge
--    writes to). No pixel dimensions are stored -- the browser knows the
--    natural size and coordinates are normalised, so nothing server-side needs
--    them, which is what keeps Pillow out of the dependency list.
--
-- 2. evidence_items can now carry an image (media_id) and can have NO source
--    puzzle at all (source_puzzle_id nullable). A fact can belong to the hunt
--    rather than to any one puzzle.
--
-- source_puzzle_id carries the EvidenceSourcePuzzleID FK from 022 with
-- ON DELETE CASCADE. MySQL will not reliably MODIFY the nullability of a
-- column under an active FK, so it is dropped and re-added around the change.
-- The CASCADE semantics are deliberately UNCHANGED: deleting a puzzle still
-- deletes evidence sourced from it. (NULL rows bypass the FK check entirely,
-- which is what makes sourceless evidence legal.)

CREATE TABLE IF NOT EXISTS `media` (
  `id`           INT(11)      NOT NULL AUTO_INCREMENT,
  `hunt_id`      INT(11)      NOT NULL,
  `path`         VARCHAR(512) NOT NULL,
  `filename`     VARCHAR(255) NOT NULL DEFAULT '',
  `content_type` VARCHAR(100) NOT NULL DEFAULT '',
  `size`         INT(11)      NOT NULL DEFAULT 0,
  `origin`       VARCHAR(16)  NOT NULL DEFAULT 'upload',
  `created_at`   DATETIME     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_media_hunt` (`hunt_id`),
  CONSTRAINT `MediaHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

ALTER TABLE `evidence_items`
  DROP FOREIGN KEY `EvidenceSourcePuzzleID`;

ALTER TABLE `evidence_items`
  MODIFY COLUMN `source_puzzle_id` INT(11) DEFAULT NULL;

ALTER TABLE `evidence_items`
  ADD CONSTRAINT `EvidenceSourcePuzzleID` FOREIGN KEY (`source_puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- Deleting an image must not delete the note written about it.
ALTER TABLE `evidence_items`
  ADD COLUMN `media_id` INT(11) DEFAULT NULL,
  ADD CONSTRAINT `EvidenceMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;
```

- [ ] **Step 3: Apply it**

Run: `pipenv run python migrate.py migrate`
Expected: reports `030_media_and_sourceless_evidence.sql` applied, exit 0.

- [ ] **Step 4: Verify the schema changed**

Run: `pipenv run python migrate.py status`
Expected: `030` listed as applied, nothing pending.

- [ ] **Step 5: Commit**

```bash
git add migrations/030_media_and_sourceless_evidence.sql
git commit -m "feat(db): add media table; allow evidence with an image and no source puzzle"
```

---

## Task 2: `MediaData` dataclass

**Files:**
- Create: `bot/store/media_data.py`
- Test: `tests/test_media_store.py`

Mirrors `bot/store/evidence_data.py` exactly, including the tzinfo reattachment — timestamps are stored naive UTC everywhere in this codebase and every `import_dict` reattaches `timezone.utc` so a freshly-created object and one read back serialise identically.

- [ ] **Step 1: Write the failing test**

```python
"""Plan A: media storage — dataclass, validation, and on-disk save."""
import datetime


class TestMediaData:
    def test_default_fields(self):
        from bot.store.media_data import MediaData
        m = MediaData()
        assert m.id == 0
        assert m.hunt_id == 0
        assert m.path == ""
        assert m.filename == ""
        assert m.content_type == ""
        assert m.size == 0
        assert m.origin == "upload"
        assert m.created_at is None

    def test_import_dict_reads_all_fields(self):
        from bot.store.media_data import MediaData
        row = {
            "id": 3,
            "hunt_id": 10,
            "path": "uploads/10/abc_crop.png",
            "filename": "crop.png",
            "content_type": "image/png",
            "size": 2048,
            "origin": "upload",
            "created_at": datetime.datetime(2026, 7, 26, 9, 0, 0),
        }
        m = MediaData.import_dict(row)
        assert m.id == 3
        assert m.hunt_id == 10
        assert m.path == "uploads/10/abc_crop.png"
        assert m.filename == "crop.png"
        assert m.content_type == "image/png"
        assert m.size == 2048
        assert m.origin == "upload"

    def test_import_dict_reattaches_utc_tzinfo(self):
        """created_at is stored naive UTC like every other timestamp column;
        import_dict must reattach tzinfo or comparisons and serialisation drift
        from the rest of the app (same pattern as EvidenceData)."""
        from bot.store.media_data import MediaData
        m = MediaData.import_dict({
            "id": 1, "hunt_id": 10, "path": "p", "filename": "f",
            "content_type": "image/png", "size": 1, "origin": "upload",
            "created_at": datetime.datetime(2026, 7, 26, 9, 0, 0),
        })
        assert m.created_at.tzinfo == datetime.timezone.utc
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_store.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.store.media_data'`. Read `/tmp/test.log`.

- [ ] **Step 3: Write the implementation**

```python
from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class MediaData:
    id: int = 0
    hunt_id: int = 0
    path: str = ""
    filename: str = ""
    content_type: str = ""
    size: int = 0
    origin: str = "upload"
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        m = cls()
        for attr in m.__dict__:
            if attr in row:
                setattr(m, attr, row[attr])
        # created_at is stored naive UTC (like every other timestamp column);
        # reattach tzinfo so downstream serialization/comparisons match the rest
        # of the codebase's aware datetimes (see EvidenceData.import_dict).
        if m.created_at is not None:
            m.created_at = m.created_at.replace(tzinfo=datetime.timezone.utc)
        return m
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_store.py -q > /tmp/test.log 2>&1`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/store/media_data.py tests/test_media_store.py
git commit -m "feat(store): add MediaData dataclass"
```

---

## Task 3: Image validation in `media_store`

**Files:**
- Create: `bot/core/media_store.py`
- Test: `tests/test_media_store.py` (append)

The declared content type is attacker-controlled, so it is never trusted — the magic bytes decide. SVG is rejected outright: it is a scriptable document, and serving one from our own origin is stored XSS.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_media_store.py`:

```python
class TestSniffImageType:
    """The declared Content-Type is not trusted; magic bytes decide."""

    def test_png(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) == "image/png"

    def test_jpeg(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "image/jpeg"

    def test_gif87a_and_gif89a(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"GIF87a" + b"\x00" * 20) == "image/gif"
        assert sniff_image_type(b"GIF89a" + b"\x00" * 20) == "image/gif"

    def test_webp(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"

    def test_riff_that_is_not_webp_is_rejected(self):
        """A RIFF container holding WAVE audio is not an image."""
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None

    def test_svg_is_rejected(self):
        """SVG is a scriptable document. Serving one from our own origin is
        stored XSS, so it is not an allowed image type however it is declared."""
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"<svg xmlns='http://www.w3.org/2000/svg'>") is None

    def test_png_bytes_declared_as_something_else_still_sniff_as_png(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"\x89PNG\r\n\x1a\n") == "image/png"

    def test_empty_and_short_input(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"") is None
        assert sniff_image_type(b"\x89PN") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_store.py -q -k Sniff > /tmp/test.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.core.media_store'`.

- [ ] **Step 3: Write the implementation**

```python
"""Storage for images attached to evidence (and, later, canvases).

Kept free of Quart and discord.py so it unit-tests without a request context or
a client: callers hand over bytes and get back a saved relative path. Bytes live
under the same media root the Discord message bridge writes to
(config.web_archive_media_dir), so there is one directory to back up and one
serving guard to get right.
"""
import datetime
import logging
import uuid

from bot.core.message_media import media_root, safe_filename
from bot.utils import config

logger = logging.getLogger(__name__)

# Raster formats only. SVG is deliberately absent: it is a scriptable document,
# and serving one from our own origin would be stored XSS.
ALLOWED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def sniff_image_type(head: bytes):
    """Return the content type implied by the leading bytes, or None.

    The client-declared Content-Type is attacker-controlled and is never
    trusted; this is what decides whether the upload is stored at all.
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    # RIFF is a container; only the WEBP flavour is an image.
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def max_upload_bytes() -> int:
    return config.web_attachment_max_bytes


def upload_relpath(hunt_id: int, filename: str) -> str:
    """Relative path under media_root(); stored on the row and re-joined when
    serving. The uuid prefix means two people uploading `screenshot.png` never
    collide, and the stored path is never taken from user input directly."""
    return f"uploads/{hunt_id}/{uuid.uuid4().hex}_{safe_filename(filename)}"
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_store.py -q -k Sniff > /tmp/test.log 2>&1`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/core/media_store.py tests/test_media_store.py
git commit -m "feat(core): add media_store with image sniffing and upload paths"
```

---

## Task 4: Saving bytes to disk

**Files:**
- Modify: `bot/core/media_store.py`
- Test: `tests/test_media_store.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_media_store.py`:

```python
from unittest.mock import patch

import pytest


class TestSaveImageBytes:
    """save_image_bytes validates, then writes under the media root."""

    def test_rejects_bytes_that_are_not_an_allowed_image(self, tmp_path):
        from bot.core.media_store import save_image_bytes, MediaRejected
        with patch("bot.core.media_store.media_root", return_value=tmp_path):
            with pytest.raises(MediaRejected) as exc:
                save_image_bytes(hunt_id=10, filename="x.svg",
                                 raw=b"<svg xmlns='http://www.w3.org/2000/svg'>")
        assert "image" in str(exc.value).lower()

    def test_rejects_oversize(self, tmp_path):
        from bot.core.media_store import save_image_bytes, MediaRejected
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000
        with patch("bot.core.media_store.media_root", return_value=tmp_path), \
             patch("bot.core.media_store.max_upload_bytes", return_value=10):
            with pytest.raises(MediaRejected) as exc:
                save_image_bytes(hunt_id=10, filename="big.png", raw=raw)
        assert "large" in str(exc.value).lower()

    def test_writes_file_and_returns_metadata(self, tmp_path):
        from bot.core.media_store import save_image_bytes
        raw = b"\x89PNG\r\n\x1a\n" + b"payload"
        with patch("bot.core.media_store.media_root", return_value=tmp_path), \
             patch("bot.core.media_store.max_upload_bytes", return_value=1_000_000):
            saved = save_image_bytes(hunt_id=10, filename="crop.png", raw=raw)
        assert saved["content_type"] == "image/png"
        assert saved["size"] == len(raw)
        assert saved["filename"] == "crop.png"
        assert saved["path"].startswith("uploads/10/")
        assert (tmp_path / saved["path"]).read_bytes() == raw

    def test_filename_cannot_escape_the_media_root(self, tmp_path):
        """A traversal filename must not add path segments at all.

        Assert on the stored relative path, not merely on where the file landed.
        A `tmp_path in written.parents` check is VACUOUS here: the fixed
        `uploads/<hunt_id>/` prefix plus the `{uuid}_` concatenation absorbs a
        two-level `../..`, so that assertion passes even with no sanitisation
        whatsoever. The payload below is also deeper than the prefix for the
        same reason.
        """
        from bot.core.media_store import save_image_bytes
        raw = b"\x89PNG\r\n\x1a\n"
        with patch("bot.core.media_store.media_root", return_value=tmp_path), \
             patch("bot.core.media_store.max_upload_bytes", return_value=1_000_000):
            saved = save_image_bytes(hunt_id=10, filename="../../../../etc/passwd",
                                     raw=raw)
        assert ".." not in saved["path"]
        assert saved["path"].startswith("uploads/10/")
        assert saved["path"].count("/") == 2      # only uploads/ and the hunt id
        written = (tmp_path / saved["path"]).resolve()
        assert written.parent == (tmp_path / "uploads" / "10").resolve()
        assert written.read_bytes() == raw

    def test_content_type_is_sniffed_not_taken_from_the_caller(self, tmp_path):
        """A caller claiming image/png for JPEG bytes gets image/jpeg stored."""
        from bot.core.media_store import save_image_bytes
        raw = b"\xff\xd8\xff\xe0" + b"\x00" * 8
        with patch("bot.core.media_store.media_root", return_value=tmp_path), \
             patch("bot.core.media_store.max_upload_bytes", return_value=1_000_000):
            saved = save_image_bytes(hunt_id=10, filename="a.png", raw=raw)
        assert saved["content_type"] == "image/jpeg"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_store.py -q -k SaveImage > /tmp/test.log 2>&1`
Expected: FAIL — `ImportError: cannot import name 'save_image_bytes'`.

- [ ] **Step 3: Write the implementation**

Append to `bot/core/media_store.py`:

```python
class MediaRejected(Exception):
    """An upload failed validation. The message is safe to show a user."""


class SavedMedia(TypedDict):
    """What a validated, stored image needs for its `media` row.

    A TypedDict rather than a bare dict because two call sites (the web upload
    route and the Discord attachment path) index these keys by name, and a typo
    on either side would otherwise be caught by nothing — not the type checker,
    not the tests. Deliberately not `MediaData`: that carries `id` and `hunt_id`,
    which do not exist until the row is inserted.

    No `created_at`: the DAO stamps its own on insert, so generating one here
    would be a second source of truth that every caller then discards.
    """
    path: str
    filename: str
    content_type: str
    size: int
    origin: str


def save_image_bytes(hunt_id: int, filename: str, raw: bytes,
                     origin: str = "upload") -> SavedMedia:
    """Validate `raw` and write it under the media root.

    Returns the metadata a `media` row needs: path, filename, content_type,
    size, origin. Raises MediaRejected if the bytes are too large or are not an
    allowed image.

    There is deliberately no parameter for the client-declared content type.
    Accepting one and ignoring it would be dead API surface that every call site
    would keep faithfully passing; the magic bytes are the only input that
    decides anything here.
    """
    limit = max_upload_bytes()
    if len(raw) > limit:
        raise MediaRejected(f"Image too large (limit {limit} bytes).")
    content_type = sniff_image_type(raw[:16])
    if content_type is None or content_type not in ALLOWED_IMAGE_TYPES:
        raise MediaRejected(
            "Not a supported image. Allowed: PNG, JPEG, GIF, WebP."
        )
    relpath = upload_relpath(hunt_id, filename)
    dest = media_root() / relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(raw)
    except Exception:
        # Leave no partial file behind on a full disk or a permission error.
        # Mirrors download_attachment in message_media.py, which already solved
        # this; the exception still propagates, so no media row is ever created
        # pointing at a truncated image.
        dest.unlink(missing_ok=True)
        raise
    return {
        "path": relpath,
        "filename": safe_filename(filename),
        "content_type": content_type,
        "size": len(raw),
        "origin": origin,
    }
```

`datetime` is no longer needed in this module once `created_at` is gone — drop the import if nothing else uses it. `TypedDict` comes from `typing`.

Note the test asserts `saved["filename"] == "crop.png"` — `safe_filename("crop.png")` returns `"crop.png"` unchanged, and `safe_filename("../../etc/passwd")` collapses to `"etc_passwd"`, which is why the traversal test passes.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_store.py -q > /tmp/test.log 2>&1`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/core/media_store.py tests/test_media_store.py
git commit -m "feat(core): save validated image uploads under the media root"
```

---

## Task 5: `MySQLMediaDb` DAO

**Files:**
- Modify: `bot/store/mysqldb.py`
- Modify: `bot/store/__init__.py`
- Test: `tests/test_media_store.py` (append)

The DAO follows the hand-rolled cursor style of `MySQLEvidenceDb` at `bot/store/mysqldb.py:784` — raw SQL, `dict_cursor()`, `try/finally cursor.close()`, explicit `self.mydb.commit()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_media_store.py`:

```python
from unittest.mock import MagicMock


class TestMySQLMediaDb:
    def _db(self):
        from bot.store.mysqldb import MySQLMediaDb
        mydb = MagicMock()
        cursor = MagicMock()
        cursor.lastrowid = 42
        mydb.dict_cursor.return_value = cursor
        return MySQLMediaDb(mydb=mydb), mydb, cursor

    def test_create_inserts_and_returns_media_with_new_id(self):
        db, mydb, cursor = self._db()
        m = db.create(hunt_id=10, path="uploads/10/a_crop.png", filename="crop.png",
                      content_type="image/png", size=99, origin="upload")
        assert m.id == 42
        assert m.hunt_id == 10
        assert m.path == "uploads/10/a_crop.png"
        assert m.content_type == "image/png"
        assert m.size == 99
        assert cursor.execute.called
        mydb.commit.assert_called_once()

    def test_create_sends_parameters_in_column_order(self):
        """Every other assertion in this class reads fields off the RETURNED
        MediaData, and those are built from `create`'s own local parameters —
        never read back from the cursor. So a SQL tuple that transposes two
        adjacent same-typed columns (`path` and `filename` are both strings)
        would corrupt every written row while leaving all of them green.
        Assert on what was actually sent to execute.
        """
        db, _, cursor = self._db()
        db.create(hunt_id=10, path="uploads/10/a_crop.png", filename="crop.png",
                  content_type="image/png", size=99, origin="upload")
        sql, params = cursor.execute.call_args[0]
        assert params[:6] == (10, "uploads/10/a_crop.png", "crop.png",
                              "image/png", 99, "upload")
        columns = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        assert columns == ["hunt_id", "path", "filename", "content_type",
                           "size", "origin", "created_at"]

    def test_create_reattaches_utc_tzinfo(self):
        """A freshly-created row must serialize like one read back from the DB."""
        import datetime
        db, _, _ = self._db()
        m = db.create(hunt_id=10, path="p", filename="f",
                      content_type="image/png", size=1, origin="upload")
        assert m.created_at.tzinfo == datetime.timezone.utc

    def test_get_by_id_returns_none_when_missing(self):
        db, _, cursor = self._db()
        cursor.fetchone.return_value = None
        assert db.get_by_id(1) is None

    def test_get_by_id_returns_media(self):
        db, _, cursor = self._db()
        cursor.fetchone.return_value = {
            "id": 5, "hunt_id": 10, "path": "p", "filename": "f",
            "content_type": "image/png", "size": 1, "origin": "upload",
            "created_at": None,
        }
        m = db.get_by_id(5)
        assert m.id == 5
        assert m.hunt_id == 10
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_store.py -q -k MySQLMediaDb > /tmp/test.log 2>&1`
Expected: FAIL — `ImportError: cannot import name 'MySQLMediaDb'`.

- [ ] **Step 3: Add the DAO**

Add to `bot/store/mysqldb.py`, immediately after the `MySQLEvidenceDb` class (which ends just before `class MySQLGuildSettingsDb`):

```python
class MySQLMediaDb(_MySQLBaseDb):
    TABLE_NAME = 'media'

    def create(self, hunt_id: int, path: str, filename: str,
               content_type: str, size: int, origin: str) -> 'MediaData':
        """Insert one media row and return it."""
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO media"
                " (hunt_id, path, filename, content_type, size, origin, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (hunt_id, path, filename, content_type, size, origin, now),
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
        # Match import_dict's tzinfo-reattachment so a freshly-created row and one
        # read back from the DB serialize identically (same as EvidenceData).
        m.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return m

    def get_by_id(self, media_id: int):
        """Return one MediaData by id, or None if missing."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("SELECT * FROM media WHERE id = %s", (media_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return MediaData.import_dict(row) if row else None
```

Add the import near the other store-data imports at the top of `bot/store/mysqldb.py` (beside `from .evidence_data import EvidenceData` on line 20):

```python
from .media_data import MediaData
```

- [ ] **Step 4: Wire up the re-exports**

In `bot/store/__init__.py`, beside `from .evidence_data import EvidenceData` (line 32):

```python
from .media_data import MediaData
```

Add `MySQLMediaDb` to the existing `from .mysqldb import ...` list on line 33, and beside `EvidenceJsonDb = MySQLEvidenceDb(mydb=mydb)` (line 144):

```python
    MediaJsonDb = MySQLMediaDb(mydb=mydb)
```

Match the surrounding indentation exactly — those assignments sit inside a function.

- [ ] **Step 5: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_store.py -q > /tmp/test.log 2>&1`
Expected: 20 passed.

- [ ] **Step 6: Check nothing else broke**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass. `bot/store/__init__.py` opens MySQL at import, so a mistake here breaks the whole suite at collection with exit 4.

- [ ] **Step 7: Commit**

```bash
git add bot/store/mysqldb.py bot/store/__init__.py tests/test_media_store.py
git commit -m "feat(store): add MySQLMediaDb DAO and MediaJsonDb"
```

---

## Task 6: `EvidenceData` carries a media id and an optional source

**Files:**
- Modify: `bot/store/evidence_data.py`
- Test: `tests/test_evidence_service.py` (append to `TestEvidenceData`)

- [ ] **Step 1: Write the failing test**

Append to the `TestEvidenceData` class in `tests/test_evidence_service.py`:

```python
    def test_media_id_defaults_to_none(self):
        from bot.store.evidence_data import EvidenceData
        assert EvidenceData().media_id is None

    def test_source_puzzle_id_defaults_to_none(self):
        """Evidence can belong to the hunt rather than to any one puzzle, so the
        default source is 'none', not the sentinel 0."""
        from bot.store.evidence_data import EvidenceData
        assert EvidenceData().source_puzzle_id is None

    def test_import_dict_reads_null_source_and_media(self):
        from bot.store.evidence_data import EvidenceData
        e = EvidenceData.import_dict({
            "id": 1, "hunt_id": 10,
            "source_puzzle_id": None,
            "target_puzzle_id": 4,
            "note": "found on the poster",
            "media_id": 7,
            "created_at": None,
        })
        assert e.source_puzzle_id is None
        assert e.media_id == 7
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q -k EvidenceData > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'EvidenceData' object has no attribute 'media_id'`, and the source-default test fails asserting `0 is None`.

- [ ] **Step 3: Update the dataclass**

In `bot/store/evidence_data.py`, change the two fields and add one:

```python
@dataclass
class EvidenceData:
    id: int = 0
    hunt_id: int = 0
    # None when the evidence belongs to the hunt rather than to a source puzzle
    # (e.g. an observation nobody has pinned to a specific puzzle yet).
    source_puzzle_id: Optional[int] = None
    target_puzzle_id: Optional[int] = None
    note: str = ""
    media_id: Optional[int] = None
    created_at: Optional[datetime.datetime] = None
```

- [ ] **Step 4: Fix the existing default-fields test**

`tests/test_evidence_service.py::TestEvidenceData::test_default_fields` asserts `e.source_puzzle_id == 0`. That expectation is now wrong. Change that line to:

```python
        assert e.source_puzzle_id is None
```

- [ ] **Step 5: Run the evidence tests**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 6: Run the whole suite**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass. The generic `commit()` writes every `__dict__` field by name, and the `media_id` column now exists (Task 1), so evidence writes still work.

- [ ] **Step 7: Commit**

```bash
git add bot/store/evidence_data.py tests/test_evidence_service.py
git commit -m "feat(store): EvidenceData carries media_id and an optional source puzzle"
```

---

## Task 7: `EvidenceDb.log` accepts a null source and a media id

**Files:**
- Modify: `bot/store/mysqldb.py:787` (`MySQLEvidenceDb.log`)
- Test: `tests/test_evidence_service.py` (new class)

- [x] **Step 1: Write the failing test**

Append to `tests/test_evidence_service.py`:

```python
class TestEvidenceDbLog:
    def _db(self):
        from bot.store.mysqldb import MySQLEvidenceDb
        mydb = MagicMock()
        cursor = MagicMock()
        cursor.lastrowid = 11
        mydb.dict_cursor.return_value = cursor
        return MySQLEvidenceDb(mydb=mydb), cursor

    def test_log_with_a_source_puzzle(self):
        db, _ = self._db()
        e = db.log(hunt_id=10, source_puzzle_id=5, note="a clue")
        assert e.id == 11
        assert e.source_puzzle_id == 5
        assert e.note == "a clue"
        assert e.media_id is None

    def test_log_without_a_source_puzzle(self):
        db, _ = self._db()
        e = db.log(hunt_id=10, source_puzzle_id=None, note="seen on the poster")
        assert e.source_puzzle_id is None
        assert e.note == "seen on the poster"

    def test_log_with_a_media_id(self):
        db, _ = self._db()
        e = db.log(hunt_id=10, source_puzzle_id=None, note="", media_id=7)
        assert e.media_id == 7
        assert e.note == ""

    def test_log_writes_media_id_to_the_insert(self):
        db, cursor = self._db()
        db.log(hunt_id=10, source_puzzle_id=None, note="", media_id=7)
        sql, params = cursor.execute.call_args[0]
        assert "media_id" in sql
        assert 7 in params
```

- [x] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q -k EvidenceDbLog > /tmp/test.log 2>&1`
Expected: FAIL — `TypeError: log() got an unexpected keyword argument 'media_id'`.

- [x] **Step 3: Widen the DAO method**

Replace `MySQLEvidenceDb.log` in `bot/store/mysqldb.py`:

```python
    def log(self, hunt_id: int, source_puzzle_id, note: str,
            media_id=None) -> 'EvidenceData':
        """Insert one evidence item and return it.

        source_puzzle_id may be None: evidence can belong to the hunt rather
        than to any one puzzle. media_id may be None: evidence can be text only.
        """
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO evidence_items"
                " (hunt_id, source_puzzle_id, note, media_id, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (hunt_id, source_puzzle_id, note, media_id, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        self.mydb.commit()
        e = EvidenceData()
        e.id = new_id or 0
        e.hunt_id = hunt_id
        e.source_puzzle_id = source_puzzle_id
        e.note = note
        e.media_id = media_id
        # Match import_dict's tzinfo-reattachment so a freshly-logged item and one
        # read back from the DB serialize identically (#33).
        e.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return e
```

- [x] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add bot/store/mysqldb.py tests/test_evidence_service.py
git commit -m "feat(store): evidence log accepts a null source and a media id"
```

---

## Task 8: `contribution_rows` renders sourceless evidence

**Files:**
- Modify: `bot/core/contribution.py`
- Test: `tests/test_contribution.py` (append)

`contribution_rows` currently keys every row off a **member puzzle** (`informs:<pid>`), so evidence with no source puzzle has no member row to hang from and silently vanishes. Sourceless evidence gets its own key namespace, appended after the member loop.

This is a pure function with no mocks — the easiest correctness win in the plan.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contribution.py`:

This file does **not** use `make_puzzle` from conftest — it has its own local `_p()` helper
building a `PuzzleData` directly (`tests/test_contribution.py:12-18`). Use it, matching the
file's existing style:

```python
# --- Sourceless evidence (Plan A) -----------------------------------------
# Evidence with no source puzzle has no member row to hang from, so it is
# appended under its own key namespace after the member loop.

def test_sourceless_evidence_gets_its_own_row():
    rows = contribution_rows(
        tag_group_id=5, members=[],
        evidence=[{"id": 9, "source_puzzle_id": None,
                   "source_label": "Harbour map", "content": "three anchors"}],
    )
    assert rows == [["informs:free:9", "Harbour map", "Information", "three anchors"]]


def test_sourceless_rows_come_after_member_rows():
    feeder = _p(2, "Alpha", solution="WORD", tag_edges={5: {"feeds"}})
    rows = contribution_rows(
        tag_group_id=5, members=[feeder],
        evidence=[{"id": 9, "source_puzzle_id": None,
                   "source_label": "Harbour map", "content": "three anchors"}],
    )
    assert rows[0][0] == "feeds:2"
    assert rows[-1][0] == "informs:free:9"


def test_multiple_sourceless_items_keep_their_given_order():
    """The docstring promises "sourceless evidence last, in the given order".
    With a single item that clause is unfalsifiable — reversing the list changes
    nothing observable — so it takes two to pin it. The ids descend so that a
    stray sort by id would fail rather than accidentally agree.
    """
    rows = contribution_rows(
        tag_group_id=5, members=[],
        evidence=[
            {"id": 9, "source_puzzle_id": None, "content": "first"},
            {"id": 3, "source_puzzle_id": None, "content": "second"},
        ],
    )
    assert [r[0] for r in rows] == ["informs:free:9", "informs:free:3"]
    assert [r[3] for r in rows] == ["first", "second"]


def test_sourceless_label_falls_back_to_an_em_dash():
    """Evidence with neither a source puzzle nor a label still renders."""
    rows = contribution_rows(
        tag_group_id=5, members=[],
        evidence=[{"id": 9, "source_puzzle_id": None, "content": "a fact"}],
    )
    assert rows[0][1] == "—"


def test_sourced_evidence_is_unaffected():
    """Rows with a source_puzzle_id keep their existing key and grouping."""
    inf = _p(4, "Gamma", tag_edges={5: {"informs"}})
    rows = contribution_rows(
        tag_group_id=5, members=[inf],
        evidence=[{"id": 4, "source_puzzle_id": 4, "content": "a datum"}],
    )
    assert rows == [["informs:4:4", "Gamma", "Information", "a datum"]]


def test_sourceless_evidence_does_not_leak_into_member_grouping():
    """A sourceless item must not be grouped under a member with no evidence,
    which would blank that member's own bare-edge row."""
    inf = _p(4, "Gamma", tag_edges={5: {"informs"}})
    rows = contribution_rows(
        tag_group_id=5, members=[inf],
        evidence=[{"id": 9, "source_puzzle_id": None, "content": "a fact"}],
    )
    assert rows[0] == ["informs:4", "Gamma", "Information", ""]
    assert rows[1][0] == "informs:free:9"
```

`contribution_rows` is already imported at the top of that file, so these need no imports.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_contribution.py -q -k Sourceless > /tmp/test.log 2>&1`
Expected: FAIL — the sourceless rows are simply absent (`assert [] == [[...]]`).

- [ ] **Step 3: Implement**

In `bot/core/contribution.py`, change the grouping so a null source cannot land in `ev_by_source`, and append the sourceless rows after the member loop. Replace the grouping line:

```python
    ev_by_source = {}
    sourceless = []
    for e in evidence:
        if e.get("source_puzzle_id") is None:
            sourceless.append(e)
        else:
            ev_by_source.setdefault(e["source_puzzle_id"], []).append(e)
```

and add, immediately before `return rows`:

```python
    # Evidence with no source puzzle has no member row to hang from — a fact
    # that belongs to the hunt, or (Plan B) one carved out of a canvas image.
    # Its own key namespace keeps it clear of `informs:<pid>`, and the reconciler
    # diffs purely on column A, so these rows survive re-tagging like any other.
    # No `informs` edge exists for them: an edge joins two puzzles, and a picture
    # or a bare observation is not one.
    for e in sourceless:
        rows.append([f"informs:free:{e['id']}", e.get("source_label") or "—",
                     "Information", e["content"]])
```

Update the module docstring's key table to add the new row:

```
  informs:free:<eid>     label or —    Information  datum with no source puzzle
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_contribution.py -q > /tmp/test.log 2>&1`
Expected: all pass, including the pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add bot/core/contribution.py tests/test_contribution.py
git commit -m "feat(core): render sourceless evidence in contribution tables"
```

---

## Task 9: Roll-up passes the source label and the media URL

**Files:**
- Modify: `bot/core/puzzle_service.py:722-734` (`_render_rollup`)
- Test: `tests/test_contribution_table_requests.py` (append) — or `tests/test_rollup_source_mode.py` if that is where `_render_rollup` is already exercised; check both and follow the existing home.

The cell text is the note plus, where there is an image, the media URL **as plain text**. It cannot be a `=HYPERLINK()` formula: `_values_update` writes `valueInputOption="RAW"` (`bot/cogs/puzzles_gsheet.py:830`) deliberately, so a solved answer like `=A1` is stored as text rather than becoming a live formula that resolves to `#REF` when the puzzle moves to the solved sheet. A formula written under RAW appears verbatim.

- [ ] **Step 1: Write the failing test**

```python
class TestRollupEvidenceContent:
    """_render_rollup builds the evidence dicts contribution_rows consumes."""

    async def test_image_evidence_appends_the_media_url_as_plain_text(self):
        """Not =HYPERLINK(): the roll-up writer is RAW on purpose, so a formula
        would be written out verbatim as text."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt, make_puzzle, make_round

        hunt = make_hunt(id=10)
        target = make_puzzle(id=4, name="Capstone", hunt_id=10)
        group = make_round(id=1, target_puzzle_id=4)
        rollup = SimpleNamespace(hunt_id=10, source_mode="round",
                                 edge_type_filter=None)
        item = SimpleNamespace(id=9, source_puzzle_id=None, note="three anchors",
                               media_id=7)

        gsheet = MagicMock()
        gsheet.render_contribution = AsyncMock()
        svc = PuzzleService.__new__(PuzzleService)
        svc.select_rollup_members = AsyncMock(return_value=[])

        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.config") as cfg:
            cfg.web_frontend_url = "https://puzzweb.example"
            edb.get_all.return_value = [item]
            await svc._render_rollup(gsheet, group, rollup, target)

        evidence = gsheet.render_contribution.await_args.kwargs["evidence"]
        assert evidence[0]["source_puzzle_id"] is None
        assert "three anchors" in evidence[0]["content"]
        assert "https://puzzweb.example/api/media/7" in evidence[0]["content"]
        assert "=HYPERLINK" not in evidence[0]["content"]

    async def test_falls_back_when_no_frontend_url_is_configured(self):
        """web_frontend_url is empty on any install without a public dashboard
        URL. Emitting a bare "/api/media/7" into a spreadsheet cell there would
        be a dead relative link, so the note stands alone — or "[image]" when
        there is no note, so the row is not silently blank.
        """
        from unittest.mock import patch
        from bot.core.puzzle_service import evidence_cell_content
        from types import SimpleNamespace
        with patch("bot.core.puzzle_service.config") as cfg:
            cfg.web_frontend_url = ""
            with_note = evidence_cell_content(
                SimpleNamespace(note="three anchors", media_id=7))
            without_note = evidence_cell_content(
                SimpleNamespace(note="", media_id=7))
        assert with_note == "three anchors"
        assert without_note == "[image]"

    async def test_text_evidence_content_is_unchanged(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt, make_puzzle, make_round

        target = make_puzzle(id=4, name="Capstone", hunt_id=10)
        group = make_round(id=1, target_puzzle_id=4)
        rollup = SimpleNamespace(hunt_id=10, source_mode="round",
                                 edge_type_filter=None)
        item = SimpleNamespace(id=9, source_puzzle_id=3, note="a clue", media_id=None)

        gsheet = MagicMock()
        gsheet.render_contribution = AsyncMock()
        svc = PuzzleService.__new__(PuzzleService)
        svc.select_rollup_members = AsyncMock(return_value=[])

        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.get_all.return_value = [item]
            await svc._render_rollup(gsheet, group, rollup, target)

        evidence = gsheet.render_contribution.await_args.kwargs["evidence"]
        assert evidence[0]["content"] == "a clue"
```

**`config` is not currently imported in `bot/core/puzzle_service.py`** — line 31 imports only `urls` from `bot.utils`. Add it as part of Step 3:

```python
from bot.utils import config
```

That is what makes `patch("bot.core.puzzle_service.config")` in the tests above bind — patch where the name is looked up, not where it is defined.

The `PuzzleService.__new__` trick above skips the constructor so no DB or bot is needed. If the existing tests in this file build the service differently, follow them instead.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_contribution_table_requests.py -q -k RollupEvidenceContent > /tmp/test.log 2>&1`
Expected: FAIL — content is `"three anchors"` with no URL appended.

- [ ] **Step 3: Implement**

In `bot/core/puzzle_service.py`, add a module-level helper above `PuzzleService` (or beside `resolve_puzzle_names`):

```python
def evidence_cell_content(item) -> str:
    """Text for one evidence datum in a rendered sheet cell.

    Where the item has an image, the dashboard media URL is appended as PLAIN
    TEXT, not =HYPERLINK(...): the roll-up writer uses valueInputOption="RAW"
    on purpose (puzzles_gsheet._values_update) so a solved answer like "=A1" is
    stored literally instead of becoming a live formula that resolves to #REF
    once the puzzle moves to the solved sheet. A formula written under RAW is
    shown verbatim, so it would be noise rather than a link.
    """
    note = item.note or ""
    media_id = getattr(item, "media_id", None)
    if not media_id:
        return note
    base = (config.web_frontend_url or "").rstrip("/")
    if not base:
        return note or "[image]"
    url = f"{base}/api/media/{media_id}"
    return f"{note}\n{url}" if note else url
```

Then change the `evidence` list comprehension in `_render_rollup` to:

```python
        evidence = [
            {
                "id": e.id,
                "source_puzzle_id": e.source_puzzle_id,
                "source_label": evidence_source_label(e),
                "content": evidence_cell_content(e),
            }
            for e in EvidenceJsonDb.get_all(rollup.hunt_id, target_puzzle_id=metapuzzle.id)
        ]
```

and add the label helper beside it:

```python
def evidence_source_label(item) -> str:
    """Display name for an evidence item's source in a rendered table.

    Empty for puzzle-sourced items — contribution_rows uses the member puzzle's
    own name for those. Plan B fills this in for canvas regions.
    """
    return ""
```

`evidence_source_label` is a seam Plan B fills in; for Plan A every sourceless item falls back to the em dash in `contribution_rows`.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_contribution_table_requests.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_contribution_table_requests.py
git commit -m "feat(core): include the media URL in rendered evidence cells"
```

---

## Task 10: Evidence sheet tab tolerates a null source

**Files:**
- Modify: `bot/core/puzzle_service.py:2779-2800` (`refresh_evidence`)
- Test: `tests/test_evidence_service.py` (append)

`refresh_evidence` renders the hunt-wide Evidence tab. Its Source Puzzle cell is `names.get(i.source_puzzle_id, f"Puzzle {i.source_puzzle_id}")`, which renders a null source as the literal string **`Puzzle None`**.

- [ ] **Step 1: Write the failing test**

```python
class TestRefreshEvidenceNullSource:
    async def test_null_source_renders_as_an_em_dash(self):
        """Not 'Puzzle None' — the fallback interpolates the id blindly."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt

        hunt = make_hunt(id=10, evidence_sheet_page_id=3, evidence_table_id=4)
        item = SimpleNamespace(id=9, source_puzzle_id=None, target_puzzle_id=None,
                               note="a fact", media_id=None, created_at=None)

        gsheet = MagicMock()
        gsheet.reconcile_table = AsyncMock()
        svc = PuzzleService.__new__(PuzzleService)
        svc._gsheet_cog = lambda h: gsheet

        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}):
            edb.get_all.return_value = [item]
            await svc.refresh_evidence(hunt)

        rows = gsheet.reconcile_table.await_args.kwargs["desired_rows"]
        assert rows[0][1] == "—"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q -k RefreshEvidenceNullSource > /tmp/test.log 2>&1`
Expected: FAIL — `assert 'Puzzle None' == '—'`.

- [ ] **Step 3: Implement**

In `refresh_evidence`, the `puzzle_ids` set must not include `None`, and the source cell needs a null branch. Replace those two pieces:

```python
        items = EvidenceJsonDb.get_all(hunt.id)
        puzzle_ids = ({i.source_puzzle_id for i in items if i.source_puzzle_id}
                      | {i.target_puzzle_id for i in items if i.target_puzzle_id})
        names = resolve_puzzle_names(puzzle_ids)
        rows = [
            [
                i.id,
                # A null source is evidence belonging to the hunt rather than to
                # a puzzle; the old fallback rendered it as "Puzzle None".
                (names.get(i.source_puzzle_id, f"Puzzle {i.source_puzzle_id}")
                 if i.source_puzzle_id else "—"),
                evidence_cell_content(i),
                names.get(i.target_puzzle_id, "") if i.target_puzzle_id else "",
                str(i.created_at)[:16] if i.created_at else "",
            ]
            for i in items
        ]
```

Note this also swaps `i.note` for `evidence_cell_content(i)` (Task 9), so the Evidence tab shows the media URL too.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_evidence_service.py
git commit -m "fix(core): render a null evidence source as an em dash, not 'Puzzle None'"
```

---

## Task 11: `log_clues` without a source puzzle

**Files:**
- Modify: `bot/core/puzzle_service.py:2736-2753` (`log_clues`), and the informs-link helpers at 2830-2880
- Test: `tests/test_evidence_service.py` (append)

`log_clues` derives auto-routing from the source puzzle's sole informs target (`_sole_informs_target`). With no source there is nothing to derive, so sourceless evidence is created untargeted and routed by hand. `link_informs_for_evidence` must be skipped entirely — an `informs` edge joins two puzzles.

- [ ] **Step 1: Write the failing test**

```python
class TestLogCluesSourceless:
    async def test_logs_with_a_null_source_and_no_auto_routing(self):
        from unittest.mock import MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt

        hunt = make_hunt(id=10)
        svc = PuzzleService.__new__(PuzzleService)

        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.side_effect = lambda hunt_id, source_puzzle_id, note, media_id=None: \
                MagicMock(id=1, note=note)
            items = await svc.log_clues(hunt, None, ["seen on the poster"])

        assert len(items) == 1
        edb.log.assert_called_once()
        assert edb.log.call_args.args[1] is None or \
               edb.log.call_args.kwargs.get("source_puzzle_id") is None
        edb.set_target.assert_not_called()

    async def test_passes_media_id_through(self):
        from unittest.mock import MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt

        svc = PuzzleService.__new__(PuzzleService)
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.side_effect = lambda hunt_id, source_puzzle_id, note, media_id=None: \
                MagicMock(id=1, note=note, media_id=media_id)
            items = await svc.log_clues(make_hunt(id=10), None, [""], media_id=7)

        assert items[0].media_id == 7

    async def test_link_informs_is_skipped_without_a_source(self):
        """An informs edge joins two puzzles; a bare observation is not one."""
        from unittest.mock import patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt

        svc = PuzzleService.__new__(PuzzleService)
        with patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb:
            await svc.link_informs_for_evidence(make_hunt(id=10), None, 4)
        tgdb.get_by_attr.assert_not_called()
```

Read `link_informs_for_evidence` at `bot/core/puzzle_service.py:2830` before writing that last test and assert against whichever DAO it actually reaches for first — the point is that it returns without touching anything.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q -k LogCluesSourceless > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute ...` from `_sole_informs_target(None)`.

- [ ] **Step 3: Implement**

Replace `log_clues`:

```python
    @pushes_board()
    async def log_clues(self, hunt, puzzle, notes: list, media_id=None) -> list:
        """Insert one evidence_item per note. Returns list of EvidenceData.

        `puzzle` may be None: evidence can belong to the hunt rather than to any
        one puzzle. With no source there is no informs edge to follow, so such
        items are created untargeted and routed by hand in the Evidence panel.

        If the source puzzle informs exactly one target, each clue is auto-routed
        to that informed target (target_puzzle_id) so it surfaces in the target's
        roll-up contribution table. Zero or several informs targets -> left
        untargeted for manual routing via the Evidence panel.
        """
        target_id = self._sole_informs_target(puzzle) if puzzle is not None else None
        items = []
        for note in notes:
            item = EvidenceJsonDb.log(hunt.id, puzzle.id if puzzle else None, note,
                                      media_id=media_id)
            if target_id is not None:
                item = EvidenceJsonDb.set_target(item.id, target_id) or item
            items.append(item)
        return items
```

Then add an early return at the top of **both** `link_informs_for_evidence` and `unlink_informs_for_evidence`:

```python
        # An informs edge joins two puzzles. Evidence with no source puzzle (a
        # hunt-level observation, or a canvas region in Plan B) has no edge to
        # create or remove.
        if source_puzzle_id is None:
            return
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py tests/test_evidence_informs.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_evidence_service.py
git commit -m "feat(core): log evidence with no source puzzle and an optional image"
```

---

## Task 12: Web serialisation carries media and a null source

**Files:**
- Modify: `bot/web/data.py:558-576` (`get_evidence`)
- Test: `tests/test_evidence_web.py` (append)

- [ ] **Step 1: Write the failing test**

```python
class TestGetEvidenceNullSource:
    def test_null_source_serialises_as_null_name(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from bot.web import data

        item = SimpleNamespace(id=9, source_puzzle_id=None, target_puzzle_id=None,
                               note="a fact", media_id=None, created_at=None)
        with patch("bot.store.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}):
            edb.get_all.return_value = [item]
            out = data.get_evidence(10)

        assert out[0]["source_puzzle_id"] is None
        assert out[0]["source_puzzle_name"] is None
        assert out[0]["media_id"] is None
        assert out[0]["media_url"] is None

    def test_media_id_becomes_a_url(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from bot.web import data

        item = SimpleNamespace(id=9, source_puzzle_id=None, target_puzzle_id=None,
                               note="", media_id=7, created_at=None)
        with patch("bot.store.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}):
            edb.get_all.return_value = [item]
            out = data.get_evidence(10)

        assert out[0]["media_id"] == 7
        assert out[0]["media_url"] == "/api/media/7"
```

`get_evidence` imports its DAOs *inside* the function (`from bot.store import EvidenceJsonDb`), so the patch target is `bot.store.EvidenceJsonDb`, not a module-level name on `bot.web.data`. That is the opposite of the usual rule and is correct here — verify by reading `bot/web/data.py:558-562`.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_web.py -q -k GetEvidenceNullSource > /tmp/test.log 2>&1`
Expected: FAIL — `KeyError: 'media_url'`.

- [ ] **Step 3: Implement**

```python
def get_evidence(hunt_id, source_puzzle_id=None, target_puzzle_id=None):
    """Return evidence items for a hunt with resolved puzzle names.

    source_puzzle_id/source_puzzle_name are None for evidence that belongs to
    the hunt rather than to any one puzzle. media_url is relative so it works
    on whichever origin is serving the SPA.
    """
    from bot.store import EvidenceJsonDb
    from bot.core.puzzle_service import resolve_puzzle_names
    items = EvidenceJsonDb.get_all(hunt_id, source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
    puzzle_ids = ({i.source_puzzle_id for i in items if i.source_puzzle_id}
                  | {i.target_puzzle_id for i in items if i.target_puzzle_id})
    names = resolve_puzzle_names(puzzle_ids)
    return [
        {
            "id": i.id,
            "source_puzzle_id": i.source_puzzle_id,
            "source_puzzle_name": names.get(i.source_puzzle_id, "") if i.source_puzzle_id else None,
            "target_puzzle_id": i.target_puzzle_id,
            "target_puzzle_name": names.get(i.target_puzzle_id, "") if i.target_puzzle_id else None,
            "note": i.note,
            "media_id": i.media_id,
            "media_url": f"/api/media/{i.media_id}" if i.media_id else None,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in items
    ]
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_web.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/web/data.py tests/test_evidence_web.py
git commit -m "feat(web): serialise evidence media and a null source"
```

---

## Task 13: `GET /api/media/<id>`

**Files:**
- Modify: `bot/web/api.py` (add after `attachment_bytes`, ~line 192)
- Test: `tests/test_media_web.py` (new)

Modelled directly on `attachment_bytes` at `bot/web/api.py:168`, including its `resolve()`-against-root traversal guard. Visibility is the hunt's, resolved through the existing `_load_visible_hunt`.

- [ ] **Step 1: Write the failing test**

```python
"""Plan A: GET /api/media/<id> — auth, hunt visibility, traversal guard."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _hunt(archived=False):
    return {"id": 10, "uid": "U1", "archived": archived, "guild_id": 1}


@pytest.fixture
def web(tmp_path):
    from quart import Quart
    from bot.web.api import api_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"
    app.register_blueprint(api_bp)

    (tmp_path / "uploads" / "10").mkdir(parents=True)
    (tmp_path / "uploads" / "10" / "a_crop.png").write_bytes(b"\x89PNG\r\n\x1a\nbytes")

    mdb = MagicMock()
    mdb.get_by_id.return_value = SimpleNamespace(
        id=7, hunt_id=10, path="uploads/10/a_crop.png", filename="crop.png",
        content_type="image/png", size=13, origin="upload", created_at=None)

    hdb = MagicMock()
    hdb.get_by_attr.return_value = SimpleNamespace(id=10, uid="U1")

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api._load_visible_hunt", AsyncMock(return_value=(_hunt(), {}))),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.core.message_media.media_root", return_value=tmp_path),
    ]
    started = [p.start() for p in patches]
    yield app, mdb, started
    for p in patches:
        p.stop()


async def test_serves_the_bytes(web):
    app, _, _ = web
    client = app.test_client()
    resp = await client.get("/api/media/7")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("image/png")
    assert await resp.get_data() == b"\x89PNG\r\n\x1a\nbytes"


async def test_missing_media_is_404(web):
    app, mdb, _ = web
    mdb.get_by_id.return_value = None
    resp = await app.test_client().get("/api/media/7")
    assert resp.status_code == 404


async def test_path_escaping_the_media_root_is_404(web):
    """Defence in depth: a stored path that resolves outside the root is refused
    even though save_image_bytes should never produce one."""
    app, mdb, _ = web
    mdb.get_by_id.return_value = SimpleNamespace(
        id=7, hunt_id=10, path="../../etc/passwd", filename="passwd",
        content_type="image/png", size=1, origin="upload", created_at=None)
    resp = await app.test_client().get("/api/media/7")
    assert resp.status_code == 404


async def test_hunt_not_visible_is_forbidden(web):
    app, _, _ = web
    with patch("bot.web.api._load_visible_hunt", AsyncMock(return_value=(None, 403))):
        resp = await app.test_client().get("/api/media/7")
    assert resp.status_code in (403, 404)


async def test_sets_nosniff(web):
    """Sniffing validates only a prefix, so a stored file can carry an appended
    payload. nosniff stops a browser MIME-sniffing that body into an executable
    context if the URL is ever navigated to directly."""
    app, _, _ = web
    resp = await app.test_client().get("/api/media/7")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


async def test_serves_the_stored_content_type_verbatim(web):
    """Never re-derived from the file extension or from anything the client
    sent — the sniffed type recorded at upload is the only source of truth."""
    app, mdb, _ = web
    mdb.get_by_id.return_value = SimpleNamespace(
        id=7, hunt_id=10, path="uploads/10/a_crop.png", filename="crop.png",
        content_type="image/gif", size=13, origin="upload", created_at=None)
    resp = await app.test_client().get("/api/media/7")
    assert resp.headers["Content-Type"].startswith("image/gif")
```

Before writing, read `tests/test_evidence_web.py` and `tests/test_add_evidence_web.py` and copy whichever app/fixture idiom they use — including how `login_required` is satisfied. The `patch("bot.web.auth.session", ...)` line above is taken from `test_add_evidence_web.py`; if the auth seam differs, follow the existing tests rather than this sketch.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_web.py -q > /tmp/test.log 2>&1`
Expected: FAIL — 404 from Quart because the route does not exist.

- [ ] **Step 3: Implement**

Add to `bot/web/api.py` after `attachment_bytes`:

```python
@api_bp.route("/media/<int:media_id>")
@login_required
async def media_bytes(media_id):
    """Serve a stored image. Visibility is the owning hunt's.

    Deliberately authenticated: nothing here is reachable without a Discord
    session, which is why evidence images cannot be embedded in Google Sheets
    with =IMAGE() (Google fetches those unauthenticated). Creating an
    unauthenticated URL to satisfy Sheets would expose team working to anyone
    holding the link.
    """
    from bot.store import HuntJsonDb, MediaJsonDb
    media = MediaJsonDb.get_by_id(media_id)
    if media is None:
        return jsonify({"error": "not_found"}), 404
    hunt_row = HuntJsonDb.get_by_attr(id=media.hunt_id)
    if hunt_row is None:
        return jsonify({"error": "not_found"}), 404
    hunt, guild_or_status = await _load_visible_hunt(hunt_row.uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "not_found_or_forbidden"}), guild_or_status
    from bot.core.message_media import media_root
    path = (media_root() / media.path).resolve()
    root = media_root().resolve()
    if root not in path.parents:            # defense in depth vs path traversal
        return jsonify({"error": "not_found"}), 404
    if not path.is_file():
        return jsonify({"error": "not_found"}), 404
    resp = await send_file(path, mimetype=media.content_type or "application/octet-stream",
                           as_attachment=False,
                           attachment_filename=media.filename)
    # sniff_image_type only validates a fixed-length PREFIX, so a stored file can
    # be a byte-perfect PNG header with an arbitrary payload appended. That is
    # harmless while the browser treats it as the type we stored, and dangerous
    # the moment the browser is allowed to MIME-sniff the body into an
    # executable context — which is exactly what nosniff forbids. The stored
    # content_type above is used verbatim and never re-derived from the
    # extension or from anything the client sent.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_web.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/web/api.py tests/test_media_web.py
git commit -m "feat(web): serve stored images at GET /api/media/<id>"
```

---

## Task 14: `POST /api/hunts/<uid>/media`

**Files:**
- Modify: `bot/web/api.py`
- Test: `tests/test_media_web.py` (append)

- [ ] **Step 1: Write the failing test**

```python
class TestMediaUpload:
    async def test_uploads_a_png(self, web):
        app, _, _ = web
        with patch("bot.store.MediaJsonDb") as mdb:
            mdb.create.return_value = SimpleNamespace(id=7, hunt_id=10)
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": ("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 201
        body = await resp.get_json()
        assert body["id"] == 7
        assert body["url"] == "/api/media/7"

    async def test_rejects_an_svg(self, web):
        app, _, _ = web
        resp = await app.test_client().post(
            "/api/hunts/U1/media",
            files={"file": ("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'>",
                            "image/svg+xml")},
        )
        assert resp.status_code == 400

    async def test_rejects_an_archived_hunt(self, web):
        app, _, _ = web
        with patch("bot.web.api._load_visible_hunt",
                   AsyncMock(return_value=(_hunt(archived=True), {}))):
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": ("crop.png", b"\x89PNG\r\n\x1a\np", "image/png")},
            )
        assert resp.status_code == 403

    async def test_missing_file_is_400(self, web):
        app, _, _ = web
        resp = await app.test_client().post("/api/hunts/U1/media")
        assert resp.status_code == 400
```

Quart's test client multipart argument differs across versions — if `files=` is not accepted, use the `form=`/`data=` idiom your installed Quart supports. Check with `pipenv run python -c "import quart; print(quart.__version__)"` and read its test-client docs before guessing.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_web.py -q -k MediaUpload > /tmp/test.log 2>&1`
Expected: FAIL — 404, route missing.

- [ ] **Step 3: Implement**

```python
@api_bp.route("/hunts/<uid>/media", methods=["POST"])
@login_required
async def upload_media(uid):
    """Store an uploaded image against a hunt and return its id and URL.

    The client-declared Content-Type is not trusted; save_image_bytes sniffs the
    magic bytes and rejects anything that is not PNG/JPEG/GIF/WebP. SVG is
    refused outright — it is a scriptable document, and serving one from our own
    origin would be stored XSS.
    """
    from bot.store import MediaJsonDb
    from bot.core.media_store import MediaRejected, max_upload_bytes, save_image_bytes
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "hunt not found"}), guild_or_status
    if hunt["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    files = await request.files
    upload = files.get("file")
    if upload is None:
        return jsonify({"error": "file required"}), 400
    # Read one byte past the cap so an oversize upload is detected without
    # buffering an unbounded body.
    raw = upload.read(max_upload_bytes() + 1)
    try:
        saved = save_image_bytes(
            hunt_id=hunt["id"],
            filename=upload.filename or "image",
            raw=raw,
            origin="upload",
        )
    except MediaRejected as exc:
        return jsonify({"error": str(exc)}), 400
    media = MediaJsonDb.create(
        hunt_id=hunt["id"], path=saved["path"], filename=saved["filename"],
        content_type=saved["content_type"], size=saved["size"], origin=saved["origin"],
    )
    return jsonify({"id": media.id, "url": f"/api/media/{media.id}"}), 201
```

`request` is imported locally in the neighbouring evidence routes (`from quart import request`); follow whichever pattern the surrounding handlers use.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_media_web.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/web/api.py tests/test_media_web.py
git commit -m "feat(web): accept image uploads at POST /api/hunts/<uid>/media"
```

---

## Task 15: `POST /api/evidence` accepts an optional source and a media id

**Files:**
- Modify: `bot/web/api.py:268-303` (`add_evidence`)
- Test: `tests/test_add_evidence_web.py` (append)

Today the route hard-requires `source_puzzle_id` and non-empty `notes`. Both become optional, but **not both empty** — an evidence item with neither text nor an image is nothing at all.

- [ ] **Step 1: Write the failing test**

```python
class TestSourcelessAndImageEvidence:
    async def test_accepts_evidence_with_no_source_puzzle(self, web):
        app = web
        resp = await app.test_client().post("/api/evidence", json={
            "hunt_uid": "U1", "notes": ["seen on the poster"],
        })
        assert resp.status_code == 201

    async def test_accepts_an_image_with_no_note(self, web):
        app = web
        resp = await app.test_client().post("/api/evidence", json={
            "hunt_uid": "U1", "notes": [], "media_id": 7,
        })
        assert resp.status_code == 201

    async def test_rejects_neither_note_nor_image(self, web):
        app = web
        resp = await app.test_client().post("/api/evidence", json={
            "hunt_uid": "U1", "notes": [],
        })
        assert resp.status_code == 400

    async def test_still_rejects_non_string_notes(self, web):
        app = web
        resp = await app.test_client().post("/api/evidence", json={
            "hunt_uid": "U1", "notes": [{"not": "a string"}],
        })
        assert resp.status_code == 400
```

The existing `web` fixture in this file returns the app; match its shape (read the file's fixture — it may yield a tuple).

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_add_evidence_web.py -q -k SourcelessAndImage > /tmp/test.log 2>&1`
Expected: FAIL — 400 "hunt_uid, source_puzzle_id, and notes required".

- [ ] **Step 3: Implement**

Replace the validation and call in `add_evidence`:

```python
    hunt_uid = body.get("hunt_uid")
    source_puzzle_id = body.get("source_puzzle_id")
    media_id = body.get("media_id")
    notes = body.get("notes", [])
    if not hunt_uid:
        return jsonify({"error": "hunt_uid required"}), 400
    if not all(isinstance(n, str) for n in notes):
        return jsonify({"error": "notes must be strings"}), 400
    clean_notes = [n for n in notes if n.strip()]
    # An item with neither text nor an image is nothing at all. Either alone is
    # fine: a transcription with no picture, or a picture with no transcription.
    if not clean_notes and not media_id:
        return jsonify({"error": "a note or an image is required"}), 400
```

then, after the hunt lookup and archived guard, make the puzzle lookup conditional:

```python
    puzzle = None
    if source_puzzle_id:
        puzzle = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
        if puzzle is None or puzzle.hunt_id != hunt["id"]:
            return jsonify({"error": "puzzle not found in hunt"}), 404
```

and the service call:

```python
    # An image with no transcription still needs one row, so fall back to a
    # single empty note rather than logging nothing.
    items = await cog.service.log_clues(hunt_obj, puzzle, clean_notes or [""],
                                        media_id=media_id)
    if items:
        asyncio.create_task(cog.service.refresh_evidence_safe(hunt_obj))
        if puzzle is not None:
            asyncio.create_task(cog.service.refresh_informs_targets(hunt_obj, puzzle))
    return jsonify([{"id": i.id, "note": i.note} for i in items]), 201
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_add_evidence_web.py -q > /tmp/test.log 2>&1`
Expected: all pass, including the pre-existing archived-hunt and non-string-notes tests.

- [ ] **Step 5: Commit**

```bash
git add bot/web/api.py tests/test_add_evidence_web.py
git commit -m "feat(web): POST /api/evidence accepts an optional source and an image"
```

---

## Task 16: `!clue` accepts an attachment and runs outside a puzzle channel

**Files:**
- Modify: `bot/cogs/puzzles.py:1480-1528` (`clue`, `clues`)
- Test: `tests/test_evidence_commands.py` (append)

Discord is where screenshots already are, so this is the highest-value half of Plan A. Two changes to `!clue`: an attached image is stored and linked, and running it outside a puzzle channel logs sourceless evidence instead of erroring.

`!clues` (the bulk shortcut) keeps requiring a puzzle channel and gains no attachment support — it is a quoting shortcut for many short strings, and one attachment cannot belong to several items.

- [ ] **Step 1: Write the failing test**

```python
class TestClueWithAttachment:
    async def test_stores_an_attached_image_and_links_it(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from tests.conftest import make_cog, make_ctx, make_hunt, make_puzzle

        puzzle = make_puzzle(id=5, name="Rusty Anchor", hunt_id=10)
        hunt = make_hunt(id=10)
        cog = make_cog(puzzle=puzzle, hunt=hunt)
        cog.service.log_clues = AsyncMock(return_value=[MagicMock(id=1)])
        cog.service.refresh_evidence_safe = AsyncMock()

        ctx = make_ctx()
        att = MagicMock()
        att.filename = "crop.png"
        att.content_type = "image/png"
        att.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\npayload")
        ctx.message.attachments = [att]

        with patch("bot.cogs.puzzles.save_image_bytes") as save, \
             patch("bot.cogs.puzzles.MediaJsonDb") as mdb:
            save.return_value = {"path": "uploads/10/a_crop.png", "filename": "crop.png",
                                 "content_type": "image/png", "size": 7, "origin": "discord"}
            mdb.create.return_value = MagicMock(id=7)
            await Puzzles.clue.callback(cog, ctx, note="three anchors")

        assert cog.service.log_clues.await_args.kwargs["media_id"] == 7

    async def test_logs_sourceless_outside_a_puzzle_channel(self):
        from unittest.mock import AsyncMock, MagicMock
        from tests.conftest import make_cog, make_ctx, make_hunt

        hunt = make_hunt(id=10)
        cog = make_cog(puzzle=None, hunt=hunt)
        cog.service.log_clues = AsyncMock(return_value=[MagicMock(id=1)])
        cog.service.refresh_evidence_safe = AsyncMock()

        ctx = make_ctx()
        ctx.message.attachments = []
        await Puzzles.clue.callback(cog, ctx, note="heard from another team")

        assert cog.service.log_clues.await_args.args[1] is None

    async def test_rejects_a_non_image_attachment(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from tests.conftest import make_cog, make_ctx, make_hunt, make_puzzle

        cog = make_cog(puzzle=make_puzzle(id=5, hunt_id=10), hunt=make_hunt(id=10))
        cog.service.log_clues = AsyncMock()
        ctx = make_ctx()
        att = MagicMock()
        att.filename = "notes.pdf"
        att.read = AsyncMock(return_value=b"%PDF-1.4")
        ctx.message.attachments = [att]

        from bot.core.media_store import MediaRejected
        with patch("bot.cogs.puzzles.save_image_bytes", side_effect=MediaRejected("nope")):
            await Puzzles.clue.callback(cog, ctx, note="x")

        ctx.send.assert_awaited()
        cog.service.log_clues.assert_not_awaited()

    async def test_note_is_optional_when_an_image_is_attached(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from tests.conftest import make_cog, make_ctx, make_hunt, make_puzzle

        cog = make_cog(puzzle=make_puzzle(id=5, hunt_id=10), hunt=make_hunt(id=10))
        cog.service.log_clues = AsyncMock(return_value=[MagicMock(id=1)])
        cog.service.refresh_evidence_safe = AsyncMock()
        ctx = make_ctx()
        att = MagicMock()
        att.filename = "crop.png"
        att.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\np")
        ctx.message.attachments = [att]

        with patch("bot.cogs.puzzles.save_image_bytes") as save, \
             patch("bot.cogs.puzzles.MediaJsonDb") as mdb:
            save.return_value = {"path": "p", "filename": "crop.png",
                                 "content_type": "image/png", "size": 5, "origin": "discord"}
            mdb.create.return_value = MagicMock(id=7)
            await Puzzles.clue.callback(cog, ctx, note="")

        cog.service.log_clues.assert_awaited()
```

Each test above needs `from bot.cogs.puzzles import Puzzles` — a `hybrid_command` wraps the function, so the callback is reached as `Puzzles.clue.callback(cog, ctx, ...)` with the cog passed explicitly. That is the idiom the existing tests in this file already use (`tests/test_evidence_commands.py:22`); do not call `cog.clue(...)`.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_commands.py -q -k ClueWithAttachment > /tmp/test.log 2>&1`
Expected: FAIL — `TypeError: log_clues() got an unexpected keyword argument 'media_id'` on the mock, or the not-a-puzzle-channel early return.

- [ ] **Step 3: Implement**

Add the imports at the top of `bot/cogs/puzzles.py`, beside the other `bot.core` imports:

```python
from bot.core.media_store import MediaRejected, save_image_bytes
from bot.store import MediaJsonDb
```

Add a helper method on the cog, just above `clue`:

```python
    async def _store_clue_attachment(self, ctx, hunt):
        """Store the first image attached to ctx.message, if any.

        Returns (media_id, error_message). media_id is None when there is no
        attachment; error_message is None unless the attachment was refused.
        """
        attachments = getattr(ctx.message, "attachments", None) or []
        if not attachments:
            return None, None
        att = attachments[0]
        raw = await att.read()
        try:
            saved = save_image_bytes(
                hunt_id=hunt.id,
                filename=att.filename or "image",
                raw=raw,
                origin="discord",
            )
        except MediaRejected as exc:
            return None, str(exc)
        media = MediaJsonDb.create(
            hunt_id=hunt.id, path=saved["path"], filename=saved["filename"],
            content_type=saved["content_type"], size=saved["size"],
            origin=saved["origin"],
        )
        return media.id, None
```

Replace `clue`:

```python
    @commands.hybrid_command(name="clue", description="Log a freeform clue/observation, optionally with an image")
    async def clue(self, ctx, *, note: str = ""):
        """*Log a freeform clue/observation to the evidence sheet.*

        Usage: `!clue text` (attach an image to log a picture too)
        Example: `!clue every answer starts with a chemical element`
        Run in a puzzle channel to log the clue from that puzzle; run anywhere
        else in the hunt to log it with no source puzzle. The text is optional
        when you attach an image. For several at once use `!clues`."""
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)
        media_id, error = await self._store_clue_attachment(ctx, hunt)
        if error:
            return await ctx.send(f":warning: {error}")
        if not note.strip() and media_id is None:
            await ctx.send(":warning: No clue text or image provided.")
            return
        await self.service.log_clues(hunt, puzzle, [note.strip()], media_id=media_id)
        asyncio.create_task(self.service.refresh_evidence_safe(hunt))
        if puzzle is not None:
            asyncio.create_task(self.service.refresh_informs_targets(hunt, puzzle))
        where = f" from **{puzzle.name}**" if puzzle is not None else ""
        what = "Clue" if media_id is None else "Clue with image"
        await ctx.send(f":pencil: {what} logged{where}.")
```

`clues` needs only the `refresh_informs_targets` guard left as-is (it still requires a puzzle channel, so `puzzle` is never None there) — leave it otherwise untouched.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_commands.py -q > /tmp/test.log 2>&1`
Expected: all pass. Existing tests asserting the old "No clue text provided." wording or the not-a-puzzle-channel rejection will need updating — that is expected, and the new behaviour is correct.

- [ ] **Step 5: Run the whole suite**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_evidence_commands.py
git commit -m "feat(bot): !clue accepts an image attachment and works outside a puzzle channel"
```

---

## Task 17: Frontend types and hooks

**Files:**
- Modify: `frontend/src/types.ts:188-197`
- Modify: `frontend/src/api.ts`

No test step: this project has no frontend test runner. Type-checking is the gate.

- [ ] **Step 1: Widen `EvidenceItem`**

```ts
export interface EvidenceItem {
  id: number;
  source_puzzle_id: number | null;
  source_puzzle_name: string | null;
  target_puzzle_id: number | null;
  target_puzzle_name: string | null;
  note: string;
  media_id: number | null;
  media_url: string | null;
  created_at: string | null;
}
```

- [ ] **Step 2: Add the upload hook**

In `frontend/src/api.ts`, beside `useLogClue` (line 169):

```ts
export function useUploadMedia(huntUid: string) {
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      // Not apiSend: that sets a JSON Content-Type, and multipart needs the
      // browser to set its own boundary.
      const resp = await fetch(`/api/hunts/${huntUid}/media`, {
        method: "POST",
        body: form,
        credentials: "same-origin",
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || "Upload failed");
      }
      return (await resp.json()) as { id: number; url: string };
    },
  });
}
```

Check how `apiSend` builds its request and whether it sets `credentials` — match it rather than the sketch above.

- [ ] **Step 3: Add a hunt-scoped evidence hook**

There are **two** evidence-creation endpoints, and the panel currently uses the wrong one for this feature:

| | route | scope | lock | audit |
| --- | --- | --- | --- | --- |
| `useLogClue` — what the panel's "Log clue" button calls today | `POST /api/puzzles/<id>/clues` (`bot/web/actions.py:456`) | puzzle, **by URL** | `puzzle_lock(puzzle_id)` | `_audit("clue", ...)` |
| widened in Task 15 | `POST /api/evidence` (`bot/web/api.py:268`) | hunt | none | none |

A sourceless or image-only clue **cannot** go through the actions route — there is no puzzle id to put in the URL. So the panel moves to `/api/evidence`. Add a sibling hook rather than contorting `useLogClue`, which stays as it is for any other caller:

```ts
export function useAddEvidence(huntUid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, note, mediaId }:
      { puzzleId: number | null; note: string; mediaId: number | null }) =>
      apiSend<{ id: number; note: string }[]>("/api/evidence", "POST", {
        hunt_uid: huntUid,
        source_puzzle_id: puzzleId,
        notes: note ? [note] : [],
        media_id: mediaId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt", huntUid] });
      qc.invalidateQueries({ queryKey: ["evidence", huntUid] });
    },
  });
}
```

- [ ] **Step 3b: Close the audit gap the move opens**

Moving the panel off the actions route drops its `_audit("clue", ...)` entry, which would silently lose the audit trail for every clue logged from the dashboard. Add the same call to `add_evidence` in `bot/web/api.py`, after the `log_clues` call:

```python
        from bot.web.actions import _audit
        await _audit("clue", puzzle_id=puzzle.id if puzzle else None,
                     hunt_uid=hunt["uid"])
```

Read `_audit`'s signature in `bot/web/actions.py` first and confirm it tolerates `puzzle_id=None` — if it does not, pass only `hunt_uid`. Import locally to avoid a circular import between the two blueprints.

The `puzzle_lock` the actions route takes is **not** carried over, and that is correct: it serialises mutations of one puzzle, and appending an evidence row for a hunt is not one. `log_clues` is decorated `@pushes_board()` and does a plain insert.

- [ ] **Step 4: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors. If the project pins a different check (read `frontend/package.json` scripts), run that instead.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(web): frontend types and hooks for image evidence"
```

---

## Task 18: Evidence panel shows images

**Files:**
- Modify: `frontend/src/components/EvidencePanel.tsx`

- [ ] **Step 1: Handle a null source in the row**

`EvidenceRow` renders `{item.source_puzzle_name}` directly (line ~293). With a null source that renders nothing at all, which reads as a bug. Replace it:

```tsx
      <Table.Td>{item.source_puzzle_name ?? "—"}</Table.Td>
```

- [ ] **Step 2: Handle a null source in filtering and sorting**

`EvidencePanel` filters and sorts on `source_puzzle_name`. Read the `filterSource` and `sort` logic (lines ~30-45 and the `useMemo` that applies them) and make both null-safe — a sourceless item must not crash a `localeCompare`, and the source filter needs an explicit "no source" choice. Use `(item.source_puzzle_name ?? "")` for comparisons.

- [ ] **Step 3: Add the thumbnail**

Add a cell before the note, rendering the image at a small fixed height with a link to the full size:

```tsx
      <Table.Td>
        {item.media_url ? (
          <Anchor href={item.media_url} target="_blank" rel="noreferrer">
            <Image src={item.media_url} alt={item.note || "evidence image"}
                   h={40} w="auto" fit="contain" />
          </Anchor>
        ) : null}
      </Table.Td>
```

Import `Anchor` and `Image` from `@mantine/core`, and add a matching header cell to the table head so the columns stay aligned.

- [ ] **Step 4: Add the image picker to the log-a-clue control**

The panel has `cluePuzzleId` / `clueNote` state and a puzzle `Select` (lines ~46-47, ~154-167). `submitClue` currently reads:

```tsx
    if (!cluePuzzleId || !clueNote.trim()) return;
    logClue.mutate(
      { puzzleId: Number(cluePuzzleId), note: clueNote.trim() },
```

Both halves of that guard now block legitimate input — a clue needs neither a puzzle nor text, as long as it has one of text or an image. Replace the control's submit path:

```tsx
  const uploadMedia = useUploadMedia(uid);
  const addEvidence = useAddEvidence(uid);
  const [clueFile, setClueFile] = useState<File | null>(null);

  async function submitClue() {
    const note = clueNote.trim();
    // Either alone is enough; neither is nothing at all.
    if (!note && !clueFile) return;
    try {
      const media = clueFile ? await uploadMedia.mutateAsync(clueFile) : null;
      await addEvidence.mutateAsync({
        puzzleId: cluePuzzleId ? Number(cluePuzzleId) : null,
        note,
        mediaId: media ? media.id : null,
      });
      setClueNote("");
      setClueFile(null);
      setCluePuzzleId(null);
    } catch (err) {
      notifyError(err);
    }
  }
```

Add a `FileInput` beside the note field:

```tsx
          <FileInput
            size="xs"
            placeholder="Image (optional)"
            accept="image/png,image/jpeg,image/gif,image/webp"
            value={clueFile}
            onChange={setClueFile}
            clearable
            w={180}
          />
```

Import `FileInput` from `@mantine/core` and `useAddEvidence` / `useUploadMedia` from `../api`. Make the puzzle `Select` `clearable` if it is not already — a clue no longer needs a source puzzle. Update the `Button`'s `loading` prop, which currently reads `logClue.isPending`, to `uploadMedia.isPending || addEvidence.isPending`.

- [ ] **Step 5: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 6: Build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/EvidencePanel.tsx
git commit -m "feat(web): show evidence images and sourceless items in the evidence panel"
```

---

## Task 19: Documentation

**Files:**
- Modify: `docs/commands.md:146-147`
- Modify: `docs/user-guide.md` (if it covers `!clue` — grep first)

House rule: when a command's arguments or permissions change, update **both** the docstring (done in Task 16) and the row in `docs/commands.md`.

- [ ] **Step 1: Update the command reference**

Replace the `!clue` row:

```markdown
| `!clue` *(slash)* | | `text` *(optional if an image is attached)* | Log a freeform clue to the evidence sheet. Attach an image to log a picture. Outside a puzzle channel the clue is logged with no source puzzle. |
```

Leave the `!clues` row unchanged — it still requires a puzzle channel and takes no attachment.

- [ ] **Step 2: Check the user guide**

Run: `grep -n "clue" docs/user-guide.md`

If it documents `!clue`, update it to match. If it does not, skip.

- [ ] **Step 3: Verify the slash description length**

The `description=` on a hybrid command must be ≤100 characters. Count the one written in Task 16: `"Log a freeform clue/observation, optionally with an image"` is 56. Confirm it did not grow.

- [ ] **Step 4: Commit**

```bash
git add docs/commands.md docs/user-guide.md
git commit -m "docs: !clue accepts an image and works outside a puzzle channel"
```

---

## Task 20: Full verification

- [ ] **Step 1: Full test suite**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass, exit 0. A timeout kill exits 124 — read `/tmp/test.log` either way.

- [ ] **Step 2: Lint**

Run: `pipenv run ruff check`
Expected: no findings. **Do not blind-`--fix` F401** on `bot/` modules — package re-exports and test patch-targets are load-bearing. Review each finding on production code.

- [ ] **Step 3: Type check**

Run: `pipenv run basedpyright bot/`
Expected: 0 errors. CI enforces this. Fix new findings rather than suppressing them; `# pyright: ignore[...]` is a last resort and needs a one-line justification.

Expect findings around the newly-optional `source_puzzle_id`: anywhere it is used as an `int` without a guard. The house pattern is `assert x is not None` immediately after acquisition with the invariant named in a short comment — but only where the invariant genuinely holds. Where it does not, add a real null branch.

- [ ] **Step 4: Frontend build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 5: Manual smoke test**

With the bot running against the test site (`docs/dashboard.md` has the topology):

1. `!clue` with an attached PNG in a puzzle channel → item appears in the Evidence panel with a thumbnail.
2. `!clue` in a non-puzzle hunt channel → item appears with an em-dash source.
3. Upload an image from the Evidence panel with no puzzle selected → 201, thumbnail renders.
4. Try to upload a `.svg` → refused with a readable message.
5. Route an image clue to a target puzzle → the target's contribution table shows the note and the media URL as text.
6. Open `/api/media/<id>` in a logged-out private window → refused.

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "fix: address lint and type findings for image evidence"
```

---

## Definition of done

Mapped to the spec's acceptance criteria (Plan A only; 5–9 belong to Plan B):

1. ✅ Evidence carries an image, free text, or both, and renders in the panel — Tasks 6, 7, 12, 18.
2. ✅ Evidence can be logged with no source puzzle and routes to a target normally — Tasks 11, 15, 16.
3. ✅ Contribution tables show sourceless evidence with its transcription and the media URL as plain text — Tasks 8, 9.
4. ✅ `!clue` with an attached screenshot produces image evidence — Task 16.
10. ✅ No unauthenticated URL serves any uploaded image — Task 13 (`@login_required` plus hunt visibility), and the deliberate absence of any public media route.
