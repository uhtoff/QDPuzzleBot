"""
Settings Cog shipped with cookiecutter-discordpy-postgres.

The settings here are unrelated to puzzle_settings.py
"""

import logging

from discord.ext import commands

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


async def _post_setup_message(guild):
    """Best-effort welcome in the system channel, else the first channel we can
    write to. Never raises: a guild that will not let us post must still get its
    settings row."""
    try:
        channel = guild.system_channel
        if channel is None or not channel.permissions_for(guild.me).send_messages:
            channel = next(
                (c for c in guild.text_channels
                 if c.permissions_for(guild.me).send_messages),
                None,
            )
        if channel is None:
            logger.info("guild %s: nowhere to post the setup message", guild.id)
            return
        await channel.send(SETUP_MESSAGE)
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
            await _post_setup_message(guild)

    @staticmethod
    def _ensure_guild_settings(guild):
        """Create a default settings row for `guild` if one doesn't exist yet.
        Returns True if a row was created, so the caller can tell a genuine join
        from the on_ready backfill."""
        if GuildSettingsDb.get(guild.id) is not None:
            return False
        g = GuildSettings()
        g.guild_id = guild.id
        g.guild_name = guild.name
        GuildSettingsDb.commit(g)
        return True


async def setup(bot):
    await bot.add_cog(Settings(bot))
