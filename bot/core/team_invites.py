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
