"""Multi-team hunts: several teams' hunts on one hub guild sharing hub channels.

Each multi-team hunt owns two hub channels, found again by their stored ids and
made at the bottom of the hub's multi_team_category (or at top level): a no-spoiler channel every team (and hub admin) can chat in, and a
finishers channel only teams that have completed can see. Their topics are
readable text, because Discord shows the topic in a Spoiler Channel's warning;
channels made before that carry a ``puzzbot-multi-team:`` marker topic, which is
still recognised and replaced. Their overwrites are
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
from bot.core.discord_channels import clean_channel_name, resolve_category, with_room

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
    """The legacy topic of a no-spoiler channel made before readable topics."""
    return f"puzzbot-multi-team:{mt_id}:no-spoilers"


def finishers_marker(mt_id) -> str:
    """The legacy topic of a finishers channel made before readable topics."""
    return f"puzzbot-multi-team:{mt_id}:finishers"


def no_spoiler_topic(hunt_name) -> str:
    return f"Chat for every team playing {hunt_name}. No spoilers, please!"


def finishers_topic(hunt_name) -> str:
    return (f"Spoilers for {hunt_name}. Only teams that have finished "
            "can see this channel.")


def archive_marker(mt_id) -> str:
    return f"puzzbot-multi-team-archive:{mt_id}"


# Discord's IS_SPOILER_CHANNEL channel flag: members get a warning and opt in
# before they see the channel. discord.py 2.7 neither names it nor keeps a text
# channel's flags, so it is read and written raw.
SPOILER_CHANNEL_FLAG = 1 << 21


async def mark_spoiler_channel(channel) -> None:
    """Tick Discord's Spoiler Channel setting on *channel*, keeping its other
    flags. Never raises: the overwrites are what hide spoilers, this is only the
    warning on top."""
    try:
        http = channel._state.http
        raw = await http.get_channel(channel.id)
        flags = int(raw.get("flags", 0))
        if not flags & SPOILER_CHANNEL_FLAG:
            await http.edit_channel(channel.id, flags=flags | SPOILER_CHANNEL_FLAG,
                                    reason="Multi-team finishers channel holds spoilers")
    except Exception:
        logger.warning("could not mark channel %s as a spoiler channel",
                       getattr(channel, "id", "?"), exc_info=True)


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


def _category_with_room(hub, category_id):
    """The stored hub category, or None if unset, deleted or full."""
    return with_room(hub.get_channel(category_id) if category_id else None)


async def _ensure_one(hub, stored_id, marker, name, topic, overwrites, category=None,
                      spoiler=False):
    """The hunt's channel (see _find_live), its overwrites replaced by *overwrites*;
    created with *topic* if absent, at the bottom of *category* when given. An
    existing channel is never moved, so an admin's placement sticks, and its topic
    is only rewritten while it is still the legacy *marker*, so an admin's own
    topic sticks too. The overwrites are the channel's own, never synced from the
    category, so a category can't widen who sees finishers. With *spoiler*, the
    channel is marked a Spoiler Channel once: when created, or when its legacy
    topic is replaced; after that an admin who unticks it is left alone."""
    channel = _find_live(hub, stored_id, marker)
    if channel is None:
        where = {"category": category} if category is not None else {}
        channel = await hub.create_text_channel(name[:100], topic=topic,
                                                overwrites=overwrites, **where)
        if spoiler:
            await mark_spoiler_channel(channel)
        return channel
    changes = {"overwrites": overwrites}
    legacy = channel.topic == marker
    if legacy:
        changes["topic"] = topic
    await channel.edit(**changes)
    if spoiler and legacy:
        await mark_spoiler_channel(channel)
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
        category = _category_with_room(hub, mt.get("hub_category_id"))
        ns = await _ensure_one(hub, mt.get("no_spoiler_channel_id"), no_spoiler_marker(mt_id),
                               f"{slug}-no-spoilers", no_spoiler_topic(mt["name"]),
                               _overwrites(hub, team_ids + admin_ids, mt_id), category)
        category = _category_with_room(hub, mt.get("hub_category_id"))
        fin = await _ensure_one(hub, mt.get("finishers_channel_id"), finishers_marker(mt_id),
                                f"{slug}-finishers", finishers_topic(mt["name"]),
                                _overwrites(hub, done_ids, mt_id), category, spoiler=True)
        MultiTeamDb.set_channels(mt_id, ns.id, fin.id)
        return ns, fin


async def find_or_create(hub, hunt_name, category_name=""):
    """(multi-team id, created) for the running multi-team hunt named *hunt_name*
    on *hub* (case-insensitive), creating it if there is none. A new one's hub
    channels go in the category *category_name* (see resolve_category); joining a
    running one keeps where its channels already are."""
    async with _LOCK:
        row = MultiTeamDb.running_by_name(hub.id, hunt_name)
        if row is not None:
            return row["id"], False
        category = await resolve_category(hub, category_name)
        return MultiTeamDb.create(hub.id, hunt_name,
                                  category.id if category is not None else None), True


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
    """The channel with the stored id, else (for channels made before readable
    topics, whose id may never have been stored) the one whose topic is the
    legacy *marker*; None if neither exists."""
    channel = hub.get_channel(stored_id) if stored_id else None
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
