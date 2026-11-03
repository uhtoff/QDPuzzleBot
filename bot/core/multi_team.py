"""Multi-team hunts: several teams' hunts on one hub guild sharing hub channels.

Each multi-team hunt owns two uncategorised hub channels, found again by a topic
marker: a no-spoiler channel every team (and hub admin) can chat in, and a
finishers channel only teams that have completed can see. Their overwrites are
always rebuilt from the database and applied as a whole dict, so a stale role is
removed rather than left behind.
"""
import asyncio
import logging
from dataclasses import dataclass

import discord

from bot.store import MultiTeamDb
from bot.core import hub_channel, hunt_archive, role_gate, role_panel
from bot.core.constants import ADMIN_ROLES
from bot.core.discord_channels import clean_channel_name

logger = logging.getLogger(__name__)

# Serialises find_or_create so two simultaneous setups for the same hunt name
# cannot each create a multi-team row.
_LOCK = asyncio.Lock()
# One lock per multi-team hunt: ensure_channels awaits Discord between scanning
# and creating, so concurrent calls would each create a pair and race on edits.
_ENSURE_LOCKS: dict[int, asyncio.Lock] = {}


class MultiTeamRefused(Exception):
    """A multi-team action the bot won't do; the message is shown to the user as-is."""


def no_spoiler_marker(mt_id) -> str:
    return f"puzzbot-multi-team:{mt_id}:no-spoilers"


def finishers_marker(mt_id) -> str:
    return f"puzzbot-multi-team:{mt_id}:finishers"


def archive_marker(mt_id) -> str:
    return f"puzzbot-multi-team-archive:{mt_id}"


def _allow():
    return discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                       read_message_history=True)


def _overwrites(hub, role_ids, mt_id):
    """@everyone denied; the bot itself allowed (it may not be Administrator, and
    the deny would otherwise hide the channel from it); each of *role_ids* that
    resolves on the hub allowed."""
    out = {
        hub.default_role: discord.PermissionOverwrite(view_channel=False),
        hub.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_channels=True, manage_permissions=True),
    }
    for rid in role_ids:
        if not rid:
            continue
        role = hub.get_role(rid)
        if role is None:
            logger.warning("multi-team %s: role %s not found on hub %s; skipped",
                           mt_id, rid, hub.id)
            continue
        out[role] = _allow()
    return out


async def _ensure_one(hub, stored_id, marker, name, overwrites):
    """The channel tagged *marker* (stored id first, then a topic scan), its
    overwrites replaced by *overwrites*; created uncategorised if absent."""
    channel = hub.get_channel(stored_id) if stored_id else None
    if channel is not None and channel.topic != marker:
        channel = None
    if channel is None:
        channel = next((c for c in hub.text_channels if c.topic == marker), None)
    if channel is None:
        return await hub.create_text_channel(name[:100], topic=marker, overwrites=overwrites)
    await channel.edit(overwrites=overwrites)
    return channel


async def ensure_channels(hub, mt_id):
    """(no-spoiler channel, finishers channel) for multi-team hunt *mt_id*, created
    if missing and with overwrites set from the current members. Idempotent."""
    async with _ENSURE_LOCKS.setdefault(mt_id, asyncio.Lock()):
        mt = MultiTeamDb.get(mt_id)
        if mt is None:
            raise LookupError(f"no multi-team hunt {mt_id}")
        if mt.get("archived_time") is not None:
            raise MultiTeamRefused(f"The multi-team hunt **{mt['name']}** is already archived.")
        members = MultiTeamDb.members(mt_id)
        admin_ids = sorted(role_gate.tier_role_ids(hub, ADMIN_ROLES))
        team_ids = [m["roster_role_id"] for m in members]
        done_ids = [m["roster_role_id"] for m in members if m["completed_time"] is not None]

        slug = clean_channel_name(mt["name"])
        ns = await _ensure_one(hub, mt.get("no_spoiler_channel_id"), no_spoiler_marker(mt_id),
                               f"{slug}-no-spoilers",
                               _overwrites(hub, team_ids + admin_ids, mt_id))
        fin = await _ensure_one(hub, mt.get("finishers_channel_id"), finishers_marker(mt_id),
                                f"{slug}-finishers", _overwrites(hub, done_ids, mt_id))
        MultiTeamDb.set_channels(mt_id, ns.id, fin.id)
        return ns, fin


async def find_or_create(hub, hunt_name):
    """(multi-team id, created) for the running multi-team hunt named *hunt_name*
    on *hub* (case-insensitive), creating it if there is none."""
    async with _LOCK:
        row = MultiTeamDb.running_by_name(hub.id, hunt_name)
        if row is not None:
            return row["id"], False
        return MultiTeamDb.create(hub.id, hunt_name), True


@dataclass
class CompleteOutcome:
    changed: bool
    message: str


