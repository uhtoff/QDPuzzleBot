"""The second !send_invites pass and the late-joiner listener can run at once. If
someone is given the hub role in that window, both may see them uninvited; they
must still get exactly one invite DM (review 2026-10-31, finding 1)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core import team_invites

HUNT_ID, ROSTER, TEAM_GUILD = 42, 70, 2000


async def test_the_sweep_and_the_listener_together_send_one_invite():
    invited = set()
    records = []

    class FakeInviteDb:
        @staticmethod
        def invited_user_ids(hunt_id):
            return set(invited)

        @staticmethod
        def record(hunt_id, user_id, code):
            records.append((hunt_id, user_id, code))
            invited.add(user_id)

    gate = asyncio.Event()
    late = MagicMock()
    late.id, late.display_name, late.bot = 9, "Late", False

    async def send(text):
        await gate.wait()   # hold the DM open so the other path can interleave

    late.send = AsyncMock(side_effect=send)

    hub = MagicMock()
    hub.chunked = True
    roster = MagicMock()
    roster.id, roster.name, roster.guild, roster.members = ROSTER, "Team Rocket", hub, [late]
    hub.get_role.side_effect = lambda i: roster if i == ROSTER else None
    late.guild = hub

    codes = iter(range(1, 10))

    async def create_invite(**kwargs):
        inv = MagicMock()
        inv.code = f"code{next(codes)}"
        inv.url = f"https://discord.gg/{inv.code}"
        inv.delete = AsyncMock()
        return inv

    lobby = MagicMock()
    lobby.name = "lobby"
    lobby.create_invite = create_invite
    guild = MagicMock()
    guild.text_channels = [lobby]
    guild.get_member.return_value = None

    hunt = MagicMock()
    hunt.id, hunt.name, hunt.roster_role_id = HUNT_ID, "Big Hunt", ROSTER
    bot = MagicMock()
    bot.get_guild.side_effect = lambda gid: guild if gid == TEAM_GUILD else None

    async def release():
        for _ in range(20):
            await asyncio.sleep(0)
        gate.set()

    with patch("bot.core.team_invites.HuntInviteDb", FakeInviteDb), \
         patch("bot.core.team_invites.HuntJsonDb") as hdb, \
         patch("bot.core.team_invites.role_panel.find_roster_role", return_value=roster):
        hdb.live_invited_hunts_for_roster.return_value = [(hunt, TEAM_GUILD)]
        report, _, _ = await asyncio.gather(
            team_invites.send_invites(bot, guild, hunt),
            team_invites.on_hub_roles_added(bot, late, [ROSTER]),
            release(),
        )

    late.send.assert_awaited_once()
    assert len(records) == 1
