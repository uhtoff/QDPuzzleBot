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
