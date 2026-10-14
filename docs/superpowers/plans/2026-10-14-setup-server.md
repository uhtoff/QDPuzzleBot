# `!setup_server` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A command, `!setup_server <hub id> <hub role id> <hunt url> <hunt name>`, run once on a team server freshly cloned from the guild template. It checks the server is fit, copies the hub's settings, pins the admin tier, creates the hunt gated to a team role named after the hub role, records that hub role against the hunt, posts a `#lobby` welcome, and then locks itself so it can never run on that server again.

**Architecture:** The work lives in a new ctx-free module, `bot/core/server_setup.py`, in the same style as `bot/core/hunt_archive.py`. `preflight()` inspects the guild and the hub and either returns the resolved targets or raises `SetupRefused` before anything is touched. `run_setup()` performs the steps, each check-then-create, and stamps the run-once lock (`guilds.setup_complete_time`) only as its final step. A run that stops partway can therefore be re-run to finish, and a completed one cannot be re-run. The bang command in `bot/cogs/puzzles.py` wires these together: lock check, then `hunt_archive.resolve_hub` (reused, so the invoker must be a hub admin), then preflight, then run, then report.

**Tech Stack:** Python 3.12, discord.py 2.7.1, MariaDB via `bot/store/mysqldb.py`, pytest (`asyncio_mode = auto`), `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`, sections *The template*, *The human, per team per hunt*, *The bot: `!setup_server`*, and *Data model*. This plan is **slice 2** of that spec (slice 1 is `docs/superpowers/plans/2026-09-24-cross-server-hunt-archive.md`, shipped). It departs from the spec in several places, all decided with the user on 2026-10-14. See *Decisions*. Task 5 writes them back into the spec.

## Decisions taken during planning (override the spec)

- **The hunt is created by `!setup_server`, on the team server.** There is no hub-side hunt beforehand. The spec's `<hunt-uid> <team-name>` arguments become the new hunt's url and name.
- **The hub role is made by hand on the hub and passed in by id.** It is the same role later slices use as the invite roster (the DM list). The bot validates it exists on the named hub and stores it as `hunts.roster_role_id`. It is also what the finished/spoilers channel on the hub is gated by after the hunt. That gating is not part of this slice.
- **The team role on the team server is named after the hub role.** `create_hunt` already makes, and gates the hunt's categories to, a role from a name. So the template carries **no** `hunt-team` role. That name is also what `resolve_team_name` gives `!archive_hunt_to_hub`, so the hub archive channel is named for the team.
- **The template carries no `Hunt` category or `#general`.** `PuzzleService.create_hunt` already creates the hunt's category, its `#general` and the gating overwrites (`_gating_overwrites`). A pre-built category would be a duplicate. The template is reduced to: `@everyone` with View Channels removed, a `mod` role, `#lobby`, `#bot-commands`, and the server settings in the spec's table.
- **Run-once lock: `guilds.setup_complete_time`,** written by its own UPDATE as the very last step and never by `GuildSettingsDb.commit()`. The command refuses on any server where it is set. Every earlier step is check-then-create, so a partial run is finished by re-running the same command. It is the user's "can't be run a second time", with a failed run still recoverable.
- **Who may run it: an admin on the hub.** The check reuses `hunt_archive.resolve_hub`. The command deliberately has **no** `@is_admin()` gate on the team server. On a fresh server the only human is the group account that owns it, which holds no `mod` role, and the hub-admin check is the real guard.
- **The admin tier is pinned by id** to the template's `mod` role (`guilds.admin_role_ids`). This answers the slice-1 drift note: the tier no longer depends on the role's name, and `on_guild_join`'s "pin your admin roles" step is done for you.
- **Settings are copied from the hub's `guilds` row:** `website_url`, `drive_parent_id`, `drive_resources_id`, `discord_bot_emoji`, `discord_use_voice_channels`. `discord_bot_channel` is set to `bot-commands`.
- **Dropped from this slice:**
  - the emoji upload. No emoji set exists anywhere in the code.
  - `--organisers`. On a team server, the organiser tier being open to everyone there is the intended single-team behaviour.
  - the role panel and the invite DMs (slices 3 and 4).
  - a dashboard equivalent (the spec defers it: the bot must already be in the guild).
  - the spec's startup check for stranded hunts.

## Roadmap (unchanged)

1. Cross-server archive. **Shipped.**
2. **This plan:** template + `!setup_server`.
3. Private invites: `hunt_invites`, per-player DM of a single-use invite to every member of `hunts.roster_role_id`, `on_member_join` auto-grant, `!invite`, late-joiner listener on the hub.
4. `#lobby` persistent join/leave panel + mod elevation from hub admin status.
5. Access audit: `hunt_access_log` + hub mirror.

## Global Constraints

- Default branch is **`staging`**, not `main`. Work in a worktree branched from **local** `staging` (origin is intentionally behind). Copy the gitignored `config.json` + `google_secrets.json` into the new worktree before running tests, or conftest dies on MySQL access-denied (pytest exit 4).
- Test command (always wrapped, always to a file, single statement):
  `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`
- Patch where names are **looked up**: `bot.core.server_setup.HuntJsonDb`, `bot.core.server_setup.GuildSettingsDb`, `bot.cogs.puzzles.GuildSettingsDb`, `bot.cogs.puzzles.hunt_archive.resolve_hub`, `bot.cogs.puzzles.server_setup.preflight`, `bot.cogs.puzzles.server_setup.run_setup`.
- **Migration → deploy → code.** `HuntData` gains a field that the generic `commit()` writes by name, so migration 047 must be applied to every environment before code that carries the field. Apply it to `puzzlebot_test` (`pipenv run python migrate.py migrate`) before running the suite.
- `migrations/047_*` is the next free number on `staging` and in every current worktree (checked 2026-10-14). Re-check before merging (parallel worktrees can collide).
- `bot/core` must not import from `bot/web`.
- Compare Discord roles by `.position` integers, never `Role < Role` (MagicMock roles in tests do not order).
- No behaviour change to `!hunt`, `!archive_hunt`, `!archive_hunt_to_hub` or `on_guild_join`; their tests stay green **without edits**.
- `pipenv run basedpyright bot/` must report 0 errors; `pipenv run ruff check` clean.

---

### Task 1: Schema, data fields and the run-once lock

