"""!setup_server: turn a server freshly cloned from the guild template into a
team server for one hunt (ctx-free; the command lives in bot/cogs/puzzles.py).

preflight() checks everything up front and raises SetupRefused before anything
is touched. run_setup() then does the work, each step check-then-create, and
stamps the run-once lock (guilds.setup_complete_time) only as its last step:
a run that stops partway is finished by re-running the same command, and a
completed one can never run again.

Spec: docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

import discord

from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLE_NAMES
from bot.core.discord_channels import clean_category_name
from bot.core import hunt_archive, multi_team as multi_team_core, role_gate, role_panel
from bot.store import GuildSettingsDb, HuntJsonDb, MultiTeamDb
from bot.store.hunt_data import HuntData

logger = logging.getLogger(__name__)

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


def _template_parts(guild):
    """The template's `mod` roles (all of them — preflight wants exactly one),
    #lobby and #bot-commands, each None when missing."""
    mods = [r for r in guild.roles if r.name == TEMPLATE_MOD_ROLE]
    lobby = discord.utils.get(guild.text_channels, name=LOBBY_CHANNEL)
    bot_channel = discord.utils.get(guild.text_channels, name=BOT_CHANNEL)
    return mods, lobby, bot_channel


def looks_like_template(guild) -> bool:
    """True if *guild* has the template's shape, by the same checks preflight
    refuses on: @everyone can't see channels, one `mod` role, #lobby and
    #bot-commands. Picks the join greeting; it decides nothing else."""
    if guild.default_role.permissions.view_channel:
        return False
    mods, lobby, bot_channel = _template_parts(guild)
    return len(mods) == 1 and lobby is not None and bot_channel is not None


def already_set_up_text(settings) -> str:
    return (f"This server was already set up ({settings.setup_complete_time:%Y-%m-%d}); "
            "!setup_server only runs once per server.")


def template_problems(guild, settings) -> list:
    """Every hub-free reason this server can't be set up as it stands, in the
    order preflight raises them (empty list = good). Wording is what the
    command shows; the dashboard lists them all."""
    problems = []
    if settings.setup_complete_time is not None:
        problems.append(already_set_up_text(settings))
    me = guild.me
    if not me.guild_permissions.administrator:
        problems.append("the bot needs Administrator here — re-invite it with permissions=8")
    if guild.default_role.permissions.view_channel:
        problems.append("@everyone can see channels here, so this server wasn't made "
                        "from the template — clone the template and start again")
    mods, lobby, bot_channel = _template_parts(guild)
    if len(mods) != 1:
        problems.append(f"expected exactly one `{TEMPLATE_MOD_ROLE}` role, found {len(mods)}")
    elif mods[0].position >= me.top_role.position:
        problems.append(f"drag the bot's role above `{TEMPLATE_MOD_ROLE}` in "
                        "Server Settings → Roles, then re-run")
    for name, channel in ((LOBBY_CHANNEL, lobby), (BOT_CHANNEL, bot_channel)):
        if channel is None:
            problems.append(f"the template's #{name} channel is missing")
    return problems


def preflight(guild, settings, hub, roster_role_id, hunt_name) -> SetupTargets:
    """Everything !setup_server needs, or SetupRefused naming the fix."""
    me = guild.me
    problems = template_problems(guild, settings)
    if problems:
        raise SetupRefused(problems[0])
    mods, lobby, bot_channel = _template_parts(guild)
    mod_role = mods[0]

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
    if hunt is not None and hunt.roster_role_id and hunt.roster_role_id != roster_role_id:
        raise SetupRefused(f"this server's hunt is already tied to hub role {hunt.roster_role_id} "
                           "— re-run with that id")
    if hunt is None:
        leftover = discord.utils.get(guild.categories, name=clean_category_name(hunt_name))
        if leftover is not None:
            raise SetupRefused(f"a `{leftover.name}` category is left from an earlier run "
                               "that saved no hunt — delete it and its channels, then re-run")
    return SetupTargets(mod_role, lobby, bot_channel, roster_role, hunt)


# Ends the lobby welcome; a re-run that finds a bot message carrying it skips the post.
WELCOME_MARK = "— puzzbot team server"


@dataclass
class SetupReport:
    steps: list = field(default_factory=list)   # one line per step done, in order
    hunt: Optional[HuntData] = None
    hunt_created: bool = False
    error: str = ""                              # set when the run stopped; lock not written


