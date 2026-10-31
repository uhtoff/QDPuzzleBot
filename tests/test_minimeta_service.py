"""Tests for M8 minimeta cluster service methods."""
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import pytest

from bot.store.puzzle_data import PuzzleData
from tests.conftest import make_hunt, make_puzzle, make_round


def _make_cog():
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock()
    bot.get_cog.return_value = None
    return Puzzles(bot)


class TestPuzzleDataParentField:
    def test_parent_puzzle_id_defaults_none(self):
        p = PuzzleData()
        assert p.parent_puzzle_id is None

    def test_make_puzzle_accepts_parent_puzzle_id(self):
        p = make_puzzle(id=2, parent_puzzle_id=1)
        assert p.parent_puzzle_id == 1

    def test_import_dict_reads_parent_puzzle_id(self):
        row = {
            "id": 5, "name": "Mini", "hunt_id": 10,
            "parent_puzzle_id": 3,
            "channel_id": 999, "solved": False, "solution": None,
            "status": "", "priority": "",
            "puzzle_type": "Mini", "notes": None, "url": "", "google_page_id": "",
            "on_solved_sheet": False, "voice_channel_id": 0, "channel_mention": "",
            "channel_name": "", "start_time": None, "solve_time": None,
            "tag_edges": None,
        }
        p = PuzzleData.import_dict(row)
        assert p.parent_puzzle_id == 3


class TestCogBeforeInvokeThread:
    """cog_before_invoke must route thread messages to the mini-puzzle row."""

    def _make_thread_ctx(self, channel_id):
        import discord
        thread = MagicMock(spec=discord.Thread)
        thread.id = channel_id
        ctx = MagicMock()
        ctx.channel = thread
        ctx.guild.id = 1
        ctx.message.id = 999
        return ctx

    @patch("bot.cogs.puzzles.GuildSettingsDb")
    @patch("bot.cogs.puzzles.PuzzleJsonDb")
    @patch("bot.cogs.puzzles.HuntJsonDb")
    @patch("bot.cogs.puzzles.TagGroupJsonDb")
    def test_thread_routes_to_mini_puzzle(self, RoundDb, HuntDb, PuzzleDb, GuildDb):
        from bot.cogs.puzzles import Puzzles
        import asyncio

        mini = make_puzzle(id=5, hunt_id=10, channel_id=777, parent_puzzle_id=3, tags=[1])
        hunt = make_hunt(id=10)
        round_ = make_round(id=1)

        GuildDb.get.return_value = MagicMock()
        GuildDb.get_channel_type.return_value = None  # thread not in GuildSettings
        PuzzleDb.get_by_attr.return_value = mini
        HuntDb.get_by_attr.return_value = hunt
        # A mini is a feeder, not a group target: the target_puzzle_id lookup must
        # miss (so channel_type stays 'Puzzle'); the tag-membership lookup hits.
        def _round_lookup(**kwargs):
            return None if kwargs.get("target_puzzle_id") else round_
        RoundDb.get_by_attr.side_effect = _round_lookup

        bot = MagicMock()
        bot.get_cog.return_value = None
        cog = Puzzles(bot)

        ctx = self._make_thread_ctx(777)

        asyncio.run(cog.cog_before_invoke(ctx))

        env = cog.environment[999]
        assert env["channel_type"] == "Puzzle"
        assert env["puzzle"] is mini
        assert env["hunt"] is hunt

    @patch("bot.cogs.puzzles.GuildSettingsDb")
    @patch("bot.cogs.puzzles.PuzzleJsonDb")
    @patch("bot.cogs.puzzles.HuntJsonDb")
    @patch("bot.cogs.puzzles.TagGroupJsonDb")
    def test_unknown_thread_becomes_guild(self, RoundDb, HuntDb, PuzzleDb, GuildDb):
        from bot.cogs.puzzles import Puzzles
        import asyncio

        GuildDb.get.return_value = MagicMock()
        GuildDb.get_channel_type.return_value = None
        PuzzleDb.get_by_attr.return_value = None  # no mini found

        bot = MagicMock()
        bot.get_cog.return_value = None
        cog = Puzzles(bot)

        ctx = self._make_thread_ctx(888)
        asyncio.run(cog.cog_before_invoke(ctx))

        assert cog.environment[999]["channel_type"] == "Guild"


