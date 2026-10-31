"""The reusable mod invite: one never-expiring, unlimited-use invite to a team
server's #lobby, posted only in the hub admin channel so hub mods can get in
without a DM each. The code is stored on the hunt so a repeat reuses it."""
import logging

import discord

from bot.core import hub_channel, role_panel, server_setup
from bot.core.team_invites import InviteUnavailable
from bot.store import HuntJsonDb

logger = logging.getLogger(__name__)


async def ensure(guild, hunt):
    """The hunt's stored invite if it is still live on *guild*, else a new #lobby
    invite (stored). Raises InviteUnavailable when none can be made."""
    code = HuntJsonDb.mod_invite_code(hunt.id)
    if code:
        # A stored code we can't check must not be replaced: the old invite would
        # be orphaned (delete only knows the latest code) and never revoked.
        try:
            invites = await guild.invites()
        except discord.HTTPException as exc:
            raise InviteUnavailable(f"I couldn't check this server's invites ({exc})") from exc
        for invite in invites:
            if invite.code == code:
                return invite
    lobby = discord.utils.get(guild.text_channels, name=server_setup.LOBBY_CHANNEL)
    if lobby is None:
        raise InviteUnavailable(f"this server has no #{server_setup.LOBBY_CHANNEL} channel")
    try:
        invite = await lobby.create_invite(max_uses=0, max_age=0, unique=True,
                                           reason=f"mod invite: {hunt.name}")
    except discord.HTTPException as exc:
        raise InviteUnavailable(f"I couldn't create an invite in #{lobby.name} ({exc})") from exc
    HuntJsonDb.set_mod_invite_code(hunt.id, invite.code)
    return invite


def invite_text(guild, team_role_name, hunt, url):
    return (f":key: Mod way into **{guild.name}** ({team_role_name}) for **{hunt.name}**: "
            f"{url} — click **Join** in #{server_setup.LOBBY_CHANNEL} when you arrive.")


async def post(bot, guild, hunt):
    """Post the mod invite for *hunt* (on team server *guild*) in the hub admin
    channel. Returns a short reply for the caller; the link is never in it."""
    roster_role = role_panel.find_roster_role(bot, hunt.roster_role_id)
    if roster_role is None:
        return f":x: I can't find **{hunt.name}**'s hub role, so there's no hub to post to."
    hub = roster_role.guild
    channel, reason = hub_channel.find(hub)
    if channel is None:
        return f":warning: Mod invite not posted: {reason}."
    try:
        invite = await ensure(guild, hunt)
    except InviteUnavailable as exc:
        return f":x: No mod invite: {exc}."
    team_role = guild.get_role(hunt.role_id)
    text = invite_text(guild, team_role.name if team_role else hunt.name, hunt, invite.url)
    if not await hub_channel.post(hub, text):
        return f":warning: Mod invite not posted: couldn't send to #{channel.name} on {hub.name}."
    return f":white_check_mark: Mod invite posted in #{channel.name} on {hub.name}."


async def delete(guild, hunt):
    """Delete the stored mod invite and clear its code. Never raises."""
    try:
        code = HuntJsonDb.mod_invite_code(hunt.id)
        if not code:
            return
        try:
            for invite in await guild.invites():
                if invite.code == code:
                    await invite.delete(reason=f"mod invite: {hunt.name} archived")
                    break
        except discord.HTTPException:
            logger.warning("could not delete mod invite %s on guild %s", code, guild.id)
        HuntJsonDb.set_mod_invite_code(hunt.id, None)
    except Exception:
        logger.exception("mod invite cleanup failed for hunt %s", hunt.id)
