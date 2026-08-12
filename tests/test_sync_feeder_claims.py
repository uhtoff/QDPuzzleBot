"""Service-level tests for sync_feeder_claims (Plan 2, Task 6).

Mirrors tests/test_feeder_matrix_service.py's mocking style: DAOs and the
GoogleSheets cog are mocked, so these assert the SERVICE's orchestration --
which target gets which claim change, routed through diff_ticks -- not what
Sheets does with the tab (see tests/test_feeder_matrix_requests.py for
read_feeder_matrix's own request-shape coverage).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.feeder_pool_data import FeederClaimData, FeederPoolItemData
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle


def _hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "SHEET1"
    h.solved_sheet_id = "ARCH1"
    return h


def _minimeta():
    p = make_puzzle(id=3, name="Matching Meta", puzzle_type="Minimeta")
    p.feeder_tab_page_id = 500
    return p


def _mini():
    return make_puzzle(id=10, name="Mini A", parent_puzzle_id=3)


# main is the minimeta (#3); Mini A is #10 -- ids are encoded in the label
# (target_column_label), never matched by bare name.
HEADER = ["key", "item", "Len", "note", "main #3", "Mini A #10"]


def _svc(header, grid, items, claims, minis):
    gsheet = MagicMock()
    gsheet.read_feeder_matrix = AsyncMock(return_value=(header, grid))
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    svc = PuzzleService(bot)
    svc.get_feeder_pool = MagicMock(return_value=(items, claims))
    svc.get_minis = MagicMock(return_value=minis)
    svc.claim_feeder = MagicMock()
    svc.unclaim_feeder = MagicMock()
    svc.refresh_feeder_matrix = AsyncMock()
    svc.refresh_minimeta = AsyncMock()
    return svc, gsheet


class TestSyncFeederClaims:
    @pytest.mark.asyncio
    async def test_a_tick_creates_a_claim(self):
        items = [FeederPoolItemData(id=12, text="ALBATROSS"),
                 FeederPoolItemData(id=13, text="MARZIPAN")]
        grid = [
            ["f:12", "ALBATROSS", "9", "", "", "TRUE"],
            ["f:13", "MARZIPAN", "8", "", "", ""],
        ]
        svc, _gsheet = _svc(HEADER, grid, items, [], [_mini()])

        await svc.sync_feeder_claims(_hunt(), _minimeta())

        svc.claim_feeder.assert_called_once_with(12, 10)

    @pytest.mark.asyncio
    async def test_an_untick_removes_an_unconfirmed_claim(self):
        items = [FeederPoolItemData(id=12, text="ALBATROSS"),
                 FeederPoolItemData(id=13, text="MARZIPAN")]
        claims = [FeederClaimData(id=1, item_id=12, target_id=10, confirmed=False)]
        grid = [
            ["f:12", "ALBATROSS", "9", "", "", ""],   # unticked -- was claimed
            ["f:13", "MARZIPAN", "8", "", "", ""],
        ]
        svc, _gsheet = _svc(HEADER, grid, items, claims, [_mini()])

        await svc.sync_feeder_claims(_hunt(), _minimeta())

        svc.unclaim_feeder.assert_called_once_with(12, 10)

    @pytest.mark.asyncio
    async def test_an_untick_never_removes_a_confirmed_claim(self):
        items = [FeederPoolItemData(id=12, text="ALBATROSS"),
                 FeederPoolItemData(id=13, text="MARZIPAN")]
        claims = [FeederClaimData(id=1, item_id=12, target_id=10, confirmed=True)]
        grid = [
            ["f:12", "ALBATROSS", "9", "", "", ""],   # unticked, but confirmed
            ["f:13", "MARZIPAN", "8", "", "", ""],
        ]
        svc, _gsheet = _svc(HEADER, grid, items, claims, [_mini()])

        await svc.sync_feeder_claims(_hunt(), _minimeta())

        svc.unclaim_feeder.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_tick_on_an_absent_feeder_is_ignored(self):
        """f:99 is not in the pool -- diff_ticks must ignore it, not claim a
        feeder that no longer (or never did) exist."""
        items = [FeederPoolItemData(id=12, text="ALBATROSS"),
                 FeederPoolItemData(id=13, text="MARZIPAN")]
        grid = [
            ["f:12", "ALBATROSS", "9", "", "", ""],
            ["f:13", "MARZIPAN", "8", "", "", ""],
            ["f:99", "GHOST", "5", "", "", "TRUE"],
        ]
        svc, _gsheet = _svc(HEADER, grid, items, [], [_mini()])

        await svc.sync_feeder_claims(_hunt(), _minimeta())

        svc.claim_feeder.assert_not_called()

    @pytest.mark.asyncio
    async def test_both_targets_are_synced_independently(self):
        """Two targets, two items, ticks arranged so a transposed column/row
        would fail: main claims item 13, Mini A claims item 12."""
        items = [FeederPoolItemData(id=12, text="ALBATROSS"),
                 FeederPoolItemData(id=13, text="MARZIPAN")]
        grid = [
            ["f:12", "ALBATROSS", "9", "", "", "TRUE"],
            ["f:13", "MARZIPAN", "8", "", "TRUE", ""],
        ]
        svc, _gsheet = _svc(HEADER, grid, items, [], [_mini()])

        await svc.sync_feeder_claims(_hunt(), _minimeta())

        svc.claim_feeder.assert_any_call(12, 10)
        svc.claim_feeder.assert_any_call(13, 3)
        assert svc.claim_feeder.call_count == 2

    @pytest.mark.asyncio
    async def test_adopts_a_note_typed_in_the_sheet(self):
        items = [FeederPoolItemData(id=12, text="ALBATROSS")]
        grid = [["f:12", "ALBATROSS", "9", "fits SEA", "", ""]]
        svc, _gsheet = _svc(HEADER, grid, items, [], [_mini()])

        with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
            await svc.sync_feeder_claims(_hunt(), _minimeta())
        pool_db.set_note.assert_called_once_with(12, "fits SEA")

    @pytest.mark.asyncio
    async def test_an_unchanged_note_is_not_rewritten(self):
        items = [FeederPoolItemData(id=12, text="ALBATROSS", note="fits SEA")]
        grid = [["f:12", "ALBATROSS", "9", "fits SEA", "", ""]]
        svc, _gsheet = _svc(HEADER, grid, items, [], [_mini()])

        with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
            await svc.sync_feeder_claims(_hunt(), _minimeta())
        pool_db.set_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_reports_added_dropped_contested_and_homeless(self):
        items = [FeederPoolItemData(id=12, text="ALBATROSS"),
                 FeederPoolItemData(id=13, text="MARZIPAN")]
        grid = [
            ["f:12", "ALBATROSS", "9", "", "TRUE", "TRUE"],   # both target -> contested
            ["f:13", "MARZIPAN", "8", "", "", ""],            # neither -> homeless
        ]
        post_claims = [
            FeederClaimData(id=1, item_id=12, target_id=3, confirmed=False),
            FeederClaimData(id=2, item_id=12, target_id=10, confirmed=False),
        ]
        svc, _gsheet = _svc(HEADER, grid, items, [], [_mini()])
        # First call (pre-sync) sees no claims; second call (post-sync, used for
        # the contested/homeless report) sees the outcome -- the DAO's own
        # correctness in reflecting an add/remove is covered elsewhere
        # (tests/test_feeder_pool_cascade.py), not re-proven here.
        svc.get_feeder_pool = MagicMock(side_effect=[(items, []), (items, post_claims)])

        report = await svc.sync_feeder_claims(_hunt(), _minimeta())

        assert sorted(report["added"]) == sorted([(12, 3), (12, 10)])
        assert report["dropped"] == []
        assert report["contested"] == [12]
        assert report["homeless"] == [13]

    @pytest.mark.asyncio
    async def test_re_renders_the_matrix_and_the_mini_list_sequentially(self):
        """Pins that BOTH renders happen -- the "provided:" rows on the
        minimeta's own table depend on claims too, so re-rendering only the matrix
        would leave them stale.

        It cannot pin the lock discipline the two share: both are AsyncMocks here,
        so no rollup_lock is ever taken and a nested implementation would pass this
        identically. The structural guards for that live in
        tests/test_feeder_matrix_service.py (both directions)."""
        svc, _gsheet = _svc(HEADER, [], [], [], [_mini()])

        await svc.sync_feeder_claims(_hunt(), _minimeta())

        svc.refresh_feeder_matrix.assert_awaited_once()
        svc.refresh_minimeta.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_without_a_matrix_tab(self):
        minimeta = _minimeta()
        minimeta.feeder_tab_page_id = None
        svc, gsheet = _svc(HEADER, [], [], [], [_mini()])

        report = await svc.sync_feeder_claims(_hunt(), minimeta)

        assert report is None
        gsheet.read_feeder_matrix.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_noop_without_sheets_cog(self):
        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        svc.get_feeder_pool = MagicMock(return_value=([], []))

        report = await svc.sync_feeder_claims(_hunt(), _minimeta())

        assert report is None

    @pytest.mark.asyncio
    async def test_noop_without_hunt_or_minimeta(self):
        svc, gsheet = _svc(HEADER, [], [], [], [_mini()])

        assert await svc.sync_feeder_claims(None, _minimeta()) is None
        assert await svc.sync_feeder_claims(_hunt(), None) is None
        gsheet.read_feeder_matrix.assert_not_awaited()


class TestUnreadableMatrix:
    """A read that yields no recognisable tick column is evidence the READ is
    unusable, not evidence that every box is unticked.

    read_feeder_matrix returns ([], []) when the tab has been deleted or the
    range comes back empty, and a solver who deletes the header row leaves every
    column unidentifiable. Either way parse_ticks yields no ticks for anyone, so
    an unguarded diff_ticks unclaims every unconfirmed claim held by the minimeta
    AND all of its minis in one command -- reported as an ordinary "Unclaimed: N".
    """

    @pytest.mark.asyncio
    async def test_an_empty_read_touches_no_claim_and_reports_an_error(self):
        items = [FeederPoolItemData(id=12, text="ALBATROSS"),
                 FeederPoolItemData(id=13, text="MARZIPAN")]
        claims = [FeederClaimData(id=1, item_id=12, target_id=10, confirmed=False),
                  FeederClaimData(id=2, item_id=13, target_id=3, confirmed=False)]
        svc, _gsheet = _svc([], [], items, claims, [_mini()])

        report = await svc.sync_feeder_claims(_hunt(), _minimeta())

        svc.unclaim_feeder.assert_not_called()
        svc.claim_feeder.assert_not_called()
        assert report["error"]
        assert "added" not in report and "dropped" not in report
        # Aborts before the re-renders too -- there is nothing to reflect.
        svc.refresh_feeder_matrix.assert_not_awaited()
        svc.refresh_minimeta.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_deleted_header_row_touches_no_claim_and_reports_an_error(self):
        # A solver deleted row 1, so what reads back as the header is a DATA row:
        # every label is unidentifiable and target_id_from_label returns None.
        header = ["f:12", "ALBATROSS", "9", "", "", "TRUE"]
        grid = [["f:13", "MARZIPAN", "8", "", "", ""]]
        items = [FeederPoolItemData(id=12, text="ALBATROSS"),
                 FeederPoolItemData(id=13, text="MARZIPAN")]
        claims = [FeederClaimData(id=1, item_id=12, target_id=10, confirmed=False),
                  FeederClaimData(id=2, item_id=13, target_id=3, confirmed=False)]
        svc, _gsheet = _svc(header, grid, items, claims, [_mini()])

        report = await svc.sync_feeder_claims(_hunt(), _minimeta())

        svc.unclaim_feeder.assert_not_called()
        svc.claim_feeder.assert_not_called()
        assert report["error"]

    @pytest.mark.asyncio
    async def test_a_header_of_only_foreign_columns_touches_no_claim(self):
        # Every id-bearing column belongs to a target that no longer exists, so
        # there is nothing of ours to read -- same rule.
        header = ["key", "item", "Len", "note", "Mini Z #77", "Mini Y #88"]
        items = [FeederPoolItemData(id=12, text="ALBATROSS")]
        claims = [FeederClaimData(id=1, item_id=12, target_id=10, confirmed=False)]
        svc, _gsheet = _svc(header, [], items, claims, [_mini()])

        report = await svc.sync_feeder_claims(_hunt(), _minimeta())

        svc.unclaim_feeder.assert_not_called()
        assert report["error"]
