# Cross-server hunt archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `!archive_hunt` run on a team server writes the hunt's whole history into a new channel on the hub, points the hunt's database row at the hub, and has the bot leave the team server.

**Architecture:** Two new `config.json` keys name the hub guild and a hub category. When a hunt is archived from any guild other than the hub, `PuzzleService.archive_hunt` takes a new path: it creates (or on a re-run, finds) a per-hunt channel in that hub category, sweeps **every** channel of the hunt into threads there (including the team server's general channel, which is no longer spared), moves `hunts.guild_id`/`hunts.channel_id` to the hub, and leaves the team server once no hunt row still points at it. Hunts archived on the hub itself take the existing path, unchanged.

**Tech Stack:** Python 3.12, discord.py, MariaDB via `bot/store/mysqldb.py`, pytest (`asyncio_mode = auto`), `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`, sections *Archiving and teardown* and *Re-homing the hunt row to the hub*. This plan is **slice 1** of that spec. It departs from the spec in one place, decided with the user on 2026-09-24 (see *Decision* below). Task 6 writes that decision back into the spec.

## Decision taken during planning (overrides the spec)

The spec gives each hunt a `hunts.archive_channel_id` column, set up in advance by `!setup_server`. **Dropped.** Instead:

- `config.json` holds `hub_guild_id` and `hub_archive_category_id`.
- At archive time the bot creates a **new hub channel per hunt**, inside that category, named exactly as today's surviving archive channel (`hunt_archive.archived_channel_name`). This is the same one-channel-per-archived-hunt shape the hub already has.
- The channel is tagged with the topic `puzzbot-archive:<hunt.uid>`. A re-run after a partial failure finds that channel by its topic and resumes, rather than creating a second one.

Result: no migration, no setup step. The slice works on its own before `!setup_server` exists.

## Roadmap (the rest of the spec, one plan each, not written yet)

1. **This plan:** cross-server archive.
2. Template + `!setup_server` (preflight, `GuildSettings`, `create_hunt`, emojis, `--organisers`, `#lobby` welcome, `hunts.roster_role_id`).
3. Private invites: `hunt_invites` table, per-player DM, `on_member_join` auto-grant, `!invite`, late-joiner listener on the hub.
4. `#lobby` persistent join/leave panel + mod elevation from hub admin status.
5. Access audit: `hunt_access_log` + hub mirror channel via `on_member_update`.

**Drift to re-check when planning slice 2:** since the spec was written, admin/organiser gating moved to role **ids** (M8: `guilds.admin_role_ids` / `organiser_role_ids`, name matching only as the fallback when unset), and `archive_hunt` is now gated by `@is_admin()` rather than `has_any_role(*ADMIN_ROLES)`. The spec's claim that "a role named `mod` clears every gate" holds only while the team server's tier ids are unset.

## Global Constraints

- Default branch is **`staging`**, not `main`. Work in a worktree branched from **local** `staging` (origin is intentionally behind). Copy the gitignored `config.json` + `google_secrets.json` into the new worktree before running tests, or conftest dies on MySQL access-denied (pytest exit 4).
- Test command (always wrapped, always to a file, single statement):
  `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`
- Patch where names are **looked up**: `bot.core.puzzle_service.HuntJsonDb`, `bot.core.hunt_archive.GuildSettingsDb`, `bot.core.hunt_archive.resolve_hub_target`, etc.
- Hunts archived **on the hub**, and every hunt when `hub_guild_id` is unset (0), must behave exactly as today. The existing `tests/test_archive_hunt_service.py` and `tests/test_archive_hunt_command.py` must stay green **without edits**.
- `hunts.role_id` is **not** touched by re-homing (0 means "legacy hunt" in `hunt_visible`).
- Keep the per-channel **webhook**. `ensure_webhook` creates one on the hub channel so messages keep their original authors. The spec's "no webhook" rejects storing a cross-guild webhook URL in the database, which this plan does not do. The bot therefore needs Manage Webhooks on the hub (it has Administrator there today).
- Once `hub_guild_id` is set, **every** non-hub guild archives to the hub, including any existing secondary server. That is intended; the docs must say so.
- No migration in this slice.

---

### Task 1: Hub config keys and hub-target helpers

**Files:**
- Modify: `bot/utils/config.py` (default_config + `Config.__init__`)
- Modify: `config.json.dist`
- Modify: `bot/core/hunt_archive.py` (imports, `HuntArchiveReport`, new helpers)
- Modify: `tests/conftest.py` (autouse fixture)
- Create: `tests/test_hub_archive_target.py`

**Interfaces:**
- Produces:
  - `config.hub_guild_id: int` (0 = no hub), `config.hub_archive_category_id: int` (0 = unset)
  - `hunt_archive.HubUnavailable(Exception)`
  - `hunt_archive.is_team_server(guild) -> bool`
  - `hunt_archive.resolve_hub_target(bot) -> tuple[discord.CategoryChannel, GuildSettings]` (raises `HubUnavailable`)
  - `hunt_archive.archive_topic(hunt) -> str`
  - `async hunt_archive.ensure_hub_archive_channel(category, hunt, name) -> discord.TextChannel`
  - `HuntArchiveReport` new fields: `aborted: str = ""`, `hub_channel_id: int = 0`, `left_guild: bool = False`

- [ ] **Step 1: Add the autouse fixture so no test depends on the developer's real `config.json`**

Append to `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _no_hub_by_default(monkeypatch):
    """Pin 'no hub configured' for every test. archive_hunt branches on
    config.hub_guild_id, and the real config.json on a dev box may set it;
    tests that exercise the hub path monkeypatch these back on."""
    from bot.utils import config
    monkeypatch.setattr(config, "hub_guild_id", 0, raising=False)
    monkeypatch.setattr(config, "hub_archive_category_id", 0, raising=False)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_hub_archive_target.py`:

```python
"""Hub-archive helpers: which guilds archive to the hub, where on the hub, and
finding a half-built archive channel again on a re-run."""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core import hunt_archive
from bot.store.hunt_data import HuntData
from bot.utils import config


def _hub(monkeypatch, hub=1000, category=1001):
    monkeypatch.setattr(config, "hub_guild_id", hub)
    monkeypatch.setattr(config, "hub_archive_category_id", category)


def _guild(id):
    g = MagicMock(); g.id = id
    return g


def test_no_hub_configured_means_no_guild_is_a_team_server():
    assert hunt_archive.is_team_server(_guild(2000)) is False


def test_the_hub_itself_is_not_a_team_server(monkeypatch):
    _hub(monkeypatch)
    assert hunt_archive.is_team_server(_guild(1000)) is False


def test_any_other_guild_is_a_team_server(monkeypatch):
    _hub(monkeypatch)
    assert hunt_archive.is_team_server(_guild(2000)) is True


def _bot_with_hub(category):
    hub = MagicMock(); hub.id = 1000
    hub.get_channel.return_value = category
    bot = MagicMock(); bot.get_guild.return_value = hub
    return bot, hub


def test_resolve_hub_target_returns_category_and_hub_guild_row(monkeypatch):
    _hub(monkeypatch)
    category = MagicMock(spec=discord.CategoryChannel)
    bot, hub = _bot_with_hub(category)
    row = MagicMock(); row.id = 9
    with patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        gsdb.get.return_value = row
        assert hunt_archive.resolve_hub_target(bot) == (category, row)
    bot.get_guild.assert_called_once_with(1000)
    hub.get_channel.assert_called_once_with(1001)
    gsdb.get.assert_called_once_with(1000)


def test_resolve_hub_target_refuses_when_bot_cannot_see_hub(monkeypatch):
    _hub(monkeypatch)
    bot = MagicMock(); bot.get_guild.return_value = None
    with pytest.raises(hunt_archive.HubUnavailable, match="1000"):
        hunt_archive.resolve_hub_target(bot)


def test_resolve_hub_target_refuses_when_category_id_is_not_a_category(monkeypatch):
    _hub(monkeypatch)
    bot, _ = _bot_with_hub(MagicMock(spec=discord.TextChannel))
    with pytest.raises(hunt_archive.HubUnavailable, match="hub_archive_category_id"):
        hunt_archive.resolve_hub_target(bot)


def test_resolve_hub_target_refuses_when_hub_has_no_guilds_row(monkeypatch):
    _hub(monkeypatch)
    bot, _ = _bot_with_hub(MagicMock(spec=discord.CategoryChannel))
    with patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        gsdb.get.return_value = None
        with pytest.raises(hunt_archive.HubUnavailable, match="guilds row"):
            hunt_archive.resolve_hub_target(bot)


def _hunt(uid):
    h = HuntData(); h.uid = uid
    return h


def _chan(topic):
    c = MagicMock(); c.topic = topic
    return c


async def test_ensure_hub_archive_channel_reuses_the_one_tagged_for_this_hunt():
    other, mine = _chan("puzzbot-archive:zzz"), _chan("puzzbot-archive:abc")
    category = MagicMock(); category.text_channels = [other, mine]
    category.create_text_channel = AsyncMock()
    got = await hunt_archive.ensure_hub_archive_channel(category, _hunt("abc"), "H - team")
    assert got is mine
    category.create_text_channel.assert_not_awaited()


async def test_ensure_hub_archive_channel_creates_a_tagged_channel_when_none_exists():
    category = MagicMock(); category.text_channels = [_chan("puzzbot-archive:zzz"), _chan(None)]
    created = MagicMock()
    category.create_text_channel = AsyncMock(return_value=created)
    got = await hunt_archive.ensure_hub_archive_channel(category, _hunt("abc"), "H - team")
    assert got is created
    category.create_text_channel.assert_awaited_once_with(
        "H - team", topic="puzzbot-archive:abc")
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_hub_archive_target.py -q > /tmp/test.log 2>&1`
Expected: FAIL, with `AttributeError: module 'bot.core.hunt_archive' has no attribute 'is_team_server'` (and similar).

- [ ] **Step 4: Add the config keys**

In `bot/utils/config.py`, add to `default_config` after `"max_channels_per_category": 50,`:

```python
    # Cross-server archive (multi-team hunts on per-team servers). When
    # hub_guild_id is set, !archive_hunt in any OTHER guild writes the hunt's
    # history into a new channel under hub_archive_category_id on the hub,
    # re-homes the hunt row there, and leaves the team server. 0 = off.
    "hub_guild_id": 0,
    "hub_archive_category_id": 0,
```

and at the end of `Config.__init__`, after the `max_channels_per_category` lines:

```python
        self.hub_guild_id = int(self.config.get(
            "hub_guild_id", default_config.get("hub_guild_id")) or 0)
        self.hub_archive_category_id = int(self.config.get(
            "hub_archive_category_id", default_config.get("hub_archive_category_id")) or 0)
```

In `config.json.dist`, after `"max_channels_per_category": 50,` add:

```json
  "hub_guild_id": 0,
  "hub_archive_category_id": 0,
```

- [ ] **Step 5: Add the helpers to `bot/core/hunt_archive.py`**

Change the store import and add the config import:

```python
from bot.store import GuildSettingsDb, PuzzleJsonDb
from bot.core.discord_channels import DELETE_REASON
from bot.utils import config
```

Add three fields to the end of `HuntArchiveReport`:

```python
    final_name: str = ""
    # Hub path only: why nothing was archived (hub missing or misconfigured).
    aborted: str = ""
    # Hub path only: the hub channel the history landed in.
    hub_channel_id: int = 0
    # Hub path only: True once the bot has left the emptied team server.
    left_guild: bool = False
```

Add after `archived_channel_name`:

```python
class HubUnavailable(Exception):
    """The configured hub cannot take an archive. Raised before anything is
    touched, so a refused archive leaves the team server exactly as it was."""


def is_team_server(guild) -> bool:
    """True when a hub is configured and *guild* is not it. archive_hunt then
    writes to the hub instead of the hunt's own guild."""
    return bool(config.hub_guild_id) and guild.id != config.hub_guild_id


def resolve_hub_target(bot):
    """(hub archive category, hub `guilds` row) or HubUnavailable.

    The row is needed to re-home the hunt: hunts.guild_id is an FK to guilds.id
    (the row id), not the snowflake. on_guild_join creates it."""
    hub = bot.get_guild(config.hub_guild_id)
    if hub is None:
        raise HubUnavailable(f"the bot can't see hub guild {config.hub_guild_id}")
    category = hub.get_channel(config.hub_archive_category_id)
    if not isinstance(category, discord.CategoryChannel):
        raise HubUnavailable(
            f"hub_archive_category_id {config.hub_archive_category_id} is not a category on the hub")
    row = GuildSettingsDb.get(hub.id)
    if row is None:
        raise HubUnavailable("the hub has no guilds row (re-invite the bot there)")
    return category, row


def archive_topic(hunt) -> str:
    """Topic tagging a hub archive channel with its hunt, so a re-run finds it."""
    return f"puzzbot-archive:{hunt.uid}"


async def ensure_hub_archive_channel(category, hunt, name):
    """The hub channel this hunt archives into: the one already tagged for it
    (a prior partial run), else a new one named *name*."""
    marker = archive_topic(hunt)
    for ch in category.text_channels:
        if ch.topic == marker:
            return ch
    return await category.create_text_channel(name, topic=marker)
```

- [ ] **Step 6: Run the new tests and the existing archive tests**

Run: `timeout 180 pipenv run python -m pytest tests/test_hub_archive_target.py tests/test_archive_hunt_service.py tests/test_archive_hunt_command.py -q > /tmp/test.log 2>&1`
Expected: all PASS.

- [ ] **Step 7: Mutation check**

In `is_team_server`, temporarily change `!=` to `==`. Re-run `tests/test_hub_archive_target.py`: the two `team_server` tests must go red. Restore by editing the line back.

- [ ] **Step 8: Commit**

```bash
git add bot/utils/config.py config.json.dist bot/core/hunt_archive.py tests/conftest.py tests/test_hub_archive_target.py
git commit -m "feat(archive): hub config keys and hub-target helpers for cross-server archive"
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
        lives in the hunt's own guild, None when *dest* is on the hub."""
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

        archive_hunt leaves a team server only when this is zero: an archived hunt
        still homed there would vanish from the dashboard once the bot left,
        because hunt_visible needs the user to share that guild with the bot."""
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

### Task 4: The hub path in `PuzzleService.archive_hunt`

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Create: `tests/test_archive_hunt_to_hub.py`

**Interfaces:**
- Consumes: everything Task 1 produces, plus `_hunt_categories`, `_sweep_hunt_categories`, `_drop_split_layout_rows` (Task 2) and `HuntJsonDb.count_for_guild` (Task 3).
- Produces: `archive_hunt(guild, hunt, invoker, *, on_progress=None)` keeps its signature. On a team server it returns a report with `hub_channel_id` set, and `aborted` or `left_guild` where they apply.

Required order, which the tests pin: resolve hub (abort before any mutation) → create/find hub channel → sweep **all** channels, general last → drop split rows → re-home + archive + commit → count → leave only if the count is zero **and** there were no failures.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_archive_hunt_to_hub.py`:

```python
"""archive_hunt from a team server: history to a new hub channel, row re-homed
to the hub, bot leaves the emptied server. Per-channel archiving is patched out
(see test_archive_hunt_service.py for the same arrangement on the local path)."""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.core import hunt_archive
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from bot.utils import config

HUB, TEAM = 1000, 2000


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


def _guild(guild_id):
    """general(60) in main_cat(50); puzzle(71) + voice(72) in round_cat(70)."""
    general, puzzle, voice = _text(60), _text(71), _voice(72)
    main_cat = MagicMock(); main_cat.id = 50; main_cat.channels = [general]; main_cat.delete = AsyncMock()
    general.category = main_cat
    round_cat = MagicMock(); round_cat.id = 70; round_cat.channels = [puzzle, voice]; round_cat.delete = AsyncMock()
    g = MagicMock(); g.id = guild_id; g.categories = [main_cat, round_cat]
    g.get_channel.return_value = general
    g.leave = AsyncMock()
    return SimpleNamespace(guild=g, general=general, puzzle=puzzle, voice=voice,
                           main_cat=main_cat, round_cat=round_cat)


@contextmanager
def _patched(svc, *, archive_side_effect=None, count=0, hub_error=None):
    hub_dest = _text(3000)
    hub_row = MagicMock(); hub_row.id = 9
    hub_category = MagicMock(spec=discord.CategoryChannel)
    tg = MagicMock(); tg.id = 3; tg.category_id = 70
    with ExitStack() as st:
        p = lambda *a, **k: st.enter_context(patch(*a, **k))
        m = SimpleNamespace(
            hub_dest=hub_dest, hub_row=hub_row, hub_category=hub_category,
            resolve=p("bot.core.hunt_archive.resolve_hub_target",
                      side_effect=hub_error, return_value=(hub_category, hub_row)),
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


def _hub_on(monkeypatch):
    monkeypatch.setattr(config, "hub_guild_id", HUB)
    monkeypatch.setattr(config, "hub_archive_category_id", 1001)


def _invoker():
    i = MagicMock(); i.display_name = "me"
    return i


async def test_team_server_archives_every_channel_to_hub_rehomes_and_leaves(monkeypatch):
    _hub_on(monkeypatch)
    svc, hunt, t = _service(), _hunt(), _guild(TEAM)
    with _patched(svc) as m:
        report = await svc.archive_hunt(t.guild, hunt, _invoker())

    m.ensure_hub.assert_awaited_once_with(m.hub_category, hunt, "H - me")
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


async def test_unreachable_hub_aborts_before_touching_anything(monkeypatch):
    _hub_on(monkeypatch)
    svc, hunt, t = _service(), _hunt(), _guild(TEAM)
    with _patched(svc, hub_error=hunt_archive.HubUnavailable("no hub")) as m:
        report = await svc.archive_hunt(t.guild, hunt, _invoker())
    assert report.aborted == "no hub"
    m.ensure_hub.assert_not_awaited()
    m.archive.assert_not_awaited()
    m.safe_del.assert_not_awaited()
    m.hdb.commit.assert_not_called()
    t.guild.leave.assert_not_awaited()
    assert (hunt.guild_id, hunt.channel_id, hunt.archived) == (5, 60, False)


async def test_a_failed_channel_still_rehomes_but_keeps_the_bot_in_the_server(monkeypatch):
    _hub_on(monkeypatch)
    svc, hunt, t = _service(), _hunt(), _guild(TEAM)
    boom = [RuntimeError("boom"), hunt_archive.ChannelArchiveResult(messages_moved=1)]
    with _patched(svc, archive_side_effect=boom) as m:
        report = await svc.archive_hunt(t.guild, hunt, _invoker())
    assert report.failures == ["text71"]
    assert (hunt.guild_id, hunt.channel_id, hunt.archived) == (9, 3000, True)
    t.guild.leave.assert_not_awaited()       # an admin must be able to re-run
    assert report.left_guild is False


async def test_another_hunt_on_the_team_server_keeps_the_bot_there(monkeypatch):
    _hub_on(monkeypatch)
    svc, hunt, t = _service(), _hunt(), _guild(TEAM)
    with _patched(svc, count=1):
        report = await svc.archive_hunt(t.guild, hunt, _invoker())
    t.guild.leave.assert_not_awaited()
    assert report.left_guild is False


async def test_a_hunt_on_the_hub_itself_takes_the_local_path(monkeypatch):
    _hub_on(monkeypatch)
    svc, hunt, t = _service(), _hunt(), _guild(HUB)
    with _patched(svc) as m:
        report = await svc.archive_hunt(t.guild, hunt, _invoker())
    m.resolve.assert_not_called()
    m.rename.assert_awaited_once_with(t.general, "H - me")
    assert [c.args[3] for c in m.archive.await_args_list] == [t.puzzle]
    assert (hunt.guild_id, hunt.channel_id) == (5, 60)
    t.guild.leave.assert_not_awaited()
    assert report.hub_channel_id == 0
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_archive_hunt_to_hub.py -q > /tmp/test.log 2>&1`
Expected: the four team-server tests FAIL (the local path runs, so `ensure_hub` is never awaited). `test_a_hunt_on_the_hub_itself_takes_the_local_path` PASSES already, which is correct: it guards the existing behaviour.

- [ ] **Step 3: Branch at the top of `archive_hunt`**

Directly after `report = hunt_archive.HuntArchiveReport()`:

```python
        if hunt_archive.is_team_server(guild):
            return await self._archive_hunt_to_hub(
                guild, hunt, invoker, report, on_progress=on_progress)
```

Append one sentence to `archive_hunt`'s docstring: `From a team server (see hunt_archive.is_team_server) the history goes to the hub instead — see _archive_hunt_to_hub.`

- [ ] **Step 4: Add `_archive_hunt_to_hub` directly below `archive_hunt`**

```python
    async def _archive_hunt_to_hub(self, guild, hunt, invoker, report, *, on_progress):
        """archive_hunt for a hunt living on a team server: condense EVERY channel
        (the general one included, since the whole server is about to be
        abandoned) into threads on a per-hunt channel on the hub, re-home the
        hunt row to the hub so the dashboard still shows it once the team server
        is gone, then leave the team server if nothing else is homed there.

        Re-runnable: the hub channel is found again by its topic, and threads
        already written are skipped by name (see _sweep_hunt_categories)."""
        try:
            hub_category, hub_row = hunt_archive.resolve_hub_target(self.bot)
        except hunt_archive.HubUnavailable as e:
            report.aborted = str(e)
            return report

        team = hunt_archive.resolve_team_name(guild, hunt, invoker)
        report.final_name = hunt_archive.archived_channel_name(hunt, team)
        dest = await hunt_archive.ensure_hub_archive_channel(
            hub_category, hunt, report.final_name)
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
                logger.exception("archive_hunt: could not leave team guild %s", guild.id)
        return report
```

- [ ] **Step 5: Run the new and existing archive tests**

Run: `timeout 180 pipenv run python -m pytest tests/test_archive_hunt_to_hub.py tests/test_archive_hunt_service.py tests/test_hub_archive_target.py -q > /tmp/test.log 2>&1`
Expected: all PASS.

- [ ] **Step 6: Mutation checks (restore each by editing the line back)**

1. Change `keep_channel_id=None` to `keep_channel_id=hunt.channel_id` in `_archive_hunt_to_hub`. The first test must fail on the `[t.puzzle, t.general]` assertion.
2. Move the `count_for_guild` check above `HuntJsonDb.commit(hunt)`. The first test must fail on the ordering assertion.
3. Delete `not report.failures and`. `test_a_failed_channel_still_rehomes_but_keeps_the_bot_in_the_server` must fail.

- [ ] **Step 7: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_archive_hunt_to_hub.py
git commit -m "feat(archive): archive a team-server hunt to the hub and re-home its row"
```

---

### Task 5: `!archive_hunt` command, hub wording and reporting

**Files:**
- Modify: `bot/cogs/puzzles.py` (`archive_hunt`, near line 3995)
- Modify: `tests/test_archive_hunt_command.py` (append tests only; do not edit existing ones)

**Interfaces:**
- Consumes: `hunt_archive.is_team_server`, and the `HuntArchiveReport` fields `aborted`, `hub_channel_id`, `left_guild`.

On a team server, the channel the command ran in has usually been deleted by the time the summary is sent, and the bot may have left the server. The summary must therefore go to the hub channel, and the reply in `ctx` must not raise.

- [ ] **Step 1: Write the failing tests (append to `tests/test_archive_hunt_command.py`)**

```python
import discord

from bot.utils import config


def _hub_on(monkeypatch):
    monkeypatch.setattr(config, "hub_guild_id", 1000)
    monkeypatch.setattr(config, "hub_archive_category_id", 1001)


async def test_archive_hunt_on_team_server_reports_to_hub_even_if_ctx_channel_is_gone(monkeypatch):
    _hub_on(monkeypatch)
    hunt = _hunt()
    cog = make_cog(hunt=hunt)
    cog.bot = MagicMock()
    hub_channel = MagicMock(); hub_channel.send = AsyncMock()
    cog.bot.get_channel.return_value = hub_channel
    cog.service.archive_hunt = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        channels_archived=4, final_name="H - me", hub_channel_id=3000, left_guild=True))
    ctx = make_ctx()
    ctx.author.display_name = "me"
    gone = discord.HTTPException(MagicMock(status=404, reason="Not Found"), "Unknown Channel")
    ctx.send = AsyncMock(side_effect=[MagicMock(edit=AsyncMock()), gone])  # status msg, then summary
    confirm = AsyncMock(return_value=True)
    with patch.object(cog, "_confirm_destructive", new=confirm), \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_hunt.return_value = []
        await Puzzles.archive_hunt.callback(cog, ctx)
    assert "hub" in confirm.await_args.args[1]
    cog.bot.get_channel.assert_called_once_with(3000)
    summary = hub_channel.send.await_args.args[0]
    assert "H - me" in summary
    assert "left this server" in summary


