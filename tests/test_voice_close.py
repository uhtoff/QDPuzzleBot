import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core import voice
from tests.test_voice_core import _category, _guild, _hunt, _puzzle, _vc

WARN = ("@here this voice channel closes in 15 seconds — **Cap One** was solved. "
        "Anyone still here will be moved to <#12>.")


@pytest.fixture(autouse=True)
def _clean_closing():
    voice._closing.clear()
    voice._hunt_locks.clear()
    yield
    voice._closing.clear()
    voice._hunt_locks.clear()


@pytest.fixture
def stores():
    with patch("bot.core.voice.HuntJsonDb") as h, patch("bot.core.voice.PuzzleJsonDb") as p:
        h.voice_channel_id.return_value = None  # nothing stored beyond the objects
        p.voice_channel_id.return_value = None
        p.is_solved.return_value = True         # still solved after any warning
        yield h, p


async def test_close_voice_channel_reports_skip_when_already_closing():
    ch = _vc(21)
    voice._closing.add(21)
    assert await voice.close_voice_channel(_guild([ch]), _hunt(), ch, "r", sleep=AsyncMock()) is False
    voice._closing.clear()
    assert await voice.close_voice_channel(_guild([ch]), _hunt(), ch, "r", sleep=AsyncMock()) is True


def _member(name="m"):
    m = MagicMock()
    m.name = name
    m.move_to = AsyncMock()
    return m


async def test_empty_channel_is_deleted_without_a_warning():
    ch = _vc(21)
    sleep = AsyncMock()
    await voice.close_voice_channel(_guild([ch]), _hunt(), ch, "r", sleep=sleep)
    ch.send.assert_not_awaited()
    sleep.assert_not_awaited()
    ch.delete.assert_awaited_once()


async def test_occupied_channel_warns_then_moves_then_deletes():
    hunt_vc = _vc(12)
    a, b = _member("a"), _member("b")
    ch = _vc(21, members=[a, b])
    guild = _guild([hunt_vc, ch])
    hunt = _hunt(voice_channel_id=12)
    sleep = AsyncMock()
    mgr = MagicMock()
    mgr.attach_mock(ch.send, "send")
    mgr.attach_mock(sleep, "sleep")
    mgr.attach_mock(a.move_to, "move_a")
    mgr.attach_mock(b.move_to, "move_b")
    mgr.attach_mock(ch.delete, "delete")
    await voice.close_voice_channel(guild, hunt, ch, "**Cap One** was solved", sleep=sleep)
    ch.send.assert_awaited_once()
    assert ch.send.await_args.args[0] == WARN
    assert ch.send.await_args.kwargs["allowed_mentions"].everyone is True
    sleep.assert_awaited_once_with(15)
    a.move_to.assert_awaited_once_with(hunt_vc)
    b.move_to.assert_awaited_once_with(hunt_vc)
    names = [c[0] for c in mgr.mock_calls]
    assert names == ["send", "sleep", "move_a", "move_b", "delete"]


async def test_close_deletes_even_if_a_move_fails():
    hunt_vc = _vc(12)
    a, b = _member("a"), _member("b")
    a.move_to.side_effect = discord.HTTPException(MagicMock(status=500, reason="x"), "boom")
    ch = _vc(21, members=[a, b])
    await voice.close_voice_channel(_guild([hunt_vc, ch]), _hunt(voice_channel_id=12), ch, "r",
                                    sleep=AsyncMock())
    b.move_to.assert_awaited_once_with(hunt_vc)
    ch.delete.assert_awaited_once()


async def test_close_without_a_hunt_voice_channel_still_warns_and_deletes(stores):
    a = _member()
    ch = _vc(21, members=[a])
    guild = _guild([ch])  # hunt has no voice id and no main category -> ensure returns None
    await voice.close_voice_channel(guild, _hunt(), ch, "r", sleep=AsyncMock())
    assert "the hunt's voice channel" in ch.send.await_args.args[0]
    a.move_to.assert_not_awaited()
    ch.delete.assert_awaited_once()


async def test_close_tolerates_channel_already_gone():
    ch = _vc(21)
    ch.delete.side_effect = discord.NotFound(MagicMock(status=404, reason="x"), "gone")
    await voice.close_voice_channel(_guild([ch]), _hunt(), ch, "r", sleep=AsyncMock())
    assert 21 not in voice._closing


