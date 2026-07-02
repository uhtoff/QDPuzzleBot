# `!archive_hunt` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-shot admin command `!archive_hunt` that condenses an entire hunt into threads on its general channel, deletes every category/channel (voice included), renames the surviving channel to `"{YYYY-MM} {hunt.name} - {team}"`, and marks the hunt archived — running unattended without the hangs the current archive commands suffer.

**Architecture:** A new ctx-free module `bot/core/hunt_archive.py` holds the hardened per-channel archive routine plus naming/webhook helpers. `PuzzleService.archive_hunt` (sibling to `delete_hunt`) orchestrates the whole-hunt sweep on top of it. A thin `!archive_hunt` cog command confirms then calls the service. The three existing archive commands (`archive_round`/`archive_category`/`archive_channel`) are migrated onto the shared routine so the fixes (download timeouts, no reactions, `allowed_mentions=none`, auto-webhook) apply everywhere.

**Tech Stack:** Python 3, discord.py (asyncio), pytest (`asyncio_mode=auto` in `pytest.ini` — `async def test_*` needs no decorator), `unittest.mock.AsyncMock`/`MagicMock`/`patch`. Tests are fully mocked (no Discord, no DB). Run with `pipenv run python -m pytest` (plain `python`/`pytest` aren't on PATH).

**Design reference:** `docs/superpowers/specs/2026-07-03-archive-hunt-design.md`.

---

## File Structure

- **Create** `bot/core/hunt_archive.py` — dataclasses (`ChannelArchiveResult`, `HuntArchiveReport`), naming helpers (`resolve_team_name`, `archived_channel_name`, `thread_name_for_channel`), `ensure_webhook`, and the hardened routine (`archive_channel_to_thread`, `archive_and_delete_channel`). Ctx-free; imports `PuzzleJsonDb` for thread naming only. One clear responsibility: turn a Discord channel's history into a thread, robustly.
- **Create** `tests/test_hunt_archive.py` — unit tests for the module.
- **Modify** `bot/core/puzzle_service.py` — add `import`, `PuzzleService.archive_hunt`, and `_archive_thread_exists`.
- **Create** `tests/test_archive_hunt_service.py` — orchestration tests (per-channel routine patched out).
- **Modify** `bot/cogs/puzzles.py` — generalise `_confirm_destructive`; add `!archive_hunt`; migrate `archive_channel`/`archive_category`/`archive_round`; add `from bot.core import hunt_archive`.
- **Create** `tests/test_archive_hunt_command.py` — cog command test (confirm gate + delegation).
- **Modify** `tests/test_archive_channel.py` — update patch target + assertions for the migrated behaviour.
- **Modify** `docs/commands.md` — add the `!archive_hunt` row; note migrated behaviour.

Test-command shortcut used throughout (per `AGENTS.md § Running tests`):

```bash
timeout 120 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1
```

A killed run exits 124 — read `/tmp/test.log` for results.

---

## Task 1: `hunt_archive` module — dataclasses + naming/webhook helpers

**Files:**
- Create: `bot/core/hunt_archive.py`
- Test: `tests/test_hunt_archive.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hunt_archive.py`:

```python
"""Unit tests for bot.core.hunt_archive (ctx-free archive primitives)."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import hunt_archive


def _hunt(role_id=0, name="Mystery", start=None):
    h = MagicMock()
    h.role_id = role_id
    h.name = name
    h.start_time = start
    return h


def test_resolve_team_name_uses_hunt_role():
    guild = MagicMock()
    role = MagicMock()
    role.name = "Team A"
    guild.get_role.return_value = role
    invoker = MagicMock()
    invoker.display_name = "russ"
    assert hunt_archive.resolve_team_name(guild, _hunt(role_id=5), invoker) == "Team A"


def test_resolve_team_name_falls_back_to_username():
    guild = MagicMock()
    guild.get_role.return_value = None          # role_id 0 OR role deleted
    invoker = MagicMock()
    invoker.display_name = "russ"
    assert hunt_archive.resolve_team_name(guild, _hunt(role_id=0), invoker) == "russ"


def test_archived_channel_name_with_start_time():
    start = datetime.datetime(2026, 6, 15, tzinfo=datetime.timezone.utc)
    assert hunt_archive.archived_channel_name(_hunt(name="Mystery", start=start), "Team A") == "2026-06 Mystery - Team A"


def test_archived_channel_name_without_start_time():
    assert hunt_archive.archived_channel_name(_hunt(name="Mystery", start=None), "Team A") == "Mystery - Team A"


def test_thread_name_for_puzzle_channel_is_capped():
    channel = MagicMock()
    channel.id = 111
    channel.category.name = "Round1"
    with patch("bot.core.hunt_archive.PuzzleJsonDb") as pdb:
        pdb.get_by_attr.return_value = MagicMock(name="p", **{"name": "Puzzle X"})
        name = hunt_archive.thread_name_for_channel(channel)
    assert name == "Puzzle X (Round1)"
    assert len(name) <= 100


def test_thread_name_for_non_puzzle_channel():
    channel = MagicMock()
    channel.id = 111
    channel.name = "chatter"
    channel.category.name = "Round1"
    with patch("bot.core.hunt_archive.PuzzleJsonDb") as pdb:
        pdb.get_by_attr.return_value = None
        assert hunt_archive.thread_name_for_channel(channel) == "chatter (Round1)"


async def test_ensure_webhook_reuses_existing():
    channel = MagicMock()
    existing = MagicMock()
    channel.webhooks = AsyncMock(return_value=[existing])
    channel.create_webhook = AsyncMock()
    assert await hunt_archive.ensure_webhook(channel) is existing
    channel.create_webhook.assert_not_awaited()


async def test_ensure_webhook_creates_when_missing():
    channel = MagicMock()
    created = MagicMock()
    channel.webhooks = AsyncMock(return_value=[])
    channel.create_webhook = AsyncMock(return_value=created)
    assert await hunt_archive.ensure_webhook(channel) is created
    channel.create_webhook.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_archive.py -q > /tmp/test.log 2>&1`
Expected: FAIL / collection error — `ModuleNotFoundError: No module named 'bot.core.hunt_archive'`.

- [ ] **Step 3: Create the module with dataclasses + helpers**

Create `bot/core/hunt_archive.py`:

```python
"""Hardened Discord channel-archiving primitives (ctx-free).

Copies a channel's human message history into a thread on a destination channel
via a webhook (impersonating original authors), skipping bot/command/empty
messages, then optionally deletes the source. Built to run unattended on large,
attachment-heavy, odd-emoji channels without hanging:

  * attachment downloads are bounded by a timeout and never abort the run
  * reactions are NOT copied (foreign/custom emoji can't be re-applied and cost
    an API round-trip each)
  * sends use allowed_mentions=none + silent so nothing pings

Used by the cog archive commands and by PuzzleService.archive_hunt.
"""
import asyncio
import logging
from dataclasses import dataclass, field

import discord

from bot.store import PuzzleJsonDb
from bot.core.discord_channels import DELETE_REASON

logger = logging.getLogger(__name__)

# Bound each attachment re-download so one stuck CDN fetch can't hang the archive.
ATTACH_TIMEOUT = 30
# Small politeness gap between webhook sends, on top of discord.py's own 429 handling.
SEND_DELAY = 0.1
# Discord thread-name hard cap.
MAX_THREAD_NAME = 100


@dataclass
class ChannelArchiveResult:
    thread: object = None
    messages_moved: int = 0
    messages_skipped: int = 0
    attachment_failures: int = 0


@dataclass
class HuntArchiveReport:
    channels_archived: int = 0
    voice_deleted: int = 0
    messages_moved: int = 0
    channels_skipped: int = 0
    categories_deleted: int = 0
    failures: list = field(default_factory=list)
    final_name: str = ""


def resolve_team_name(guild, hunt, invoker) -> str:
    """The hunt's gating-role name (hunt.role_id); the invoker's display name if the
    hunt has no role (role_id 0) or the role can no longer be resolved."""
    role = guild.get_role(hunt.role_id) if getattr(hunt, "role_id", 0) else None
    if role is not None:
        return role.name
    return invoker.display_name


def archived_channel_name(hunt, team) -> str:
    """'<YYYY-MM> <hunt.name> - <team>' when the hunt has a start_time, else
    '<hunt.name> - <team>'."""
    start = getattr(hunt, "start_time", None)
    if start is not None:
        return f"{start:%Y-%m} {hunt.name} - {team}"
    return f"{hunt.name} - {team}"


def thread_name_for_channel(channel) -> str:
    """Name the archive thread after the puzzle (if this is a puzzle channel), else
    the channel; suffixed with the category name and capped at MAX_THREAD_NAME."""
    puzzle = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
    category = getattr(channel, "category", None)
    round_name = category.name if category is not None else ""
    base = puzzle.name if puzzle else channel.name
    return f"{base} ({round_name})"[:MAX_THREAD_NAME]


async def ensure_webhook(channel) -> discord.Webhook:
    """Return an existing webhook on *channel*, creating one if there is none."""
    hooks = await channel.webhooks()
    if hooks:
        return hooks[0]
    return await channel.create_webhook(name="Archiver")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_archive.py -q > /tmp/test.log 2>&1`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/core/hunt_archive.py tests/test_hunt_archive.py
git commit -m "feat(core): hunt_archive naming + webhook helpers"
```

---

## Task 2: The hardened per-channel archive routine

**Files:**
- Modify: `bot/core/hunt_archive.py` (append two functions)
- Test: `tests/test_hunt_archive.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hunt_archive.py`:

```python
async def _aiter(items):
    for item in items:
        yield item


def _msg(id, content="", attachments=None, embeds=None, author_id=222):
    m = MagicMock()
    m.id = id
    m.created_at = id
    m.author = MagicMock()
    m.author.id = author_id
    m.author.display_name = f"user{author_id}"
    m.author.avatar = None
    m.content = content
    m.attachments = attachments or []
    m.embeds = embeds or []
    return m


def _source(messages):
    src = MagicMock()
    src.id = 111
    src.name = "chan"
    src.history = MagicMock(return_value=_aiter(messages))
    src.threads = []
    src.archived_threads = MagicMock(return_value=_aiter([]))
    src.delete = AsyncMock()
    return src


def _dest_and_webhook():
    dest = MagicMock()
    dest.send = AsyncMock(return_value=MagicMock())
    dest.create_thread = AsyncMock(return_value=MagicMock())
    webhook = MagicMock()
    webhook.send = AsyncMock(return_value=MagicMock())
    return dest, webhook


def _bot(app_id=999):
    bot = MagicMock()
    bot.application_id = app_id
    return bot


async def test_routine_skips_bot_and_command_messages():
    bot_msg = _msg(1, content="hi", author_id=999)         # bot's own id -> skip
    cmd_msg = _msg(2, content="!solve foo", author_id=222)  # bang command -> skip
    keep = _msg(3, content="a clue", author_id=222)
    src = _source([bot_msg, cmd_msg, keep])
    dest, webhook = _dest_and_webhook()
    result = await hunt_archive.archive_channel_to_thread(_bot(), dest, webhook, src, thread_name="t")
    assert webhook.send.await_count == 1
    assert result.messages_moved == 1


async def test_routine_sends_with_mentions_off_and_no_reactions():
    keep = _msg(1, content="ping <@42>", author_id=222)
    src = _source([keep])
    dest, webhook = _dest_and_webhook()
    moved = webhook.send.return_value
    moved.add_reaction = AsyncMock()
    await hunt_archive.archive_channel_to_thread(_bot(), dest, webhook, src, thread_name="t")
    kwargs = webhook.send.await_args.kwargs
    assert kwargs["allowed_mentions"].users is False  # AllowedMentions.none()
    assert kwargs["silent"] is True
    moved.add_reaction.assert_not_awaited()            # reactions are never copied


async def test_routine_times_out_stuck_attachment_but_keeps_message():
    attachment = MagicMock()
    attachment.filename = "big.bin"
    # asyncio.wait_for awaits this and the TimeoutError propagates — caught as a
    # skipped attachment, not a fatal error.
    attachment.to_file = AsyncMock(side_effect=TimeoutError("simulated stuck download"))
    msg = _msg(1, content="see file", attachments=[attachment], author_id=222)
    src = _source([msg])
    dest, webhook = _dest_and_webhook()
    result = await hunt_archive.archive_channel_to_thread(_bot(), dest, webhook, src, thread_name="t")
    # Message still sent (it has text); the attachment was dropped, not fatal.
    assert webhook.send.await_count == 1
    assert result.attachment_failures == 1
    assert webhook.send.await_args.kwargs["files"] == []


async def test_archive_and_delete_deletes_source_on_success():
    src = _source([_msg(1, content="x", author_id=222)])
    dest, webhook = _dest_and_webhook()
    await hunt_archive.archive_and_delete_channel(_bot(), dest, webhook, src, thread_name="t")
    src.delete.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_archive.py -k routine_or_delete -q > /tmp/test.log 2>&1`

(Use `-k "routine or delete"` — quoting for the shell.) Expected: FAIL — `module 'bot.core.hunt_archive' has no attribute 'archive_channel_to_thread'`.

- [ ] **Step 3: Append the routine to `bot/core/hunt_archive.py`**

```python
async def _gather_messages(source):
    """Every message in *source* and its active + archived threads, oldest first."""
    messages = []
    async for m in source.history(limit=None, oldest_first=True):
        messages.append(m)
    for thread in source.threads:
        async for m in thread.history(limit=None, oldest_first=True):
            messages.append(m)
    async for archived in source.archived_threads():
        async for m in archived.history(limit=None, oldest_first=True):
            messages.append(m)
    return sorted(messages, key=lambda m: m.created_at)


async def archive_channel_to_thread(bot, dest, webhook, source, *, thread_name) -> ChannelArchiveResult:
    """Copy *source*'s human history into a new thread on *dest*. Archive only,
    no delete. Skips the bot's own messages, `!command` messages, and empty
    messages; drops reactions; never pings; never hangs on a stuck download."""
    result = ChannelArchiveResult()
    placeholder = await dest.send(content=f"Archive of channel {source.name}", silent=True)
    thread = await dest.create_thread(name=thread_name, message=placeholder)
    result.thread = thread
    bot_id = bot.application_id
    for message in await _gather_messages(source):
        if not message:
            continue
        author = message.author
        if author.id == bot_id:
            continue
        content = message.content
        if content and content[0] == "!" and content[:3] != "!s ":
            continue
        files = []
        for a in message.attachments:
            try:
                files.append(await asyncio.wait_for(a.to_file(use_cached=True), timeout=ATTACH_TIMEOUT))
            except Exception as e:  # timeout / aiohttp / HTTP — skip the file, keep the message
                result.attachment_failures += 1
                logger.warning("Skipping attachment %r while archiving: %s", getattr(a, "filename", "?"), e)
        if not content and not files and not message.embeds:
            result.messages_skipped += 1
            continue
        avatar_url = getattr(author.avatar, "url", None)
        try:
            await webhook.send(
                content=content,
                username=author.display_name,
                avatar_url=avatar_url,
                files=files,
                embeds=message.embeds,
                thread=thread,
                wait=True,
                silent=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            result.messages_moved += 1
        except discord.HTTPException as e:
            result.messages_skipped += 1
            logger.warning("Skipping message from %s while archiving: %s", author.display_name, e)
        await asyncio.sleep(SEND_DELAY)
    return result


async def archive_and_delete_channel(bot, dest, webhook, source, *, thread_name) -> ChannelArchiveResult:
    """archive_channel_to_thread, then delete *source* on success."""
    result = await archive_channel_to_thread(bot, dest, webhook, source, thread_name=thread_name)
    await source.delete(reason=DELETE_REASON)
    return result
```

Note: `asyncio.sleep(SEND_DELAY)` runs in tests without patching (real 0.1s). To keep the suite fast you may patch `bot.core.hunt_archive.asyncio.sleep` with `AsyncMock()` in individual tests if desired; it is not required for correctness.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_archive.py -q > /tmp/test.log 2>&1`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/core/hunt_archive.py tests/test_hunt_archive.py
git commit -m "feat(core): hardened per-channel archive routine (timeouts, no reactions, mentions-off)"
```

---

## Task 3: `PuzzleService.archive_hunt` orchestrator

**Files:**
- Modify: `bot/core/puzzle_service.py` — add import (near line 22 `from bot.core import category_layout`), and the methods (add after `delete_hunt`, which ends ~line 2460).
- Test: `tests/test_archive_hunt_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_archive_hunt_service.py`:

```python
"""PuzzleService.archive_hunt — whole-hunt sweep orchestration.

The per-channel routine (hunt_archive.archive_and_delete_channel) is patched out;
this asserts enumeration, voice deletion, category teardown, rename, and the
archived flag.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core import hunt_archive
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData


def _service():
    bot = MagicMock()
    bot.application_id = 999
    return PuzzleService(bot)


def _hunt():
    h = HuntData()
    h.id = 7
    h.name = "H"
    h.category_id = 50
    h.channel_id = 60
    h.role_id = 0            # -> team falls back to invoker name
    h.start_time = None      # -> name has no YYYY-MM prefix
    return h


def _text_channel(id):
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = id
    ch.name = f"text{id}"
    return ch


def _voice_channel(id):
    ch = MagicMock(spec=discord.VoiceChannel)
    ch.id = id
    ch.name = f"voice{id}"
    return ch


async def test_archive_hunt_sweeps_renames_and_marks_archived():
    svc, hunt = _service(), _hunt()
    invoker = MagicMock(); invoker.display_name = "me"

    dest = MagicMock(); dest.id = 60; dest.threads = []
    dest.edit = AsyncMock()
    main_cat = MagicMock(); main_cat.id = 50; main_cat.channels = [dest]; main_cat.delete = AsyncMock()
    dest.category = main_cat
    text = _text_channel(71); voice = _voice_channel(72)
    round_cat = MagicMock(); round_cat.id = 70; round_cat.channels = [text, voice]; round_cat.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = [main_cat, round_cat]
    guild.get_channel.return_value = dest
    # discord.utils.get(guild.categories, id=cid) / name=... resolves against this list.

    tg = MagicMock(); tg.id = 3; tg.category_id = 70

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.hunt_archive.PuzzleJsonDb") as pdb, \
         patch("bot.core.hunt_archive.ensure_webhook", new=AsyncMock(return_value=MagicMock())), \
         patch("bot.core.hunt_archive.archive_and_delete_channel",
               new=AsyncMock(return_value=hunt_archive.ChannelArchiveResult(messages_moved=3))) as archive_call, \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)) as safe_del, \
         patch.object(svc, "safe_rename", new=AsyncMock()) as rename:
        tgdb.get_all.return_value = [tg]
        tgcdb.get_for_group.return_value = []       # single-category (legacy) round
        pdb.get_by_attr.return_value = None         # thread named after channel

        report = await svc.archive_hunt(guild, hunt, invoker)

    # Text channel archived; voice channel deleted.
    archive_call.assert_awaited_once()
    assert archive_call.await_args.args[3] is text        # (bot, dest, webhook, source)
    safe_del.assert_any_await(guild, 72)
    # Round category deleted; dest detached and main category deleted.
    round_cat.delete.assert_awaited_once()
    dest.edit.assert_awaited_once_with(category=None)
    main_cat.delete.assert_awaited_once()
    # Renamed and flagged.
    rename.assert_awaited_once_with(dest, "H - me")
    hdb.commit.assert_called_once_with(hunt)
    assert hunt.archived is True
    assert report.final_name == "H - me"
    assert report.channels_archived == 1
    assert report.voice_deleted == 1


async def test_archive_hunt_isolates_a_failing_channel():
    svc, hunt = _service(), _hunt()
    invoker = MagicMock(); invoker.display_name = "me"

    dest = MagicMock(); dest.id = 60; dest.threads = []; dest.edit = AsyncMock()
    main_cat = MagicMock(); main_cat.id = 50; main_cat.channels = [dest]; main_cat.delete = AsyncMock()
    dest.category = main_cat
    bad = _text_channel(71)
    round_cat = MagicMock(); round_cat.id = 70; round_cat.channels = [bad]; round_cat.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [main_cat, round_cat]; guild.get_channel.return_value = dest
    tg = MagicMock(); tg.id = 3; tg.category_id = 70

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.hunt_archive.PuzzleJsonDb") as pdb, \
         patch("bot.core.hunt_archive.ensure_webhook", new=AsyncMock(return_value=MagicMock())), \
         patch("bot.core.hunt_archive.archive_and_delete_channel",
               new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)), \
         patch.object(svc, "safe_rename", new=AsyncMock()):
        tgdb.get_all.return_value = [tg]
        tgcdb.get_for_group.return_value = []
        pdb.get_by_attr.return_value = None
        report = await svc.archive_hunt(guild, hunt, invoker)

    assert "text71" in report.failures       # failure recorded, run still completed
    assert hunt.archived is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_archive_hunt_service.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleService' object has no attribute 'archive_hunt'`.

- [ ] **Step 3: Add the import and methods**

In `bot/core/puzzle_service.py`, add to the imports (just after line 22 `from bot.core import category_layout`):

```python
from bot.core import hunt_archive
```

Add these methods to `PuzzleService` (place immediately after `delete_hunt`):

```python
    def _archive_thread_exists(self, dest, thread_name):
        """True if an active thread with *thread_name* already exists on *dest* —
        used to make archive_hunt resumable after a partial run."""
        return any(getattr(t, "name", None) == thread_name for t in getattr(dest, "threads", []))

    async def archive_hunt(self, guild, hunt, invoker, *, on_progress=None):
        """Condense a whole hunt into threads on its general channel, delete its
        categories/channels (voice included), rename the surviving channel, and
        mark the hunt archived. Google Sheets and DB rows are kept.

        Per-channel failures are isolated and collected on the report; one bad
        channel never aborts the run.
        """
        report = hunt_archive.HuntArchiveReport()
        dest = guild.get_channel(hunt.channel_id)
        if dest is None:
            report.failures.append("general-channel-missing")
            return report
        webhook = await hunt_archive.ensure_webhook(dest)

        # Every category the hunt owns: main + solved-by-name + each group's categories.
        category_ids = set()
        if hunt.category_id:
            category_ids.add(hunt.category_id)
        solved = discord.utils.get(
            guild.categories, name=category_layout.solved_category_name(hunt.name))
        if solved is not None:
            category_ids.add(solved.id)
        for tg in TagGroupJsonDb.get_all(hunt.id):
            if tg.category_id:
                category_ids.add(tg.category_id)
            for row in category_layout.group_categories(tg, TagGroupCategoryDb):
                if row.category_id:
                    category_ids.add(row.category_id)

        dest_category_id = dest.category.id if dest.category is not None else None
        categories = [c for c in
                      (discord.utils.get(guild.categories, id=cid) for cid in category_ids)
                      if c is not None]
        # Process dest's own category last so we never strand the surviving channel.
        ordered = ([c for c in categories if c.id != dest_category_id] +
                   [c for c in categories if c.id == dest_category_id])

        total = sum(1 for c in ordered for ch in c.channels if ch.id != dest.id)
        done = 0
        for category in ordered:
            for ch in list(category.channels):
                if ch.id == dest.id:
                    continue
                done += 1
                try:
                    if ch.name == self._SOLVE_DIVIDER_NAME:
                        await self._safe_delete_channel(guild, ch.id)
                    elif isinstance(ch, discord.TextChannel):
                        thread_name = hunt_archive.thread_name_for_channel(ch)
                        if self._archive_thread_exists(dest, thread_name):
                            # A prior run archived this but failed to delete it — finish that.
                            await self._safe_delete_channel(guild, ch.id)
                            report.channels_skipped += 1
                        else:
                            r = await hunt_archive.archive_and_delete_channel(
                                self.bot, dest, webhook, ch, thread_name=thread_name)
                            report.channels_archived += 1
                            report.messages_moved += r.messages_moved
                    else:
                        await self._safe_delete_channel(guild, ch.id)  # voice / stage / other
                        report.voice_deleted += 1
                except Exception:
                    logger.exception("archive_hunt: failed on channel %s",
                                     getattr(ch, "name", getattr(ch, "id", "?")))
                    report.failures.append(getattr(ch, "name", str(getattr(ch, "id", "?"))))
                if on_progress is not None:
                    await on_progress(done, total, getattr(ch, "name", ""))
            if category.id != dest_category_id:
                try:
                    await category.delete(reason=self._DISCORD_DELETE_REASON)
                    report.categories_deleted += 1
                except Exception:
                    logger.exception("archive_hunt: could not delete category %s", category.id)

        # dest survives alone: detach it and delete its now-empty category.
        if dest_category_id is not None:
            try:
                await dest.edit(category=None)
                main_cat = discord.utils.get(guild.categories, id=dest_category_id)
                if main_cat is not None:
                    await main_cat.delete(reason=self._DISCORD_DELETE_REASON)
                    report.categories_deleted += 1
            except Exception:
                logger.exception("archive_hunt: could not detach/delete main category")

        team = hunt_archive.resolve_team_name(guild, hunt, invoker)
        report.final_name = hunt_archive.archived_channel_name(hunt, team)
        await self.safe_rename(dest, report.final_name)

        hunt.archived = True
        HuntJsonDb.commit(hunt)
        return report
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_archive_hunt_service.py -q > /tmp/test.log 2>&1`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_archive_hunt_service.py
git commit -m "feat(core): PuzzleService.archive_hunt orchestrator"
```

---

## Task 4: Generalise `_confirm_destructive` for archive copy

**Files:**
- Modify: `bot/cogs/puzzles.py:3228-3242` (the `_confirm_destructive` method)
- Test: `tests/test_confirm_destructive.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_confirm_destructive.py`:

```python
"""_confirm_destructive: archive callers get accurate (non-'sheets deleted') copy."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_ctx


async def test_archive_confirm_omits_sheets_warning():
    cog = Puzzles.__new__(Puzzles)
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.ConfirmView") as CV:
        view = CV.return_value
        view.value = True
        view.wait = AsyncMock()
        result = await cog._confirm_destructive(ctx, "Archive hunt H?", sheets_deleted=False)
    assert result is True
    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.title == "⚠️ Confirm archive"
    assert "Google sheets will be permanently deleted" not in embed.description


async def test_delete_confirm_keeps_sheets_warning_by_default():
    cog = Puzzles.__new__(Puzzles)
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.ConfirmView") as CV:
        view = CV.return_value
        view.value = False
        view.wait = AsyncMock()
        result = await cog._confirm_destructive(ctx, "Delete puzzle X?")
    assert result is False
    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.title == "⚠️ Confirm deletion"
    assert "Google sheets will be permanently deleted" in embed.description
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_confirm_destructive.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `_confirm_destructive() got an unexpected keyword argument 'sheets_deleted'`.

- [ ] **Step 3: Generalise the method**

Replace the body of `_confirm_destructive` (puzzles.py:3228) with:

```python
    async def _confirm_destructive(self, ctx, description, *, sheets_deleted=True):
        """Send a Confirm/Cancel prompt and return True only if the invoker
        confirms. Returns False on cancel or timeout. ``sheets_deleted=False``
        switches the copy for archiving (which keeps the sheets)."""
        view = ConfirmView(ctx.author.id)
        if sheets_deleted:
            title = "⚠️ Confirm deletion"
            body = description + "\n\n**Google sheets will be permanently deleted (not recoverable).**"
            timeout_msg = ":hourglass: Confirmation timed out — nothing was deleted."
        else:
            title = "⚠️ Confirm archive"
            body = description
            timeout_msg = ":hourglass: Confirmation timed out — nothing was archived."
        embed = discord.Embed(title=title, description=body, color=discord.Color.red())
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if view.value is None:
            await ctx.send(timeout_msg)
        return view.value is True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_confirm_destructive.py -q > /tmp/test.log 2>&1`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_confirm_destructive.py
git commit -m "refactor(cog): _confirm_destructive supports non-deletion (archive) copy"
```

---

## Task 5: `!archive_hunt` cog command

**Files:**
- Modify: `bot/cogs/puzzles.py` — add `from bot.core import hunt_archive` (line 22 area, alongside `from bot.core import category_layout`); add the command near the other archive commands (after `archive_solved_manually`, ~line 3143).
- Test: `tests/test_archive_hunt_command.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_archive_hunt_command.py`:

```python
"""!archive_hunt cog command: confirm gate + delegation to service.archive_hunt."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.core import hunt_archive
from tests.conftest import make_cog, make_ctx


def _hunt():
    h = MagicMock()
    h.name = "H"
    h.role_id = 0
    h.start_time = None
    h.channel_id = 60
    h.layout_mode = "channel"
    return h


async def test_archive_hunt_runs_when_confirmed():
    hunt = _hunt()
    cog = make_cog(hunt=hunt)
    cog.service.archive_hunt = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        channels_archived=4, final_name="H - me"))
    ctx = make_ctx()
    ctx.author.display_name = "me"
    with patch.object(cog, "_confirm_destructive", new=AsyncMock(return_value=True)):
        await Puzzles.archive_hunt.callback(cog, ctx)
    cog.service.archive_hunt.assert_awaited_once()
    assert cog.service.archive_hunt.await_args.args[0] is ctx.guild
    assert cog.service.archive_hunt.await_args.args[1] is hunt
    assert cog.service.archive_hunt.await_args.args[2] is ctx.author


async def test_archive_hunt_aborts_when_cancelled():
    hunt = _hunt()
    cog = make_cog(hunt=hunt)
    cog.service.archive_hunt = AsyncMock()
    ctx = make_ctx()
    ctx.author.display_name = "me"
    with patch.object(cog, "_confirm_destructive", new=AsyncMock(return_value=False)):
        await Puzzles.archive_hunt.callback(cog, ctx)
    cog.service.archive_hunt.assert_not_awaited()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_archive_hunt_command.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `type object 'Puzzles' has no attribute 'archive_hunt'`.

- [ ] **Step 3: Add the import and the command**

Change the import at `bot/cogs/puzzles.py:22` from:

```python
from bot.core import category_layout
```

to:

```python
from bot.core import category_layout, hunt_archive
```

Add the command after `archive_solved_manually` (after puzzles.py:3143):

```python
    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def archive_hunt(self, ctx):
        """*Archive the whole hunt: condense every channel into threads here, delete all its categories/channels, rename this channel, mark it archived.*

        Usage: `!archive_hunt`
        Example: `!archive_hunt`
        Run in any channel of the hunt. Admins/mods only. Asks to confirm, then runs
        unattended. Google sheets are kept.
        Bang-only: no web-dashboard equivalent, by design."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        if getattr(hunt, "layout_mode", "") == "forum":
            await ctx.send(":x: !archive_hunt is not available for forum hunts yet")
            return
        team = hunt_archive.resolve_team_name(ctx.guild, hunt, ctx.author)
        final = hunt_archive.archived_channel_name(hunt, team)
        desc = (f"Archive hunt **{hunt.name}**? Condense every channel into threads on this "
                f"channel, delete all its categories and channels (including voice), rename "
                f"this channel to **{final}**, and mark the hunt archived. Google sheets are "
                f"kept. **Not reversible.**")
        if not await self._confirm_destructive(ctx, desc, sheets_deleted=False):
            return
        status = await ctx.send("⏳ Archiving…")

        async def on_progress(done, total, name):
            try:
                await status.edit(content=f"⏳ Archived {done}/{total} — {name}")
            except discord.HTTPException:
                pass

        report = await self.service.archive_hunt(ctx.guild, hunt, ctx.author, on_progress=on_progress)
        summary = (f":white_check_mark: Archived hunt into **{report.final_name}** — "
                   f"{report.channels_archived} channel(s), {report.messages_moved} message(s) moved, "
                   f"{report.voice_deleted} voice channel(s) removed.")
        if report.failures:
            summary += f"\n:warning: {len(report.failures)} channel(s) failed: {', '.join(report.failures)}"
        await ctx.send(summary)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_archive_hunt_command.py -q > /tmp/test.log 2>&1`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_archive_hunt_command.py
git commit -m "feat(cog): !archive_hunt command (confirm + progress + summary)"
```

---

## Task 6: Migrate the existing archive commands onto the routine

**Files:**
- Modify: `bot/cogs/puzzles.py` — `archive_channel` body (3013-3101); `archive_category` inner call (3004); `archive_round` unchanged except it already calls `archive_category`.
- Modify: `tests/test_archive_channel.py` — patch target + kwargs.

- [ ] **Step 1: Update the regression tests first (they define the new contract)**

In `tests/test_archive_channel.py`:

1. Change both `with patch("bot.cogs.puzzles.PuzzleJsonDb") as puzzle_db:` lines to
   `with patch("bot.core.hunt_archive.PuzzleJsonDb") as puzzle_db:` (thread naming now
   happens in `hunt_archive`).
2. In `_make_archive_to`, add `archive_to.create_webhook = AsyncMock()` (unused when a
   webhook already exists, but present so `ensure_webhook` is fully mockable).
3. In `test_archives_attachment_only_and_embed_only_messages`, after the existing
   assertions, add a check that mentions are disabled:

```python
    assert sent_calls[0].kwargs["allowed_mentions"].users is False
```

(The existing `webhook.send.await_count == 2`, `files`, `embeds`, and
`channel.delete.assert_awaited_once()` assertions all still hold.)

- [ ] **Step 2: Run the tests to verify they fail against the OLD implementation**

Run: `timeout 120 pipenv run python -m pytest tests/test_archive_channel.py -q > /tmp/test.log 2>&1`
Expected: FAIL — old `archive_channel` sends without `allowed_mentions` and reads `PuzzleJsonDb` from the cog namespace, so the new assertions/patch target don't match.

- [ ] **Step 3: Migrate `archive_channel`**

Replace the entire body of `archive_channel` (puzzles.py:3013-3101, from `if channel is None:` through `return True`) with:

```python
        if channel is None:
            channel = ctx.channel
        if archive_to is None:
            archive_to = self.bot.get_channel(self.get_hunt(ctx).channel_id)
        webhook = await hunt_archive.ensure_webhook(archive_to)
        thread_name = hunt_archive.thread_name_for_channel(channel)
        await hunt_archive.archive_and_delete_channel(
            ctx.bot, archive_to, webhook, channel, thread_name=thread_name)
        return True
```

(The old inline replay loop, the manual-webhook check, and reaction copying are all
removed — they now live in `hunt_archive`. `archive_category`/`archive_round` are
unchanged: they still call `self.archive_channel(...)`, which now delegates.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_archive_channel.py -q > /tmp/test.log 2>&1`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_archive_channel.py
git commit -m "refactor(cog): migrate archive_round/category/channel onto hunt_archive"
```

---

## Task 7: Docs — command reference + docstrings

**Files:**
- Modify: `docs/commands.md` (Archiving section, ~line 159-164)
- Modify: `bot/cogs/puzzles.py` — `archive_channel` docstring (drop the "needs a webhook" precondition).

- [ ] **Step 1: Add the `!archive_hunt` row and note migrated behaviour**

In `docs/commands.md`, in the Archiving table, add a row above `!archive_round`:

```markdown
| `!archive_hunt` | any hunt channel | Archive the **whole hunt**: condense every channel into threads here, delete all categories/channels (voice included), rename this channel to `{YYYY-MM} {hunt name} - {team}`, mark the hunt archived. Confirms first, then runs unattended. Sheets kept. |
```

And append a sentence under the Archiving prose paragraph:

```markdown
Channel archiving auto-creates the destination webhook, does not copy reactions, and
times out stuck attachment downloads (so a busy channel can't hang the run).
```

- [ ] **Step 2: Fix the `archive_channel` docstring**

In `bot/cogs/puzzles.py`, change the `archive_channel` docstring line:

```
        Run in the channel to archive. The destination needs a webhook. Admins/mods only.
```

to:

```
        Run in the channel to archive (creates the destination webhook if missing). Admins/mods only.
```

- [ ] **Step 3: Verify docs render / no broken tables**

Run: `timeout 120 pipenv run python -m pytest tests/test_archive_channel.py tests/test_hunt_archive.py tests/test_archive_hunt_service.py tests/test_archive_hunt_command.py tests/test_confirm_destructive.py -q > /tmp/test.log 2>&1`
Expected: PASS (all archive-related tests green together).

- [ ] **Step 4: Commit**

```bash
git add docs/commands.md bot/cogs/puzzles.py
git commit -m "docs(commands): add !archive_hunt; note migrated archive behaviour"
```

---

## Final verification

- [ ] Run the full suite:

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: PASS (no regressions). If 124 (timeout), re-run the archive test files individually.

- [ ] Manual smoke on the test server (`puzzlebot_test`, per `docs/dashboard.md`): a small hunt with two rounds, a voice channel, an attachment, a custom-emoji reaction, and an @mention → `!archive_hunt` → confirm → verify: one uncategorised channel named `"{YYYY-MM} {hunt.name} - {team}"` remains; threads hold the human messages; no ping fired; no reactions; voice channel + categories gone; hunt shows archived on the dashboard; sheets still present.

---

## Self-Review notes (author checklist — completed)

- **Spec coverage:** team-role name + username fallback (Task 1); `YYYY-MM` prefix from `start_time` (Task 1); reactions dropped, attachment timeout, `allowed_mentions=none`, auto-webhook (Task 2); enumerate all categories, delete voice, delete categories, keep dest, rename, `archived=True`, per-channel isolation, resume guard (Task 3); confirm with archive copy (Tasks 4-5); A-full migration (Task 6); docs (Task 7). `archive_solved_manually` intentionally untouched (spec § Out of scope).
- **Placeholder scan:** none — every code/edit step carries full code.
- **Type/name consistency:** `ChannelArchiveResult`/`HuntArchiveReport` defined in Task 1, used in Tasks 2-3/5; `archive_channel_to_thread(bot, dest, webhook, source, *, thread_name)` / `archive_and_delete_channel(...)` signatures identical across Tasks 2, 3, 6; `resolve_team_name(guild, hunt, invoker)` / `archived_channel_name(hunt, team)` / `thread_name_for_channel(channel)` / `ensure_webhook(channel)` consistent across Tasks 1, 3, 5, 6; `_confirm_destructive(ctx, description, *, sheets_deleted=True)` consistent across Tasks 4, 5.
- **Not covered by unit tests (documented):** the resume thread-exists guard and `on_progress` throttling are exercised in the manual smoke, not unit tests.
