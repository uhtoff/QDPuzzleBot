# Cross-server hunt archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new admin command, `!archive_hunt_to_hub <hub server id>`, run in a hunt on a team server, writes the hunt's whole history into a new channel on the named hub, moves the hunt's database row to that hub, and has the bot leave the emptied team server.

**Architecture:** `!archive_hunt` is left exactly as it is. The new command validates the hub up front, before its confirm prompt: the bot must be in it, the invoker must be an admin there, and it must not be the current server. Then `PuzzleService.archive_hunt_to_hub` creates (or on a re-run, finds) an uncategorised per-hunt channel on the hub. It sweeps **every** channel of the hunt into threads there, including the team server's general channel, reusing the existing sweep once that is extracted from `archive_hunt`. Finally it moves `hunts.guild_id`/`hunts.channel_id` to the hub and leaves the team server once no hunt row still points at it.

**Tech Stack:** Python 3.12, discord.py, MariaDB via `bot/store/mysqldb.py`, pytest (`asyncio_mode = auto`), `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`, sections *Archiving and teardown* and *Re-homing the hunt row to the hub*. This plan is **slice 1** of that spec. It departs from the spec in one place, decided with the user on 2026-09-24 (see *Decisions*). Task 6 writes that decision back into the spec.

## Decisions taken during planning (override the spec)

The spec gives each hunt a `hunts.archive_channel_id` column, set up in advance by `!setup_server`, and has `!archive_hunt` write there. **Replaced**, after two rounds with the user (a global `config.json` hub, then a hub stored on each server, were both considered and dropped):

- **The hub is named on the command:** `!archive_hunt_to_hub <hub server id>`. There is no config key, no migration and no setup step. `!archive_hunt` is unchanged, so no existing server's behaviour changes. `!setup_server` may later store a default so the id can be omitted. That is out of scope here.
- **No hub category is needed.** Today's archived hunt channels on the hub already sit outside any category (`archive_hunt` detaches the surviving channel). The new hub channel is created the same way, named exactly as today (`hunt_archive.archived_channel_name`).
- **The channel is tagged with the topic `puzzbot-archive:<hunt.uid>`.** A re-run after a partial failure finds that channel by its topic and resumes, rather than creating a second one.
- **Guard against misuse:** the invoker must hold the **Admin tier on the hub** (via `role_gate`, the same resolution `@is_admin()` uses), so nobody can dump threads into a hub they don't run. The confirm prompt names the hub, so a mistyped id is caught before anything is touched.

## Roadmap (the rest of the spec, one plan each, not written yet)

1. **This plan:** cross-server archive.
2. Template + `!setup_server` (preflight, `GuildSettings`, `create_hunt`, emojis, `--organisers`, `#lobby` welcome, `hunts.roster_role_id`, optionally a stored default hub).
3. Private invites: `hunt_invites` table, per-player DM, `on_member_join` auto-grant, `!invite`, late-joiner listener on the hub.
4. `#lobby` persistent join/leave panel + mod elevation from hub admin status.
5. Access audit: `hunt_access_log` + hub mirror channel via `on_member_update`.

**Drift to re-check when planning slice 2:** since the spec was written, admin/organiser gating moved to role **ids** (M8: `guilds.admin_role_ids` / `organiser_role_ids`, name matching only as the fallback when unset), and `archive_hunt` is now gated by `@is_admin()` rather than `has_any_role(*ADMIN_ROLES)`. The spec's claim that "a role named `mod` clears every gate" holds only while the team server's tier ids are unset.

## Global Constraints

- Default branch is **`staging`**, not `main`. Work in a worktree branched from **local** `staging` (origin is intentionally behind). Copy the gitignored `config.json` + `google_secrets.json` into the new worktree before running tests, or conftest dies on MySQL access-denied (pytest exit 4).
- Test command (always wrapped, always to a file, single statement):
  `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`
- Patch where names are **looked up**: `bot.core.puzzle_service.HuntJsonDb`, `bot.core.hunt_archive.GuildSettingsDb`, `bot.core.hunt_archive.role_gate`, `bot.cogs.puzzles.hunt_archive.resolve_hub`, etc.
- `!archive_hunt`'s behaviour must not change. Its existing tests (`tests/test_archive_hunt_service.py`, `tests/test_archive_hunt_command.py`, `test_bang_archive_hunt_confirms_before_locking` in `tests/test_lock_ordering.py`) must stay green **without edits**.
- `bot/core` must not import from `bot/web`. The hub member lookup is written locally, not borrowed from `bot.web.auth.get_or_fetch_member`.
- `hunts.role_id` is **not** touched by re-homing (0 means "legacy hunt" in `hunt_visible`).
- Keep the per-channel **webhook**. `ensure_webhook` creates one on the hub channel so messages keep their original authors. The spec's "no webhook" rejects storing a cross-guild webhook URL in the database, which this plan does not do. The bot needs Manage Webhooks on the hub (it has Administrator there today).
- Bang-only, like `!archive_hunt`: no web-dashboard equivalent, by the same design decision.
- No migration in this slice.

---

### Task 1: Hub resolution and hub-channel helpers

**Files:**
- Modify: `bot/core/hunt_archive.py` (imports, `HuntArchiveReport`, new helpers)
- Create: `tests/test_hub_archive_target.py`

**Interfaces:**
- Produces:
  - `hunt_archive.HubUnavailable(Exception)`. Its message is shown to the user after `:x: `.
  - `async hunt_archive.resolve_hub(bot, hub_guild_id: int, guild, invoker) -> tuple[discord.Guild, GuildSettings]` (raises `HubUnavailable`)
  - `hunt_archive.archive_topic(hunt) -> str`
  - `async hunt_archive.ensure_hub_archive_channel(hub, hunt, name) -> discord.TextChannel`
  - `HuntArchiveReport` new fields: `hub_channel_id: int = 0`, `left_guild: bool = False`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hub_archive_target.py`:

```python
"""Hub helpers for !archive_hunt_to_hub: validating the named hub, and finding a
half-built hub archive channel again on a re-run."""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core import hunt_archive
from bot.store.hunt_data import HuntData

