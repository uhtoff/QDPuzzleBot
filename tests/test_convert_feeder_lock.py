"""M26 — the web convert-to-feeder demote must take the same sorted member-lock
acquisition every other group-wide convert/delete edge takes.

``convert_to_feeder`` (bot/web/actions.py) demotes a target puzzle by deleting the
tag group it targets. ``MySQLTagGroupJsonDb.delete`` deletes the ``tag_group`` row
*and* ``DELETE FROM tags WHERE tag_group_id = …`` — i.e. it destroys EVERY member
feeder's membership edge, not just the target's. That is a group-wide mutation, but
the route held only ``puzzle_lock(target_id)``: a command already in flight on a
member feeder (a solve loaded pre-delete) could commit across the delete, and
``MySQLPuzzleJsonDb.commit``'s edge diff then re-INSERTs the member's edge against
a ``tag_group`` row that no longer exists — the #27 lost-update mechanism, here
landing as an FK violation (``tags.RoundID``).

Unlike the convert_to_type family, this path never reaches ``bulk_re_edge``
(``PuzzleService.convert_to_puzzle`` calls ``feeders_orphaned_by_convert``,
``_teardown_converted_group_categories``, ``TagGroupJsonDb.delete``,
``PuzzleJsonDb.commit`` and the glyph/sheet refreshes — none of which take a puzzle
lock), so an outer ``puzzles_lock`` cannot self-deadlock against an internal one.
``test_route_completes_without_self_deadlock`` is the standing guard on that.

LIVE MySQL (puzzlebot_test) for the interleaving test — the behaviour under test is
what a real commit's edge diff does against a real FK, which a mock cannot
reproduce. Rows are prefixed ``LockM26_`` and removed in a ``finally``; nothing here
counts or scans whole tables, so it is safe alongside another suite.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.locks import _PUZZLE_LOCKS, puzzle_lock
from bot.store import PuzzleJsonDb, mydb
from tests.conftest import (live_hunt_id, make_hunt, make_hunt_ctx, make_puzzle,
                            make_round, make_web_app, start_patches, stop_patches)


# ---------------------------------------------------------------------------
# Live-DB fixture helpers (local — the parallel runner shards by file)
# ---------------------------------------------------------------------------

def _insert_tag_group(name):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tag_group (hunt_id, name, group_code) VALUES (%s, %s, %s)",
        (live_hunt_id(), name, "LM26"),
    )
    gid = cur.lastrowid
    cur.close()
    return gid


def _insert_puzzle(name):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)", (live_hunt_id(), name)
    )
    pid = cur.lastrowid
    cur.close()
    return pid


def _insert_edge(gid, pid, edge_type):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s,%s,%s)",
        (gid, pid, edge_type),
    )
    cur.close()


def _set_target(gid, pid):
    cur = mydb.cursor()
    cur.execute("UPDATE tag_group SET target_puzzle_id = %s WHERE id = %s", (pid, gid))
    cur.close()


def _tag_rows_for_group(gid):
    """The tags rows still pointing at *gid*. Scoped to one group id — never a
    whole-table scan (another suite may be writing to this database)."""
    cur = mydb.cursor()
    cur.execute(
        "SELECT puzzle_id, edge_type FROM tags WHERE tag_group_id = %s", (gid,))
    rows = sorted(cur.fetchall())
    cur.close()
    return rows


def _cleanup(gid, puzzle_ids):
    cur = mydb.cursor()
    cur.execute("DELETE FROM tags WHERE tag_group_id = %s", (gid,))
    for pid in puzzle_ids:
        cur.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
    cur.execute("DELETE FROM tag_group WHERE id = %s", (gid,))
    cur.close()
    for pid in puzzle_ids:
        _PUZZLE_LOCKS.pop(pid, None)


def _real_service():
    """A real PuzzleService whose Discord/Sheets side-effects are inert.

    Deliberately the production object, not a stub: the durable mutation under
    test (``TagGroupJsonDb.delete`` + the target's commit) must be the real code.
    ``bot.get_cog`` returns None so ``_gsheet_cog`` finds no GoogleSheets cog, and
    the route passes ``guild=None`` (no guild snowflake in the ctx), which makes
    ``_refresh_channel_glyph`` and the split-category teardown no-op.
    """
    from bot.core.puzzle_service import PuzzleService

    bot = MagicMock()
    bot.get_cog.return_value = None
    service = PuzzleService(bot)
    service.refresh_unassigned_safe = AsyncMock()
    return service


def _route_patches(hunt_id):
    return [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle",
              AsyncMock(return_value=(make_hunt_ctx(hunt_id=hunt_id,
                                                    guild_snowflake=None), None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
    ]


# ---------------------------------------------------------------------------
# The outcome test: a concurrent write on a MEMBER must not survive the demote
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_member_write_in_flight_cannot_resurrect_the_deleted_groups_edge():
    """The #27 interleaving, on the demote path.

    A solve is already in flight on a member feeder: it holds that feeder's lock
    and has read the puzzle (edges: ``feeds`` → the group). The demote then runs.
    Pre-fix it takes only the TARGET's lock, so it deletes the group — and every
    member's tags row with it — while the feeder's commit is still pending; that
    commit's edge diff then tries to re-INSERT ``(group, feeder, 'feeds')`` against
    a tag_group that is gone, and MariaDB rejects it on the ``RoundID`` FK.

    Post-fix the route holds the feeder's lock too, so it cannot start until the
    in-flight commit is done — the write lands, and the demote then removes the
    edges cleanly.

    Two members (target + feeder) is the minimum that can fail: a target-only
    group has no member edge to lose, and passes either way.
    """
    gid = _insert_tag_group("LockM26_Round")
    target_id = _insert_puzzle("LockM26_Meta")
    feeder_id = _insert_puzzle("LockM26_Feeder")
    try:
        _set_target(gid, target_id)
        _insert_edge(gid, target_id, "target")
        _insert_edge(gid, feeder_id, "feeds")

        cog = MagicMock()
        cog.service = _real_service()
        app = make_web_app("actions", cog=cog)

        holds_lock = asyncio.Event()
        may_commit = asyncio.Event()
        writer_error = []

        async def in_flight_solve():
            """The bang edge on the feeder: lock, re-read, mutate, commit."""
            async with puzzle_lock(feeder_id):
                member = PuzzleJsonDb.get_by_attr(id=feeder_id)
                assert member is not None
                holds_lock.set()
                await may_commit.wait()
                member.status = "Critical"
                try:
                    PuzzleJsonDb.commit(member)
                except Exception as exc:          # noqa: BLE001 — recorded, asserted below
                    writer_error.append(exc)

        patches = _route_patches(live_hunt_id())
        start_patches(patches)
        try:
            solve = asyncio.create_task(in_flight_solve())
            await asyncio.wait_for(holds_lock.wait(), timeout=5)

            route = asyncio.create_task(
                app.test_client().post(f"/api/puzzles/{target_id}/convert-to-feeder"))
            # Long enough for the demote to run to completion if nothing blocks it.
            await asyncio.sleep(0.2)

            may_commit.set()
            await asyncio.wait_for(solve, timeout=5)
            resp = await asyncio.wait_for(route, timeout=10)
        finally:
            stop_patches(patches)

        assert resp.status_code == 200
        assert writer_error == [], (
            "the in-flight member commit ran across the group delete and its edge "
            f"diff was rejected: {writer_error!r}"
        )
        assert _tag_rows_for_group(gid) == [], (
            "a membership edge for the deleted group survived the demote"
        )
    finally:
        _cleanup(gid, [target_id, feeder_id])
        _PUZZLE_LOCKS.pop(gid, None)


# ---------------------------------------------------------------------------
# Self-deadlock guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_completes_without_self_deadlock():
    """Uncontended, the demote must finish promptly.

    ``asyncio.Lock`` is not reentrant, so if anyone later nests a second
    acquisition of these same ids inside this route (in the route, or in
    ``PuzzleService.convert_to_puzzle``'s callees — the trap ``bulk_re_edge``
    documents), the task blocks on itself forever with no error and no timeout.
    This test is the tripwire: it would hang and blow the ``wait_for``.
    """
    gid = _insert_tag_group("LockM26_DeadlockRound")
    target_id = _insert_puzzle("LockM26_DeadlockMeta")
    feeder_id = _insert_puzzle("LockM26_DeadlockFeeder")
    try:
        _set_target(gid, target_id)
        _insert_edge(gid, target_id, "target")
        _insert_edge(gid, feeder_id, "feeds")

        cog = MagicMock()
        cog.service = _real_service()
        app = make_web_app("actions", cog=cog)

        patches = _route_patches(live_hunt_id())
        start_patches(patches)
        try:
            resp = await asyncio.wait_for(
                app.test_client().post(
                    f"/api/puzzles/{target_id}/convert-to-feeder"),
                timeout=10)
        finally:
            stop_patches(patches)

        assert resp.status_code == 200
        assert (await resp.get_json())["orphaned"] == 1
    finally:
        _cleanup(gid, [target_id, feeder_id])
        _PUZZLE_LOCKS.pop(gid, None)


# ---------------------------------------------------------------------------
# Structural pin (weaker: asserts the acquisition, not an outcome)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_demote_route_holds_target_and_members():
    """Matches ``test_puzzle_keyed_convert_route_holds_target_and_members`` in
    tests/test_lock_ordering.py: the exact id SET must be target + every member,
    which the interleaving test above (one member) cannot pin on its own."""
    puzzle = make_puzzle(id=5350, is_target=True)
    tag_group = make_round(id=5309, target_puzzle_id=5350)
    seen = {}

    async def fake_demote(_guild, _hunt, _puzzle):
        seen["held"] = {pid for pid in (5348, 5349, 5350)
                        if _PUZZLE_LOCKS[pid].locked()}
        return 2

    cog = MagicMock()
    cog.service.convert_to_puzzle = fake_demote
    cog.service.refresh_unassigned_safe = AsyncMock()
    app = make_web_app("actions", cog=cog)

    pdb = MagicMock()
    pdb.get_by_attr.side_effect = lambda **kw: puzzle if kw.get("id") == 5350 else None
    pdb.get_all_from_tag_group.return_value = [
        MagicMock(id=5350), MagicMock(id=5348), MagicMock(id=5349)]
    rdb = MagicMock(); rdb.get_by_attr.return_value = tag_group
    hdb = MagicMock(); hdb.get_by_attr.return_value = make_hunt()

    patches = _route_patches(10) + [
        patch("bot.store.TagGroupJsonDb", rdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    start_patches(patches)
    try:
        resp = await asyncio.wait_for(
            app.test_client().post("/api/puzzles/5350/convert-to-feeder"), timeout=5)
        assert resp.status_code == 200
    finally:
        stop_patches(patches)
        for pid in (5348, 5349, 5350):
            _PUZZLE_LOCKS.pop(pid, None)

    assert seen["held"] == {5348, 5349, 5350}