async def set_complete(bot, hunt, complete: bool) -> CompleteOutcome:
    """Mark *hunt*'s team finished (or not) in its multi-team hunt, then rebuild
    the hub channels so the finishers overwrite follows, and announce a finish."""
    link = MultiTeamDb.for_hunt(hunt.id)
    if link is None:
        raise MultiTeamRefused(f"**{hunt.name}** isn't part of a multi-team hunt.")
    if not hunt.roster_role_id:
        raise MultiTeamRefused(
            f"**{hunt.name}** has no hub role, so it can't be marked complete.")
    role = role_panel.find_roster_role(bot, hunt.roster_role_id)
    if role is None:
        raise MultiTeamRefused(
            f"I can't find **{hunt.name}**'s hub role — is the bot still on the hub?")
    if link["archived_time"] is not None:
        raise MultiTeamRefused(f"The multi-team hunt **{link['name']}** is already archived.")
    if not complete and hunt.archived:
        raise MultiTeamRefused(f"**{hunt.name}** is archived, so it stays complete.")

    team = role.name
    if not MultiTeamDb.set_completed(hunt.id, complete):
        # No change, but still rebuild the channels: a retry after an earlier hub
        # failure must repair the overwrites. Nothing is announced.
        await ensure_channels(role.guild, link["multi_team_id"])
        return CompleteOutcome(
            False, f"**{team}** is already complete." if complete
            else f"**{team}** isn't marked complete.")
    _, finishers = await ensure_channels(role.guild, link["multi_team_id"])
    if not complete:
        return CompleteOutcome(True, f":white_check_mark: **{team}** marked not complete.")
    await finishers.send(f"🏁 **{team}** has finished!",
                         allowed_mentions=discord.AllowedMentions.none())
    return CompleteOutcome(
        True, f":white_check_mark: **{team}** marked complete — "
              f"they can now see #{finishers.name}.")


def _find_live(hub, stored_id, marker):
    """The live channel tagged *marker* (stored id first, then a topic scan), or None."""
    channel = hub.get_channel(stored_id) if stored_id else None
    if channel is not None and channel.topic != marker:
        channel = None
    if channel is None:
        channel = next((c for c in hub.text_channels if c.topic == marker), None)
    return channel


def ensure_no_teams_playing(mt_id) -> None:
    """Raise MultiTeamRefused if any member's hunt is not yet archived. Archive now
    (command and route) calls this first; the automatic path only runs once every
    team has archived, so archive_multi_team itself does not."""
    mt = MultiTeamDb.get(mt_id)
    if mt is None:
        raise LookupError(f"no multi-team hunt {mt_id}")
    live = sum(1 for m in MultiTeamDb.members(mt_id) if not m["archived"])
    if live:
        raise MultiTeamRefused(
            f"{live} team(s) are still playing **{mt['name']}** \u2014 "
            "archive their servers to the hub first.")


async def archive_multi_team(bot, hub, mt_id) -> str:
    """Condense the hunt's two hub channels into threads on one archive channel and
    delete them, then mark the multi-team hunt archived. Returns the summary line;
    never raises (a failure is logged and returned, and Archive now retries it)."""
    name = f"multi-team hunt {mt_id}"
    try:
        async with _ENSURE_LOCKS.setdefault(mt_id, asyncio.Lock()):
            mt = MultiTeamDb.get(mt_id)
            if mt is None:
                raise LookupError(f"no multi-team hunt {mt_id}")
            name = mt["name"]
            if mt.get("archived_time") is not None:
                return f"The multi-team hunt **{name}** is already archived."
            sources = [
                (_find_live(hub, mt.get("no_spoiler_channel_id"), no_spoiler_marker(mt_id)),
                 "No spoilers"),
                (_find_live(hub, mt.get("finishers_channel_id"), finishers_marker(mt_id)),
                 "Finishers"),
            ]
            sources = [(ch, thread) for ch, thread in sources if ch is not None]
            if not sources:
                MultiTeamDb.mark_archived(mt_id)
                return (f"Nothing left to archive for the multi-team hunt **{name}**; "
                        "marked it archived.")
            dest_name = f"{mt['created_at']:%Y-%m} {name} - all teams"
            channels = messages = 0
            dest = await hunt_archive.ensure_tagged_channel(
                hub, archive_marker(mt_id), dest_name)
            webhook = await hunt_archive.ensure_webhook(dest)
            for source, thread in sources:
                result = await hunt_archive.archive_and_delete_channel(
                    bot, dest, webhook, source, thread_name=thread)
                channels += 1
                messages += result.messages_moved
            MultiTeamDb.mark_archived(mt_id)
            return (f":white_check_mark: Archived the multi-team hunt **{name}** into "
                    f"**{dest.name}** \u2014 {channels} channel(s), {messages} message(s) moved.")
    except Exception:
        logger.exception("archive_multi_team(%s) failed", mt_id)
        return (f":x: Archiving the multi-team hunt **{name}** failed \u2014 the error has "
                "been logged. Use Archive now to retry.")


async def on_team_archived(bot, hunt) -> None:
    """A team's hunt has just been archived to the hub: mark it complete and, if it
    was the last team standing, archive the multi-team hunt's channels. Nothing
    propagates -- the team's own archive has already succeeded."""
    try:
        link = MultiTeamDb.for_hunt(hunt.id)
        if link is None or link["archived_time"] is not None:
            return
        try:
            await set_complete(bot, hunt, True)
        except MultiTeamRefused as exc:
            logger.info("on_team_archived: hunt %s not marked complete: %s", hunt.id, exc)
        except Exception:
            logger.exception("on_team_archived: marking hunt %s complete failed", hunt.id)
        mt_id = link["multi_team_id"]
        if not all(m["archived"] for m in MultiTeamDb.members(mt_id)):
            return
        role = (role_panel.find_roster_role(bot, hunt.roster_role_id)
                if hunt.roster_role_id else None)
        hub = role.guild if role is not None else bot.get_guild(link["hub_guild_id"])
        if hub is None:
            logger.warning("on_team_archived: hub %s unavailable; multi-team hunt %s "
                           "left for Archive now", link["hub_guild_id"], mt_id)
            return
        await hub_channel.post(hub, await archive_multi_team(bot, hub, mt_id),
                               allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.exception("on_team_archived failed for hunt %s", getattr(hunt, "id", None))