HUB, TEAM, USER = 1000, 2000, 42


def _team():
    g = MagicMock(); g.id = TEAM
    return g


def _invoker():
    m = MagicMock(); m.id = USER
    return m


def _hub(member):
    hub = MagicMock(); hub.id = HUB; hub.name = "The Hub"
    hub.get_member.return_value = member
    hub.chunked = True
    return hub


def _member(*role_ids):
    m = MagicMock(); m.roles = [MagicMock(id=r) for r in role_ids]
    return m


def _bot(hub):
    bot = MagicMock(); bot.get_guild.return_value = hub
    return bot


async def test_resolve_hub_returns_hub_and_its_guilds_row_for_a_hub_admin():
    member = _member(7, 8)
    hub = _hub(member)
    row = MagicMock(); row.id = 9
    with patch("bot.core.hunt_archive.role_gate") as rg, \
         patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        rg.member_holds_tier.return_value = True
        gsdb.get.return_value = row
        got = await hunt_archive.resolve_hub(_bot(hub), HUB, _team(), _invoker())
    assert got == (hub, row)
    hub.get_member.assert_called_once_with(USER)
    assert rg.member_holds_tier.call_args.args[:2] == ({7, 8}, hub)
    gsdb.get.assert_called_once_with(HUB)


async def test_resolve_hub_refuses_the_current_server():
    with pytest.raises(hunt_archive.HubUnavailable, match="this server"):
        await hunt_archive.resolve_hub(_bot(_hub(_member())), TEAM, _team(), _invoker())


async def test_resolve_hub_refuses_a_server_the_bot_is_not_in():
    with pytest.raises(hunt_archive.HubUnavailable, match="1000"):
        await hunt_archive.resolve_hub(_bot(None), HUB, _team(), _invoker())


async def test_resolve_hub_refuses_a_non_member():
    with pytest.raises(hunt_archive.HubUnavailable, match="aren't a member of The Hub"):
        await hunt_archive.resolve_hub(_bot(_hub(None)), HUB, _team(), _invoker())


async def test_resolve_hub_fetches_the_member_while_the_hub_is_unchunked():
    hub = _hub(None); hub.chunked = False
    hub.fetch_member = AsyncMock(return_value=_member(7))
    with patch("bot.core.hunt_archive.role_gate") as rg, \
         patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        rg.member_holds_tier.return_value = True
        gsdb.get.return_value = MagicMock()
        await hunt_archive.resolve_hub(_bot(hub), HUB, _team(), _invoker())
    hub.fetch_member.assert_awaited_once_with(USER)


async def test_resolve_hub_refuses_a_member_who_is_not_a_hub_admin():
    with patch("bot.core.hunt_archive.role_gate") as rg, \
         patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        rg.member_holds_tier.return_value = False
        with pytest.raises(hunt_archive.HubUnavailable, match="aren't an admin on The Hub"):
            await hunt_archive.resolve_hub(_bot(_hub(_member(7))), HUB, _team(), _invoker())
    gsdb.get.assert_not_called()


async def test_resolve_hub_refuses_a_hub_with_no_guilds_row():
    with patch("bot.core.hunt_archive.role_gate") as rg, \
         patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        rg.member_holds_tier.return_value = True
        gsdb.get.return_value = None
        with pytest.raises(hunt_archive.HubUnavailable, match="guilds row"):
            await hunt_archive.resolve_hub(_bot(_hub(_member(7))), HUB, _team(), _invoker())


def _hunt(uid):
    h = HuntData(); h.uid = uid
    return h


def _chan(topic):
    c = MagicMock(); c.topic = topic
    return c


async def test_ensure_hub_archive_channel_reuses_the_one_tagged_for_this_hunt():
    other, mine = _chan("puzzbot-archive:zzz"), _chan("puzzbot-archive:abc")
    hub = MagicMock(); hub.text_channels = [other, mine]
    hub.create_text_channel = AsyncMock()
    got = await hunt_archive.ensure_hub_archive_channel(hub, _hunt("abc"), "H - team")
    assert got is mine
    hub.create_text_channel.assert_not_awaited()


async def test_ensure_hub_archive_channel_creates_an_uncategorised_tagged_channel():
    hub = MagicMock(); hub.text_channels = [_chan("puzzbot-archive:zzz"), _chan(None)]
    created = MagicMock()
    hub.create_text_channel = AsyncMock(return_value=created)
    got = await hunt_archive.ensure_hub_archive_channel(hub, _hunt("abc"), "H - team")
    assert got is created
    hub.create_text_channel.assert_awaited_once_with(
        "H - team", topic="puzzbot-archive:abc")
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_hub_archive_target.py -q > /tmp/test.log 2>&1`
Expected: FAIL, with `AttributeError: module 'bot.core.hunt_archive' has no attribute 'resolve_hub'` (and similar).

- [ ] **Step 3: Add the helpers to `bot/core/hunt_archive.py`**

Change the imports:

```python
from bot.store import GuildSettingsDb, PuzzleJsonDb
from bot.core import role_gate
from bot.core.constants import ADMIN_ROLES
from bot.core.discord_channels import DELETE_REASON
```

Add two fields to the end of `HuntArchiveReport`:

```python
    final_name: str = ""
    # archive_hunt_to_hub only: the hub channel the history landed in.
    hub_channel_id: int = 0
    # archive_hunt_to_hub only: True once the bot has left the emptied team server.
    left_guild: bool = False
```

Add after `archived_channel_name`:

```python
class HubUnavailable(Exception):
    """The named hub can't take this archive. Raised before anything is
    touched; the message is shown to the invoker as-is."""


