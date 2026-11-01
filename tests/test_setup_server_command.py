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

AUTHOR_ID = 42


def _ctx():
    ctx = make_ctx()
    ctx.author.id = AUTHOR_ID
    ctx.guild.owner_id = AUTHOR_ID
    return ctx


async def _call(cog, ctx, settings, resolve=None, preflight=None, run=None):
    hub, hub_row = HUB, HUB_ROW
    with patch("bot.core.server_setup.GuildSettingsDb") as gdb, \
         patch("bot.core.server_setup.hunt_archive.resolve_hub",
               new=resolve or AsyncMock(return_value=(hub, hub_row))) as res, \
         patch("bot.core.server_setup.preflight",
               new=preflight or MagicMock(return_value=TARGETS)) as pre, \
         patch("bot.core.server_setup.run_setup",
               new=run or AsyncMock(return_value=server_setup.SetupReport(steps=["did a thing"]))) as rs, \
         patch("bot.core.notifier.DiscordNotifier") as notifier:
        gdb.get.return_value = settings
        notifier.return_value.welcome = AsyncMock()
        await Puzzles.setup_server.callback(cog, ctx, 1000, 70, "https://hunt.example", hunt_name="Big Hunt")
    return res, pre, rs, notifier, hub_row


def _sent(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


async def test_a_locked_server_refuses_before_anything_else():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    res, pre, rs, _, _ = await _call(cog, ctx, _settings(locked=True))
    assert "already set up" in _sent(ctx)
    res.assert_not_awaited(); pre.assert_not_called(); rs.assert_not_awaited()


async def test_a_server_with_no_settings_row_refuses():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    res, _, rs, _, _ = await _call(cog, ctx, None)
    assert ctx.send.await_args.args[0].startswith(":x:")
    res.assert_not_awaited(); rs.assert_not_awaited()


async def test_a_bad_hub_refuses_before_preflight():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    bad = AsyncMock(side_effect=hunt_archive.HubUnavailable("you aren't an admin on The Hub"))
    _, pre, rs, _, _ = await _call(cog, ctx, _settings(), resolve=bad)
    ctx.send.assert_awaited_once_with(":x: you aren't an admin on The Hub")
    pre.assert_not_called(); rs.assert_not_awaited()


async def test_a_preflight_refusal_makes_nothing():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    refuse = MagicMock(side_effect=server_setup.SetupRefused("the template's #lobby channel is missing"))
    _, _, rs, _, _ = await _call(cog, ctx, _settings(), preflight=refuse)
    ctx.send.assert_awaited_once_with(":x: the template's #lobby channel is missing")
    rs.assert_not_awaited()


async def test_a_non_owner_non_admin_is_refused_before_anything_else():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    ctx.guild.owner_id = AUTHOR_ID + 1
    with patch("bot.core.server_setup.role_gate.member_holds_tier", return_value=False):
        res, _, rs, _, _ = await _call(cog, ctx, _settings())
    assert "Only this server's owner" in _sent(ctx)
    res.assert_not_awaited(); rs.assert_not_awaited()


async def test_a_team_server_admin_who_is_not_owner_may_run_it():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    ctx.guild.owner_id = AUTHOR_ID + 1
    with patch("bot.core.server_setup.role_gate.member_holds_tier", return_value=True):
        _, _, rs, _, _ = await _call(cog, ctx, _settings())
    rs.assert_awaited_once()


async def test_a_full_run_passes_everything_through_and_reports_locked():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    settings = _settings()
    hunt = MagicMock(); hunt.channel_id = 61
    run = AsyncMock(return_value=server_setup.SetupReport(
        steps=["Created hunt **Big Hunt**"], hunt=hunt, hunt_created=True))
    res, pre, _, notifier, hub_row = await _call(cog, ctx, settings, run=run)
    res.assert_awaited_once_with(cog.bot, 1000, ctx.guild, ctx.author)
    pre.assert_called_once_with(ctx.guild, settings, HUB, 70, "Big Hunt")
    assert run.await_args.args == (cog.service, ctx.guild, ctx.author, TARGETS,
                                   settings, hub_row, "Big Hunt", "https://hunt.example")
    notifier.return_value.welcome.assert_awaited_once_with(
        ctx.guild, hunt, channel=cog.bot.get_channel.return_value)
    cog.bot.get_channel.assert_called_with(61)
    sent = _sent(ctx)
    assert "Created hunt **Big Hunt**" in sent and "locked" in sent


async def test_a_stopped_run_says_to_rerun_and_does_not_claim_locked():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    run = AsyncMock(return_value=server_setup.SetupReport(steps=["Settings copied"], error="sheets down"))
    _, _, _, notifier, _ = await _call(cog, ctx, _settings(), run=run)
    sent = _sent(ctx)
    assert "sheets down" in sent and "re-run" in sent and "locked" not in sent
    notifier.return_value.welcome.assert_not_awaited()


async def test_a_completed_setup_points_at_join_then_send_invites():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    await _call(cog, ctx, _settings())
    text = _sent(ctx)
    assert "**Join**" in text and "!send_invites Big Hunt" in text
    assert text.index("**Join**") < text.index("!send_invites Big Hunt")


async def test_setup_server_completion_posts_the_mod_invite():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    hunt = MagicMock(); hunt.channel_id = 61
    run = AsyncMock(return_value=server_setup.SetupReport(steps=["x"], hunt=hunt, hunt_created=True))
    chan = MagicMock(); chan.name = "hub-admin"
    with patch("bot.cogs.puzzles.mod_invite.post",
               new=AsyncMock(return_value=(":white_check_mark: Mod invite posted in #hub-admin on The Hub.",
                                           "hub-admin"))) as post, \
         patch("bot.core.hub_channel.find") as find:
        await _call(cog, ctx, _settings(), run=run)
    post.assert_awaited_once_with(cog.bot, ctx.guild, hunt)
    find.assert_not_called()
    assert "A mod invite for hub admins is in the hub's #hub-admin." in _sent(ctx)

    ctx2 = _ctx()
    errored = AsyncMock(return_value=server_setup.SetupReport(steps=["x"], hunt=hunt, error="boom"))
    with patch("bot.cogs.puzzles.mod_invite.post", new=AsyncMock()) as post2:
        await _call(cog, ctx2, _settings(), run=errored)
    post2.assert_not_awaited()


async def test_a_refused_mod_invite_adds_a_line_but_setup_still_completes():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    hunt = MagicMock(); hunt.channel_id = 61
    run = AsyncMock(return_value=server_setup.SetupReport(steps=["x"], hunt=hunt))
    with patch("bot.cogs.puzzles.mod_invite.post",
               new=AsyncMock(return_value=(":warning: Mod invite not posted: nope.", None))):
        await _call(cog, ctx, _settings(), run=run)
    text = _sent(ctx)
    assert "Setup complete" in text and "Mod invite not posted: nope" in text


async def test_trailing_files_token_sets_the_mode_and_leaves_the_name():
    cog = make_cog(); cog.bot = MagicMock(); ctx = _ctx()
    seen = {}

    async def fake_perform(bot, service, guild, author, hub_id, role_id, name, url, **kw):
        seen.update(name=name, **kw)
        return server_setup.SetupOutcome(lines=["ok"], ok=True)

    with patch("bot.core.server_setup.perform_setup", new=fake_perform):
        await Puzzles.setup_server.callback(cog, ctx, 1000, 70, "https://h.example",
                                            hunt_name="Big Hunt, files")
        assert (seen["name"], seen["sheet_mode"]) == ("Big Hunt", "files")
        await Puzzles.setup_server.callback(cog, ctx, 1000, 70, "https://h.example",
                                            hunt_name="Big Hunt")
        assert (seen["name"], seen["sheet_mode"]) == ("Big Hunt", "tabs")
