"""H4: the bang edge reads its puzzle INSIDE the per-puzzle lock.

cog_before_invoke resolves the channel's puzzle and snapshots it BEFORE
with_puzzle_mutex acquires the lock, and services commit the object they are handed
field by field -- so a command acting on that snapshot silently clobbers whatever a
concurrent writer changed while it waited.

LIVE MySQL (puzzlebot_test) -- follows tests/test_tag_edges.py. The whole point is
what a real re-read returns, so PuzzleJsonDb is deliberately NOT patched here.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.puzzles import Puzzles, with_puzzle_mutex
from bot.core.locks import _PUZZLE_LOCKS
from bot.store import PuzzleJsonDb, mydb
from tests.conftest import live_hunt_id


def _insert_puzzle(name="H4Test", status=""):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name, status) VALUES (%s, %s, %s)",
        (live_hunt_id(), name, status),
    )
    pid = cur.lastrowid
    cur.close()
    return pid


def _set_status(puzzle_id, status):
    """A concurrent writer -- a web action or another bang command."""
    cur = mydb.cursor()
    cur.execute("UPDATE puzzles SET status = %s WHERE id = %s", (status, puzzle_id))
    cur.close()


def _delete_puzzle(puzzle_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()


def _make_ctx(message_id):
    ctx = MagicMock()
    ctx.message.id = message_id
    ctx.send = AsyncMock()
    # Real discord.py Context objects never carry an `env` attribute -- nothing in
    # the bot ever sets ctx.env (see with_hunt_sheet_mutex's comment). A bare
    # MagicMock() auto-vivifies `.env` as a truthy child mock, which would make
    # with_puzzle_mutex's `getattr(ctx, "env", None) or self.environment.get(...)`
    # pick up that bogus mock instead of the seeded self.environment. Setting it
    # to None explicitly matches the real object being simulated.
    ctx.env = None
    return ctx


def _seed_environment(cog, ctx, puzzle):
    cog.environment[ctx.message.id] = {
        "channel_type": "Puzzle",
        "hunt": None,
        "tag_group": None,
        "puzzle": puzzle,
        "guild": None,
        "guild_data": None,
        "gsheet_cog": None,
    }


@pytest.mark.asyncio
async def test_command_body_sees_the_row_as_it_is_when_the_lock_is_taken():
    puzzle_id = _insert_puzzle(status="")
    try:
        cog = Puzzles(MagicMock())
        seen = {}

        @with_puzzle_mutex(wait=True)
        async def probe(self, ctx):
            seen["status"] = self.environment[ctx.message.id]["puzzle"].status

        ctx = _make_ctx(1001)
        # The pre-lock snapshot, exactly as cog_before_invoke would leave it.
        _seed_environment(cog, ctx, PuzzleJsonDb.get_by_attr(id=puzzle_id))

        # Someone else writes the row after that snapshot and before we run --
        # the window the per-puzzle lock was supposed to close.
        _set_status(puzzle_id, "Critical")

        await probe(cog, ctx)

        assert seen["status"] == "Critical", (
            "the command body acted on a snapshot read before the lock"
        )
    finally:
        _PUZZLE_LOCKS.pop(puzzle_id, None)
        _delete_puzzle(puzzle_id)


@pytest.mark.asyncio
async def test_a_waiting_command_re_reads_what_the_holder_wrote():
    """The realistic ordering: B blocks on A's lock, and must see A's write."""
    puzzle_id = _insert_puzzle(status="")
    try:
        cog = Puzzles(MagicMock())
        seen = {}

        @with_puzzle_mutex(wait=True)
        async def holder(self, ctx):
            _set_status(puzzle_id, "Critical")   # A's write, under the lock
            await asyncio.sleep(0)

        @with_puzzle_mutex(wait=True)
        async def waiter(self, ctx):
            seen["status"] = self.environment[ctx.message.id]["puzzle"].status

        ctx_a, ctx_b = _make_ctx(2001), _make_ctx(2002)
        # BOTH snapshots are taken up front -- distinct ctx.message.id values,
        # because self.environment is keyed on it and a shared id would make the
        # two commands overwrite each other's entry and prove nothing.
        _seed_environment(cog, ctx_a, PuzzleJsonDb.get_by_attr(id=puzzle_id))
        _seed_environment(cog, ctx_b, PuzzleJsonDb.get_by_attr(id=puzzle_id))

        await asyncio.gather(holder(cog, ctx_a), waiter(cog, ctx_b))

        assert seen["status"] == "Critical", (
            "the waiting command used its pre-lock snapshot and lost A's write"
        )
    finally:
        _PUZZLE_LOCKS.pop(puzzle_id, None)
        _delete_puzzle(puzzle_id)