async def _hub_member(hub, user_id):
    """The invoker's member object on the hub, or None. Once the hub is chunked
    the cache is complete, so a miss is authoritative."""
    member = hub.get_member(user_id)
    if member is None and not hub.chunked:
        try:
            member = await hub.fetch_member(user_id)
        except discord.NotFound:
            member = None
    return member


async def resolve_hub(bot, hub_guild_id, guild, invoker):
    """(hub guild, hub `guilds` row) for !archive_hunt_to_hub, or HubUnavailable.

    The invoker must hold the Admin tier ON THE HUB, so nobody can dump a hunt
    into a hub they don't run. The row is needed to re-home the hunt:
    hunts.guild_id is an FK to guilds.id (the row id), not the snowflake."""
    if hub_guild_id == guild.id:
        raise HubUnavailable("that's this server — use !archive_hunt to archive in place")
    hub = bot.get_guild(hub_guild_id)
    if hub is None:
        raise HubUnavailable(f"the bot isn't in server {hub_guild_id}")
    member = await _hub_member(hub, invoker.id)
    if member is None:
        raise HubUnavailable(f"you aren't a member of {hub.name}")
    if not role_gate.member_holds_tier({r.id for r in member.roles}, hub, ADMIN_ROLES):
        raise HubUnavailable(f"you aren't an admin on {hub.name}")
    row = GuildSettingsDb.get(hub.id)
    if row is None:
        raise HubUnavailable(f"{hub.name} has no guilds row (re-invite the bot there)")
    return hub, row


def archive_topic(hunt) -> str:
    """Topic tagging a hub archive channel with its hunt, so a re-run finds it."""
    return f"puzzbot-archive:{hunt.uid}"


async def ensure_hub_archive_channel(hub, hunt, name):
    """The hub channel this hunt archives into: the one already tagged for it
    (a prior partial run), else a new uncategorised one named *name*, the same
    place archive_hunt leaves its surviving channel today."""
    marker = archive_topic(hunt)
    for ch in hub.text_channels:
        if ch.topic == marker:
            return ch
    return await hub.create_text_channel(name, topic=marker)
```

If `from bot.core import role_gate` creates an import cycle (check with `pipenv run python -c "import bot.core.hunt_archive"`), stop and report it rather than moving imports inside functions.

- [ ] **Step 4: Run the new tests and the existing archive tests**

Run: `timeout 180 pipenv run python -m pytest tests/test_hub_archive_target.py tests/test_archive_hunt_service.py tests/test_archive_hunt_command.py -q > /tmp/test.log 2>&1`
Expected: all PASS.

- [ ] **Step 5: Mutation check**

In `resolve_hub`, temporarily delete the `if not role_gate.member_holds_tier(...)` check (both lines). `test_resolve_hub_refuses_a_member_who_is_not_a_hub_admin` must go red. Restore by editing the lines back.

- [ ] **Step 6: Commit**

```bash
git add bot/core/hunt_archive.py tests/test_hub_archive_target.py
git commit -m "feat(archive): resolve and validate a hub for cross-server archive"
```

---

### Task 2: Extract the archive sweep (pure refactor, no behaviour change)

**Files:**
- Modify: `bot/core/puzzle_service.py` (`archive_hunt`, starting near line 3834)

**Interfaces:**
- Produces (on `PuzzleService`):
  - `_hunt_categories(self, guild, hunt) -> list` — the hunt's live category objects
  - `async _sweep_hunt_categories(self, guild, dest, webhook, categories, report, *, keep_channel_id, keep_category_id, on_progress) -> None`
  - `_drop_split_layout_rows(self, hunt) -> None`

The gate for this task is the **unedited** `tests/test_archive_hunt_service.py`. Move the code; do not change it.

- [ ] **Step 1: Run the existing archive tests to get a green baseline**

Run: `timeout 180 pipenv run python -m pytest tests/test_archive_hunt_service.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 2: Add the three extracted methods directly above `async def archive_hunt`**

```python
    def _hunt_categories(self, guild, hunt):
        """Every live category the hunt owns: main + solved-by-name + any
        split/overflow categories the hunt's own general container owns + each
        group's categories."""
        category_ids = set()
        if hunt.category_id:
            category_ids.add(hunt.category_id)
        solved = discord.utils.get(
            guild.categories, name=category_layout.solved_category_name(hunt.name))
        if solved is not None:
            category_ids.add(solved.id)
        for row in category_layout.group_categories(hunt, HuntCategoryDb):
            if row.category_id:
                category_ids.add(row.category_id)
        for tg in TagGroupJsonDb.get_all(hunt.id):
            if tg.category_id:
                category_ids.add(tg.category_id)
            for row in category_layout.group_categories(tg, TagGroupCategoryDb):
                if row.category_id:
                    category_ids.add(row.category_id)
        return [c for c in
                (discord.utils.get(guild.categories, id=cid) for cid in category_ids)
                if c is not None]

    async def _sweep_hunt_categories(self, guild, dest, webhook, categories, report, *,
                                     keep_channel_id, keep_category_id, on_progress):
        """Condense every channel in *categories* into threads on *dest* (deleting
        each), then delete every category except *keep_category_id*.
        *keep_channel_id* is skipped: the surviving archive channel when *dest*
        lives in the hunt's own guild, None when *dest* is on a hub."""
        total = sum(1 for c in categories for ch in c.channels if ch.id != keep_channel_id)
        done = 0
        for category in categories:
            for ch in list(category.channels):
                if ch.id == keep_channel_id:
                    continue
                # ... the existing per-channel body, moved verbatim ...
            if category.id != keep_category_id:
                # ... the existing category-delete block, moved verbatim ...

    def _drop_split_layout_rows(self, hunt):
        """Archive keeps every hunt/tag_group DB row (only the flag below
        changes), so no FK cascade ever fires — drop the split-layout join
        rows explicitly, or they point at the deleted categories forever and
        is_split() stays true for the archived hunt and every archived round."""
        HuntCategoryDb.delete_for_group(hunt.id)
        for tg in TagGroupJsonDb.get_all(hunt.id):
            TagGroupCategoryDb.delete_for_group(tg.id)
```

