# Team Invites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Players reach their team server by a private, single-use invite DM'd to them, and get the team role the moment they arrive.
- `!send_invites <hunt name>` DMs everyone holding the hunt's hub role.
- Anyone given that hub role later is DM'd automatically.
- `!invite_player <user>` re-issues one invite, falling back to DMing the invoker to pass on.
- On arrival, the bot grants the team role to anyone holding the hub role, regardless of how they got in.
- The template greeting and `!setup_server`'s completion message walk the person setting up through the whole sequence.
- `!archive_hunt_to_hub` no longer needs the hub's id on a team server (Task 6).

**Architecture:**
- **Logic:** a new ctx-free module, `bot/core/team_invites.py`, in the style of `bot/core/role_panel.py`. It holds:
  - invite creation and delivery;
  - the `!send_invites` run and its report text;
  - the `!invite_player` path;
  - the two event handlers: arrival on a team server, and roles added on the hub.

  It reuses `role_panel.find_roster_role` and `hunt_archive.hub_member`, so the arrival grant and the Join button decide eligibility the same way.
- **Events:** a new listener-only cog, `bot/cogs/team_servers.py`, routes `on_member_join` and `on_member_update` into that module and never lets an error escape. Cogs are auto-discovered from `bot/cogs/*.py` files that define `setup()`.
- **Commands:** the two bang commands live beside `!setup_server` / `!post_role_panel` in `bot/cogs/puzzles.py`.
- **Storage:** migration 048 adds a `hunt_invites` table (who was DM'd, and when they arrived) and `hunts.invites_sent_time` (set by the first completed `!send_invites`, which is what switches on late-joiner DMs).

**Tech Stack:** Python 3.12, discord.py 2.7.1 (`TextChannel.create_invite`, `Invite.delete`, `User.send`, `Intents.all()` with member chunking), MariaDB via `bot/store/mysqldb.py`, pytest (`asyncio_mode = auto`), `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`, sections *Invite delivery*, *Data model* (`hunt_invites`), *Failure cases*, and acceptance criteria 2, 3 and 6. This is **slice 4**. Slices 1–3 are shipped to staging: the archive, `!setup_server`, and the `#lobby` panel (plan `docs/superpowers/plans/2026-10-30-lobby-role-panel.md`). It departs from the spec where listed under *Decisions*, all decided with the user on 2026-10-30. Task 5 writes them back into the spec.

## Decisions taken during planning (override the spec)

- **The team role is granted on arrival by the hub role, not by the invite used (user decision).** When anyone joins a team server, the bot checks whether they hold the hunt's hub role, the same check as Join. If they do, it grants the team role. The spec matched the invite code instead. Discord doesn't report which invite a member used, and a forwarded invite would grant the role to whoever used it. With this design a forwarded invite gets its holder only `#lobby`. Hub admins are **not** given `mod` on arrival; they still opt in with Join.
- **`hunt_invites` is a record, not the gate.** It has one row per DM'd invite, and re-issues add rows. Its jobs are:
  - "already invited, don't DM again" (acceptance criterion 6);
  - `joined_at`, so a mod can see who hasn't arrived.
- **The first DMs go out from a separate `!send_invites <hunt name>` (user decision),** run on the team server by a `mod`, not from inside `!setup_server`. That way the person setting up can check the server first, and it works on servers set up before this slice. It skips anyone already on the server or already DM'd, so re-running is safe.
- **Late joiners are DM'd automatically, but only once `!send_invites` has run for that hunt (user decision).** The first completed run stamps `hunts.invites_sent_time`. A hub role granted before that triggers nothing; those people are covered when `!send_invites` runs.
- **`!invite_player <user>`, not `!invite`.** `!invite` already exists: `bot/cogs/utility.py` shows the bot's own invite link. The spec's `!invite <user>` is renamed rather than hijacking that command. The user can be given by id or name, because they are not on the team server yet. The bot tries to DM them first. If their DMs are closed, it DMs the link to whoever ran the command, to pass on privately (user decision). No invite link is ever posted in a channel (acceptance criterion 3).
- **A failed DM leaves nothing behind.** The undelivered invite is deleted and no row is recorded, so the next `!send_invites` retries that person. Reports name people, never links.
- **Invites are single-use, never expire, and point at `#lobby`** (`max_uses=1, max_age=0, unique=True`). The hub-role check guards the team role, so an old link reaching the wrong person gains them nothing beyond `#lobby`.
- **A late-joiner DM that fails** is reported by name in that team server's `#bot-commands`, with the `!invite_player <id>` to run, and never with the link.
- **`hunts.invites_sent_time` is not a `HuntData` field.** The generic `HuntJsonDb.commit()` writes every `HuntData` field, so a stale hunt object would clear it. It is written only by `HuntJsonDb.mark_invites_sent` (its own UPDATE, first run wins) and read only by SQL. `HuntData.import_dict` already ignores columns it has no field for.
- **The template greeting and `!setup_server`'s completion message list the whole sequence (user request):**
  1. drag the bot's role;
  2. `!setup_server`;
  3. click **Join** to get `mod`;
  4. check the server, then `!send_invites <hunt name>`;
  5. optionally transfer ownership.
- **Not in this slice:**
  - removing the team role when someone loses the hub role (not in the spec; a later decision);
  - the access log (slice 5);
  - a dashboard equivalent;
  - a race guard between `!send_invites` and a simultaneous late-joiner DM to the same person (worst case, one person gets two invites).

## Roadmap

1. Cross-server archive. **Shipped.**
2. Template + `!setup_server`. **Shipped.**
3. `#lobby` Join/Leave panel. **Shipped** to staging, and tested in Discord 2026-10-30.
4. **This plan:** private invites, arrival grant, late-joiner DMs, `!invite_player`, the full setup greeting.
5. Access audit: `hunt_access_log` + hub mirror.

## Global Constraints

- Default branch is **`staging`**, not `main`. Work in a worktree branched from **local** `staging` (origin is intentionally behind). Before running tests, copy the gitignored `config.json` + `google_secrets.json` into the new worktree, or conftest dies on MySQL access-denied (pytest exit 4).
- Test command (always wrapped, always to a file, single statement):
  `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`
- **Migration → deploy → code.** Apply migration 048 to `puzzlebot_test` (`pipenv run python migrate.py migrate`) in Task 1, before running anything that touches the new table or column. Every other environment needs 048 applied before this code is deployed there.
- `migrations/048_*` is the next free number on `staging` and in every current worktree (checked 2026-10-30). Re-check before merging (parallel worktrees can collide).
- Patch where names are **looked up**:
  - `bot.core.team_invites.HuntInviteDb`, `bot.core.team_invites.HuntJsonDb`;
  - `bot.core.team_invites.role_panel.find_roster_role`, `bot.core.team_invites.hunt_archive.hub_member`;
  - `bot.cogs.team_servers.team_invites.on_join`, `bot.cogs.team_servers.team_invites.on_hub_roles_added`;
  - `bot.cogs.puzzles.HuntJsonDb`, `bot.cogs.puzzles.team_invites.send_invites`, `bot.cogs.puzzles.team_invites.invite_one`.
- **No invite URL in any channel message.** URLs appear only in DMs. Tests assert `discord.gg` is absent from channel replies.
- `bot/cogs/puzzles.py` is ~4400 lines: grep for anchors, never read it whole. Match anchors by **quoted text**, not line numbers; a missing anchor is a blocker to report, not a guess.

---

### Task 1: Migration 048 and the storage it needs

**Files:**
- Create: `migrations/048_team_invites.sql`
- Modify: `bot/store/mysqldb.py`:
  - add three methods to `MySQLHuntJsonDb` (after `count_for_guild`);
  - add a new class `MySQLHuntInviteDb` directly after `MySQLHuntJsonDb`.
- Modify: `bot/store/__init__.py`:
  - add `MySQLHuntInviteDb` to the `from .mysqldb import …` line;
  - add `HuntInviteDb = MySQLHuntInviteDb(mydb=mydb)` directly after `HuntJsonDb = MySQLHuntJsonDb(mydb=mydb)`.
- Test: `tests/test_team_invites_store.py` (mocked), `tests/test_team_invites_live_db.py` (live `puzzlebot_test`)

**Interfaces:**
- Produces:
  - `HuntJsonDb.live_team_hunt_for_guild(guild_snowflake) -> Optional[HuntData]`;
  - `HuntJsonDb.live_invited_hunts_for_roster(roster_role_id) -> list[tuple[HuntData, int]]`, where the int is the team guild's Discord snowflake;
  - `HuntJsonDb.mark_invites_sent(hunt_id) -> None`;
  - `HuntInviteDb.record(hunt_id, user_id, code) -> None`;
  - `HuntInviteDb.invited_user_ids(hunt_id) -> set[int]`;
  - `HuntInviteDb.mark_joined(hunt_id, user_id) -> None`.

- [ ] **Step 1: Write the migration**

Create `migrations/048_team_invites.sql`:

```sql
-- 048_team_invites.sql
-- Slice 4 of docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md:
-- private team-server invites (plan docs/superpowers/plans/2026-10-30-team-invites.md).
--
-- hunt_invites -- one row per single-use invite DM'd to a holder of a hunt's hub
-- role (re-issues add rows). A record, not a gate: the team role is granted on
-- arrival by the hub role alone. It answers "already invited?" so nobody is DM'd
-- twice, and joined_at shows who hasn't arrived. Rows go with their hunt.
--
-- hunts.invites_sent_time -- stamped by the first completed !send_invites for a
-- hunt; switches on automatic DMs to people given the hub role later. Written
-- only by HuntJsonDb.mark_invites_sent and read only by SQL: HuntData has no such
-- field, so the generic commit() can never clear it.
--
-- PRE-FLIGHT: none needed. The table is new, and the column is additive with a
-- NULL default that is right for every existing row by construction --
-- !send_invites does not exist before this migration, so no hunt has sent
-- invites.
--
-- Re-runnable only in the sense that a second apply fails loudly on the
-- duplicate column.

CREATE TABLE IF NOT EXISTS `hunt_invites` (
  `id`         INT(11)             NOT NULL AUTO_INCREMENT,
  `hunt_id`    INT(11)             NOT NULL,
  `user_id`    BIGINT(20) UNSIGNED NOT NULL,
  `code`       VARCHAR(32)         NOT NULL,
  `created_at` DATETIME            NOT NULL,
  `joined_at`  DATETIME            DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_hunt_invites_hunt_user` (`hunt_id`, `user_id`),
  CONSTRAINT `HuntInviteHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

ALTER TABLE `hunts`
  ADD COLUMN `invites_sent_time` DATETIME NULL DEFAULT NULL;
```

Apply it to the test database:

Run: `pipenv run python migrate.py migrate`
Expected: `048_team_invites.sql` is reported as applied.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_team_invites_store.py`:

```python
"""Slice 4 storage, mocked: hunts.invites_sent_time is never written by the
generic HuntData commit (a stale hunt object must not clear it)."""
from unittest.mock import MagicMock

from bot.store.hunt_data import HuntData
from bot.store.mysqldb import MySQLHuntJsonDb


def _stub_db():
    cursor = MagicMock()
    cursor.lastrowid = 5
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    mydb = MagicMock()
    mydb.cursor.return_value = cursor
    mydb.dict_cursor.return_value = cursor
    return mydb, cursor


def test_hunt_commit_never_writes_invites_sent_time():
    mydb, cursor = _stub_db()
    hunt = HuntData()
    hunt.id = 3
    hunt.name = "Big Hunt"
    MySQLHuntJsonDb(mydb=mydb).commit(hunt)
    assert cursor.execute.call_args_list, "commit issued no SQL"
    assert all("invites_sent_time" not in c.args[0] for c in cursor.execute.call_args_list)


def test_import_dict_ignores_the_invites_sent_time_column():
    hunt = HuntData.import_dict({"id": 3, "name": "Big Hunt", "invites_sent_time": None})
    assert not hasattr(hunt, "invites_sent_time")
```

Create `tests/test_team_invites_live_db.py`:

```python
"""Slice 4 storage against the real schema (LIVE puzzlebot_test; needs migration
048). The joins, filters and the first-run-wins stamp are SQL, so they are
checked against MariaDB rather than a mock. Follows the live-DB convention of
tests/test_hunt_category_cascade.py: raw inserts, torn down in finally."""
from bot.store import HuntInviteDb, HuntJsonDb, mydb

GUILD_SNOWFLAKE = 990000000000000048
ROSTER = 880000000000000048


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def _one(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row


def test_invite_storage_against_the_real_schema():
    guild_row = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)",
                      (GUILD_SNOWFLAKE, "slice-4 live test"))
    live = archived = None
    try:
        live = _exec("INSERT INTO hunts (name, guild_id, roster_role_id, archived) "
                     "VALUES (%s, %s, %s, 0)", ("s4 live", guild_row, ROSTER))
        archived = _exec("INSERT INTO hunts (name, guild_id, roster_role_id, archived) "
                         "VALUES (%s, %s, %s, 1)", ("s4 archived", guild_row, ROSTER))

        assert HuntJsonDb.live_team_hunt_for_guild(GUILD_SNOWFLAKE).id == live
        assert HuntJsonDb.live_invited_hunts_for_roster(ROSTER) == []   # nothing sent yet

        HuntJsonDb.mark_invites_sent(live)
        first = _one("SELECT invites_sent_time FROM hunts WHERE id = %s", (live,))[0]
        assert first is not None
        HuntJsonDb.mark_invites_sent(live)                                # first run wins
        assert _one("SELECT invites_sent_time FROM hunts WHERE id = %s", (live,))[0] == first
        HuntJsonDb.mark_invites_sent(archived)

        got = HuntJsonDb.live_invited_hunts_for_roster(ROSTER)
        assert [(h.id, snowflake) for h, snowflake in got] == [(live, GUILD_SNOWFLAKE)]
        assert HuntJsonDb.live_invited_hunts_for_roster(ROSTER + 1) == []

        HuntInviteDb.record(live, 111, "abc")
        HuntInviteDb.record(live, 222, "def")
        assert HuntInviteDb.invited_user_ids(live) == {111, 222}
        HuntInviteDb.mark_joined(live, 111)
        joined = _one("SELECT COUNT(*) FROM hunt_invites WHERE hunt_id = %s AND joined_at IS NOT NULL",
                      (live,))[0]
        assert joined == 1
    finally:
        for hunt_id in (live, archived):
            if hunt_id is not None:
                _exec("DELETE FROM hunts WHERE id = %s", (hunt_id,))     # cascades hunt_invites
        _exec("DELETE FROM guilds WHERE id = %s", (guild_row,))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_team_invites_store.py tests/test_team_invites_live_db.py -q > /tmp/test.log 2>&1`
Expected:
- the live test errors with `ImportError: cannot import name 'HuntInviteDb'`;
- `test_hunt_commit_never_writes_invites_sent_time` and `test_import_dict_ignores_the_invites_sent_time_column` already pass.

Those two pass by design. They pin that nobody adds the field to `HuntData` later. To prove they bite, temporarily add `invites_sent_time: Optional[datetime.datetime] = None` to `HuntData` in `bot/store/hunt_data.py`, confirm both fail, then remove the line by editing it back.

- [ ] **Step 4: Implement the storage**

In `bot/store/mysqldb.py`, add to `MySQLHuntJsonDb`, directly after `count_for_guild`:

```python
    def live_team_hunt_for_guild(self, guild_snowflake):
        """The live hunt on the guild whose Discord id is *guild_snowflake* that has
        a hub roster role -- a team server holds exactly one -- or None. Hub-hosted
        hunts have no roster role, so the hub itself never matches."""
        row = self._fetch_one(
            "SELECT hunts.* FROM `hunts` JOIN `guilds` ON hunts.guild_id = guilds.id "
            "WHERE guilds.guild_id = %s AND hunts.archived = 0 "
            "AND hunts.roster_role_id IS NOT NULL AND hunts.roster_role_id <> 0 "
            "ORDER BY hunts.id ASC LIMIT 1",
            (guild_snowflake,),
        )
        return HuntData.import_dict(row) if row else None

    def live_invited_hunts_for_roster(self, roster_role_id):
        """[(hunt, team guild snowflake)] for live hunts whose hub roster role is
        *roster_role_id* and whose invites have gone out (invites_sent_time set):
        the hunts a newly-given hub role should trigger an invite DM for."""
        rows = self._fetch_all(
            "SELECT hunts.*, guilds.guild_id AS guild_snowflake FROM `hunts` "
            "JOIN `guilds` ON hunts.guild_id = guilds.id "
            "WHERE hunts.roster_role_id = %s AND hunts.archived = 0 "
            "AND hunts.invites_sent_time IS NOT NULL ORDER BY hunts.id ASC",
            (roster_role_id,),
        )
        return [(HuntData.import_dict(r), int(r["guild_snowflake"])) for r in rows]

    def mark_invites_sent(self, hunt_id):
        """Stamp hunts.invites_sent_time with now (UTC) unless already set: the first
        completed !send_invites wins. Its own UPDATE -- HuntData has no such field,
        so the generic commit() can never clear it."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self._exec(
            "UPDATE `hunts` SET `invites_sent_time` = COALESCE(`invites_sent_time`, %s) "
            "WHERE `id` = %s",
            (now, hunt_id),
        )
```

and add, directly after the end of the `MySQLHuntJsonDb` class:

```python
class MySQLHuntInviteDb(_MySQLBaseDb):
    """hunt_invites: one row per single-use team-server invite DM'd to a holder of
    a hunt's hub role (slice 4). A record, not a gate."""
    TABLE_NAME = 'hunt_invites'

    def record(self, hunt_id, user_id, code):
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self._exec(
            "INSERT INTO `hunt_invites` (hunt_id, user_id, code, created_at) "
            "VALUES (%s, %s, %s, %s)",
            (hunt_id, user_id, code, now),
        )

    def invited_user_ids(self, hunt_id):
        """Everyone ever DM'd an invite for *hunt_id*."""
        rows = self._fetch_all(
            "SELECT DISTINCT user_id FROM `hunt_invites` WHERE hunt_id = %s", (hunt_id,))
        return {int(r["user_id"]) for r in rows}

    def mark_joined(self, hunt_id, user_id):
        """Note that *user_id* arrived on *hunt_id*'s team server (a no-op for
        someone who was never DM'd)."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self._exec(
            "UPDATE `hunt_invites` SET joined_at = %s "
            "WHERE hunt_id = %s AND user_id = %s AND joined_at IS NULL",
            (now, hunt_id, user_id),
        )
```

In `bot/store/__init__.py`, add `MySQLHuntInviteDb` to the end of the `from .mysqldb import …` line, and directly after `    HuntJsonDb = MySQLHuntJsonDb(mydb=mydb)` add:

```python
    HuntInviteDb = MySQLHuntInviteDb(mydb=mydb)
```

If the live test's `INSERT INTO guilds` fails on a NOT NULL column, add that column with a neutral value to the insert. Never change the assertions.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_team_invites_store.py tests/test_team_invites_live_db.py tests/test_setup_server_store.py tests/test_store_lazy_connect.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add migrations/048_team_invites.sql bot/store/mysqldb.py bot/store/__init__.py tests/test_team_invites_store.py tests/test_team_invites_live_db.py
git commit -m "feat(store): hunt_invites and hunts.invites_sent_time (migration 048)"
```

---

### Task 2: `team_invites`: delivery, `!send_invites`, `!invite_player`

**Files:**
- Create: `bot/core/team_invites.py`
- Test: `tests/test_team_invites.py`

**Interfaces:**
- Consumes:
  - Task 1's `HuntInviteDb.record` / `invited_user_ids` and `HuntJsonDb.mark_invites_sent`;
  - `role_panel.find_roster_role(bot, roster_role_id)`;
  - `hunt_archive.hub_member(hub, user_id)` (async);
  - `server_setup.LOBBY_CHANNEL`.
- Produces:
  - `class InviteUnavailable(Exception)`;
  - `@dataclass InviteReport(sent: list, failed: list, already_here: int, already_invited: int, error: str)`;
  - `dm_text(hunt, team, url) -> str`;
  - `async make_invite(guild, hunt) -> discord.Invite`;
  - `async deliver(guild, hunt, team, user) -> bool`;
  - `async send_invites(bot, guild, hunt) -> InviteReport`;
  - `report_text(report) -> str`;
  - `async invite_one(bot, guild, hunt, user, invoker) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_team_invites.py`:

```python
"""team_invites: !send_invites and !invite_player DM private single-use invites to
holders of the hunt's hub role, record who was DM'd, leave nothing behind when a
DM fails, and never put a link anywhere but a DM."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord

from bot.core import team_invites
from bot.core.team_invites import InviteReport

HUNT_ID, ROSTER, OTHER_HUB_ROLE = 42, 70, 99


def _forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Cannot send messages to this user")


def _person(uid, name, dms_open=True, bot=False):
    p = MagicMock()
    p.id = uid
    p.display_name = name
    p.bot = bot
    p.send = AsyncMock(side_effect=None if dms_open else _forbidden())
    return p


def _hunt(roster_role_id=ROSTER):
    h = MagicMock()
    h.id = HUNT_ID
    h.name = "Big Hunt"
    h.roster_role_id = roster_role_id
    return h


class World:
    """A team server with #lobby, a hub, and the hunt's hub role."""

    def __init__(self, members=(), here=(), invited=(), lobby=True, invite_fails=False,
                 hub_roles_of=None, roster_found=True):
        self.hub = MagicMock()
        self.hub.name = "The Hub"
        self.roster = MagicMock()
        self.roster.id = ROSTER
        self.roster.name = "Team Rocket"
        self.roster.guild = self.hub
        self.roster.members = list(members)
        self.roster_found = roster_found
        self.invites = []

        async def create_invite(**kwargs):
            if invite_fails:
                raise _forbidden()
            inv = MagicMock()
            n = len(self.invites) + 1
            inv.code = f"code{n}"
            inv.url = f"https://discord.gg/code{n}"
            inv.delete = AsyncMock()
            inv.kwargs = kwargs
            self.invites.append(inv)
            return inv

        self.lobby = MagicMock()
        self.lobby.name = "lobby"
        self.lobby.create_invite = create_invite
        cmds = MagicMock()
        cmds.name = "bot-commands"
        self.guild = MagicMock()
        self.guild.text_channels = [cmds, self.lobby] if lobby else [cmds]
        here_ids = {p.id for p in here}
        self.guild.get_member.side_effect = lambda uid: MagicMock() if uid in here_ids else None
        self.invited = set(invited)
        self.hub_roles_of = hub_roles_of or {}
        self.bot = MagicMock()

    def patched(self, stack):
        idb = stack.enter_context(patch("bot.core.team_invites.HuntInviteDb"))
        idb.invited_user_ids.return_value = self.invited
        hdb = stack.enter_context(patch("bot.core.team_invites.HuntJsonDb"))
        stack.enter_context(patch("bot.core.team_invites.role_panel.find_roster_role",
                                  return_value=self.roster if self.roster_found else None))

        async def hub_member(hub, uid):
            if uid not in self.hub_roles_of:
                return None
            m = MagicMock()
            m.roles = [MagicMock(id=r) for r in self.hub_roles_of[uid]]
            return m

        self.hub_member = stack.enter_context(patch(
            "bot.core.team_invites.hunt_archive.hub_member", new=AsyncMock(side_effect=hub_member)))
        return idb, hdb

    async def send(self, hunt=None):
        with ExitStack() as stack:
            idb, hdb = self.patched(stack)
            report = await team_invites.send_invites(self.bot, self.guild, hunt or _hunt())
        return report, idb, hdb

    async def invite_one(self, user, invoker):
        with ExitStack() as stack:
            idb, _ = self.patched(stack)
            reply = await team_invites.invite_one(self.bot, self.guild, _hunt(), user, invoker)
        return reply, idb


