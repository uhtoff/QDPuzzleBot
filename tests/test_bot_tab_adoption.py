"""A bot-owned tab is created, or adopted — but never stolen from a puzzle (M14, D5).

`refresh_evidence` had no fallback at all: when a tab named "Evidence" already
existed, `create_blank_tab` 400s on the duplicate name and the refresh wedged
permanently. `refresh_unassigned` had the opposite bug — a fallback that adopted
ANY tab carrying the name, including a puzzle's.

Copying the sibling's fallback into `refresh_evidence` (what the review's stated
fix said to do) would have been worse than the wedge. The two ensure_* helpers
are not symmetric: `ensure_evidence_table` opens with a full-tab
`_values_clear` (`puzzles_gsheet.py:1029`) as a one-time migration off the old
flat render, and it runs exactly when `evidence_table_id is None` — the adoption
path. A blind adopt would therefore have wiped a solver's entire tab and written
the bot header over it.

So both sites route through `_ensure_bot_tab`, which adopts only a tab that no
puzzle and no additional sheet claims. Anything else gets a uniquified name.
The load-bearing assertions here are the ones pinning which page id reaches
`ensure_*_table` — that is the id whose contents get cleared.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData

_EXISTING_PAGE = 555        # the tab already carrying the reserved name
_FRESH_PAGE = 556           # what create_blank_tab returns for a uniquified name


def _service():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def _hunt(hunt_id):
    """Own hunt id per test — _ROLLUP_LOCKS is a module-global that outlives the
    event loop, so a key contended across two tests raises spuriously."""
    h = HuntData()
    h.id = hunt_id
    h.name = "H"
    h.google_sheet_id = "SHEET_ID"
    # All None so both refreshes take their lazy tab-creation path.
    h.unassigned_sheet_page_id = None
    h.unassigned_table_id = None
    h.evidence_sheet_page_id = None
    h.evidence_table_id = None
    return h


def _gsheet(reserved_name, existing_page=_EXISTING_PAGE):
    """A cog whose create_blank_tab rejects the reserved name the way Sheets does."""
    gsheet = MagicMock()

    async def create(spreadsheet_id, name, index=None):
        if name == reserved_name:
            raise RuntimeError('addSheet: A sheet with the name "%s" already exists' % name)
        return _FRESH_PAGE

    gsheet.create_blank_tab = AsyncMock(side_effect=create)
    gsheet.get_page_id_by_name = AsyncMock(return_value=existing_page)
    gsheet.get_unique_tab_name = AsyncMock(return_value=f"{reserved_name} (2)")
    gsheet.ensure_evidence_table = AsyncMock(return_value=77)
    gsheet.ensure_unassigned_table = AsyncMock(return_value=78)
    gsheet.reconcile_table = AsyncMock()
    return gsheet


def _owner(page_id):
    """A puzzle/sheet record claiming `page_id` — google_page_id is a str column."""
    owner = MagicMock()
    owner.google_page_id = str(page_id)
    return owner


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_adopts_a_tab_no_puzzle_claims():
    """A leftover from an earlier run is ours: adopt it rather than wedging."""
    svc, hunt = _service(), _hunt(710)
    gsheet = _gsheet("Evidence")

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.SheetsJsonDb") as sdb, \
         patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        pdb.get_by_attr.return_value = None
        sdb.get_by_page_id.return_value = None
        edb.get_all.return_value = []
        await svc.refresh_evidence(hunt)

    assert hunt.evidence_sheet_page_id == _EXISTING_PAGE
    gsheet.get_unique_tab_name.assert_not_awaited()
    gsheet.ensure_evidence_table.assert_awaited_once_with(_EXISTING_PAGE)


@pytest.mark.asyncio
async def test_evidence_never_clears_a_tab_a_puzzle_owns():
    """The destructive case. A puzzle called "Evidence" holds the name, so the
    refresh must build its own tab — and `ensure_evidence_table`, which opens
    with a full-tab clear, must be pointed at the NEW page id, never the
    puzzle's. Two distinct ids so adopting and creating disagree.
    """
    svc, hunt = _service(), _hunt(711)
    gsheet = _gsheet("Evidence")

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.SheetsJsonDb") as sdb, \
         patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        pdb.get_by_attr.return_value = _owner(_EXISTING_PAGE)
        sdb.get_by_page_id.return_value = None
        edb.get_all.return_value = []
        await svc.refresh_evidence(hunt)

    assert hunt.evidence_sheet_page_id == _FRESH_PAGE
    gsheet.get_unique_tab_name.assert_awaited_once()
    gsheet.create_blank_tab.assert_awaited_with("SHEET_ID", "Evidence (2)")
    # The clear targets our new tab, not the puzzle's.
    gsheet.ensure_evidence_table.assert_awaited_once_with(_FRESH_PAGE)
    assert gsheet.ensure_evidence_table.await_args.args[0] != _EXISTING_PAGE


@pytest.mark.asyncio
async def test_evidence_never_adopts_an_additional_sheets_tab():
    """A puzzle's extra working sheet is just as destroyable as its main tab."""
    svc, hunt = _service(), _hunt(712)
    gsheet = _gsheet("Evidence")

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.SheetsJsonDb") as sdb, \
         patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        pdb.get_by_attr.return_value = None
        sdb.get_by_page_id.return_value = _owner(_EXISTING_PAGE)
        edb.get_all.return_value = []
        await svc.refresh_evidence(hunt)

    assert hunt.evidence_sheet_page_id == _FRESH_PAGE
    gsheet.ensure_evidence_table.assert_awaited_once_with(_FRESH_PAGE)


