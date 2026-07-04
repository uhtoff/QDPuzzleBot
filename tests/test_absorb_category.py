"""!absorb_category — adopt a HAND-MADE category's puzzle channels into the
hunt's general split/overflow layout (the cleanup path for categories that
were split by hand before the overflow engine existed).

Service seam: PuzzleService.absorb_category(guild, hunt, category). Each
puzzle channel relocates through category_layout.move_channel_to_owner with
the hunt owner (solved-aware, auto-splitting/overflowing general); hand-made
divider channels are deleted; the category itself is deleted only once fully
emptied. Bot-managed categories are refused outright."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.discord_channels import SOLVE_DIVIDER
from tests.conftest import make_cog, make_hunt, make_puzzle


def _svc():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def _hand_category(channels):
    cat = MagicMock(id=555)
    cat.name = "Hand Split 2"
    cat.channels = list(channels)
    cat.text_channels = list(channels)
    cat.delete = AsyncMock()
    return cat


def _chan(cid, name):
    ch = MagicMock(id=cid)
    ch.name = name
    ch.delete = AsyncMock()
    return ch


@pytest.mark.asyncio
async def test_absorb_moves_puzzles_solved_aware_and_deletes_empty_category():
    svc = _svc()
    guild = MagicMock()
    hunt = make_hunt(id=10)
    ch1, ch2 = _chan(901, "puz-a"), _chan(902, "puz-b")
    cat = _hand_category([ch1, ch2])
    p1 = make_puzzle(id=1, channel_id=901, solved=True)
    p2 = make_puzzle(id=2, channel_id=902, solved=False)
    p1.hunt_id = p2.hunt_id = 10
    by_channel = {901: p1, 902: p2}

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.puzzle_service.category_layout.is_managed_category",
               MagicMock(return_value=False), create=True), \
         patch("bot.core.puzzle_service.category_layout.move_channel_to_owner",
               AsyncMock(return_value=True)) as mco:
        pdb.get_by_attr.side_effect = lambda **kw: by_channel.get(kw.get("channel_id"))

        report = await svc.absorb_category(guild, hunt, cat)

    mco.assert_any_await(guild, hunt, hcdb, ch1, True)
    mco.assert_any_await(guild, hunt, hcdb, ch2, False)
    assert report["moved"] == 2
    assert report["skipped"] == [] and report["failed"] == []
    assert report["deleted"] is True
    cat.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_absorb_refuses_bot_managed_category():
    """A category the engine already tracks (a group's/hunt's home or a
    split/overflow member) must not be absorbed — that would fight the
    layout the bot itself maintains."""
    svc = _svc()
    cat = _hand_category([_chan(901, "puz-a")])
    with patch("bot.core.puzzle_service.category_layout.is_managed_category",
               MagicMock(return_value=True), create=True), \
         patch("bot.core.puzzle_service.category_layout.move_channel_to_owner",
               AsyncMock()) as mco:
        with pytest.raises(ValueError, match="managed"):
            await svc.absorb_category(MagicMock(), make_hunt(id=10), cat)
    mco.assert_not_awaited()
    cat.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_absorb_skips_non_puzzle_and_foreign_hunt_channels_keeps_category():
    svc = _svc()
    guild = MagicMock()
    hunt = make_hunt(id=10)
    mine, chat, foreign = _chan(901, "puz-a"), _chan(902, "chit-chat"), _chan(903, "other-hunt-puz")
    cat = _hand_category([mine, chat, foreign])
    p1 = make_puzzle(id=1, channel_id=901, solved=False); p1.hunt_id = 10
    p3 = make_puzzle(id=3, channel_id=903, solved=False); p3.hunt_id = 99
    by_channel = {901: p1, 903: p3}

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb"), \
         patch("bot.core.puzzle_service.category_layout.is_managed_category",
               MagicMock(return_value=False), create=True), \
         patch("bot.core.puzzle_service.category_layout.move_channel_to_owner",
               AsyncMock(return_value=True)) as mco:
        pdb.get_by_attr.side_effect = lambda **kw: by_channel.get(kw.get("channel_id"))

        report = await svc.absorb_category(guild, hunt, cat)

    assert mco.await_count == 1                      # only the hunt's own puzzle
    assert report["moved"] == 1
    assert sorted(report["skipped"]) == ["chit-chat", "other-hunt-puz"]
    assert report["deleted"] is False
    cat.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_absorb_deletes_hand_made_divider_and_still_empties_category():
    svc = _svc()
    guild = MagicMock()
    hunt = make_hunt(id=10)
    puz = _chan(901, "puz-a")
    divider = _chan(902, SOLVE_DIVIDER)
    cat = _hand_category([puz, divider])
    p1 = make_puzzle(id=1, channel_id=901, solved=True); p1.hunt_id = 10

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb"), \
         patch("bot.core.puzzle_service.category_layout.is_managed_category",
               MagicMock(return_value=False), create=True), \
         patch("bot.core.puzzle_service.category_layout.move_channel_to_owner",
               AsyncMock(return_value=True)):
        pdb.get_by_attr.side_effect = lambda **kw: (
            p1 if kw.get("channel_id") == 901 else None)

        report = await svc.absorb_category(guild, hunt, cat)

    divider.delete.assert_awaited_once()
    assert report["deleted"] is True
    cat.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_absorb_reports_failed_moves_and_keeps_category():
    svc = _svc()
    guild = MagicMock()
    hunt = make_hunt(id=10)
    ch1 = _chan(901, "puz-a")
    cat = _hand_category([ch1])
    p1 = make_puzzle(id=1, name="puz-a", channel_id=901, solved=False)
    p1.hunt_id = 10

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb"), \
         patch("bot.core.puzzle_service.category_layout.is_managed_category",
               MagicMock(return_value=False), create=True), \
         patch("bot.core.puzzle_service.category_layout.move_channel_to_owner",
               AsyncMock(return_value=False)):
        pdb.get_by_attr.return_value = p1

        report = await svc.absorb_category(guild, hunt, cat)

    assert report["failed"] == ["puz-a"]
    assert report["deleted"] is False
    cat.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_bang_absorb_category_calls_service_and_reports():
    cog = make_cog(hunt=make_hunt(id=10))
    cog.get_guild = MagicMock(return_value=MagicMock())
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.channel.category = MagicMock(id=555)
    cog.service.absorb_category = AsyncMock(return_value={
        "moved": 3, "skipped": [], "failed": [], "deleted": True})

    await cog.absorb_category.callback(cog, ctx)

    cog.service.absorb_category.assert_awaited_once()
    assert cog.service.absorb_category.await_args.args[2] is ctx.channel.category
    ctx.send.assert_awaited()


@pytest.mark.asyncio
async def test_bang_absorb_category_requires_a_category():
    cog = make_cog(hunt=make_hunt(id=10))
    cog.get_guild = MagicMock(return_value=MagicMock())
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.channel.category = None
    cog.service.absorb_category = AsyncMock()

    await cog.absorb_category.callback(cog, ctx)

    cog.service.absorb_category.assert_not_awaited()
    ctx.send.assert_awaited()
