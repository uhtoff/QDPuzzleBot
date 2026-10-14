"""!setup_server: lock, then hub, then preflight; all refuse before anything is made."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.puzzles import Puzzles
from bot.core import hunt_archive, server_setup
from tests.conftest import make_cog, make_ctx


def _settings(locked=False):
    s = MagicMock()
    s.setup_complete_time = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc) if locked else None
    return s


HUB, HUB_ROW = MagicMock(), MagicMock()
HUB.name = "The Hub"
TARGETS = MagicMock()
TARGETS.roster_role.name = "Team Rocket"


async def _call(cog, ctx, settings, resolve=None, preflight=None, run=None):
    hub, hub_row = HUB, HUB_ROW
    with patch("bot.cogs.puzzles.GuildSettingsDb") as gdb, \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=resolve or AsyncMock(return_value=(hub, hub_row))) as res, \
         patch("bot.cogs.puzzles.server_setup.preflight",
               new=preflight or MagicMock(return_value=TARGETS)) as pre, \
         patch("bot.cogs.puzzles.server_setup.run_setup",
               new=run or AsyncMock(return_value=server_setup.SetupReport(steps=["did a thing"]))) as rs, \
         patch("bot.core.notifier.DiscordNotifier") as notifier:
        gdb.get.return_value = settings
        notifier.return_value.welcome = AsyncMock()
        await Puzzles.setup_server.callback(cog, ctx, 1000, 70, "https://hunt.example", hunt_name="Big Hunt")
    return res, pre, rs, notifier, hub_row


def _sent(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


async def test_a_locked_server_refuses_before_anything_else():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    res, pre, rs, _, _ = await _call(cog, ctx, _settings(locked=True))
    assert "already set up" in _sent(ctx)
    res.assert_not_awaited(); pre.assert_not_called(); rs.assert_not_awaited()


async def test_a_server_with_no_settings_row_refuses():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    res, _, rs, _, _ = await _call(cog, ctx, None)
    assert ctx.send.await_args.args[0].startswith(":x:")
    res.assert_not_awaited(); rs.assert_not_awaited()


async def test_a_bad_hub_refuses_before_preflight():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    bad = AsyncMock(side_effect=hunt_archive.HubUnavailable("you aren't an admin on The Hub"))
    _, pre, rs, _, _ = await _call(cog, ctx, _settings(), resolve=bad)
    ctx.send.assert_awaited_once_with(":x: you aren't an admin on The Hub")
    pre.assert_not_called(); rs.assert_not_awaited()


async def test_a_preflight_refusal_makes_nothing():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    refuse = MagicMock(side_effect=server_setup.SetupRefused("the template's #lobby channel is missing"))
    _, _, rs, _, _ = await _call(cog, ctx, _settings(), preflight=refuse)
    ctx.send.assert_awaited_once_with(":x: the template's #lobby channel is missing")
    rs.assert_not_awaited()


async def test_a_full_run_passes_everything_through_and_reports_locked():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    settings = _settings()
    hunt = MagicMock(); hunt.channel_id = 61
    run = AsyncMock(return_value=server_setup.SetupReport(
        steps=["Created hunt **Big Hunt**"], hunt=hunt, hunt_created=True))
    res, pre, _, notifier, hub_row = await _call(cog, ctx, settings, run=run)
    res.assert_awaited_once_with(cog.bot, 1000, ctx.guild, ctx.author)
    pre.assert_called_once_with(ctx.guild, settings, HUB, 70, "Big Hunt")
    assert run.await_args.args == (cog.service, ctx.guild, ctx.author, TARGETS,
                                   settings, hub_row, "Big Hunt", "https://hunt.example")
    notifier.return_value.welcome.assert_awaited_once()
    sent = _sent(ctx)
    assert "Created hunt **Big Hunt**" in sent and "locked" in sent


async def test_a_stopped_run_says_to_rerun_and_does_not_claim_locked():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()
    run = AsyncMock(return_value=server_setup.SetupReport(steps=["Settings copied"], error="sheets down"))
    _, _, _, notifier, _ = await _call(cog, ctx, _settings(), run=run)
    sent = _sent(ctx)
    assert "sheets down" in sent and "re-run" in sent and "locked" not in sent
    notifier.return_value.welcome.assert_not_awaited()