**Files:**
- Create: `migrations/047_setup_server.sql`
- Modify: `bot/store/puzzle_settings.py` (new field on `GuildSettings`)
- Modify: `bot/store/hunt_data.py` (new field on `HuntData`)
- Modify: `bot/store/mysqldb.py` (`MySQLGuildSettingsDb.mark_setup_complete`)
- Modify: `bot/cogs/puzzles.py` (`PROTECTED_SETTING_KEYS`)
- Create: `tests/test_setup_server_store.py`

**Interfaces:**
- Produces:
  - `GuildSettings.setup_complete_time: Optional[datetime.datetime] = None`, read off `SELECT *` by `import_dict` (the `_time` suffix gives it UTC), and **not** written by `commit()`.
  - `GuildSettingsDb.mark_setup_complete(settings: GuildSettings) -> None`: stamps the row and the cached object.
  - `HuntData.roster_role_id: int = 0`, written by the generic `commit()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_setup_server_store.py`:

```python
"""!setup_server's storage: the run-once lock on guilds, and the hub roster role on
hunts. The lock is written ONLY by mark_setup_complete, so no ordinary settings
write (commit) can ever clear it."""
import datetime
import re
from unittest.mock import MagicMock

from bot.store.hunt_data import HuntData
from bot.store.mysqldb import MySQLGuildSettingsDb, MySQLHuntJsonDb
from bot.store.puzzle_settings import GuildSettings


def _stub_db():
    cursor = MagicMock()
    cursor.lastrowid = 5
    mydb = MagicMock()
    mydb.cursor.return_value = cursor
    return mydb, cursor


def test_setup_complete_time_is_read_off_the_row_as_utc():
    stamped = datetime.datetime(2026, 10, 14, 12, 30)
    settings = GuildSettings.import_dict({"id": 3, "guild_id": 99, "setup_complete_time": stamped})
    assert settings.setup_complete_time == stamped.replace(tzinfo=datetime.timezone.utc)


def test_a_guild_never_set_up_reads_as_none():
    settings = GuildSettings.import_dict({"id": 3, "guild_id": 99, "setup_complete_time": None})
    assert settings.setup_complete_time is None


def test_commit_never_writes_the_lock():
    mydb, cursor = _stub_db()
    settings = GuildSettings()
    settings.id = 3
    settings.guild_id = 99
    settings.setup_complete_time = None
    MySQLGuildSettingsDb(mydb=mydb).commit(settings)
    assert "setup_complete_time" not in cursor.execute.call_args.args[0]


def test_mark_setup_complete_stamps_this_row_and_the_cache():
    mydb, cursor = _stub_db()
    db = MySQLGuildSettingsDb(mydb=mydb)
    settings = GuildSettings()
    settings.id = 3          # the row id: distinct from the snowflake so a swap shows
    settings.guild_id = 99
    db.mark_setup_complete(settings)
    stmt, params = cursor.execute.call_args.args
    assert stmt == "UPDATE `guilds` SET `setup_complete_time`=%s WHERE `id`=%s"
    assert params[1] == 3
    # MariaDB DATETIME is naive; import_dict re-attaches UTC on the way back.
    assert params[0].tzinfo is None
    assert settings.setup_complete_time is not None
    assert settings.setup_complete_time.tzinfo is datetime.timezone.utc
    assert db.cached_settings[99] is settings
    cursor.close.assert_called_once()


def test_hunt_commit_writes_roster_role_id():
    mydb, cursor = _stub_db()
    hunt = HuntData(name="H", uid="abc123")
    hunt.roster_role_id = 777
    hunt.role_id = 555       # a neighbouring id column: a swap with it would show
    MySQLHuntJsonDb(mydb).commit(hunt)
    stmt, params = cursor.execute.call_args.args
    columns = re.findall(r"`(\w+)`", stmt.split("VALUES")[0])[1:]  # [0] is the table
    assert params[columns.index("roster_role_id")] == 777
    assert params[columns.index("role_id")] == 555
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_setup_server_store.py -q > /tmp/test.log 2>&1`
Expected: FAIL. `setup_complete_time` and `roster_role_id` are not attributes, and `mark_setup_complete` does not exist.

- [ ] **Step 3: Write the migration**

Create `migrations/047_setup_server.sql`:

```sql
-- 047_setup_server.sql
-- Slice 2 of docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md:
-- !setup_server (plan docs/superpowers/plans/2026-10-14-setup-server.md).
--
-- guilds.setup_complete_time -- !setup_server's run-once lock. NULL means "never
-- completed"; the command stamps it as its very last step and refuses on any
-- guild where it is set. Written only by GuildSettingsDb.mark_setup_complete,
-- never by commit(), so no settings write can clear it.
--
-- hunts.roster_role_id -- the HUB role (made by hand on the hub) listing a
-- team-server hunt's players: the invite DM list for slice 3, and what the hub's
-- post-hunt spoilers channel is gated by. Typed like hunts.role_id.
--
-- PRE-FLIGHT: none needed, and here is why rather than a query. Both columns are
-- additive and defaulted, so this applies cleanly anywhere; the question is
-- only whether the default is RIGHT for existing rows. It is, by construction:
-- !setup_server does not exist before this migration, so no guild has completed
-- it (NULL is true for every row) and no hunt has a hub roster role (NULL is
-- true for every row). Nothing reads either column as meaningful except the
-- code that ships alongside.
--
-- Re-runnable only in the sense that a second apply fails loudly on the
-- duplicate column.

ALTER TABLE `guilds`
  ADD COLUMN `setup_complete_time` DATETIME NULL DEFAULT NULL;

ALTER TABLE `hunts`
  ADD COLUMN `roster_role_id` bigint(20) unsigned DEFAULT NULL AFTER `role_id`;
```

Apply it to the test database: `pipenv run python migrate.py migrate`. Expected: it reports applying `047_setup_server.sql`.

- [ ] **Step 4: Add the fields and the lock method**

In `bot/store/puzzle_settings.py`, add after `organiser_role_ids: str = ""` (keep `typing.Optional` imported; add `from typing import Optional` if absent):

```python
    # !setup_server's run-once lock (migration 047). Set ONLY by
    # GuildSettingsDb.mark_setup_complete -- commit() deliberately never writes
    # it, so no ordinary settings write can clear it.
    setup_complete_time: Optional[datetime.datetime] = None
```

