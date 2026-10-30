# `#lobby` Role Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A team server's `#lobby` welcome gets two persistent buttons, **Join** and **Leave**, for the hunt's team role. Join works only for someone who holds the hunt's roster role on the hub, or is a hub admin. A hub admin also gets this server's admin-tier role (`mod`). Leave takes both away. `!setup_server` posts the panel, and a new `!post_role_panel <hunt name>` re-posts it.

**Architecture:** A new ctx-free module, `bot/core/role_panel.py`, holds the eligibility and role-change logic (`join`, `leave`, `find_roster_role`) and the buttons. The buttons are a discord.py `DynamicItem` (`HuntRoleButton`) whose `custom_id` carries the action and the hunt id (`hunt_role:join:42`). Registering the class once with `bot.add_dynamic_items()` in the Puzzles cog's `setup()` makes every panel ever posted work after a restart, and no view state is stored anywhere. `server_setup.run_setup` attaches `panel_view(hunt.id)` to the welcome it already posts. The new bang command posts the same welcome and view.

**Tech Stack:** Python 3.12, discord.py 2.7.1 (`discord.ui.DynamicItem`, `View(timeout=None)`), pytest (`asyncio_mode = auto`), `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`, section *The `#lobby` role panel*, and acceptance criteria 5 and 8. This is **slice 3**. Slice 1 is `docs/superpowers/plans/2026-09-24-cross-server-hunt-archive.md` and slice 2 is `docs/superpowers/plans/2026-10-14-setup-server.md`, both shipped to staging. It departs from the spec in the places listed under *Decisions*, all decided with the user on 2026-10-30. Task 5 writes them back into the spec.

## Decisions taken during planning (override the spec)

- **Join is gated on the hub roster role (user decision, 2026-10-30).** The spec let anyone already in the team server click Join. Until slice 4's single-use DM'd invites exist, players arrive by ordinary invite links, and a leaked link would let another team's player in to self-grant the role. So Join first checks that the clicker is a member of the hub **and** either holds `hunts.roster_role_id` there or holds the hub's Admin tier. Anyone else gets an ephemeral refusal naming the role and the hub. Leave is not gated.
- **Mod elevation grants this server's pinned Admin-tier roles,** i.e. `role_gate.tier_role_ids(guild, ADMIN_ROLES)`, which `!setup_server` pinned to the template's `mod` role. It is not a lookup by name, so it keeps working if someone renames `mod`. Hub admin status is checked with `role_gate.member_holds_tier(..., hub, ADMIN_ROLES)`, the same check `resolve_hub` uses.
- **Leave removes the team role and those Admin-tier roles,** as the spec says, even if `mod` was handed out by hand. Someone given `mod` by hand who is not a hub admin cannot get it back through Join. They ask an admin, as they did before the panel existed. The playbook says so.
- **The buttons live in `bot/core/role_panel.py`, not `bot/cogs/ui.py`.** `server_setup` (core) posts the panel, and core must not import a cog module. They are a `DynamicItem`, not a `View` subclass registered with `add_view` per hunt. One `add_dynamic_items` call covers every hunt, past and future.
- **The `custom_id` carries `hunts.id` (an int), not `hunts.uid`.** It is shorter, the template can match it with `[0-9]+`, and it is what `HuntJsonDb.get_by_attr(id=…)` looks up. A click checks that the hunt belongs to the server it was clicked in, so a panel copied elsewhere does nothing.
- **The panel is the welcome message.** `run_setup` sends the existing welcome with the view attached, rather than a second message. The welcome text changes to point at Join.
- **`!post_role_panel <hunt name>` is new.** It covers servers set up before this slice (the 2026-10-30 trial server) and a deleted panel. It is Admin tier, runs in the bot channel, and refuses hunts with no `roster_role_id` (anything not made by `!setup_server`). Posting again is harmless, because old panels keep working.
- **Discord's audit log gets a `reason=`** on every grant and removal (`role panel: join <hunt>`). The bot's own access log (`hunt_access_log` and the hub mirror) remains slice 5.
- **`hunt_archive._hub_member` becomes public `hub_member`.** It is renamed for visibility only, not because its meaning changed. Its one other call site is `resolve_hub`.
- **Dropped from this slice:**
  - a dashboard equivalent of `!post_role_panel`;
  - any change to `!setup_server`'s run-once lock;
  - invites (slice 4);
  - the access log (slice 5).

## Roadmap

1. Cross-server archive. **Shipped.**
2. Template + `!setup_server`. **Shipped** to staging (the template was built and trialled 2026-10-30).
3. **This plan:** the `#lobby` Join/Leave panel with the roster check and mod elevation.
4. Private invites: `hunt_invites`, single-use invite DMs to every member of `hunts.roster_role_id`, `on_member_join` auto-grant, `!invite`, the late-joiner listener on the hub.
5. Access audit: `hunt_access_log` + hub mirror.

The 2026-10-14 plan listed invites as 3 and the panel as 4. The spec's own text (and this order) puts the panel first, because invites rely on it for anyone the auto-grant misses.

## Global Constraints

