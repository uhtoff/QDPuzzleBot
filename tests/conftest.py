"""Shared fixtures for the QDPuzzleBot test suite."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.store.hunt_data import HuntData
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import TagGroupData

_LIVE_HUNT_ID = None

# --- test-database guard ---------------------------------------------------
#
# A dozen store-layer files (test_tag_edges, test_hunt_category_cascade, ...)
# run real INSERT/DELETE statements against whatever database `config.database`
# names -- there is nothing in the tests themselves that checks it is a *test*
# database. On a checkout whose config.json carries production credentials (a
# deployed host, or a developer who copied them), `pytest` would write to, and
# delete rows from, production. So refuse the whole session up front rather
# than trusting each live test to check for itself: a guard you have to
# remember to request is the bug, not the fix.

_TEST_DATABASE_SUFFIX = "_test"


def _is_test_database(name):
    """True only for a database name that ends in ``_test``.

    Deliberately strict and with no environment escape hatch: an override is
    exactly what a misconfigured production host would end up setting.
    """
    return isinstance(name, str) and name.endswith(_TEST_DATABASE_SUFFIX)


def guard_test_database(name):
    """Raise ``pytest.UsageError`` unless ``name`` is a test database.

    Pure in the database name, so it can be driven directly by
    tests/test_test_database_guard.py -- it neither reads the config nor
    connects to MySQL (connecting would re-introduce the import-time database
    dependency that the lazy-connect change removed).
    """
    if _is_test_database(name):
        return
    raise pytest.UsageError(
        f"REFUSING TO RUN THE TEST SUITE against database {name!r}: it is not a "
        f"test database (the name must end in '{_TEST_DATABASE_SUFFIX}'). This "
        "suite inserts and deletes real rows, so running it here risks "
        'destroying live data. Point the "database" key in config.json at a '
        'local test database (e.g. "puzzlebot_test") and run pytest again.'
    )


def pytest_sessionstart(session):
    """Refuse the session before a single test can touch the database.

    `pytest_sessionstart` fires before collection, so this runs earlier than
    any test module import, fixture, or query -- and it fails once, loudly,
    for the whole run instead of once per live test.
    """
    from bot.utils import config

    guard_test_database(config.database)


def live_hunt_id():
    """Return the id of a real hunts row for live-DB tests to FK against.

    The store-layer live tests (test_tag_edges, test_is_target_derivation,
    test_get_unassigned) insert tag_group/puzzle rows whose hunt_id FKs
    hunts(id). Rather than depend on a fixed seeded hunt id, look one up at
    runtime; only if the hunts table is empty do we create a throwaway hunt so
    there is always a valid parent. The result is cached for the test session.
    """
    global _LIVE_HUNT_ID
    if _LIVE_HUNT_ID is not None:
        return _LIVE_HUNT_ID

    from bot.store import mydb

    cur = mydb.cursor()
    cur.execute("SELECT id FROM hunts ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO hunts (name) VALUES (%s)", ("live-test-hunt",))
        hunt_id = cur.lastrowid
        mydb.commit()
    else:
        hunt_id = row[0]
    cur.close()
    _LIVE_HUNT_ID = hunt_id
    return _LIVE_HUNT_ID


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
        "on_solved_sheet": False,
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


def make_round_ref(id, name, group_code="RC", is_target_of=False, edges=("feeds",)):
    """Build a round reference dict as embedded in a puzzle row."""
    return {
        "id": id,
        "name": name,
        "group_code": group_code,
        "is_target_of": is_target_of,
        "edges": list(edges),
    }


def make_hunt(
    id=10,
    google_sheet_id="SHEET1",
    solved_sheet_id="ARCHIVE1",
    **kwargs,
):
    """Return a HuntData with sensible defaults.

    Extra attributes can be set via kwargs, e.g.::

        make_hunt(evidence_sheet_page_id=None)
    """
    h = HuntData()
    h.id = id
    h.google_sheet_id = google_sheet_id
    h.solved_sheet_id = solved_sheet_id
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


def make_web_app(blueprint, *, cog=None, get_guild=None):
    """Build the bare Quart app that every web-route test hand-rolls: a fresh
    app with the fixed test secret key and `blueprint` ("actions" or "api")
    registered.

    If `cog` is given, `app.bot` is wired so `app.bot.get_cog(...)` returns it,
    and `app.bot.get_guild(...)` returns a MagicMock (or `get_guild`, when a
    caller needs to assert identity against a specific mock later).
    """
    from quart import Quart

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"
    if cog is not None:
        app.bot = MagicMock()
        app.bot.get_cog.return_value = cog
        app.bot.get_guild.return_value = get_guild if get_guild is not None else MagicMock()
    if blueprint == "actions":
        from bot.web.actions import actions_bp as bp
    elif blueprint == "api":
        from bot.web.api import api_bp as bp
    else:
        raise ValueError(f"unknown blueprint {blueprint!r}")
    app.register_blueprint(bp)
    return app


def make_hunt_ctx(hunt_id=10, uid="U1", archived=False, guild_snowflake="123"):
    """Return the actions_bp request context dict every actions-route fixture
    hand-rolls: ``{"hunt": {...}, "guild_snowflake": ...}``."""
    return {"hunt": {"id": hunt_id, "uid": uid, "archived": archived},
            "guild_snowflake": guild_snowflake}


def make_api_hunt(archived=False):
    """The hunt dict api_bp's ``_load_visible_hunt`` resolves — the api-route
    counterpart of the hunt embedded in :func:`make_hunt_ctx`."""
    return {"id": 10, "uid": "U1", "archived": archived, "guild_id": 1}


def start_patches(patches):
    """Start each patch in `patches`, in order, returning the started mocks.

    If a later `start()` raises, stop whichever patches already started before
    re-raising -- otherwise a failure mid-list leaks patches for the rest of
    the test session.
    """
    started = []          # the patchers, so the rollback below can stop them
    mocks = []            # what start() handed back, which is what callers want
    try:
        for p in patches:
            mocks.append(p.start())
            started.append(p)
    except BaseException:
        for p in reversed(started):
            p.stop()
        raise
    return mocks


def stop_patches(patches):
    """Stop every patch in `patches` (the same list passed to start_patches)."""
    for p in patches:
        p.stop()


def make_cog(puzzle=None, hunt=None):
    """Return a Puzzles cog stub with get_puzzle/get_hunt/service mocked.

    ``puzzle`` is returned by ``cog.get_puzzle``; ``hunt`` (or a fresh
    MagicMock) is returned by ``cog.get_hunt``.
    """
    from bot.cogs.puzzles import Puzzles

    cog = Puzzles.__new__(Puzzles)
    # Real Puzzles.__init__ always sets this (with_puzzle_mutex's
    # _refresh_environment reads it via self.environment.get(...)); __new__
    # bypasses __init__, so it must be stubbed in here to match.
    cog.environment = {}
    cog.get_puzzle = lambda ctx: puzzle
    cog.get_hunt = lambda ctx: hunt or MagicMock()
    cog.service = MagicMock()
    # Fire-and-forget async refreshes are wrapped in asyncio.create_task, so they
    # must return awaitables even when a test doesn't care about them.
    cog.service.refresh_informs_targets = AsyncMock()
    return cog
