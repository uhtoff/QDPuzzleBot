"""M2/M5: the group-wide bang edges hold EVERY affected puzzle's lock.

A command that mutates a whole group's membership must take one sorted
acquisition over the target and every member (`puzzles_lock(convert_lock_ids(...))`,
see bot/core/locks.py). Holding only the context puzzle's lock — or none at all —
leaves the #27 lost-update window open: a concurrent locked command loads a member
BEFORE the group mutation, commits AFTER it, and PuzzleJsonDb.commit's
bidirectional tag-edge diff then DELETEs the edge row the group mutation just
wrote (see MySQLPuzzleJsonDb.commit).

LIVE MySQL (puzzlebot_test) — follows tests/test_bang_edge_atomicity.py. The
behaviour under test is what a real re-read and a real edge diff do, which a mock
cannot reproduce. Rows are prefixed ``LockM2_``/``LockM5_`` and deleted in a
``finally``.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.puzzles import Puzzles, with_puzzle_mutex
from bot.core.locks import _PUZZLE_LOCKS
from bot.store import PuzzleJsonDb, mydb
from tests.conftest import live_hunt_id


# --------------------------------------------------------------- live fixtures
def _insert_tag_group(name, group_type, category_id):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tag_group (hunt_id, name, type, category_id) VALUES (%s,%s,%s,%s)",
        (live_hunt_id(), name, group_type, category_id),
    )
    gid = cur.lastrowid
    cur.close()
    return gid


def _set_target(tag_group_id, puzzle_id):
    cur = mydb.cursor()
    cur.execute(
        "UPDATE tag_group SET target_puzzle_id = %s WHERE id = %s",
        (puzzle_id, tag_group_id),
    )
    cur.close()


def _insert_puzzle(name, channel_id=None):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name, channel_id) VALUES (%s,%s,%s)",
        (live_hunt_id(), name, channel_id),
    )
    pid = cur.lastrowid
    cur.close()
    return pid


def _insert_edge(tag_group_id, puzzle_id, edge_type):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s,%s,%s)",
        (tag_group_id, puzzle_id, edge_type),
    )
    cur.close()


def _edges(tag_group_id, puzzle_id):
    cur = mydb.cursor()
    cur.execute(
        "SELECT edge_type FROM tags WHERE tag_group_id=%s AND puzzle_id=%s",
        (tag_group_id, puzzle_id),
    )
    edges = sorted(r[0] for r in cur.fetchall())
    cur.close()
    return edges


def _cleanup(group_ids, puzzle_ids):
    cur = mydb.cursor()
    for pid in puzzle_ids:
        cur.execute("DELETE FROM tags WHERE puzzle_id = %s", (pid,))
        cur.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
        _PUZZLE_LOCKS.pop(pid, None)
    for gid in group_ids:
        cur.execute("DELETE FROM tags WHERE tag_group_id = %s", (gid,))
        cur.execute("DELETE FROM tag_group WHERE id = %s", (gid,))
    cur.close()


# --------------------------------------------------------------- mock plumbing
def _make_ctx(message_id, channel_id=None):
    ctx = MagicMock()
    ctx.message.id = message_id
    ctx.send = AsyncMock()
    ctx.channel.id = channel_id
    # Real Context objects never carry `.env`; a bare MagicMock auto-vivifies it
    # as a truthy child mock, which with_puzzle_mutex would use instead of the
    # seeded self.environment (see tests/test_bang_edge_atomicity.py).
    ctx.env = None
    return ctx


def _seed_environment(cog, ctx, *, channel_type, puzzle=None, tag_group=None, hunt=None):
    cog.environment[ctx.message.id] = {
        "channel_type": channel_type,
        "hunt": hunt,
        "tag_group": tag_group,
        "puzzle": puzzle,
        "guild": ctx.guild,
        "guild_data": None,
        "gsheet_cog": None,
    }


def _make_cog():
    """A real Puzzles cog with a real PuzzleService, with only the Sheets fan-out
    stubbed — the DB writes under test are the real ones."""
    cog = Puzzles(MagicMock())
    cog.service.refresh_targets = AsyncMock(return_value=[])
    cog.service.refresh_target = AsyncMock()
    cog.service.refresh_unassigned_safe = AsyncMock()
    return cog


def _make_category(category_id, channel_ids):
    category = MagicMock()
    category.id = category_id
    channels = []
    for cid in channel_ids:
        channel = MagicMock()
        channel.id = cid
        channels.append(channel)
    category.channels = channels
    return category


# ------------------------------------------------------------------------ M2
@pytest.mark.asyncio
async def test_sync_does_not_lose_its_tag_to_a_concurrent_command_on_a_member():
    """!sync writes every member's edges, so it must hold every member's lock.

    Interleaving: a locked command (B) on the member has already re-read the
    member under its lock when !sync (A) runs. If A does not take B's lock, A
    tags the member while B holds it, and B's commit then diffs its pre-sync
    tag_edges against the DB and deletes the edge A just wrote (#27).
    """
    category_id = 880001
    channel_id = 880002
    gid = _insert_tag_group("LockM2_group", "Metapuzzle", category_id)
    target = _insert_puzzle("LockM2_target")
    member = _insert_puzzle("LockM2_member", channel_id=channel_id)
    try:
        _insert_edge(gid, target, "target")
        _set_target(gid, target)

        cog = _make_cog()

        # --- B: an ordinary locked bang command on the member puzzle ---------
        b_holds_lock = asyncio.Event()
        b_may_commit = asyncio.Event()

        @with_puzzle_mutex(wait=True)
        async def concurrent_member_command(self, ctx):
            # with_puzzle_mutex re-read the member under the lock; this snapshot
            # is what a !solve/!status would commit at the end of its body.
            snapshot = self.environment[ctx.message.id]["puzzle"]
            b_holds_lock.set()
            try:
                # Bounded: once !sync takes the member's lock properly, it blocks
                # here and nothing will ever set this event — the timeout is what
                # lets the fixed ordering complete instead of deadlocking the test.
                await asyncio.wait_for(b_may_commit.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            snapshot.status = "Critical"
            PuzzleJsonDb.commit(snapshot)

        ctx_b = _make_ctx(770002, channel_id=channel_id)
        _seed_environment(
            cog, ctx_b, channel_type="Puzzle",
            puzzle=PuzzleJsonDb.get_by_attr(id=member),
        )
        b_task = asyncio.create_task(concurrent_member_command(cog, ctx_b))
        await b_holds_lock.wait()

        # --- A: !sync, run in the group's main channel -----------------------
        ctx_a = _make_ctx(770001, channel_id=999999)
        ctx_a.channel.category = _make_category(category_id, [channel_id])
        ctx_a.guild.categories = []
        from bot.store import HuntJsonDb
        hunt = HuntJsonDb.get_by_attr(id=live_hunt_id())
        _seed_environment(
            cog, ctx_a, channel_type="Metapuzzle",
            puzzle=PuzzleJsonDb.get_by_attr(id=target), hunt=hunt,
        )

        await Puzzles.sync.callback(cog, ctx_a)

        b_may_commit.set()
        await b_task

        assert _edges(gid, member) == ["feeds"], (
            "!sync tagged the member without holding its lock; the concurrent "
            "command's commit diffed a pre-sync snapshot and deleted the edge"
        )
    finally:
        _cleanup([gid], [target, member])


# ------------------------------------------------------------------------ M5
@pytest.mark.asyncio
async def test_convert_to_puzzle_does_not_let_a_feeder_resurrect_the_deleted_group():
    """!convert_to_puzzle deletes the group, cascade-deleting every member's edge
    rows — so it must hold every member's lock, like its convert siblings.

    Interleaving: a locked command (B) on a feeder has already re-read the feeder
    under its lock when the demote (A) runs. If A does not take B's lock, B's
    commit afterwards diffs its pre-demote tag_edges against the now-empty DB and
    tries to INSERT the edge back against a tag_group row that no longer exists —
    the IntegrityError the service's own comment warns about, raised in the middle
    of an unrelated command that has already written the puzzle row.
    """
    category_id = 880011
    gid = _insert_tag_group("LockM5_group", "Metapuzzle", category_id)
    target = _insert_puzzle("LockM5_target", channel_id=880012)
    feeder = _insert_puzzle("LockM5_feeder", channel_id=880013)
    try:
        _insert_edge(gid, target, "target")
        _insert_edge(gid, feeder, "feeds")
        _set_target(gid, target)

        cog = _make_cog()
        # Discord/Sheets fan-out of the demote — not what this test is about.
        cog.service.notifier = MagicMock()
        cog.service.notifier.board_changed = AsyncMock()
        cog.service.notifier.refresh_info = AsyncMock()
        cog.service._refresh_channel_glyph = AsyncMock()
        cog.service._refresh_host_target_glyphs = AsyncMock()
        cog.service._gsheet_cog = MagicMock(return_value=None)
        cog.info = AsyncMock()

        b_holds_lock = asyncio.Event()
        b_may_commit = asyncio.Event()
        b_error = {}

        @with_puzzle_mutex(wait=True)
        async def concurrent_feeder_command(self, ctx):
            snapshot = self.environment[ctx.message.id]["puzzle"]
            b_holds_lock.set()
            try:
                await asyncio.wait_for(b_may_commit.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            snapshot.status = "Critical"
            try:
                PuzzleJsonDb.commit(snapshot)
            except Exception as exc:            # noqa: BLE001 - recorded, asserted below
                b_error["exc"] = exc

        ctx_b = _make_ctx(770012, channel_id=880013)
        _seed_environment(
            cog, ctx_b, channel_type="Puzzle",
            puzzle=PuzzleJsonDb.get_by_attr(id=feeder),
        )
        b_task = asyncio.create_task(concurrent_feeder_command(cog, ctx_b))
        await b_holds_lock.wait()

        from bot.store import HuntJsonDb, TagGroupJsonDb
        hunt = HuntJsonDb.get_by_attr(id=live_hunt_id())
        ctx_a = _make_ctx(770011, channel_id=880012)
        _seed_environment(
            cog, ctx_a, channel_type="Metapuzzle", hunt=hunt,
            puzzle=PuzzleJsonDb.get_by_attr(id=target),
            tag_group=TagGroupJsonDb.get_by_attr(id=gid),
        )

        await Puzzles.convert_to_puzzle.callback(cog, ctx_a)

        b_may_commit.set()
        await b_task

        assert "exc" not in b_error, (
            "!convert_to_puzzle deleted the group without holding the feeder's "
            f"lock; the concurrent command's commit then blew up: {b_error.get('exc')!r}"
        )
        assert _edges(gid, feeder) == [], (
            "the concurrent command's pre-demote snapshot resurrected an edge to "
            "the deleted group"
        )
    finally:
        _cleanup([gid], [target, feeder])