In `bot/store/hunt_data.py`, add after `role_id: int = 0`:

```python
    # The HUB role (made by hand on the hub) listing this team-server hunt's
    # players: slice 3's invite DM list. 0/None on hub-hosted hunts.
    roster_role_id: int = 0
```

In `bot/store/mysqldb.py`, add to `MySQLGuildSettingsDb` directly after `commit`:

```python
    def mark_setup_complete(self, settings: GuildSettings):
        """Stamp guilds.setup_complete_time with now (UTC): !setup_server's
        run-once lock. Its own UPDATE, never part of commit(), so no ordinary
        settings write can clear it."""
        now = datetime.datetime.now(datetime.timezone.utc)
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "UPDATE `guilds` SET `setup_complete_time`=%s WHERE `id`=%s",
                (now.replace(tzinfo=None), settings.id))
        finally:
            cursor.close()
        settings.setup_complete_time = now
        self.cached_settings[settings.guild_id] = settings
```

In `bot/cogs/puzzles.py`, add to `PROTECTED_SETTING_KEYS`:

```python
    "setup_complete_time":
        "This is !setup_server's run-once lock; it is set when setup completes.",
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_setup_server_store.py tests/test_guild_auth_role_ids.py tests/test_update_settings_protected_keys.py -q > /tmp/test.log 2>&1`
Expected: PASS, the two existing files unedited.

- [ ] **Step 6: Mutation check**

Change `WHERE \`id\`=%s` to use `settings.guild_id` in `mark_setup_complete`, then confirm `test_mark_setup_complete_stamps_this_row_and_the_cache` goes red on `params[1] == 3`. Restore by editing the line back.

- [ ] **Step 7: Commit**

```bash
git add migrations/047_setup_server.sql bot/store/puzzle_settings.py bot/store/hunt_data.py bot/store/mysqldb.py bot/cogs/puzzles.py tests/test_setup_server_store.py
git commit -m "feat(store): setup_server run-once lock and hunts.roster_role_id (migration 047)"
```

---

### Task 2: `server_setup.preflight`: refuse an unfit server before touching it

**Files:**
- Create: `bot/core/server_setup.py`
- Create: `tests/test_server_setup_preflight.py`