def welcome_text(hunt, team):
    """The #lobby welcome; the Join / Leave panel is attached to this message."""
    return (f"Welcome to **{team}**'s server for **{hunt.name}**. Click **Join** to get "
            f"the **{team}** role and see the hunt channels — it's for people with "
            f"**{team}** on the hub, and hub admins also get `mod` here. **Leave** "
            f"removes it, and `mod` too. {WELCOME_MARK}")


async def _has_welcome(lobby, bot_id):
    async for message in lobby.history(limit=50):
        if message.author.id == bot_id and WELCOME_MARK in message.content:
            return True
    return False


def parse_setup_tail(text: str) -> tuple[str, str, bool]:
    """Split `<hunt name>[, files][, multi-team]` (clauses in either order) into
    (hunt_name, sheet_mode, multi_team). Clauses are peeled off the end while they
    are exactly `files` or `multi-team`; a name with an inner comma stays whole."""
    sheet_mode, multi_team = "tabs", False
    name = text.rstrip()
    while "," in name:
        head, _, tail = name.rpartition(",")
        clause = tail.strip().lower()
        if clause == "files":
            sheet_mode = "files"
        elif clause == "multi-team":
            multi_team = True
        else:
            break
        name = head.rstrip()
    return name, sheet_mode, multi_team


async def run_setup(service, guild, invoker, targets, settings, hub_row, hunt_name, hunt_url,
                    *, start_timestamp=0, username="", password="", create_solved_sheet=False,
                    sheet_mode="tabs", hub=None, multi_team=False):
    """Do the setup steps, then stamp the run-once lock. Never raises: a failure
    lands in report.error, with the lock unwritten so a re-run finishes the job."""
    report = SetupReport()
    try:
        settings.guild_name = guild.name
        settings.website_url = hub_row.website_url
        settings.drive_parent_id = hub_row.drive_parent_id
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
                guild, name=hunt_name, url=hunt_url, start_timestamp=start_timestamp,
                username=username, password=password,
                create_solved_sheet=create_solved_sheet, sheet_mode=sheet_mode,
                role_name=team, guild_id=settings.id, creator=invoker,
                confirm_existing_role=True)
            report.hunt_created = True
            report.steps.append(f"Created hunt **{hunt.name}**, gated to **{team}**.")
        else:
            report.steps.append(f"Hunt **{hunt.name}** was already created; reusing it.")
        report.hunt = hunt
        if not hunt.role_id:
            raise RuntimeError(
                f"the hunt isn't gated — give yourself `{targets.mod_role.name}`, run "
                f"`!set_hunt_role {hunt.name}, {team}` in #{targets.bot_channel.name}, "
                "then re-run !setup_server")

        if hunt.roster_role_id != targets.roster_role.id:
            hunt.roster_role_id = targets.roster_role.id
            HuntJsonDb.commit(hunt)
        report.steps.append(f"Hub role **{team}** recorded as the hunt's roster.")

        existing_mt = MultiTeamDb.for_hunt(hunt.id)
        if existing_mt is not None:
            report.steps.append(f"Already part of multi-team hunt **{existing_mt['name']}**.")
            if existing_mt.get("archived_time") is None:
                await multi_team_core.ensure_channels(hub, existing_mt["multi_team_id"])
        elif multi_team:
            # A new one's channels go in the hub's multi_team_category setting.
            mt_id, created = await multi_team_core.find_or_create(
                hub, hunt.name, hub_row.multi_team_category)
            MultiTeamDb.link(hunt.id, mt_id)
            ns, fin = await multi_team_core.ensure_channels(hub, mt_id)
            mt_row = MultiTeamDb.get(mt_id)
            mt_name = mt_row["name"] if mt_row else hunt.name
            if created:
                report.steps.append(f"Started multi-team hunt **{mt_name}** on the hub "
                                    f"(#{ns.name} and #{fin.name}).")
            else:
                report.steps.append(f"Joined multi-team hunt **{mt_name}** — now "
                                    f"{sum(1 for m in MultiTeamDb.members(mt_id) if not m["archived"])} teams.")

        if await _has_welcome(targets.lobby, guild.me.id):
            report.steps.append(f"#{targets.lobby.name} welcome already posted.")
        else:
            await targets.lobby.send(welcome_text(hunt, team),
                                     view=role_panel.panel_view(hunt.id))
            report.steps.append(f"Posted the welcome in #{targets.lobby.name}.")

        GuildSettingsDb.mark_setup_complete(settings)
    except Exception as exc:
        logger.exception("setup_server stopped on guild %s", guild.id)
        report.error = str(exc) or type(exc).__name__
    return report


_RUNNING: set = set()   # guild ids with a setup in flight


@dataclass
class SetupOutcome:
    lines: list = field(default_factory=list)   # exactly what !setup_server sends
    ok: bool = False
    hunt: Optional[HuntData] = None
    error: str = ""


