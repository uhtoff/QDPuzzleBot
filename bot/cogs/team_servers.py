"""Listeners for team servers (slice 4): grant the team role on arrival, and DM an
invite when someone is given a hunt's hub role. The logic lives in
bot/core/team_invites.py; these only route the events, and never let an error
escape into discord.py's event loop."""
import logging

from discord.ext import commands

from bot.core import team_invites

logger = logging.getLogger(__name__)


class TeamServers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        try:
            await team_invites.on_join(self.bot, member)
        except Exception:
            logger.exception("team-server arrival failed for member %s", getattr(member, "id", "?"))

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        added = {r.id for r in after.roles} - {r.id for r in before.roles}
        if not added:
            return
        try:
            await team_invites.on_hub_roles_added(self.bot, after, added)
        except Exception:
            logger.exception("late-joiner invite failed for member %s", getattr(after, "id", "?"))


async def setup(bot):
    await bot.add_cog(TeamServers(bot))