class TestMakeMinimeta:
    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_sets_puzzle_type(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        puzzle = make_puzzle(id=1)
        assert puzzle.puzzle_type == ""
        await svc.make_minimeta(puzzle)
        assert puzzle.puzzle_type == "Minimeta"
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_idempotent(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        puzzle = make_puzzle(id=1, puzzle_type="Minimeta")
        await svc.make_minimeta(puzzle)
        assert puzzle.puzzle_type == "Minimeta"
        PuzzleDb.commit.assert_called_once()


class TestGetMinis:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    def test_returns_children(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        children = [make_puzzle(id=2), make_puzzle(id=3)]
        PuzzleDb.get_children.return_value = children
        result = svc.get_minis(1)
        PuzzleDb.get_children.assert_called_once_with(1)
        assert result == children

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    def test_empty(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        PuzzleDb.get_children.return_value = []
        assert svc.get_minis(1) == []


class TestCreateMini:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_creates_puzzle_row_with_parent(self, RoundDb, PuzzleDb):
        """create_mini sets parent_puzzle_id + puzzle_type=Mini and leaves the mini
        UNTAGGED (it rolls up onto the minimeta via the puzzle-keyed path, not a round)."""
        import asyncio, discord
        from bot.core.puzzle_service import PuzzleService

        minimeta = make_puzzle(id=3, hunt_id=10, channel_id=555, tags=[1])
        hunt = make_hunt(id=10)
        round_ = make_round(id=1)
        RoundDb.get_by_attr.return_value = round_

        # Discord stubs
        guild = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 9001
        thread.name = "tiny-puzzle"
        channel = MagicMock(spec=discord.TextChannel)

        async def fake_create_thread(**kwargs):
            return thread
        channel.create_thread = fake_create_thread
        guild.get_channel.return_value = channel

        # Sheet stub
        gsheet = MagicMock()
        async def fake_create_sheet(p, **kwargs):
            return None
        gsheet.create_puzzle_spreadsheet = fake_create_sheet
        gsheet.compute_insert_index = AsyncMock(return_value=0)
        bot = MagicMock()
        bot.get_cog.return_value = gsheet

        svc = PuzzleService(bot)
        mini = asyncio.run(
            svc.create_mini(guild, hunt, minimeta, "Tiny Puzzle")
        )

        assert mini.parent_puzzle_id == 3
        assert mini.puzzle_type == "Mini"
        assert mini.channel_id == 9001
        assert mini.tags == []   # minis are children, not round feeders
        PuzzleDb.commit.assert_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_posts_welcome_message_with_sheet_link(self, RoundDb, PuzzleDb):
        """create_mini posts a welcome message in the thread naming the minimeta
        and linking to the mini's Google sheet tab."""
        import asyncio, discord
        from unittest.mock import AsyncMock
        from bot.core.puzzle_service import PuzzleService

        minimeta = make_puzzle(id=3, name="Big Meta", hunt_id=10, channel_id=555, tags=[1])
        hunt = make_hunt(id=10)  # google_sheet_id == "SHEET1"
        RoundDb.get_by_attr.return_value = make_round(id=1)

        guild = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 9003
        thread.name = "tiny-puzzle"
        thread.send = AsyncMock()
        channel = MagicMock(spec=discord.TextChannel)

        async def fake_create_thread(**kwargs):
            return thread
        channel.create_thread = fake_create_thread
        guild.get_channel.return_value = channel

        gsheet = MagicMock()
        async def fake_create_sheet(p, **kwargs):
            p.google_page_id = "PAGE7"
            return None
        gsheet.create_puzzle_spreadsheet = fake_create_sheet
        gsheet.compute_insert_index = AsyncMock(return_value=0)
        bot = MagicMock()
        bot.get_cog.return_value = gsheet

        svc = PuzzleService(bot)
        asyncio.run(svc.create_mini(guild, hunt, minimeta, "Tiny Puzzle"))

        thread.send.assert_awaited_once()
        msg = thread.send.call_args[0][0]
        assert "Tiny Puzzle" in msg      # mini name
        assert "Big Meta" in msg          # minimeta name
        assert "SHEET1" in msg            # spreadsheet id in the link
        assert "PAGE7" in msg             # mini's tab/page id in the link

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_auto_makes_parent_minimeta(self, RoundDb, PuzzleDb):
        """create_mini auto-sets puzzle_type=Minimeta on parent if not already set."""
        import asyncio, discord
        from bot.core.puzzle_service import PuzzleService

        minimeta = make_puzzle(id=3, hunt_id=10, channel_id=555, tags=[1], puzzle_type="")
        hunt = make_hunt()
        RoundDb.get_by_attr.return_value = make_round(id=1)

        guild = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 9002
        thread.name = "another-mini"
        channel = MagicMock(spec=discord.TextChannel)

        async def fake_create_thread(**kwargs):
            return thread
        channel.create_thread = fake_create_thread
        guild.get_channel.return_value = channel

        gsheet = MagicMock()
        async def fake_create_sheet(p, **kwargs):
            return None
        gsheet.create_puzzle_spreadsheet = fake_create_sheet
        gsheet.compute_insert_index = AsyncMock(return_value=0)
        bot = MagicMock()
        bot.get_cog.return_value = gsheet

        svc = PuzzleService(bot)
        asyncio.run(
            svc.create_mini(guild, hunt, minimeta, "Another Mini")
        )

        assert minimeta.puzzle_type == "Minimeta"


class TestSolveArchivesThread:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_mini_solve_archives_thread(self, RoundDb, PuzzleDb):
        """Solving a mini-puzzle archives its Discord thread."""
        import asyncio, discord
        from bot.core.puzzle_service import PuzzleService

        mini = make_puzzle(id=5, hunt_id=10, channel_id=9001, tags=[], parent_puzzle_id=3)
        hunt = make_hunt()
        RoundDb.get_by_attr.return_value = None

        thread = MagicMock(spec=discord.Thread)
        async def fake_edit(**kw):
            return None
        thread.edit = fake_edit
        guild = MagicMock()
        guild.get_channel_or_thread.return_value = thread

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        asyncio.run(svc.solve(guild, hunt, mini, "ANSWER"))

        guild.get_channel_or_thread.assert_called_once_with(9001)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_regular_puzzle_solve_no_thread_archive(self, RoundDb, PuzzleDb):
        """Solving a regular puzzle does not attempt thread archive."""
        import asyncio
        from bot.core.puzzle_service import PuzzleService

        puzzle = make_puzzle(id=1, hunt_id=10, tags=[])
        hunt = make_hunt()
        RoundDb.get_by_attr.return_value = None

        guild = MagicMock()
        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        asyncio.run(svc.solve(guild, hunt, puzzle, "ANSWER"))

        guild.get_channel_or_thread.assert_not_called()


class TestUnsolveService:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_unsolve_clears_fields(self, RoundDb, PuzzleDb):
        """unsolve resets solved/solution/solve_time."""
        import asyncio
        from bot.core.puzzle_service import PuzzleService

        puzzle = make_puzzle(id=1, hunt_id=10, solved=True, solution="ANS", tags=[])
        puzzle.solve_time = "sometime"
        hunt = make_hunt()
        RoundDb.get_by_attr.return_value = None

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        asyncio.run(svc.unsolve(None, hunt, puzzle))

        assert puzzle.solved is False
        assert puzzle.solution == ""
        assert puzzle.solve_time is None
        # Canonical lowercase since the status-vocabulary tranche: "Unsolved" was
        # not in STATUSES at all, and the column held both spellings.
        assert puzzle.status == "unsolved"
        PuzzleDb.commit.assert_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_mini_unsolve_unarchives_thread(self, RoundDb, PuzzleDb):
        """unsolve on a mini-puzzle unarchives its thread."""
        import asyncio, discord
        from bot.core.puzzle_service import PuzzleService

        mini = make_puzzle(id=5, solved=True, solution="X", tags=[], parent_puzzle_id=3, channel_id=9001)
        mini.solve_time = "t"
        hunt = make_hunt()
        RoundDb.get_by_attr.return_value = None

        thread = MagicMock(spec=discord.Thread)
        async def fake_edit(**kw):
            return None
        thread.edit = fake_edit
        guild = MagicMock()
        guild.get_channel_or_thread.return_value = thread

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        asyncio.run(svc.unsolve(guild, hunt, mini))

        guild.get_channel_or_thread.assert_called_once_with(9001)

    @patch("bot.core.puzzle_service.SheetsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_commits_after_sheet_restore(self, RoundDb, PuzzleDb, SheetsDb):
        """The DB commit must happen AFTER restore_puzzle_from_solved_sheet mutates
        puzzle.on_solved_sheet/google_page_id, else those fields never persist (#4)."""
        import asyncio
        from bot.core.puzzle_service import PuzzleService
        from bot.store.puzzle_data import AdditionalSheetData

        puzzle = make_puzzle(id=1, hunt_id=10, solved=True, solution="ANS", tags=[])
        puzzle.additional_sheets = [AdditionalSheetData(google_page_id="extra-1")]
        hunt = make_hunt()
        RoundDb.get_by_attr.return_value = None
        PuzzleDb.get_all_from_hunt.return_value = []

        order = []
        gsheet_cog = MagicMock()
        gsheet_cog.compute_insert_index = AsyncMock(return_value=3)

        async def fake_restore(puzzle_data, restore_index=None):
            order.append("restore")

        gsheet_cog.restore_puzzle_from_solved_sheet = fake_restore
        PuzzleDb.commit.side_effect = lambda *a, **kw: order.append("commit_puzzle")
        SheetsDb.commit.side_effect = lambda *a, **kw: order.append("commit_sheet")

        bot = MagicMock()
        bot.get_cog.return_value = gsheet_cog
        svc = PuzzleService(bot)

        asyncio.run(svc.unsolve(None, hunt, puzzle))

        assert order == ["restore", "commit_puzzle", "commit_sheet"]


class TestSolveMiniByNumber:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_resolves_by_number_and_delegates(self, RoundDb, PuzzleDb):
        import asyncio
        from bot.core.puzzle_service import PuzzleService
        from unittest.mock import AsyncMock

        mini1 = make_puzzle(id=10, parent_puzzle_id=3, tags=[])
        mini2 = make_puzzle(id=11, parent_puzzle_id=3, tags=[])
        PuzzleDb.get_children.return_value = [mini1, mini2]
        RoundDb.get_by_attr.return_value = None

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        svc.solve = AsyncMock(return_value=True)

        asyncio.run(
            svc.solve_mini_by_number(None, make_hunt(), 3, 2, "ANSWER")
        )

        svc.solve.assert_called_once()
        called_puzzle = svc.solve.call_args[0][2]
        assert called_puzzle.id == 11  # 1-indexed: number 2 → index 1

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    def test_raises_on_bad_number(self, PuzzleDb):
        import asyncio
        from bot.core.puzzle_service import PuzzleService

        PuzzleDb.get_children.return_value = [make_puzzle(id=10, parent_puzzle_id=3)]
        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        with pytest.raises(ValueError, match="No mini #5"):
            asyncio.run(
                svc.solve_mini_by_number(None, make_hunt(), 3, 5, "X")
            )


class TestUnsolveMiniByNumber:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    def test_resolves_and_delegates(self, RoundDb, PuzzleDb):
        import asyncio
        from bot.core.puzzle_service import PuzzleService
        from unittest.mock import AsyncMock

        mini = make_puzzle(id=10, parent_puzzle_id=3, solved=True, solution="ANS", tags=[])
        PuzzleDb.get_children.return_value = [mini]
        RoundDb.get_by_attr.return_value = None

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        svc.unsolve = AsyncMock()

        asyncio.run(
            svc.unsolve_mini_by_number(None, make_hunt(), 3, 1)
        )

        svc.unsolve.assert_called_once()
        assert svc.unsolve.call_args[0][2].id == 10


# TestWebDataMinisPayload lived here and asserted substrings of
# inspect.getsource(data.get_hunt_puzzles). Replaced by behavioural coverage in
# tests/test_hunt_puzzles_payload.py, which executes the function (M33).


class TestRefreshMiniMetaUsesStandardPath:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.rollup_lock")
    async def test_calls_render_minimeta_contribution(self, lock_cm, RollupsDb, PuzzleDb):
        """refresh_minimeta must call render_minimeta_contribution (standard path)."""
        from unittest.mock import AsyncMock, MagicMock
        from bot.core.puzzle_service import PuzzleService

        minimeta = make_puzzle(id=1, hunt_id=10)
        hunt = make_hunt(id=10)
        children = [make_puzzle(id=2, parent_puzzle_id=1)]
        PuzzleDb.get_children.return_value = children
        RollupsDb.get_by_attr.return_value = None

        gsheet = MagicMock()
        gsheet.render_minimeta_contribution = AsyncMock()
        gsheet._record_rollup_tab = AsyncMock()
        bot = MagicMock()
        bot.get_cog.return_value = gsheet

        # rollup_lock must be an async context manager
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def _noop(*a):
            yield
        lock_cm.side_effect = _noop

        svc = PuzzleService(bot)
        await svc.refresh_minimeta(hunt, minimeta)

        gsheet.render_minimeta_contribution.assert_awaited_once_with(minimeta, children, link_for=ANY)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.rollup_lock")
    async def test_does_not_call_add_metapuzzle_data(self, lock_cm, RollupsDb, PuzzleDb):
        """refresh_minimeta must NOT call the old add_metapuzzle_data path."""
        from unittest.mock import AsyncMock, MagicMock
        from bot.core.puzzle_service import PuzzleService

        minimeta = make_puzzle(id=1, hunt_id=10)
        hunt = make_hunt(id=10)
        PuzzleDb.get_children.return_value = []
        RollupsDb.get_by_attr.return_value = None

        gsheet = MagicMock()
        gsheet.render_minimeta_contribution = AsyncMock()
        gsheet._record_rollup_tab = AsyncMock()
        bot = MagicMock()
        bot.get_cog.return_value = gsheet

        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def _noop(*a):
            yield
        lock_cm.side_effect = _noop

        svc = PuzzleService(bot)
        await svc.refresh_minimeta(hunt, minimeta)

        gsheet.add_metapuzzle_data.assert_not_called()


class TestRemoveMinimeta:
    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_deletes_minis_and_clears_type(self, PuzzleDb, RollupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        m1 = make_puzzle(id=2, parent_puzzle_id=1)
        m2 = make_puzzle(id=3, parent_puzzle_id=1)
        svc.get_minis = MagicMock(return_value=[m1, m2])
        svc.delete_puzzle = AsyncMock()
        svc._gsheet_cog = MagicMock(return_value=None)
        RollupDb.get_by_attr.return_value = None

        await svc.remove_minimeta(None, make_hunt(), minimeta)

        assert svc.delete_puzzle.await_count == 2
        assert minimeta.puzzle_type is None
        PuzzleDb.commit.assert_called_with(minimeta)

    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_raises_when_not_minimeta(self, PuzzleDb, RollupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        puzzle = make_puzzle(id=1, puzzle_type="Cryptic")
        svc.get_minis = MagicMock(return_value=[])
        with pytest.raises(RuntimeError, match="not_minimeta"):
            await svc.remove_minimeta(None, make_hunt(), puzzle)

    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_deletes_rollup_row_when_present(self, PuzzleDb, RollupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        svc.get_minis = MagicMock(return_value=[])
        svc.delete_puzzle = AsyncMock()
        svc._gsheet_cog = MagicMock(return_value=None)
        rollup = MagicMock(id=7)
        RollupDb.get_by_attr.return_value = rollup

        await svc.remove_minimeta(None, make_hunt(), minimeta)

        RollupDb.delete.assert_called_once_with(7)

    @staticmethod
    def _gsheet_with_tables(tables):
        """A GoogleSheets stub whose sheet 99 holds `tables` (None = no key)."""
        gsheet = MagicMock()
        gsheet.get_spreadsheet_id.return_value = "SS"
        sheet = {"properties": {"sheetId": 99}}
        if tables is not None:
            sheet["tables"] = tables
        gsheet.sheet_list = AsyncMock(return_value={"sheets": [sheet]})
        gsheet.demote_tab_to_puzzle = AsyncMock()
        return gsheet

    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_demotes_tab_when_rollup_table_present(self, PuzzleDb, RollupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        minimeta.google_page_id = "99"
        svc.get_minis = MagicMock(return_value=[])
        svc.delete_puzzle = AsyncMock()
        # OUR rollup table, named the way render_minimeta_contribution creates it.
        gsheet = self._gsheet_with_tables(
            [{"tableId": "t", "name": "Contributions 99"}])
        svc._gsheet_cog = MagicMock(return_value=gsheet)
        RollupDb.get_by_attr.return_value = None

        await svc.remove_minimeta(None, make_hunt(), minimeta)

        gsheet.demote_tab_to_puzzle.assert_awaited_once_with(minimeta)
        assert minimeta.puzzle_type is None

    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_skips_demote_when_the_only_table_is_a_solver_s(self, PuzzleDb, RollupDb):
        """M16: "any native Table" is not "our rollup Table". A minimeta made but
        never rendered has no Table of ours; if a solver has added one of their
        own, demoting deletes columns A-D of an unpromoted tab — their data, no
        undo. Only the bot's own named table licenses the demote."""
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        minimeta.google_page_id = "99"
        svc.get_minis = MagicMock(return_value=[])
        svc.delete_puzzle = AsyncMock()
        gsheet = self._gsheet_with_tables([{"tableId": "x", "name": "Table5"}])
        svc._gsheet_cog = MagicMock(return_value=gsheet)
        RollupDb.get_by_attr.return_value = None

        await svc.remove_minimeta(None, make_hunt(), minimeta)

        gsheet.demote_tab_to_puzzle.assert_not_awaited()
        assert minimeta.puzzle_type is None

    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_skips_demote_when_no_table(self, PuzzleDb, RollupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        minimeta.google_page_id = "99"
        svc.get_minis = MagicMock(return_value=[])
        svc.delete_puzzle = AsyncMock()
        gsheet = MagicMock()
        gsheet.get_spreadsheet_id.return_value = "SS"
        gsheet.sheet_list = AsyncMock(return_value={
            "sheets": [{"properties": {"sheetId": 99}}]
        })
        gsheet.demote_tab_to_puzzle = AsyncMock()
        svc._gsheet_cog = MagicMock(return_value=gsheet)
        RollupDb.get_by_attr.return_value = None

        await svc.remove_minimeta(None, make_hunt(), minimeta)

        gsheet.demote_tab_to_puzzle.assert_not_awaited()
        assert minimeta.puzzle_type is None

    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_mini_delete_failure_is_isolated(self, PuzzleDb, RollupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        m1 = make_puzzle(id=2, parent_puzzle_id=1)
        m2 = make_puzzle(id=3, parent_puzzle_id=1)
        svc.get_minis = MagicMock(return_value=[m1, m2])
        svc.delete_puzzle = AsyncMock(side_effect=[RuntimeError("boom"), None])
        svc._gsheet_cog = MagicMock(return_value=None)
        RollupDb.get_by_attr.return_value = None

        await svc.remove_minimeta(None, make_hunt(), minimeta)

        assert svc.delete_puzzle.await_count == 2
        assert minimeta.puzzle_type is None


class TestDeleteMini:
    """delete_mini is a separate seam from delete_puzzle: it must also re-render
    the parent minimeta's puzzle-keyed rollup, which refresh_targets never covers."""

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_deletes_and_refreshes_parent_rollup(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService

        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        mini = make_puzzle(id=2, parent_puzzle_id=1, tags=[])
        PuzzleDb.get_by_attr.return_value = minimeta
        svc = PuzzleService(MagicMock())
        svc.delete_puzzle = AsyncMock()
        svc.refresh_minimeta = AsyncMock()
        hunt = make_hunt(id=10)
        guild = MagicMock()

        returned = await svc.delete_mini(guild, hunt, mini)

        svc.delete_puzzle.assert_awaited_once_with(guild, hunt, mini)
        svc.refresh_minimeta.assert_awaited_once_with(hunt, minimeta)
        assert returned is minimeta

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_rollup_refresh_follows_the_deletion(self, PuzzleDb):
        """Order matters: refreshing before the row is gone re-renders the doomed
        mini straight back onto the parent's sheet."""
        from bot.core.puzzle_service import PuzzleService

        PuzzleDb.get_by_attr.return_value = make_puzzle(id=1)
        calls = []
        svc = PuzzleService(MagicMock())
        svc.delete_puzzle = AsyncMock(side_effect=lambda *a: calls.append("delete"))
        svc.refresh_minimeta = AsyncMock(side_effect=lambda *a: calls.append("refresh"))

        await svc.delete_mini(None, make_hunt(), make_puzzle(id=2, parent_puzzle_id=1))

        assert calls == ["delete", "refresh"]

    async def test_rejects_a_non_mini(self):
        from bot.core.puzzle_service import PuzzleService

        svc = PuzzleService(MagicMock())
        svc.delete_puzzle = AsyncMock()

        with pytest.raises(ValueError, match="not a mini-puzzle"):
            await svc.delete_mini(None, make_hunt(), make_puzzle(id=2, parent_puzzle_id=None))

        svc.delete_puzzle.assert_not_awaited()


class TestDeleteMiniByNumber:
    async def test_resolves_the_right_mini_by_number(self):
        from bot.core.puzzle_service import PuzzleService

        m1 = make_puzzle(id=10, parent_puzzle_id=3)
        m2 = make_puzzle(id=11, parent_puzzle_id=3)
        m3 = make_puzzle(id=12, parent_puzzle_id=3)
        svc = PuzzleService(MagicMock())
        svc.get_minis = MagicMock(return_value=[m1, m2, m3])
        svc.delete_mini = AsyncMock()

        deleted = await svc.delete_mini_by_number(None, make_hunt(), 3, 2)

        # 1-indexed: number 2 → the middle mini, not the first or last.
        assert svc.delete_mini.await_args[0][2] is m2
        assert deleted is m2

    async def test_raises_on_bad_number(self):
        from bot.core.puzzle_service import PuzzleService

        svc = PuzzleService(MagicMock())
        svc.get_minis = MagicMock(return_value=[make_puzzle(id=10, parent_puzzle_id=3)])
        svc.delete_mini = AsyncMock()

        with pytest.raises(ValueError, match="No mini #5"):
            await svc.delete_mini_by_number(None, make_hunt(), 3, 5)

        svc.delete_mini.assert_not_awaited()
