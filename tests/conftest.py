"""Shared fixtures for the QDPuzzleBot test suite."""

from unittest.mock import AsyncMock, MagicMock

from bot.store.hunt_data import HuntData
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import TagGroupData


def make_puzzle(
    id=1,
    name="Test Puzzle",
    hunt_id=10,
    solved=False,
    solution=None,
    is_target=False,
    tags=None,
    status="",
    channel_id=111,
    parent_puzzle_id=None,
    puzzle_type="",
):
    p = PuzzleData()
    p.id = id
    p.name = name
    p.hunt_id = hunt_id
    p.channel_id = channel_id
    p.solved = solved
    p.solution = solution
    p.is_target = is_target
    p.tags = tags if tags is not None else []
    p.status = status
    p.parent_puzzle_id = parent_puzzle_id
    p.puzzle_type = puzzle_type
    return p


def make_round(
    id=1,
    name="Test Round",
    hunt_id=10,
    target_puzzle_id=0,
    group_code="TROUND",
    category_id=999,
):
    r = TagGroupData()
    r.id = id
    r.name = name
    r.hunt_id = hunt_id
    r.target_puzzle_id = target_puzzle_id
    r.group_code = group_code
    r.category_id = category_id
    return r


def make_puzzle_row(
    id,
    name,
    solved=False,
    solution=None,
    is_meta=False,
    rounds=None,
    notes=None,
    channel_id=None,
    status="",
    priority="",
    puzzle_type="",
    start_time=None,
    solve_time=None,
):
    """Build a puzzle dict as returned by get_hunt_puzzles (dashboard layer)."""
    return {
        "id": id,
        "name": name,
        "url": f"https://example.com/puzzle/{id}",
        "channel_id": str(channel_id or id),
        "discord_url": None,
        "sheet_page": None,
        "is_meta": is_meta,
        "status": status,
        "solved": solved,
        "archived": False,
        "solution": solution,
        "priority": priority,
        "puzzle_type": puzzle_type,
        "notes": notes or [],
        "start_time": None,
        "solve_time": None,
        "last_activity": None,
        "rounds": rounds or [],
        "sheets": [],
    }


def make_round_ref(id, name, group_code="RC", is_target_of=False):
    """Build a round reference dict as embedded in a puzzle row."""
    return {"id": id, "name": name, "group_code": group_code, "is_target_of": is_target_of}


def make_hunt(
    id=10,
    google_sheet_id="SHEET1",
    archive_google_sheet_id="ARCHIVE1",
    **kwargs,
):
    """Return a HuntData with sensible defaults.

    Extra attributes can be set via kwargs, e.g.::

        make_hunt(evidence_sheet_page_id=None)
    """
    h = HuntData()
    h.id = id
    h.google_sheet_id = google_sheet_id
    h.archive_google_sheet_id = archive_google_sheet_id
    for attr, val in kwargs.items():
        setattr(h, attr, val)
    return h


def make_ctx(channel_id=111, message_id=1):
    """Return a minimal mock Discord Context suitable for cog callback tests."""
    ctx = MagicMock()
    ctx.channel.id = channel_id
    ctx.message.id = message_id
    ctx.guild = MagicMock()
    ctx.send = AsyncMock()
    return ctx


def make_cog(puzzle=None, hunt=None):
    """Return a Puzzles cog stub with get_puzzle/get_hunt/service mocked.

    ``puzzle`` is returned by ``cog.get_puzzle``; ``hunt`` (or a fresh
    MagicMock) is returned by ``cog.get_hunt``.
    """
    from bot.cogs.puzzles import Puzzles

    cog = Puzzles.__new__(Puzzles)
    cog.get_puzzle = lambda ctx: puzzle
    cog.get_hunt = lambda ctx: hunt or MagicMock()
    cog.service = MagicMock()
    return cog
