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

from bot.core.puzzle_service import PuzzleService, default_edge_for_group
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
        cog.service.safe_rename = AsyncMock(return_value=True)
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
        # create_puzzle returned a puzzle with its own channel (id 111) != the
        # general channel (321) — the fresh-channel branch retires the leftover.
        cog.service._safe_delete_channel.assert_awaited_once_with(guild, 321)
        assert group.target_puzzle_id == 99
        assert group.type == "Metapuzzle"

    @pytest.mark.asyncio
    async def test_bare_group_promote_adopts_existing_channel(self, cog):
        # The common path: create_puzzle reuses the general channel by name and
        # leaves the meta unlinked (channel_id 0). Promote adopts that channel onto
        # the meta and swaps its pin to the meta welcome — no delete, no race.
        group = make_round(id=42, name="Bag", target_puzzle_id=0, category_id=999)
        group.type = "Group"
        new_meta = make_puzzle(id=99, is_target=True, channel_id=0)
        cog.service.create_puzzle = AsyncMock(return_value=new_meta)
        cog.service.set_source_mode = AsyncMock()
        cog.service.bulk_re_edge = AsyncMock(return_value="flipped 3 none → feeds")
        cog.service._safe_delete_channel = AsyncMock(return_value=True)
        cog.service.safe_rename = AsyncMock(return_value=True)
        cog.service.notifier = MagicMock()
        cog.service.notifier.board_changed = AsyncMock()
        cog.service.notifier.refresh_info = AsyncMock()
        guild = MagicMock()
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.category_layout") as mcl:
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: None
            chat = MagicMock(); chat.id = 321; chat.name = "bag"; chat.mention = "<#321>"
            mcl.group_chat_channel.return_value = chat
            await cog.service.convert_tag_group_type(guild, make_hunt(), group, "Metapuzzle")
        assert new_meta.channel_id == 321                       # adopted in place
        cog.service._safe_delete_channel.assert_not_awaited()   # nothing to retire
        cog.service.notifier.refresh_info.assert_awaited_once()  # pin -> meta welcome
        assert group.target_puzzle_id == 99

    @pytest.mark.asyncio
    async def test_bare_group_promote_prerenames_chat_to_glyph_and_adopts(self, cog):
        # Regression: create_puzzle now computes a GLYPHED channel name, so the
        # bare-named general chat ("bag") no longer matches unless we pre-rename it
        # to the glyphed name ("🧩-bag") first. Without the pre-rename, create_puzzle
        # would spawn a fresh 🧩-bag channel and we'd wrongly DELETE the chat +
        # history. Assert we (a) pre-rename the chat to the glyph and (b) do NOT
        # retire it.
        group = make_round(id=42, name="Bag", target_puzzle_id=0, category_id=999)
        group.type = "Group"
        # create_puzzle returns the meta unlinked (channel_id 0 => created=False
        # adopt case), simulating get_or_create_channel matching the renamed chat.
        new_meta = make_puzzle(id=99, is_target=True, channel_id=0)
        cog.service.create_puzzle = AsyncMock(return_value=new_meta)
        cog.service.set_source_mode = AsyncMock()
        cog.service.bulk_re_edge = AsyncMock(return_value="flipped 3 none → feeds")
        cog.service._safe_delete_channel = AsyncMock(return_value=True)
        cog.service.safe_rename = AsyncMock(return_value=True)
        cog.service.notifier = MagicMock()
        cog.service.notifier.board_changed = AsyncMock()
        cog.service.notifier.refresh_info = AsyncMock()
        guild = MagicMock()
        chat = MagicMock(); chat.id = 321; chat.name = "bag"; chat.mention = "<#321>"
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.category_layout") as mcl, \
             patch.object(PuzzleService, "_is_metameta_group", return_value=False):
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: None
            mcl.group_chat_channel.return_value = chat
            await cog.service.convert_tag_group_type(guild, make_hunt(), group, "Metapuzzle")
        # A promoted bare Group owns its category, so it's a primary target -> starred.
        cog.service.safe_rename.assert_awaited_once_with(chat, "⭐🧩-bag")  # pre-rename
        cog.service._safe_delete_channel.assert_not_awaited()            # adopted, not retired
        assert new_meta.channel_id == 321
        assert group.target_puzzle_id == 99

    @pytest.mark.asyncio
    async def test_bare_group_to_group_is_noop(self, cog):
        group = make_round(id=42, target_puzzle_id=0)
        group.type = "Group"
        _g, summary = await cog.service.convert_tag_group_type(None, make_hunt(), group, "Group")
        assert "already" in summary.lower()