def _refused(text):
    return SetupOutcome(lines=[f":x: {text}"], ok=False, error=text)


async def perform_setup(bot, service, guild, invoker, hub_id, roster_role_id, hunt_name, hunt_url,
                        *, start_timestamp=0, username="", password="", create_solved_sheet=False,
                        sheet_mode="tabs", on_start=None, invites_by_dashboard=False, via_dashboard=False,
                        multi_team=False):
    """The whole !setup_server flow after argument parsing, shared with the
    dashboard. *invoker* is a Member of *guild*. *on_start*, if given, is
    awaited with the team role name once the checks pass and before anything is
    made (the command uses it for its "Setting up…" line). *invites_by_dashboard*
    and *via_dashboard* only reword the closing lines for a dashboard run. A second
    concurrent run on the same guild is refused."""
    if guild.id in _RUNNING:
        return _refused("Setup is already running on this server — wait for it to finish.")
    _RUNNING.add(guild.id)   # no await between check and add
    try:
        return await _perform_setup(
            bot, service, guild, invoker, hub_id, roster_role_id, hunt_name, hunt_url,
            start_timestamp=start_timestamp, username=username, password=password,
            create_solved_sheet=create_solved_sheet, sheet_mode=sheet_mode,
            on_start=on_start,
            invites_by_dashboard=invites_by_dashboard, via_dashboard=via_dashboard,
            multi_team=multi_team)
    finally:
        _RUNNING.discard(guild.id)


async def _perform_setup(bot, service, guild, invoker, hub_id, roster_role_id, hunt_name, hunt_url,
                         *, start_timestamp, username, password, create_solved_sheet,
                         sheet_mode, on_start, invites_by_dashboard, via_dashboard, multi_team):
    from bot.core import mod_invite       # mod_invite imports this module
    from bot.core.notifier import DiscordNotifier

    settings = GuildSettingsDb.get(guild.id)
    if settings is None:
        return _refused("This server has no settings row yet — remove the bot and re-invite it.")
    if settings.setup_complete_time is not None:
        return _refused(already_set_up_text(settings))
    if invoker.id != guild.owner_id and not role_gate.member_holds_tier(
            {r.id for r in invoker.roles}, guild, ADMIN_ROLES):
        return _refused("Only this server's owner (or an admin here) can run !setup_server.")
    try:
        hub, hub_row = await hunt_archive.resolve_hub(bot, hub_id, guild, invoker)
        targets = preflight(guild, settings, hub, roster_role_id, hunt_name)
    except (hunt_archive.HubUnavailable, SetupRefused) as e:
        return _refused(str(e))
    if on_start is not None:
        await on_start(targets.roster_role.name)
    report = await run_setup(
        service, guild, invoker, targets, settings, hub_row, hunt_name, hunt_url,
        start_timestamp=start_timestamp, username=username, password=password,
        create_solved_sheet=create_solved_sheet, sheet_mode=sheet_mode,
        hub=hub, multi_team=multi_team)
    if report.hunt_created and report.hunt is not None:
        try:
            await DiscordNotifier(bot).welcome(
                guild, report.hunt, channel=bot.get_channel(report.hunt.channel_id))
        except discord.HTTPException:
            logger.exception("setup_server: could not post the hunt welcome")
    lines = [f"• {s}" for s in report.steps]
    if report.error:
        lines.append(f":x: Setup stopped: {report.error}")
        if via_dashboard:
            lines.append("Fix that, then press Set up again; finished steps are skipped.")
        else:
            lines.append("Fix that, then re-run the same !setup_server command; finished steps are skipped.")
    else:
        lines.append(":white_check_mark: Setup complete; !setup_server is now locked on this server.")
        if report.hunt is not None:
            invite_reply, posted_in = await mod_invite.post(bot, guild, report.hunt)
            if posted_in is not None:
                lines.append(f"A mod invite for hub admins is in the hub's #{posted_in}.")
            else:
                lines.append(invite_reply)
        if invites_by_dashboard:
            lines.append("Next: click **Join** in #lobby (as a hub admin you get `mod` too) and "
                         "check the server; the invites report is below. Transfer server "
                         "ownership to a team member if wanted.")
        else:
            lines.append(f"Next: click **Join** in #lobby (as a hub admin you get `mod` too), check "
                         f"the server, then run `!send_invites {hunt_name}` to DM everyone with "
                         "the hub role. Transfer server ownership to a team member if wanted.")
    return SetupOutcome(lines=lines, ok=not report.error, hunt=report.hunt, error=report.error)