# --------------------------------------------------------------------------
# Unassigned Feeders — the pre-existing blind adopt D5 calls out
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unassigned_adopts_a_tab_no_puzzle_claims():
    svc, hunt = _service(), _hunt(713)
    gsheet = _gsheet("Unassigned Feeders")

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.SheetsJsonDb") as sdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        pdb.get_by_attr.return_value = None
        pdb.get_unassigned.return_value = []
        sdb.get_by_page_id.return_value = None
        await svc.refresh_unassigned(hunt)

    assert hunt.unassigned_sheet_page_id == _EXISTING_PAGE
    gsheet.get_unique_tab_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_unassigned_never_writes_into_a_puzzles_tab():
    """The regression D5 names: the old fallback adopted by name alone, so the
    bot header and rollup rows landed in a live solving grid."""
    svc, hunt = _service(), _hunt(714)
    gsheet = _gsheet("Unassigned Feeders")

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.SheetsJsonDb") as sdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        pdb.get_by_attr.return_value = _owner(_EXISTING_PAGE)
        pdb.get_unassigned.return_value = []
        sdb.get_by_page_id.return_value = None
        await svc.refresh_unassigned(hunt)

    assert hunt.unassigned_sheet_page_id == _FRESH_PAGE
    gsheet.create_blank_tab.assert_awaited_with("SHEET_ID", "Unassigned Feeders (2)")
    gsheet.ensure_unassigned_table.assert_awaited_once_with(_FRESH_PAGE, False)
    # Nothing was reconciled into the puzzle's tab.
    assert gsheet.reconcile_table.await_args.kwargs["page_id"] == _FRESH_PAGE


# --------------------------------------------------------------------------
# Failures that are not "name taken"
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_create_failure_with_no_matching_tab_is_not_swallowed():
    """If no tab carries the name, the create failed for some other reason —
    a quota trip, a revoked token. Surfacing it beats inventing a page id."""
    svc, hunt = _service(), _hunt(715)
    gsheet = _gsheet("Evidence")
    gsheet.get_page_id_by_name = AsyncMock(return_value=None)

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.SheetsJsonDb"), \
         patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        edb.get_all.return_value = []
        with pytest.raises(RuntimeError):
            await svc.refresh_evidence(hunt)

    assert hunt.evidence_sheet_page_id is None
