"""Tests for M8 minimeta cluster service methods."""
from unittest.mock import MagicMock, patch
import pytest

from bot.store.puzzle_data import PuzzleData
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle, make_round


def _make_cog():
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock()
    bot.get_cog.return_value = None
    return Puzzles(bot)


def _make_hunt(id=10):
    h = HuntData()
    h.id = id
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCHIVE1"
    return h


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
            "metapuzzle": 0, "metameta": 0, "status": "", "priority": "",
            "puzzle_type": "Mini", "notes": None, "url": "", "google_page_id": "",
            "archived": False, "voice_channel_id": 0, "channel_mention": "",
            "channel_name": "", "start_time": None, "solve_time": None,
            "archive_time": None, "tag_edges": None,
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
    @patch("bot.cogs.puzzles.RoundJsonDb")
    def test_thread_routes_to_mini_puzzle(self, RoundDb, HuntDb, PuzzleDb, GuildDb):
        from bot.cogs.puzzles import Puzzles
        import asyncio, discord

        mini = make_puzzle(id=5, hunt_id=10, channel_id=777, parent_puzzle_id=3, tags=[1])
        hunt = _make_hunt(id=10)
        round_ = make_round(id=1)

        GuildDb.get.return_value = MagicMock()
        GuildDb.get_channel_type.return_value = None  # thread not in GuildSettings
        PuzzleDb.get_by_attr.return_value = mini
        HuntDb.get_by_attr.return_value = hunt
        RoundDb.get_by_attr.return_value = round_

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
    @patch("bot.cogs.puzzles.RoundJsonDb")
    def test_unknown_thread_becomes_guild(self, RoundDb, HuntDb, PuzzleDb, GuildDb):
        from bot.cogs.puzzles import Puzzles
        import asyncio, discord

        GuildDb.get.return_value = MagicMock()
        GuildDb.get_channel_type.return_value = None
        PuzzleDb.get_by_attr.return_value = None  # no mini found

        bot = MagicMock()
        bot.get_cog.return_value = None
        cog = Puzzles(bot)

        ctx = self._make_thread_ctx(888)
        asyncio.run(cog.cog_before_invoke(ctx))

        assert cog.environment[999]["channel_type"] == "Guild"