async def test_archive_hunt_on_team_server_says_nothing_happened_when_hub_is_unavailable(monkeypatch):
    _hub_on(monkeypatch)
    hunt = _hunt()
    cog = make_cog(hunt=hunt)
    cog.bot = MagicMock()
    cog.service.archive_hunt = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        aborted="the bot can't see hub guild 1000"))
    ctx = make_ctx()
    ctx.author.display_name = "me"
    ctx.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    with patch.object(cog, "_confirm_destructive", new=AsyncMock(return_value=True)), \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_hunt.return_value = []
        await Puzzles.archive_hunt.callback(cog, ctx)
    ctx.send.assert_awaited_with(
        ":x: Nothing was archived: the bot can't see hub guild 1000")
    cog.bot.get_channel.assert_not_called()
```

(`_hunt`, `make_cog`, `make_ctx`, `Puzzles`, `hunt_archive`, `AsyncMock`, `MagicMock` and `patch` are already imported at the top of this file. Move the two new imports up to join them.)

- [ ] **Step 2: Run them to confirm they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_archive_hunt_command.py -q > /tmp/test.log 2>&1`
Expected: the two new tests FAIL (the confirm text has no "hub"; the summary goes to `ctx.send` and raises). The existing tests PASS.

- [ ] **Step 3: Implement in the cog's `archive_hunt`**