The two `# ... moved verbatim ...` markers are **move instructions, not code to leave in place**. Cut the body of today's inner `for ch in list(category.channels):` loop (from `done += 1` through the `on_progress` call) and today's `if category.id != dest_category_id:` delete block out of `archive_hunt`, and paste them in unchanged. Then make exactly two substitutions:

- `if ch.id == dest.id:` becomes `if ch.id == keep_channel_id:` (shown above)
- `if category.id != dest_category_id:` becomes `if category.id != keep_category_id:` (shown above)

- [ ] **Step 3: Rewrite `archive_hunt`'s body to call them**

`archive_hunt` becomes, with the docstring unchanged:

```python
        report = hunt_archive.HuntArchiveReport()
        dest = guild.get_channel(hunt.channel_id)
        if dest is None:
            report.failures.append("general-channel-missing")
            return report
        webhook = await hunt_archive.ensure_webhook(dest)

        categories = self._hunt_categories(guild, hunt)
        dest_category_id = dest.category.id if dest.category is not None else None
        # Process dest's own category last so we never strand the surviving channel.
        ordered = ([c for c in categories if c.id != dest_category_id] +
                   [c for c in categories if c.id == dest_category_id])
        await self._sweep_hunt_categories(
            guild, dest, webhook, ordered, report,
            keep_channel_id=dest.id, keep_category_id=dest_category_id,
            on_progress=on_progress)

        # dest survives alone: detach it and delete its now-empty category.
        # ... the existing `if dest_category_id is not None:` block, unchanged ...

        team = hunt_archive.resolve_team_name(guild, hunt, invoker)
        report.final_name = hunt_archive.archived_channel_name(hunt, team)
        await self.safe_rename(dest, report.final_name)

        self._drop_split_layout_rows(hunt)

        hunt.archived = True
        HuntJsonDb.commit(hunt)
        return report
```

(The `# ... unchanged ...` line again means: leave that existing block exactly where it is.)

- [ ] **Step 4: Run the unedited existing tests**

Run: `timeout 180 pipenv run python -m pytest tests/test_archive_hunt_service.py tests/test_archive_hunt_command.py -q > /tmp/test.log 2>&1`
Expected: PASS. Then check `git diff tests/` is empty.

- [ ] **Step 5: Review the diff as a move**

Run: `git diff --color-moved=zebra bot/core/puzzle_service.py`
Expected: the per-channel body and category-delete block show as moved lines. The only non-moved changes are the two renamed comparisons, the new `def` lines and docstrings, and the new calls in `archive_hunt`.

- [ ] **Step 6: Commit**

```bash
git add bot/core/puzzle_service.py
git commit -m "refactor(archive): extract archive_hunt's category sweep for reuse"
```

---

### Task 3: `HuntJsonDb.count_for_guild`

**Files:**
- Modify: `bot/store/mysqldb.py` (`MySQLHuntJsonDb`, after `check_duplicates`)
- Create: `tests/test_hunt_count_for_guild.py`

**Interfaces:**
- Produces: `HuntJsonDb.count_for_guild(guild_row_id: int) -> int`. Counts hunt rows, **archived or not**, whose `guild_id` (the `guilds.id` FK) equals the argument.

It counts archived rows too, deliberately: an older archived hunt still homed on a secondary server would disappear from the dashboard if the bot left that server (`hunt_visible` requires the user to share the guild with the bot).

- [ ] **Step 1: Write the failing test**

```python
"""count_for_guild gates the bot leaving a team server after a hub archive."""
from unittest.mock import MagicMock

from bot.store.mysqldb import MySQLHuntJsonDb


def _db(row):
    db = MySQLHuntJsonDb(mydb=MagicMock())
    db._fetch_one = MagicMock(return_value=row)
    return db


def test_counts_every_hunt_row_on_the_guild_row_archived_or_not():
    db = _db({"n": 2})
    assert db.count_for_guild(5) == 2
    sql, params = db._fetch_one.call_args.args
    assert params == (5,)
    assert "guild_id = %s" in sql
    assert "archived" not in sql   # archived rows still pin the guild


def test_no_row_means_zero():
    assert _db(None).count_for_guild(5) == 0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `timeout 180 pipenv run python -m pytest tests/test_hunt_count_for_guild.py -q > /tmp/test.log 2>&1`
Expected: FAIL, `AttributeError: ... has no attribute 'count_for_guild'`.

- [ ] **Step 3: Implement it (add after `check_duplicates` in `MySQLHuntJsonDb`)**

```python
    def count_for_guild(self, guild_row_id) -> int:
        """How many hunt rows, archived or not, point at `guilds.id` *guild_row_id*.

        archive_hunt_to_hub leaves a team server only when this is zero: an
        archived hunt still homed there would vanish from the dashboard once the
        bot left, because hunt_visible needs the user to share that guild with
        the bot."""
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM `hunts` WHERE guild_id = %s", (guild_row_id,))
        return int(row["n"]) if row else 0
```

- [ ] **Step 4: Run it to confirm it passes**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/store/mysqldb.py tests/test_hunt_count_for_guild.py
git commit -m "feat(store): HuntJsonDb.count_for_guild"
```

---

### Task 4: `PuzzleService.archive_hunt_to_hub`

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Create: `tests/test_archive_hunt_to_hub.py`

**Interfaces:**
- Consumes: `hunt_archive.ensure_hub_archive_channel`, `HuntArchiveReport.hub_channel_id`/`left_guild` (Task 1); `_hunt_categories`, `_sweep_hunt_categories`, `_drop_split_layout_rows` (Task 2); `HuntJsonDb.count_for_guild` (Task 3).
- Produces: `async PuzzleService.archive_hunt_to_hub(self, guild, hunt, invoker, hub, hub_row, *, on_progress=None) -> HuntArchiveReport`. `hub`/`hub_row` are what `hunt_archive.resolve_hub` returned; the cog validates first.

