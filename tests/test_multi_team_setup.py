"""Setup's multi-team choice: parse_setup_tail, and run_setup's create/join/reuse step."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import server_setup
from bot.core.server_setup import parse_setup_tail
from tests.test_server_setup_run import _guild, _history, _hub_row, _hunt, _settings, _targets


@pytest.mark.parametrize("text,expected", [
    ("Big Hunt", ("Big Hunt", "tabs", False)),
    ("Big Hunt, files", ("Big Hunt", "files", False)),
    ("Big Hunt, multi-team", ("Big Hunt", "tabs", True)),
    ("Big Hunt, files, multi-team", ("Big Hunt", "files", True)),
    ("Big Hunt, multi-team, files", ("Big Hunt", "files", True)),
    ("Big Hunt ,  MULTI-Team ", ("Big Hunt", "tabs", True)),
    ("Foo, Bar", ("Foo, Bar", "tabs", False)),
    ("Foo, Bar, multi-team", ("Foo, Bar", "tabs", True)),
    ("Foo, multi-team, Bar", ("Foo, multi-team, Bar", "tabs", False)),
    ("Big Hunt, multi-teams", ("Big Hunt, multi-teams", "tabs", False)),
])
def test_parse_setup_tail(text, expected):
    assert parse_setup_tail(text) == expected


class MT:
    """Patches the multi-team seam run_setup looks up."""
    def __init__(self, linked=None, running=None, members=2, fail=None):
        self.db = MagicMock()
        self.db.for_hunt.return_value = linked
        self.db.get.return_value = {"id": 3, "name": "GPH 2026"}
        self.db.members.return_value = [object()] * members
        self.ns = MagicMock(); self.ns.name = "no-spoilers"
        self.fin = MagicMock(); self.fin.name = "finishers"
        self.ensure = AsyncMock(side_effect=fail, return_value=(self.ns, self.fin))
        self.find = AsyncMock(return_value=(3, running is None))

    async def run(self, multi_team=True, hunt=None):
        hunt = hunt or _hunt(roster_role_id=70)
        service = MagicMock(); service.create_hunt = AsyncMock(return_value=hunt)
        targets = _targets()
        targets.lobby.history = _history()
        hub = MagicMock()
        with patch("bot.core.server_setup.GuildSettingsDb") as gdb, \
             patch("bot.core.server_setup.HuntJsonDb"), \
             patch("bot.core.server_setup.MultiTeamDb", self.db), \
             patch("bot.core.server_setup.multi_team_core.find_or_create", self.find), \
             patch("bot.core.server_setup.multi_team_core.ensure_channels", self.ensure):
            report = await server_setup.run_setup(
                service, _guild(), MagicMock(), targets, _settings(), _hub_row(),
                "gph 2026", "https://h.example", hub=hub, multi_team=multi_team)
        self.hub, self.gdb = hub, gdb
        return report


async def test_no_multi_team_makes_no_multi_team_calls():
    mt = MT()
    report = await mt.run(multi_team=False)
    assert report.error == ""
    mt.find.assert_not_awaited(); mt.db.link.assert_not_called(); mt.ensure.assert_not_awaited()


async def test_multi_team_with_none_running_creates_one():
    mt = MT(running=None)
    report = await mt.run()
    assert report.error == ""
    mt.find.assert_awaited_once_with(mt.hub, "Big Hunt")
    mt.db.link.assert_called_once_with(42, 3)
    mt.ensure.assert_awaited_once_with(mt.hub, 3)
    assert ("Started multi-team hunt **GPH 2026** on the hub (#no-spoilers and #finishers)."
            in report.steps)
    mt.gdb.mark_setup_complete.assert_called_once()


async def test_multi_team_joins_a_running_one_and_reports_the_count_with_stored_casing():
    mt = MT(running=object(), members=3)
    report = await mt.run()
    assert "Joined multi-team hunt **GPH 2026** — now 3 teams." in report.steps
    assert not any("Started" in s for s in report.steps)
    mt.db.members.assert_called_once_with(3)


async def test_link_precedes_ensure_channels():
    mt = MT()
    order = []
    mt.db.link.side_effect = lambda *a: order.append("link")
    mt.ensure.side_effect = lambda *a: order.append("ensure") or (mt.ns, mt.fin)
    await mt.run()
    assert order == ["link", "ensure"]


async def test_a_failure_leaves_error_and_no_lock_then_rerun_reuses_the_link():
    mt = MT(fail=RuntimeError("discord said no"))
    report = await mt.run()
    assert report.error == "discord said no"
    mt.gdb.mark_setup_complete.assert_not_called()
    mt.db.link.assert_called_once_with(42, 3)

    # Re-run: the link is now stored, so the step reuses it and re-ensures channels.
    again = MT(linked={"multi_team_id": 3, "name": "GPH 2026"})
    report = await again.run(multi_team=False)
    assert report.error == ""
    assert "Already part of multi-team hunt **GPH 2026**." in report.steps
    again.find.assert_not_awaited(); again.db.link.assert_not_called()
    again.ensure.assert_awaited_once_with(again.hub, 3)
    again.gdb.mark_setup_complete.assert_called_once()


async def test_an_existing_link_wins_over_the_multi_team_answer():
    mt = MT(linked={"multi_team_id": 8, "name": "Old One"})
    report = await mt.run(multi_team=True)
    assert "Already part of multi-team hunt **Old One**." in report.steps
    mt.find.assert_not_awaited(); mt.db.link.assert_not_called()
    mt.ensure.assert_awaited_once_with(mt.hub, 8)