- Default branch is **`staging`**, not `main`. Work in a worktree branched from **local** `staging` (origin is intentionally behind). Before running tests, copy the gitignored `config.json` + `google_secrets.json` into the new worktree. Without them conftest dies on MySQL access-denied (pytest exit 4).
- Test command (always wrapped, always to a file, single statement):
  `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`
- Patch where names are **looked up**:
  - `bot.core.role_panel.HuntJsonDb`, `bot.core.role_panel.GuildSettingsDb`;
  - `bot.core.role_panel.role_gate.tier_role_ids`, `bot.core.role_panel.role_gate.member_holds_tier`;
  - `bot.core.role_panel.hunt_archive.hub_member`;
  - `bot.cogs.puzzles.HuntJsonDb`, `bot.cogs.puzzles.Puzzles`.
- **No migration in this slice.** Nothing touches the schema.
- A `HuntRoleButton` whose hunt id is not digits raises `ValueError` at construction. This is discord.py's own check against the template (verified on 2.7.1). Any test hunt that reaches `panel_view` must have an int `.id`.
- User-facing copy says "role", "hub" and "team server". Replies are ephemeral; `#lobby` gets no bot chatter beyond the panel itself.

---

### Task 1: `role_panel.join` / `leave`: who may join, and what they get

**Files:**
- Create: `bot/core/role_panel.py`
- Modify: `bot/core/hunt_archive.py` (rename `_hub_member` → `hub_member`: the definition and its one call in `resolve_hub`)
- Test: `tests/test_role_panel.py`

**Interfaces:**
- Consumes: `hunt_archive.hub_member(hub, user_id) -> Optional[discord.Member]` (async); `role_gate.tier_role_ids(guild, names) -> frozenset[int]`; `role_gate.member_holds_tier(member_role_ids, guild, names) -> bool`; `HuntJsonDb.get_by_attr(id=…)`; `GuildSettingsDb.get(guild_snowflake)` (its `.id` is the row id that `hunts.guild_id` references).
- Produces:
  - `find_roster_role(bot, roster_role_id) -> Optional[discord.Role]`;
  - `async join(bot, guild, member, hunt_id: int) -> str`;
  - `async leave(guild, member, hunt_id: int) -> str`.

  Both return the ephemeral reply text. Neither raises on a refusal.

- [ ] **Step 1: Make `_hub_member` public**

In `bot/core/hunt_archive.py`, rename the function `_hub_member` to `hub_member` (the `async def` line) and update its one caller in `resolve_hub` (`member = await _hub_member(hub, invoker.id)` → `member = await hub_member(hub, invoker.id)`). Then confirm nothing else refers to the old name:

Run: `grep -rn "_hub_member" bot/ tests/`
Expected: no output.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_role_panel.py`:

```python
"""role_panel.join / leave: Join is for the hunt's roster role on the hub, or a
hub admin (who also gets this server's Admin-tier role); Leave takes both away.
Nobody else is granted anything."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.core import role_panel

HUNT_ID, GUILD_ROW, TEAM_ROLE, MOD, ROSTER, OTHER_HUB_ROLE, USER = 42, 7, 71, 50, 70, 99, 9


def _role(role_id, name, guild=None):
    r = MagicMock()
    r.id = role_id
    r.name = name
    r.guild = guild
    return r


def _hunt(archived=False, roster_role_id=ROSTER, guild_id=GUILD_ROW):
    h = MagicMock()
    h.id = HUNT_ID
    h.name = "Big Hunt"
    h.role_id = TEAM_ROLE
    h.roster_role_id = roster_role_id
    h.guild_id = guild_id
    h.archived = archived
    return h


class World:
    """A team server and a hub, with one person on each. By default, an ordinary
    roster player who holds nothing on the team server yet."""

    def __init__(self, hub_roles=(ROSTER,), held=(), on_hub=True, hub_admin=False,
                 hunt=None, roster_found=True):
        self.hub = MagicMock()
        self.hub.name = "The Hub"
        self.roster = _role(ROSTER, "Team Rocket", self.hub)
        self.hub.get_role.side_effect = (
            lambda i: self.roster if (i == ROSTER and roster_found) else None)
        unrelated = MagicMock()
        unrelated.get_role.return_value = None
        self.bot = MagicMock()
        self.bot.guilds = [unrelated, self.hub]

        self.team_role = _role(TEAM_ROLE, "Team Rocket")
        self.mod = _role(MOD, "mod")
        roles = {TEAM_ROLE: self.team_role, MOD: self.mod}
        self.guild = MagicMock()
        self.guild.id = 2000
        self.guild.get_role.side_effect = roles.get

        self.member = MagicMock()
        self.member.id = USER
        self.member.roles = [roles[i] for i in held]
        self.member.add_roles = AsyncMock()
        self.member.remove_roles = AsyncMock()

        self.hub_member = None
        if on_hub:
            self.hub_member = MagicMock()
            self.hub_member.roles = [_role(i, f"role {i}") for i in hub_roles]
        self.hub_admin = hub_admin
        self.hunt = hunt or _hunt()

    def _patched(self, stack):
        hdb = stack.enter_context(patch("bot.core.role_panel.HuntJsonDb"))
        hdb.get_by_attr.return_value = self.hunt
        gdb = stack.enter_context(patch("bot.core.role_panel.GuildSettingsDb"))
        gdb.get.return_value = MagicMock(id=GUILD_ROW)
        stack.enter_context(patch(
            "bot.core.role_panel.role_gate.tier_role_ids",
            side_effect=lambda g, names: frozenset({MOD}) if g is self.guild else frozenset()))
        stack.enter_context(patch(
            "bot.core.role_panel.role_gate.member_holds_tier",
            side_effect=lambda ids, g, names: g is self.hub and self.hub_admin))
        stack.enter_context(patch(
            "bot.core.role_panel.hunt_archive.hub_member",
            new=AsyncMock(return_value=self.hub_member)))
        return hdb

    async def join(self):
        with ExitStack() as stack:
            hdb = self._patched(stack)
            reply = await role_panel.join(self.bot, self.guild, self.member, HUNT_ID)
        hdb.get_by_attr.assert_called_once_with(id=HUNT_ID)
        return reply

    async def leave(self):
        with ExitStack() as stack:
            self._patched(stack)
            return await role_panel.leave(self.guild, self.member, HUNT_ID)


# --- find_roster_role ---------------------------------------------------------

def test_find_roster_role_looks_in_every_server_the_bot_is_in():
    w = World()
    assert role_panel.find_roster_role(w.bot, ROSTER) is w.roster


def test_find_roster_role_is_none_when_no_server_has_it():
    w = World(roster_found=False)
    assert role_panel.find_roster_role(w.bot, ROSTER) is None


# --- join: who gets in ---------------------------------------------------------

async def test_a_roster_player_gets_the_team_role_only():
    w = World()
    reply = await w.join()
    w.member.add_roles.assert_awaited_once()
    assert w.member.add_roles.await_args.args == (w.team_role,)
    assert "Big Hunt" in w.member.add_roles.await_args.kwargs["reason"]
    assert "Team Rocket" in reply


async def test_a_hub_admin_gets_the_team_role_and_mod():
    w = World(hub_roles=(OTHER_HUB_ROLE,), hub_admin=True)
    await w.join()
    assert w.member.add_roles.await_args.args == (w.team_role, w.mod)


async def test_a_hub_admin_who_already_plays_still_gets_mod():
    w = World(hub_admin=True, held=(TEAM_ROLE,))
    await w.join()
    assert w.member.add_roles.await_args.args == (w.mod,)


async def test_a_hub_member_without_the_roster_role_is_refused():
    w = World(hub_roles=(OTHER_HUB_ROLE,))
    reply = await w.join()
    w.member.add_roles.assert_not_awaited()
    assert "Team Rocket" in reply and "The Hub" in reply


async def test_someone_not_on_the_hub_is_refused():
    w = World(on_hub=False)
    reply = await w.join()
    w.member.add_roles.assert_not_awaited()
    assert "The Hub" in reply


async def test_a_roster_role_no_server_has_refuses():
    w = World(roster_found=False)
    await w.join()
    w.member.add_roles.assert_not_awaited()


async def test_a_hunt_with_no_roster_role_refuses():
    w = World(hunt=_hunt(roster_role_id=0))
    await w.join()
    w.member.add_roles.assert_not_awaited()


async def test_an_archived_hunt_refuses_join():
    w = World(hunt=_hunt(archived=True))
    reply = await w.join()
    w.member.add_roles.assert_not_awaited()
    assert "over" in reply


async def test_a_hunt_on_another_server_refuses_join():
    w = World(hunt=_hunt(guild_id=GUILD_ROW + 1))
    await w.join()
    w.member.add_roles.assert_not_awaited()


async def test_already_holding_the_team_role_is_a_no_op():
    w = World(held=(TEAM_ROLE,))
    reply = await w.join()
    w.member.add_roles.assert_not_awaited()
    assert "already" in reply


async def test_a_role_the_bot_cannot_grant_names_the_fix():
    w = World()
    w.member.add_roles.side_effect = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no")
    reply = await w.join()
    assert "drag my role" in reply


# --- leave ---------------------------------------------------------------------

async def test_leave_removes_the_team_role_and_mod():
    w = World(held=(TEAM_ROLE, MOD))
    await w.leave()
    assert w.member.remove_roles.await_args.args == (w.team_role, w.mod)
    assert "Big Hunt" in w.member.remove_roles.await_args.kwargs["reason"]


async def test_leave_removes_only_what_is_held():
    w = World(held=(TEAM_ROLE,))
    await w.leave()
    assert w.member.remove_roles.await_args.args == (w.team_role,)


async def test_leave_with_nothing_held_is_a_no_op():
    w = World()
    reply = await w.leave()
    w.member.remove_roles.assert_not_awaited()
    assert "nothing to leave" in reply


async def test_leave_is_not_gated_on_the_hub():
    w = World(on_hub=False, held=(TEAM_ROLE,))
    await w.leave()
    w.member.remove_roles.assert_awaited_once()


async def test_a_hunt_on_another_server_refuses_leave():
    w = World(hunt=_hunt(guild_id=GUILD_ROW + 1), held=(TEAM_ROLE,))
    await w.leave()
    w.member.remove_roles.assert_not_awaited()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_role_panel.py -q > /tmp/test.log 2>&1`
Expected: collection error, `ModuleNotFoundError: No module named 'bot.core.role_panel'`.

- [ ] **Step 4: Write the module**

Create `bot/core/role_panel.py` (the buttons are added in Task 2):

```python
"""The #lobby role panel: Join / Leave for a team server's hunt role (slice 3 of
the fresh-server-per-team spec).

