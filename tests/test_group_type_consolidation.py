"""Group-type consolidation (code-review finding #1).

asyncio_mode=auto (pytest.ini) -> async tests need no decorator. conftest
factories are plain functions. Patch DAOs at the cog import site
(bot.cogs.puzzles.*), mirroring tests/test_group_channel_type.py.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_cog, make_ctx, make_hunt, make_round


# --- Round is a create-time alias of Metapuzzle (keeps " - meta" suffix) -----

async def test_round_alias_creates_metapuzzle_with_suffix():
    cog = make_cog()
    cog.bot = MagicMock()                 # DiscordNotifier(self.bot) only stores bot
    cog.service.create_tag_group = AsyncMock(return_value=(make_round(), MagicMock()))
    await cog._round_impl(make_ctx(), "the-bog", "r")
    kwargs = cog.service.create_tag_group.call_args.kwargs
    assert kwargs["round_type"] == "Metapuzzle"
    assert kwargs["meta_puzzle_name"] == "the-bog - meta"


# Regression guard — PASSES on current code (group behaviour is unchanged); it
# proves !group still works after roundnometa/rnm are removed.
async def test_group_alias_creates_group_no_meta():
    cog = make_cog()
    cog.bot = MagicMock()
    cog.service.create_tag_group = AsyncMock(return_value=(make_round(), None))
    await cog._round_impl(make_ctx(), "loose-ends", "group")
    kwargs = cog.service.create_tag_group.call_args.kwargs
    assert kwargs["round_type"] == "Group"
    assert kwargs["meta_puzzle_name"] is False


def test_roundnometa_aliases_removed():
    from bot.cogs.puzzles import Puzzles
    assert "roundnometa" not in Puzzles.round.aliases
    assert "rnm" not in Puzzles.round.aliases


# --- Membership lists route the newer types correctly (the finding) ---------

async def test_delete_round_allows_capstone_channel():
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Capstone"
    cog.get_tag_group = lambda ctx: make_round(id=7)
    cog._confirm_destructive = AsyncMock(return_value=False)   # decline -> stop after the guard
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_tag_group.return_value = []
        await Puzzles.delete_round.callback(cog, ctx)
    cog._confirm_destructive.assert_awaited_once()             # guard passed (not rejected)
    assert all("main group channel" not in str(c) for c in ctx.send.call_args_list)


async def test_delete_round_bang_passes_keep_puzzles_false():
    from bot.cogs.puzzles import Puzzles
    tag_group = make_round(id=7)
    cog = make_cog(hunt=make_hunt(channel_id=999))
    cog.bot = MagicMock()
    cog.bot.get_channel.return_value = MagicMock(send=AsyncMock())
    cog.get_channel_type = lambda ctx: "Group"
    cog.get_tag_group = lambda ctx: tag_group
    cog._confirm_destructive = AsyncMock(return_value=True)
    cog.service.delete_round = AsyncMock(return_value=[])
    ctx = make_ctx()
    cog.environment = {ctx.message.id: {"puzzle": None, "tag_group": tag_group}}
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_tag_group.return_value = []
        await Puzzles.delete_round.callback(cog, ctx)
    cog.service.delete_round.assert_awaited_once()
    assert cog.service.delete_round.await_args.kwargs["keep_puzzles"] is False


def test_get_settings_routes_capstone_to_tag_group():
    cog = make_cog()
    tg, guild = object(), object()
    cog.get_channel_type = lambda ctx: "Capstone"
    cog.get_tag_group = lambda ctx: tg
    cog.get_guild_data = lambda ctx: guild
    assert cog.get_settings(make_ctx()) is tg


# --- !info routes meta-bearing types to the metapuzzle welcome --------------

async def test_info_capstone_calls_metapuzzle_handler():
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Capstone"
    cog.send_initial_metapuzzle_channel_messages = AsyncMock()
    await Puzzles.info.callback(cog, make_ctx())
    cog.send_initial_metapuzzle_channel_messages.assert_awaited_once()
