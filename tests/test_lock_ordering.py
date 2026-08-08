"""H5 — convert vs. delete/archive must not deadlock on overlapping puzzle locks.

The bug: ``bulk_re_edge`` acquired every *member's* ``puzzle_lock`` internally, while
its convert callers held the **target's** lock — acquisition order ``T, then
sorted(members)``. ``delete_round``/``delete_hunt``/``archive_hunt`` take
``puzzles_lock(member_ids)``, and that id list **includes** the target (it carries a
``target`` self-edge row in the group) — order ``sorted(members ∪ {T})``. Whenever a
member id sorts below the target id (the feeders-first creation order the domain
supports), convert holds ``T`` and waits on member ``a`` while delete holds ``a`` and
waits on ``T``. ``asyncio.Lock`` never times out, so both tasks wedge until restart.

The fix is structural: **one** sorted acquisition per mutation, at the edge.
``bulk_re_edge`` locks nothing; every convert edge takes ``puzzles_lock`` over
``convert_lock_ids(...)`` (target + members) in the same globally sorted order the
delete/archive edges already use, so no cycle can form.

These tests pin both halves of that invariant:
  * the service acquires no puzzle lock of its own (so nesting is impossible), and
  * each convert edge holds the target *and* every member for the whole call.

Together they make the ordering cycle unconstructible. Helpers are local to this
file (the parallel runner shards by file).
"""
import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_hunt, make_hunt_ctx, make_puzzle, make_round,
                            make_web_app, start_patches, stop_patches)


# ---------------------------------------------------------------------------
# Helpers (local — do not import from sibling test files)
# ---------------------------------------------------------------------------

def _make_cog():
    from bot.cogs.puzzles import Puzzles

    bot = MagicMock()
    bot.get_cog.return_value = None  # no GoogleSheets cog
    return Puzzles(bot)


@contextlib.contextmanager
def _service_mocks(cog):
    """Patch the JsonDb seams and stub the service's sheet/board side-effects."""
    cog.service.refresh_target = AsyncMock()
    cog.service.refresh_targets = AsyncMock(return_value=[])
    cog.service.notifier = MagicMock()
    cog.service.notifier.board_changed = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        mrdb.get_by_attr.return_value = None
        mhdb.get_by_attr.return_value = make_hunt()
        yield mpdb, mrdb, mhdb


def _held(*puzzle_ids):
    """Which of *puzzle_ids* currently have their per-puzzle lock held."""
    from bot.core.locks import _PUZZLE_LOCKS

    return {pid for pid in puzzle_ids if _PUZZLE_LOCKS[pid].locked()}


# ---------------------------------------------------------------------------
# Half 1: the service takes no puzzle locks of its own
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bulk_re_edge_does_not_acquire_member_locks():
    """``bulk_re_edge`` must not lock its members: the caller already holds them.

    Called with a member's lock already held by this task — which is exactly what
    the fixed convert edges do — a ``bulk_re_edge`` that re-acquired it would block
    forever on a non-reentrant ``asyncio.Lock``. Pre-fix this hangs and blows the
    ``wait_for`` timeout; post-fix it returns its summary.
    """
    from bot.core.locks import puzzle_lock

    cog = _make_cog()
    grp = make_round(id=705)
    hunt = make_hunt()
    member = make_puzzle(id=7042, tags=[705])
    member.tag_edges = {705: {"feeds"}}

    with _service_mocks(cog) as (mpdb, _mrdb, _mhdb):
        mpdb.get_all_from_tag_group.return_value = [member]
        async with puzzle_lock(7042):
            summary = await asyncio.wait_for(
                cog.service.bulk_re_edge(grp, "informs", hunt), timeout=2)

    assert summary == "flipped 1 feeds → informs"


