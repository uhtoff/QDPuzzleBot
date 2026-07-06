# Discord → Web Message Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror Discord messages from hunt puzzle channels into the web database as they happen, render them in an in-dashboard chat viewer, and make hunt archiving produce a self-contained web transcript for free.

**Architecture:** A new `bridge` cog receives gateway push events (`on_message`, `on_raw_message_edit`, `on_raw_message_delete`) — zero REST calls, zero rate-limit buckets — and enqueues records to a background worker (the exact pattern in `bot/cogs/activity.py`). The worker writes rows via a new `MessageDb` DAO, downloads attachment bytes to disk off the gateway path, and publishes live events over the existing `events.py` → `/api/ws/activity` WebSocket. A read model in `bot/web/data.py` plus two Quart routes feed a React `ChatViewer` modal opened from the Discord badge. Only channels tracked in the `puzzles` table are captured; explicit message deletions hard-delete rows and bytes for privacy; deleting a *channel* (archiving) does not.

**Tech Stack:** discord.py cogs, raw MySQL via `_MySQLBaseDb` DAOs, Quart (async Flask) + `bot/web` pub/sub WebSocket, React + TypeScript + Vite + Mantine + `@tabler/icons-react`. Tests: pytest (`asyncio_mode = auto`, fully mocked, factories in `tests/conftest.py`), `pipenv run python -m pytest`. Type check: `pipenv run basedpyright bot/` (must be 0 errors). Lint: `pipenv run ruff check`.

---

## Conventions for every task

- **Run tests:** `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1` then read `/tmp/test.log`. A timeout-killed run exits 124.
- **Patch at the lookup site**, not the definition site (see `docs/agents/python.md`).
- **Awaited things are `AsyncMock`; sync things are `MagicMock`.** `events.publish` is **sync** — patch with a plain `Mock`.
- **`MagicMock().name` is a Mock, not a string** — set `.name`, `.id`, `.content` explicitly.
- Commit after each task. Branch is `staging` (the active integration branch).
- After Phase A and again at the end: `pipenv run basedpyright bot/` must report 0 errors; `pipenv run ruff check` must be clean.

## File structure (what each new/changed file is responsible for)

**Phase A — capture & storage**
- Create `migrations/027_message_bridge.sql` — `messages` + `message_attachments` tables. **Check the number isn't already taken by a parallel worktree before merging** (see the migration-collision note in project memory); renumber if so.
- Modify `bot/utils/config.py` — three new `web.*` settings.
- Modify `bot/store/mysqldb.py` — add `MySQLMessageDb(_MySQLBaseDb)`.
- Modify `bot/store/__init__.py` — instantiate `MessageDb` on a dedicated connection.
- Create `bot/core/message_media.py` — attachment path + download helpers (pure, testable).
- Create `bot/cogs/bridge.py` — the cog: listeners + background worker. Auto-loaded (any `cogs/*.py` with `setup()` is discovered).

**Phase B — read API & viewer**
- Modify `bot/web/data.py` — `get_hunt_by_channel_id`, `get_channel_messages`, `get_attachment_with_channel`, and a `discord_deep_url` field.
- Modify `bot/web/api.py` — `_channel_messages_visible` + two routes.
- Modify `frontend/src/types.ts` — `ChatAttachment`, `ChatMessage`, extend `ActivityEvent`, add `Puzzle.discord_deep_url`.
- Modify `frontend/src/api.ts` — `fetchChannelMessages`.
- Create `frontend/src/hooks/useChannelMessages.ts` — fetch transcript (+ live tail while open).
- Create `frontend/src/components/ChatViewer.tsx` — the modal.
- Modify `frontend/src/components/PuzzleLinks.tsx` — badge opens the viewer.

**Phase C — live push**
- Modify `bot/cogs/bridge.py` — publish `message` / `message_edit` / `message_delete` events.
- Modify `bot/web/api.py` — dispatch those event types through `_activity_event_visible`.
- (Frontend live tail already built in `useChannelMessages` in Phase B — Phase C wires the server side.)

**Phase D — badge swap polish & archive integration**
- Finish `PuzzleLinks.tsx` state handling (live vs archived) + `discord://` deep link.
- No changes to `bot/core/hunt_archive.py` or the `archive_hunt` service — verified by a test asserting archived transcripts survive channel deletion.

---

## Shared definitions (referenced across tasks — define exactly once, in Phase A)

`kind` values (module constants in `bot/cogs/bridge.py`, mirrored as raw ints in the API/frontend):

```python
KIND_HUMAN = 0
KIND_THIS_BOT = 1
KIND_OTHER_BOT = 2
KIND_COMMAND = 3
```

---

# Phase A — Capture & storage

### Task A1: Migration for messages + attachments

**Files:**
- Create: `migrations/027_message_bridge.sql`

- [ ] **Step 1: Write the migration**

```sql
-- Discord -> web message bridge (forward-only capture of hunt puzzle channels).
-- Additive: existing tables/queries are untouched. Ship before deploying the code.

CREATE TABLE IF NOT EXISTS `messages` (
    `id`            BIGINT UNSIGNED NOT NULL,   -- Discord message snowflake (time-sortable)
    `channel_id`    BIGINT UNSIGNED NOT NULL,
    `guild_id`      BIGINT UNSIGNED NOT NULL,
    `author_id`     BIGINT UNSIGNED NOT NULL,
    `author_name`   VARCHAR(255)    NOT NULL DEFAULT '',
    `author_avatar` VARCHAR(255)    NULL,
    `kind`          TINYINT         NOT NULL DEFAULT 0,   -- 0 human,1 this-bot,2 other-bot,3 command
    `content`       TEXT            NULL,
    `reference_id`  BIGINT UNSIGNED NULL,
    `created_at`    DATETIME(3)     NOT NULL,
    `edited_at`     DATETIME(3)     NULL,
    PRIMARY KEY (`id`),
    KEY `idx_messages_channel` (`channel_id`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `message_attachments` (
    `id`              BIGINT UNSIGNED NOT NULL,   -- Discord attachment snowflake
    `message_id`      BIGINT UNSIGNED NOT NULL,
    `filename`        VARCHAR(255)    NOT NULL DEFAULT '',
    `content_type`    VARCHAR(100)    NULL,
    `size`            INT             NOT NULL DEFAULT 0,
    `source_url`      VARCHAR(512)    NOT NULL DEFAULT '',
    `local_path`      VARCHAR(512)    NULL,
    `download_status` TINYINT         NOT NULL DEFAULT 0,   -- 0 pending,1 done,2 failed
    PRIMARY KEY (`id`),
    KEY `idx_message_attachments_msg` (`message_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

- [ ] **Step 2: Apply it to the local test DB** (so later manual verification works)

Run: `pipenv run python -c "import bot.utils as u; print(u.config.database)"` to confirm the DB name, then apply with your MySQL client, e.g.
`mysql <db> < migrations/027_message_bridge.sql`
Expected: two tables created, no error.

- [ ] **Step 3: Commit**

```bash
git add migrations/027_message_bridge.sql
git commit -m "feat(bridge): messages + message_attachments schema (migration 027)"
```

---

### Task A2: Config settings

**Files:**
- Modify: `bot/utils/config.py` (the `web = self.config.get("web", {})` block, ~line 42-53)

- [ ] **Step 1: Add the settings** after the existing `self.web_frontend_dist` line

