"""Startup check: warn each hub's admin channel about live team servers the bot
can no longer manage (it left, lost Administrator, or sits below `mod`)."""
import logging

from bot.core import hub_channel, role_panel
from bot.store import HuntJsonDb

logger = logging.getLogger(__name__)

HEADER = ":warning: Problems with team servers I found on startup:"


def problems(bot):
    """{hub guild: [problem lines]} for live team hunts; hunts whose hub can't be
    found are collected under the None key."""
    found = {}
    for hunt, snowflake in HuntJsonDb.live_team_hunts():
        try:
            role = role_panel.find_roster_role(bot, hunt.roster_role_id)
            hub = role.guild if role is not None else None
            lines = _hunt_problems(bot, hunt, snowflake)
        except Exception:
            logger.exception("startup check skipped hunt %s", getattr(hunt, "id", "?"))
            continue
        if lines:
            found.setdefault(hub, []).extend(lines)
    return found


def _hunt_problems(bot, hunt, snowflake):
    guild = bot.get_guild(snowflake)
    if guild is None:
        return [f"I'm no longer on **{hunt.name}**'s team server"]
    lines = []
    if not guild.me.guild_permissions.administrator:
        lines.append(f"I've lost Administrator on **{guild.name}**")
    admin = role_panel.admin_roles(guild)
    if admin and not all(guild.me.top_role > r for r in admin):
        lines.append(f"My role is below `mod` on **{guild.name}** — drag it above")
    return lines


async def run(bot):
    for hub, lines in problems(bot).items():
        if hub is None:
            logger.warning("team-server problems with no findable hub: %s", "; ".join(lines))
            continue
        await hub_channel.post(hub, "\n".join([HEADER] + [f"• {line}" for line in lines]))
