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

from bot.core.puzzle_service import default_edge_for_group
from tests.conftest import make_hunt, make_puzzle, make_round


class TestDefaultEdgeForGroup:
    def test_bare_group_pools_none(self):
        assert default_edge_for_group(make_round(id=1, target_puzzle_id=0)) == "none"

    def test_meta_feeds(self):
        g = make_round(id=1, target_puzzle_id=9)
        g.type = "Metapuzzle"
        assert default_edge_for_group(g) == "feeds"

    def test_capstone_informs(self):
        g = make_round(id=1, target_puzzle_id=9)
        g.type = "Capstone"
        assert default_edge_for_group(g) == "informs"

    def test_targeted_unknown_type_feeds(self):
        g = make_round(id=1, target_puzzle_id=9)
        g.type = ""
        assert default_edge_for_group(g) == "feeds"


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
    async def test_promote_in_place_restructures_sheet(self, cog):
        # A bare puzzle promoted in place must restructure its tab
        # (promote_tab_to_group: header -> B1, contribution Table -> A4), else the
        # lazily-created Table hides column A and the header's first column with it.
        puz = make_puzzle(id=7)
        gsheet = MagicMock()
        gsheet.promote_tab_to_group = AsyncMock()
        cog.bot.get_cog.return_value = gsheet
        await _run_convert(cog, puz, "Metapuzzle")
        gsheet.promote_tab_to_group.assert_awaited_once_with(puz)

    @pytest.mark.asyncio
    async def test_already_grouped_does_not_restructure(self, cog):
        # Converting the TYPE of an existing group must NOT re-promote the sheet —
        # the tab is already structured.
        puz = make_puzzle(id=7)
        gsheet = MagicMock()
        gsheet.promote_tab_to_group = AsyncMock()
        cog.bot.get_cog.return_value = gsheet
        await _run_convert(cog, puz, "Capstone", existing_group=MagicMock())
        gsheet.promote_tab_to_group.assert_not_called()

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

    @pytest.mark.asyncio
    async def test_promote_in_place_tags_target_self_edge(self, cog):
        # A promoted target is a member of its own group but neither feeds nor
        # informs it — it carries a 'none' self-edge (not the 'feeds' default).
        puz = make_puzzle(id=7)
        cog.service.set_source_mode = AsyncMock()
        cog.service.bulk_re_edge = AsyncMock(return_value="flipped 0 → feeds")
        cog.service.notifier = MagicMock()
        cog.service.notifier.board_changed = AsyncMock()
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: setattr(r, "id", 42)
            mrdb.get_by_attr.return_value = None  # bare puzzle: promote in place
            mrdb.generate_uid.return_value = "GCODE1"
            mhdb.get_by_attr.return_value = make_hunt()
            await cog.service.convert_to_type(None, make_hunt(), puz, "Metapuzzle")
        mpdb.tag_puzzle_with_edge.assert_called_once_with(7, 42, "target")


class TestConvertToMeta:
    """convert_to_meta wraps a feeder in a new round; the new target's self-edge
    is 'target' (it is the thing fed, not a feeder)."""

    @pytest.mark.asyncio
    async def test_tags_target_self_edge(self):
        cog = _make_cog_convert()
        puz = make_puzzle(id=7)
        cog.service.notifier = MagicMock()
        cog.service.notifier.board_changed = AsyncMock()
        cog.service._update_forum_tags = AsyncMock()
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: setattr(r, "id", 42)
            mrdb.generate_uid.return_value = "GCODE1"
            mhdb.get_by_attr.return_value = make_hunt()
            _new_round, puzzle = await cog.service.convert_to_meta(None, make_hunt(), puz)
        mpdb.tag_puzzle_with_edge.assert_called_once_with(7, 42, "target")
        assert puzzle.has_edge(42, "target")


class TestConvertToPuzzle:
    """convert_to_feeder is renamed to convert_to_puzzle (body unchanged)."""

    def test_renamed_exists(self):
        cog = _make_cog_convert()
        assert hasattr(cog.service, "convert_to_puzzle")

    def test_old_name_removed(self):
        cog = _make_cog_convert()
        assert not hasattr(cog.service, "convert_to_feeder")