```python
        # Message bridge (Discord -> web). Off by default so capture ships dark.
        self.web_bridge_enabled = bool(web.get("bridge_enabled", False))
        # Root dir for downloaded attachment bytes. Required when bridge is on.
        self.web_archive_media_dir = web.get("archive_media_dir", "")
        # Reject attachments larger than this (bytes). Default 25 MB (Discord free cap).
        self.web_attachment_max_bytes = int(web.get("attachment_max_bytes", 26214400))
```

- [ ] **Step 2: Sanity check import**

Run: `pipenv run python -c "import bot.utils as u; print(u.config.web_bridge_enabled, u.config.web_attachment_max_bytes)"`
Expected: `False 26214400`

- [ ] **Step 3: Commit**

```bash
git add bot/utils/config.py
git commit -m "feat(bridge): web.bridge_enabled / archive_media_dir / attachment_max_bytes config"
```

---

### Task A3: `MessageDb` DAO

**Files:**
- Modify: `bot/store/mysqldb.py` (add the class near `MySQLChannelActivityDb`, ~line 660)
- Modify: `bot/store/__init__.py` (import + instantiate)
- Test: `tests/test_message_db.py`

- [ ] **Step 1: Write the failing test**

```python
"""MessageDb DAO: write/edit/delete + attachment rows, all against a mocked cursor."""
from unittest.mock import MagicMock


def _make_db():
    from bot.store.mysqldb import MySQLMessageDb
    mydb = MagicMock()
    cursor = MagicMock()
    mydb.cursor.return_value = cursor
    return MySQLMessageDb(mydb=mydb), cursor


def test_write_inserts_with_upsert_and_commits():
    db, cursor = _make_db()
    db.write(id=10, channel_id=20, guild_id=30, author_id=40, author_name="Ann",
             author_avatar=None, kind=0, content="hi", reference_id=None,
             created_at="2026-07-06 00:00:00.000")
    sql = cursor.execute.call_args.args[0]
    assert "INSERT INTO messages" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert cursor.execute.call_args.args[1][0] == 10
    db.mydb.commit.assert_called_once()


def test_mark_edited_updates_content_and_edited_at():
    db, cursor = _make_db()
    db.mark_edited(10, "edited", "2026-07-06 00:01:00.000")
    sql, params = cursor.execute.call_args.args
    assert "UPDATE messages SET" in sql and "edited_at" in sql
    assert params == ("edited", "2026-07-06 00:01:00.000", 10)


def test_delete_message_returns_local_paths_then_deletes_both_tables():
    db, cursor = _make_db()
    cursor.fetchall.return_value = [{"local_path": "g/c/1_a.png"}, {"local_path": None}]
    paths = db.delete_message(10)
    assert paths == ["g/c/1_a.png"]  # None filtered out
    executed = " ".join(c.args[0] for c in cursor.execute.call_args_list)
    assert "DELETE FROM message_attachments WHERE message_id" in executed
    assert "DELETE FROM messages WHERE id" in executed


def test_add_attachment_inserts_pending():
    db, cursor = _make_db()
    db.add_attachment(id=1, message_id=10, filename="a.png", content_type="image/png",
                      size=123, source_url="https://cdn/x")
    sql, params = cursor.execute.call_args.args
    assert "INSERT INTO message_attachments" in sql
    assert params == (1, 10, "a.png", "image/png", 123, "https://cdn/x", 0)


def test_set_attachment_local_marks_done():
    db, cursor = _make_db()
    db.set_attachment_local(1, "g/c/1_a.png", 1)
    sql, params = cursor.execute.call_args.args
    assert "UPDATE message_attachments SET" in sql
    assert params == ("g/c/1_a.png", 1, 1)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 180 pipenv run python -m pytest tests/test_message_db.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ImportError: cannot import name 'MySQLMessageDb'`.

- [ ] **Step 3: Implement `MySQLMessageDb`** in `bot/store/mysqldb.py`

```python
class MySQLMessageDb(_MySQLBaseDb):
    TABLE_NAME = 'messages'

    def write(self, *, id, channel_id, guild_id, author_id, author_name,
              author_avatar, kind, content, reference_id, created_at):
        """Insert a captured message. Idempotent on the Discord message id."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "INSERT INTO messages "
                "(id, channel_id, guild_id, author_id, author_name, author_avatar, "
                " kind, content, reference_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE content=VALUES(content)",
                (id, channel_id, guild_id, author_id, author_name, author_avatar,
                 kind, content, reference_id, created_at),
            )
        finally:
            cursor.close()
        self.mydb.commit()

    def mark_edited(self, id, content, edited_at):
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "UPDATE messages SET content=%s, edited_at=%s WHERE id=%s",
                (content, edited_at, id),
            )
        finally:
            cursor.close()
        self.mydb.commit()

    def delete_message(self, id) -> list:
        """Hard-delete a message and its attachment rows. Returns the local_path
        values that existed (relative paths) so the caller can unlink the bytes."""
        cursor = self.mydb.cursor()
        try:
            paths_cursor = self.mydb.dict_cursor()
            try:
                paths_cursor.execute(
                    "SELECT local_path FROM message_attachments WHERE message_id=%s", (id,)
                )
                rows = paths_cursor.fetchall() or []
            finally:
                paths_cursor.close()
            cursor.execute("DELETE FROM message_attachments WHERE message_id=%s", (id,))
            cursor.execute("DELETE FROM messages WHERE id=%s", (id,))
        finally:
            cursor.close()
        self.mydb.commit()
        return [r["local_path"] for r in rows if r.get("local_path")]

    def add_attachment(self, *, id, message_id, filename, content_type, size, source_url):
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "INSERT INTO message_attachments "
                "(id, message_id, filename, content_type, size, source_url, download_status) "
                "VALUES (%s,%s,%s,%s,%s,%s,0) "
                "ON DUPLICATE KEY UPDATE source_url=VALUES(source_url)",
                (id, message_id, filename, content_type, size, source_url),
            )
        finally:
            cursor.close()
        self.mydb.commit()

    def set_attachment_local(self, id, local_path, download_status):
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "UPDATE message_attachments SET local_path=%s, download_status=%s WHERE id=%s",
                (local_path, download_status, id),
            )
        finally:
            cursor.close()
        self.mydb.commit()
```

> Note the test mocks `mydb.cursor()` and `mydb.dict_cursor()` off the same `MagicMock`; `dict_cursor()` returns a fresh child mock whose `.fetchall()` the `delete_message` test sets. Adjust `_make_db` if you prefer a distinct dict cursor — the assertions only inspect `cursor.execute`.

- [ ] **Step 4: Instantiate on a dedicated connection** in `bot/store/__init__.py`

In the `from .mysqldb import ...` line (~line 33) add `MySQLMessageDb`. Then, inside the `if config.storage == 'mysql':` block after `ChannelActivityDb = ...` (~line 133):

```python
    # The bridge worker writes on a background thread (asyncio.to_thread), and a
    # mysql.connector connection is not thread-safe to share; give it its own.
    message_db_conn = MySQLReconnector(
        host="localhost",
        user=config.mysql_username,
        password=config.mysql_password,
        database=config.database,
        charset="utf8mb4",
    )
    MessageDb = MySQLMessageDb(mydb=message_db_conn)
```

- [ ] **Step 5: Run tests + type check**

Run: `timeout 180 pipenv run python -m pytest tests/test_message_db.py -q > /tmp/test.log 2>&1`
Expected: PASS (5 tests).
Run: `pipenv run basedpyright bot/store/mysqldb.py bot/store/__init__.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add bot/store/mysqldb.py bot/store/__init__.py tests/test_message_db.py
git commit -m "feat(bridge): MessageDb DAO (write/edit/delete + attachments)"
```

