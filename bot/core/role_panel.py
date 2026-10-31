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

from bot.core import access_notes, hunt_archive, role_gate
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


def admin_roles(guild):
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

    wanted = [team_role] + (admin_roles(guild) if is_hub_admin else [])
    held = {r.id for r in member.roles}
    to_add = [r for r in wanted if r.id not in held]
    if not to_add:
        return f"You already have **{team_role.name}**."
    for r in to_add:
        access_notes.expect(guild.id, member.id, r.id, "button")
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
    candidates = [guild.get_role(hunt.role_id)] + admin_roles(guild)
    to_remove = [r for r in candidates if r is not None and r.id in held]
    if not to_remove:
        return "You don't have this hunt's role, so there's nothing to leave."
    for r in to_remove:
        access_notes.expect(guild.id, member.id, r.id, "button")
    try:
        await member.remove_roles(*to_remove, reason=f"role panel: leave {hunt.name}")
    except discord.Forbidden:
        return _CANT_GRANT
    return f"Done — removed {_names(to_remove)}. Click **Join** to come back."


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