Join is for members of the hunt's roster role on the hub, or hub admins; a hub
admin also gets this server's Admin-tier roles (`mod`, pinned by !setup_server).
Leave removes both. Every reply is the ephemeral text shown to the clicker.

Spec: docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md;
plan: docs/superpowers/plans/2026-10-30-lobby-role-panel.md.
"""
import logging

import discord

from bot.core import hunt_archive, role_gate
from bot.core.constants import ADMIN_ROLES
from bot.store import GuildSettingsDb, HuntJsonDb

logger = logging.getLogger(__name__)

_CANT_GRANT = ("I'm not allowed to change that role — an admin needs to drag my role "
               "above it in Server Settings → Roles.")


def find_roster_role(bot, roster_role_id):
    """The hub role *roster_role_id*, from whichever server the bot shares that
    holds it, or None. Role ids are snowflakes, unique across Discord."""
    for g in bot.guilds:
        role = g.get_role(roster_role_id)
        if role is not None:
            return role
    return None


def _admin_roles(guild):
    """This server's Admin-tier roles, as Role objects (on a team server, `mod`)."""
    roles = (guild.get_role(i) for i in role_gate.tier_role_ids(guild, ADMIN_ROLES))
    return [r for r in roles if r is not None]


def _hunt_here(guild, hunt_id):
    """The hunt *hunt_id* if it lives on *guild*, else None: a panel only acts
    for its own server's hunt."""
    hunt = HuntJsonDb.get_by_attr(id=hunt_id)
    settings = GuildSettingsDb.get(guild.id)
    if hunt is None or settings is None or hunt.guild_id != settings.id:
        return None
    return hunt


def _names(roles):
    return ", ".join(f"**{r.name}**" for r in roles)


async def join(bot, guild, member, hunt_id):
    """Grant the hunt's team role to a roster member or hub admin, plus this
    server's Admin-tier roles to a hub admin. Returns the reply."""
    hunt = _hunt_here(guild, hunt_id)
    if hunt is None:
        return "This button isn't for a hunt on this server."
    if hunt.archived:
        return f"**{hunt.name}** is over, so there's nothing to join."
    team_role = guild.get_role(hunt.role_id)
    if team_role is None:
        return "This hunt's team role is missing here — ask an organiser."
    roster = find_roster_role(bot, hunt.roster_role_id) if hunt.roster_role_id else None
    if roster is None:
        return ("I can't find this hunt's hub role, so I can't check who it's for — "
                "ask an organiser.")
    hub = roster.guild
    on_hub = await hunt_archive.hub_member(hub, member.id)
    if on_hub is None:
        return (f"Join is for **{roster.name}** on **{hub.name}**, and you aren't on "
                f"{hub.name}.")
    hub_role_ids = {r.id for r in on_hub.roles}
    is_hub_admin = role_gate.member_holds_tier(hub_role_ids, hub, ADMIN_ROLES)
    if roster.id not in hub_role_ids and not is_hub_admin:
        return (f"Join is for **{roster.name}** on **{hub.name}** — ask an organiser "
                "there to give you that role.")

    wanted = [team_role] + (_admin_roles(guild) if is_hub_admin else [])
    held = {r.id for r in member.roles}
    to_add = [r for r in wanted if r.id not in held]
    if not to_add:
        return f"You already have **{team_role.name}**."
    try:
        await member.add_roles(*to_add, reason=f"role panel: join {hunt.name}")
    except discord.Forbidden:
        return _CANT_GRANT
    return f"Done — you now have {_names(to_add)}. The hunt channels should appear now."


async def leave(guild, member, hunt_id):
    """Remove the hunt's team role and this server's Admin-tier roles, whichever
    the member holds. Not gated: anyone may drop their own access."""
    hunt = _hunt_here(guild, hunt_id)
    if hunt is None:
        return "This button isn't for a hunt on this server."
    held = {r.id for r in member.roles}
    candidates = [guild.get_role(hunt.role_id)] + _admin_roles(guild)
    to_remove = [r for r in candidates if r is not None and r.id in held]
    if not to_remove:
        return "You don't have this hunt's role, so there's nothing to leave."
    try:
        await member.remove_roles(*to_remove, reason=f"role panel: leave {hunt.name}")
    except discord.Forbidden:
        return _CANT_GRANT
    return f"Done — removed {_names(to_remove)}. Click **Join** to come back."
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_role_panel.py tests/test_hunt_archive.py tests/test_archive_hunt_to_hub.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 6: Prove the gate is load-bearing**

Temporarily change `if roster.id not in hub_role_ids and not is_hub_admin:` to `if False:`. Run `tests/test_role_panel.py` and confirm `test_a_hub_member_without_the_roster_role_is_refused` fails. Restore the line by editing it back (not `git checkout`) and re-run to green.

- [ ] **Step 7: Commit**

