"""Plan C: the three sheet commands running in a hunt channel."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import make_ctx, make_cog, make_hunt, make_puzzle


class TestHuntSheetMutex:
    async def test_takes_the_hunt_lock_when_there_is_no_puzzle(self):
        from bot.core.locks import _HUNT_SHEET_LOCKS
        from bot.cogs.puzzles import with_hunt_sheet_mutex
        calls = []

        async def command_body(self, ctx):
            # Observed from INSIDE the critical section: asserting afterwards
            # would pass even if the lock were never taken at all.
            calls.append(_HUNT_SHEET_LOCKS[10].locked())

        command = with_hunt_sheet_mutex()(command_body)
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        cog.environment = {}
        await command(cog, make_ctx())
        assert calls == [True]
        assert not _HUNT_SHEET_LOCKS[10].locked()

    async def test_does_not_take_the_hunt_lock_when_there_is_a_puzzle(self):
        """with_puzzle_mutex already covers that case; taking both would be two
        locks for one command and a fresh deadlock surface."""
        from bot.core.locks import _HUNT_SHEET_LOCKS
        from bot.cogs.puzzles import with_hunt_sheet_mutex
        calls = []

        async def command_body(self, ctx):
            calls.append(_HUNT_SHEET_LOCKS[10].locked())

        command = with_hunt_sheet_mutex()(command_body)
        cog = make_cog(puzzle=make_puzzle(id=5, hunt_id=10), hunt=make_hunt(id=10))
        cog.environment = {1: {"puzzle": make_puzzle(id=5, hunt_id=10)}}
        await command(cog, make_ctx())
        assert calls == [False]

    async def test_runs_unlocked_when_there_is_no_hunt_either(self):
        from bot.cogs.puzzles import with_hunt_sheet_mutex
        calls = []

        async def command_body(self, ctx):
            calls.append("ran")

        command = with_hunt_sheet_mutex()(command_body)
        cog = make_cog(puzzle=None, hunt=None)
        cog.get_hunt = lambda ctx: None
        cog.environment = {}
        await command(cog, make_ctx())
        assert calls == ["ran"]

    async def test_passes_arguments_and_the_return_value_through(self):
        from bot.cogs.puzzles import with_hunt_sheet_mutex

        @with_hunt_sheet_mutex()
        async def command(self, ctx, number: int):
            return number * 2

        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        cog.environment = {}
        assert await command(cog, make_ctx(), 3) == 6
