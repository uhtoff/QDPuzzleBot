"""Shared fixtures for the QDPuzzleBot test suite."""

from bot.store.puzzle_data import PuzzleData
from bot.store.round_data import RoundData


def make_puzzle(
    id=1,
    name="Test Puzzle",
    hunt_id=10,
    solved=False,
    solution=None,
    metapuzzle=0,
    metameta=0,
    tags=None,
    status="",
    channel_id=111,
):
    p = PuzzleData()
    p.id = id
    p.name = name
    p.hunt_id = hunt_id
    p.channel_id = channel_id
    p.solved = solved
    p.solution = solution
    p.metapuzzle = metapuzzle
    p.metameta = metameta
    p.tags = tags if tags is not None else []
    p.status = status
    return p


def make_round(
    id=1,
    name="Test Round",
    hunt_id=10,
    meta_id=0,
    meta_code="TROUND",
    category_id=999,
):
    r = RoundData()
    r.id = id
    r.name = name
    r.hunt_id = hunt_id
    r.meta_id = meta_id
    r.meta_code = meta_code
    r.category_id = category_id
    return r


def make_puzzle_row(
    id,
    name,
    solved=False,
    solution=None,
    is_meta=False,
    is_metameta=False,
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
        "is_metameta": is_metameta,
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


def make_round_ref(id, name, meta_code="RC", is_meta_of=False):
    """Build a round reference dict as embedded in a puzzle row."""
    return {"id": id, "name": name, "meta_code": meta_code, "is_meta_of": is_meta_of}