```bash
git add bot/core/role_panel.py bot/core/hunt_archive.py tests/test_role_panel.py
git commit -m "feat(role-panel): join/leave logic, gated on the hub roster role"
```

---

### Task 2: The persistent buttons, registered at startup

**Files:**
- Modify: `bot/core/role_panel.py` (append)
- Modify: `bot/cogs/puzzles.py` (`setup()` at the bottom of the file, and the `from bot.core import …` line that already imports `server_setup`)
- Test: `tests/test_role_panel_buttons.py`

**Interfaces:**
- Consumes: `join`, `leave` from Task 1.
- Produces:
  - `CUSTOM_ID_TEMPLATE: str`;
  - `class HuntRoleButton(discord.ui.DynamicItem[discord.ui.Button])`, with `.action` (`"join"` | `"leave"`), `.hunt_id: int` and `.custom_id`;
  - `panel_view(hunt_id: int) -> discord.ui.View`: timeout None, children Join then Leave.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_role_panel_buttons.py`:

```python
"""The panel's buttons: the custom_id carries action + hunt, the class is
registered at cog load so posted panels survive a restart, and a click always
gets an ephemeral answer."""
import re
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core import role_panel
from bot.core.role_panel import HuntRoleButton, panel_view


def test_custom_ids_carry_the_action_and_the_hunt():
    assert HuntRoleButton("join", 42).custom_id == "hunt_role:join:42"
    assert HuntRoleButton("leave", 42).custom_id == "hunt_role:leave:42"


def test_the_template_matches_only_join_and_leave_with_a_numeric_hunt():
    t = re.compile(role_panel.CUSTOM_ID_TEMPLATE)
    assert t.fullmatch("hunt_role:join:42")
    assert t.fullmatch("hunt_role:leave:7")
    assert not t.fullmatch("hunt_role:delete:42")
    assert not t.fullmatch("hunt_role:join:abc")


async def test_from_custom_id_rebuilds_the_button_after_a_restart():
    match = re.fullmatch(role_panel.CUSTOM_ID_TEMPLATE, "hunt_role:leave:42")
    button = await HuntRoleButton.from_custom_id(MagicMock(), MagicMock(), match)
    assert (button.action, button.hunt_id) == ("leave", 42)


async def test_panel_view_is_persistent_with_join_then_leave():
    view = panel_view(42)
    assert view.timeout is None and view.is_persistent()
    assert [c.custom_id for c in view.children] == ["hunt_role:join:42", "hunt_role:leave:42"]
    assert [c.item.label for c in view.children] == ["Join", "Leave"]


def _interaction():
    i = MagicMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    return i


async def test_a_join_click_answers_privately_with_joins_reply():
    i = _interaction()
    with patch("bot.core.role_panel.join", new=AsyncMock(return_value="welcome")) as j, \
         patch("bot.core.role_panel.leave", new=AsyncMock()) as lv:
        await HuntRoleButton("join", 42).callback(i)
    i.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    j.assert_awaited_once_with(i.client, i.guild, i.user, 42)
    lv.assert_not_awaited()
    i.followup.send.assert_awaited_once_with("welcome", ephemeral=True)


async def test_a_leave_click_calls_leave():
    i = _interaction()
    with patch("bot.core.role_panel.join", new=AsyncMock()) as j, \
         patch("bot.core.role_panel.leave", new=AsyncMock(return_value="bye")) as lv:
        await HuntRoleButton("leave", 42).callback(i)
    lv.assert_awaited_once_with(i.guild, i.user, 42)
    j.assert_not_awaited()
    i.followup.send.assert_awaited_once_with("bye", ephemeral=True)


async def test_a_failure_still_answers_the_click():
    i = _interaction()
    with patch("bot.core.role_panel.join", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await HuntRoleButton("join", 42).callback(i)
    i.followup.send.assert_awaited_once()
    assert "went wrong" in i.followup.send.await_args.args[0]
    assert i.followup.send.await_args.kwargs == {"ephemeral": True}


async def test_cog_setup_registers_the_buttons_so_they_survive_a_restart():
    from bot.cogs import puzzles
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    with patch("bot.cogs.puzzles.Puzzles"):
        await puzzles.setup(bot)
    bot.add_dynamic_items.assert_called_once_with(HuntRoleButton)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_role_panel_buttons.py -q > /tmp/test.log 2>&1`
Expected: collection error, `ImportError: cannot import name 'HuntRoleButton'`.

- [ ] **Step 3: Append the buttons to `bot/core/role_panel.py`**

```python
# -- The buttons ---------------------------------------------------------------
# A DynamicItem, not a View registered per hunt: the custom_id carries the action
# and the hunt id, so one bot.add_dynamic_items(HuntRoleButton) at cog load makes
# every panel ever posted work after a restart, with no view state stored.

CUSTOM_ID_TEMPLATE = r"hunt_role:(?P<action>join|leave):(?P<hunt_id>[0-9]+)"


class HuntRoleButton(discord.ui.DynamicItem[discord.ui.Button], template=CUSTOM_ID_TEMPLATE):
    def __init__(self, action, hunt_id):
        joining = action == "join"
        super().__init__(discord.ui.Button(
            label="Join" if joining else "Leave",
            style=discord.ButtonStyle.success if joining else discord.ButtonStyle.secondary,
            custom_id=f"hunt_role:{action}:{hunt_id}",
        ))
        self.action = action
        self.hunt_id = hunt_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["hunt_id"]))

    async def callback(self, interaction):
        # Deferred first: the hub lookup can fetch a member, and an unanswered
        # click fails in Discord after three seconds.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if self.action == "join":
                text = await join(interaction.client, interaction.guild, interaction.user, self.hunt_id)
            else:
                text = await leave(interaction.guild, interaction.user, self.hunt_id)
        except Exception:
            logger.exception("role panel %s failed for hunt %s", self.action, self.hunt_id)
            text = "Something went wrong — ask an organiser."
        await interaction.followup.send(text, ephemeral=True)