Replace the `desc = (...)` assignment with:

```python
        to_hub = hunt_archive.is_team_server(ctx.guild)
        if to_hub:
            desc = (f"Archive hunt **{hunt.name}** to the hub? Condense every channel "
                    f"(this one included) into threads on a new hub channel **{final}**, "
                    f"delete all its categories and channels (including voice), mark the "
                    f"hunt archived, and remove the bot from this server if no other hunt "
                    f"uses it. Google sheets are kept. **Not reversible.**")
        else:
            desc = (f"Archive hunt **{hunt.name}**? Condense every channel into threads on this "
                    f"channel, delete all its categories and channels (including voice), rename "
                    f"this channel to **{final}**, and mark the hunt archived. Google sheets are "
                    f"kept. **Not reversible.**")
```

Replace everything from `report = await self.service.archive_hunt(...)` to the end of the method with:

```python
            report = await self.service.archive_hunt(ctx.guild, hunt, ctx.author, on_progress=on_progress)
            if report.aborted:
                await ctx.send(f":x: Nothing was archived: {report.aborted}")
                return
            summary = (f":white_check_mark: Archived hunt into **{report.final_name}** — "
                       f"{report.channels_archived} channel(s), {report.messages_moved} message(s) moved, "
                       f"{report.voice_deleted} voice channel(s) removed.")
            if report.failures:
                summary += f"\n:warning: {len(report.failures)} channel(s) failed: {', '.join(report.failures)}"
            if not to_hub:
                await ctx.send(summary)
                return
            if report.left_guild:
                summary += "\nThe bot has left this server; it can now be deleted by its owner."
            hub_channel = self.bot.get_channel(report.hub_channel_id)
            if hub_channel is not None:
                await hub_channel.send(summary)
            try:
                # The channel this ran in is usually archived by now, and the bot
                # may have left the server, so this reply is best-effort only.
                await ctx.send(summary)
            except discord.HTTPException:
                pass
```

