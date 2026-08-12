"""Service-level feeder pool tests. DAOs mocked at the puzzle_service import site.

NOTE: build a real PuzzleService. `make_cog()` sets `cog.service = MagicMock()`, so
asserting against `make_cog().service` would pass no matter what the service did.
`PuzzleService(MagicMock())` is the house pattern — see tests/test_create_service.py.
"""
from unittest.mock import MagicMock, patch

from bot.core.puzzle_service import PuzzleService
from bot.store import FeederPoolItemData, FeederClaimData
from tests.conftest import make_puzzle


def _service():
    return PuzzleService(MagicMock())


def test_add_feeder_items_splits_a_paste_and_strips_blanks():
    svc = _service()
    minimeta = make_puzzle(id=100, name="Matching Meta")
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.add_many.return_value = []
        svc.add_feeder_items(minimeta, "ALBATROSS\n\nMARZIPAN\n  QUAY  \n")
    pool_db.add_many.assert_called_once_with(100, ["ALBATROSS", "MARZIPAN", "QUAY"])


def test_add_feeder_items_uppercases_nothing_and_keeps_order():
    """Given feeders are quoted verbatim from the puzzle — the bot must not
    normalise them, and paste order is the order the puzzle presented them."""
    svc = _service()
    minimeta = make_puzzle(id=100)
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.add_many.return_value = []
        svc.add_feeder_items(minimeta, "Seventh Heaven\nalbatross")
    pool_db.add_many.assert_called_once_with(100, ["Seventh Heaven", "albatross"])


def test_add_feeder_items_propagates_an_over_length_rejection():
    """add_feeder_items must not swallow the DAO's all-or-nothing ValueError --
    a caught-and-ignored exception here would silently drop the paste."""
    svc = _service()
    minimeta = make_puzzle(id=100)
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.add_many.side_effect = ValueError(
            "line too long (300 > 255 chars): 'XXX...'")
        try:
            svc.add_feeder_items(minimeta, "X" * 300)
            raise AssertionError("expected ValueError to propagate")
        except ValueError:
            pass


def test_claim_feeder_records_the_claim():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        svc.claim_feeder(item_id=12, target_id=101)
    claim_db.add.assert_called_once_with(12, 101)


def test_unclaim_feeder_removes_an_unconfirmed_claim():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        claim_db.get.return_value = FeederClaimData(
            id=1, item_id=12, target_id=101, confirmed=False)
        status = svc.unclaim_feeder(item_id=12, target_id=101)
    claim_db.remove.assert_called_once_with(12, 101)
    assert status == "removed"


def test_unclaim_feeder_refuses_a_confirmed_claim():
    """Confirmed claims are bot-owned -- solving proved them, and only unsolving
    the mini undoes that. A refusal must not touch the row, or !unsolve_mini has
    nothing left to demote."""
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        claim_db.get.return_value = FeederClaimData(
            id=1, item_id=12, target_id=101, confirmed=True)
        status = svc.unclaim_feeder(item_id=12, target_id=101)
    claim_db.remove.assert_not_called()
    assert status == "refused"


def test_unclaim_feeder_reports_no_claim_to_remove():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        claim_db.get.return_value = None
        status = svc.unclaim_feeder(item_id=12, target_id=101)
    claim_db.remove.assert_not_called()
    assert status == "absent"


def test_get_claim_delegates_to_the_dao():
    svc = _service()
    claim = FeederClaimData(id=1, item_id=12, target_id=101, confirmed=True)
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        claim_db.get.return_value = claim
        assert svc.get_claim(12, 101) is claim
    claim_db.get.assert_called_once_with(12, 101)


def test_get_feeder_pool_returns_items_and_claims_for_the_right_minimeta():
    """Two items and two minis, so a transposed id cannot pass."""
    svc = _service()
    minimeta = make_puzzle(id=100, name="Matching Meta")
    items = [
        FeederPoolItemData(id=12, minimeta_puzzle_id=100, text="ALBATROSS", position=0),
        FeederPoolItemData(id=13, minimeta_puzzle_id=100, text="MARZIPAN", position=1),
    ]
    claims = [
        FeederClaimData(id=1, item_id=12, target_id=101, confirmed=True),
        FeederClaimData(id=2, item_id=13, target_id=102, confirmed=False),
    ]
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db, \
         patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        pool_db.get_for_minimeta.return_value = items
        claim_db.get_for_minimeta.return_value = claims
        got_items, got_claims = svc.get_feeder_pool(minimeta)

    assert [i.text for i in got_items] == ["ALBATROSS", "MARZIPAN"]
    assert got_claims == claims
    pool_db.get_for_minimeta.assert_called_once_with(100)
    claim_db.get_for_minimeta.assert_called_once_with(100)


def test_edit_feeder_item_sets_the_text():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        svc.edit_feeder_item(12, "ALBATROSS")
    pool_db.set_text.assert_called_once_with(12, "ALBATROSS")


def test_remove_feeder_item_deletes_it():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        svc.remove_feeder_item(12)
    pool_db.delete.assert_called_once_with(12)


def test_get_feeder_item_refuses_an_item_from_another_pool():
    """Two minimetas — an unscoped lookup would return the foreign item and let a
    command in the wrong channel edit or delete it."""
    svc = _service()
    mine = make_puzzle(id=100, name="Mine")
    foreign_item = FeederPoolItemData(
        id=12, minimeta_puzzle_id=999, text="ALBATROSS", position=0)
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.get_by_id.return_value = foreign_item
        assert svc.get_feeder_item(mine, 12) is None


def test_get_feeder_item_returns_an_item_from_this_pool():
    svc = _service()
    mine = make_puzzle(id=100, name="Mine")
    item = FeederPoolItemData(
        id=12, minimeta_puzzle_id=100, text="ALBATROSS", position=0)
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.get_by_id.return_value = item
        assert svc.get_feeder_item(mine, 12) is item


def test_get_feeder_item_handles_a_missing_id():
    svc = _service()
    mine = make_puzzle(id=100, name="Mine")
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.get_by_id.return_value = None
        assert svc.get_feeder_item(mine, 12) is None