def panel_view(hunt_id):
    """The Join / Leave buttons for *hunt_id*, to attach to a #lobby message."""
    view = discord.ui.View(timeout=None)
    view.add_item(HuntRoleButton("join", hunt_id))
    view.add_item(HuntRoleButton("leave", hunt_id))
    return view
```

- [ ] **Step 4: Register the buttons in the cog's `setup()`**

In `bot/cogs/puzzles.py`, extend the existing import line `from bot.core import category_layout, hunt_archive, media_gc, server_setup` to `from bot.core import category_layout, hunt_archive, media_gc, role_panel, server_setup`. Then change the module-level `setup` at the bottom of the file from:

```python
async def setup(bot):
    await bot.add_cog(Puzzles(bot))
```

to:

```python
async def setup(bot):
    # Every #lobby role panel ever posted keeps working across restarts.
    bot.add_dynamic_items(role_panel.HuntRoleButton)
    await bot.add_cog(Puzzles(bot))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_role_panel_buttons.py tests/test_role_panel.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add bot/core/role_panel.py bot/cogs/puzzles.py tests/test_role_panel_buttons.py
git commit -m "feat(role-panel): persistent Join/Leave buttons, registered at cog load"
```

---

### Task 3: `!setup_server` posts the panel as its welcome

**Files:**
- Modify: `bot/core/server_setup.py` (`_welcome_text` → public `welcome_text`, new wording; the `targets.lobby.send(...)` call in `run_setup`; import `role_panel`)
- Test: `tests/test_server_setup_run.py` (the `_hunt` helper gains an int `.id`; one test is extended; one is added)

**Interfaces:**
- Consumes: `role_panel.panel_view(hunt_id)` from Task 2.
- Produces: `server_setup.welcome_text(hunt, team: str) -> str`, which still ends with `WELCOME_MARK`. Task 4 uses it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_server_setup_run.py`, change the `_hunt` helper so the hunt has an int id. This is required: `HuntRoleButton` rejects a non-numeric id, see *Global Constraints*.

```python
def _hunt(role_id=71, roster_role_id=0):
    h = MagicMock(); h.id = 42; h.name = "Big Hunt"; h.role_id = role_id; h.roster_role_id = roster_role_id
    return h
```

In `test_a_fresh_run_does_every_step_then_locks`, directly after the existing two lines

```python
    targets.lobby.send.assert_awaited_once()
    assert server_setup.WELCOME_MARK in targets.lobby.send.await_args.args[0]
```

add:

```python
    view = targets.lobby.send.await_args.kwargs["view"]
    assert [c.custom_id for c in view.children] == ["hunt_role:join:42", "hunt_role:leave:42"]
```

and add this test at the end of the file:

```python
def test_the_welcome_points_at_join_for_the_team():
    text = server_setup.welcome_text(_hunt(), "Team Rocket")
    assert "**Join**" in text and "Team Rocket" in text and "Big Hunt" in text
    assert text.endswith(server_setup.WELCOME_MARK)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_server_setup_run.py -q > /tmp/test.log 2>&1`
Expected: `test_a_fresh_run_does_every_step_then_locks` fails with `KeyError: 'view'`, and `test_the_welcome_points_at_join_for_the_team` fails with `AttributeError: … has no attribute 'welcome_text'`.

- [ ] **Step 3: Implement**

In `bot/core/server_setup.py`:

1. Add `role_panel` to the imports: `from bot.core import role_panel` (after the existing `from bot.core.discord_channels import clean_category_name` line).
2. Replace `_welcome_text` with:

```python
def welcome_text(hunt, team):
    """The #lobby welcome; the Join / Leave panel is attached to this message."""
    return (f"Welcome to **{team}**'s server for **{hunt.name}**. Click **Join** to get "
            f"the **{team}** role and see the hunt channels — it's for people with "
            f"**{team}** on the hub, and hub admins also get `mod` here. **Leave** "
            f"gives the role back. {WELCOME_MARK}")
```

3. In `run_setup`, change `await targets.lobby.send(_welcome_text(hunt, team))` to:

```python
            await targets.lobby.send(welcome_text(hunt, team),
                                     view=role_panel.panel_view(hunt.id))
```

Then confirm nothing still uses the old name:

Run: `grep -rn "_welcome_text" bot/ tests/`
Expected: no output.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_server_setup_run.py tests/test_server_setup_preflight.py tests/test_setup_server_command.py tests/test_template_greeting.py -q > /tmp/test.log 2>&1`
Expected: all pass. A `run_setup` test that now fails with `report.error` containing "must match the template" has a hunt mock without an int `.id`. Give that mock `.id = 42`; do not weaken the assertion.

- [ ] **Step 5: Commit**

```bash
git add bot/core/server_setup.py tests/test_server_setup_run.py
git commit -m "feat(setup-server): the #lobby welcome carries the Join/Leave panel"
```

---

### Task 4: `!post_role_panel <hunt name>`

**Files:**
- Modify: `bot/cogs/puzzles.py`. Add the command directly after `setup_server` (the `@commands.command()` / `@commands.guild_only()` / `async def setup_server` block).
- Test: `tests/test_post_role_panel_command.py`

**Interfaces:**
- Consumes: `server_setup.welcome_text`, `server_setup.LOBBY_CHANNEL`, `role_panel.panel_view`, `HuntJsonDb.get_by_name_for_guild(name, guild_snowflake)`, `self.check_is_bot_channel(ctx)`.
- Produces: the bang command `!post_role_panel <hunt name>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_post_role_panel_command.py`:

```python
"""!post_role_panel: (re)posts the #lobby Join/Leave panel for a hunt made by
!setup_server, and refuses anything the panel couldn't serve."""
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.puzzles import Puzzles
from bot.core import server_setup
from tests.conftest import make_cog, make_ctx


def _hunt(roster_role_id=70, archived=False):
    h = MagicMock()
    h.id = 42
    h.name = "Big Hunt"
    h.role_id = 71
    h.roster_role_id = roster_role_id
    h.archived = archived
    return h


def _ctx(lobby=True, team_role=True):
    ctx = make_ctx()
    lob = MagicMock()
    lob.name = "lobby"
    lob.mention = "#lobby"
    lob.send = AsyncMock()
    cmds = MagicMock()
    cmds.name = "bot-commands"
    ctx.guild.text_channels = [cmds, lob] if lobby else [cmds]
    role = MagicMock()
    role.name = "Team Rocket"
    ctx.guild.get_role.side_effect = lambda i: role if (team_role and i == 71) else None
    return ctx, lob


async def _call(ctx, hunt, in_bot_channel=True):
    cog = make_cog()
    cog.check_is_bot_channel = AsyncMock(return_value=in_bot_channel)
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb:
        hdb.get_by_name_for_guild.return_value = hunt
        await Puzzles.post_role_panel.callback(cog, ctx, hunt_name="Big Hunt")
    return hdb