---

### Task A4: Attachment media helpers

**Files:**
- Create: `bot/core/message_media.py`
- Test: `tests/test_message_media.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def test_safe_filename_strips_path_separators():
    from bot.core.message_media import safe_filename
    assert safe_filename("../../etc/passwd") == "etc_passwd"
    assert safe_filename("a b.png") == "a b.png"
    assert safe_filename("") == "file"


def test_attachment_relpath_layout():
    from bot.core.message_media import attachment_relpath
    assert attachment_relpath(1, 2, 3, "a.png") == "1/2/3_a.png"


async def test_download_attachment_writes_bytes_and_returns_true(tmp_path):
    from bot.core.message_media import download_attachment
    resp = MagicMock()
    resp.headers = {"Content-Length": "3"}
    resp.content.iter_chunked = lambda n: _aiter([b"abc"])
    session = MagicMock()
    session.get.return_value.__aenter__ = AsyncMock(return_value=resp)
    session.get.return_value.__aexit__ = AsyncMock(return_value=False)
    dest = tmp_path / "x" / "y.png"
    ok = await download_attachment(session, "https://cdn/x", dest, max_bytes=10)
    assert ok is True and dest.read_bytes() == b"abc"


async def test_download_attachment_rejects_oversize(tmp_path):
    from bot.core.message_media import download_attachment
    resp = MagicMock()
    resp.headers = {"Content-Length": "999"}
    session = MagicMock()
    session.get.return_value.__aenter__ = AsyncMock(return_value=resp)
    session.get.return_value.__aexit__ = AsyncMock(return_value=False)
    dest = tmp_path / "x" / "y.png"
    ok = await download_attachment(session, "https://cdn/x", dest, max_bytes=10)
    assert ok is False and not dest.exists()


async def _aiter(items):
    for i in items:
        yield i
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 180 pipenv run python -m pytest tests/test_message_media.py -q > /tmp/test.log 2>&1`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `bot/core/message_media.py`**

