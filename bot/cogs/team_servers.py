"""Listeners for team servers (slice 4): grant the team role on arrival, DM an
invite when someone is given a hunt's hub role, and remove them from the team
server when they lose it. The logic lives in bot/core/team_invites.py; these only
route the events, and never let an error escape into discord.py's event loop."""
import logging

from discord.ext import commands

from bot.core import access_log, startup_check, team_invites

logger = logging.getLogger(__name__)


class TeamServers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._checked = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._checked:
            return
        self._checked = True
        try:
            await startup_check.run(self.bot)
        except Exception:
            logger.exception("startup team-server check failed")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        try:
            await team_invites.on_join(self.bot, member)
        except Exception:
            logger.exception("team-server arrival failed for member %s", getattr(member, "id", "?"))
        try:
            await access_log.on_arrived(self.bot, member)
        except Exception:
            logger.exception("access-log arrival failed for member %s", getattr(member, "id", "?"))

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        try:
            await access_log.on_departed(self.bot, member)
        except Exception:
            logger.exception("access-log departure failed for member %s", getattr(member, "id", "?"))

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        added = {r.id for r in after.roles} - {r.id for r in before.roles}
        removed = {r.id for r in before.roles} - {r.id for r in after.roles}
        if added or removed:
            try:
                await access_log.on_roles_changed(self.bot, before, after)
            except Exception:
                logger.exception("access-log role change failed for member %s", getattr(after, "id", "?"))
        if added:
            try:
                await team_invites.on_hub_roles_added(self.bot, after, added)
            except Exception:
                logger.exception("late-joiner invite failed for member %s", getattr(after, "id", "?"))
        if removed:
            try:
                await team_invites.on_hub_roles_removed(self.bot, after, removed)
            except Exception:
                logger.exception("team-leaving check failed for member %s", getattr(after, "id", "?"))


async def setup(bot):
    await bot.add_cog(TeamServers(bot))