Required order, which the tests pin: create/find hub channel → sweep **all** channels, general last → drop split rows → re-home + archive + commit → count → leave only if the count is zero **and** there were no failures.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_archive_hunt_to_hub.py`:

```python
"""PuzzleService.archive_hunt_to_hub: history to a new hub channel, row re-homed
to the hub, bot leaves the emptied team server. Per-channel archiving is patched
out (see test_archive_hunt_service.py for the same arrangement on archive_hunt)."""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.core import hunt_archive
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData


def _service():
    bot = MagicMock(); bot.application_id = 999
    return PuzzleService(bot)


def _hunt():
    h = HuntData()
    h.id = 7; h.uid = "abc"; h.name = "H"
    h.guild_id = 5            # the TEAM server's guilds row id
    h.category_id = 50; h.channel_id = 60
    h.role_id = 0; h.start_time = None
    return h


def _text(id):
    ch = MagicMock(spec=discord.TextChannel); ch.id = id; ch.name = f"text{id}"
    ch.threads = []; ch.edit = AsyncMock()
    return ch


def _voice(id):
    ch = MagicMock(spec=discord.VoiceChannel); ch.id = id; ch.name = f"voice{id}"
    return ch


def _team():
    """general(60) in main_cat(50); puzzle(71) + voice(72) in round_cat(70)."""
    general, puzzle, voice = _text(60), _text(71), _voice(72)
    main_cat = MagicMock(); main_cat.id = 50; main_cat.channels = [general]; main_cat.delete = AsyncMock()
    general.category = main_cat
    round_cat = MagicMock(); round_cat.id = 70; round_cat.channels = [puzzle, voice]; round_cat.delete = AsyncMock()
    g = MagicMock(); g.id = 2000; g.categories = [main_cat, round_cat]
    g.get_channel.return_value = general
    g.leave = AsyncMock()
    return SimpleNamespace(guild=g, general=general, puzzle=puzzle, voice=voice,
                           main_cat=main_cat, round_cat=round_cat)


@contextmanager
def _patched(svc, *, archive_side_effect=None, count=0):
    hub_dest = _text(3000)
    tg = MagicMock(); tg.id = 3; tg.category_id = 70
    with ExitStack() as st:
        p = lambda *a, **k: st.enter_context(patch(*a, **k))
        m = SimpleNamespace(
            hub=MagicMock(), hub_row=MagicMock(id=9), hub_dest=hub_dest,
            ensure_hub=p("bot.core.hunt_archive.ensure_hub_archive_channel",
                         new=AsyncMock(return_value=hub_dest)),
            tgdb=p("bot.core.puzzle_service.TagGroupJsonDb"),
            hdb=p("bot.core.puzzle_service.HuntJsonDb"),
            tgcdb=p("bot.core.puzzle_service.TagGroupCategoryDb"),
            pdb=p("bot.core.hunt_archive.PuzzleJsonDb"),
            hook=p("bot.core.hunt_archive.ensure_webhook", new=AsyncMock(return_value=MagicMock())),
            archive=p("bot.core.hunt_archive.archive_and_delete_channel",
                      new=AsyncMock(side_effect=archive_side_effect,
                                    return_value=hunt_archive.ChannelArchiveResult(messages_moved=2))),
            safe_del=st.enter_context(patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True))),
            rename=st.enter_context(patch.object(svc, "safe_rename", new=AsyncMock())),
        )
        m.tgdb.get_all.return_value = [tg]
        m.tgcdb.get_for_group.return_value = []
        m.pdb.get_by_attr.return_value = None
        m.hdb.count_for_guild.return_value = count
        yield m


def _invoker():
    i = MagicMock(); i.display_name = "me"
    return i


async def _run(svc, hunt, t, m):
    return await svc.archive_hunt_to_hub(t.guild, hunt, _invoker(), m.hub, m.hub_row)


async def test_archives_every_channel_to_the_hub_rehomes_and_leaves():
    svc, hunt, t = _service(), _hunt(), _team()
    with _patched(svc) as m:
        report = await _run(svc, hunt, t, m)

    m.ensure_hub.assert_awaited_once_with(m.hub, hunt, "H - me")
    m.hook.assert_awaited_once_with(m.hub_dest)
    # Every text channel lands on the hub, the general one included and last.
    calls = m.archive.await_args_list
    assert [c.args[3] for c in calls] == [t.puzzle, t.general]
    assert all(c.args[1] is m.hub_dest for c in calls)
    m.safe_del.assert_any_await(t.guild, 72)
    t.round_cat.delete.assert_awaited_once()
    t.main_cat.delete.assert_awaited_once()
    t.general.edit.assert_not_awaited()      # nothing is detached and kept
    m.rename.assert_not_awaited()            # the hub channel is born with its name
    # Re-homed onto the hub; role_id untouched.
    assert (hunt.guild_id, hunt.channel_id, hunt.archived, hunt.role_id) == (9, 3000, True, 0)
    m.hdb.commit.assert_called_once_with(hunt)
    # Counted against the TEAM guild row, after the re-homing commit.
    m.hdb.count_for_guild.assert_called_once_with(5)
    names = [c[0] for c in m.hdb.mock_calls]
    assert names.index("commit") < names.index("count_for_guild")
    t.guild.leave.assert_awaited_once()
    assert report.left_guild is True
    assert report.hub_channel_id == 3000
    assert report.final_name == "H - me"
    assert report.channels_archived == 2


async def test_a_failed_channel_still_rehomes_but_keeps_the_bot_in_the_server():
    svc, hunt, t = _service(), _hunt(), _team()
    boom = [RuntimeError("boom"), hunt_archive.ChannelArchiveResult(messages_moved=1)]
    with _patched(svc, archive_side_effect=boom) as m:
        report = await _run(svc, hunt, t, m)
    assert report.failures == ["text71"]
    assert (hunt.guild_id, hunt.channel_id, hunt.archived) == (9, 3000, True)
    t.guild.leave.assert_not_awaited()       # an admin must be able to re-run
    assert report.left_guild is False


