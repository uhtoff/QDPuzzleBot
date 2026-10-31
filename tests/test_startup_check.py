"""Startup warning about broken team servers."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.team_servers import TeamServers
from bot.core import startup_check


class R:
    def __init__(self, pos):
        self.pos = pos

    def __gt__(self, other):
        return self.pos > other.pos


def _hunt(name, roster=70):
    h = MagicMock()
    h.name = name
    h.roster_role_id = roster
    return h


def _hub(name="Hub"):
    hub = MagicMock()
    hub.name = name
    return hub


def _team(name="Team", admin=True, top=10):
    g = MagicMock()
    g.name = name
    g.me.guild_permissions.administrator = admin
    g.me.top_role = R(top)
    return g


def _bot(teams, hubs):
    """teams {snowflake: guild}; hubs {roster_id: hub}."""
    bot = MagicMock()
    bot.get_guild.side_effect = lambda i: teams.get(i)

    def find(b, rid):
        hub = hubs.get(rid)
        return None if hub is None else MagicMock(guild=hub)
    return bot, find


def _run(hunts, teams, hubs, admin_roles=(R(5),)):
    bot, find = _bot(teams, hubs)
    post = AsyncMock(return_value=True)
    with patch("bot.core.startup_check.HuntJsonDb.live_team_hunts", return_value=hunts), \
         patch("bot.core.startup_check.role_panel.find_roster_role", side_effect=find), \
         patch("bot.core.startup_check.role_panel.admin_roles", return_value=list(admin_roles)), \
         patch("bot.core.startup_check.hub_channel.post", post):
        import asyncio
        asyncio.run(startup_check.run(bot))
    return post


def test_all_well_posts_nothing():
    hub = _hub()
    assert not _run([(_hunt("A"), 1)], {1: _team()}, {70: hub}).await_count


def test_a_missing_team_server_is_reported_to_its_hub():
    hub = _hub()
    post = _run([(_hunt("Big Hunt"), 1)], {}, {70: hub})
    post.assert_awaited_once()
    assert post.await_args.args[0] is hub
    assert "I'm no longer on **Big Hunt**'s team server" in post.await_args.args[1]


def test_lost_administrator_and_low_role_are_both_listed_in_one_message():
    hub = _hub()
    post = _run([(_hunt("A"), 1)], {1: _team("Rocket", admin=False, top=1)}, {70: hub})
    post.assert_awaited_once()
    text = post.await_args.args[1]
    assert "I've lost Administrator on **Rocket**" in text
    assert "My role is below `mod` on **Rocket** — drag it above" in text
    assert text.count("•") == 2


def test_problems_on_two_hubs_post_twice():
    h1, h2 = _hub("H1"), _hub("H2")
    post = _run([(_hunt("A", 70), 1), (_hunt("B", 71), 2)], {}, {70: h1, 71: h2})
    assert post.await_count == 2
    assert {c.args[0] for c in post.await_args_list} == {h1, h2}


def test_an_unfindable_hub_is_only_logged():
    assert not _run([(_hunt("A"), 1)], {}, {}).await_count


def test_an_odd_hunt_is_skipped_not_raised():
    hub = _hub()
    bot, find = _bot({}, {70: hub})
    bad = _hunt("Bad", 0)
    bad.name = None
    with patch("bot.core.startup_check.HuntJsonDb.live_team_hunts",
               return_value=[(bad, 1), (_hunt("Good"), 2)]), \
         patch("bot.core.startup_check.role_panel.find_roster_role",
               side_effect=lambda b, rid: (_ for _ in ()).throw(ValueError) if rid == 0 else find(b, rid)):
        result = startup_check.problems(bot)
    assert list(result[hub]) == ["I'm no longer on **Good**'s team server"]


@pytest.mark.asyncio
async def test_the_cog_runs_the_check_once():
    cog = TeamServers(MagicMock())
    with patch("bot.cogs.team_servers.startup_check.run", new=AsyncMock(side_effect=RuntimeError)) as run:
        await cog.on_ready()
        await cog.on_ready()
    run.assert_awaited_once()
