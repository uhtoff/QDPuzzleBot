"""M3.5 + Phase B: Notifier interface, NullNotifier no-op contract, CompositeNotifier seam.

Method arg shapes pinned against the relocated callers:
  welcome      <- _web_send_welcome(guild, hunt, hunt_round, puzzle, channel)  (:700)
  refresh_info <- web_refresh_info(guild, hunt, puzzle)                        (:708)
  puzzle_solved<- web_post_solved_embed(guild, hunt, puzzle)                   (:893)
  board_changed<- PuzzleService mutations (Phase B)
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.notifier import (   # <-- new → right reason
    Notifier,
    NullNotifier,
    DiscordNotifier,
    CompositeNotifier,
)


@pytest.mark.asyncio
async def test_null_notifier_methods_are_noops():
    n = NullNotifier()
    # Each interface method awaits cleanly and returns None.
    assert await n.welcome(guild=None, hunt=None, tag_group=None, puzzle=None, channel=None) is None
    assert await n.refresh_info(guild=None, hunt=None, puzzle=None) is None
    assert await n.puzzle_solved(guild=None, hunt=None, puzzle=None) is None


def test_null_notifier_implements_interface():
    assert issubclass(NullNotifier, Notifier)


def test_discord_notifier_implements_interface():
    assert issubclass(DiscordNotifier, Notifier)


def test_composite_notifier_implements_interface():
    assert issubclass(CompositeNotifier, Notifier)


@pytest.mark.asyncio
async def test_composite_notifier_fans_out_each_method_to_all_sinks():
    # The Phase B sink seam: every interface call must reach every child notifier.
    # (Phase B registers events.publish as one such sink without reshaping this interface.)
    def _sink():
        s = MagicMock()
        s.welcome = AsyncMock()
        s.refresh_info = AsyncMock()
        s.puzzle_solved = AsyncMock()
        return s

    a, b = _sink(), _sink()
    comp = CompositeNotifier([a, b])

    await comp.welcome(guild=None, hunt=None, tag_group=None, puzzle=None, channel=None)
    await comp.refresh_info(guild=None, hunt=None, puzzle=None)
    await comp.puzzle_solved(guild=None, hunt=None, puzzle=None)

    for s in (a, b):
        s.welcome.assert_awaited_once()
        s.refresh_info.assert_awaited_once()
        s.puzzle_solved.assert_awaited_once()


@pytest.mark.asyncio
async def test_composite_notifier_with_no_sinks_is_a_noop():
    comp = CompositeNotifier([])
    assert await comp.refresh_info(guild=None, hunt=None, puzzle=None) is None


# ---- forum guidelines sync on refresh ----

@pytest.mark.asyncio
async def test_discord_refresh_info_syncs_forum_guidelines():
    # In a forum-mode hunt, refreshing a meta's pinned info must also re-sync the
    # forum guidelines/topic so a rename or solve keeps the rules in step.
    cog = MagicMock()
    cog.send_initial_metapuzzle_channel_messages = AsyncMock()
    bot = MagicMock(); bot.get_cog.return_value = cog
    n = DiscordNotifier(bot)

    guild = MagicMock(); guild.get_channel_or_thread.return_value = MagicMock()
    hunt = MagicMock(); hunt.layout_mode = "forum"
    puzzle = MagicMock(); puzzle.is_target = True; puzzle.channel_id = 50; puzzle.id = 9
    puzzle.tags = [1]
    group = MagicMock(); group.target_puzzle_id = 9; group.type = "Metapuzzle"

    sync = AsyncMock()
    with patch("bot.store.TagGroupJsonDb") as tdb, \
         patch("bot.core.forum_config.sync_forum_guidelines", sync):
        tdb.get_by_attr.return_value = group
        await n.refresh_info(guild, hunt, puzzle)

    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_discord_refresh_info_skips_guidelines_for_channel_hunt():
    # Channel-mode hunts have no forum guidelines to sync.
    cog = MagicMock()
    cog.send_initial_metapuzzle_channel_messages = AsyncMock()
    bot = MagicMock(); bot.get_cog.return_value = cog
    n = DiscordNotifier(bot)

    guild = MagicMock(); guild.get_channel_or_thread.return_value = MagicMock()
    hunt = MagicMock(); hunt.layout_mode = "channel"
    puzzle = MagicMock(); puzzle.is_target = True; puzzle.channel_id = 50; puzzle.id = 9
    puzzle.tags = [1]
    group = MagicMock(); group.target_puzzle_id = 9; group.type = "Metapuzzle"

    sync = AsyncMock()
    with patch("bot.store.TagGroupJsonDb") as tdb, \
         patch("bot.core.forum_config.sync_forum_guidelines", sync):
        tdb.get_by_attr.return_value = group
        await n.refresh_info(guild, hunt, puzzle)

    sync.assert_not_awaited()


# ---- Phase B: board_changed ----

@pytest.mark.asyncio
async def test_null_notifier_board_changed_is_noop():
    n = NullNotifier()
    assert await n.board_changed(hunt=None) is None


@pytest.mark.asyncio
async def test_composite_notifier_fans_out_board_changed():
    def _sink():
        s = MagicMock()
        s.board_changed = AsyncMock()
        return s

    a, b = _sink(), _sink()
    comp = CompositeNotifier([a, b])
    hunt = MagicMock()

    await comp.board_changed(hunt)

    a.board_changed.assert_awaited_once_with(hunt)
    b.board_changed.assert_awaited_once_with(hunt)


@pytest.mark.asyncio
async def test_composite_notifier_add_sink_registers_for_future_calls():
    sink = MagicMock()
    sink.board_changed = AsyncMock()
    comp = CompositeNotifier([])
    hunt = MagicMock()

    comp.add_sink(sink)
    await comp.board_changed(hunt)

    sink.board_changed.assert_awaited_once_with(hunt)
