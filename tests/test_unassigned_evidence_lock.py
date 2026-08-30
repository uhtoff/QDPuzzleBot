"""The Unassigned Feeders and Evidence refreshes must serialise per tab (M22).

Both render through gsheet_cog.reconcile_table, which is read-then-write across
three awaits (read the key column, compute next_row = max(existing)+1, then
batch_update). Two interleaved refreshes of the same tab therefore compute the
same next_row and the second append overwrites the first. Roughly 21 fire-and-
forget create_task sites drive refresh_unassigned and 6 drive refresh_evidence,
so the interleaving is routine rather than theoretical.

The fix is rollup_lock keyed per hunt PER TAB — ("unassigned", hunt.id) and
("evidence", hunt.id) — so same-tab refreshes queue but the two different tabs
still run in parallel (they never touch each other's rows). The last test here is
what pins the per-tab key: a single per-hunt key would pass the first two tests
and fail that one.
"""
import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData

_UNASSIGNED_PAGE = 900
_EVIDENCE_PAGE = 901


def _lock_service():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def _lock_hunt(hunt_id):
    """Each test gets its OWN hunt id on purpose. _ROLLUP_LOCKS is a module-global
    defaultdict that outlives the per-test event loop, so a lock CONTENDED in one
    test and contended again in the next raises "bound to a different event loop"
    — a spurious red that masks what the test is actually asserting.
    """
    h = HuntData()
    h.id = hunt_id
    h.name = "H"
    h.google_sheet_id = "SHEET_ID"
    # Pre-set so neither refresh takes its lazy tab/table creation path.
    h.unassigned_sheet_page_id = _UNASSIGNED_PAGE
    h.unassigned_table_id = 11
    h.evidence_sheet_page_id = _EVIDENCE_PAGE
    h.evidence_table_id = 12
    return h


def _tracking_gsheet(order, holds):
    """A gsheet cog whose reconcile_table logs "<tag>-start"/"<tag>-end" around a
    sleep, so two concurrent refreshes visibly interleave unless serialised.

    Tags are "U<n>"/"E<n>" by tab and arrival order; `holds` is consumed in call
    order, so the first arrival can be made the slow one.
    """
    names = {_UNASSIGNED_PAGE: "U", _EVIDENCE_PAGE: "E"}
    counts = defaultdict(int)
    holds_iter = iter(holds)
    gsheet = MagicMock()

    async def reconcile(*, page_id, **_kwargs):
        counts[page_id] += 1
        tag = f"{names[page_id]}{counts[page_id]}"
        order.append(f"{tag}-start")
        await asyncio.sleep(next(holds_iter))
        order.append(f"{tag}-end")

    gsheet.reconcile_table = AsyncMock(side_effect=reconcile)
    return gsheet


def _no_interleave(order):
    """True when each run's start is immediately followed by its own end."""
    assert len(order) == 4, order
    return (order[0].split("-")[0] == order[1].split("-")[0]
            and order[2].split("-")[0] == order[3].split("-")[0]
            and order[0].split("-")[0] != order[2].split("-")[0])


@pytest.mark.asyncio
async def test_concurrent_unassigned_refreshes_do_not_interleave():
    svc, hunt = _lock_service(), _lock_hunt(701)
    order = []
    gsheet = _tracking_gsheet(order, [0.02, 0.0])

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        pdb.get_unassigned.return_value = []
        await asyncio.gather(svc.refresh_unassigned(hunt), svc.refresh_unassigned(hunt))

    assert _no_interleave(order), order


@pytest.mark.asyncio
async def test_concurrent_evidence_refreshes_do_not_interleave():
    svc, hunt = _lock_service(), _lock_hunt(702)
    order = []
    gsheet = _tracking_gsheet(order, [0.02, 0.0])

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        edb.get_all.return_value = []
        await asyncio.gather(svc.refresh_evidence(hunt), svc.refresh_evidence(hunt))

    assert _no_interleave(order), order


@pytest.mark.asyncio
async def test_unassigned_and_evidence_do_not_block_each_other():
    """Pins the PER-TAB key: they are different tabs and cannot race, so a slow
    unassigned refresh must not hold up an evidence refresh of the same hunt.
    A single ("rollup", hunt.id) key would serialise them and fail here.
    """
    svc, hunt = _lock_service(), _lock_hunt(703)
    order = []
    gsheet = _tracking_gsheet(order, [0.02, 0.0])

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        pdb.get_unassigned.return_value = []
        edb.get_all.return_value = []
        await asyncio.gather(svc.refresh_unassigned(hunt), svc.refresh_evidence(hunt))

    # Evidence starts AND finishes inside the unassigned run's critical section.
    assert order == ["U1-start", "E1-start", "E1-end", "U1-end"], order


def test_unassigned_and_evidence_keys_are_distinct_locks():
    """The two tuple keys must land on different locks in the shared registry."""
    from bot.core.locks import _ROLLUP_LOCKS

    assert _ROLLUP_LOCKS[("unassigned", 704)] is not _ROLLUP_LOCKS[("evidence", 704)]
    # ...and neither collides with a plain group-id key of the same hunt id.
    assert _ROLLUP_LOCKS[("unassigned", 704)] is not _ROLLUP_LOCKS[704]
