"""`!update_settings voice_per_puzzle …` must actually store the flag.

The voice columns are written only by narrow setters (the generic commit skips
them so stale objects can't clear them), so the command's generic path used to
reply "Updated" while saving nothing (user report, 2026-11-08)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.store import HuntData
from tests.conftest import make_cog, make_ctx


@pytest.fixture
def env():
    cog = make_cog()
    hunt = HuntData(name="H", id=10)
    hunt.voice_per_puzzle = 0  # reads back from MySQL as int
    cog.get_settings = lambda ctx: hunt
    cog.save_settings = MagicMock()
    cog.service.notifier.board_changed = AsyncMock()
    ctx = make_ctx()
    ctx.send = AsyncMock()
    ctx.invoked_with = "update_settings"
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb:
        yield MagicMock(cog=cog, ctx=ctx, hunt=hunt, hdb=hdb)


@pytest.mark.parametrize("raw,expected", [("on", True), ("true", True), ("1", True),
                                          ("off", False), ("0", False)])
async def test_update_settings_stores_voice_per_puzzle_through_its_setter(env, raw, expected):
    await Puzzles.update_settings.callback(env.cog, env.ctx, "voice_per_puzzle", raw)
    env.hdb.set_voice_per_puzzle.assert_called_once_with(10, expected)
    assert env.hunt.voice_per_puzzle is expected
    env.cog.save_settings.assert_not_called()
    assert env.ctx.send.await_args.args[0].startswith(
        f":white_check_mark: Updated `voice_per_puzzle={expected}`")


async def test_update_settings_rejects_a_non_boolean_voice_value(env):
    await Puzzles.update_settings.callback(env.cog, env.ctx, "voice_per_puzzle", "maybe")
    env.hdb.set_voice_per_puzzle.assert_not_called()
    assert env.ctx.send.await_args.args[0].startswith(":x:")


async def test_update_settings_refuses_voice_channel_id(env):
    await Puzzles.update_settings.callback(env.cog, env.ctx, "voice_channel_id", "123")
    env.hdb.set_voice_channel.assert_not_called()
    env.cog.save_settings.assert_not_called()
    assert "`!voice`" in env.ctx.send.await_args.args[0]