class TestConvertToGroupTeardown:
    @pytest.fixture
    def cog(self):
        return _make_cog_convert()

    async def _run(self, cog, gsheet=None):
        puz = make_puzzle(id=7, is_target=True, channel_id=555)
        group = make_round(id=42, name="Alpha", target_puzzle_id=7, category_id=999)
        group.type = "Metapuzzle"
        cog.service.set_source_mode = AsyncMock()
        cog.service.bulk_re_edge = AsyncMock(return_value="flipped 2 feeds → none")
        cog.service.notifier = MagicMock()
        cog.service.notifier.board_changed = AsyncMock()
        cog.service.notifier.refresh_group_info = AsyncMock()
        cog.service.safe_rename = AsyncMock(return_value=True)
        if gsheet is not None:
            cog.bot.get_cog.return_value = gsheet
        hunt = make_hunt()
        guild = MagicMock()
        channel = MagicMock(); channel.id = 555
        guild.get_channel.return_value = channel
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: None
            mrdb.get_by_attr.return_value = group
            mhdb.get_by_attr.return_value = hunt
            await cog.service.convert_to_type(guild, hunt, puz, "Group")
        return puz, group, mpdb, guild

    @pytest.mark.asyncio
    async def test_deletes_target_sheet(self, cog):
        gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()
        gsheet.promote_tab_to_group = AsyncMock()
        await self._run(cog, gsheet=gsheet)
        gsheet.delete_puzzle_spreadsheet.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deletes_target_record_and_clears_target(self, cog):
        _puz, group, mpdb, _guild = await self._run(cog)
        mpdb.delete.assert_called_once_with(7)
        assert group.target_puzzle_id == 0

    @pytest.mark.asyncio
    async def test_renames_channel_and_refreshes_group_pin(self, cog):
        _puz, _group, _mpdb, _guild = await self._run(cog)
        cog.service.safe_rename.assert_awaited_once()
        cog.service.notifier.refresh_group_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_group_conversion_does_not_delete_target(self, cog):
        puz = make_puzzle(id=7, is_target=True, channel_id=555)
        group = make_round(id=42, target_puzzle_id=7)
        group.type = "Metapuzzle"
        cog.service.set_source_mode = AsyncMock()
        cog.service.bulk_re_edge = AsyncMock(return_value="x")
        cog.service.notifier = MagicMock(); cog.service.notifier.board_changed = AsyncMock()
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: None
            mrdb.get_by_attr.return_value = group
            mhdb.get_by_attr.return_value = make_hunt()
            await cog.service.convert_to_type(None, make_hunt(), puz, "Capstone")
        mpdb.delete.assert_not_called()
        assert group.target_puzzle_id == 7


class TestConvertTagGroupType:
    @pytest.fixture
    def cog(self):
        return _make_cog_convert()

    @pytest.mark.asyncio
    async def test_targeted_group_delegates_to_convert_to_type(self, cog):
        target = make_puzzle(id=7, is_target=True)
        group = make_round(id=42, target_puzzle_id=7)
        cog.service.convert_to_type = AsyncMock(return_value=(group, "ok"))
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb:
            mpdb.get_by_attr.return_value = target
            g, summary = await cog.service.convert_tag_group_type(None, make_hunt(), group, "Group")
        cog.service.convert_to_type.assert_awaited_once()
        assert cog.service.convert_to_type.await_args.args[3] == "Group"
        assert summary == "ok"

    @pytest.mark.asyncio
    async def test_bare_group_to_metapuzzle_creates_meta(self, cog):
        group = make_round(id=42, name="Bag", target_puzzle_id=0, category_id=999)
        group.type = "Group"
        new_meta = make_puzzle(id=99, is_target=True)
        cog.service.create_puzzle = AsyncMock(return_value=new_meta)
        cog.service.set_source_mode = AsyncMock()
        cog.service.bulk_re_edge = AsyncMock(return_value="flipped 3 none → feeds")
        cog.service._safe_delete_channel = AsyncMock(return_value=True)
        cog.service.notifier = MagicMock(); cog.service.notifier.board_changed = AsyncMock()
        guild = MagicMock()
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.category_layout") as mcl:
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: None
            chat = MagicMock(); chat.id = 321
            mcl.group_chat_channel.return_value = chat
            g, summary = await cog.service.convert_tag_group_type(guild, make_hunt(), group, "Metapuzzle")
        cog.service.create_puzzle.assert_awaited_once()
        cog.service._safe_delete_channel.assert_awaited_once_with(guild, 321)
        assert group.target_puzzle_id == 99
        assert group.type == "Metapuzzle"

    @pytest.mark.asyncio
    async def test_bare_group_to_group_is_noop(self, cog):
        group = make_round(id=42, target_puzzle_id=0)
        group.type = "Group"
        _g, summary = await cog.service.convert_tag_group_type(None, make_hunt(), group, "Group")
        assert "already" in summary.lower()
