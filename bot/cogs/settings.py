"""
Settings Cog shipped with cookiecutter-discordpy-postgres.

The settings here are unrelated to puzzle_settings.py
"""

import logging

import discord
from discord.ext import commands

from bot.core import server_setup
from bot.utils import config
from bot.store import GuildSettings, GuildSettingsDb

logger = logging.getLogger(__name__)

# Posted once, when the bot joins a server. A brand-new guild is UNPINNED by
# definition — admin_role_ids is NULL on the row we have just created — so it
# sits in the name-matching state where anyone holding Discord's Manage Roles
# can create a role called `admin`, self-assign it and hold full bot and
# dashboard admin. Nothing else surfaces that: the playbook is a document and
# !show_auth_roles only answers if you already thought to ask. This is the one
# moment whoever added the bot is definitely looking.
SETUP_MESSAGE = """\
:wave: **Thanks for adding me!** Two setup steps are manual — nothing I do performs \
them, and neither fails loudly if you skip it.

**1. Pin the roles that grant admin.** Right now I fall back to matching role \
*names* — `Moderator`, `mod`, `admin` — which means anyone here who has Discord's \
**Manage Roles** permission can create a role with one of those names, give it to \
themselves, and hold full bot and dashboard admin (including deleting a hunt). Fix it \
with:
```
!set_admin_roles @Your Admin Role
```
Then `!show_auth_roles` to confirm. Pinning the organiser tier \
(`!set_organiser_roles`) is optional — leaving it unpinned keeps organiser actions \
open to everyone here, which is fine for a single trusted team.

**2. After `!hunt`, set the team role's permission overwrite** on the hunt's first \
category, by hand in Discord. Every later category is cloned from that one, so the \
whole hunt inherits it — and if you set nothing, the hunt is ungated in Discord no \
matter what the dashboard says.

Both are also available on the dashboard under **Server settings**. \
`!help` lists everything else."""


# The join greeting for a server cloned from the team-server template, where
# !setup_server does both of SETUP_MESSAGE's manual steps. Posted in
# #bot-commands: only the owner and `mod` see it, and players never do.
_TEMPLATE_SETUP_STEPS = f"""\
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


def template_setup_message(settings_id=None) -> str:
    """The template-clone greeting. When the dashboard URL is configured and we
    know the guilds row id, lead with a link to the dashboard setup page and
    keep the command form as the alternative."""
    base = (config.web_frontend_url or "").rstrip("/")
    intro = (":wave: **Thanks for adding me!** This looks like a team server made from "
             "the template. To finish setting it up:\n\n")
    if base and settings_id:
        return (intro
                + f"**Set me up on the dashboard:** {base}/guilds/{settings_id}/setup\n\n"
                + "Or, step by step:\n\n" + _TEMPLATE_SETUP_STEPS)
    return intro + _TEMPLATE_SETUP_STEPS


async def _post_setup_message(guild, settings_id=None):
    """Best-effort welcome: a template clone gets template_setup_message() in
    #bot-commands; anything else gets SETUP_MESSAGE in the system channel, else
    the first channel we can write to. Never raises: a guild that will not let
    us post must still get its settings row."""
    try:
        text, channel = SETUP_MESSAGE, guild.system_channel
        if server_setup.looks_like_template(guild):
            text = template_setup_message(settings_id)
            channel = discord.utils.get(guild.text_channels, name=server_setup.BOT_CHANNEL)
        if channel is None or not channel.permissions_for(guild.me).send_messages:
            channel = next(
                (c for c in guild.text_channels
                 if c.permissions_for(guild.me).send_messages),
                None,
            )
        if channel is None:
            logger.info("guild %s: nowhere to post the setup message", guild.id)
            return
        await channel.send(text)
    except Exception:
        logger.exception("guild %s: failed to post the setup message", guild.id)


class Settings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")
        # on_guild_join only fires at the moment of joining, so guilds the bot
        # was already a member of when this code/DB was deployed have no row in
        # `guilds`. Then GuildSettingsDb.get*() returns None and the first
        # caller to dereference it crashes (e.g. check_is_bot_channel). Backfill
        # any missing rows for the guilds we're currently in.
        for guild in self.bot.guilds:
            self._ensure_guild_settings(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Add new guilds to the database, and greet them with the setup steps.

        The greeting is keyed on the row being NEW, not on the join event: on_ready
        backfills rows for guilds we were already in, and a restart must not spam an
        established server."""
        created = self._ensure_guild_settings(guild)
        if created:
            await _post_setup_message(guild, created.id)

    @staticmethod
    def _ensure_guild_settings(guild):
        """Create a default settings row for `guild` if one doesn't exist yet.
        Returns the new row if one was created (None otherwise), so the caller can
        tell a genuine join from the on_ready backfill and read the row id."""
        if GuildSettingsDb.get(guild.id) is not None:
            return None
        g = GuildSettings()
        g.guild_id = guild.id
        g.guild_name = guild.name
        GuildSettingsDb.commit(g)
        return g


async def setup(bot):
    await bot.add_cog(Settings(bot))
