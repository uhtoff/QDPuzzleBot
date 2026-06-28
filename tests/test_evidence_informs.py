from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from tests.conftest import make_hunt, make_puzzle, make_round


@pytest.mark.asyncio
async def test_link_adds_informs_edge_without_evicting_feeds():
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)
    cog.service.refresh_target = AsyncMock()
    cog.service.refresh_targets = AsyncMock(return_value=[])
    source = make_puzzle(id=8, tags=[5]); source.tag_edges = {5: {"feeds"}}
    group = make_round(id=5, target_puzzle_id=9)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        mpdb.get_by_attr.return_value = source
        mrdb.get_by_attr.return_value = group
        mpdb.tag_puzzle_with_edge.return_value = None
        mpdb.commit.return_value = None
        mhdb.get_by_attr.return_value = make_hunt()
        await cog.service.link_informs_for_evidence(
            make_hunt(), source_puzzle_id=8, target_puzzle_id=9)
    assert source.has_edge(5, "informs")
    assert source.has_edge(5, "feeds")