The summary wording must contain `left this server` (the test pins it).

Update the command docstring's first line and body:

```python
        """*Archive the whole hunt: condense every channel into threads, delete all its categories/channels, and mark it archived.*

        Usage: `!archive_hunt`
        Example: `!archive_hunt`
        Run in any channel of the hunt. Admins/mods only. Asks to confirm, then runs
        unattended. Google sheets are kept. On the hub, the threads go on this
        channel, which is renamed. On a team server (config `hub_guild_id`), they go
        on a new channel on the hub, the hunt moves there, and the bot then leaves.
        Bang-only: no web-dashboard equivalent, by design."""
```

- [ ] **Step 4: Run the command tests**

Same command as Step 2. Expected: all PASS.

- [ ] **Step 5: Mutation check**

Delete the `try:`/`except discord.HTTPException: pass` wrapper, leaving a bare `await ctx.send(summary)`. The first new test must fail with `HTTPException`. Restore.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_archive_hunt_command.py
git commit -m "feat(archive): !archive_hunt reports to the hub when run on a team server"
```

---

### Task 6: Docs and full-suite gate

**Files:**
- Modify: `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`
- Modify: `docs/new-server-playbook.md`

- [ ] **Step 1: Amend the spec**

In the spec:
- Under **Data model**, replace the `hunts.archive_channel_id` bullet with:
  > - **`hub_archive_category_id` in `config.json`** (alongside `hub_guild_id`). *Changed 2026-09-24, in place of a `hunts.archive_channel_id` column:* `!archive_hunt` creates a new hub channel per hunt in that category, named as archived channels are today, and tags it with the topic `puzzbot-archive:<hunt uid>` so a re-run resumes into it. Nothing to set up per hunt. Implemented by `docs/superpowers/plans/2026-09-24-cross-server-hunt-archive.md`.
- In **The bot: `!setup_server`**, step 4: drop `and archive_channel_id`.
- In **Archiving and teardown**, change "writes its history threads into `hunts.archive_channel_id` on the hub" to "writes its history threads into a new per-hunt channel under `hub_archive_category_id` on the hub, the team server's general channel included".
- In **Re-homing**, change "`channel_id` to the hub archive channel" to "`channel_id` to the new hub channel". Add a note: *the bot leaves only when no hunt row, archived or not, still references the team server's `guilds` row, and only after a run with no failures.*
- Update the **Status** line to: `Design approved; slice 1 (cross-server archive) implemented — see plan 2026-09-24.`

- [ ] **Step 2: Add a short section to `docs/new-server-playbook.md`**

Append:

```markdown
## Archiving team-server hunts to the hub