# --- send_invites ----------------------------------------------------------------

async def test_invites_everyone_not_here_and_not_yet_invited():
    ann, bob, cat, dee = (_person(1, "Ann"), _person(2, "Bob"), _person(3, "Cat"), _person(4, "Dee"))
    w = World(members=[ann, bob, cat, dee], here=[bob], invited={3})
    report, idb, hdb = await w.send()
    assert report.sent == ["Ann", "Dee"]
    assert (report.already_here, report.already_invited, report.failed, report.error) == (1, 1, [], "")
    assert "https://discord.gg/code1" in ann.send.await_args.args[0]
    assert "https://discord.gg/code2" in dee.send.await_args.args[0]
    bob.send.assert_not_awaited()
    cat.send.assert_not_awaited()
    assert idb.record.call_args_list == [call(HUNT_ID, 1, "code1"), call(HUNT_ID, 4, "code2")]
    hdb.mark_invites_sent.assert_called_once_with(HUNT_ID)


async def test_each_invite_is_single_use_and_never_expires():
    w = World(members=[_person(1, "Ann")])
    await w.send()
    kwargs = w.invites[0].kwargs
    assert (kwargs["max_uses"], kwargs["max_age"], kwargs["unique"]) == (1, 0, True)


async def test_the_dm_names_the_team_and_the_hunt():
    ann = _person(1, "Ann")
    await World(members=[ann]).send()
    text = ann.send.await_args.args[0]
    assert "Team Rocket" in text and "Big Hunt" in text


async def test_closed_dms_delete_the_invite_and_record_nothing():
    ann = _person(1, "Ann", dms_open=False)
    w = World(members=[ann])
    report, idb, hdb = await w.send()
    assert report.failed == ["Ann"] and report.sent == []
    w.invites[0].delete.assert_awaited_once()
    idb.record.assert_not_called()
    hdb.mark_invites_sent.assert_called_once_with(HUNT_ID)


async def test_bots_with_the_hub_role_are_skipped():
    robot = _person(1, "Robot", bot=True)
    w = World(members=[robot])
    report, idb, _ = await w.send()
    robot.send.assert_not_awaited()
    assert report.sent == [] and w.invites == []


async def test_no_lobby_stops_before_anything_and_does_not_switch_on_late_dms():
    ann = _person(1, "Ann")
    report, idb, hdb = await World(members=[ann], lobby=False).send()
    assert "#lobby" in report.error
    ann.send.assert_not_awaited()
    hdb.mark_invites_sent.assert_not_called()


async def test_an_invite_that_cannot_be_created_stops_the_run():
    ann = _person(1, "Ann")
    report, idb, hdb = await World(members=[ann], invite_fails=True).send()
    assert "couldn't create" in report.error
    ann.send.assert_not_awaited()
    hdb.mark_invites_sent.assert_not_called()


async def test_an_unfindable_hub_role_stops_the_run():
    ann = _person(1, "Ann")
    report, idb, hdb = await World(members=[ann], roster_found=False).send()
    assert report.error
    ann.send.assert_not_awaited()
    hdb.mark_invites_sent.assert_not_called()


# --- report_text -------------------------------------------------------------------

def test_the_report_names_people_and_never_carries_a_link():
    text = team_invites.report_text(InviteReport(
        sent=["Ann", "Dee"], failed=["Eve"], already_here=1, already_invited=2))
    assert "Ann" in text and "Dee" in text and "Eve" in text
    assert "!invite_player" in text
    assert "1 already here" in text and "2 already invited" in text
    assert "discord.gg" not in text
    assert "automatically" in text


def test_a_stopped_run_says_so_and_promises_nothing():
    text = team_invites.report_text(InviteReport(sent=["Ann"], error="this server has no #lobby channel"))
    assert "Stopped" in text and "#lobby" in text
    assert "automatically" not in text


def test_a_run_with_nobody_to_invite_says_so():
    assert "Nobody" in team_invites.report_text(InviteReport())


# --- invite_one (!invite_player) ----------------------------------------------------

async def test_dms_a_hub_role_holder_a_fresh_invite():
    user, me = _person(5, "Fay"), _person(9, "Mod")
    w = World(hub_roles_of={5: {ROSTER}})
    reply, idb = await w.invite_one(user, me)
    assert "https://discord.gg/code1" in user.send.await_args.args[0]
    me.send.assert_not_awaited()
    idb.record.assert_called_once_with(HUNT_ID, 5, "code1")
    assert "Fay" in reply and "discord.gg" not in reply
    w.hub_member.assert_awaited_once_with(w.hub, 5)


async def test_closed_dms_send_the_link_to_the_invoker_instead():
    user, me = _person(5, "Fay", dms_open=False), _person(9, "Mod")
    w = World(hub_roles_of={5: {ROSTER}})
    reply, idb = await w.invite_one(user, me)
    assert "https://discord.gg/code1" in me.send.await_args.args[0]
    assert "Fay" in me.send.await_args.args[0]
    idb.record.assert_called_once_with(HUNT_ID, 5, "code1")
    assert "to you" in reply and "discord.gg" not in reply


async def test_when_nobody_can_be_dmd_the_invite_is_deleted():
    user, me = _person(5, "Fay", dms_open=False), _person(9, "Mod", dms_open=False)
    w = World(hub_roles_of={5: {ROSTER}})
    reply, idb = await w.invite_one(user, me)
    w.invites[0].delete.assert_awaited_once()
    idb.record.assert_not_called()
    assert reply.startswith(":x:")


async def test_refuses_someone_without_the_hub_role():
    user, me = _person(5, "Fay"), _person(9, "Mod")
    w = World(hub_roles_of={5: {OTHER_HUB_ROLE}})
    reply, idb = await w.invite_one(user, me)
    assert w.invites == []
    user.send.assert_not_awaited()
    assert "Team Rocket" in reply


