"""#70: capstone/runaround channels must not get the generic meta welcome
verbatim — a capstone's members should be pointed at !informs, not the
!feeds_only/!feeds hints that are the wrong edge for them."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_hunt, make_puzzle, make_round


def _make_cog():
    cog = Puzzles.__new__(Puzzles)
    cog.get_puzzle_sheet = lambda ctx, puzzle, hunt: "SHEET1"
    cog._overview_url = lambda ctx, hunt, guild_data=None: "http://example.test"
    cog.service = MagicMock()
    cog.service.get_minis.return_value = []
    return cog


async def _welcome_embed(group_type):
    cog = _make_cog()
    tag_group = make_round(id=5, group_code="AB12CD")
    tag_group.type = group_type
    puzzle = make_puzzle(id=1)
    hunt = make_hunt()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock())

    await cog.send_initial_metapuzzle_channel_messages(
        None, channel, tag_group=tag_group, hunt=hunt, puzzle=puzzle,
    )
    embed = channel.send.await_args.kwargs["embeds"][0]
    fields = {f.name: f.value for f in embed.fields}
    return embed, fields


class TestCapstoneWelcome:
    @pytest.mark.asyncio
    async def test_description_names_the_capstone_not_meta(self):
        embed, _ = await _welcome_embed("Capstone")
        assert "capstone channel" in embed.description
        assert "meta channel" not in embed.description

    @pytest.mark.asyncio
    async def test_recommends_informs_not_feeds(self):
        _, fields = await _welcome_embed("Capstone")
        commands = fields["General Commands"]
        informs_pos = commands.index("!informs")
        feeds_only_pos = commands.index("!feeds_only")
        assert informs_pos < feeds_only_pos
        assert "this is the edge capstone members should use" in commands


class TestRunaroundWelcome:
    @pytest.mark.asyncio
    async def test_description_names_the_runaround_not_meta(self):
        embed, _ = await _welcome_embed("Runaround")
        assert "runaround channel" in embed.description
        assert "meta channel" not in embed.description


class TestPlainMetaWelcomeUnchanged:
    @pytest.mark.asyncio
    async def test_description_still_says_meta_channel(self):
        embed, _ = await _welcome_embed("Metapuzzle")
        assert "meta channel" in embed.description

    @pytest.mark.asyncio
    async def test_feeds_only_still_leads_for_plain_meta(self):
        _, fields = await _welcome_embed("Metapuzzle")
        commands = fields["General Commands"]
        feeds_only_pos = commands.index("!feeds_only")
        informs_pos = commands.index("!informs")
        assert feeds_only_pos < informs_pos
