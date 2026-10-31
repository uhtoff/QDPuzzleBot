"""Slice 5: turn team-server member events into durable `hunt_access_log` rows, and
post only what mods need to see (hand changes, hub-driven removals, every
elevate/demote that wasn't a button press) to the hub admin channel.

Whether a change was the bot's own comes from `access_notes`; anything without a
note is a hand change, and the actor is looked up in the audit log."""
import asyncio
import datetime
import logging

import discord

from bot.core import access_notes, hub_channel, role_panel
from bot.store import HuntAccessLogDb, HuntJsonDb

logger = logging.getLogger(__name__)

ACTIONS = ("grant", "revoke", "elevate", "demote", "arrive", "depart")
VIAS = ("button", "arrival", "hub_role_lost", "hub_admin_lost", "hand", "self")

ALWAYS_POSTED_VIAS = ("hand", "hub_role_lost", "hub_admin_lost")
AUDIT_WINDOW_SECONDS = 30
AUDIT_RETRY_SECONDS = 2


def _posted(action, via):
    """True when this change goes to the hub admin channel."""
    if via in ALWAYS_POSTED_VIAS:
        return True
    return action in ("elevate", "demote") and via != "button"


def _recent(entry):
    created = getattr(entry, "created_at", None)
    if created is None:
        return True
    now = datetime.datetime.now(datetime.timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=datetime.timezone.utc)
    return (now - created).total_seconds() <= AUDIT_WINDOW_SECONDS


def _entry_has_role(entry, role_id, added):
    side = entry.after if added else entry.before
    return role_id in [r.id for r in (getattr(side, "roles", None) or [])]


async def _find_entry(guild, user_id, action, matches=lambda entry: True):
    """The recent audit entry for `action` on `user_id`, or None. One retry, since
    the audit entry can lag the gateway event. Never raises."""
    for attempt in range(2):
        try:
            async for entry in guild.audit_logs(action=action, limit=10):
                target = getattr(entry, "target", None)
                if getattr(target, "id", None) != user_id or not _recent(entry):
                    continue
                if matches(entry):
                    return entry
        except (discord.Forbidden, discord.HTTPException):
            return None
        if attempt == 0:
            await asyncio.sleep(AUDIT_RETRY_SECONDS)
    return None


async def _find_actor(guild, user_id, action, matches=lambda entry: True):
    """The id of whoever did `action` to `user_id` recently, or None."""
    entry = await _find_entry(guild, user_id, action, matches)
    return entry.user.id if entry is not None and entry.user is not None else None


async def _hand_actor(guild, member, role_id, added):
    return await _find_actor(
        guild, member.id, discord.AuditLogAction.member_role_update,
        lambda e: _entry_has_role(e, role_id, added))


def _hub_of(bot, hunt):
    role = role_panel.find_roster_role(bot, hunt.roster_role_id)
    return role.guild if role is not None else None


def _watched(guild, hunt):
    """(role, is_mod) for the hunt's team role and the guild's admin roles."""
    out = []
    team = guild.get_role(hunt.role_id)
    if team is not None:
        out.append((team, False))
    out.extend((r, True) for r in role_panel.admin_roles(guild))
    return out


def _action(is_mod, added):
    return ("elevate" if added else "demote") if is_mod else ("grant" if added else "revoke")


def _verb(action):
    return {"grant": "was given", "revoke": "lost", "elevate": "was made", "demote": "lost"}[action]


def _line(member, guild, role, action, via, actor_id):
    who = f"**{member.display_name}**"
    what = f"{_verb(action)} **{role.name}**"
    text = f":information_source: {who} {what} on **{guild.name}**"
    if via == "hand":
        text += f" (by hand, by <@{actor_id}>)" if actor_id else " (by hand, by someone)"
    elif via == "hub_role_lost":
        text += " (they lost the hub role)"
    elif via == "hub_admin_lost":
        text += " (they lost hub admin)"
    return text


async def on_roles_changed(bot, before, after):
    member = after
    if getattr(member, "bot", False):
        return
    guild = member.guild
    hunt = HuntJsonDb.live_team_hunt_for_guild(guild.id)
    if hunt is None:
        return
    before_ids = {r.id for r in before.roles}
    after_ids = {r.id for r in after.roles}
    hub = None
    for role, is_mod in _watched(guild, hunt):
        added = role.id in after_ids and role.id not in before_ids
        removed = role.id in before_ids and role.id not in after_ids
        if not (added or removed):
            continue
        action = _action(is_mod, added)
        via = access_notes.take(guild.id, member.id, role.id)
        actor_id = None
        if via is None:
            via = "hand"
            actor_id = await _hand_actor(guild, member, role.id, added)
        HuntAccessLogDb.record(hunt.id, member.id, member.display_name, action, via,
                               actor_id, role.name)
        if _posted(action, via):
            if hub is None:
                hub = _hub_of(bot, hunt)
            await hub_channel.post(hub, _line(member, guild, role, action, via, actor_id))


async def on_arrived(bot, member):
    if getattr(member, "bot", False):
        return
    hunt = HuntJsonDb.live_team_hunt_for_guild(member.guild.id)
    if hunt is None:
        return
    HuntAccessLogDb.record(hunt.id, member.id, member.display_name, "arrive", "arrival")


async def on_departed(bot, member):
    if getattr(member, "bot", False):
        return
    guild = member.guild
    hunt = HuntJsonDb.live_team_hunt_for_guild(guild.id)
    if hunt is None:
        return
    via = access_notes.take(guild.id, member.id, "kick")
    actor_id = None
    if via is None:
        entry = await _find_entry(guild, member.id, discord.AuditLogAction.kick)
        if entry is None:
            entry = await _find_entry(guild, member.id, discord.AuditLogAction.ban)
        if entry is not None:
            via = "hand"
            actor_id = entry.user.id if entry.user is not None else None
        else:
            via = "self"
    held = {r.id for r in member.roles}
    rows = [("depart", None)]
    for role, is_mod in _watched(guild, hunt):
        if role.id in held:
            rows.append((_action(is_mod, False), role.name))
    for action, detail in rows:
        HuntAccessLogDb.record(hunt.id, member.id, member.display_name, action, via,
                               actor_id, detail)
    if any(_posted(a, via) for a, _ in rows):
        text = f":door: **{member.display_name}** left **{guild.name}**"
        if via == "hand":
            text += f" (removed by <@{actor_id}>)"
        elif via == "self":
            text += " (left on their own)"
        elif via == "hub_role_lost":
            text += " (they lost the hub role)"
        elif via == "hub_admin_lost":
            text += " (they lost hub admin)"
        lost = [d for a, d in rows if d]
        if lost:
            text += f"; lost {', '.join(f'**{d}**' for d in lost)}"
        await hub_channel.post(_hub_of(bot, hunt), text)