async def test_another_hunt_on_the_team_server_keeps_the_bot_there():
    svc, hunt, t = _service(), _hunt(), _team()
    with _patched(svc, count=1) as m:
        report = await _run(svc, hunt, t, m)
    t.guild.leave.assert_not_awaited()
    assert report.left_guild is False
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_archive_hunt_to_hub.py -q > /tmp/test.log 2>&1`
Expected: FAIL, `AttributeError: 'PuzzleService' object has no attribute 'archive_hunt_to_hub'`.

- [ ] **Step 3: Add `archive_hunt_to_hub` directly below `archive_hunt`**

```python
    async def archive_hunt_to_hub(self, guild, hunt, invoker, hub, hub_row, *, on_progress=None):
        """Archive a hunt living on a team server into *hub* (already validated
        by hunt_archive.resolve_hub): condense EVERY channel, the general one
        included since the whole server is about to be abandoned, into threads on
        a per-hunt hub channel. Then re-home the hunt row to the hub so the
        dashboard still shows it once the team server is gone, and leave the team
        server if nothing else is homed there.

        Re-runnable: the hub channel is found again by its topic, and threads
        already written are skipped by name (see _sweep_hunt_categories)."""
        report = hunt_archive.HuntArchiveReport()
        team = hunt_archive.resolve_team_name(guild, hunt, invoker)
        report.final_name = hunt_archive.archived_channel_name(hunt, team)
        dest = await hunt_archive.ensure_hub_archive_channel(hub, hunt, report.final_name)
        report.hub_channel_id = dest.id
        webhook = await hunt_archive.ensure_webhook(dest)

        categories = self._hunt_categories(guild, hunt)
        general = guild.get_channel(hunt.channel_id)
        general_category_id = (general.category.id
                               if general is not None and general.category is not None else None)
        # The general channel is where admins usually run this: take it last.
        ordered = ([c for c in categories if c.id != general_category_id] +
                   [c for c in categories if c.id == general_category_id])
        await self._sweep_hunt_categories(
            guild, dest, webhook, ordered, report,
            keep_channel_id=None, keep_category_id=None, on_progress=on_progress)

        self._drop_split_layout_rows(hunt)

        # Re-home. hunts.guild_id is the guilds ROW id, not a snowflake. role_id
        # stays as a historical record: archived hunts never reach the role check,
        # and 0 would mean "legacy hunt".
        team_guild_row_id = hunt.guild_id
        hunt.guild_id = hub_row.id
        hunt.channel_id = dest.id
        hunt.archived = True
        HuntJsonDb.commit(hunt)

        # Leave only a clean, unreferenced server: on failure an admin must be able
        # to re-run from a surviving channel.
        if not report.failures and HuntJsonDb.count_for_guild(team_guild_row_id) == 0:
            try:
                await guild.leave()
                report.left_guild = True
            except discord.HTTPException:
                logger.exception("archive_hunt_to_hub: could not leave team guild %s", guild.id)
        return report
```

- [ ] **Step 4: Run the new and existing archive tests**

Run: `timeout 180 pipenv run python -m pytest tests/test_archive_hunt_to_hub.py tests/test_archive_hunt_service.py tests/test_hub_archive_target.py -q > /tmp/test.log 2>&1`
Expected: all PASS.

- [ ] **Step 5: Mutation checks (restore each by editing the line back)**

1. Change `keep_channel_id=None` to `keep_channel_id=hunt.channel_id`. The first test must fail on the `[t.puzzle, t.general]` assertion.
2. Move the `count_for_guild` check above `HuntJsonDb.commit(hunt)`. The first test must fail on the ordering assertion.
3. Delete `not report.failures and`. `test_a_failed_channel_still_rehomes_but_keeps_the_bot_in_the_server` must fail.

- [ ] **Step 6: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_archive_hunt_to_hub.py
git commit -m "feat(archive): PuzzleService.archive_hunt_to_hub re-homes a team-server hunt"
```

---

### Task 5: The `!archive_hunt_to_hub` command

**Files:**
- Modify: `bot/cogs/puzzles.py` (new command directly below `archive_hunt`, near line 3995; small summary extraction in `archive_hunt`)
- Modify: `tests/test_command_gating.py` (census entry)
- Modify: `tests/test_lock_ordering.py` (append one test)
- Create: `tests/test_archive_hunt_to_hub_command.py`

**Interfaces:**
- Consumes: `hunt_archive.resolve_hub`, `hunt_archive.HubUnavailable` (Task 1); `service.archive_hunt_to_hub` (Task 4).

Validate the hub **before** the confirm prompt, so a bad id or a non-admin is refused without a prompt and the prompt can name the hub. By the time the summary is sent, the channel the command ran in has usually been archived and the bot may have left the server. The summary therefore goes to the hub channel, and the reply in `ctx` must not raise.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_archive_hunt_to_hub_command.py`:

```python
"""!archive_hunt_to_hub: hub validated before confirming, summary to the hub."""
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.cogs.puzzles import Puzzles
from bot.core import hunt_archive
from tests.conftest import make_cog, make_ctx


def _hunt():
    h = MagicMock()
    h.name = "H"; h.role_id = 0; h.start_time = None
    h.channel_id = 60; h.layout_mode = "channel"
    return h


def _hub():
    hub = MagicMock(); hub.name = "The Hub"
    hub_channel = MagicMock(); hub_channel.send = AsyncMock()
    hub.get_channel.return_value = hub_channel
    return hub, hub_channel


def _ctx():
    ctx = make_ctx(); ctx.author.display_name = "me"
    return ctx


