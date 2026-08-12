"""Request-shape tests for the minimeta contribution table's 'provided:' rows
(Plan 2, Task 7).

No live Google: patches the cog's own sheet_list/reconcile_table seams and
asserts the `desired_rows` passed to reconcile_table — this proves nothing
about what Google does with those rows. Mirrors the honesty header of
tests/test_contribution_table_requests.py and tests/test_feeder_matrix_requests.py.
"""
import asyncio

from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.core.sheet_tables import contribution_table_name
from bot.store.feeder_pool_data import FeederClaimData, FeederPoolItemData
from bot.store.puzzle_data import PuzzleData


def _cog():
    return GoogleSheets.__new__(GoogleSheets)


def _puzzle(id, name, **kw):
    p = PuzzleData()
    p.id = id
    p.name = name
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _run(minimeta, members, items, claims):
    """Run render_minimeta_contribution with the Table already present (so the
    lazy promote_tab_to_target path is never touched), capturing reconcile_table's
    desired_rows."""
    cog = _cog()
    page_id = minimeta.google_page_id
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [{
            "properties": {"sheetId": page_id},
            "tables": [{"name": contribution_table_name(page_id)}],
        }]}
    cog.sheet_list = _fake_sheet_list

    calls = []

    async def _fake_reconcile(**kwargs):
        calls.append(kwargs)
    cog.reconcile_table = _fake_reconcile

    asyncio.run(cog.render_minimeta_contribution(minimeta, members, items, claims))
    return calls


def _minimeta():
    return _puzzle(1, "Matching Meta", google_page_id=900)


def test_a_feeder_claimed_directly_to_the_minimeta_appears_as_provided():
    """Two items: one claimed to the minimeta itself (must appear), one claimed
    to a mini instead (must not) -- so a renderer that always/never emits a
    provided row cannot pass."""
    minimeta = _minimeta()
    items = [
        FeederPoolItemData(id=12, text="ALBATROSS"),
        FeederPoolItemData(id=13, text="MARZIPAN"),
    ]
    claims = [
        FeederClaimData(id=1, item_id=12, target_id=minimeta.id, confirmed=False),
        FeederClaimData(id=2, item_id=13, target_id=101, confirmed=False),
    ]
    calls = _run(minimeta, [], items, claims)

    assert len(calls) == 1
    rows = calls[0]["desired_rows"]
    assert ["provided:12", "—", "Provided", "ALBATROSS"] in rows
    assert not any(r[0] == "provided:13" for r in rows)


def test_a_provided_row_superseded_by_a_confirmed_claim_elsewhere_does_not_appear():
    """The minimeta claims BOTH items directly; item 13 is also confirmed to a
    mini (solving proved it belongs there), which supersedes the minimeta's own
    guess. Item 12 has no rival confirmation and must still appear -- so a
    renderer that drops every minimeta claim (not just the superseded one)
    cannot pass."""
    minimeta = _minimeta()
    items = [
        FeederPoolItemData(id=12, text="ALBATROSS"),
        FeederPoolItemData(id=13, text="MARZIPAN"),
    ]
    claims = [
        FeederClaimData(id=1, item_id=12, target_id=minimeta.id, confirmed=False),
        FeederClaimData(id=2, item_id=13, target_id=minimeta.id, confirmed=False),
        FeederClaimData(id=3, item_id=13, target_id=101, confirmed=True),
    ]
    calls = _run(minimeta, [], items, claims)

    rows = calls[0]["desired_rows"]
    assert ["provided:12", "—", "Provided", "ALBATROSS"] in rows
    assert not any(r[0] == "provided:13" for r in rows)


def test_an_empty_pool_never_renders_a_provided_row():
    minimeta = _minimeta()
    calls = _run(minimeta, [], [], [])

    rows = calls[0]["desired_rows"]
    assert not any(r[0].startswith("provided:") for r in rows)


def test_provided_rows_sit_alongside_feeder_rows_from_minis():
    """The mini-list rows (feeds:) and the provided rows (provided:) are two
    disjoint key namespaces on the SAME table -- both must be present together,
    not one crowding out the other."""
    minimeta = _minimeta()
    mini = _puzzle(101, "Mini A", parent_puzzle_id=1, solution="SEABIRDS")
    items = [FeederPoolItemData(id=12, text="ALBATROSS")]
    claims = [FeederClaimData(id=1, item_id=12, target_id=minimeta.id, confirmed=False)]
    calls = _run(minimeta, [minimeta, mini], items, claims)

    rows = calls[0]["desired_rows"]
    assert ["feeds:101", "Mini A", "Feeder", "SEABIRDS"] in rows
    assert ["provided:12", "—", "Provided", "ALBATROSS"] in rows