async def test_close_warns_again_after_restart_mid_window(stores):
    _, p = stores
    hunt_vc = _vc(12)
    puzzle = _puzzle(voice_channel_id=21)
    for _ in range(2):
        ch = _vc(21, members=[_member()])
        guild = _guild([hunt_vc, ch])
        puzzle.voice_channel_id = 21
        p.reset_mock()
        mgr = MagicMock()
        mgr.attach_mock(ch.send, "send")
        mgr.attach_mock(ch.delete, "delete")
        mgr.attach_mock(p.set_voice_channel, "clear")
        assert await voice.close_puzzle_voice(guild, _hunt(voice_channel_id=12), puzzle, "r",
                                              sleep=AsyncMock()) is True
        assert [c[0] for c in mgr.mock_calls] == ["send", "delete", "clear"]
    assert puzzle.voice_channel_id == 0 or puzzle.voice_channel_id is None
    p.set_voice_channel.assert_called_with(7, None)


async def test_sweep_skips_channels_already_closing(stores):
    h, p = stores
    gate = asyncio.Event()

    async def blocked_sleep(_):
        await gate.wait()

    hunt_vc = _vc(12)
    ch = _vc(21, members=[_member()])
    guild = _guild([hunt_vc, ch])
    guild.id = 99
    hunt = _hunt(voice_channel_id=12, guild_id=5)
    puzzle = _puzzle(voice_channel_id=21, hunt_id=1)
    h.get_by_attr.return_value = hunt
    p.voice_due.return_value = [puzzle]
    bot = MagicMock()
    bot.guilds = [guild]
    with patch("bot.core.voice.GuildSettingsDb") as g:
        g.get_cached.return_value = SimpleNamespace(id=5)
        first = asyncio.create_task(voice.close_puzzle_voice(guild, hunt, puzzle, "r",
                                                             sleep=blocked_sleep))
        await asyncio.sleep(0)
        await asyncio.wait_for(voice.sweep_solved(bot, sleep=blocked_sleep), 1)
        gate.set()
        await first
    ch.send.assert_awaited_once()
    ch.delete.assert_awaited_once()


async def test_sweep_closes_due_puzzles(stores):
    h, p = stores
    ch = _vc(21)
    guild = _guild([ch])
    hunt = _hunt(guild_id=5)
    puzzle = _puzzle(voice_channel_id=21, hunt_id=1)
    h.get_by_attr.return_value = hunt
    p.voice_due.return_value = [puzzle]
    bot = MagicMock()
    bot.guilds = [guild]
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    with patch("bot.core.voice.GuildSettingsDb") as g:
        g.get_cached.return_value = SimpleNamespace(id=5)
        await voice.sweep_solved(bot, now=now, sleep=AsyncMock())
    p.voice_due.assert_called_once_with(now - voice.SOLVED_GRACE)
    ch.delete.assert_awaited_once()
    p.set_voice_channel.assert_called_once_with(7, None)


async def test_sweep_skips_unavailable_guild_and_survives_errors(stores):
    h, p = stores
    h.get_by_attr.return_value = _hunt(guild_id=5)
    p.voice_due.return_value = [_puzzle(voice_channel_id=21, hunt_id=1)]
    bot = MagicMock()
    bot.guilds = []
    await voice.sweep_solved(bot, sleep=AsyncMock())  # no guild: skipped, no raise
    p.set_voice_channel.assert_not_called()

    bad, good = _vc(21), _vc(31)
    bad.delete.side_effect = RuntimeError("boom")
    guild = _guild([bad, good])
    bot.guilds = [guild]
    p.voice_due.return_value = [_puzzle(id=7, voice_channel_id=21, hunt_id=1),
                                _puzzle(id=8, voice_channel_id=31, hunt_id=1)]
    with patch("bot.core.voice.GuildSettingsDb") as g:
        g.get_cached.return_value = SimpleNamespace(id=5)
        await voice.sweep_solved(bot, sleep=AsyncMock())  # error logged, not raised
    bad.delete.assert_awaited_once()
    good.delete.assert_awaited_once()
    p.set_voice_channel.assert_called_once_with(8, None)


