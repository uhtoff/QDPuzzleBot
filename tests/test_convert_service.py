"""Milestone C — Phase 1 failing tests for the convert service methods.

Covers the unified ``convert_to_type(guild, hunt, puzzle, new_type) -> (group, str)``
and the rename of ``convert_to_feeder`` -> ``convert_to_puzzle``.

``convert_to_type`` derives source_mode + default edge from ``TYPE_DEFAULTS`` and
(except for Runaround) calls ``bulk_re_edge`` to flip the group's members. We stub
``set_source_mode`` and ``bulk_re_edge`` on the service and assert the dispatch.

Phase 1 expectation: these error with ``AttributeError`` (the methods do not exist
yet, and ``convert_to_puzzle`` is not yet the name). Helpers are local to this file.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_puzzle


def _make_cog_convert():
    from bot.cogs.puzzles import Puzzles

    bot = MagicMock()
    bot.get_cog.return_value = None  # no GoogleSheets cog
    return Puzzles(bot)


def _mode_passed(mock):
    """The source_mode argument from a set_source_mode(hunt, group, mode) call."""
    call = mock.call_args
    if "mode" in call.kwargs:
        return call.kwargs["mode"]
    return call.args[2]


def _new_edge_passed(mock):
    """The new_edge argument from a bulk_re_edge(group, new_edge, hunt) call."""
    call = mock.call_args
    if "new_edge" in call.kwargs:
        return call.kwargs["new_edge"]
    return call.args[1]


async def _run_convert(cog, puzzle, new_type, existing_group=None, summary="flipped 0 → x"):
    """Drive convert_to_type with the DBs patched and the edge/source-mode
    side-effects stubbed. ``existing_group=None`` exercises the promote-in-place
    (bare puzzle) path; pass a TagGroupData to exercise the already-grouped path."""
    cog.service.set_source_mode = AsyncMock()
    cog.service.bulk_re_edge = AsyncMock(return_value=summary)
    cog.service.notifier = MagicMock()
    cog.service.notifier.board_changed = AsyncMock()
    hunt = make_hunt()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        mpdb.commit.side_effect = lambda p: None
        mrdb.commit.side_effect = lambda r: None
        mrdb.get_by_attr.return_value = existing_group  # None -> bare puzzle path
        mrdb.generate_uid.return_value = "GCODE1"
        mhdb.get_by_attr.return_value = hunt
        return await cog.service.convert_to_type(None, hunt, puzzle, new_type)


class TestConvertToType:
    @pytest.fixture
    def cog(self):
        return _make_cog_convert()

    @pytest.mark.asyncio
    async def test_promote_in_place_creates_group(self, cog):
        puz = make_puzzle(id=7)
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
            cog.service.set_source_mode = AsyncMock()
            cog.service.bulk_re_edge = AsyncMock(return_value="flipped 0 → feeds")
            cog.service.notifier = MagicMock()
            cog.service.notifier.board_changed = AsyncMock()
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: None
            mrdb.get_by_attr.return_value = None  # bare puzzle: no existing group
            mrdb.generate_uid.return_value = "GCODE1"
            mhdb.get_by_attr.return_value = make_hunt()
            tag_group, _summary = await cog.service.convert_to_type(
                None, make_hunt(), puz, "Metapuzzle"
            )
            assert mrdb.commit.called
        assert tag_group.type == "Metapuzzle"

    @pytest.mark.asyncio
    async def test_sets_source_mode_round_for_metapuzzle(self, cog):
        puz = make_puzzle(id=7)
        await _run_convert(cog, puz, "Metapuzzle")
        assert _mode_passed(cog.service.set_source_mode) == "round"

    @pytest.mark.asyncio
    async def test_capstone_sets_source_mode_round(self, cog):
        puz = make_puzzle(id=7)
        await _run_convert(cog, puz, "Capstone")
        assert _mode_passed(cog.service.set_source_mode) == "round"

    @pytest.mark.asyncio
    async def test_runaround_sets_source_mode_subtree(self, cog):
        puz = make_puzzle(id=7)
        await _run_convert(cog, puz, "Runaround")
        assert _mode_passed(cog.service.set_source_mode) == "subtree"

    @pytest.mark.asyncio
    async def test_capstone_calls_bulk_re_edge_informs(self, cog):
        puz = make_puzzle(id=7)
        await _run_convert(cog, puz, "Capstone")
        cog.service.bulk_re_edge.assert_called_once()
        assert _new_edge_passed(cog.service.bulk_re_edge) == "informs"

    @pytest.mark.asyncio
    async def test_metapuzzle_calls_bulk_re_edge_feeds(self, cog):
        puz = make_puzzle(id=7)
        await _run_convert(cog, puz, "Metapuzzle")
        cog.service.bulk_re_edge.assert_called_once()
        assert _new_edge_passed(cog.service.bulk_re_edge) == "feeds"

    @pytest.mark.asyncio
    async def test_runaround_does_not_call_bulk_re_edge(self, cog):
        puz = make_puzzle(id=7)
        await _run_convert(cog, puz, "Runaround")
        cog.service.bulk_re_edge.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_summary_string(self, cog):
        puz = make_puzzle(id=7)
        _group, summary = await _run_convert(
            cog, puz, "Metapuzzle", summary="flipped 3 feeds → informs"
        )
        assert summary == "flipped 3 feeds → informs"

    @pytest.mark.asyncio
    async def test_group_type_bulk_re_edge_none(self, cog):
        puz = make_puzzle(id=7)
        await _run_convert(cog, puz, "Group")
        cog.service.bulk_re_edge.assert_called_once()
        assert _new_edge_passed(cog.service.bulk_re_edge) == "none"


class TestConvertToPuzzle:
    """convert_to_feeder is renamed to convert_to_puzzle (body unchanged)."""

    def test_renamed_exists(self):
        cog = _make_cog_convert()
        assert hasattr(cog.service, "convert_to_puzzle")

    def test_old_name_removed(self):
        cog = _make_cog_convert()
        assert not hasattr(cog.service, "convert_to_feeder")