class TestConvertToPuzzleSplitRound:
    """Demoting the meta of a SPLIT round deletes the tag_group row, and the
    join rows mapping its '— solved'/'(N)' categories FK-cascade off it — the
    Discord footprint must be handled BEFORE the row delete (same ordering fix
    as delete_round/delete_hunt), relocating member channels out and tearing
    the categories down instead of leaving N untracked orphan categories."""

    @pytest.mark.asyncio
    async def test_split_round_relocates_members_and_tears_down_before_row_delete(self):
        from bot.core.puzzle_service import PuzzleService

        bot = MagicMock(); bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        svc.safe_rename = AsyncMock()
        guild = MagicMock()
        hunt = make_hunt(id=10, category_id=500)
        meta = make_puzzle(id=1, is_target=True, channel_id=901, tags=[5])
        feeder = make_puzzle(id=2, channel_id=902, tags=[5])
        group = make_round(id=5, category_id=100, target_puzzle_id=1)

        meta_ch = MagicMock(id=901); meta_ch.category_id = 100
        feeder_ch = MagicMock(id=902); feeder_ch.category_id = 101
        guild.get_channel = MagicMock(
            side_effect=lambda cid: {901: meta_ch, 902: feeder_ch}.get(cid))

        order = []
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
             patch("bot.core.puzzle_service.TagGroupCategoryDb"), \
             patch("bot.core.puzzle_service.category_layout.is_split",
                   return_value=True), \
             patch("bot.core.puzzle_service.category_layout.group_categories",
                   return_value=[MagicMock(category_id=100),
                                 MagicMock(category_id=101)]), \
             patch("bot.core.puzzle_service.category_layout.delete_group_categories",
                   AsyncMock(side_effect=lambda *a, **k: order.append("teardown") or True)
                   ) as teardown, \
             patch.object(svc, "_relocate_kept_puzzle",
                          AsyncMock(return_value=True)) as reloc:
            tgdb.get_by_attr.return_value = group
            tgdb.delete = MagicMock(side_effect=lambda gid: order.append("row_delete"))
            pdb.get_all_from_tag_group.return_value = [meta, feeder]

            await svc.convert_to_puzzle(guild, hunt, meta)

        assert reloc.await_count == 2                 # the meta's channel + the feeder's
        teardown.assert_awaited_once()
        assert order == ["teardown", "row_delete"]    # categories before the FK cascade

    @pytest.mark.asyncio
    async def test_single_round_demote_leaves_category_alone(self):
        """Legacy (unsplit) rounds keep today's behaviour: the category and all
        channels stay physically in place; only the row is deleted."""
        from bot.core.puzzle_service import PuzzleService

        bot = MagicMock(); bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        svc.safe_rename = AsyncMock()
        hunt = make_hunt(id=10, category_id=500)
        meta = make_puzzle(id=1, is_target=True, channel_id=901, tags=[5])
        group = make_round(id=5, category_id=100, target_puzzle_id=1)

        with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
             patch("bot.core.puzzle_service.TagGroupCategoryDb"), \
             patch("bot.core.puzzle_service.category_layout.is_split",
                   return_value=False), \
             patch("bot.core.puzzle_service.category_layout.delete_group_categories",
                   AsyncMock()) as teardown, \
             patch.object(svc, "_relocate_kept_puzzle", AsyncMock()) as reloc:
            tgdb.get_by_attr.return_value = group
            pdb.get_all_from_tag_group.return_value = [meta]

            await svc.convert_to_puzzle(MagicMock(), hunt, meta)

        reloc.assert_not_awaited()
        teardown.assert_not_awaited()
        tgdb.delete.assert_called_once_with(5)


class TestConvertBangGuards:
    """!convert_to_capstone / !convert_to_runaround accept a REGULAR puzzle
    (promoted in place by convert_to_type, like !convert_to_meta) and solved
    puzzles; the meta -> capstone path is unchanged."""

    def _cog_with(self, puzzle):
        cog = _make_cog_convert()
        cog.get_puzzle = MagicMock(return_value=puzzle)
        cog.get_hunt = MagicMock(return_value=make_hunt())
        cog.set_tag_group = MagicMock()
        cog.set_channel_type = MagicMock()
        cog.info = AsyncMock()
        cog.service = MagicMock()
        cog.service.convert_to_type = AsyncMock(
            return_value=(make_round(id=42, group_code="CAP123"), "ok"))
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel.send = AsyncMock()
        return cog, ctx

    @pytest.mark.asyncio
    async def test_capstone_allows_regular_puzzle(self):
        puz = make_puzzle(id=7, channel_id=555, is_target=False)
        cog, ctx = self._cog_with(puz)
        await cog.convert_to_capstone.callback(cog, ctx)
        cog.service.convert_to_type.assert_awaited_once()
        assert cog.service.convert_to_type.call_args.args[-1] == "Capstone"

    @pytest.mark.asyncio
    async def test_runaround_allows_regular_puzzle(self):
        puz = make_puzzle(id=7, channel_id=555, is_target=False)
        cog, ctx = self._cog_with(puz)
        await cog.convert_to_runaround.callback(cog, ctx)
        cog.service.convert_to_type.assert_awaited_once()
        assert cog.service.convert_to_type.call_args.args[-1] == "Runaround"

    @pytest.mark.asyncio
    async def test_capstone_allows_solved_puzzle(self):
        puz = make_puzzle(id=7, channel_id=555, is_target=False, solved=True)
        cog, ctx = self._cog_with(puz)
        await cog.convert_to_capstone.callback(cog, ctx)
        cog.service.convert_to_type.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_capstone_still_works_on_a_meta(self):
        puz = make_puzzle(id=7, channel_id=555, is_target=True)
        cog, ctx = self._cog_with(puz)
        await cog.convert_to_capstone.callback(cog, ctx)
        cog.service.convert_to_type.assert_awaited_once()


