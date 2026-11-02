"""Feeder-solve announcements: an answer change on a puzzle is announced in the
channel of every puzzle it FEEDS (a mini's minimeta included; informs-only links
stay quiet -- feeds_targets already excludes them, see test_header_links).

The service decides WHETHER and WHAT kind of change to announce (solved / added /
changed / unsolved; partials never); DiscordNotifier decides how it reads and
where it lands.
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.notifier import (
    CompositeNotifier,
    DiscordNotifier,
    NullNotifier,
    build_feeder_announcement,
)
from tests.conftest import make_hunt, make_puzzle


# ------------------------------------------------------------------ service

def _service():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None          # no GoogleSheets cog
    notifier = NullNotifier()
    notifier.feeder_answer_changed = AsyncMock()
    return PuzzleService(bot, notifier=notifier), notifier.feeder_answer_changed


@contextmanager
def _db(targets):
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tdb, \
         patch("bot.core.puzzle_service.feeds_targets", return_value=targets):
        tdb.get_by_attr.return_value = None
        pdb.get_by_attr.return_value = None
        pdb.get_all_from_hunt.return_value = []
        pdb.get_children.return_value = []
        yield


META_A = make_puzzle(id=50, name="Meta A", channel_id=500, is_target=True)
META_B = make_puzzle(id=60, name="Meta B", channel_id=600, is_target=True)


@pytest.mark.asyncio
async def test_first_solve_announces_solved_to_every_fed_target():
    svc, announce = _service()
    p = make_puzzle(id=1, tags=[1, 2])
    with _db([META_A, META_B]):
        assert await svc.solve(None, make_hunt(), p, "apple") is True
    announce.assert_awaited_once_with(None, p, [META_A, META_B], "solved", "", "APPLE")


@pytest.mark.asyncio
async def test_solve_after_partial_announces_the_full_answer_as_solved():
    svc, announce = _service()
    p = make_puzzle(id=1, tags=[1], status="partial", solution="APP")
    with _db([META_A]):
        await svc.solve(None, make_hunt(), p, "le")
    announce.assert_awaited_once_with(None, p, [META_A], "solved", "APP", "APP/LE")


@pytest.mark.asyncio
async def test_solving_an_already_solved_puzzle_announces_an_added_answer():
    svc, announce = _service()
    p = make_puzzle(id=1, tags=[1], solved=True, status="solved", solution="APPLE")
    with _db([META_A]):
        await svc.solve(None, make_hunt(), p, "pear")
    announce.assert_awaited_once_with(None, p, [META_A], "added", "APPLE", "APPLE/PEAR")


@pytest.mark.asyncio
async def test_a_puzzle_that_feeds_nothing_announces_nothing():
    svc, announce = _service()
    with _db([]):
        await svc.solve(None, make_hunt(), make_puzzle(id=1), "apple")
    announce.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_solution_and_change_solution_announce():
    svc, announce = _service()
    p = make_puzzle(id=1, tags=[1], solved=True, status="solved", solution="APPLE")
    with _db([META_A]):
        await svc.add_solution(None, make_hunt(), p, "pear")
        await svc.change_solution(None, make_hunt(), p, "plum")
    assert [c.args[3:] for c in announce.await_args_list] == [
        ("added", "APPLE", "APPLE/PEAR"),
        ("changed", "APPLE/PEAR", "PLUM"),
    ]


@pytest.mark.asyncio
async def test_unsolve_of_a_solved_puzzle_announces_the_retracted_answer():
    svc, announce = _service()
    p = make_puzzle(id=1, tags=[1], solved=True, status="solved", solution="APPLE")
    with _db([META_A]):
        await svc.unsolve(None, make_hunt(), p)
    announce.assert_awaited_once_with(None, p, [META_A], "unsolved", "APPLE", "")


@pytest.mark.asyncio
async def test_partials_stay_quiet_both_ways():
    svc, announce = _service()
    p = make_puzzle(id=1, tags=[1])
    with _db([META_A]):
        await svc.add_partial(None, make_hunt(), p, "app")
        await svc.unsolve(None, make_hunt(), p)        # clears a partial, never solved
    announce.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_announcement_never_fails_the_solve():
    svc, announce = _service()
    announce.side_effect = RuntimeError("discord down")
    p = make_puzzle(id=1, tags=[1])
    with _db([META_A]):
        assert await svc.solve(None, make_hunt(), p, "apple") is True
    assert p.solved is True


# ------------------------------------------------------------------ wording

def test_announcement_wording_per_kind():
    p = make_puzzle(id=1, name="Feeder", channel_id=111)
    assert build_feeder_announcement(p, "solved", "", "APPLE") == \
        "✅ <#111> solved: `APPLE`"
    assert build_feeder_announcement(p, "added", "APPLE", "APPLE/PEAR") == \
        "➕ <#111> has another answer: `PEAR` (now `APPLE/PEAR`)"
    assert build_feeder_announcement(p, "changed", "APPLE", "PLUM") == \
        "✏️ <#111> answer changed: `APPLE` → `PLUM`"
    assert build_feeder_announcement(p, "unsolved", "APPLE", "") == \
        "↩️ <#111> unsolved (was `APPLE`)"


def test_announcement_names_a_puzzle_with_no_channel():
    p = make_puzzle(id=1, name="Feeder", channel_id=None)
    assert build_feeder_announcement(p, "solved", "", "APPLE") == "✅ **Feeder** solved: `APPLE`"


# ------------------------------------------------------------------ discord

@pytest.mark.asyncio
async def test_discord_notifier_posts_in_each_target_channel_without_pings():
    channels = {500: MagicMock(), 600: MagicMock()}
    for ch in channels.values():
        ch.send = AsyncMock()
    channels[500].send.side_effect = RuntimeError("missing perms")   # one failure
    guild = MagicMock()
    guild.get_channel_or_thread.side_effect = channels.get
    no_channel = make_puzzle(id=70, channel_id=None)
    p = make_puzzle(id=1, channel_id=111)

    await DiscordNotifier(MagicMock()).feeder_answer_changed(
        guild, p, [META_A, no_channel, META_B], "solved", "", "APPLE")

    for ch in channels.values():
        ch.send.assert_awaited_once()
        assert ch.send.await_args.args == ("✅ <#111> solved: `APPLE`",)
        mentions = ch.send.await_args.kwargs["allowed_mentions"]
        assert (mentions.everyone, mentions.users, mentions.roles) == (False, False, False)


@pytest.mark.asyncio
async def test_discord_notifier_without_a_guild_does_nothing():
    await DiscordNotifier(MagicMock()).feeder_answer_changed(
        None, make_puzzle(id=1), [META_A], "solved", "", "APPLE")


@pytest.mark.asyncio
async def test_composite_fans_the_announcement_out():
    a, b = MagicMock(), MagicMock()
    a.feeder_answer_changed = AsyncMock()
    b.feeder_answer_changed = AsyncMock()
    p = make_puzzle(id=1)
    await CompositeNotifier([a, b]).feeder_answer_changed(None, p, [META_A], "solved", "", "X")
    for s in (a, b):
        s.feeder_answer_changed.assert_awaited_once_with(None, p, [META_A], "solved", "", "X")