@pytest.mark.asyncio
async def test_bulk_re_edge_still_serializes_via_the_caller_held_lock():
    """The #27 guarantee is preserved, just relocated: an in-flight command on a
    member cannot commit its stale edge across the flip, because the *caller* holds
    that member's lock for the whole flip."""
    from bot.core.locks import puzzle_lock, puzzles_lock

    cog = _make_cog()
    grp = make_round(id=706)
    hunt = make_hunt()
    member = make_puzzle(id=7043, tags=[706])
    member.tag_edges = {706: {"feeds"}}

    order = []

    async def in_flight_command():
        async with puzzle_lock(7043):
            order.append("solve-start")
            await asyncio.sleep(0.02)
            order.append("solve-end")

    with _service_mocks(cog) as (mpdb, _mrdb, _mhdb):
        mpdb.get_all_from_tag_group.return_value = [member]
        mpdb.bulk_update_edge_type.side_effect = lambda *a, **kw: order.append("flip")

        task = asyncio.create_task(in_flight_command())
        await asyncio.sleep(0)  # let in_flight_command grab the lock first
        async with puzzles_lock([7043]):          # the edge's single acquisition
            # Bounded: pre-fix the service re-acquires 7043 and this never returns.
            await asyncio.wait_for(
                cog.service.bulk_re_edge(grp, "informs", hunt), timeout=2)
        await task

    assert order == ["solve-start", "solve-end", "flip"]


# ---------------------------------------------------------------------------
# convert_lock_ids — what an edge must lock to mutate a group
# ---------------------------------------------------------------------------

class TestConvertLockIds:
    def test_grouped_puzzle_covers_target_and_members(self):
        from bot.core.locks import convert_lock_ids

        grp = make_round(id=9, target_puzzle_id=60)
        pdb = MagicMock()
        pdb.get_all_from_tag_group.return_value = [MagicMock(id=60), MagicMock(id=58)]
        rdb = MagicMock()
        rdb.get_by_attr.return_value = grp

        with patch("bot.store.PuzzleJsonDb", pdb), patch("bot.store.TagGroupJsonDb", rdb):
            assert convert_lock_ids(puzzle_id=60) == {58, 60}

    def test_bare_puzzle_is_just_itself(self):
        """A promote-in-place convert has no group yet — only the puzzle to lock."""
        from bot.core.locks import convert_lock_ids

        pdb = MagicMock()
        rdb = MagicMock()
        rdb.get_by_attr.return_value = None

        with patch("bot.store.PuzzleJsonDb", pdb), patch("bot.store.TagGroupJsonDb", rdb):
            assert convert_lock_ids(puzzle_id=60) == {60}

    def test_group_keyed_covers_target_and_members(self):
        from bot.core.locks import convert_lock_ids

        grp = make_round(id=9, target_puzzle_id=60)
        pdb = MagicMock()
        pdb.get_all_from_tag_group.return_value = [MagicMock(id=58)]

        with patch("bot.store.PuzzleJsonDb", pdb):
            assert convert_lock_ids(tag_group=grp) == {58, 60}

    def test_target_less_group_is_just_its_members(self):
        """A bare Group has target_puzzle_id 0 — never lock puzzle id 0."""
        from bot.core.locks import convert_lock_ids

        grp = make_round(id=9, target_puzzle_id=0)
        pdb = MagicMock()
        pdb.get_all_from_tag_group.return_value = [MagicMock(id=58), MagicMock(id=59)]

        with patch("bot.store.PuzzleJsonDb", pdb):
            assert convert_lock_ids(tag_group=grp) == {58, 59}


