"""Phase B: PuzzleService mutations emit board_changed on self.notifier.

Each test patches the minimal DB/Discord seams and asserts that the service
calls notifier.board_changed(hunt) exactly once after a successful mutation.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle, make_round


def _hunt(id=10, uid="HUNT1", guild_id=111222333):
    h = HuntData()
    h.id = id
    h.uid = uid
    h.guild_id = guild_id
    h.google_sheet_id = "S1"
    h.archive_google_sheet_id = "A1"
    h.url = None
    h.category_id = 1
    h.name = "H"
    return h


def _notifier():
    n = MagicMock()
    n.board_changed = AsyncMock()
    n.welcome = AsyncMock()
    n.refresh_info = AsyncMock()
    n.puzzle_solved = AsyncMock()
    return n


def _svc(notifier):
    bot = MagicMock()
    bot.get_cog.return_value = None  # no sheets cog
    return PuzzleService(bot, notifier=notifier)


# ---- solve ----

@pytest.mark.asyncio
async def test_solve_emits_board_changed():
    notifier = _notifier()
    svc = _svc(notifier)
    hunt = _hunt()
    puzzle = make_puzzle(id=1, hunt_id=10)

    with patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb:
        rdb.get_by_attr.return_value = None
        await svc.solve(guild=MagicMock(), hunt=hunt, puzzle=puzzle, solution="ANSWER")

    notifier.board_changed.assert_awaited_once_with(hunt)


# ---- move_to_meta → tag_edge_only ----

@pytest.mark.asyncio
async def test_tag_edge_only_emits_board_changed():
    notifier = _notifier()
    svc = _svc(notifier)
    hunt = _hunt()
    puzzle = make_puzzle(id=1, hunt_id=10, tags=[5])
    puzzle.tag_edges = {}
    meta_round = make_round(id=5)

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb:
        hdb.get_by_attr.return_value = hunt
        rdb.get_by_attr.return_value = meta_round
        svc.refresh_targets = AsyncMock(return_value=[])
        await svc.tag_edge_only(puzzle=puzzle, group=meta_round, edge="feeds", guild=None)

    notifier.board_changed.assert_awaited_once_with(hunt)


# ---- add_to_meta → tag_with_edge ----

@pytest.mark.asyncio
async def test_tag_with_edge_emits_board_changed():
    notifier = _notifier()
    svc = _svc(notifier)
    hunt = _hunt()
    puzzle = make_puzzle(id=1, hunt_id=10)
    puzzle.tag_edges = {}
    meta_round = make_round(id=5)

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb"):
        hdb.get_by_attr.return_value = hunt
        svc.refresh_targets = AsyncMock(return_value=[])
        await svc.tag_with_edge(puzzle=puzzle, group=meta_round, edge="feeds")

    notifier.board_changed.assert_awaited_once_with(hunt)


# ---- remove_from_meta → untag_from ----

@pytest.mark.asyncio
async def test_untag_from_emits_board_changed():
    notifier = _notifier()
    svc = _svc(notifier)
    hunt = _hunt()
    puzzle = make_puzzle(id=1, hunt_id=10, tags=[5])
    puzzle.tag_edges = {5: "feeds"}
    meta_round = make_round(id=5)

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb:
        hdb.get_by_attr.return_value = hunt
        rdb.get_by_attr.return_value = meta_round
        svc.refresh_targets = AsyncMock(return_value=[])
        await svc.untag_from(puzzle=puzzle, group=meta_round)

    notifier.board_changed.assert_awaited_once_with(hunt)


# ---- add_sheet ----

@pytest.mark.asyncio
async def test_add_sheet_emits_board_changed():
    notifier = _notifier()
    svc = _svc(notifier)
    hunt = _hunt()
    puzzle = make_puzzle(id=1, hunt_id=10, solved=False)
    puzzle.additional_sheets = []

    with patch("bot.core.puzzle_service.SheetsJsonDb"):
        await svc.add_sheet(hunt=hunt, puzzle=puzzle, name="Extra")

    notifier.board_changed.assert_awaited_once_with(hunt)


# ---- remove_sheet ----

@pytest.mark.asyncio
async def test_remove_sheet_emits_board_changed():
    notifier = _notifier()
    svc = _svc(notifier)
    hunt = _hunt()
    sheet = MagicMock()
    sheet.id = 99
    sheet.google_page_id = "gp1"
    puzzle = make_puzzle(id=1, hunt_id=10)
    puzzle.additional_sheets = [sheet]

    with patch("bot.core.puzzle_service.SheetsJsonDb"):
        await svc.remove_sheet(hunt=hunt, puzzle=puzzle, sheet_id=99)

    notifier.board_changed.assert_awaited_once_with(hunt)


# ---- create_puzzle ----

@pytest.mark.asyncio
async def test_create_puzzle_emits_board_changed():
    notifier = _notifier()
    bot = MagicMock()
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    bot.get_cog.return_value = gsheet
    svc = PuzzleService(bot, notifier=notifier)

    guild = MagicMock()
    guild.channels = []
    cat = MagicMock(id=999)
    guild.categories = [cat]
    hunt = _hunt()
    round_data = make_round(id=5, category_id=999)

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="newpuz"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(guild, hunt, round_data, "Newpuz")

    notifier.board_changed.assert_awaited_once_with(hunt)


# ---- create_round ----

@pytest.mark.asyncio
async def test_create_tag_group_emits_board_changed():
    notifier = _notifier()
    bot = MagicMock()
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    bot.get_cog.return_value = gsheet
    svc = PuzzleService(bot, notifier=notifier)

    guild = MagicMock()
    guild.channels = []
    ref_cat = MagicMock(id=1, position=0)
    guild.categories = [ref_cat]
    new_cat = MagicMock(id=2, channels=[])
    ref_cat.clone = AsyncMock(return_value=new_cat)
    new_cat.edit = AsyncMock()
    hunt = _hunt()
    meta_puzzle = make_puzzle(id=77, name="New Round", is_target=True, channel_id=222)

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))), \
         patch.object(svc, "create_puzzle", AsyncMock(return_value=meta_puzzle)):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "META01"
        await svc.create_tag_group(guild, hunt, "New Round")

    notifier.board_changed.assert_awaited_once_with(hunt)
