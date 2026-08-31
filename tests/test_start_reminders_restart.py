"""C1: !start_reminders cannot restart.

`tasks.Loop.cancel()` is asynchronous -- the loop's task hasn't finished when
the next statement runs, so a follow-up `start()` races the still-finishing
cancel and discord.py raises RuntimeError("Task is already launched...").
Re-running `!start_reminders` to repoint reminders at a new hunt is the
natural way to use it, and it always failed. `restart()` is discord.py's
supported idiom: it cancels the running task and defers the restart to a
done-callback once cancellation completes.

`reminder_loop` is a non-data descriptor (discord.ext.tasks.Loop.__get__)
that caches a per-instance copy into the instance `__dict__` on first access,
so replacing `cog.reminder_loop` with a plain mock before it is ever accessed
is a legitimate stand-in, not a bypass of real behaviour -- the command code
only ever calls `is_running()` / `restart()` / `start()` on whatever object
that attribute holds.
"""
from unittest.mock import MagicMock

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_cog, make_ctx


def _cog(is_running):
    cog = make_cog()
    cog.get_hunt_channel = MagicMock(return_value=MagicMock())
    cog.reminder_loop = MagicMock()
    cog.reminder_loop.is_running.return_value = is_running
    return cog


@pytest.mark.asyncio
async def test_start_reminders_restarts_when_already_running():
    """LOAD-BEARING: distinguishes 'always restart' (wrong -- would also start
    a loop that isn't running yet) from 'restart only when already running'.
    """
    cog = _cog(is_running=True)
    ctx = make_ctx()

    await Puzzles.start_reminders.callback(cog, ctx)

    cog.reminder_loop.restart.assert_called_once_with(cog.get_hunt_channel.return_value)
    cog.reminder_loop.start.assert_not_called()
    cog.reminder_loop.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_start_reminders_starts_when_not_running():
    """The keeper: without it, an implementation that unconditionally calls
    restart() would pass the test above too."""
    cog = _cog(is_running=False)
    ctx = make_ctx()

    await Puzzles.start_reminders.callback(cog, ctx)

    cog.reminder_loop.start.assert_called_once_with(cog.get_hunt_channel.return_value)
    cog.reminder_loop.restart.assert_not_called()