class TestTeardownGroupTargetRefreshesOtherGroups:
    """Converting a targeted group to a bare Group deletes the target puzzle,
    which cascades its tags rows — including any feeds/informs edges to OTHER
    groups (the metameta case). Those groups' contribution tables then carry a
    row for a puzzle that no longer exists until some unrelated refresh clears
    it. delete_round and delete_puzzle both refresh the other groups; this path
    did not."""

    @pytest.mark.asyncio
    async def test_other_fed_groups_are_refreshed(self):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        hunt = make_hunt()
        group = make_round(id=10, name="Inner")
        group.target_puzzle_id = 1
        target = make_puzzle(id=1, name="Inner Meta")
        # The target feeds its own group (10) AND an outer metameta (20).
        target.tags = [10, 20]

        outer = make_round(id=20, name="Outer")
        svc.refresh_targets = AsyncMock(return_value=[])
        svc.safe_rename = AsyncMock()
        svc.notifier = MagicMock()
        svc.notifier.refresh_group_info = AsyncMock()

        with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
             patch.object(svc, "_gsheet_cog", return_value=None):
            rdb.get_by_attr.side_effect = lambda **kw: (
                outer if kw.get("id") == 20 else group if kw.get("id") == 10 else None)
            await svc._teardown_group_target(None, hunt, group, target)
            pdb.delete.assert_called_once_with(1)

        svc.refresh_targets.assert_awaited_once()
        refreshed = svc.refresh_targets.await_args.args[1]
        ids = [g.id for g in refreshed if g is not None]
        assert 20 in ids, "the outer metameta was never told its feeder vanished"
        assert 10 not in ids, "the group being torn down must not be refreshed"


# --------------------------------------------------------------------------
# M7: a Sheets failure during the edge flip must not abort the conversion
# --------------------------------------------------------------------------

class TestConvertSurvivesEdgeFlipSheetFailure:
    """`bulk_re_edge` used to end in its own `refresh_target`, whose raw Sheets
    error propagated out of `convert_to_type` BEFORE the Group teardown and
    `set_source_mode` ran — leaving a half-converted group that "Rebuild" could
    not finish (M7). The conversion's single render now happens at the end, once
    every durable mutation is committed."""

    @pytest.fixture
    def cog(self):
        return _make_cog_convert()

    async def _run_group_teardown(self, cog):
        from googleapiclient.errors import HttpError

        puz = make_puzzle(id=7, is_target=True, channel_id=555)
        group = make_round(id=42, name="Alpha", target_puzzle_id=7, category_id=999)
        group.type = "Metapuzzle"
        # The real bulk_re_edge runs; only the sheet render fails.
        cog.service.refresh_target = AsyncMock(
            side_effect=HttpError(MagicMock(status=500), b"boom"))
        cog.service.set_source_mode = AsyncMock()
        cog.service.notifier = MagicMock()
        cog.service.notifier.board_changed = AsyncMock()
        cog.service.notifier.refresh_group_info = AsyncMock()
        cog.service.safe_rename = AsyncMock(return_value=True)
        hunt = make_hunt()
        guild = MagicMock()
        channel = MagicMock(); channel.id = 555
        guild.get_channel.return_value = channel
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
            mpdb.commit.side_effect = lambda p: None
            # A member is required: bulk_re_edge short-circuits on an empty
            # group and never reaches the sheet render at all.
            member = make_puzzle(id=8)
            member.tag_edges = {42: {"feeds"}}
            mpdb.get_all_from_tag_group.return_value = [member]
            mrdb.commit.side_effect = lambda r: None
            mrdb.get_by_attr.return_value = group
            mhdb.get_by_attr.return_value = hunt
            await cog.service.convert_to_type(guild, hunt, puz, "Group")
        return puz, group, mpdb

    @pytest.mark.asyncio
    async def test_teardown_still_runs(self, cog):
        _puz, group, mpdb = await self._run_group_teardown(cog)
        mpdb.delete.assert_called_once_with(7)
        assert group.target_puzzle_id == 0

    @pytest.mark.asyncio
    async def test_source_mode_still_set(self, cog):
        await self._run_group_teardown(cog)
        cog.service.set_source_mode.assert_awaited_once()
        assert _mode_passed(cog.service.set_source_mode) == \
            __import__("bot.core.puzzle_service", fromlist=["TYPE_DEFAULTS"]) \
            .TYPE_DEFAULTS["Group"]["default_source_mode"]