# ---------------------------------------------------------------------------
# Half 2: every convert edge holds target + members for the whole call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_group_keyed_convert_route_holds_target_and_members():
    """``change_group_type`` must hold the target's AND every member's lock while
    the service runs. Pre-fix it held only the target (feeder 5048 unlocked), which
    is the half of the cycle that lets a concurrent delete interleave."""
    tag_group = make_round(id=5009, name="Old", target_puzzle_id=5050)
    seen = {}

    async def fake_convert(_guild, _hunt, tg, _new_type):
        seen["held"] = _held(5048, 5050)
        return (tg, "ok")

    cog = MagicMock()
    cog.service.convert_tag_group_type = fake_convert
    cog.service.refresh_unassigned_safe = AsyncMock()
    app = make_web_app("actions", cog=cog)

    rdb = MagicMock(); rdb.get_by_attr.return_value = tag_group
    hdb = MagicMock(); hdb.get_by_attr.return_value = make_hunt()
    pdb = MagicMock()
    pdb.get_all_from_tag_group.return_value = [MagicMock(id=5050), MagicMock(id=5048)]

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_tag_group",
              AsyncMock(return_value=(make_hunt_ctx(), None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.TagGroupJsonDb", rdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    start_patches(patches)
    try:
        resp = await asyncio.wait_for(
            app.test_client().post("/api/tag_groups/5009/change-type",
                                   json={"type": "Metapuzzle"}),
            timeout=5)
        assert resp.status_code == 200
    finally:
        stop_patches(patches)

    assert seen["held"] == {5048, 5050}


@pytest.mark.asyncio
async def test_puzzle_keyed_convert_route_holds_target_and_members():
    """``_convert_to_type_handler`` (convert-to-capstone et al.) must hold the
    target's AND every member's lock — pre-fix it took ``puzzle_lock(puzzle_id)``
    alone and left the members to ``bulk_re_edge``'s nested acquisition."""
    puzzle = make_puzzle(id=5150, is_target=True)
    tag_group = make_round(id=5109, target_puzzle_id=5150)
    seen = {}

    async def fake_convert(_guild, _hunt, _puzzle, _new_type):
        seen["held"] = _held(5148, 5150)
        return (tag_group, "ok")

    cog = MagicMock()
    cog.service.convert_to_type = fake_convert
    cog.service.refresh_unassigned_safe = AsyncMock()
    app = make_web_app("actions", cog=cog)

    def puzzle_get(**kwargs):
        return puzzle if kwargs.get("id") == 5150 else None

    pdb = MagicMock()
    pdb.get_by_attr.side_effect = puzzle_get
    pdb.get_all_from_tag_group.return_value = [MagicMock(id=5150), MagicMock(id=5148)]
    rdb = MagicMock(); rdb.get_by_attr.return_value = tag_group
    hdb = MagicMock(); hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle",
              AsyncMock(return_value=(make_hunt_ctx(), None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.TagGroupJsonDb", rdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    start_patches(patches)
    try:
        resp = await asyncio.wait_for(
            app.test_client().post("/api/puzzles/5150/convert-to-capstone"), timeout=5)
        assert resp.status_code == 200
    finally:
        stop_patches(patches)

    assert seen["held"] == {5148, 5150}


@pytest.mark.asyncio
async def test_bang_convert_holds_target_and_members():
    """The bang convert commands took NO puzzle lock at all (no ``with_puzzle_mutex``
    on ``convert_to_meta``/``capstone``/…), so a concurrent solve could lose its
    update across the flip. They must take the same sorted set as the web edges."""
    from bot.cogs.puzzles import Puzzles
    from tests.conftest import make_ctx

    puzzle = make_puzzle(id=5250, is_target=True)
    tag_group = make_round(id=5209, target_puzzle_id=5250)
    hunt = make_hunt()
    seen = {}

    async def fake_convert(_guild, _hunt, _puzzle, _new_type):
        seen["held"] = _held(5248, 5250)
        return (tag_group, "ok")

    cog = _make_cog()
    cog.get_puzzle = lambda ctx: puzzle
    cog.get_hunt = lambda ctx: hunt
    cog.set_tag_group = lambda ctx, tag_group: None
    cog.set_channel_type = lambda ctx, channel_type: None
    cog.info = AsyncMock()
    cog.service = MagicMock()
    cog.service.convert_to_type = fake_convert

    pdb = MagicMock()
    pdb.get_by_attr.return_value = puzzle
    pdb.get_all_from_tag_group.return_value = [MagicMock(id=5250), MagicMock(id=5248)]
    rdb = MagicMock(); rdb.get_by_attr.return_value = tag_group

    with patch("bot.store.PuzzleJsonDb", pdb), patch("bot.store.TagGroupJsonDb", rdb):
        await asyncio.wait_for(
            Puzzles.convert_to_capstone.callback(cog, make_ctx()), timeout=5)

    assert seen["held"] == {5248, 5250}


# ---------------------------------------------------------------------------
# Aggravator: bang delete/archive must not hold the locks across the human confirm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bang_delete_round_confirms_before_locking():
    """Holding every member's lock across the confirm click blocks all solves and
    retags in the round for the length of the confirm window (up to the view's
    timeout). Confirm first, then lock."""
    from bot.cogs.puzzles import Puzzles
    from tests.conftest import make_cog, make_ctx

    tag_group = make_round(id=5307)
    cog = make_cog(hunt=make_hunt(channel_id=999))
    cog.bot = MagicMock()
    cog.bot.get_channel.return_value = MagicMock(send=AsyncMock())
    cog.get_channel_type = lambda ctx: "Group"
    cog.get_tag_group = lambda ctx: tag_group
    cog.service.delete_round = AsyncMock(return_value=[])
    ctx = make_ctx()
    cog.environment = {ctx.message.id: {"puzzle": None, "tag_group": tag_group}}

    order = []

    async def confirm(*_args, **_kwargs):
        order.append("confirm")
        return True

    cog._confirm_destructive = confirm

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb, \
         patch("bot.cogs.puzzles.puzzles_lock") as lock_mock:
        PuzzleDb.get_all_from_tag_group.return_value = [make_puzzle(id=1), make_puzzle(id=2)]
        lock_mock.side_effect = lambda ids: order.append("lock") or _null_cm()
        await Puzzles.delete_round.callback(cog, ctx)

    assert order == ["confirm", "lock"]


@pytest.mark.asyncio
async def test_bang_delete_hunt_confirms_before_locking():
    from bot.cogs.puzzles import Puzzles
    from tests.conftest import make_cog, make_ctx

    cog = make_cog(hunt=make_hunt(channel_id=999))
    cog.get_channel_type = lambda ctx: "Hunt"
    cog.service.delete_hunt = AsyncMock(return_value=[])
    ctx = make_ctx()
    ctx.channel = MagicMock(send=AsyncMock())
    cog.environment = {ctx.message.id: {"puzzle": None, "tag_group": None}}

    order = []

    async def confirm(*_args, **_kwargs):
        order.append("confirm")
        return True

    cog._confirm_destructive = confirm

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb, \
         patch("bot.cogs.puzzles.puzzles_lock") as lock_mock:
        PuzzleDb.get_all_from_hunt.return_value = [make_puzzle(id=3), make_puzzle(id=1)]
        lock_mock.side_effect = lambda ids: order.append("lock") or _null_cm()
        await Puzzles.delete_hunt.callback(cog, ctx)

    assert order == ["confirm", "lock"]


@pytest.mark.asyncio
async def test_bang_archive_hunt_confirms_before_locking():
    from bot.cogs.puzzles import Puzzles
    from bot.core import hunt_archive
    from tests.conftest import make_cog, make_ctx

    hunt = make_hunt(channel_id=999)
    cog = make_cog(hunt=hunt)
    cog.service.archive_hunt = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        channels_archived=4, final_name="H - me"))
    ctx = make_ctx()
    ctx.author.display_name = "me"

    order = []

    async def confirm(*_args, **_kwargs):
        order.append("confirm")
        return True

    cog._confirm_destructive = confirm

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb, \
         patch("bot.cogs.puzzles.puzzles_lock") as lock_mock:
        PuzzleDb.get_all_from_hunt.return_value = [MagicMock(id=3), MagicMock(id=1)]
        lock_mock.side_effect = lambda ids: order.append("lock") or _null_cm()
        await Puzzles.archive_hunt.callback(cog, ctx)

    assert order == ["confirm", "lock"]


@contextlib.asynccontextmanager
async def _null_cm():
    yield