async def test_close_puzzle_voice_clears_id_when_channel_was_deleted_by_hand(stores):
    _, p = stores
    puzzle = _puzzle(voice_channel_id=21)
    assert await voice.close_puzzle_voice(_guild([]), _hunt(), puzzle, "r",
                                          sleep=AsyncMock()) is False
    p.set_voice_channel.assert_called_once_with(7, None)
    assert not puzzle.voice_channel_id


async def test_delete_puzzle_voice_now_deletes_without_warning():
    ch = _vc(21, members=[_member()])
    await voice.delete_puzzle_voice_now(_guild([ch]), _puzzle(voice_channel_id=21))
    ch.send.assert_not_awaited()
    ch.delete.assert_awaited_once()
    await voice.delete_puzzle_voice_now(_guild([]), _puzzle(voice_channel_id=22))  # gone: fine


async def test_cleanup_never_touches_the_hunt_voice_channel_and_removes_empty_categories(stores):
    _, p = stores
    hunt_vc = _vc(12, "Big Hunt voice")
    pa, pb = _vc(21, "Alpha"), _vc(22, "Beta")
    orphan = _vc(23, "Orphan")
    vcat = _category(600, "Big Hunt Voice")
    vcat.channels = [pa, pb, orphan]
    other = _category(700, "Other Hunt Voice")
    other.channels = []
    guild = _guild([hunt_vc, pa, pb, orphan], categories=[vcat, other])
    hunt = _hunt(voice_channel_id=12)
    puzzles = [_puzzle(id=1, voice_channel_id=21), _puzzle(id=2, voice_channel_id=22)]
    p.with_voice.return_value = puzzles
    removed = await voice.cleanup_hunt(guild, hunt, sleep=AsyncMock())
    assert sorted(removed) == ["Alpha", "Beta", "Orphan"]
    for c in (pa, pb, orphan):
        c.delete.assert_awaited_once()
    hunt_vc.delete.assert_not_awaited()
    vcat.delete.assert_awaited_once()
    other.delete.assert_not_awaited()
    assert hunt.voice_channel_id == 12


async def test_cleanup_occupied_channels_share_one_wait(stores):
    _, p = stores
    hunt_vc = _vc(12)
    c1 = _vc(21, "A", members=[_member()])
    c2 = _vc(22, "B", members=[_member()])
    guild = _guild([hunt_vc, c1, c2])
    p.with_voice.return_value = [_puzzle(id=1, voice_channel_id=21),
                                 _puzzle(id=2, voice_channel_id=22)]
    gate = asyncio.Event()

    async def gated(_):
        await gate.wait()

    task = asyncio.create_task(voice.cleanup_hunt(guild, _hunt(voice_channel_id=12), sleep=gated))
    for _ in range(20):
        await asyncio.sleep(0)
    # both warned while neither sleep has returned -> waits run concurrently
    c1.send.assert_awaited_once()
    c2.send.assert_awaited_once()
    c1.delete.assert_not_awaited()
    c2.delete.assert_not_awaited()
    gate.set()
    assert sorted(await task) == ["A", "B"]


async def test_close_aborts_when_no_longer_wanted_after_the_warning():
    hunt_vc = _vc(12)
    a = _member()
    ch = _vc(21, members=[a])
    still = AsyncMock(return_value=False)
    sleep = AsyncMock()
    assert await voice.close_voice_channel(_guild([hunt_vc, ch]), _hunt(voice_channel_id=12), ch,
                                           "r", sleep=sleep, still_wanted=still) is False
    ch.send.assert_awaited_once()          # the warning still went out
    still.assert_awaited_once()
    sleep.assert_awaited_once()
    a.move_to.assert_not_awaited()
    ch.delete.assert_not_awaited()
    assert 21 not in voice._closing


async def test_close_goes_ahead_when_still_wanted():
    hunt_vc = _vc(12)
    ch = _vc(21, members=[_member()])
    still = AsyncMock(return_value=True)
    assert await voice.close_voice_channel(_guild([hunt_vc, ch]), _hunt(voice_channel_id=12), ch,
                                           "r", sleep=AsyncMock(), still_wanted=still) is True
    ch.delete.assert_awaited_once()