async def test_refuses_someone_not_on_the_hub():
    user, me = _person(5, "Fay"), _person(9, "Mod")
    w = World()
    reply, _ = await w.invite_one(user, me)
    assert w.invites == [] and reply.startswith(":x:")


async def test_someone_already_here_is_not_invited():
    user, me = _person(5, "Fay"), _person(9, "Mod")
    w = World(here=[user], hub_roles_of={5: {ROSTER}})
    reply, _ = await w.invite_one(user, me)
    assert w.invites == []
    assert "already" in reply
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_team_invites.py -q > /tmp/test.log 2>&1`
Expected: collection error, `ImportError: cannot import name 'team_invites' from 'bot.core'`.

- [ ] **Step 3: Write the module**

Create `bot/core/team_invites.py`. Task 3 appends the event handlers to it.

```python
"""Slice 4: private, single-use invites to a team server, and the team role on
arrival (spec docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md;
plan docs/superpowers/plans/2026-10-30-team-invites.md).

Who may have the team role is decided by the hub role alone, exactly as the
#lobby Join button decides it (role_panel): an invite is only a way in, and a
forwarded one gets its holder nothing beyond #lobby. hunt_invites records who was
DM'd, so nobody is DM'd twice and a mod can see who hasn't arrived. Invite links
appear only in DMs, never in a channel.
"""
import logging
from dataclasses import dataclass, field

import discord

from bot.core import hunt_archive, role_panel, server_setup
from bot.store import HuntInviteDb, HuntJsonDb

logger = logging.getLogger(__name__)


class InviteUnavailable(Exception):
    """No invite can be made on the team server; the message names the fix."""


@dataclass
class InviteReport:
    sent: list = field(default_factory=list)     # display names DM'd an invite
    failed: list = field(default_factory=list)   # display names whose DMs are closed
    already_here: int = 0
    already_invited: int = 0
    error: str = ""                              # set when the run stopped early


def dm_text(hunt, team, url):
    return (f"You're on **{team}** for **{hunt.name}**. Here's your private invite to the "
            f"team server — it works once, so don't pass it on. The hunt channels appear "
            f"as soon as you arrive: {url}")


async def make_invite(guild, hunt):
    """A fresh single-use, never-expiring invite to the team server's #lobby."""
    lobby = discord.utils.get(guild.text_channels, name=server_setup.LOBBY_CHANNEL)
    if lobby is None:
        raise InviteUnavailable(f"this server has no #{server_setup.LOBBY_CHANNEL} channel")
    try:
        return await lobby.create_invite(max_uses=1, max_age=0, unique=True,
                                         reason=f"team invite: {hunt.name}")
    except discord.HTTPException as exc:
        raise InviteUnavailable(f"I couldn't create an invite in #{lobby.name} ({exc})") from exc


async def _discard(invite):
    try:
        await invite.delete(reason="team invite: the DM failed")
    except discord.HTTPException:
        logger.warning("could not delete undelivered invite %s", invite.code)


async def deliver(guild, hunt, team, user):
    """DM *user* a fresh invite and record it. False if their DMs are closed: the
    invite is deleted and nothing recorded, so a later run retries them. Raises
    InviteUnavailable when no invite can be made at all."""
    invite = await make_invite(guild, hunt)
    try:
        await user.send(dm_text(hunt, team, invite.url))
    except discord.HTTPException:
        await _discard(invite)
        return False
    HuntInviteDb.record(hunt.id, user.id, invite.code)
    return True


async def send_invites(bot, guild, hunt):
    """!send_invites: DM an invite to every holder of the hunt's hub role who is
    neither on this server nor already DM'd. A completed run (DM failures and
    all) stamps hunts.invites_sent_time, which switches on late-joiner DMs."""
    report = InviteReport()
    roster = role_panel.find_roster_role(bot, hunt.roster_role_id) if hunt.roster_role_id else None
    if roster is None:
        report.error = "I can't find this hunt's hub role"
        return report
    invited = HuntInviteDb.invited_user_ids(hunt.id)
    for person in roster.members:
        if person.bot:
            continue
        if guild.get_member(person.id) is not None:
            report.already_here += 1
        elif person.id in invited:
            report.already_invited += 1
        else:
            try:
                delivered = await deliver(guild, hunt, roster.name, person)
            except InviteUnavailable as exc:
                report.error = str(exc)
                return report
            (report.sent if delivered else report.failed).append(person.display_name)
    HuntJsonDb.mark_invites_sent(hunt.id)
    return report


def report_text(report):
    """The !send_invites reply: names, never links."""
    lines = []
    if report.sent:
        lines.append(f":incoming_envelope: Invited {len(report.sent)}: {', '.join(report.sent)}.")
    if report.failed:
        lines.append(f":warning: Couldn't DM {len(report.failed)} (DMs closed): "
                     f"{', '.join(report.failed)} — run `!invite_player <user>` for each.")
    skipped = []
    if report.already_here:
        skipped.append(f"{report.already_here} already here")
    if report.already_invited:
        skipped.append(f"{report.already_invited} already invited")
    if skipped:
        lines.append("Skipped: " + ", ".join(skipped) + ".")
    if report.error:
        lines.append(f":x: Stopped: {report.error}. Fix that and re-run; anyone already "
                     "invited is skipped.")
        return "\n".join(lines)
    if not lines:
        lines.append("Nobody has the hub role yet, so there was nobody to invite.")
    lines.append("Anyone given the hub role from now on is DM'd an invite automatically.")
    return "\n".join(lines)


async def invite_one(bot, guild, hunt, user, invoker):
    """!invite_player: a fresh invite for one holder of the hub role, DM'd to them,
    or to *invoker* to pass on when their DMs are closed. Returns the reply."""
    roster = role_panel.find_roster_role(bot, hunt.roster_role_id) if hunt.roster_role_id else None
    if roster is None:
        return ":x: I can't find this hunt's hub role."
    on_hub = await hunt_archive.hub_member(roster.guild, user.id)
    if on_hub is None or roster.id not in {r.id for r in on_hub.roles}:
        return (f":x: {user.display_name} doesn't have **{roster.name}** on "
                f"**{roster.guild.name}**, so I won't invite them.")
    if guild.get_member(user.id) is not None:
        return f"{user.display_name} is already on this server."
    try:
        invite = await make_invite(guild, hunt)
    except InviteUnavailable as exc:
        return f":x: {exc}."
    via_invoker = False
    try:
        await user.send(dm_text(hunt, roster.name, invite.url))
    except discord.HTTPException:
        try:
            await invoker.send(f"{user.display_name} doesn't accept DMs from me. Pass this "
                               f"single-use invite to them privately: {invite.url}")
        except discord.HTTPException:
            await _discard(invite)
            return (f":x: I couldn't DM {user.display_name} or you. Allow DMs from server "
                    "members (Privacy Settings, on the hub) and re-run.")
        via_invoker = True
    HuntInviteDb.record(hunt.id, user.id, invite.code)
    if via_invoker:
        return (f":incoming_envelope: {user.display_name}'s DMs are closed, so I've DM'd "
                "the invite to you to pass on.")
    return f":incoming_envelope: DM'd a fresh invite to **{user.display_name}**."
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_team_invites.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Prove the hub-role check is load-bearing**

Temporarily change `if on_hub is None or roster.id not in {r.id for r in on_hub.roles}:` in `invite_one` to `if on_hub is None:`. Run `tests/test_team_invites.py` and confirm `test_refuses_someone_without_the_hub_role` fails. Restore the line by editing it back (not `git checkout`) and re-run to green.

- [ ] **Step 6: Commit**

```bash
git add bot/core/team_invites.py tests/test_team_invites.py
git commit -m "feat(invites): DM single-use team-server invites to the hub role"
```

---

### Task 3: Arrival grant and late-joiner DMs, routed by a listener cog

**Files:**
- Modify: `bot/core/team_invites.py` (append)
- Create: `bot/cogs/team_servers.py`
- Test: `tests/test_team_invites_events.py`

**Interfaces:**
- Consumes:
  - Task 1's `HuntJsonDb.live_team_hunt_for_guild`, `HuntJsonDb.live_invited_hunts_for_roster`, `HuntInviteDb.mark_joined`, `HuntInviteDb.invited_user_ids`;
  - Task 2's `deliver`, `InviteUnavailable`;
  - `server_setup.BOT_CHANNEL`.
- Produces:
  - `async on_join(bot, member) -> None`;
  - `async on_hub_roles_added(bot, member, added_role_ids: set[int]) -> None`;
  - the cog `TeamServers` with listeners `on_member_join(member)` and `on_member_update(before, after)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_team_invites_events.py`:

