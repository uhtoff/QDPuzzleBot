"""Tests for M8 minimeta bang commands."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import pytest

from tests.conftest import make_puzzle, make_round
from bot.cogs.puzzles import Puzzles


def _make_ctx(puzzle, channel_id=111, message_id=1):
    ctx = MagicMock()
    ctx.channel.id = channel_id
    ctx.message.id = message_id
    ctx.guild = MagicMock()
    ctx.send = AsyncMock()
    return ctx


def _make_cog(puzzle, hunt=None):
    cog = Puzzles.__new__(Puzzles)
    cog.get_puzzle = lambda ctx: puzzle
    cog.get_hunt = lambda ctx: hunt or MagicMock()
    cog.service = MagicMock()
    cog.info = AsyncMock()  # pin refresh — asserted in TestPinRefresh
    return cog


class TestMakeMiniMeta:
    @pytest.mark.asyncio
    async def test_sets_puzzle_type_and_confirms(self):
        puzzle = make_puzzle(id=1)
        cog = _make_cog(puzzle)
        ctx = _make_ctx(puzzle)
        cog.service.make_minimeta = MagicMock()

        await Puzzles.make_minimeta.callback(cog, ctx)

        cog.service.make_minimeta.assert_called_once_with(puzzle)
        ctx.send.assert_called_once()
        assert "minimeta" in ctx.send.call_args[0][0].lower()


class TestListMinis:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        puzzle = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog(puzzle)
        ctx = _make_ctx(puzzle)
        cog.service.get_minis = MagicMock(return_value=[])

        await Puzzles.list_minis.callback(cog, ctx)

        ctx.send.assert_called_once()
        assert "no mini" in ctx.send.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_shows_minis_with_solve_state(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        mini1 = make_puzzle(id=2, name="Mini A", solved=False)
        mini2 = make_puzzle(id=3, name="Mini B", solved=True, solution="ANS")
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
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
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
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
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
        cog.service.solve_mini_by_number = AsyncMock(side_effect=ValueError("No mini #9"))

        await Puzzles.solve_mini.callback(cog, ctx, 9, "X")

        ctx.send.assert_called_once()
        assert ":x:" in ctx.send.call_args[0][0]


class TestUnsolveMiniCommand:
    @pytest.mark.asyncio
    async def test_delegates_to_service(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
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
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
        mini = make_puzzle(id=2, name="Mini A", channel_id=9001)
        cog.service.create_mini = AsyncMock(return_value=mini)

        await Puzzles.add_mini.callback(cog, ctx, "Mini A")

        cog.info.assert_awaited_once()
        assert cog.info.call_args.kwargs.get("update") is True

    @pytest.mark.asyncio
    async def test_solve_mini_refreshes_pin(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
        cog.service.solve_mini_by_number = AsyncMock(return_value=True)

        await Puzzles.solve_mini.callback(cog, ctx, 1, "ANS")

        cog.info.assert_awaited_once()
        assert cog.info.call_args.kwargs.get("update") is True

    @pytest.mark.asyncio
    async def test_unsolve_mini_refreshes_pin(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
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