async def test_refuses_a_bad_hub_without_asking_to_confirm():
    cog = make_cog(hunt=_hunt()); cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock()
    ctx = _ctx()
    confirm = AsyncMock(return_value=True)
    with patch.object(cog, "_confirm_destructive", new=confirm), \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=AsyncMock(side_effect=hunt_archive.HubUnavailable("you aren't an admin on The Hub"))):
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 1000)
    ctx.send.assert_awaited_once_with(":x: you aren't an admin on The Hub")
    confirm.assert_not_awaited()
    cog.service.archive_hunt_to_hub.assert_not_awaited()


async def test_names_the_hub_then_reports_there_even_if_ctx_channel_is_gone():
    hunt = _hunt()
    cog = make_cog(hunt=hunt); cog.bot = MagicMock()
    hub, hub_channel = _hub()
    hub_row = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        channels_archived=4, final_name="H - me", hub_channel_id=3000, left_guild=True))
    ctx = _ctx()
    gone = discord.HTTPException(MagicMock(status=404, reason="Not Found"), "Unknown Channel")
    ctx.send = AsyncMock(side_effect=[MagicMock(edit=AsyncMock()), gone])  # status msg, then summary
    confirm = AsyncMock(return_value=True)
    with patch.object(cog, "_confirm_destructive", new=confirm), \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=AsyncMock(return_value=(hub, hub_row))) as resolve, \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_hunt.return_value = []
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 1000)
    resolve.assert_awaited_once_with(cog.bot, 1000, ctx.guild, ctx.author)
    assert "The Hub" in confirm.await_args.args[1]
    args = cog.service.archive_hunt_to_hub.await_args.args
    assert args[:5] == (ctx.guild, hunt, ctx.author, hub, hub_row)
    hub.get_channel.assert_called_once_with(3000)
    summary = hub_channel.send.await_args.args[0]
    assert "H - me" in summary
    assert "left this server" in summary
```

Append to `tests/test_lock_ordering.py`, directly after `test_bang_archive_hunt_confirms_before_locking`:

```python
@pytest.mark.asyncio
async def test_bang_archive_hunt_to_hub_confirms_before_locking():
    from bot.cogs.puzzles import Puzzles
    from bot.core import hunt_archive
    from tests.conftest import make_cog, make_ctx

    hunt = make_hunt(channel_id=999)
    cog = make_cog(hunt=hunt)
    cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        channels_archived=4, final_name="H - me", hub_channel_id=3000))
    ctx = make_ctx()
    ctx.author.display_name = "me"

    order = []

    async def confirm(*_args, **_kwargs):
        order.append("confirm")
        return True

    cog._confirm_destructive = confirm

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb, \
         patch("bot.cogs.puzzles.puzzles_lock") as lock_mock, \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        PuzzleDb.get_all_from_hunt.return_value = [MagicMock(id=3), MagicMock(id=1)]
        lock_mock.side_effect = lambda ids: order.append("lock") or _null_cm()
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 1000)

    assert order == ["confirm", "lock"]
```

In `tests/test_command_gating.py`'s `EXPECTED`, add directly after `("archive_hunt", "admin"),`:

```python
    ("archive_hunt_to_hub", "admin"),
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_archive_hunt_to_hub_command.py tests/test_lock_ordering.py tests/test_command_gating.py -q > /tmp/test.log 2>&1`
Expected: the new command tests and lock test FAIL with `AttributeError: ... has no attribute 'archive_hunt_to_hub'`. `test_the_map_is_a_census` FAILS, because the map names a command the cogs don't have yet.

- [ ] **Step 3: Extract the summary line from `archive_hunt` (behaviour-preserving)**

Add a module-level helper just above `class Puzzles` (next to the other module helpers like `is_admin`):

```python
def _archive_summary(report):
    """The completion line shared by !archive_hunt and !archive_hunt_to_hub."""
    summary = (f":white_check_mark: Archived hunt into **{report.final_name}** — "
               f"{report.channels_archived} channel(s), {report.messages_moved} message(s) moved, "
               f"{report.voice_deleted} voice channel(s) removed.")
    if report.failures:
        summary += f"\n:warning: {len(report.failures)} channel(s) failed: {', '.join(report.failures)}"
    return summary