```python
"""Arrival and late joiners: whoever joins a team server holding the hunt's hub
role gets the team role (however they got in); whoever is given the hub role once
invites have gone out is DM'd one; failures are reported by name, never by link;
and the listener cog never lets an error escape."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.cogs.team_servers import TeamServers
from bot.core import team_invites

HUNT_ID, TEAM_ROLE, ROSTER, OTHER_HUB_ROLE, TEAM_GUILD = 42, 71, 70, 99, 2000


def _forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Cannot send messages to this user")


def _hunt():
    h = MagicMock()
    h.id = HUNT_ID
    h.name = "Big Hunt"
    h.role_id = TEAM_ROLE
    h.roster_role_id = ROSTER
    return h


def _role(role_id, name, guild=None):
    r = MagicMock()
    r.id = role_id
    r.name = name
    r.guild = guild
    return r


class World:
    def __init__(self, hub_roles=(ROSTER,), on_hub=True, held=(), team_hunt=True,
                 here=False, invited=(), dms_open=True, bot_in_team_guild=True, is_bot=False):
        self.hub = MagicMock()
        self.hub.name = "The Hub"
        self.roster = _role(ROSTER, "Team Rocket", self.hub)
        self.hub.get_role.side_effect = lambda i: self.roster if i == ROSTER else None

        self.team_role = _role(TEAM_ROLE, "Team Rocket")
        self.invites = []

        async def create_invite(**kwargs):
            inv = MagicMock()
            inv.code = f"code{len(self.invites) + 1}"
            inv.url = f"https://discord.gg/{inv.code}"
            inv.delete = AsyncMock()
            self.invites.append(inv)
            return inv

        lobby = MagicMock()
        lobby.name = "lobby"
        lobby.create_invite = create_invite
        self.bot_commands = MagicMock()
        self.bot_commands.name = "bot-commands"
        self.bot_commands.send = AsyncMock()
        self.team_guild = MagicMock()
        self.team_guild.id = TEAM_GUILD
        self.team_guild.text_channels = [self.bot_commands, lobby]
        self.team_guild.get_role.side_effect = lambda i: self.team_role if i == TEAM_ROLE else None
        self.team_guild.get_member.side_effect = lambda uid: MagicMock() if here else None

        self.bot = MagicMock()
        self.bot.get_guild.side_effect = (
            lambda gid: self.team_guild if (gid == TEAM_GUILD and bot_in_team_guild) else None)

        # The person, as a member of the team server (arrival) ...
        self.joiner = MagicMock()
        self.joiner.id = 5
        self.joiner.bot = is_bot
        self.joiner.guild = self.team_guild
        self.joiner.roles = [self.team_role] if TEAM_ROLE in held else []
        self.joiner.add_roles = AsyncMock()
        # ... and as a member of the hub (role added there).
        self.hub_person = MagicMock()
        self.hub_person.id = 5
        self.hub_person.bot = is_bot
        self.hub_person.display_name = "Fay"
        self.hub_person.guild = self.hub
        self.hub_person.roles = [_role(i, f"role {i}") for i in hub_roles]
        self.hub_person.send = AsyncMock(side_effect=None if dms_open else _forbidden())

        self.on_hub = on_hub
        self.team_hunt = team_hunt
        self.invited = set(invited)

    def patched(self, stack):
        idb = stack.enter_context(patch("bot.core.team_invites.HuntInviteDb"))
        idb.invited_user_ids.return_value = self.invited
        hdb = stack.enter_context(patch("bot.core.team_invites.HuntJsonDb"))
        hdb.live_team_hunt_for_guild.return_value = _hunt() if self.team_hunt else None
        hdb.live_invited_hunts_for_roster.side_effect = (
            lambda rid: [(_hunt(), TEAM_GUILD)] if rid == ROSTER else [])
        stack.enter_context(patch("bot.core.team_invites.role_panel.find_roster_role",
                                  return_value=self.roster))
        self.hub_member = stack.enter_context(patch(
            "bot.core.team_invites.hunt_archive.hub_member",
            new=AsyncMock(return_value=self.hub_person if self.on_hub else None)))
        return idb, hdb

    async def arrive(self):
        with ExitStack() as stack:
            idb, hdb = self.patched(stack)
            await team_invites.on_join(self.bot, self.joiner)
        return idb, hdb

    async def given(self, *role_ids):
        with ExitStack() as stack:
            idb, hdb = self.patched(stack)
            await team_invites.on_hub_roles_added(self.bot, self.hub_person, set(role_ids))
        return idb, hdb


# --- arrival ---------------------------------------------------------------------

async def test_a_hub_role_holder_gets_the_team_role_on_arrival():
    w = World()
    idb, hdb = await w.arrive()
    hdb.live_team_hunt_for_guild.assert_called_once_with(TEAM_GUILD)
    w.hub_member.assert_awaited_once_with(w.hub, 5)
    assert w.joiner.add_roles.await_args.args == (w.team_role,)
    assert "Big Hunt" in w.joiner.add_roles.await_args.kwargs["reason"]
    idb.mark_joined.assert_called_once_with(HUNT_ID, 5)


async def test_someone_without_the_hub_role_gets_nothing_on_arrival():
    w = World(hub_roles=(OTHER_HUB_ROLE,))
    idb, _ = await w.arrive()
    w.joiner.add_roles.assert_not_awaited()
    idb.mark_joined.assert_not_called()


async def test_someone_not_on_the_hub_gets_nothing_on_arrival():
    w = World(on_hub=False)
    await w.arrive()
    w.joiner.add_roles.assert_not_awaited()


async def test_a_server_without_a_team_hunt_is_ignored():
    w = World(team_hunt=False)
    await w.arrive()
    w.hub_member.assert_not_awaited()
    w.joiner.add_roles.assert_not_awaited()


async def test_a_bot_joining_is_ignored():
    w = World(is_bot=True)
    await w.arrive()
    w.joiner.add_roles.assert_not_awaited()


async def test_arriving_with_the_team_role_already_is_noted_but_not_re_granted():
    w = World(held=(TEAM_ROLE,))
    idb, _ = await w.arrive()
    w.joiner.add_roles.assert_not_awaited()
    idb.mark_joined.assert_called_once_with(HUNT_ID, 5)


# --- given the hub role later ------------------------------------------------------

async def test_someone_given_the_hub_role_is_dmd_an_invite():
    w = World()
    idb, hdb = await w.given(ROSTER)
    hdb.live_invited_hunts_for_roster.assert_called_once_with(ROSTER)
    text = w.hub_person.send.await_args.args[0]
    assert "https://discord.gg/code1" in text and "Team Rocket" in text
    idb.record.assert_called_once_with(HUNT_ID, 5, "code1")


async def test_an_unrelated_role_triggers_nothing():
    w = World()
    await w.given(OTHER_HUB_ROLE)
    w.hub_person.send.assert_not_awaited()
    assert w.invites == []


async def test_someone_already_on_the_team_server_is_not_dmd():
    w = World(here=True)
    await w.given(ROSTER)
    w.hub_person.send.assert_not_awaited()


async def test_someone_already_invited_is_not_dmd_twice():
    w = World(invited={5})
    await w.given(ROSTER)
    w.hub_person.send.assert_not_awaited()


async def test_a_team_server_the_bot_is_not_in_is_skipped():
    w = World(bot_in_team_guild=False)
    await w.given(ROSTER)
    w.hub_person.send.assert_not_awaited()


async def test_a_failed_dm_is_reported_to_the_team_server_by_name_not_link():
    w = World(dms_open=False)
    idb, _ = await w.given(ROSTER)
    w.invites[0].delete.assert_awaited_once()
    idb.record.assert_not_called()
    text = w.bot_commands.send.await_args.args[0]
    assert "Fay" in text and "!invite_player 5" in text
    assert "discord.gg" not in text


# --- the listener cog --------------------------------------------------------------

async def test_a_join_is_routed_to_team_invites():
    bot, member = MagicMock(), MagicMock()
    with patch("bot.cogs.team_servers.team_invites.on_join", new=AsyncMock()) as on_join:
        await TeamServers(bot).on_member_join(member)
    on_join.assert_awaited_once_with(bot, member)


async def test_a_join_error_never_escapes():
    with patch("bot.cogs.team_servers.team_invites.on_join",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        await TeamServers(MagicMock()).on_member_join(MagicMock())


async def test_a_role_update_routes_only_the_added_roles():
    bot = MagicMock()
    kept, added = _role(1, "kept"), _role(2, "added")
    before, after = MagicMock(), MagicMock()
    before.roles, after.roles = [kept], [kept, added]
    with patch("bot.cogs.team_servers.team_invites.on_hub_roles_added", new=AsyncMock()) as handler:
        await TeamServers(bot).on_member_update(before, after)
    handler.assert_awaited_once_with(bot, after, {2})


async def test_a_role_removal_does_nothing():
    before, after = MagicMock(), MagicMock()
    before.roles, after.roles = [_role(1, "a"), _role(2, "b")], [_role(1, "a")]
    with patch("bot.cogs.team_servers.team_invites.on_hub_roles_added", new=AsyncMock()) as handler:
        await TeamServers(MagicMock()).on_member_update(before, after)
    handler.assert_not_awaited()


async def test_a_role_update_error_never_escapes():
    before, after = MagicMock(), MagicMock()
    before.roles, after.roles = [], [_role(2, "b")]
    with patch("bot.cogs.team_servers.team_invites.on_hub_roles_added",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        await TeamServers(MagicMock()).on_member_update(before, after)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_team_invites_events.py -q > /tmp/test.log 2>&1`
Expected: collection error, `ModuleNotFoundError: No module named 'bot.cogs.team_servers'`.

- [ ] **Step 3: Append the handlers to `bot/core/team_invites.py`**

```python
# -- Events (routed by bot/cogs/team_servers.py) -----------------------------------

async def on_join(bot, member):
    """Someone joined a server. On a team server, a holder of the hunt's hub role
    gets the team role at once, however they got in; anyone else is left to
    #lobby. Hub admins are not given `mod` here -- they opt in with Join."""
    if member.bot:
        return
    hunt = HuntJsonDb.live_team_hunt_for_guild(member.guild.id)
    if hunt is None:
        return
    roster = role_panel.find_roster_role(bot, hunt.roster_role_id)
    team_role = member.guild.get_role(hunt.role_id)
    if roster is None or team_role is None:
        return
    on_hub = await hunt_archive.hub_member(roster.guild, member.id)
    if on_hub is None or roster.id not in {r.id for r in on_hub.roles}:
        return
    HuntInviteDb.mark_joined(hunt.id, member.id)
    if team_role.id not in {r.id for r in member.roles}:
        await member.add_roles(team_role, reason=f"joined with {roster.name} on the hub: {hunt.name}")


async def _tell_mods(guild, text):
    """A line for the team server's #bot-commands (owner + mod only)."""
    channel = discord.utils.get(guild.text_channels, name=server_setup.BOT_CHANNEL)
    if channel is None:
        logger.warning("guild %s has no #%s for: %s", guild.id, server_setup.BOT_CHANNEL, text)
        return
    try:
        await channel.send(text)
    except discord.HTTPException:
        logger.exception("could not post to #%s on guild %s", server_setup.BOT_CHANNEL, guild.id)


async def on_hub_roles_added(bot, member, added_role_ids):
    """Someone was given roles. For each that is the hub role of a live hunt whose
    invites have gone out, DM them an invite -- unless they're already on that
    team server or already DM'd. A failed DM is reported by name in the team
    server's #bot-commands, never with the link."""
    if member.bot:
        return
    for role_id in added_role_ids:
        for hunt, guild_snowflake in HuntJsonDb.live_invited_hunts_for_roster(role_id):
            team_guild = bot.get_guild(guild_snowflake)
            if team_guild is None or team_guild.get_member(member.id) is not None:
                continue
            if member.id in HuntInviteDb.invited_user_ids(hunt.id):
                continue
            role = member.guild.get_role(role_id)
            team = role.name if role is not None else "your team"
            try:
                delivered = await deliver(team_guild, hunt, team, member)
            except InviteUnavailable as exc:
                logger.warning("late-joiner invite for %s on guild %s: %s",
                               member.id, guild_snowflake, exc)
                delivered = False
            if not delivered:
                await _tell_mods(team_guild, (
                    f":warning: **{member.display_name}** was just given **{team}** on the "
                    f"hub, but I couldn't DM them an invite. Run `!invite_player {member.id}` here."))
```

