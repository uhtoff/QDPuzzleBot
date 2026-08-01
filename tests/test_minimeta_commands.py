"""Tests for M8 minimeta bang commands."""
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import pytest

from tests.conftest import make_cog, make_ctx, make_puzzle
from bot.cogs.puzzles import Puzzles


def _make_cog_with_info(puzzle, hunt=None):
    """Like make_cog but also stubs cog.info (used in TestPinRefresh)."""
    cog = make_cog(puzzle, hunt)
    cog.info = AsyncMock()
    return cog


class TestMakeMiniMeta:
    @pytest.mark.asyncio
    async def test_sets_puzzle_type_and_confirms(self):
        puzzle = make_puzzle(id=1)
        cog = _make_cog_with_info(puzzle)
        ctx = make_ctx()
        cog.service.make_minimeta = AsyncMock()

        await Puzzles.make_minimeta.callback(cog, ctx)

        cog.service.make_minimeta.assert_called_once_with(puzzle)
        ctx.send.assert_called_once()
        assert "minimeta" in ctx.send.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_refreshes_pinned_info(self):
        """#12: the pin must refresh, or it keeps showing the pre-minimeta state."""
        puzzle = make_puzzle(id=1)
        cog = _make_cog_with_info(puzzle)
        ctx = make_ctx()
        cog.service.make_minimeta = AsyncMock()

        await Puzzles.make_minimeta.callback(cog, ctx)

        cog.info.assert_awaited_once_with(ctx, update=True)


class TestRemoveMiniMeta:
    @pytest.mark.asyncio
    async def test_removes_and_confirms(self):
        puzzle = make_puzzle(id=1, puzzle_type="Minimeta")
        hunt = MagicMock()
        cog = _make_cog_with_info(puzzle, hunt)
        ctx = make_ctx()
        cog.service.remove_minimeta = AsyncMock()

        await Puzzles.remove_minimeta.callback(cog, ctx)

        cog.service.remove_minimeta.assert_called_once_with(ctx.guild, hunt, puzzle)
        ctx.send.assert_called_once()
        assert "minimeta" in ctx.send.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_refreshes_pinned_info(self):
        """#12: the pin must refresh, or it keeps showing dead mini links/Type."""
        puzzle = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog_with_info(puzzle, MagicMock())
        ctx = make_ctx()
        cog.service.remove_minimeta = AsyncMock()

        await Puzzles.remove_minimeta.callback(cog, ctx)

        cog.info.assert_awaited_once_with(ctx, update=True)

    @pytest.mark.asyncio
    async def test_not_minimeta_reports_error(self):
        puzzle = make_puzzle(id=1, puzzle_type="Cryptic")
        cog = _make_cog_with_info(puzzle, MagicMock())
        ctx = make_ctx()
        cog.service.remove_minimeta = AsyncMock(side_effect=RuntimeError("not_minimeta"))

        await Puzzles.remove_minimeta.callback(cog, ctx)

        ctx.send.assert_called_once()
        assert "not a minimeta" in ctx.send.call_args[0][0].lower()
        cog.info.assert_not_awaited()