```

In `archive_hunt`, replace the `summary = (...)` assignment and its `if report.failures:` addition with `summary = _archive_summary(report)`, leaving `await ctx.send(summary)` as is. Run `tests/test_archive_hunt_command.py` and confirm it still passes unedited.

- [ ] **Step 4: Add the command directly below `archive_hunt`**

```python
    @commands.command()
    @is_admin()
    async def archive_hunt_to_hub(self, ctx, hub_id: int):
        """*Archive the whole hunt onto another server (the hub), then leave this one.*

        Usage: `!archive_hunt_to_hub <hub server id>`
        Example: `!archive_hunt_to_hub 123456789012345678`
        Run in any channel of the hunt. Admins/mods only, and you must be an admin
        on the hub too. Condenses every channel (the general one included) into
        threads on a new channel on the hub, deletes all its categories/channels,
        moves the hunt to the hub on the dashboard, and removes the bot from this
        server if no other hunt uses it. Asks to confirm. Google sheets are kept.
        Bang-only: no web-dashboard equivalent, by design."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        if getattr(hunt, "layout_mode", "") == "forum":
            await ctx.send(":x: !archive_hunt_to_hub is not available for forum hunts yet")
            return
        try:
            hub, hub_row = await hunt_archive.resolve_hub(self.bot, hub_id, ctx.guild, ctx.author)
        except hunt_archive.HubUnavailable as e:
            await ctx.send(f":x: {e}")
            return
        team = hunt_archive.resolve_team_name(ctx.guild, hunt, ctx.author)
        final = hunt_archive.archived_channel_name(hunt, team)
        desc = (f"Archive hunt **{hunt.name}** to **{hub.name}**? Condense every channel "
                f"(this one included) into threads on a new channel **{final}** there, "
                f"delete all its categories and channels (including voice), move the hunt "
                f"to {hub.name} on the dashboard, and remove the bot from this server if no "
                f"other hunt uses it. Google sheets are kept. **Not reversible.**")
        # Confirm BEFORE locking — same reasoning as archive_hunt.
        if not await self._confirm_destructive(ctx, desc, sheets_deleted=False):
            return
        member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_hunt(hunt.id)]
        async with puzzles_lock(member_puzzle_ids):
            status = await ctx.send("⏳ Archiving…")

            async def on_progress(done, total, name):
                try:
                    await status.edit(content=f"⏳ Archived {done}/{total} — {name}")
                except discord.HTTPException:
                    pass

            report = await self.service.archive_hunt_to_hub(
                ctx.guild, hunt, ctx.author, hub, hub_row, on_progress=on_progress)
            summary = _archive_summary(report)
            if report.left_guild:
                summary += "\nThe bot has left this server; its owner can now delete it."
            hub_channel = hub.get_channel(report.hub_channel_id)
            if hub_channel is not None:
                await hub_channel.send(summary)
            try:
                # The channel this ran in is usually archived by now, and the bot
                # may have left the server, so this reply is best-effort only.
                await ctx.send(summary)
            except discord.HTTPException:
                pass
```

The test pins the phrase `left this server` in the summary.

- [ ] **Step 5: Run the command, lock and gating tests**

Same command as Step 2, plus `tests/test_archive_hunt_command.py`. Expected: all PASS.

- [ ] **Step 6: Mutation checks (restore each)**

1. Delete the `try:`/`except discord.HTTPException: pass` wrapper, leaving a bare `await ctx.send(summary)`. `test_names_the_hub_then_reports_there_even_if_ctx_channel_is_gone` must fail with `HTTPException`.
2. Move the `resolve_hub` block below the `_confirm_destructive` call. `test_refuses_a_bad_hub_without_asking_to_confirm` must fail.

- [ ] **Step 7: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_archive_hunt_to_hub_command.py tests/test_lock_ordering.py tests/test_command_gating.py
git commit -m "feat(archive): !archive_hunt_to_hub archives a team-server hunt onto a hub"
```

---

### Task 6: Docs and full-suite gate

**Files:**
- Modify: `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`
- Modify: `docs/commands.md`
- Modify: `docs/user-guide.md`

- [ ] **Step 1: Amend the spec**

In the spec:
- Under **Data model**, replace the `hunts.archive_channel_id` bullet and the `hub_guild_id in config.json` bullet with:
  > - **No stored hub or archive channel.** *Changed 2026-09-24:* the hub is named on the command, `!archive_hunt_to_hub <hub server id>`, which checks that the invoker is an admin on that hub. The bot creates a new uncategorised hub channel per hunt, named as archived channels are today, and tags it with the topic `puzzbot-archive:<hunt uid>` so a re-run resumes into it. `!setup_server` may later store a default hub. Implemented by `docs/superpowers/plans/2026-09-24-cross-server-hunt-archive.md`.
- In **The bot: `!setup_server`**, step 4: drop `and archive_channel_id`.
- In **Archiving and teardown**, change the first paragraph to: "`!archive_hunt_to_hub <hub id>` writes the hunt's history threads into a new per-hunt channel on the hub, the team server's general channel included. `!archive_hunt` is unchanged and still archives in place."
- In **Re-homing**, change "`archive_hunt` must also re-point" to "`archive_hunt_to_hub` must also re-point", and "`channel_id` to the hub archive channel" to "`channel_id` to the new hub channel". Add: *the bot leaves only when no hunt row, archived or not, still references the team server's `guilds` row, and only after a run with no failures.*
- Update the **Status** line to: `Design approved; slice 1 (cross-server archive, as !archive_hunt_to_hub) implemented — see plan 2026-09-24.`

- [ ] **Step 2: `docs/commands.md`**

In the summary table row at line ~50, change `` `!archive_hunt` · `` to `` `!archive_hunt` · `!archive_hunt_to_hub` · ``.

In the archive command table, add a row directly after the `!archive_hunt` row:

```markdown
| `!archive_hunt_to_hub <hub server id>` | any hunt channel | Archive the **whole hunt onto another server** (the hub): condense every channel, this one included, into threads on a new hub channel `{YYYY-MM} {hunt name} - {team}`, delete all categories/channels, move the hunt to the hub on the dashboard, and remove the bot from this server if no other hunt is on it. You must be an admin on the hub as well. Refuses before confirming if the bot isn't in the hub or you aren't a hub admin. If any channel fails, the bot stays in the server; re-run from a surviving channel. Sheets kept. |
```

- [ ] **Step 3: `docs/user-guide.md`**

After the `!archive_hunt` sentence in the *After the hunt* bullet (line ~420), insert: ``For a hunt played on its own team server, `!archive_hunt_to_hub <hub server id>` does the same but lands the threads on the hub and then removes the bot from the team server.``

- [ ] **Step 4: Full suite**

Run: `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: exit 0. On failure, read the log with `tail -n 20 /tmp/test.log` and `grep -m 20 FAILED /tmp/test.log`.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md docs/commands.md docs/user-guide.md
git commit -m "docs: !archive_hunt_to_hub, and the spec's hub-on-the-command decision"
```

---

## Manual verification (after merge to staging; not automatable)

On the test deployment (`puzzweb.home.meldon.net`, DB `puzzlebot_test`):

1. Invite the bot to a scratch server, `!create_hunt`, add two puzzles and post a few messages in each channel.
2. As someone who is **not** an admin on the hub, run `!archive_hunt_to_hub <hub id>`. Expect `:x: you aren't an admin on …`, with no prompt and no changes.
3. As a hub admin, run `!archive_hunt_to_hub <scratch server's own id>`. Expect `:x: that's this server …`.
4. As a hub admin, run `!archive_hunt_to_hub <hub id>` and confirm. Check: a new uncategorised channel appears on the hub, with one thread per channel (general included) and the summary posted in it. The scratch server's categories are gone. The bot has left the scratch server.
5. Dashboard: the hunt shows as archived for a hub member who was **never** in the scratch server.