- [ ] **Step 4: Create the listener cog**

Create `bot/cogs/team_servers.py`:

```python
"""Listeners for team servers (slice 4): grant the team role on arrival, and DM an
invite when someone is given a hunt's hub role. The logic lives in
bot/core/team_invites.py; these only route the events, and never let an error
escape into discord.py's event loop."""
import logging

from discord.ext import commands

from bot.core import team_invites

logger = logging.getLogger(__name__)


class TeamServers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        try:
            await team_invites.on_join(self.bot, member)
        except Exception:
            logger.exception("team-server arrival failed for member %s", getattr(member, "id", "?"))

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        added = {r.id for r in after.roles} - {r.id for r in before.roles}
        if not added:
            return
        try:
            await team_invites.on_hub_roles_added(self.bot, after, added)
        except Exception:
            logger.exception("late-joiner invite failed for member %s", getattr(after, "id", "?"))


async def setup(bot):
    await bot.add_cog(TeamServers(bot))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_team_invites_events.py tests/test_team_invites.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 6: Prove the arrival check is load-bearing**

Temporarily change the `on_join` line `if on_hub is None or roster.id not in {r.id for r in on_hub.roles}:` to `if on_hub is None:`. Confirm `test_someone_without_the_hub_role_gets_nothing_on_arrival` fails. Restore by editing the line back and re-run to green.

- [ ] **Step 7: Commit**

```bash
git add bot/core/team_invites.py bot/cogs/team_servers.py tests/test_team_invites_events.py
git commit -m "feat(invites): team role on arrival, and invite DMs for late joiners"
```

---

### Task 4: `!send_invites` and `!invite_player`

**Files:**
- Modify: `bot/cogs/puzzles.py`:
  - add `team_invites` to the existing line `from bot.core import category_layout, hunt_archive, media_gc, role_panel, server_setup`, keeping alphabetical order;
  - add both commands directly after the `post_role_panel` method.
- Modify: `tests/test_command_gating.py` (`EXPECTED` census: add both commands as `"admin"`, in alphabetical position)
- Test: `tests/test_invite_commands.py`

**Interfaces:**
- Consumes:
  - `team_invites.send_invites(bot, guild, hunt) -> InviteReport`;
  - `team_invites.report_text(report) -> str`;
  - `team_invites.invite_one(bot, guild, hunt, user, invoker) -> str`;
  - `HuntJsonDb.get_by_name_for_guild(name, guild_snowflake)`;
  - `HuntJsonDb.live_team_hunt_for_guild(guild_snowflake)`;
  - `self.check_is_bot_channel(ctx)`;
  - `is_admin()`.
- Produces: the bang commands `!send_invites <hunt name>` and `!invite_player <user>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_invite_commands.py`:

```python
"""!send_invites and !invite_player: admin commands on a team server that route to
team_invites, refusing anything that has no hub role to invite from."""
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.puzzles import Puzzles
from bot.core.team_invites import InviteReport
from tests.conftest import make_cog, make_ctx


def _hunt(roster_role_id=70, archived=False):
    h = MagicMock()
    h.id = 42
    h.name = "Big Hunt"
    h.roster_role_id = roster_role_id
    h.archived = archived
    return h


def _cog(in_bot_channel=True):
    cog = make_cog()
    cog.bot = MagicMock()
    cog.check_is_bot_channel = AsyncMock(return_value=in_bot_channel)
    return cog


