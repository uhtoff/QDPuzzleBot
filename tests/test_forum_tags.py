"""Phase 1 (1E): auto-apply forum post tags by kind/solved.

Local, uniquely-named helpers (the parallel runner shards by file). These FAIL until
Phase 4: bot/core/forum_config.py exposes neither desired_tags_for nor apply_forum_tags.
"""
from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_puzzle, make_round


def _grp(type_, target=9):
    g = make_round(id=1, target_puzzle_id=target)
    g.type = type_
    return g


# (group, puzzle, metameta, minimeta, expected)
_CASES = [
    (_grp("Runaround"), make_puzzle(id=9, is_target=True, solved=True),
     False, False, {"runaround", "solved"}),
    (_grp("Metapuzzle"), make_puzzle(id=9, is_target=True),
     True, False, {"metameta"}),
    (_grp("Metapuzzle"), make_puzzle(id=9, is_target=True),
     False, False, {"meta"}),
    (None, make_puzzle(id=9, is_target=False),
     False, True, {"minimeta"}),
    (_grp("Capstone"), make_puzzle(id=9, is_target=True),
     False, False, {"capstone"}),
    (None, make_puzzle(id=9, is_target=False, solved=False),
     False, False, set()),
    (None, make_puzzle(id=9, is_target=False, solved=True),
     False, False, {"solved"}),
]


@pytest.mark.parametrize("group,puzzle,metameta,minimeta,expected", _CASES)
def test_desired_tags_truth_table(group, puzzle, metameta, minimeta, expected):
    from bot.core.forum_config import desired_tags_for

    assert desired_tags_for(group, puzzle, metameta=metameta, minimeta=minimeta) == expected


FakeTag = namedtuple("FakeTag", ["name"])


@pytest.mark.asyncio
async def test_apply_forum_tags_maps_names_and_skips_noop():
    from bot.core.forum_config import apply_forum_tags

    meta_tag = FakeTag("meta")
    forum = MagicMock()
    forum.available_tags = [meta_tag, FakeTag("solved"), FakeTag("capstone")]

    thread = MagicMock(applied_tags=[], edit=AsyncMock())
    await apply_forum_tags(thread, forum, {"meta"})
    thread.edit.assert_awaited_once()
    assert thread.edit.await_args.kwargs.get("applied_tags") == [meta_tag]

    # Second call: already-applied set equals desired -> no edit.
    thread2 = MagicMock(applied_tags=[meta_tag], edit=AsyncMock())
    await apply_forum_tags(thread2, forum, {"meta"})
    thread2.edit.assert_not_awaited()