class TestListMinis:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        puzzle = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = make_cog(puzzle)
        ctx = make_ctx()
        cog.service.get_minis = MagicMock(return_value=[])

        await Puzzles.list_minis.callback(cog, ctx)

        ctx.send.assert_called_once()
        assert "no mini" in ctx.send.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_shows_minis_with_solve_state(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        mini1 = make_puzzle(id=2, name="Mini A", solved=False)
        mini2 = make_puzzle(id=3, name="Mini B", solved=True, solution="ANS")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_minis = MagicMock(return_value=[mini1, mini2])

        await Puzzles.list_minis.callback(cog, ctx)

        ctx.send.assert_called_once()
        # The embed should mention both mini names in its description
        embed = ctx.send.call_args.kwargs["embed"]
        assert "Mini A" in embed.description
        assert "Mini B" in embed.description


class TestSolveMiniCommand:
    @pytest.mark.asyncio
    async def test_delegates_to_service(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog_with_info(parent)
        ctx = make_ctx()
        cog.service.solve_mini_by_number = AsyncMock(return_value=True)

        await Puzzles.solve_mini.callback(cog, ctx, 2, "ANSWER")

        cog.service.solve_mini_by_number.assert_called_once()
        args = cog.service.solve_mini_by_number.call_args[0]
        assert args[3] == 2
        assert args[4] == "ANSWER"
        ctx.send.assert_called_once()
        assert "#2" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_bad_number_sends_error(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.solve_mini_by_number = AsyncMock(side_effect=ValueError("No mini #9"))

        await Puzzles.solve_mini.callback(cog, ctx, 9, "X")

        ctx.send.assert_called_once()
        assert ":x:" in ctx.send.call_args[0][0]


class TestUnsolveMiniCommand:
    @pytest.mark.asyncio
    async def test_delegates_to_service(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog_with_info(parent)
        ctx = make_ctx()
        cog.service.unsolve_mini_by_number = AsyncMock()

        await Puzzles.unsolve_mini.callback(cog, ctx, 1)

        cog.service.unsolve_mini_by_number.assert_called_once()
        ctx.send.assert_called_once()


class TestPinRefresh:
    """add_mini / solve_mini / unsolve_mini must refresh the minimeta's pinned
    info (mirroring add_sheet) so the mini list in the pin stays current."""

    @pytest.mark.asyncio
    async def test_add_mini_refreshes_pin(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog_with_info(parent)
        ctx = make_ctx()
        mini = make_puzzle(id=2, name="Mini A", channel_id=9001)
        cog.service.create_mini = AsyncMock(return_value=mini)

        await Puzzles.add_mini.callback(cog, ctx, "Mini A")

        cog.info.assert_awaited_once()
        assert cog.info.call_args.kwargs.get("update") is True

    @pytest.mark.asyncio
    async def test_solve_mini_refreshes_pin(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog_with_info(parent)
        ctx = make_ctx()
        cog.service.solve_mini_by_number = AsyncMock(return_value=True)

        await Puzzles.solve_mini.callback(cog, ctx, 1, "ANS")

        cog.info.assert_awaited_once()
        assert cog.info.call_args.kwargs.get("update") is True

    @pytest.mark.asyncio
    async def test_unsolve_mini_refreshes_pin(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog_with_info(parent)
        ctx = make_ctx()
        cog.service.unsolve_mini_by_number = AsyncMock()

        await Puzzles.unsolve_mini.callback(cog, ctx, 1)

        cog.info.assert_awaited_once()
        assert cog.info.call_args.kwargs.get("update") is True


class TestBuildEmbedListMinis:
    """_build_embed_list must append a Mini-puzzles embed when the puzzle has minis."""

    def _cog_with_minis(self, minis):
        cog = Puzzles.__new__(Puzzles)
        cog.service = MagicMock()
        cog.service.get_minis = MagicMock(return_value=minis)
        return cog

    def test_includes_minis_embed_when_present(self):
        import discord
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        mini1 = make_puzzle(id=2, name="Mini A", solved=False)
        mini2 = make_puzzle(id=3, name="Mini B", solved=True, solution="ANS")
        cog = self._cog_with_minis([mini1, mini2])

        embeds = cog._build_embed_list(None, discord.Embed(title="x"), parent, MagicMock())

        text = " ".join(f.value for e in embeds for f in e.fields)
        assert "Mini A" in text
        assert "Mini B" in text

    def test_no_minis_embed_when_none(self):
        import discord
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = self._cog_with_minis([])

        embeds = cog._build_embed_list(None, discord.Embed(title="x"), parent, MagicMock())

        assert len(embeds) == 1  # just the main embed


class TestCreateMiniListLinks:
    """create_mini_list rows should link to both the mini's thread and its sheet."""

    def _cog(self):
        cog = Puzzles.__new__(Puzzles)
        cog.service = MagicMock()
        return cog

    def test_links_thread_and_sheet_when_hunt_given(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        mini = make_puzzle(id=2, name="Mini A", channel_id=9001)
        mini.google_page_id = "777"
        hunt = MagicMock()
        hunt.google_sheet_id = "SHEETID"

        text = self._cog().create_mini_list(parent, [mini], hunt)

        # Thread jump-link (channel mention) and a hyperlinked sheet tab.
        assert "<#9001>" in text
        assert "gid=777" in text
        assert "[sheet]" in text

    def test_no_sheet_link_without_hunt(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        mini = make_puzzle(id=2, name="Mini A", channel_id=9001)
        mini.google_page_id = "777"

        text = self._cog().create_mini_list(parent, [mini])

        assert "<#9001>" in text  # thread link needs no hunt
        assert "[sheet]" not in text  # sheet link omitted when hunt absent


class TestMoveToSolvedThread:
    """move_to_solved must not crash on a thread (Thread.edit has no position=);
    a mini-puzzle thread is archived instead of repositioned."""

    @pytest.mark.asyncio
    async def test_thread_archived_not_repositioned(self):
        cog = Puzzles.__new__(Puzzles)
        ctx = MagicMock()
        ctx.channel = MagicMock(spec=discord.Thread)
        ctx.channel.edit = AsyncMock()
        ctx.send = AsyncMock()

        await Puzzles.move_to_solved.callback(cog, ctx)

        ctx.channel.edit.assert_awaited_once_with(archived=True)
        # The crashing call passed position=...; it must never be used on a thread.
        assert "position" not in ctx.channel.edit.call_args.kwargs


class TestWebPostSolvedEmbedThread:
    """Dashboard solve of a mini must post the SOLVED embed in its thread
    (resolved via get_channel_or_thread) and never try to reposition it."""

    @pytest.mark.asyncio
    async def test_posts_embed_to_thread_and_skips_reposition(self):
        puzzle = make_puzzle(id=2, name="Mini A", channel_id=9001, solved=True, solution="ANS")
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        thread.edit = AsyncMock()
        guild = MagicMock()
        guild.get_channel_or_thread = MagicMock(return_value=thread)

        cog = Puzzles.__new__(Puzzles)
        cog.web_refresh_info = AsyncMock()

        with patch("bot.cogs.puzzles.GuildSettingsDb") as gsdb:
            gsdb.get.return_value = MagicMock(discord_bot_emoji="")
            await cog.web_post_solved_embed(guild, MagicMock(), puzzle)

        # Thread resolved via get_channel_or_thread, embed posted into it.
        guild.get_channel_or_thread.assert_called_once_with(9001)
        thread.send.assert_awaited_once()
        # Never repositions a thread (Thread.edit has no position=).
        for call in thread.edit.await_args_list:
            assert "position" not in call.kwargs


class TestDeleteMiniCommand:
    """`!delete_mini` works from two contexts: the minimeta's channel (with a
    number) and the mini's own thread (without one)."""

    def _cog(self, puzzle, hunt=None):
        cog = make_cog(puzzle, hunt)
        cog._confirm_destructive = AsyncMock(return_value=True)
        cog.web_refresh_info = AsyncMock()
        cog.service.delete_mini = AsyncMock(return_value=MagicMock())
        return cog

    @pytest.mark.asyncio
    async def test_number_in_minimeta_channel_picks_that_mini(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta", channel_id=111)
        m1 = make_puzzle(id=10, name="First", parent_puzzle_id=1, channel_id=901)
        m2 = make_puzzle(id=11, name="Second", parent_puzzle_id=1, channel_id=902)
        cog = self._cog(parent)
        cog.service.get_minis.return_value = [m1, m2]
        ctx = make_ctx(channel_id=111)

        await Puzzles.delete_mini.callback(cog, ctx, 2)

        # 1-indexed against !list_minis; picking the first would be off-by-one.
        assert cog.service.delete_mini.await_args[0][2] is m2

    @pytest.mark.asyncio
    async def test_in_mini_thread_deletes_that_mini_without_a_number(self):
        mini = make_puzzle(id=10, name="Word Search", parent_puzzle_id=1, channel_id=901)
        cog = self._cog(mini)
        ctx = make_ctx(channel_id=901)
        ctx.channel.parent.send = AsyncMock()

        await Puzzles.delete_mini.callback(cog, ctx, None)

        assert cog.service.delete_mini.await_args[0][2] is mini
        cog.service.get_minis.assert_not_called()

    @pytest.mark.asyncio
    async def test_thread_report_goes_to_the_parent_channel(self):
        """The thread is deleted with the mini, so a ctx.send confirmation would
        be posted into a dead channel."""
        mini = make_puzzle(id=10, parent_puzzle_id=1, channel_id=901)
        cog = self._cog(mini)
        ctx = make_ctx(channel_id=901)
        parent_channel = MagicMock()
        parent_channel.send = AsyncMock()
        ctx.channel.parent = parent_channel

        await Puzzles.delete_mini.callback(cog, ctx, None)

        parent_channel.send.assert_awaited_once()
        assert "Deleted mini-puzzle" in parent_channel.send.call_args[0][0]
        # ctx.send only carried the confirm prompt, which is stubbed out here.
        ctx.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_minimeta_channel_report_stays_in_that_channel(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta", channel_id=111)
        mini = make_puzzle(id=10, parent_puzzle_id=1, channel_id=901)
        cog = self._cog(parent)
        cog.service.get_minis.return_value = [mini]
        ctx = make_ctx(channel_id=111)

        await Puzzles.delete_mini.callback(cog, ctx, 1)

        ctx.send.assert_awaited_once()
        assert "Deleted mini-puzzle" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_refreshes_the_parent_pin(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta", channel_id=111)
        mini = make_puzzle(id=10, parent_puzzle_id=1, channel_id=901)
        minimeta_returned = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = self._cog(parent)
        cog.service.get_minis.return_value = [mini]
        cog.service.delete_mini = AsyncMock(return_value=minimeta_returned)
        ctx = make_ctx(channel_id=111)

        await Puzzles.delete_mini.callback(cog, ctx, 1)

        # The pin lives on the MINIMETA, not the deleted mini.
        assert cog.web_refresh_info.await_args[0][2] is minimeta_returned

    @pytest.mark.asyncio
    async def test_cancelled_confirmation_deletes_nothing(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta", channel_id=111)
        cog = self._cog(parent)
        cog.service.get_minis.return_value = [make_puzzle(id=10, parent_puzzle_id=1)]
        cog._confirm_destructive = AsyncMock(return_value=False)
        ctx = make_ctx(channel_id=111)

        await Puzzles.delete_mini.callback(cog, ctx, 1)

        cog.service.delete_mini.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_out_of_range_number_is_rejected(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta", channel_id=111)
        cog = self._cog(parent)
        cog.service.get_minis.return_value = [make_puzzle(id=10, parent_puzzle_id=1)]
        ctx = make_ctx(channel_id=111)

        await Puzzles.delete_mini.callback(cog, ctx, 5)

        cog.service.delete_mini.assert_not_awaited()
        assert "No mini #5" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_missing_number_in_minimeta_channel_is_rejected(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta", channel_id=111)
        cog = self._cog(parent)
        ctx = make_ctx(channel_id=111)

        await Puzzles.delete_mini.callback(cog, ctx, None)

        cog.service.delete_mini.assert_not_awaited()
        assert "list_minis" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_number_inside_a_mini_thread_is_rejected(self):
        """Ambiguous: a number there could mean a sibling. Refuse rather than guess."""
        mini = make_puzzle(id=10, parent_puzzle_id=1, channel_id=901)
        cog = self._cog(mini)
        ctx = make_ctx(channel_id=901)

        await Puzzles.delete_mini.callback(cog, ctx, 2)

        cog.service.delete_mini.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_a_puzzle_channel_is_rejected(self):
        cog = self._cog(None)
        ctx = make_ctx(channel_id=555)

        await Puzzles.delete_mini.callback(cog, ctx, 1)

        cog.service.delete_mini.assert_not_awaited()
