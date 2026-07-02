"""!change_solution and !add_solution share one implementation (#80): both were
independently-copied ~30-line tails that had already drifted (add_solution's
embed was missing the Follow-up field the other three solve variants include).
Also covers #79: the "PUZZLE SOLVED!" embed is now built by one shared
bot.core.notifier.build_solved_embed formula instead of three independent
copies (bang !solve, web_post_solved_embed, and the notifier fallback).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.notifier import build_solved_embed
from tests.conftest import make_cog, make_ctx, make_puzzle


def _wire_for_embed(cog):
    cog.get_guild = lambda ctx: MagicMock()
    cog.get_guild_data = lambda ctx: MagicMock(discord_bot_emoji=":x:")
    cog.info = AsyncMock()
    cog._warn_stale_sheets = AsyncMock()
    return cog


class TestChangeOrAddSolutionImpl:
    @pytest.mark.asyncio
    async def test_change_mode_posts_updated_embed_with_follow_up(self):
        puzzle = make_puzzle(id=1, solved=True, solution="NEW")
        cog = _wire_for_embed(make_cog(puzzle=puzzle))
        cog.service.change_solution = AsyncMock()
        ctx = make_ctx()

        await cog._change_or_add_solution_impl(ctx, "new answer", mode="change")

        cog.service.change_solution.assert_awaited_once()
        embed = ctx.send.call_args.kwargs["embed"]
        assert embed.title == "PUZZLE SOLUTION UPDATED!"
        assert any(f.name == "Follow-up" for f in embed.fields)
        cog.info.assert_awaited_once_with(ctx, update=True)

    @pytest.mark.asyncio
    async def test_add_mode_posts_added_embed_with_follow_up(self):
        """Regression for #80: add_solution's embed previously omitted the
        Follow-up field that change_solution/_solve_impl/_partial_impl all have."""
        puzzle = make_puzzle(id=1, solved=True, solution="OLD/EXTRA")
        cog = _wire_for_embed(make_cog(puzzle=puzzle))
        cog.service.add_solution = AsyncMock()
        ctx = make_ctx()

        await cog._change_or_add_solution_impl(ctx, "extra answer", mode="add")

        cog.service.add_solution.assert_awaited_once()
        embed = ctx.send.call_args.kwargs["embed"]
        assert embed.title == "EXTRA PUZZLE SOLUTION ADDED!"
        assert any(f.name == "Follow-up" for f in embed.fields)
        cog.info.assert_awaited_once_with(ctx, update=True)

    @pytest.mark.asyncio
    async def test_change_mode_falls_back_to_solve_impl_when_unsolved(self):
        puzzle = make_puzzle(id=1, solved=False)
        cog = _wire_for_embed(make_cog(puzzle=puzzle))
        cog._solve_impl = AsyncMock()
        cog._partial_impl = AsyncMock()
        ctx = make_ctx()

        await cog._change_or_add_solution_impl(ctx, "answer", mode="change")

        cog._solve_impl.assert_awaited_once()
        cog._partial_impl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_mode_falls_back_to_partial_impl_when_unsolved(self):
        puzzle = make_puzzle(id=1, solved=False)
        cog = _wire_for_embed(make_cog(puzzle=puzzle))
        cog._solve_impl = AsyncMock()
        cog._partial_impl = AsyncMock()
        ctx = make_ctx()

        await cog._change_or_add_solution_impl(ctx, "answer", mode="add")

        cog._partial_impl.assert_awaited_once()
        cog._solve_impl.assert_not_awaited()


class TestBuildSolvedEmbedShared:
    def test_title_and_description(self):
        puzzle = make_puzzle(id=1, solution="BLUE WHALE")
        embed = build_solved_embed(":tada:", puzzle, "custom follow-up")
        assert embed.title == "PUZZLE SOLVED!"
        assert "BLUE WHALE" in embed.description
        assert ":tada:" in embed.description
        assert embed.fields[0].name == "Follow-up"
        assert embed.fields[0].value == "custom follow-up"

    @pytest.mark.asyncio
    async def test_web_post_solved_embed_uses_shared_builder(self):
        from bot.cogs.puzzles import Puzzles

        puzzle = make_puzzle(id=2, channel_id=111, solution="ANS")
        channel = MagicMock()
        channel.send = AsyncMock()
        channel.category = None
        guild = MagicMock()
        guild.get_channel_or_thread.return_value = channel

        cog = Puzzles.__new__(Puzzles)
        cog.web_refresh_info = AsyncMock()

        with patch("bot.cogs.puzzles.GuildSettingsDb") as gsdb:
            gsdb.get.return_value = MagicMock(discord_bot_emoji="")
            await cog.web_post_solved_embed(guild, MagicMock(), puzzle)

        embed = channel.send.call_args.kwargs["embed"]
        assert embed.title == "PUZZLE SOLVED!"
        assert embed.fields[0].value == "Solved from the dashboard. Tidying up the sheets…"
