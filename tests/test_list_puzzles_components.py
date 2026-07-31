"""!list_puzzles renders via Components V2 rather than a chopped-up embed.

The behaviour worth pinning is the one the old embed version could not do: a
round with more than 15 puzzles stays a single list instead of being split into
"part 2"/"part 3" fields to fit the 1024-character embed field limit.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest


def _puzzle(name, *, ptype=None, solution=None, status=None):
    p = MagicMock()
    p.name = name
    p.channel_mention = f"<#{abs(hash(name)) % 10**6}>"
    p.puzzle_type = ptype
    p.solution = solution
    p.status = status
    return p


def _cog():
    from bot.cogs.puzzles import Puzzles
    cog = object.__new__(Puzzles)          # skip __init__: only two seams are used
    return cog


async def _run(cog, ctx):
    from bot.cogs.puzzles import Puzzles
    await Puzzles.list_puzzles.callback(cog, ctx)


def _sent_text(ctx):
    """Every TextDisplay string across all views sent to the channel."""
    out = []

    def walk(item):
        if isinstance(item, discord.ui.TextDisplay):
            out.append(str(item.content))
        for child in getattr(item, "children", []) or []:
            walk(child)

    for call in ctx.send.await_args_list:
        view = call.kwargs.get("view")
        if view is not None:
            for child in view.children:
                walk(child)
    return "\n".join(out)


@pytest.fixture
def ctx():
    c = MagicMock()
    c.send = AsyncMock()
    return c


async def test_renders_a_components_v2_view_not_an_embed(ctx):
    cog = _cog()
    group = MagicMock(); group.id = 7; group.name = "Alpha"
    cog.get_tag_group = MagicMock(return_value=group)
    cog.get_channel_type = MagicMock(return_value="Puzzle")
    cog.get_hunt = MagicMock(return_value=None)

    with patch("bot.cogs.puzzles.PuzzleJsonDb.get_all_from_tag_group",
               return_value=[_puzzle("aaa", ptype="meta"), _puzzle("bbb", solution="ANSWER")]):
        await _run(cog, ctx)

    assert ctx.send.await_count == 1
    kwargs = ctx.send.await_args.kwargs
    assert "embed" not in kwargs and "embeds" not in kwargs, "should no longer send an embed"
    assert isinstance(kwargs["view"], discord.ui.LayoutView)

    text = _sent_text(ctx)
    assert "Puzzles in Round Alpha" in text
    assert "aaa" in text and "type:meta" in text
    assert "sol:**ANSWER**" in text


async def test_large_round_is_not_split_into_part_fields(ctx):
    """The specific regression the embed version had: >15 puzzles produced
    'Alpha - part 2'. 20 puzzles is enough to trigger it and small enough to
    stay inside one Components V2 message."""
    cog = _cog()
    group = MagicMock(); group.id = 7; group.name = "Alpha"
    cog.get_tag_group = MagicMock(return_value=group)
    cog.get_channel_type = MagicMock(return_value="Puzzle")
    cog.get_hunt = MagicMock(return_value=None)
    puzzles = [_puzzle(f"puz{i:02d}") for i in range(20)]

    with patch("bot.cogs.puzzles.PuzzleJsonDb.get_all_from_tag_group", return_value=puzzles):
        await _run(cog, ctx)

    text = _sent_text(ctx)
    assert "part 2" not in text, "round should no longer be chopped into parts"
    for i in range(20):
        assert f"puz{i:02d}" in text, f"puzzle {i} missing from output"
    assert text.count("### Alpha") == 1, "round heading should appear exactly once"


async def test_hunt_channel_lists_every_round_not_a_website_pointer(ctx):
    """Outside a round but inside a hunt, all rounds are listed. This replaces
    the old ':x: go to the website' wall, which existed because an embed could
    not hold a whole hunt."""
    cog = _cog()
    hunt = MagicMock(); hunt.id = 3; hunt.name = "Hunt 2027"
    cog.get_tag_group = MagicMock(return_value=None)
    cog.get_channel_type = MagicMock(return_value="Hunt")
    cog.get_hunt = MagicMock(return_value=hunt)

    groups = []
    for gid, gname in [(1, "Alpha"), (2, "Beta"), (3, "Gamma")]:
        g = MagicMock(); g.id = gid; g.name = gname
        groups.append(g)
    by_group = {
        1: [_puzzle("alpha-one"), _puzzle("alpha-two")],
        2: [_puzzle("beta-one")],
        3: [_puzzle("gamma-one"), _puzzle("gamma-two")],
    }

    with patch("bot.cogs.puzzles.TagGroupJsonDb.get_all", return_value=groups), \
         patch("bot.cogs.puzzles.PuzzleJsonDb.get_all_from_tag_group",
               side_effect=lambda gid: by_group[gid]):
        await _run(cog, ctx)

    text = _sent_text(ctx)
    assert "quarantinedecrypters.com" not in text
    assert "Puzzles in Hunt 2027" in text
    # Every round, and every puzzle within it - three rounds is enough for a
    # dropped or mis-grouped round to be detectable.
    for heading in ("### Alpha", "### Beta", "### Gamma"):
        assert heading in text, f"missing {heading}"
    for name in ("alpha-one", "alpha-two", "beta-one", "gamma-one", "gamma-two"):
        assert name in text, f"missing puzzle {name}"


async def test_no_hunt_context_still_gets_the_pointer_message(ctx):
    """With no hunt resolved there is genuinely nothing to list, so the
    pointer survives."""
    cog = _cog()
    cog.get_tag_group = MagicMock(return_value=None)
    cog.get_channel_type = MagicMock(return_value="Guild")
    cog.get_hunt = MagicMock(return_value=None)

    await _run(cog, ctx)

    assert ctx.send.await_count == 1
    assert "quarantinedecrypters.com" in ctx.send.await_args.args[0]
    assert "Please go to" in ctx.send.await_args.args[0], "typo 'got to' should be fixed"
