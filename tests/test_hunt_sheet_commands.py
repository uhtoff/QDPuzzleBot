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


class TestAddSheetCommand:
    async def test_hunt_channel_creates_a_hunt_level_sheet(self):
        from bot.cogs.puzzles import Puzzles
        hunt = make_hunt(id=10)
        cog = make_cog(puzzle=None, hunt=hunt)
        cog.environment = {}
        cog.service.add_hunt_sheet = AsyncMock(
            return_value=SimpleNamespace(id=1, puzzle_name="alt text"))
        ctx = make_ctx()
        await Puzzles.add_sheet(cog, ctx, "alt", "text")
        cog.service.add_hunt_sheet.assert_awaited_once_with(hunt, "alt text")
        assert ":white_check_mark:" in ctx.send.await_args[0][0]

    async def test_puzzle_channel_is_unchanged(self):
        from bot.cogs.puzzles import Puzzles
        hunt = make_hunt(id=10)
        puzzle = make_puzzle(id=5, hunt_id=10, name="Alpha")
        cog = make_cog(puzzle=puzzle, hunt=hunt)
        cog.environment = {}
        cog.service.add_sheet = AsyncMock()
        cog.service.add_hunt_sheet = AsyncMock()
        cog.info = AsyncMock()
        await Puzzles.add_sheet(cog, make_ctx(), "grid", "2")
        cog.service.add_sheet.assert_awaited_once_with(hunt, puzzle, "grid 2")
        cog.service.add_hunt_sheet.assert_not_awaited()

    async def test_no_hunt_at_all_refuses(self):
        """Outside any hunt category there is nothing to attach a sheet to."""
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=None)
        cog.get_hunt = lambda ctx: None
        cog.environment = {}
        cog.service.add_hunt_sheet = AsyncMock()
        ctx = make_ctx()
        await Puzzles.add_sheet(cog, ctx, "alt")
        cog.service.add_hunt_sheet.assert_not_awaited()
        assert ":x:" in ctx.send.await_args[0][0]


class TestRemoveSheetCommand:
    async def test_hunt_channel_removes_by_index(self):
        from bot.cogs.puzzles import Puzzles
        hunt = make_hunt(id=10)
        cog = make_cog(puzzle=None, hunt=hunt)
        cog.environment = {}
        sheet = SimpleNamespace(id=4, puzzle_name="alt text")
        cog.service.get_hunt_sheets = MagicMock(return_value=[sheet])
        cog.service.remove_hunt_sheet_by_index = AsyncMock()
        ctx = make_ctx()
        await Puzzles.remove_sheet(cog, ctx, 1)
        cog.service.remove_hunt_sheet_by_index.assert_awaited_once_with(hunt, 1)
        assert "alt text" in ctx.send.await_args[0][0]

    async def test_hunt_channel_rejects_a_bad_index(self):
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[])
        cog.service.remove_hunt_sheet_by_index = AsyncMock()
        ctx = make_ctx()
        await Puzzles.remove_sheet(cog, ctx, 1)
        cog.service.remove_hunt_sheet_by_index.assert_not_awaited()
        assert ":x:" in ctx.send.await_args[0][0]

    async def test_names_the_sheet_the_index_actually_points_at(self):
        """Two sheets: with one, a message that always named sheets[0] would pass.
        """
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[
            SimpleNamespace(id=7, puzzle_name="first"),
            SimpleNamespace(id=2, puzzle_name="second"),
        ])
        cog.service.remove_hunt_sheet_by_index = AsyncMock()
        ctx = make_ctx()
        await Puzzles.remove_sheet(cog, ctx, 2)
        assert "second" in ctx.send.await_args[0][0]


class TestListSheetsCommand:
    async def test_hunt_channel_lists_the_hunt_sheets(self):
        from bot.cogs.puzzles import Puzzles
        hunt = make_hunt(id=10, google_sheet_id="SHEET1")
        cog = make_cog(puzzle=None, hunt=hunt)
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[
            SimpleNamespace(id=7, puzzle_name="alt text", google_page_id="P7"),
            SimpleNamespace(id=2, puzzle_name="site source", google_page_id="P2"),
        ])
        ctx = make_ctx()
        await Puzzles.list_additional_sheets(cog, ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        value = embed.fields[0].value
        # Two sheets, numbered in the order !remove_sheet will index them.
        assert "1: [alt text]" in value
        assert "2: [site source]" in value
        assert value.index("1: [alt text]") < value.index("2: [site source]")

    async def test_hunt_channel_with_no_sheets_says_so(self):
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[])
        ctx = make_ctx()
        await Puzzles.list_additional_sheets(cog, ctx)
        # Must be the "none yet" message, not the old "not a puzzle channel"
        # refusal -- a bare ":x:" check passes on either and proves nothing.
        assert "hunt sheet" in ctx.send.await_args[0][0].lower()
        cog.service.get_hunt_sheets.assert_called_once()

    async def test_links_point_at_the_hunt_spreadsheet(self):
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10, google_sheet_id="SHEET1"))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[
            SimpleNamespace(id=7, puzzle_name="alt text", google_page_id="P7"),
        ])
        ctx = make_ctx()
        await Puzzles.list_additional_sheets(cog, ctx)
        value = ctx.send.await_args.kwargs["embed"].fields[0].value
        assert "SHEET1" in value
        assert "P7" in value

    async def test_puzzle_channel_is_unchanged(self):
        from bot.cogs.puzzles import Puzzles
        puzzle = make_puzzle(id=5, hunt_id=10, name="Alpha")
        puzzle.additional_sheets = [
            # on_solved_sheet is read per sheet since migration 045; this test
            # is about link numbering, so the value just has to be present.
            SimpleNamespace(id=1, puzzle_name="grid 2", google_page_id="P1",
                            on_solved_sheet=False)]
        cog = make_cog(puzzle=puzzle, hunt=make_hunt(id=10))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock()
        ctx = make_ctx()
        await Puzzles.list_additional_sheets(cog, ctx)
        cog.service.get_hunt_sheets.assert_not_called()
        assert "grid 2" in ctx.send.await_args.kwargs["embed"].fields[0].value