**Interfaces:**
- Consumes: `HuntJsonDb.get_by_name_for_guild(name, guild_snowflake)`, `HuntJsonDb.count_for_guild(guild_row_id) -> int`, `discord_channels.clean_category_name(name)`.
- Produces:
  - `server_setup.TEMPLATE_MOD_ROLE = "mod"`, `LOBBY_CHANNEL = "lobby"`, `BOT_CHANNEL = "bot-commands"`
  - `server_setup.SetupRefused(Exception)`, whose message is shown after `:x: `.
  - `@dataclass SetupTargets(mod_role, lobby, bot_channel, roster_role, hunt)`, where `hunt` is a prior partial run's `HuntData`, else `None`.
  - `server_setup.preflight(guild, settings, hub, roster_role_id: int, hunt_name: str) -> SetupTargets` (sync; raises `SetupRefused`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server_setup_preflight.py`:

```python
"""preflight: everything !setup_server needs is checked before anything is made,
and each refusal names the one-step human fix."""
from unittest.mock import MagicMock, patch

import pytest

from bot.core import server_setup
from bot.core.constants import ADMIN_ROLES
from bot.core.server_setup import SetupRefused, preflight

TEAM, HUB, MOD, ROSTER, BOT_ROLE = 2000, 1000, 50, 70, 90


def _role(role_id, name, position):
    r = MagicMock()
    r.id = role_id
    r.name = name
    r.position = position
    r.managed = False
    r.is_default.return_value = False
    return r


def _channel(name):
    c = MagicMock()
    c.name = name
    return c


def _guild():
    g = MagicMock()
    g.id = TEAM
    g.me.guild_permissions.administrator = True
    g.me.top_role = _role(BOT_ROLE, "PuzzBot", 10)
    g.default_role = _role(TEAM, "@everyone", 0)
    g.default_role.permissions.view_channel = False
    g.roles = [g.default_role, _role(MOD, "mod", 5), g.me.top_role]
    g.text_channels = [_channel("lobby"), _channel("bot-commands")]
    g.categories = []
    return g


def _hub(roster=None):
    hub = MagicMock()
    hub.id = HUB
    roster = roster or _role(ROSTER, "Team Rocket", 3)
    hub.get_role.side_effect = lambda rid: roster if rid == ROSTER else None
    return hub


def _settings():
    s = MagicMock()
    s.id = 7
    return s


def _run(guild=None, hub=None, roster_id=ROSTER, existing=None, count=0, name="Big Hunt"):
    with patch("bot.core.server_setup.HuntJsonDb") as hdb:
        hdb.get_by_name_for_guild.return_value = existing
        hdb.count_for_guild.return_value = count
        return preflight(guild or _guild(), _settings(), hub or _hub(), roster_id, name)


def test_template_mod_role_clears_the_admin_gate_by_name():
    assert server_setup.TEMPLATE_MOD_ROLE in ADMIN_ROLES


def test_a_fresh_template_server_passes_and_resolves_its_targets():
    guild = _guild()
    targets = _run(guild=guild)
    assert targets.mod_role.id == MOD
    assert targets.lobby is guild.text_channels[0]
    assert targets.bot_channel is guild.text_channels[1]
    assert targets.roster_role.id == ROSTER
    assert targets.hunt is None


def test_refuses_without_administrator():
    guild = _guild()
    guild.me.guild_permissions.administrator = False
    with pytest.raises(SetupRefused, match="Administrator"):
        _run(guild=guild)


def test_refuses_a_server_where_everyone_can_see_channels():
    guild = _guild()
    guild.default_role.permissions.view_channel = True
    with pytest.raises(SetupRefused, match="template"):
        _run(guild=guild)


@pytest.mark.parametrize("roles", [[], ["mod", "mod"]])
def test_refuses_unless_exactly_one_mod_role(roles):
    guild = _guild()
    guild.roles = [guild.default_role, guild.me.top_role] + [
        _role(MOD + i, n, 5) for i, n in enumerate(roles)]
    with pytest.raises(SetupRefused, match="mod"):
        _run(guild=guild)


@pytest.mark.parametrize("mod_position", [10, 11])
def test_refuses_when_mod_is_not_below_the_bot(mod_position):
    guild = _guild()
    guild.roles[1].position = mod_position          # equal is not below
    with pytest.raises(SetupRefused, match="drag"):
        _run(guild=guild)


@pytest.mark.parametrize("missing", ["lobby", "bot-commands"])
def test_refuses_a_missing_template_channel(missing):
    guild = _guild()
    guild.text_channels = [c for c in guild.text_channels if c.name != missing]
    with pytest.raises(SetupRefused, match=missing):
        _run(guild=guild)


def test_refuses_a_role_id_not_on_the_hub():
    with pytest.raises(SetupRefused, match="role"):
        _run(roster_id=ROSTER + 1)


def test_refuses_a_managed_hub_role():
    roster = _role(ROSTER, "Some Bot", 3)
    roster.managed = True
    with pytest.raises(SetupRefused, match="role"):
        _run(hub=_hub(roster))


def test_refuses_the_hubs_everyone_role():
    roster = _role(ROSTER, "@everyone", 0)
    roster.is_default.return_value = True
    with pytest.raises(SetupRefused, match="role"):
        _run(hub=_hub(roster))


def test_refuses_a_hub_role_named_like_a_permission_tier():
    with pytest.raises(SetupRefused, match="tier"):
        _run(hub=_hub(_role(ROSTER, "mod", 3)))


def test_refuses_a_server_that_already_has_another_hunt():
    with pytest.raises(SetupRefused, match="another hunt"):
        _run(existing=None, count=1)


def test_a_rerun_picks_up_the_hunt_a_partial_run_created():
    hunt = MagicMock()
    hunt.name = "Big Hunt"
    targets = _run(existing=hunt, count=1)
    assert targets.hunt is hunt


def test_refuses_a_leftover_category_with_no_hunt_saved():
    guild = _guild()
    leftover = MagicMock()
    leftover.name = "big-hunt"                      # clean_category_name("Big Hunt")
    guild.categories = [leftover]
    with patch("bot.core.server_setup.clean_category_name", return_value="big-hunt"):
        with pytest.raises(SetupRefused, match="delete"):
            _run(guild=guild)


def test_refuses_a_team_role_left_above_the_bot():
    guild = _guild()
    guild.roles.append(_role(71, "Team Rocket", 12))
    with pytest.raises(SetupRefused, match="drag"):
        _run(guild=guild)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_server_setup_preflight.py -q > /tmp/test.log 2>&1`
Expected: FAIL. `ModuleNotFoundError: bot.core.server_setup`.

- [ ] **Step 3: Implement**

Create `bot/core/server_setup.py`:

```python
"""!setup_server: turn a server freshly cloned from the guild template into a
team server for one hunt (ctx-free; the command lives in bot/cogs/puzzles.py).

preflight() checks everything up front and raises SetupRefused before anything
is touched. run_setup() then does the work, each step check-then-create, and
stamps the run-once lock (guilds.setup_complete_time) only as its last step:
a run that stops partway is finished by re-running the same command, and a
completed one can never run again.

Spec: docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md.
"""
from dataclasses import dataclass
from typing import Optional

import discord

from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLE_NAMES
from bot.core.discord_channels import clean_category_name
from bot.store import HuntJsonDb

# What the guild template provides. TEMPLATE_MOD_ROLE must stay one of
# ADMIN_ROLES, so the gate works by name before the tier is pinned by id.
TEMPLATE_MOD_ROLE = "mod"
LOBBY_CHANNEL = "lobby"
BOT_CHANNEL = "bot-commands"


class SetupRefused(Exception):
    """The server can't be set up as it stands. Raised before anything is
    touched; the message is shown to the invoker as-is."""


@dataclass
class SetupTargets:
    mod_role: discord.Role
    lobby: discord.TextChannel
    bot_channel: discord.TextChannel
    roster_role: discord.Role          # on the HUB
    hunt: Optional[object] = None      # a prior partial run's HuntData


def preflight(guild, settings, hub, roster_role_id, hunt_name) -> SetupTargets:
    """Everything !setup_server needs, or SetupRefused naming the fix."""
    me = guild.me
    if not me.guild_permissions.administrator:
        raise SetupRefused("the bot needs Administrator here — re-invite it with permissions=8")
    if guild.default_role.permissions.view_channel:
        raise SetupRefused("@everyone can see channels here, so this server wasn't made "
                           "from the template — clone the template and start again")
    mods = [r for r in guild.roles if r.name == TEMPLATE_MOD_ROLE]
    if len(mods) != 1:
        raise SetupRefused(f"expected exactly one `{TEMPLATE_MOD_ROLE}` role, found {len(mods)}")
    mod_role = mods[0]
    if mod_role.position >= me.top_role.position:
        raise SetupRefused(f"drag the bot's role above `{TEMPLATE_MOD_ROLE}` in "
                           "Server Settings → Roles, then re-run")
    lobby = discord.utils.get(guild.text_channels, name=LOBBY_CHANNEL)
    bot_channel = discord.utils.get(guild.text_channels, name=BOT_CHANNEL)
    for name, channel in ((LOBBY_CHANNEL, lobby), (BOT_CHANNEL, bot_channel)):
        if channel is None:
            raise SetupRefused(f"the template's #{name} channel is missing")

    roster_role = hub.get_role(roster_role_id)
    if roster_role is None or roster_role.is_default() or roster_role.managed:
        raise SetupRefused(f"{roster_role_id} isn't a role you can use on {hub.name}")
    if roster_role.name in ADMIN_ROLES + ORGANISER_ROLE_NAMES:
        raise SetupRefused(f"the hub role is named `{roster_role.name}`, a permission tier "
                           "name — rename it on the hub first")
    team_role = discord.utils.get(guild.roles, name=roster_role.name)
    if team_role is not None and team_role.position >= me.top_role.position:
        raise SetupRefused(f"drag the bot's role above `{team_role.name}`, then re-run")

    hunt = HuntJsonDb.get_by_name_for_guild(hunt_name, guild.id)
    if HuntJsonDb.count_for_guild(settings.id) > (1 if hunt is not None else 0):
        raise SetupRefused("this server already has another hunt — a team server holds one")
    if hunt is None:
        leftover = discord.utils.get(guild.categories, name=clean_category_name(hunt_name))
        if leftover is not None:
            raise SetupRefused(f"a `{leftover.name}` category is left from an earlier run "
                               "that saved no hunt — delete it, then re-run")
    return SetupTargets(mod_role, lobby, bot_channel, roster_role, hunt)
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_server_setup_preflight.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Mutation check**

Change `mod_role.position >= me.top_role.position` to `>`. Confirm `test_refuses_when_mod_is_not_below_the_bot[10]` goes red. Restore.

- [ ] **Step 6: Commit**

```bash
git add bot/core/server_setup.py tests/test_server_setup_preflight.py
git commit -m "feat(setup): server_setup.preflight refuses an unfit team server up front"
```

---

### Task 3: `server_setup.run_setup`: the steps, then the lock

**Files:**
- Modify: `bot/core/server_setup.py`
- Create: `tests/test_server_setup_run.py`

**Interfaces:**
- Consumes: `SetupTargets` (Task 2); `GuildSettingsDb.commit`, `GuildSettingsDb.mark_setup_complete` (Task 1); `HuntJsonDb.commit`; `PuzzleService.create_hunt(guild, *, name, url, start_timestamp, role_name, guild_id, creator, confirm_existing_role)`.
- Produces:
  - `server_setup.WELCOME_MARK: str`
  - `@dataclass SetupReport(steps: list[str], hunt=None, hunt_created: bool = False, error: str = "")`
  - `async server_setup.run_setup(service, guild, invoker, targets, settings, hub_row, hunt_name, hunt_url) -> SetupReport`. It never raises: a failure lands in `report.error`, and the lock is then **not** written.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server_setup_run.py`:

```python
"""run_setup: settings copied from the hub, admin pinned to the template's mod
role, hunt created once, roster recorded, lobby welcomed once, and the run-once
lock written last -- never after a failure."""
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core import server_setup
from bot.core.server_setup import SetupTargets, run_setup
from bot.store.puzzle_settings import GuildSettings

MOD, ROSTER, BOT = 50, 70, 900


def _history(*messages):
    async def history(limit=None):
        for m in messages:
            yield m
    return history


def _targets(hunt=None):
    mod = MagicMock(); mod.id = MOD
    lobby = MagicMock(); lobby.send = AsyncMock(); lobby.history = _history()
    bot_channel = MagicMock(); bot_channel.name = "bot-commands"
    roster = MagicMock(); roster.id = ROSTER; roster.name = "Team Rocket"
    return SetupTargets(mod, lobby, bot_channel, roster, hunt)


def _hub_row():
    row = GuildSettings()
    row.website_url = "https://web.example"
    row.drive_parent_id = "drive-parent"
    row.drive_resources_id = "drive-resources"
    row.discord_bot_emoji = ":dog:"
    row.discord_use_voice_channels = True
    return row


def _settings():
    s = GuildSettings()
    s.id = 7
    s.guild_id = 2000
    return s


def _guild():
    g = MagicMock(); g.name = "Rocket HQ"; g.me.id = BOT
    return g


def _hunt(role_id=71, roster_role_id=0):
    h = MagicMock(); h.name = "Big Hunt"; h.role_id = role_id; h.roster_role_id = roster_role_id
    return h


async def _run(targets, service=None, settings=None):
    service = service or MagicMock()
    with patch("bot.core.server_setup.GuildSettingsDb") as gdb, \
         patch("bot.core.server_setup.HuntJsonDb") as hdb:
        report = await run_setup(service, _guild(), MagicMock(), targets, settings or _settings(),
                                 _hub_row(), "Big Hunt", "https://hunt.example")
    return report, gdb, hdb


async def test_a_fresh_run_does_every_step_then_locks():
    hunt = _hunt()
    service = MagicMock(); service.create_hunt = AsyncMock(return_value=hunt)
    targets = _targets()
    settings = _settings()
    report, gdb, hdb = await _run(targets, service, settings)

    assert report.error == ""
    assert report.hunt is hunt and report.hunt_created
    assert (settings.website_url, settings.drive_parent_id, settings.drive_resources_id,
            settings.discord_bot_emoji, settings.discord_use_voice_channels) == (
        "https://web.example", "drive-parent", "drive-resources", ":dog:", True)
    assert settings.discord_bot_channel == "bot-commands"
    assert settings.admin_role_ids == str(MOD)
    assert settings.guild_name == "Rocket HQ"
    gdb.commit.assert_called_once_with(settings)

    kwargs = service.create_hunt.await_args.kwargs
    assert kwargs["name"] == "Big Hunt" and kwargs["url"] == "https://hunt.example"
    assert kwargs["role_name"] == "Team Rocket"
    assert kwargs["guild_id"] == 7
    assert kwargs["confirm_existing_role"] is True
    assert hunt.roster_role_id == ROSTER
    hdb.commit.assert_called_once_with(hunt)

    targets.lobby.send.assert_awaited_once()
    assert server_setup.WELCOME_MARK in targets.lobby.send.await_args.args[0]
    gdb.mark_setup_complete.assert_called_once_with(settings)


async def test_a_rerun_reuses_the_hunt_and_the_welcome():
    hunt = _hunt(roster_role_id=ROSTER)
    service = MagicMock(); service.create_hunt = AsyncMock()
    targets = _targets(hunt=hunt)
    posted = MagicMock(); posted.author.id = BOT; posted.content = "hi " + server_setup.WELCOME_MARK
    targets.lobby.history = _history(posted)
    report, gdb, hdb = await _run(targets, service)

    assert report.error == ""
    assert report.hunt is hunt and not report.hunt_created
    service.create_hunt.assert_not_awaited()
    hdb.commit.assert_not_called()           # roster already recorded
    targets.lobby.send.assert_not_awaited()
    gdb.mark_setup_complete.assert_called_once()


async def test_someone_elses_message_with_the_mark_is_not_the_welcome():
    hunt = _hunt(roster_role_id=ROSTER)
    targets = _targets(hunt=hunt)
    impostor = MagicMock(); impostor.author.id = BOT + 1; impostor.content = server_setup.WELCOME_MARK
    targets.lobby.history = _history(impostor)
    await _run(targets)
    targets.lobby.send.assert_awaited_once()


async def test_a_failure_is_reported_and_never_locks():
    service = MagicMock(); service.create_hunt = AsyncMock(side_effect=RuntimeError("sheets down"))
    report, gdb, _ = await _run(_targets(), service)
    assert "sheets down" in report.error
    assert report.steps                      # the settings step it did finish is listed
    gdb.mark_setup_complete.assert_not_called()


async def test_an_ungated_hunt_stops_before_the_lock():
    service = MagicMock(); service.create_hunt = AsyncMock(return_value=_hunt(role_id=0))
    report, gdb, _ = await _run(_targets(), service)
    assert "!set_hunt_role" in report.error
    gdb.mark_setup_complete.assert_not_called()


async def test_a_failed_welcome_never_locks():
    targets = _targets(hunt=_hunt(roster_role_id=ROSTER))
    targets.lobby.send = AsyncMock(side_effect=RuntimeError("no perms"))
    report, gdb, _ = await _run(targets)
    assert "no perms" in report.error
    gdb.mark_setup_complete.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_server_setup_run.py -q > /tmp/test.log 2>&1`
Expected: FAIL. `ImportError: cannot import name 'run_setup'`.

- [ ] **Step 3: Implement**

Change the head of `bot/core/server_setup.py` to:

```python
import logging
from dataclasses import dataclass, field
from typing import Optional

import discord

from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLE_NAMES
from bot.core.discord_channels import clean_category_name
from bot.store import GuildSettingsDb, HuntJsonDb

logger = logging.getLogger(__name__)
```

Then append:

```python
# Ends the lobby welcome; a re-run that finds a bot message carrying it skips the post.
WELCOME_MARK = "— puzzbot team server"


@dataclass
class SetupReport:
    steps: list = field(default_factory=list)   # one line per step done, in order
    hunt: Optional[object] = None
    hunt_created: bool = False
    error: str = ""                              # set when the run stopped; lock not written


def _welcome_text(hunt, team):
    return (f"Welcome to **{team}**'s server for **{hunt.name}**. Hunt channels appear "
            f"once you have the **{team}** role. If this is the only channel you can see, "
            f"ask an organiser on the hub. {WELCOME_MARK}")


async def _has_welcome(lobby, bot_id):
    async for message in lobby.history(limit=50):
        if message.author.id == bot_id and WELCOME_MARK in message.content:
            return True
    return False


async def run_setup(service, guild, invoker, targets, settings, hub_row, hunt_name, hunt_url):
    """Do the setup steps, then stamp the run-once lock. Never raises: a failure
    lands in report.error, with the lock unwritten so a re-run finishes the job."""
    report = SetupReport()
    try:
        settings.guild_name = guild.name
        settings.website_url = hub_row.website_url
        settings.drive_parent_id = hub_row.drive_parent_id
        settings.drive_resources_id = hub_row.drive_resources_id
        settings.discord_bot_emoji = hub_row.discord_bot_emoji
        settings.discord_use_voice_channels = hub_row.discord_use_voice_channels
        settings.discord_bot_channel = targets.bot_channel.name
        settings.admin_role_ids = str(targets.mod_role.id)
        GuildSettingsDb.commit(settings)
        report.steps.append(f"Settings copied from the hub; admin pinned to `{targets.mod_role.name}`.")

        team = targets.roster_role.name
        hunt = targets.hunt
        if hunt is None:
            hunt = await service.create_hunt(
                guild, name=hunt_name, url=hunt_url, start_timestamp=0,
                role_name=team, guild_id=settings.id, creator=invoker,
                confirm_existing_role=True)
            report.hunt_created = True
            report.steps.append(f"Created hunt **{hunt.name}**, gated to **{team}**.")
        else:
            report.steps.append(f"Hunt **{hunt.name}** was already created; reusing it.")
        report.hunt = hunt
        if not hunt.role_id:
            raise RuntimeError(f"the hunt isn't gated — run `!set_hunt_role {hunt.name}, {team}`, "
                               "then re-run !setup_server")

        if hunt.roster_role_id != targets.roster_role.id:
            hunt.roster_role_id = targets.roster_role.id
            HuntJsonDb.commit(hunt)
        report.steps.append(f"Hub role **{team}** recorded as the hunt's roster.")

        if await _has_welcome(targets.lobby, guild.me.id):
            report.steps.append(f"#{targets.lobby.name} welcome already posted.")
        else:
            await targets.lobby.send(_welcome_text(hunt, team))
            report.steps.append(f"Posted the welcome in #{targets.lobby.name}.")

        GuildSettingsDb.mark_setup_complete(settings)
    except Exception as exc:
        logger.exception("setup_server stopped on guild %s", guild.id)
        report.error = str(exc) or type(exc).__name__
    return report
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_server_setup_run.py tests/test_server_setup_preflight.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Mutation check**

Move `GuildSettingsDb.mark_setup_complete(settings)` to directly after `GuildSettingsDb.commit(settings)`. Confirm `test_a_failure_is_reported_and_never_locks` and `test_a_failed_welcome_never_locks` go red. Restore.

- [ ] **Step 6: Commit**

```bash
git add bot/core/server_setup.py tests/test_server_setup_run.py
git commit -m "feat(setup): server_setup.run_setup builds the team server, then locks it"
```

---

### Task 4: The `!setup_server` command

**Files:**
- Modify: `bot/cogs/puzzles.py` (import `server_setup` alongside `hunt_archive` at line 37; new command directly after `archive_hunt_to_hub`)
- Create: `tests/test_setup_server_command.py`

**Interfaces:**
- Consumes: `hunt_archive.resolve_hub(bot, hub_guild_id, guild, invoker) -> (hub, hub_row)` (raises `HubUnavailable`); `server_setup.preflight`, `server_setup.run_setup`, `SetupRefused`, `SetupReport`; `GuildSettingsDb.get(snowflake)`; `DiscordNotifier(bot).welcome(guild, hunt, channel=...)`.
- Produces: `Puzzles.setup_server(ctx, hub_id: int, roster_role_id: int, hunt_url: str, *, hunt_name: str)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_setup_server_command.py`:

```python
"""!setup_server: lock, then hub, then preflight; all refuse before anything is made."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.puzzles import Puzzles
from bot.core import hunt_archive, server_setup
from tests.conftest import make_cog, make_ctx


def _settings(locked=False):
    s = MagicMock()
    s.setup_complete_time = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc) if locked else None
    return s


HUB, HUB_ROW = MagicMock(), MagicMock()
HUB.name = "The Hub"
TARGETS = MagicMock()
TARGETS.roster_role.name = "Team Rocket"


async def _call(cog, ctx, settings, resolve=None, preflight=None, run=None):
    hub, hub_row = HUB, HUB_ROW
    with patch("bot.cogs.puzzles.GuildSettingsDb") as gdb, \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=resolve or AsyncMock(return_value=(hub, hub_row))) as res, \
         patch("bot.cogs.puzzles.server_setup.preflight",
               new=preflight or MagicMock(return_value=TARGETS)) as pre, \
         patch("bot.cogs.puzzles.server_setup.run_setup",
               new=run or AsyncMock(return_value=server_setup.SetupReport(steps=["did a thing"]))) as rs, \
         patch("bot.core.notifier.DiscordNotifier") as notifier:
        gdb.get.return_value = settings
        notifier.return_value.welcome = AsyncMock()
        await Puzzles.setup_server.callback(cog, ctx, 1000, 70, "https://hunt.example", hunt_name="Big Hunt")
    return res, pre, rs, notifier, hub_row


def _sent(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


async def test_a_locked_server_refuses_before_anything_else():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    res, pre, rs, _, _ = await _call(cog, ctx, _settings(locked=True))
    assert "already set up" in _sent(ctx)
    res.assert_not_awaited(); pre.assert_not_called(); rs.assert_not_awaited()


async def test_a_server_with_no_settings_row_refuses():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    res, _, rs, _, _ = await _call(cog, ctx, None)
    assert ctx.send.await_args.args[0].startswith(":x:")
    res.assert_not_awaited(); rs.assert_not_awaited()


async def test_a_bad_hub_refuses_before_preflight():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    bad = AsyncMock(side_effect=hunt_archive.HubUnavailable("you aren't an admin on The Hub"))
    _, pre, rs, _, _ = await _call(cog, ctx, _settings(), resolve=bad)
    ctx.send.assert_awaited_once_with(":x: you aren't an admin on The Hub")
    pre.assert_not_called(); rs.assert_not_awaited()


async def test_a_preflight_refusal_makes_nothing():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    refuse = MagicMock(side_effect=server_setup.SetupRefused("the template's #lobby channel is missing"))
    _, _, rs, _, _ = await _call(cog, ctx, _settings(), preflight=refuse)
    ctx.send.assert_awaited_once_with(":x: the template's #lobby channel is missing")
    rs.assert_not_awaited()


async def test_a_full_run_passes_everything_through_and_reports_locked():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    settings = _settings()
    hunt = MagicMock(); hunt.channel_id = 61
    run = AsyncMock(return_value=server_setup.SetupReport(
        steps=["Created hunt **Big Hunt**"], hunt=hunt, hunt_created=True))
    res, pre, _, notifier, hub_row = await _call(cog, ctx, settings, run=run)
    res.assert_awaited_once_with(cog.bot, 1000, ctx.guild, ctx.author)
    pre.assert_called_once_with(ctx.guild, settings, HUB, 70, "Big Hunt")
    assert run.await_args.args == (cog.service, ctx.guild, ctx.author, TARGETS,
                                   settings, hub_row, "Big Hunt", "https://hunt.example")
    notifier.return_value.welcome.assert_awaited_once()
    sent = _sent(ctx)
    assert "Created hunt **Big Hunt**" in sent and "locked" in sent


async def test_a_stopped_run_says_to_rerun_and_does_not_claim_locked():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    run = AsyncMock(return_value=server_setup.SetupReport(steps=["Settings copied"], error="sheets down"))
    _, _, _, notifier, _ = await _call(cog, ctx, _settings(), run=run)
    sent = _sent(ctx)
    assert "sheets down" in sent and "re-run" in sent and "locked" not in sent
    notifier.return_value.welcome.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_setup_server_command.py -q > /tmp/test.log 2>&1`
Expected: FAIL. `Puzzles` has no attribute `setup_server`.

- [ ] **Step 3: Implement**

In `bot/cogs/puzzles.py` change line 37 to `from bot.core import category_layout, hunt_archive, media_gc, server_setup`. Add directly after `archive_hunt_to_hub`:

```python
    @commands.command()
    @commands.guild_only()
    async def setup_server(self, ctx, hub_id: int, roster_role_id: int, hunt_url: str, *, hunt_name: str):
        """*Set up a server freshly made from the team-server template (runs once).*

        Usage: `!setup_server <hub server id> <hub role id> <hunt url> <hunt name>`
        Example: `!setup_server 123456789012345678 234567890123456789 https://hunt.example Big Hunt`
        You must be an admin on the hub; no role is needed here, since nobody but the
        owner is in a fresh server. Checks the server matches the template, copies the
        hub's settings, pins `mod` as the admin role, creates the hunt gated to a team
        role named after the hub role, and records that hub role as the hunt's roster.
        If it stops partway, re-run the same command to finish; once complete it is
        locked on this server. Bang-only: the bot must already be here."""
        settings = GuildSettingsDb.get(ctx.guild.id)
        if settings is None:
            await ctx.send(":x: This server has no settings row yet — remove the bot and re-invite it.")
            return
        if settings.setup_complete_time is not None:
            await ctx.send(f":x: This server was already set up ({settings.setup_complete_time:%Y-%m-%d}); "
                           "!setup_server only runs once per server.")
            return
        try:
            hub, hub_row = await hunt_archive.resolve_hub(self.bot, hub_id, ctx.guild, ctx.author)
            targets = server_setup.preflight(ctx.guild, settings, hub, roster_role_id, hunt_name)
        except (hunt_archive.HubUnavailable, server_setup.SetupRefused) as e:
            await ctx.send(f":x: {e}")
            return
        await ctx.send(f"⏳ Setting up **{hunt_name}** for **{targets.roster_role.name}**…")
        report = await server_setup.run_setup(
            self.service, ctx.guild, ctx.author, targets, settings, hub_row, hunt_name, hunt_url)
        if report.hunt_created and report.hunt is not None:
            from bot.core.notifier import DiscordNotifier
            try:
                await DiscordNotifier(self.bot).welcome(
                    ctx.guild, report.hunt, channel=self.bot.get_channel(report.hunt.channel_id))
            except discord.HTTPException:
                logger.exception("setup_server: could not post the hunt welcome")
        lines = [f"• {s}" for s in report.steps]
        if report.error:
            lines.append(f":x: Setup stopped: {report.error}")
            lines.append("Fix that, then re-run the same !setup_server command; finished steps are skipped.")
        else:
            lines.append(":white_check_mark: Setup complete; !setup_server is now locked on this server.")
            lines.append("Next: give players the team role (invites by DM arrive in a later release), "
                         "and transfer ownership to a team member if wanted.")
        await ctx.send("\n".join(lines))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_setup_server_command.py tests/test_archive_hunt_to_hub_command.py -q > /tmp/test.log 2>&1`
Expected: PASS, the existing archive file unedited.

- [ ] **Step 5: Mutation check**

Swap the lock check below the `resolve_hub` call. Confirm `test_a_locked_server_refuses_before_anything_else` goes red on `res.assert_not_awaited()`. Restore.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_setup_server_command.py
git commit -m "feat(setup): !setup_server sets up a templated team server, once"
```

---

### Task 5: Docs (commands, playbook, spec), then full verification

**Files:**
- Modify: `docs/commands.md` (tier table "Admins/mods → Hunt lifecycle" row; "Hunt management" table)
- Modify: `docs/new-server-playbook.md` (new section before "## Capacity, when planning a server per hunt")
- Modify: `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md` (Status line; template table; `!setup_server` steps; Data model)

- [ ] **Step 1: `docs/commands.md`**

In the tier table, append ` · \`!setup_server\`` to the **Admins/mods → Hunt lifecycle** row. In *Hunt management*, add after the `!hunt` row:

```markdown
| `!setup_server` | | `hub-id hub-role-id hunt-url hunt name` | a fresh team server | hub admins | Set up a server just made from the team-server template: checks it matches the template, copies the hub's settings, pins `mod` as the admin role, creates the hunt gated to a team role named after the hub role, records the hub role as the hunt's roster, and posts a `#lobby` welcome. Re-run to finish a run that stopped partway; once complete it refuses for good on that server. See the [new server playbook](new-server-playbook.md#team-servers-one-per-team-per-hunt). |
```

- [ ] **Step 2: `docs/new-server-playbook.md`**

Insert before `## Capacity, when planning a server per hunt`:

```markdown
## Team servers (one per team per hunt)

For a multi-team hunt each team plays on its own server, made fresh from a guild
template. Sections 1–6 above do not apply: `!setup_server` does them.

**Once: build the template.** Make a model server with exactly:

- `@everyone`: **View Channels off** (the server is locked down by default).
- A `mod` role: Manage Channels + Manage Messages, **not** Administrator.
- `#lobby`: `@everyone` allowed to view (the only channel newcomers see).
- `#bot-commands`.
- Notifications = mentions only, join messages off, verification low.

Then Server Settings → Server Template → generate the `discord.new/…` link. No hunt
category: the bot creates and gates it. Re-sync the template when the model drifts.

**Per team, per hunt:**

1. On the hub, make the team's role by hand (e.g. `Team Rocket`) and copy its id.
2. Using the shared group account, open the template link to create the server.
3. Invite the bot with Administrator (`permissions=8`).
4. In Server Settings → Roles, drag the bot's role above `mod`.
5. In the new server: `!setup_server <hub id> <hub role id> <hunt url> <hunt name>`.
   You must be an admin on the hub. If it stops, fix what it says and re-run the same
   command; once it reports complete it is locked on that server.
6. Optionally transfer server ownership to a member of that team and have the group
   account leave.
7. Give players the team role (until invite DMs land in a later release).

At hunt end, `!archive_hunt_to_hub <hub id>` moves the history to the hub.
```

- [ ] **Step 3: Write the decisions back into the spec**

In `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`:
- Status line: `Design approved; slice 1 (cross-server archive) implemented — plan 2026-09-24; slice 2 (!setup_server) — plan 2026-10-14.`
- In the template table, delete the `hunt-team`, `Hunt` category and `#general` rows, and add under the table: `*Changed 2026-10-14:* no hunt-team role, Hunt category or #general in the template — create_hunt makes the category, its #general and the gating overwrites itself, from a team role named after the hub roster role.`
- Replace the heading `### The bot: \`!setup_server <hunt-uid> <team-name> [--organisers]\`` with `### The bot: \`!setup_server <hub id> <hub role id> <hunt url> <hunt name>\``, and add directly under it: `*Changed 2026-10-14 (plan 2026-10-14-setup-server.md):* the hunt is created here, on the team server; the hub role is made by hand and passed in; the invoker must be a hub admin; settings are copied from the hub's row; admin is pinned to mod by id; a run-once lock (guilds.setup_complete_time) is written last. Emoji upload and --organisers are dropped. Steps 6's role panel and 7's invites remain for slices 3–4.`
- In *Data model*, after the `hunts.roster_role_id` bullet add: `— made by hand on the hub and passed to !setup_server (2026-10-14).` and add a bullet: `**guilds.setup_complete_time** — !setup_server's run-once lock (migration 047).`

- [ ] **Step 4: Full verification**

Run, each as its own command:
- `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`. Expected: all pass. Read `tail -n 20 /tmp/test.log`.
- `pipenv run basedpyright bot/`. Expected: 0 errors.
- `pipenv run ruff check`. Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add docs/commands.md docs/new-server-playbook.md docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md
git commit -m "docs: !setup_server, the team-server playbook, and slice-2 decisions in the spec"
```

---

## Manual acceptance (test deployment, after merge and migration 047)

1. Build the template on a scratch server as the playbook says; create a server from it with a test account; invite the test bot.
2. Run `!setup_server` *before* dragging the bot's role: expect the "drag the bot's role above `mod`" refusal and nothing created.
3. Drag the role; run it with a hub role id that doesn't exist: expect the role refusal.
4. Run it properly: hunt category gated to the team role, `#general` welcome, `#lobby` welcome, dashboard shows the hunt to a member given the team role.
5. Run it again: expect "already set up".
6. `!archive_hunt_to_hub` from the hunt: the hub channel is named for the team.