def _sent(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


# --- !send_invites ----------------------------------------------------------------

async def _send_invites(cog, ctx, hunt, report=None):
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb, \
         patch("bot.cogs.puzzles.team_invites.send_invites",
               new=AsyncMock(return_value=report or InviteReport(sent=["Ann"]))) as run:
        hdb.get_by_name_for_guild.return_value = hunt
        await Puzzles.send_invites.callback(cog, ctx, hunt_name="Big Hunt")
    return hdb, run


async def test_send_invites_runs_and_reports():
    cog, ctx, hunt = _cog(), make_ctx(), _hunt()
    hdb, run = await _send_invites(cog, ctx, hunt)
    hdb.get_by_name_for_guild.assert_called_once_with("Big Hunt", ctx.guild.id)
    run.assert_awaited_once_with(cog.bot, ctx.guild, hunt)
    assert "Ann" in _sent(ctx)


async def test_send_invites_outside_the_bot_channel_does_nothing():
    cog, ctx = _cog(in_bot_channel=False), make_ctx()
    hdb, run = await _send_invites(cog, ctx, _hunt())
    hdb.get_by_name_for_guild.assert_not_called()
    run.assert_not_awaited()


async def test_send_invites_refuses_an_unknown_hunt():
    cog, ctx = _cog(), make_ctx()
    _, run = await _send_invites(cog, ctx, None)
    run.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_send_invites_refuses_an_archived_hunt():
    cog, ctx = _cog(), make_ctx()
    _, run = await _send_invites(cog, ctx, _hunt(archived=True))
    run.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_send_invites_refuses_a_hunt_with_no_hub_role():
    cog, ctx = _cog(), make_ctx()
    _, run = await _send_invites(cog, ctx, _hunt(roster_role_id=0))
    run.assert_not_awaited()
    assert "hub role" in _sent(ctx)


# --- !invite_player ---------------------------------------------------------------

async def _invite_player(cog, ctx, hunt, user):
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb, \
         patch("bot.cogs.puzzles.team_invites.invite_one",
               new=AsyncMock(return_value="DM'd it")) as one:
        hdb.live_team_hunt_for_guild.return_value = hunt
        await Puzzles.invite_player.callback(cog, ctx, user)
    return hdb, one


async def test_invite_player_routes_with_the_invoker():
    cog, ctx, hunt, user = _cog(), make_ctx(), _hunt(), MagicMock()
    hdb, one = await _invite_player(cog, ctx, hunt, user)
    hdb.live_team_hunt_for_guild.assert_called_once_with(ctx.guild.id)
    one.assert_awaited_once_with(cog.bot, ctx.guild, hunt, user, ctx.author)
    assert "DM'd it" in _sent(ctx)


async def test_invite_player_refuses_a_server_with_no_team_hunt():
    cog, ctx = _cog(), make_ctx()
    _, one = await _invite_player(cog, ctx, None, MagicMock())
    one.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_invite_player_outside_the_bot_channel_does_nothing():
    cog, ctx = _cog(in_bot_channel=False), make_ctx()
    hdb, one = await _invite_player(cog, ctx, _hunt(), MagicMock())
    hdb.live_team_hunt_for_guild.assert_not_called()
    one.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_invite_commands.py -q > /tmp/test.log 2>&1`
Expected: every test fails with `AttributeError: type object 'Puzzles' has no attribute 'send_invites'` (or `'invite_player'`).

- [ ] **Step 3: Implement the commands**

In `bot/cogs/puzzles.py`, change `from bot.core import category_layout, hunt_archive, media_gc, role_panel, server_setup` to `from bot.core import category_layout, hunt_archive, media_gc, role_panel, server_setup, team_invites`. Then add directly after the end of the `post_role_panel` method:

```python
    @commands.command()
    @commands.guild_only()
    @is_admin()
    async def send_invites(self, ctx, *, hunt_name: str):
        """*DM a private invite to everyone with the hunt's hub role.*

        Usage: `!send_invites <hunt name>`
        Example: `!send_invites Big Hunt`
        Each person gets a single-use invite to this server by DM, and the team
        role as soon as they arrive. Anyone already here or already invited is
        skipped, so re-running is safe; after the first run, anyone given the hub
        role is DM'd automatically. Names anyone whose DMs are closed — use
        `!invite_player` for them. Run in the bot channel. Admins/mods only."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        hunt = HuntJsonDb.get_by_name_for_guild(hunt_name, ctx.guild.id)
        if hunt is None:
            await ctx.send(f":x: No hunt named **{hunt_name}** on this server.")
            return
        if hunt.archived:
            await ctx.send(f":x: **{hunt.name}** is archived.")
            return
        if not hunt.roster_role_id:
            await ctx.send(f":x: **{hunt.name}** isn't tied to a hub role (only hunts made by "
                           "!setup_server are), so there's nobody to invite.")
            return
        await ctx.send(f"⏳ Sending invites for **{hunt.name}**…")
        report = await team_invites.send_invites(self.bot, ctx.guild, hunt)
        await ctx.send(team_invites.report_text(report))

    @commands.command()
    @commands.guild_only()
    @is_admin()
    async def invite_player(self, ctx, user: discord.User):
        """*DM one person a fresh private invite to this team server.*

        Usage: `!invite_player <user id or name>`
        Example: `!invite_player 123456789012345678`
        For someone with the hunt's hub role whose invite DM failed or got lost.
        If their DMs are closed, the invite is DM'd to you to pass on privately.
        Run in the bot channel. Admins/mods only."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        hunt = HuntJsonDb.live_team_hunt_for_guild(ctx.guild.id)
        if hunt is None:
            await ctx.send(":x: This server has no live hunt with a hub role (only !setup_server "
                           "makes those).")
            return
        await ctx.send(await team_invites.invite_one(self.bot, ctx.guild, hunt, user, ctx.author))
```

In `tests/test_command_gating.py`, add `("invite_player", "admin"),` and `("send_invites", "admin"),` to the `EXPECTED` map at their alphabetical positions, matching how the existing entries are written there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_invite_commands.py tests/test_command_gating.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_invite_commands.py tests/test_command_gating.py
git commit -m "feat(invites): !send_invites and !invite_player"
```

---

### Task 5: The full setup walk-through, docs, spec write-back, verification

**Files:**
- Modify: `bot/cogs/settings.py`: replace `TEMPLATE_SETUP_MESSAGE`.
- Modify: `bot/cogs/puzzles.py`: the `setup_server` completion line beginning `lines.append("Next: players with the hub role click **Join**`.
- Test: `tests/test_template_greeting.py` (one new test), `tests/test_setup_server_command.py` (one new test)
- Modify: `docs/commands.md`, `docs/new-server-playbook.md`, `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`

**Interfaces:**
- Consumes: the commands from Task 4; `server_setup.TEMPLATE_MOD_ROLE`, `server_setup.BOT_CHANNEL`, `server_setup.LOBBY_CHANNEL`.
- Produces: user-facing copy only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_template_greeting.py`:

```python
async def test_template_greeting_walks_through_the_whole_setup():
    g = _template_guild()
    await _join(g)
    text = _bot_commands(g).sent[0]
    steps = ["above `mod`", "!setup_server <hub server id>", "**Join**",
             "!send_invites <hunt name>", "ownership"]
    for step in steps:
        assert step in text, step
    positions = [text.index(s) for s in steps]
    assert positions == sorted(positions), "steps are out of order"
```

Append to `tests/test_setup_server_command.py`:

```python
async def test_a_completed_setup_points_at_join_then_send_invites():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    await _call(cog, ctx, _settings())
    text = _sent(ctx)
    assert "**Join**" in text and "!send_invites Big Hunt" in text
    assert text.index("**Join**") < text.index("!send_invites Big Hunt")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_template_greeting.py tests/test_setup_server_command.py -q > /tmp/test.log 2>&1`
Expected: exactly the two new tests fail, both on missing `!send_invites`. Everything else passes.

- [ ] **Step 3: Rewrite the two messages**

In `bot/cogs/settings.py`, replace the whole `TEMPLATE_SETUP_MESSAGE = f"""…"""` assignment with:

```python
TEMPLATE_SETUP_MESSAGE = f"""\
:wave: **Thanks for adding me!** This looks like a team server made from the template. \
To finish setting it up:

**1.** In Server Settings → Roles, drag my role above `{server_setup.TEMPLATE_MOD_ROLE}`.
**2.** Here in #{server_setup.BOT_CHANNEL}, run:
```
!setup_server <hub server id> <hub role id> <hunt url> <hunt name>
```
You must be this server's owner and an admin on the hub. To copy the ids, turn on \
Developer Mode (User Settings → Advanced), then right-click the hub's server icon, \
and the team's role in the hub's Server Settings → Roles. If it stops partway, fix \
what it says and re-run the same command.
**3.** Click **Join** in #{server_setup.LOBBY_CHANNEL}. As a hub admin you also get \
`{server_setup.TEMPLATE_MOD_ROLE}`, which the next step needs.
**4.** Check the server looks right, then run `!send_invites <hunt name>` here: everyone \
with the hub role gets a private invite by DM, and anyone given the role later is \
invited automatically.
**5.** Optionally, transfer server ownership to someone on the team."""
```

In `bot/cogs/puzzles.py`, `setup_server`, replace the two-line statement

```python
            lines.append("Next: players with the hub role click **Join** in #lobby (hub admins get "
                         "`mod` too); transfer server ownership to a team member if wanted.")
```

with:

```python
            lines.append(f"Next: click **Join** in #lobby (as a hub admin you get `mod` too), check "
                         f"the server, then run `!send_invites {hunt_name}` to DM everyone with "
                         "the hub role. Transfer server ownership to a team member if wanted.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_template_greeting.py tests/test_setup_server_command.py tests/test_guild_settings_backfill.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: `docs/commands.md`**

- Admins/mods summary table, *Hunt lifecycle* row: add `` · `!send_invites` · `!invite_player` `` after `` `!post_role_panel` ``.
- Detailed table: add these two rows directly after the `!post_role_panel` row:

```markdown
| `!send_invites` | | `hunt name` | bot channel | admins/mods | DM a private, single-use invite to this team server to everyone holding the hunt's hub role; they get the team role on arrival. Skips anyone already here or already invited, so re-running is safe. After the first run, anyone given the hub role is DM'd automatically. Names (never links) anyone whose DMs are closed. |
| `!invite_player` | | `user id or name` | bot channel | admins/mods | DM one hub-role holder a fresh invite to this team server; if their DMs are closed, it's DM'd to you to pass on privately. (`!invite` is the bot's own invite link.) |
```

- [ ] **Step 6: Playbook**

In `docs/new-server-playbook.md`, "Team servers", *Per team, per hunt*:
- In step 4, change `posts the next two steps in` to `posts the remaining steps in`.
- Replace steps 7 and 8 (from `7. Optionally transfer server ownership` through the end of step 8, ending `re-posts the panel if it goes missing.`) with:

```markdown
7. Click **Join** in `#lobby`. As a hub admin you also get `mod`, which you need for the
   next step (and for `!archive_hunt_to_hub` at hunt end).
8. Check the server looks right, then in `#bot-commands`: `!send_invites <hunt name>`.
   Everyone with the hub role gets a private, single-use invite by DM, and the bot gives
   them the team role as they arrive. The reply names anyone whose DMs are closed — run
   `!invite_player <user id>` for each (if their DMs are still closed, the invite is DM'd
   to you to pass on). Re-running is safe: anyone already here or invited is skipped. From
   then on, anyone given the hub role is DM'd an invite automatically.
9. Optionally transfer server ownership to a member of that team and have the group
   account leave.

Anyone who arrives some other way lands in `#lobby`: **Join** gives them the team role if
they hold the hub role, and nothing otherwise. **Leave** removes the team role and `mod`;
someone given `mod` by hand who isn't a hub admin can't get it back through Join.
`!post_role_panel <hunt name>` re-posts the panel if it goes missing.
```

- [ ] **Step 7: Spec write-back**

In the spec:
- Status line (line 4): append `; slice 4 (invites) — plan 2026-10-30`.
- Directly under the heading `## Invite delivery`, insert:

```markdown
*Changed 2026-10-30 (plan 2026-10-30-team-invites.md):*
- **Arrival grant:** the team role is granted on arrival by the hub role alone (the Join check), not by matching the invite used. Discord doesn't report which invite was used, and a forwarded invite would otherwise grant the role.
- **`hunt_invites`** records who was DM'd, so nobody is DM'd twice and `joined_at` shows who arrived.
- **`!send_invites <hunt name>`** sends the first DMs, run by a mod after checking the server, rather than `!setup_server`. Its first completed run stamps `hunts.invites_sent_time`, which switches on the late-joiner listener.
- **The spec's `!invite <user>` is `!invite_player <user>`,** because `!invite` already shows the bot's own link. If the player's DMs are closed, it DMs the link to the invoker to pass on.
- **Failed DMs** delete the invite and record nothing. A failed late-joiner DM is reported by name, never by link, in the team server's `#bot-commands`.
```

- [ ] **Step 8: Full verification**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass. Read the tail (`tail -n 5 /tmp/test.log`).

Run: `pipenv run ruff check bot/core/team_invites.py bot/cogs/team_servers.py bot/cogs/puzzles.py bot/cogs/settings.py bot/store/mysqldb.py bot/store/__init__.py tests/test_team_invites.py tests/test_team_invites_events.py tests/test_team_invites_store.py tests/test_team_invites_live_db.py tests/test_invite_commands.py`
Expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add bot/cogs/settings.py bot/cogs/puzzles.py tests/test_template_greeting.py tests/test_setup_server_command.py docs/commands.md docs/new-server-playbook.md docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md
git commit -m "feat(invites): the full setup walk-through, and docs for slice 4"
```

---

### Task 6: `!archive_hunt_to_hub` finds the hub by itself

**Why:** a hunt made by `!setup_server` stores its hub role (`hunts.roster_role_id`), and role ids are unique across Discord. So the role's server *is* the hub, and asking for its id is redundant. Join already finds the hub this way (`role_panel.find_roster_role`). Decided with the user 2026-10-30:
- the id becomes optional, and when omitted the hunt's hub is used;
- a given id is still accepted, for hunts with no hub role;
- a given id that disagrees with the hunt's hub role is refused, so nobody archives to the wrong server by mistake.

**Files:**
- Modify: `bot/core/hunt_archive.py` (a new pure function `choose_hub_id`, after `class HubUnavailable`)
- Modify: `bot/cogs/puzzles.py` (`archive_hunt_to_hub`: signature, docstring, and the hub-choosing lines before `resolve_hub`)
- Test: `tests/test_choose_hub_id.py` (new), `tests/test_archive_hunt_to_hub_command.py` (three new tests, and the `_hunt` helper)
- Modify: `docs/commands.md`, `docs/new-server-playbook.md`, the spec

**Interfaces:**
- Consumes: `role_panel.find_roster_role(bot, roster_role_id)` (its `.guild.id` is the hub's snowflake); `hunt_archive.resolve_hub(bot, hub_guild_id, guild, invoker)` (unchanged).
- Produces: `hunt_archive.choose_hub_id(given_hub_id: Optional[int], roster_hub_id: Optional[int]) -> int`, which raises `HubUnavailable`.

`hunt_archive` must **not** import `role_panel`, because `role_panel` already imports `hunt_archive`. The cog looks the role up and passes the hub's id in, which keeps `choose_hub_id` a pure function.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_choose_hub_id.py`:

```python
"""choose_hub_id: !archive_hunt_to_hub's hub is the server holding the hunt's hub
role when there is one; an explicit id is for hunts without one, and an id that
disagrees with the hub role is refused rather than trusted."""
import pytest

from bot.core.hunt_archive import HubUnavailable, choose_hub_id

HUB, OTHER = 1000, 2000


def test_no_id_uses_the_hub_role_s_server():
    assert choose_hub_id(None, HUB) == HUB


def test_no_id_and_no_hub_role_asks_for_the_id():
    with pytest.raises(HubUnavailable, match="server id"):
        choose_hub_id(None, None)


def test_an_id_is_used_when_the_hunt_has_no_hub_role():
    assert choose_hub_id(OTHER, None) == OTHER


def test_an_id_matching_the_hub_role_is_fine():
    assert choose_hub_id(HUB, HUB) == HUB


def test_an_id_disagreeing_with_the_hub_role_is_refused():
    with pytest.raises(HubUnavailable) as exc:
        choose_hub_id(OTHER, HUB)
    assert str(HUB) in str(exc.value) and str(OTHER) in str(exc.value)
```

In `tests/test_archive_hunt_to_hub_command.py`, the `_hunt()` helper builds a `MagicMock` with no `roster_role_id`. An auto-created attribute would read as a truthy hub role. Add `h.roster_role_id = 0` to it: that is the shape of a hunt with no hub role, the same as every hunt before slice 2. No existing assertion changes. Do the same for any hunt mock passed to `archive_hunt_to_hub` in `tests/test_lock_ordering.py` (grep `archive_hunt_to_hub.callback` there). Then append:

```python
async def test_with_no_id_it_archives_to_the_hunt_s_hub():
    hunt = _hunt(); hunt.roster_role_id = 70
    cog = make_cog(hunt=hunt); cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock()
    roster = MagicMock(); roster.guild.id = 1000
    ctx = _ctx()
    with patch("bot.cogs.puzzles.role_panel.find_roster_role", return_value=roster) as find, \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=AsyncMock(side_effect=hunt_archive.HubUnavailable("stop here"))) as resolve:
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx)
    find.assert_called_once_with(cog.bot, 70)
    resolve.assert_awaited_once_with(cog.bot, 1000, ctx.guild, ctx.author)


async def test_an_id_disagreeing_with_the_hunt_s_hub_is_refused_before_resolving():
    hunt = _hunt(); hunt.roster_role_id = 70
    cog = make_cog(hunt=hunt); cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock()
    roster = MagicMock(); roster.guild.id = 1000
    ctx = _ctx()
    with patch("bot.cogs.puzzles.role_panel.find_roster_role", return_value=roster), \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub", new=AsyncMock()) as resolve:
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 2000)
    resolve.assert_not_awaited()
    cog.service.archive_hunt_to_hub.assert_not_awaited()
    reply = ctx.send.await_args.args[0]
    assert reply.startswith(":x:") and "1000" in reply


async def test_no_id_on_a_hunt_without_a_hub_role_asks_for_one():
    cog = make_cog(hunt=_hunt()); cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock()
    ctx = _ctx()
    with patch("bot.cogs.puzzles.hunt_archive.resolve_hub", new=AsyncMock()) as resolve:
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx)
    resolve.assert_not_awaited()
    assert "server id" in ctx.send.await_args.args[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_choose_hub_id.py tests/test_archive_hunt_to_hub_command.py tests/test_lock_ordering.py -q > /tmp/test.log 2>&1`
Expected:
- `tests/test_choose_hub_id.py` errors on `ImportError: cannot import name 'choose_hub_id'`;
- the three new command tests fail (`archive_hunt_to_hub` still requires `hub_id`);
- every pre-existing test still passes.

- [ ] **Step 3: Implement**

In `bot/core/hunt_archive.py`, directly after the `HubUnavailable` class, add:

```python
def choose_hub_id(given_hub_id, roster_hub_id):
    """The hub server !archive_hunt_to_hub archives to. *roster_hub_id* is the
    server holding the hunt's hub role (None if it has none): role ids are
    unique, so that server is the hub. An explicit *given_hub_id* is for hunts
    without a hub role; one that disagrees with the hub role is refused."""
    if given_hub_id is None:
        if roster_hub_id is None:
            raise HubUnavailable("this hunt has no hub role to find its hub from — give the "
                                 "hub's server id: `!archive_hunt_to_hub <hub server id>`")
        return roster_hub_id
    if roster_hub_id is not None and given_hub_id != roster_hub_id:
        raise HubUnavailable(f"this hunt's hub role is on server {roster_hub_id}, not "
                             f"{given_hub_id} — leave the id out to archive there")
    return given_hub_id
```

In `bot/cogs/puzzles.py`, `archive_hunt_to_hub`:
- change the signature `async def archive_hunt_to_hub(self, ctx, hub_id: int):` to `async def archive_hunt_to_hub(self, ctx, hub_id: Optional[int] = None):`;
- in the docstring, change ``Usage: `!archive_hunt_to_hub <hub server id>` `` to ``Usage: `!archive_hunt_to_hub [hub server id]` ``, and add after the `Example:` line:

```
        On a team server made by !setup_server, leave the id out: the hub is the
        server holding the hunt's hub role. Give it only for hunts without one.
```

- replace

```python
        try:
            hub, hub_row = await hunt_archive.resolve_hub(self.bot, hub_id, ctx.guild, ctx.author)
        except hunt_archive.HubUnavailable as e:
```

with

```python
        roster = (role_panel.find_roster_role(self.bot, hunt.roster_role_id)
                  if hunt.roster_role_id else None)
        try:
            hub_id = hunt_archive.choose_hub_id(hub_id, roster.guild.id if roster else None)
            hub, hub_row = await hunt_archive.resolve_hub(self.bot, hub_id, ctx.guild, ctx.author)
        except hunt_archive.HubUnavailable as e:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_choose_hub_id.py tests/test_archive_hunt_to_hub_command.py tests/test_lock_ordering.py tests/test_archive_hunt_to_hub.py tests/test_hunt_archive.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Prove the mismatch guard is load-bearing**

Temporarily delete the `if roster_hub_id is not None and given_hub_id != roster_hub_id:` branch (both of its lines). Confirm `test_an_id_disagreeing_with_the_hub_role_is_refused` and `test_an_id_disagreeing_with_the_hunt_s_hub_is_refused_before_resolving` fail. Restore by editing the lines back, and re-run to green.

- [ ] **Step 6: Docs**

- `docs/commands.md`, the detailed row beginning `` | `!archive_hunt_to_hub <hub server id>` ``: change that first cell to `` `!archive_hunt_to_hub [hub server id]` ``, and add as the last sentence of the description cell (before its closing `|`): ``On a team server made by `!setup_server` leave the id out — the hub is found from the hunt's hub role; an id that disagrees with it is refused.``
- `docs/new-server-playbook.md`: change ``At hunt end, `!archive_hunt_to_hub <hub id>` moves the history to the hub.`` to ``At hunt end, `!archive_hunt_to_hub` (in any hunt channel, no id needed) moves the history to the hub.``
- Spec: directly under the heading `## Archiving and teardown`, insert:

```markdown
*Changed 2026-10-30 (plan 2026-10-30-team-invites.md, Task 6):* the hub id is optional. A hunt made by `!setup_server` stores its hub role, whose server is the hub (`hunt_archive.choose_hub_id`); an explicit id is for hunts without one, and one that disagrees with the hub role is refused.
```

- [ ] **Step 7: Full verification, then commit**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`, and confirm all pass (`tail -n 5 /tmp/test.log`).
Run: `pipenv run ruff check bot/core/hunt_archive.py bot/cogs/puzzles.py tests/test_choose_hub_id.py tests/test_archive_hunt_to_hub_command.py tests/test_lock_ordering.py`, and confirm `All checks passed!`

```bash
git add bot/core/hunt_archive.py bot/cogs/puzzles.py tests/test_choose_hub_id.py tests/test_archive_hunt_to_hub_command.py tests/test_lock_ordering.py docs/commands.md docs/new-server-playbook.md docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md
git commit -m "feat(archive): !archive_hunt_to_hub finds the hub from the hunt's hub role"
```

(Leave `tests/test_lock_ordering.py` out of `git add` if it needed no change.)

---

## Manual acceptance (test deployment, after merge to staging)

First apply migration **048** to the test database. If the test subscription's deploy runs `deploy.sh`, that happens automatically; otherwise run `pipenv run python migrate.py migrate`. Then restart the bot on the merged code.

1. **Greeting:** invite the test bot to a fresh template server. `#bot-commands` shows all five steps.
2. **Setup:** run `!setup_server`. The completion message points at Join, then `!send_invites <hunt name>`.
3. **First DMs:** click Join (you get `mod`). Make sure a second account holds the hub role and isn't on the team server. Run `!send_invites <hunt name>`.
   - The second account gets a DM with a link.
   - The reply names them, and no link appears in the channel.
4. **Arrival:** the second account uses the link and lands with the team role already granted, without clicking Join.
5. **Single use:** the same link no longer works for a third account.
6. **Re-run:** `!send_invites` again reports them as already here, and sends no new DM.
7. **Late joiner:** give a third account the hub role on the hub. It gets a DM by itself within seconds.
8. **Closed DMs:** that third account turns off DMs from server members (Privacy Settings on the hub), and its role is removed and re-added. Expected: `#bot-commands` gets a "couldn't DM … `!invite_player <id>`" line with no link. Then run `!invite_player <id>`, and the link arrives in **your** DMs.
9. **Wrong person:** someone without the hub role who arrives via a forwarded link gets no team role, and Join refuses them.
10. **Archive without an id:** at the end, run `!archive_hunt_to_hub` (no id) in a hunt channel. It names the right hub in its confirmation. A different hub's id is refused.