def _sent(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


async def test_posts_the_welcome_and_panel_in_lobby():
    ctx, lob = _ctx()
    hdb = await _call(ctx, _hunt())
    hdb.get_by_name_for_guild.assert_called_once_with("Big Hunt", ctx.guild.id)
    lob.send.assert_awaited_once()
    text = lob.send.await_args.args[0]
    view = lob.send.await_args.kwargs["view"]
    assert server_setup.WELCOME_MARK in text and "Team Rocket" in text
    assert [c.custom_id for c in view.children] == ["hunt_role:join:42", "hunt_role:leave:42"]
    assert "#lobby" in _sent(ctx)


async def test_outside_the_bot_channel_does_nothing():
    ctx, lob = _ctx()
    hdb = await _call(ctx, _hunt(), in_bot_channel=False)
    hdb.get_by_name_for_guild.assert_not_called()
    lob.send.assert_not_awaited()


async def test_an_unknown_hunt_refuses():
    ctx, lob = _ctx()
    await _call(ctx, None)
    lob.send.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_a_hunt_with_no_hub_role_refuses():
    ctx, lob = _ctx()
    await _call(ctx, _hunt(roster_role_id=0))
    lob.send.assert_not_awaited()
    assert "hub role" in _sent(ctx)


async def test_an_archived_hunt_refuses():
    ctx, lob = _ctx()
    await _call(ctx, _hunt(archived=True))
    lob.send.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_a_server_without_lobby_refuses():
    ctx, lob = _ctx(lobby=False)
    await _call(ctx, _hunt())
    lob.send.assert_not_awaited()
    assert "#lobby" in _sent(ctx)


async def test_a_missing_team_role_refuses():
    ctx, lob = _ctx(team_role=False)
    await _call(ctx, _hunt())
    lob.send.assert_not_awaited()
    assert ":x:" in _sent(ctx)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_post_role_panel_command.py -q > /tmp/test.log 2>&1`
Expected: every test fails with `AttributeError: type object 'Puzzles' has no attribute 'post_role_panel'`.

- [ ] **Step 3: Implement the command**

In `bot/cogs/puzzles.py`, directly after the end of the `setup_server` method, add:

```python
    @commands.command()
    @commands.guild_only()
    @is_admin()
    async def post_role_panel(self, ctx, *, hunt_name: str):
        """*Post the #lobby Join/Leave panel for a team server's hunt.*

        Usage: `!post_role_panel <hunt name>`
        Example: `!post_role_panel Big Hunt`
        !setup_server posts it for you; use this if it was deleted, or on a server
        set up before the panel existed. Join is for people with the hunt's hub
        role, and hub admins (who also get `mod`). Run in the bot channel.
        Admins/mods only."""
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
                           "!setup_server are), so Join would have nobody to check against.")
            return
        lobby = discord.utils.get(ctx.guild.text_channels, name=server_setup.LOBBY_CHANNEL)
        if lobby is None:
            await ctx.send(f":x: This server has no #{server_setup.LOBBY_CHANNEL} channel.")
            return
        team_role = ctx.guild.get_role(hunt.role_id)
        if team_role is None:
            await ctx.send(f":x: **{hunt.name}**'s team role is missing on this server.")
            return
        await lobby.send(server_setup.welcome_text(hunt, team_role.name),
                         view=role_panel.panel_view(hunt.id))
        await ctx.send(f":white_check_mark: Posted the Join/Leave panel in {lobby.mention}.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_post_role_panel_command.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_post_role_panel_command.py
git commit -m "feat(role-panel): !post_role_panel re-posts the #lobby panel"
```

---

### Task 5: Docs, spec write-back, full verification

**Files:**
- Modify: `docs/commands.md`
  - Admins/mods summary table, *Hunt lifecycle* row: add `` · `!post_role_panel` `` after `` `!setup_server` ``.
  - Detailed table: add a row after the `!setup_server` row.
  - The `!setup_server` row: replace "and posts a `#lobby` welcome" with "and posts the `#lobby` welcome with its Join/Leave panel".
- Modify: `docs/new-server-playbook.md`, "Team servers", *Per team, per hunt*, step 8.
- Modify: `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`: the status line and the *The `#lobby` role panel* section.
- Modify: `docs/user-guide.md`, only if it already describes how players get onto a team server. Check with `grep -n -i "team server\|lobby" docs/user-guide.md`, and add one sentence about Join if there is a matching passage. Otherwise leave it.

- [ ] **Step 1: `docs/commands.md`: the new row**

```markdown
| `!post_role_panel` | | `hunt name` | bot channel | admins/mods | Post the `#lobby` Join/Leave panel for a hunt made by `!setup_server` — `!setup_server` already posts one; use this if it was deleted or the server predates the panel. Join is for people holding the hunt's hub role (hub admins also get `mod`); Leave removes both. Old panels keep working. |
```

- [ ] **Step 2: Playbook step 8**

Replace step 8 of *Per team, per hunt* with:

```markdown
8. Players click **Join** in `#lobby`: it gives the team role to anyone holding the hub
   role, and hub admins also get `mod` (so they can run `!archive_hunt_to_hub` at hunt
   end). Nobody else can use it. **Leave** takes both away; someone given `mod` by hand
   who isn't a hub admin can't get it back through Join. Until invite DMs land, players
   still need an invite link to reach the server. `!post_role_panel <hunt name>`
   re-posts the panel if it goes missing.
```

- [ ] **Step 3: Spec write-back**

In the spec:
- Status line (line 4): append `; slice 3 (#lobby role panel) — plan 2026-10-30`.
- Directly under the heading `## The #lobby role panel`, insert:

```markdown
*Changed 2026-10-30 (plan 2026-10-30-lobby-role-panel.md):* Join is gated — the clicker must be on the hub and hold the hunt's roster role there, or be a hub admin (who also gets this server's pinned Admin-tier roles, i.e. `mod`). Until slice 4's single-use invites exist, players arrive by ordinary links, so server membership alone is not a safe gate. The buttons are a `DynamicItem` in `bot/core/role_panel.py` (custom_id `hunt_role:<join|leave>:<hunts.id>`, registered once with `bot.add_dynamic_items`), not a View in `bot/cogs/ui.py`; the panel is attached to the `#lobby` welcome; `!post_role_panel <hunt name>` re-posts it.
```

- [ ] **Step 4: Full verification**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass. Read the tail of `/tmp/test.log` (`tail -n 5`).

Run: `pipenv run ruff check bot/core/role_panel.py bot/core/server_setup.py bot/core/hunt_archive.py bot/cogs/puzzles.py tests/test_role_panel.py tests/test_role_panel_buttons.py tests/test_post_role_panel_command.py tests/test_server_setup_run.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add docs/commands.md docs/new-server-playbook.md docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md docs/user-guide.md
git commit -m "docs: the #lobby role panel and !post_role_panel"
```

(Leave `docs/user-guide.md` out of `git add` if Step 3 left it unchanged.)

---

## Manual acceptance (test deployment, after merge to staging)

No migration is needed. Restart the test bot on the merged code, then:

1. **Existing trial server:** in `#bot-commands`, run `!post_role_panel <hunt name>`. A welcome with **Join** / **Leave** appears in `#lobby`.
2. **Roster player:** on an account holding the hub role, click Join. A private reply arrives and the hunt channels appear. Click Leave and they disappear.
3. **Hub member without the role:** click Join. The private refusal names the role and the hub, and no role is given.
4. **Hub admin:** click Join. You get the team role **and** `mod`. `!show_auth_roles` in `#bot-commands` answers you.
5. **Restart persistence:** restart the bot, then click Join or Leave on the same old message. It still works (acceptance criterion 8).
6. **Fresh server:** make a new server from the template and run `!setup_server`. The `#lobby` welcome arrives with the buttons already on it.
7. **Audit:** Server Settings → Audit Log shows the role changes with `role panel: join <hunt>` as the reason.
