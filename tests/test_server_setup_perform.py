"""template_problems and perform_setup: the shared !setup_server seam."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.core import server_setup
from tests.conftest import make_cog, make_ctx
from tests.test_server_setup_preflight import _guild, _role


def _settings(locked=False):
    s = MagicMock()
    s.id = 7
    s.setup_complete_time = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc) if locked else None
    return s


def _break_no_admin(g, s):
    g.me.guild_permissions.administrator = False


def _break_everyone_sees(g, s):
    g.default_role.permissions.view_channel = True


def _break_two_mods(g, s):
    g.roles.append(_role(51, "mod", 4))


def _break_bot_below_mod(g, s):
    g.me.top_role.position = 1


def _break_no_lobby(g, s):
    g.text_channels = [c for c in g.text_channels if c.name != "lobby"]


def _break_locked(g, s):
    s.setup_complete_time = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize("breaker, fragment", [
    (_break_no_admin, "needs Administrator"),
    (_break_everyone_sees, "@everyone can see channels"),
    (_break_two_mods, "exactly one `mod` role, found 2"),
    (_break_bot_below_mod, "drag the bot's role above `mod`"),
    (_break_no_lobby, "#lobby channel is missing"),
    (_break_locked, "already set up (2026-10-01)"),
])
def test_template_problems_lists_each_problem(breaker, fragment):
    guild, settings = _guild(), _settings()
    breaker(guild, settings)
    problems = server_setup.template_problems(guild, settings)
    assert len(problems) == 1 and fragment in problems[0]


def test_template_problems_lists_several_at_once():
    guild, settings = _guild(), _settings()
    _break_no_admin(guild, settings); _break_no_lobby(guild, settings)
    assert len(server_setup.template_problems(guild, settings)) == 2


def test_template_problems_empty_for_a_good_clone():
    assert server_setup.template_problems(_guild(), _settings()) == []


def _invoker(owner=True):
    m = MagicMock(); m.id = 42; m.roles = []
    return m


async def test_perform_setup_passes_hunt_fields_to_create_hunt():
    guild = _guild(); guild.owner_id = 42
    hub_row = MagicMock()
    hunt = MagicMock(role_id=5, roster_role_id=70, channel_id=61)
    service = MagicMock(); service.create_hunt = AsyncMock(return_value=hunt)
    bot = MagicMock()
    hub = MagicMock(); hub.name = "Hub"
    roster = _role(70, "Team Rocket", 3); hub.get_role.return_value = roster
    with patch("bot.core.server_setup.GuildSettingsDb") as gdb, \
         patch("bot.core.server_setup.HuntJsonDb") as hdb, \
         patch("bot.core.server_setup.hunt_archive.resolve_hub", new=AsyncMock(return_value=(hub, hub_row))), \
         patch("bot.core.server_setup._has_welcome", new=AsyncMock(return_value=True)), \
         patch("bot.core.notifier.DiscordNotifier") as notifier, \
         patch("bot.core.mod_invite.post", new=AsyncMock(return_value=("r", None))):
        gdb.get.return_value = _settings()
        hdb.get_by_name_for_guild.return_value = None
        hdb.count_for_guild.return_value = 0
        notifier.return_value.welcome = AsyncMock()
        out = await server_setup.perform_setup(
            bot, service, guild, _invoker(), 1000, 70, "Big Hunt", "https://h.example",
            start_timestamp=1234, username="u", password="p", create_solved_sheet=True)
    kw = service.create_hunt.await_args.kwargs
    assert (kw["start_timestamp"], kw["username"], kw["password"], kw["create_solved_sheet"]) == \
        (1234, "u", "p", True)
    assert out.ok and out.hunt is hunt and out.error == ""


async def test_perform_setup_refuses_a_non_owner_non_admin():
    guild = _guild(); guild.owner_id = 99
    with patch("bot.core.server_setup.GuildSettingsDb") as gdb, \
         patch("bot.core.server_setup.role_gate.member_holds_tier", return_value=False), \
         patch("bot.core.server_setup.hunt_archive.resolve_hub", new=AsyncMock()) as res:
        gdb.get.return_value = _settings()
        out = await server_setup.perform_setup(
            MagicMock(), MagicMock(), guild, _invoker(), 1000, 70, "Big Hunt", "u")
    text = "Only this server's owner (or an admin here) can run !setup_server."
    assert (out.ok, out.error, out.lines) == (False, text, [f":x: {text}"])
    res.assert_not_awaited()


async def test_setup_server_command_sends_perform_setup_lines():
    cog = make_cog(); cog.bot = MagicMock(); ctx = make_ctx()

    async def fake(bot, service, guild, invoker, hub_id, role_id, name, url, *, on_start=None, **kw):
        await on_start("Team Rocket")
        return server_setup.SetupOutcome(lines=["a", "b"], ok=True)

    with patch("bot.core.server_setup.perform_setup", new=fake):
        await Puzzles.setup_server.callback(cog, ctx, 1000, 70, "https://h.example", hunt_name="Big Hunt")
    sent = [c.args[0] for c in ctx.send.await_args_list]
    assert sent == ["⏳ Setting up **Big Hunt** for **Team Rocket**…", "a\nb"]


def _patched_run(run_setup, extra=None):
    hub_row = MagicMock(); hub = MagicMock(); hub.name = "Hub"
    stack = [
        patch("bot.core.server_setup.GuildSettingsDb"),
        patch("bot.core.server_setup.hunt_archive.resolve_hub", new=AsyncMock(return_value=(hub, hub_row))),
        patch("bot.core.server_setup.preflight", new=MagicMock(return_value=MagicMock())),
        patch("bot.core.server_setup.run_setup", new=run_setup),
        patch("bot.core.notifier.DiscordNotifier"),
        patch("bot.core.mod_invite.post", new=AsyncMock(return_value=("r", None))),
    ]
    return stack


async def _run(report, **kw):
    from contextlib import ExitStack
    guild = _guild(); guild.owner_id = 42
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patched_run(AsyncMock(return_value=report))]
        mocks[0].get.return_value = _settings()
        return await server_setup.perform_setup(
            MagicMock(), MagicMock(), guild, _invoker(), 1000, 70, "Big Hunt", "u", **kw)


async def test_next_line_after_dashboard_invites_does_not_ask_for_send_invites():
    ok = server_setup.SetupReport(steps=["x"], hunt=MagicMock())
    plain = (await _run(ok)).lines[-1]
    dash = (await _run(ok, invites_by_dashboard=True)).lines[-1]
    assert "!send_invites Big Hunt" in plain
    assert dash == ("Next: click **Join** in #lobby (as a hub admin you get `mod` too) and "
                    "check the server; the invites report is below. Transfer server "
                    "ownership to a team member if wanted.")


async def test_stopped_line_wording_depends_on_entry_point():
    bad = server_setup.SetupReport(steps=["x"], error="boom")
    cmd = (await _run(bad)).lines[-1]
    web = (await _run(bad, via_dashboard=True)).lines[-1]
    assert cmd == "Fix that, then re-run the same !setup_server command; finished steps are skipped."
    assert web == "Fix that, then press Set up again; finished steps are skipped."


async def test_a_second_concurrent_run_on_the_same_guild_is_refused():
    import asyncio
    from contextlib import ExitStack
    gate = asyncio.Event()

    async def slow_run(*a, **k):
        await gate.wait()
        return server_setup.SetupReport(steps=["x"])

    guild = _guild(); guild.owner_id = 42
    other = _guild(); other.owner_id = 42; other.id = guild.id + 1
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patched_run(slow_run)]
        mocks[0].get.return_value = _settings()

        def call(g):
            return server_setup.perform_setup(
                MagicMock(), MagicMock(), g, _invoker(), 1000, 70, "Big Hunt", "u")
        first = asyncio.ensure_future(call(guild))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        second = await call(guild)
        text = "Setup is already running on this server — wait for it to finish."
        assert (second.ok, second.error, second.lines) == (False, text, [f":x: {text}"])
        third = asyncio.ensure_future(call(other))     # a different guild is not blocked
        await asyncio.sleep(0); await asyncio.sleep(0)
        assert not third.done() or third.result().ok
        gate.set()
        assert (await first).ok and (await third).ok
        assert (await call(guild)).error == ""         # released after finishing


async def test_the_guard_is_released_when_setup_raises():
    guild = _guild(); guild.owner_id = 42
    with patch("bot.core.server_setup.GuildSettingsDb") as gdb:
        gdb.get.side_effect = RuntimeError("db")
        try:
            await server_setup.perform_setup(
                MagicMock(), MagicMock(), guild, _invoker(), 1000, 70, "Big Hunt", "u")
        except RuntimeError:
            pass
    assert guild.id not in server_setup._RUNNING