Set in `config.json`, then restart the bot:

- `hub_guild_id`: the permanent hub server's id.
- `hub_archive_category_id`: a category on the hub that archived hunts land in.

From then on, `!archive_hunt` run in **any server other than the hub** condenses
the whole hunt into a new channel in that category, moves the hunt's dashboard
record to the hub, and removes the bot from the team server once no hunt is left
there. This includes any existing secondary server. If the hub or the category
is missing, it refuses and changes nothing. A run with failed channels keeps
the bot in the server; re-run `!archive_hunt` from a surviving channel to finish.
```

- [ ] **Step 3: Full suite**

Run: `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: exit 0. On failure, read the log with `tail -n 20 /tmp/test.log` and `grep -m 20 FAILED /tmp/test.log`.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md docs/new-server-playbook.md
git commit -m "docs: record the per-hunt hub archive channel decision and hub setup"
```

---

## Manual verification (after merge to staging; not automatable)

On the test deployment (`puzzweb.home.meldon.net`, DB `puzzlebot_test`):

1. Set `hub_guild_id` and `hub_archive_category_id` in the test `config.json`, then restart.
2. Invite the bot to a scratch server, `!create_hunt`, add two puzzles and post a few messages.
3. `!archive_hunt` there. Check: a new channel appears under the hub category with one thread per channel (general included). The scratch server's categories are gone. The bot has left the scratch server.
4. Dashboard: the hunt shows as archived for a hub member who was **never** in the scratch server.
5. Negative case: set `hub_archive_category_id` to a text-channel id, restart, and run `!archive_hunt` in a fresh scratch hunt. Expect `:x: Nothing was archived: …` with the server unchanged.