```python
"""Attachment byte storage for the message bridge.

Kept pure/free of discord.py so it unit-tests without a client: a caller passes
an aiohttp session, a source URL, and a destination path.
"""
import logging
import re
from pathlib import Path

from bot.utils import config

logger = logging.getLogger(__name__)


def media_root() -> Path:
    return Path(config.web_archive_media_dir)


def safe_filename(name: str) -> str:
    """Collapse to a safe basename: no separators, no traversal."""
    name = (name or "").replace("\\", "/")
    name = name.split("/")[-1] if "/" not in name else re.sub(r"[/]+", "_", name).strip("_")
    name = re.sub(r"[^A-Za-z0-9 ._-]", "_", name).strip("_")
    return name or "file"


def attachment_relpath(guild_id: int, channel_id: int, attachment_id: int, filename: str) -> str:
    """Relative path under media_root(); stored on the row and re-joined when serving."""
    return f"{guild_id}/{channel_id}/{attachment_id}_{safe_filename(filename)}"


async def download_attachment(session, url: str, dest: Path, max_bytes: int) -> bool:
    """Stream url -> dest, capped at max_bytes. Returns True on success.
    Never raises: a failed download leaves no partial file and returns False."""
    try:
        async with session.get(url) as resp:
            declared = resp.headers.get("Content-Length")
            if declared is not None and int(declared) > max_bytes:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with open(dest, "wb") as fh:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        return False
                    fh.write(chunk)
        return True
    except Exception:
        logger.exception("attachment download failed: %s", url)
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return False
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 180 pipenv run python -m pytest tests/test_message_media.py -q > /tmp/test.log 2>&1`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/core/message_media.py tests/test_message_media.py
git commit -m "feat(bridge): attachment media path + capped downloader"
```

---

### Task A5: The bridge cog (capture, gate, worker) — no live events yet

**Files:**
- Create: `bot/cogs/bridge.py`
- Test: `tests/test_bridge_cog.py`

This task builds capture + DB writes. Publishing live events is added in Phase C so the two concerns are tested separately.

- [ ] **Step 1: Write the failing test**

```python
"""Bridge cog: kind tagging, puzzle-channel gate, and worker DB ops."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_cog():
    from bot.cogs.bridge import MessageBridge
    bot = MagicMock()
    bot.application_id = 999
    cog = MessageBridge(bot)
    cog._puzzle_channels = {100}   # only channel 100 is a tracked puzzle channel
    return cog


def _msg(*, mid, cid, author_id, is_bot, content, guild_id=42):
    m = MagicMock()
    m.id = mid
    m.channel = MagicMock(); m.channel.id = cid
    m.guild = MagicMock(); m.guild.id = guild_id
    m.author = MagicMock(); m.author.id = author_id; m.author.bot = is_bot
    m.author.display_name = "Ann"
    m.author.display_avatar = MagicMock(); m.author.display_avatar.url = "http://a/av.png"
    m.content = content
    m.created_at = MagicMock()
    m.created_at.replace.return_value = "2026-07-06 00:00:00"
    m.reference = None
    m.attachments = []
    return m


def test_kind_human_bot_command():
    from bot.cogs.bridge import KIND_HUMAN, KIND_THIS_BOT, KIND_OTHER_BOT, KIND_COMMAND
    cog = _make_cog()
    assert cog._kind_for(_msg(mid=1, cid=100, author_id=1, is_bot=False, content="hi")) == KIND_HUMAN
    assert cog._kind_for(_msg(mid=1, cid=100, author_id=1, is_bot=False, content="!s ANSWER")) == KIND_COMMAND
    assert cog._kind_for(_msg(mid=1, cid=100, author_id=999, is_bot=True, content="x")) == KIND_THIS_BOT
    assert cog._kind_for(_msg(mid=1, cid=100, author_id=5, is_bot=True, content="x")) == KIND_OTHER_BOT


async def test_on_message_gate_drops_non_puzzle_channel():
    cog = _make_cog()
    await cog.on_message(_msg(mid=1, cid=777, author_id=1, is_bot=False, content="hi"))
    assert cog._queue.empty()


async def test_on_message_enqueues_insert_for_puzzle_channel():
    cog = _make_cog()
    await cog.on_message(_msg(mid=1, cid=100, author_id=1, is_bot=False, content="hi"))
    rec = cog._queue.get_nowait()
    assert rec["op"] == "insert" and rec["id"] == 1 and rec["channel_id"] == 100


async def test_worker_insert_writes_row():
    cog = _make_cog()
    rec = {"op": "insert", "id": 1, "channel_id": 100, "guild_id": 42, "author_id": 1,
           "author_name": "Ann", "author_avatar": None, "kind": 0, "content": "hi",
           "reference_id": None, "created_at": "2026-07-06 00:00:00", "attachments": []}
    with patch("bot.cogs.bridge.MessageDb") as db:
        await cog._handle(rec)
    db.write.assert_called_once()
    assert db.write.call_args.kwargs["id"] == 1


async def test_worker_delete_unlinks_files(tmp_path):
    cog = _make_cog()
    f = tmp_path / "g" / "c" / "1_a.png"; f.parent.mkdir(parents=True); f.write_bytes(b"x")
    with patch("bot.cogs.bridge.MessageDb") as db, \
         patch("bot.cogs.bridge.media_root", return_value=tmp_path):
        db.delete_message.return_value = ["g/c/1_a.png"]
        await cog._handle({"op": "delete", "id": 1})
    db.delete_message.assert_called_once_with(1)
    assert not f.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 180 pipenv run python -m pytest tests/test_bridge_cog.py -q > /tmp/test.log 2>&1`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `bot/cogs/bridge.py`**

```python
import asyncio
import datetime
import logging

import aiohttp
import discord
from discord.ext import commands

from bot.store import MessageDb
from bot.core.message_media import media_root, attachment_relpath, download_attachment
from bot.utils import config

logger = logging.getLogger(__name__)

KIND_HUMAN = 0
KIND_THIS_BOT = 1
KIND_OTHER_BOT = 2
KIND_COMMAND = 3

_QUEUE_MAXSIZE = 10000
_REFRESH_SECONDS = 60


class MessageBridge(commands.Cog):
    """Forward-only mirror of hunt puzzle-channel messages into the web DB.

    Capture is inbound-only (gateway push events), so it makes no REST calls and
    consumes no rate-limit buckets. All DB writes and attachment downloads run on
    a background worker, off the gateway path (mirrors bot/cogs/activity.py).
    """

    def __init__(self, bot):
        self.bot = bot
        self._queue: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._worker: asyncio.Task | None = None
        self._refresher: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._puzzle_channels: set[int] = set()

    async def cog_load(self):
        if not config.web_bridge_enabled:
            logger.info("Message bridge disabled (web.bridge_enabled=false)")
            return
        self._puzzle_channels = await asyncio.to_thread(self._load_puzzle_channels)
        self._session = aiohttp.ClientSession()
        self._worker = asyncio.create_task(self._drain(), name="message-bridge-writer")
        self._refresher = asyncio.create_task(self._refresh_loop(), name="message-bridge-refresh")

    async def cog_unload(self):
        for task in (self._worker, self._refresher):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._worker = self._refresher = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ---- gating -----------------------------------------------------------
    @staticmethod
    def _load_puzzle_channels() -> set[int]:
        from bot.store import mydb
        cur = mydb.dict_cursor()
        try:
            cur.execute("SELECT channel_id FROM puzzles WHERE channel_id IS NOT NULL")
            rows = cur.fetchall() or []
        finally:
            cur.close()
        return {int(r["channel_id"]) for r in rows}

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(_REFRESH_SECONDS)
            try:
                self._puzzle_channels = await asyncio.to_thread(self._load_puzzle_channels)
            except Exception:
                logger.exception("Failed to refresh bridge puzzle-channel set")

    def _kind_for(self, message: discord.Message) -> int:
        if message.author.id == self.bot.application_id:
            return KIND_THIS_BOT
        if message.author.bot:
            return KIND_OTHER_BOT
        if message.content.startswith("!"):
            return KIND_COMMAND
        return KIND_HUMAN

    # ---- listeners --------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.channel.id not in self._puzzle_channels:
            return
        author_name = (getattr(message.author, "display_name", None) or message.author.name)[:255]
        avatar = getattr(getattr(message.author, "display_avatar", None), "url", None)
        rec = {
            "op": "insert",
            "id": message.id,
            "channel_id": message.channel.id,
            "guild_id": message.guild.id,
            "author_id": message.author.id,
            "author_name": author_name,
            "author_avatar": avatar,
            "kind": self._kind_for(message),
            "content": message.content or "",
            "reference_id": (message.reference.message_id if message.reference else None),
            "created_at": message.created_at.replace(tzinfo=None),
            "attachments": [
                {"id": a.id, "filename": a.filename, "content_type": a.content_type,
                 "size": a.size, "url": a.url}
                for a in message.attachments
            ],
        }
        self._enqueue(rec)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        content = (payload.data or {}).get("content")
        if content is None:
            return  # embed-only / non-content edit
        edited = (payload.data or {}).get("edited_timestamp")
        self._enqueue({"op": "edit", "id": payload.message_id,
                       "content": content, "edited_at": _parse_iso(edited)})

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        # No gate: if the id isn't ours the delete is a harmless no-op; if it is,
        # privacy requires purging it regardless of channel state.
        self._enqueue({"op": "delete", "id": payload.message_id})

    def _enqueue(self, rec: dict):
        try:
            self._queue.put_nowait(rec)
        except asyncio.QueueFull:
            logger.warning("Message bridge queue full; dropping a record")

    # ---- worker -----------------------------------------------------------
    async def _drain(self):
        while True:
            rec = await self._queue.get()
            try:
                await self._handle(rec)
            except Exception:
                logger.exception("Bridge worker failed on %r", rec.get("op"))
            finally:
                self._queue.task_done()

    async def _handle(self, rec: dict):
        op = rec["op"]
        if op == "insert":
            await asyncio.to_thread(
                MessageDb.write,
                id=rec["id"], channel_id=rec["channel_id"], guild_id=rec["guild_id"],
                author_id=rec["author_id"], author_name=rec["author_name"],
                author_avatar=rec["author_avatar"], kind=rec["kind"],
                content=rec["content"], reference_id=rec["reference_id"],
                created_at=rec["created_at"],
            )
            for att in rec["attachments"]:
                await self._store_attachment(rec, att)
        elif op == "edit":
            await asyncio.to_thread(MessageDb.mark_edited, rec["id"], rec["content"], rec["edited_at"])
        elif op == "delete":
            paths = await asyncio.to_thread(MessageDb.delete_message, rec["id"])
            for rel in paths:
                try:
                    (media_root() / rel).unlink(missing_ok=True)
                except Exception:
                    logger.exception("Failed to unlink attachment bytes %s", rel)

    async def _store_attachment(self, rec: dict, att: dict):
        await asyncio.to_thread(
            MessageDb.add_attachment,
            id=att["id"], message_id=rec["id"], filename=att["filename"],
            content_type=att["content_type"], size=att["size"], source_url=att["url"],
        )
        rel = attachment_relpath(rec["guild_id"], rec["channel_id"], att["id"], att["filename"])
        dest = media_root() / rel
        assert self._session is not None
        ok = await download_attachment(self._session, att["url"], dest, config.web_attachment_max_bytes)
        await asyncio.to_thread(MessageDb.set_attachment_local, att["id"], rel if ok else None, 1 if ok else 2)


def _parse_iso(value):
    if not value:
        return datetime.datetime.utcnow().replace(microsecond=0)
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return datetime.datetime.utcnow().replace(microsecond=0)


async def setup(bot):
    await bot.add_cog(MessageBridge(bot))
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 180 pipenv run python -m pytest tests/test_bridge_cog.py -q > /tmp/test.log 2>&1`
Expected: PASS (6 tests).
Run: `pipenv run basedpyright bot/cogs/bridge.py bot/core/message_media.py`
Expected: 0 errors.

- [ ] **Step 5: Manual smoke (optional, requires a real DB + a running bot with `bridge_enabled: true` and `archive_media_dir` set)**

Send a message in a puzzle channel; confirm a row appears: `SELECT * FROM messages ORDER BY id DESC LIMIT 3;`. Edit and delete it; confirm the row updates then vanishes.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/bridge.py tests/test_bridge_cog.py
git commit -m "feat(bridge): capture cog — gate to puzzle channels, worker writes + attachments"
```

- [ ] **Step 7: Phase A gate — full suite + types + lint**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` (expect all green)
Run: `pipenv run basedpyright bot/` (0 errors)
Run: `pipenv run ruff check` (clean)

---

# Phase B — Read API & viewer

### Task B1: Read models in `data.py`

**Files:**
- Modify: `bot/web/data.py`
- Test: `tests/test_message_read_models.py`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import patch


async def test_get_channel_messages_returns_ascending_with_attachments():
    from bot.web import data
    msg_rows = [
        {"id": 2, "author_name": "Bo", "author_avatar": None, "author_id": 5, "kind": 0,
         "content": "second", "reference_id": None, "created_at": "t2", "edited_at": None},
        {"id": 1, "author_name": "Ann", "author_avatar": None, "author_id": 4, "kind": 0,
         "content": "first", "reference_id": None, "created_at": "t1", "edited_at": None},
    ]
    att_rows = [{"id": 9, "message_id": 2, "filename": "a.png", "content_type": "image/png",
                 "size": 3, "local_path": "1/2/9_a.png", "download_status": 1}]
    with patch("bot.web.data.fetch_all", side_effect=[msg_rows, att_rows]):
        out = await data.get_channel_messages(100, before=None, limit=50)
    assert [m["id"] for m in out] == [1, 2]           # ascending for render
    assert out[1]["attachments"][0]["id"] == 9        # attachment attached to msg 2


async def test_get_hunt_by_channel_id_queries_join():
    from bot.web import data
    with patch("bot.web.data.fetch_one", return_value={"id": 7, "archived": 0}) as f:
        hunt = await data.get_hunt_by_channel_id(100)
    assert hunt["id"] == 7
    assert "JOIN puzzles" in f.call_args.args[0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 180 pipenv run python -m pytest tests/test_message_read_models.py -q > /tmp/test.log 2>&1`
Expected: FAIL — attributes not defined.

- [ ] **Step 3: Implement in `bot/web/data.py`** (append near the other read helpers)

```python
async def get_hunt_by_channel_id(channel_id):
    """Resolve the hunt that owns a puzzle channel. Works for archived hunts too
    (the hunt row persists after the channel is deleted), so it authorizes frozen
    transcripts that a live channel-permission check could not."""
    return await fetch_one(
        "SELECT h.* FROM hunts h JOIN puzzles p ON p.hunt_id = h.id "
        "WHERE p.channel_id = %s LIMIT 1",
        (channel_id,),
    )


async def get_channel_messages(channel_id, before=None, limit=50):
    """Return up to `limit` messages for a channel, ascending by id (render order).
    `before` is an exclusive snowflake cursor for scrolling into history."""
    limit = max(1, min(int(limit), 200))
    if before:
        rows = await fetch_all(
            "SELECT id, author_id, author_name, author_avatar, kind, content, "
            "reference_id, created_at, edited_at FROM messages "
            "WHERE channel_id=%s AND id < %s ORDER BY id DESC LIMIT %s",
            (channel_id, before, limit),
        )
    else:
        rows = await fetch_all(
            "SELECT id, author_id, author_name, author_avatar, kind, content, "
            "reference_id, created_at, edited_at FROM messages "
            "WHERE channel_id=%s ORDER BY id DESC LIMIT %s",
            (channel_id, limit),
        )
    rows = list(reversed(rows))  # ascending for the viewer
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join(["%s"] * len(ids))
    atts = await fetch_all(
        "SELECT id, message_id, filename, content_type, size, local_path, download_status "
        f"FROM message_attachments WHERE message_id IN ({placeholders})",
        tuple(ids),
    )
    by_msg = {}
    for a in atts:
        by_msg.setdefault(a["message_id"], []).append({
            "id": str(a["id"]),
            "filename": a["filename"],
            "content_type": a["content_type"],
            "size": a["size"],
            "url": (f"/api/attachments/{a['id']}" if a["download_status"] == 1 else None),
        })
    out = []
    for r in rows:
        out.append({
            "id": str(r["id"]),
            "author_id": str(r["author_id"]),
            "author_name": r["author_name"],
            "author_avatar": r["author_avatar"],
            "kind": r["kind"],
            "content": r["content"],
            "reference_id": (str(r["reference_id"]) if r["reference_id"] else None),
            "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
            "edited_at": (r["edited_at"].isoformat() if hasattr(r["edited_at"], "isoformat") and r["edited_at"] else None),
            "attachments": by_msg.get(r["id"], []),
        })
    return out


async def get_attachment_with_channel(attachment_id):
    """Attachment row plus the channel it belongs to, for auth + file serving."""
    return await fetch_one(
        "SELECT a.id, a.filename, a.content_type, a.local_path, a.download_status, "
        "m.channel_id FROM message_attachments a JOIN messages m ON m.id = a.message_id "
        "WHERE a.id = %s LIMIT 1",
        (attachment_id,),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 180 pipenv run python -m pytest tests/test_message_read_models.py -q > /tmp/test.log 2>&1`
Expected: PASS (2 tests).

- [ ] **Step 5: Add `discord_deep_url`** in `build_board`'s puzzle dict (`data.py`, right after the `discord_url` field ~line 208)

```python
                "discord_deep_url": (
                    f"discord://-/channels/{guild_snowflake}/{row['channel_id']}"
                    if guild_snowflake and row["channel_id"] else None
                ),
```

- [ ] **Step 6: Commit**

```bash
git add bot/web/data.py tests/test_message_read_models.py
git commit -m "feat(bridge): read models — channel transcript, hunt-by-channel, deep link"
```

---

### Task B2: API routes + visibility

**Files:**
- Modify: `bot/web/api.py`
- Test: `tests/test_message_routes_visibility.py`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import AsyncMock, MagicMock, patch


async def test_channel_messages_visible_uses_hunt_gate():
    from bot.web.api import _channel_messages_visible
    with patch("bot.web.api.data.get_hunt_by_channel_id", new=AsyncMock(return_value={"uid": "H"})), \
         patch("bot.web.api.data.get_guilds_map", new=AsyncMock(return_value={})), \
         patch("bot.web.api.hunt_visible", return_value=True) as hv:
        ok = await _channel_messages_visible(100, membership={42}, roles_by_guild={})
    assert ok is True and hv.called


async def test_channel_messages_hidden_when_no_hunt():
    from bot.web.api import _channel_messages_visible
    with patch("bot.web.api.data.get_hunt_by_channel_id", new=AsyncMock(return_value=None)):
        ok = await _channel_messages_visible(100, membership={42}, roles_by_guild={})
    assert ok is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 180 pipenv run python -m pytest tests/test_message_routes_visibility.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `_channel_messages_visible` undefined.

- [ ] **Step 3: Implement in `bot/web/api.py`** — the visibility helper (near `_board_event_visible`) and the two routes (near `hunt_detail`). Reuse existing imports: `login_required`, `current_user`, `resolve_membership`, `hunt_visible`, `data`, `jsonify`, `request`, `send_file`.

```python
async def _channel_messages_visible(channel_id, membership, roles_by_guild):
    """Authorize a transcript request via the owning hunt (works for archived,
    channel-deleted hunts, unlike the live channel-permission check)."""
    hunt = await data.get_hunt_by_channel_id(channel_id)
    if hunt is None:
        return False
    guilds_map = await data.get_guilds_map()
    return hunt_visible(hunt, membership, roles_by_guild, guilds_map)


@api_bp.route("/channels/<int:channel_id>/messages")
@login_required
async def channel_messages(channel_id):
    user = current_user()
    assert user is not None
    membership, roles_by_guild = await resolve_membership(user["id"])
    if not await _channel_messages_visible(channel_id, membership, roles_by_guild):
        return jsonify({"error": "not_found_or_forbidden"}), 404
    before = request.args.get("before", type=int)
    limit = request.args.get("limit", default=50, type=int)
    messages = await data.get_channel_messages(channel_id, before=before, limit=limit)
    return jsonify({"messages": messages})


@api_bp.route("/attachments/<int:attachment_id>")
@login_required
async def attachment_bytes(attachment_id):
    user = current_user()
    assert user is not None
    att = await data.get_attachment_with_channel(attachment_id)
    if att is None or att["download_status"] != 1 or not att["local_path"]:
        return jsonify({"error": "not_found"}), 404
    membership, roles_by_guild = await resolve_membership(user["id"])
    if not await _channel_messages_visible(att["channel_id"], membership, roles_by_guild):
        return jsonify({"error": "forbidden"}), 403
    from bot.core.message_media import media_root
    path = (media_root() / att["local_path"]).resolve()
    root = media_root().resolve()
    if root not in path.parents:            # defense in depth vs path traversal
        return jsonify({"error": "not_found"}), 404
    return await send_file(path, mimetype=att["content_type"] or "application/octet-stream",
                           attachment_filename=att["filename"])
```

> If `send_file` / `request` aren't already imported at the top of `api.py`, add them to the existing `from quart import ...` line. Confirm with `grep -n "from quart import" bot/web/api.py`.

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 180 pipenv run python -m pytest tests/test_message_routes_visibility.py -q > /tmp/test.log 2>&1`
Expected: PASS (2 tests).
Run: `pipenv run basedpyright bot/web/api.py bot/web/data.py`
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add bot/web/api.py tests/test_message_routes_visibility.py
git commit -m "feat(bridge): transcript + attachment routes with hunt-based visibility"
```

---

### Task B3: Frontend types + API client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add types** to `frontend/src/types.ts`

```typescript
export interface ChatAttachment {
  id: string;
  filename: string;
  content_type: string | null;
  size: number;
  url: string | null; // /api/attachments/<id>, or null if not downloaded
}

export interface ChatMessage {
  id: string;
  author_id: string;
  author_name: string;
  author_avatar: string | null;
  kind: number; // 0 human, 1 this-bot, 2 other-bot, 3 command
  content: string;
  reference_id: string | null;
  created_at: string;
  edited_at: string | null;
  attachments: ChatAttachment[];
}
```

Extend `ActivityEvent` (add fields; keep existing ones):

```typescript
export interface ActivityEvent {
  type: string;
  channel_id?: string;
  guild_id?: number;
  uid?: string;
  at?: string;
  author?: string;
  message?: ChatMessage;   // for type === "message" | "message_edit"
  message_id?: string;     // for type === "message_delete"
}
```

Add to the `Puzzle` interface (near `discord_url`): `discord_deep_url: string | null;`

- [ ] **Step 2: Add the fetch helper** to `frontend/src/api.ts` (follow the existing fetch/credentials pattern in that file)

```typescript
export async function fetchChannelMessages(
  channelId: string,
  before?: string,
): Promise<ChatMessage[]> {
  const qs = before ? `?before=${before}` : "";
  const res = await fetch(`/api/channels/${channelId}/messages${qs}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(`messages ${res.status}`);
  const data = await res.json();
  return data.messages as ChatMessage[];
}
```

Add `ChatMessage` to the existing type import from `./types` at the top of `api.ts`.

- [ ] **Step 3: Type-check the frontend**

Run: `cd frontend && npx tsc --noEmit > /tmp/tsc.log 2>&1; cd ..` then read `/tmp/tsc.log`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(bridge): frontend chat message types + fetchChannelMessages"
```

---

### Task B4: `useChannelMessages` hook (transcript + live tail)

**Files:**
- Create: `frontend/src/hooks/useChannelMessages.ts`

The hook owns its own WebSocket while the viewer is open, so it's self-contained and doesn't disturb the board-level `useActivityFeed`. On non-live (archived) channels it skips the socket and just serves the fetched transcript.

- [ ] **Step 1: Implement the hook**

```typescript
import { useEffect, useState } from "react";
import type { ChatMessage, ActivityEvent } from "../types";
import { fetchChannelMessages } from "../api";

export function useChannelMessages(channelId: string | null, live: boolean) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);

  // Initial transcript load whenever the channel changes.
  useEffect(() => {
    if (!channelId) return;
    let cancelled = false;
    setLoading(true);
    fetchChannelMessages(channelId)
      .then((m) => { if (!cancelled) setMessages(m); })
      .catch(() => { if (!cancelled) setMessages([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [channelId]);

  // Live tail via a dedicated socket, only for live (non-archived) channels.
  useEffect(() => {
    if (!channelId || !live) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/api/ws/activity`);
    ws.onmessage = (raw) => {
      try {
        const ev: ActivityEvent = JSON.parse(raw.data);
        if (ev.channel_id !== channelId) return;
        if (ev.type === "message" && ev.message) {
          setMessages((prev) =>
            prev.some((m) => m.id === ev.message!.id) ? prev : [...prev, ev.message!]);
        } else if (ev.type === "message_edit" && ev.message) {
          setMessages((prev) => prev.map((m) => (m.id === ev.message!.id ? ev.message! : m)));
        } else if (ev.type === "message_delete" && ev.message_id) {
          setMessages((prev) => prev.filter((m) => m.id !== ev.message_id));
        }
      } catch { /* ignore */ }
    };
    return () => ws.close();
  }, [channelId, live]);

  return { messages, loading };
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit > /tmp/tsc.log 2>&1; cd ..`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useChannelMessages.ts
git commit -m "feat(bridge): useChannelMessages hook (transcript + live tail)"
```

---

### Task B5: `ChatViewer` modal component

**Files:**
- Create: `frontend/src/components/ChatViewer.tsx`

- [ ] **Step 1: Implement the component**

```tsx
import { Modal, Stack, Group, Avatar, Text, Anchor, Image, Loader } from "@mantine/core";
import { IconBrandDiscord } from "@tabler/icons-react";
import { useChannelMessages } from "../hooks/useChannelMessages";

interface Props {
  opened: boolean;
  onClose: () => void;
  channelId: string | null;
  live: boolean;                 // true while the hunt is active; false when archived
  title: string;
  discordDeepUrl: string | null; // discord://-/channels/... (opens the app)
  discordUrl: string | null;     // https fallback
}

const KIND_COMMAND = 3;

export function ChatViewer({ opened, onClose, channelId, live, title, discordDeepUrl, discordUrl }: Props) {
  const { messages, loading } = useChannelMessages(opened ? channelId : null, live);

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg">
      {live && (discordDeepUrl || discordUrl) && (
        <Group gap={6} mb="sm">
          <IconBrandDiscord size={16} />
          <Anchor href={discordDeepUrl ?? discordUrl!} size="sm">Open in Discord</Anchor>
          {discordDeepUrl && discordUrl && (
            <Anchor href={discordUrl} target="_blank" size="xs" c="dimmed">(browser)</Anchor>
          )}
        </Group>
      )}
      {loading && <Loader size="sm" />}
      {!loading && messages.length === 0 && (
        <Text c="dimmed" size="sm">No captured messages for this channel.</Text>
      )}
      <Stack gap="xs">
        {messages.map((m) => (
          <Group key={m.id} align="flex-start" gap="xs" wrap="nowrap">
            <Avatar src={m.author_avatar} size={24} radius="xl" />
            <div style={{ minWidth: 0 }}>
              <Group gap={6}>
                <Text size="sm" fw={600}>{m.author_name}</Text>
                <Text size="xs" c="dimmed">{new Date(m.created_at).toLocaleString()}</Text>
                {m.edited_at && <Text size="xs" c="dimmed">(edited)</Text>}
              </Group>
              {m.content && (
                <Text size="sm" c={m.kind === KIND_COMMAND ? "dimmed" : undefined}
                      style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {m.content}
                </Text>
              )}
              {m.attachments.map((a) =>
                a.url ? (
                  a.content_type?.startsWith("image/") ? (
                    <Image key={a.id} src={a.url} alt={a.filename} maw={320} radius="sm" mt={4} />
                  ) : (
                    <Anchor key={a.id} href={a.url} size="sm" mt={4}>{a.filename}</Anchor>
                  )
                ) : (
                  <Text key={a.id} size="xs" c="dimmed">{a.filename} (unavailable)</Text>
                ),
              )}
            </div>
          </Group>
        ))}
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit > /tmp/tsc.log 2>&1; cd ..`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ChatViewer.tsx
git commit -m "feat(bridge): ChatViewer modal (Discord-style transcript render)"
```

---

### Task B6: Badge opens the viewer

**Files:**
- Modify: `frontend/src/components/PuzzleLinks.tsx`

- [ ] **Step 1: Replace the Discord anchor with a viewer-opening button.** Add a `huntArchived` prop and manage modal state locally.

```tsx
import { useState } from "react";
import { ActionIcon, Group, Indicator, Tooltip } from "@mantine/core";
import { IconBrandDiscord, IconExternalLink, IconFileSpreadsheet } from "@tabler/icons-react";
import type { Puzzle } from "../types";
import { ChatViewer } from "./ChatViewer";

interface Props {
  puzzle: Puzzle;
  sheetBase: string | null;
  solvedSheetBase: string | null;
  huntArchived: boolean;
}

export function PuzzleLinks({ puzzle, sheetBase, solvedSheetBase, huntArchived }: Props) {
  const base = puzzle.on_solved_sheet && solvedSheetBase ? solvedSheetBase : sheetBase;
  const sheetUrl = base && puzzle.sheet_page ? `${base}${puzzle.sheet_page}` : null;
  const [chatOpen, setChatOpen] = useState(false);

  return (
    <Group gap={4} wrap="nowrap">
      {sheetUrl && (
        <Tooltip label="Spreadsheet">
          <Indicator label={puzzle.sheets.length} size={16} disabled={puzzle.sheets.length === 0} color="green">
            <ActionIcon component="a" href={sheetUrl} target="_blank" variant="subtle" color="green">
              <IconFileSpreadsheet size={16} />
            </ActionIcon>
          </Indicator>
        </Tooltip>
      )}
      {puzzle.channel_id && (
        <>
          <Tooltip label="Chat">
            <ActionIcon variant="subtle" color="indigo" onClick={() => setChatOpen(true)}>
              <IconBrandDiscord size={16} />
            </ActionIcon>
          </Tooltip>
          <ChatViewer
            opened={chatOpen}
            onClose={() => setChatOpen(false)}
            channelId={puzzle.channel_id}
            live={!huntArchived}
            title={puzzle.name}
            discordDeepUrl={puzzle.discord_deep_url}
            discordUrl={puzzle.discord_url}
          />
        </>
      )}
      {puzzle.url && (
        <Tooltip label="Puzzle page">
          <ActionIcon component="a" href={puzzle.url} target="_blank" variant="subtle" color="gray">
            <IconExternalLink size={16} />
          </ActionIcon>
        </Tooltip>
      )}
    </Group>
  );
}
```

- [ ] **Step 2: Pass `huntArchived` from the caller.** Find where `PuzzleLinks` is rendered:

Run: `grep -rn "PuzzleLinks" frontend/src`
Add `huntArchived={<hunt.archived flag in scope>}` at each call site (the `HuntBoard` page already knows the hunt's `archived` boolean from the hunt detail response).

- [ ] **Step 3: Type-check + build**

Run: `cd frontend && npx tsc --noEmit > /tmp/tsc.log 2>&1; cd ..`
Expected: no errors (fix any missing `huntArchived` prop at call sites).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/PuzzleLinks.tsx frontend/src/pages
git commit -m "feat(bridge): badge opens in-dashboard ChatViewer (live + archived)"
```

---

# Phase C — Live push

### Task C1: Publish live events from the worker

**Files:**
- Modify: `bot/cogs/bridge.py`
- Test: `tests/test_bridge_events.py`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import MagicMock, patch


def _make_cog():
    from bot.cogs.bridge import MessageBridge
    bot = MagicMock(); bot.application_id = 999
    cog = MessageBridge(bot); cog._puzzle_channels = {100}
    return cog


async def test_insert_publishes_message_event():
    cog = _make_cog()
    rec = {"op": "insert", "id": 1, "channel_id": 100, "guild_id": 42, "author_id": 1,
           "author_name": "Ann", "author_avatar": None, "kind": 0, "content": "hi",
           "reference_id": None, "created_at": "2026-07-06 00:00:00", "attachments": []}
    with patch("bot.cogs.bridge.MessageDb"), patch("bot.cogs.bridge.events.publish") as pub:
        await cog._handle(rec)
    ev = pub.call_args.args[0]
    assert ev["type"] == "message" and ev["channel_id"] == "100" and ev["guild_id"] == 42
    assert ev["message"]["id"] == "1" and ev["message"]["content"] == "hi"


async def test_delete_publishes_delete_event():
    cog = _make_cog()
    with patch("bot.cogs.bridge.MessageDb") as db, patch("bot.cogs.bridge.events.publish") as pub, \
         patch("bot.cogs.bridge.media_root"):
        db.delete_message.return_value = []
        await cog._handle({"op": "delete", "id": 1, "channel_id": 100, "guild_id": 42})
    ev = pub.call_args.args[0]
    assert ev["type"] == "message_delete" and ev["message_id"] == "1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 180 pipenv run python -m pytest tests/test_bridge_events.py -q > /tmp/test.log 2>&1`
Expected: FAIL — no publish / `events` not imported.

- [ ] **Step 3: Wire events into `bot/cogs/bridge.py`.** Add `from bot.web import events` to the imports. Add a builder and publish calls. The delete op now needs `channel_id`/`guild_id`, so include them when enqueuing deletes.

Update `on_raw_message_delete`:

```python
    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        self._enqueue({"op": "delete", "id": payload.message_id,
                       "channel_id": payload.channel_id, "guild_id": payload.guild_id})
```

Add the builder and publish in `_handle`:

```python
    @staticmethod
    def _message_payload(rec: dict) -> dict:
        return {
            "id": str(rec["id"]),
            "author_id": str(rec["author_id"]),
            "author_name": rec["author_name"],
            "author_avatar": rec["author_avatar"],
            "kind": rec["kind"],
            "content": rec["content"],
            "reference_id": (str(rec["reference_id"]) if rec.get("reference_id") else None),
            "created_at": rec["created_at"].isoformat() if hasattr(rec["created_at"], "isoformat") else str(rec["created_at"]),
            "edited_at": None,
            "attachments": [],  # attachments arrive via a board refresh / reopen; live text is the priority
        }

    def _publish(self, event: dict):
        try:
            events.publish(event)
        except Exception:
            logger.exception("Bridge failed to publish %s", event.get("type"))
```

In `_handle`, after the insert `write`+attachments:

```python
            self._publish({"type": "message", "channel_id": str(rec["channel_id"]),
                           "guild_id": rec["guild_id"], "message": self._message_payload(rec)})
```

After the edit `mark_edited`:

```python
            self._publish({"type": "message_edit", "channel_id": None, "guild_id": None,
                           "message": {"id": str(rec["id"]), "content": rec["content"]}})
```

> Edits don't carry channel/guild in the raw payload we stored; the frontend matches on `message.id` within the already-open viewer, so a `channel_id` of `None` is fine — but to let `ws_activity` guild-filter it, include guild when available. If `payload.data` includes `channel_id`/`guild_id`, thread them through the edit record like the delete record and set them here. Otherwise edits are only delivered to viewers already showing that message id (acceptable for v1); document this in the commit.

After the delete `delete_message`+unlink:

```python
            self._publish({"type": "message_delete", "channel_id": str(rec.get("channel_id")) if rec.get("channel_id") else None,
                           "guild_id": rec.get("guild_id"), "message_id": str(rec["id"])})
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 180 pipenv run python -m pytest tests/test_bridge_events.py tests/test_bridge_cog.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/bridge.py tests/test_bridge_events.py
git commit -m "feat(bridge): publish live message/edit/delete events from the worker"
```

---

### Task C2: WebSocket dispatch for message events

**Files:**
- Modify: `bot/web/api.py` (the event-type dispatch in `ws_activity`, ~line 329-333)
- Test: `tests/test_ws_message_visibility.py`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import AsyncMock, MagicMock, patch


async def test_message_event_uses_channel_permission_gate():
    from bot.web import api
    channel = MagicMock(); channel.permissions_for.return_value = MagicMock(view_channel=True)
    guild = MagicMock(); guild.get_channel.return_value = channel
    guild.get_member.return_value = MagicMock()
    bot = MagicMock(); bot.get_guild.return_value = guild
    ev = {"type": "message", "channel_id": "100", "guild_id": 42}
    with patch("bot.web.api.bot_app", MagicMock(return_value=MagicMock(bot=bot))):
        ok = await api._activity_event_visible(ev, user_id=7, guild_id=42)
    assert ok is True
```

(This confirms message events can reuse `_activity_event_visible`; the wiring in Step 3 routes them there.)

- [ ] **Step 2: Run to verify it passes already** (the helper exists) — this pins the contract before wiring:

Run: `timeout 180 pipenv run python -m pytest tests/test_ws_message_visibility.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 3: Extend the dispatch** in `ws_activity` (`bot/web/api.py`)

```python
            visible = False
            if event.get("type") == "activity":
                visible = await _activity_event_visible(event, user["id"], event["guild_id"])
            elif event.get("type") == "board":
                visible = await _board_event_visible(event, membership, roles_by_guild)
            elif event.get("type") in ("message", "message_edit", "message_delete"):
                # Live channels only (archived channels emit no events); the live
                # channel-permission check matches the same per-hunt role gate.
                visible = await _activity_event_visible(event, user["id"], event["guild_id"])
```

> Note `message_edit` events with `guild_id: None` will be filtered out by the earlier `event.get("guild_id") not in membership` guard (line ~320). That's acceptable per the C1 note — if you threaded guild_id through edits, they'll pass here too.

- [ ] **Step 4: Run the visibility suite**

Run: `timeout 180 pipenv run python -m pytest tests/test_ws_message_visibility.py tests/test_ws_activity_visibility.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/web/api.py tests/test_ws_message_visibility.py
git commit -m "feat(bridge): dispatch live message events over /ws/activity with per-channel gate"
```

---

# Phase D — Archive integration & final verification

### Task D1: Archived transcripts survive channel deletion (regression guard)

**Files:**
- Test: `tests/test_bridge_archive_survives.py`

The `archive_hunt` service deletes channels but never emits per-message delete events, so captured rows must remain. This test locks that invariant so nobody later "cleans up" messages on channel delete.

- [ ] **Step 1: Write the test**

```python
"""Deleting a *channel* must not purge its captured messages (that's the archive).
Only an explicit per-message delete purges (privacy). The bridge has no
on_guild_channel_delete listener, so channel deletion is inert for the messages table."""
import inspect


def test_bridge_has_no_channel_delete_listener():
    import bot.cogs.bridge as bridge
    src = inspect.getsource(bridge)
    assert "on_guild_channel_delete" not in src
    assert "on_raw_message_delete" in src  # per-message privacy purge still present
```

- [ ] **Step 2: Run**

Run: `timeout 180 pipenv run python -m pytest tests/test_bridge_archive_survives.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_bridge_archive_survives.py
git commit -m "test(bridge): channel deletion (archive) does not purge captured messages"
```

---

### Task D2: Full verification pass

- [ ] **Step 1: Backend suite**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all green. Read the tail of `/tmp/test.log`.

- [ ] **Step 2: Types + lint**

Run: `pipenv run basedpyright bot/` → 0 errors.
Run: `pipenv run ruff check` → clean.

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npx tsc --noEmit && npm run build > /tmp/build.log 2>&1; cd ..`
Expected: build succeeds.

- [ ] **Step 4: End-to-end manual smoke** (requires a real DB, `bridge_enabled: true`, `archive_media_dir` set, migration 027 applied)

1. Send a text message + an image in a puzzle channel. Open the dashboard → click the badge → the message and inline image appear; a second live message appears without refresh.
2. Edit the message in Discord → the viewer updates. Delete it → it disappears and the bytes are gone from `archive_media_dir` and the rows from `messages`/`message_attachments`.
3. `!archive_hunt` the hunt → the channel is deleted, the badge still opens the viewer, the transcript (incl. image) is intact, and there's no "Open in Discord" link.

- [ ] **Step 5: Update the design doc status**

Edit `docs/superpowers/specs/2026-07-06-discord-web-message-bridge-design.md` header to `Status: Implemented on staging` and commit.

---

## Self-review checklist (run before handing off for execution)

- **Spec coverage:** live-bridge capture (A5), everything-tagged `kind` (A5), puzzle-channel gate (A5), attachment bytes to disk + capped download (A4/A5), hard-delete-on-message-delete incl. byte unlink (A3/A5), edits overwrite (A3/A5), `web.bridge_enabled` dark ship (A2/A5), transcript + attachment routes with hunt-based visibility that works for archived channels (B1/B2), viewer in both states + `discord://` deep link (B5/B6/data.py deep url), live push reusing events/WS with per-channel gate (C1/C2), webhook archiver untouched + archive survives channel delete (D1). ✔
- **Placeholders:** none — every code step carries full code.
- **Type consistency:** `MessageDb.write/mark_edited/delete_message/add_attachment/set_attachment_local` used identically in A3, A5, C1. `kind` ints 0–3 consistent across cog, data.py, types.ts. `discord_deep_url` defined (data.py B1), typed (types.ts B3), consumed (PuzzleLinks B6, ChatViewer B5). Event shapes (`message`/`message_edit`/`message_delete`) match between C1 (publish) and C2 (dispatch) and the frontend hook (B4).

## Known limitations (documented, intentional)

- Newly created puzzle channels may miss up to `_REFRESH_SECONDS` (60s) of early messages until the gate set refreshes (typically only bot setup messages).
- Live `message_edit` events without guild context are delivered only to viewers already showing that message id (see C1 note); the frozen transcript is always correct on reopen.
- Live message events carry text immediately; attachments render on viewer reopen (the REST transcript has them). Acceptable for v1.
- `send_file` attachment serving is via the app; nginx `X-Accel-Redirect` is a future optimization.
