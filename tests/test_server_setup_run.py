"""run_setup: settings copied from the hub, admin pinned to the template's mod
role, hunt created once, roster recorded, lobby welcomed once, and the run-once
lock written last -- never after a failure."""
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core import server_setup
from bot.core.server_setup import SetupTargets, run_setup
from bot.store.puzzle_settings import GuildSettings

MOD, ROSTER, BOT = 50, 70, 900


def _history(*messages):
    async def history(limit=None):
        for m in messages:
            yield m
    return history


def _targets(hunt=None):
    mod = MagicMock(); mod.id = MOD
    lobby = MagicMock(); lobby.send = AsyncMock(); lobby.history = _history()
    bot_channel = MagicMock(); bot_channel.name = "bot-commands"
    roster = MagicMock(); roster.id = ROSTER; roster.name = "Team Rocket"
    return SetupTargets(mod, lobby, bot_channel, roster, hunt)


def _hub_row():
    row = GuildSettings()
    row.website_url = "https://web.example"
    row.drive_parent_id = "drive-parent"
    row.drive_resources_id = "drive-resources"
    row.discord_bot_emoji = ":dog:"
    row.discord_use_voice_channels = True
    return row


def _settings():
    s = GuildSettings()
    s.id = 7
    s.guild_id = 2000
    return s


def _guild():
    g = MagicMock(); g.name = "Rocket HQ"; g.me.id = BOT
    return g


def _hunt(role_id=71, roster_role_id=0):
    h = MagicMock(); h.id = 42; h.name = "Big Hunt"; h.role_id = role_id; h.roster_role_id = roster_role_id
    return h


async def _run(targets, service=None, settings=None):
    service = service or MagicMock()
    with patch("bot.core.server_setup.GuildSettingsDb") as gdb, \
         patch("bot.core.server_setup.HuntJsonDb") as hdb, \
         patch("bot.core.server_setup.MultiTeamDb") as mdb:
        mdb.for_hunt.return_value = None
        report = await run_setup(service, _guild(), MagicMock(), targets, settings or _settings(),
                                 _hub_row(), "Big Hunt", "https://hunt.example")
    return report, gdb, hdb


async def test_a_fresh_run_does_every_step_then_locks():
    hunt = _hunt()
    service = MagicMock(); service.create_hunt = AsyncMock(return_value=hunt)
    targets = _targets()
    settings = _settings()
    report, gdb, hdb = await _run(targets, service, settings)

    assert report.error == ""
    assert report.hunt is hunt and report.hunt_created
    assert (settings.website_url, settings.drive_parent_id, settings.drive_resources_id,
            settings.discord_bot_emoji, settings.discord_use_voice_channels) == (
        "https://web.example", "drive-parent", "drive-resources", ":dog:", True)
    assert settings.discord_bot_channel == "bot-commands"
    assert settings.admin_role_ids == str(MOD)
    assert settings.guild_name == "Rocket HQ"
    gdb.commit.assert_called_once_with(settings)

    kwargs = service.create_hunt.await_args.kwargs
    assert kwargs["name"] == "Big Hunt" and kwargs["url"] == "https://hunt.example"
    assert kwargs["role_name"] == "Team Rocket"
    assert kwargs["guild_id"] == 7
    assert kwargs["confirm_existing_role"] is True
    assert hunt.roster_role_id == ROSTER
    hdb.commit.assert_called_once_with(hunt)

    targets.lobby.send.assert_awaited_once()
    assert server_setup.WELCOME_MARK in targets.lobby.send.await_args.args[0]
    view = targets.lobby.send.await_args.kwargs["view"]
    assert [c.custom_id for c in view.children] == ["hunt_role:join:42", "hunt_role:leave:42"]
    gdb.mark_setup_complete.assert_called_once_with(settings)


async def test_a_rerun_reuses_the_hunt_and_the_welcome():
    hunt = _hunt(roster_role_id=ROSTER)
    service = MagicMock(); service.create_hunt = AsyncMock()
    targets = _targets(hunt=hunt)
    posted = MagicMock(); posted.author.id = BOT; posted.content = "hi " + server_setup.WELCOME_MARK
    targets.lobby.history = _history(posted)
    report, gdb, hdb = await _run(targets, service)

    assert report.error == ""
    assert report.hunt is hunt and not report.hunt_created
    service.create_hunt.assert_not_awaited()
    hdb.commit.assert_not_called()           # roster already recorded
    targets.lobby.send.assert_not_awaited()
    gdb.mark_setup_complete.assert_called_once()


async def test_someone_elses_message_with_the_mark_is_not_the_welcome():
    hunt = _hunt(roster_role_id=ROSTER)
    targets = _targets(hunt=hunt)
    impostor = MagicMock(); impostor.author.id = BOT + 1; impostor.content = server_setup.WELCOME_MARK
    targets.lobby.history = _history(impostor)
    await _run(targets)
    targets.lobby.send.assert_awaited_once()


async def test_a_failure_is_reported_and_never_locks():
    service = MagicMock(); service.create_hunt = AsyncMock(side_effect=RuntimeError("sheets down"))
    report, gdb, _ = await _run(_targets(), service)
    assert "sheets down" in report.error
    assert report.steps                      # the settings step it did finish is listed
    gdb.mark_setup_complete.assert_not_called()


async def test_an_ungated_hunt_stops_before_the_lock():
    service = MagicMock(); service.create_hunt = AsyncMock(return_value=_hunt(role_id=0))
    report, gdb, _ = await _run(_targets(), service)
    assert "!set_hunt_role" in report.error
    assert "#bot-commands" in report.error
    gdb.mark_setup_complete.assert_not_called()


async def test_a_failed_welcome_never_locks():
    targets = _targets(hunt=_hunt(roster_role_id=ROSTER))
    targets.lobby.send = AsyncMock(side_effect=RuntimeError("no perms"))
    report, gdb, _ = await _run(targets)
    assert "no perms" in report.error
    gdb.mark_setup_complete.assert_not_called()


def test_the_welcome_points_at_join_for_the_team():
    text = server_setup.welcome_text(_hunt(), "Team Rocket")
    assert "**Join**" in text and "Team Rocket" in text and "Big Hunt" in text
    assert text.endswith(server_setup.WELCOME_MARK)
    assert "**Leave**" in text and "`mod`" in text