async def test_sweep_keeps_the_channel_of_a_puzzle_unsolved_during_the_warning(stores):
    h, p = stores
    hunt_vc = _vc(12)
    ch = _vc(21, members=[_member()])
    guild = _guild([hunt_vc, ch])
    hunt = _hunt(voice_channel_id=12, guild_id=5)
    puzzle = _puzzle(voice_channel_id=21, hunt_id=1)
    h.get_by_attr.return_value = hunt
    p.voice_due.return_value = [puzzle]
    p.is_solved.return_value = False      # unsolved while the 15 s ran
    bot = MagicMock()
    bot.guilds = [guild]
    with patch("bot.core.voice.GuildSettingsDb") as g:
        g.get_cached.return_value = SimpleNamespace(id=5)
        await voice.sweep_solved(bot, sleep=AsyncMock())
    p.is_solved.assert_called_once_with(7)
    ch.send.assert_awaited_once()
    ch.delete.assert_not_awaited()
    p.set_voice_channel.assert_not_called()
    assert puzzle.voice_channel_id == 21


def _bot_with_board(guilds):
    bot = MagicMock()
    bot.guilds = guilds
    puzzles = MagicMock()
    puzzles.service.notifier.board_changed = AsyncMock()
    bot.get_cog.side_effect = lambda name: puzzles if name == "Puzzles" else None
    return bot, puzzles.service.notifier.board_changed


async def test_sweep_pushes_the_board_once_per_hunt_that_lost_a_channel(stores):
    h, p = stores
    c1, c2 = _vc(21), _vc(22)
    guild = _guild([c1, c2])
    hunt = _hunt(guild_id=5)
    h.get_by_attr.return_value = hunt
    p.voice_due.return_value = [_puzzle(id=7, voice_channel_id=21, hunt_id=1),
                                _puzzle(id=8, voice_channel_id=22, hunt_id=1)]
    bot, board_changed = _bot_with_board([guild])
    with patch("bot.core.voice.GuildSettingsDb") as g:
        g.get_cached.return_value = SimpleNamespace(id=5)
        await voice.sweep_solved(bot, sleep=AsyncMock())
    board_changed.assert_awaited_once_with(hunt)


async def test_sweep_does_not_push_when_nothing_closed(stores):
    h, p = stores
    hunt_vc = _vc(12)
    ch = _vc(21, members=[_member()])
    guild = _guild([hunt_vc, ch])
    h.get_by_attr.return_value = _hunt(voice_channel_id=12, guild_id=5)
    p.voice_due.return_value = [_puzzle(voice_channel_id=21, hunt_id=1)]
    p.is_solved.return_value = False
    bot, board_changed = _bot_with_board([guild])
    with patch("bot.core.voice.GuildSettingsDb") as g:
        g.get_cached.return_value = SimpleNamespace(id=5)
        await voice.sweep_solved(bot, sleep=AsyncMock())
    board_changed.assert_not_awaited()


async def test_notify_board_never_raises():
    bot = MagicMock()
    bot.get_cog.return_value.service.notifier.board_changed = AsyncMock(side_effect=RuntimeError)
    await voice.notify_board(bot, _hunt())
    bot.get_cog.return_value = None
    await voice.notify_board(bot, _hunt())


async def test_sweep_refreshes_each_closed_puzzles_pin(stores):
    """The pinned info's voice link goes once the channel is gone."""
    h, p = stores
    c1, c2 = _vc(21), _vc(22)
    guild = _guild([c1, c2])
    hunt = _hunt(guild_id=5)
    h.get_by_attr.return_value = hunt
    p1 = _puzzle(id=7, voice_channel_id=21, hunt_id=1)
    p2 = _puzzle(id=8, voice_channel_id=22, hunt_id=1)
    p.voice_due.return_value = [p1, p2]
    bot, _ = _bot_with_board([guild])
    refresh = bot.get_cog("Puzzles").service.notifier.refresh_info = AsyncMock()
    with patch("bot.core.voice.GuildSettingsDb") as g:
        g.get_cached.return_value = SimpleNamespace(id=5)
        await voice.sweep_solved(bot, sleep=AsyncMock())
    assert {c.args[2].id for c in refresh.await_args_list} == {7, 8}
    assert all(c.args[0] is guild and c.args[1] is hunt for c in refresh.await_args_list)
