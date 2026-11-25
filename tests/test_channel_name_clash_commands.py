"""!puzzle / !rename_puzzle: a channel-name clash asks Accept/Cancel, then
retries with a -N channel name on Accept and changes nothing on Cancel."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.core.errors import ChannelNameClash
from tests.conftest import make_hunt, make_puzzle


def _cog():
    cog = Puzzles(MagicMock())
    cog.check_available_space = AsyncMock(return_value=True)
    cog.service.refresh_unassigned_safe = AsyncMock()
    return cog


def _ctx(cog, hunt, puzzle=None):
    ctx = MagicMock()
    ctx.message.id = 4242
    ctx.send = AsyncMock()
    ctx.channel.send = AsyncMock()
    ctx.env = None
    cog.environment[4242] = {"channel_type": "Hunt", "hunt": hunt, "tag_group": None,
                             "puzzle": puzzle, "guild": ctx.guild, "guild_data": None,
                             "gsheet_cog": None}
    return ctx


@pytest.mark.parametrize("accept,creates", [(True, ["ask", "suffix"]), (False, ["ask"])])
async def test_bang_puzzle_clash_prompts_then_suffixes_or_cancels(accept, creates):
    cog = _cog()
    hunt = make_hunt(id=1)
    ctx = _ctx(cog, hunt)
    clash = ChannelNameClash("general", "general-1")
    cog.service.create_puzzle = AsyncMock(side_effect=[clash, None])
    cog._confirm_channel_suffix = AsyncMock(return_value=accept)
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb, \
         patch("bot.core.notifier.DiscordNotifier", MagicMock()):
        pdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.puzzle.callback(cog, ctx, arg="General")
    modes = [c.kwargs["on_channel_clash"] for c in cog.service.create_puzzle.await_args_list]
    assert modes == creates
    cog._confirm_channel_suffix.assert_awaited_once_with(ctx, clash, "created")


async def test_bang_puzzle_without_clash_never_prompts():
    cog = _cog()
    ctx = _ctx(cog, make_hunt(id=1))
    cog.service.create_puzzle = AsyncMock(return_value=None)
    cog._confirm_channel_suffix = AsyncMock()
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb, \
         patch("bot.core.notifier.DiscordNotifier", MagicMock()):
        pdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.puzzle.callback(cog, ctx, arg="Tide")
    cog.service.create_puzzle.assert_awaited_once()
    cog._confirm_channel_suffix.assert_not_awaited()


@pytest.mark.parametrize("accept,modes", [(True, ["ask", "suffix"]), (False, ["ask"])])
async def test_bang_rename_clash_prompts_then_suffixes_or_cancels(accept, modes):
    cog = _cog()
    puz = make_puzzle(id=70, name="Tide", channel_id=50)
    ctx = _ctx(cog, make_hunt(id=1), puzzle=puz)
    clash = ChannelNameClash("general", "general-1")
    cog.service.rename_puzzle = AsyncMock(side_effect=[clash, None])
    cog._confirm_channel_suffix = AsyncMock(return_value=accept)
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb:
        pdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.rename_puzzle.callback(cog, ctx, puzzle_name="General")
    got = [c.kwargs["on_channel_clash"] for c in cog.service.rename_puzzle.await_args_list]
    assert got == modes
    assert ctx.channel.send.await_count == (1 if accept else 0)   # the success line


async def test_bang_rename_prompts_outside_the_puzzle_lock_and_rechecks_duplicates():
    """The Accept/Cancel prompt must not hold the puzzle lock (it would block every
    other change to the puzzle for up to 30s), and a name taken while the prompt
    was open must still be refused."""
    from bot.core.locks import _PUZZLE_LOCKS
    cog = _cog()
    puz = make_puzzle(id=71, name="Tide", channel_id=50)
    ctx = _ctx(cog, make_hunt(id=1), puzzle=puz)
    cog.service.rename_puzzle = AsyncMock(side_effect=[ChannelNameClash("general", "general-1"), None])
    held_during_rename, held_during_prompt = [], []

    async def confirm(*_a):
        held_during_prompt.append(_PUZZLE_LOCKS[71].locked())
        pdb.check_duplicates_in_hunt.return_value = True   # taken meanwhile
        return True
    cog._confirm_channel_suffix = AsyncMock(side_effect=confirm)
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb:
        pdb.get_by_attr.return_value = puz
        pdb.check_duplicates_in_hunt.side_effect = (
            lambda *a, **k: held_during_rename.append(_PUZZLE_LOCKS[71].locked())
            or pdb.check_duplicates_in_hunt.return_value)
        pdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.rename_puzzle.callback(cog, ctx, puzzle_name="General")
    assert held_during_prompt == [False]
    assert held_during_rename == [True, True]      # both passes check under the lock
    assert cog.service.rename_puzzle.await_count == 1   # the second pass was refused
    assert "already exists" in ctx.send.await_args.args[0]
    ctx.channel.send.assert_not_awaited()
