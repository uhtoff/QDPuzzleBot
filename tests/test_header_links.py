"""Task 7: header up-links — each puzzle tab's row 3 carries a "Feeds:" line
linking to the metas it feeds.

Covers the service's target selection (feeds_targets), the header geometry
(B3:G3 progress, bold "Feeds:" at H3, links in I3:Q3), the links cell's rich-text
request shape, the existing-tab re-stamp batch, per-spreadsheet batching, and the
edge-change hooks that rewrite a header. No live Google: request bodies are
captured at the cog seams. Helpers are file-local.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.core.sheet_location import Link, SheetLocation
from bot.store.puzzle_data import PuzzleData
from tests.conftest import make_hunt, make_puzzle, make_round


def _url(sheet, page):
    return f"https://docs.google.com/spreadsheets/d/{sheet}/edit#gid={page}"


def _cog():
    return GoogleSheets.__new__(GoogleSheets)


def _wire(mpdb, mtdb, puzzles_by_id, groups_by_id, members_by_group=None):
    members_by_group = members_by_group or {}

    def p_get(**kw):
        if "id" in kw:
            return puzzles_by_id.get(kw["id"])
        return None

    def g_get(**kw):
        if "id" in kw:
            return groups_by_id.get(kw["id"])
        if "target_puzzle_id" in kw:
            return next((g for g in groups_by_id.values()
                         if g.target_puzzle_id == kw["target_puzzle_id"]), None)
        return None

    mpdb.get_by_attr.side_effect = p_get
    mpdb.get_all_from_tag_group.side_effect = lambda gid: list(members_by_group.get(gid, []))
    mpdb.get_children.side_effect = lambda pid: []
    mpdb.commit.side_effect = lambda p: None
    mtdb.get_by_attr.side_effect = g_get


# --- feeds_targets ----------------------------------------------------------


def test_feeds_targets_follows_feeds_only_sorted_by_name_skipping_self_and_untargeted():
    from bot.core.puzzle_service import feeds_targets
    beta = make_puzzle(id=21, name="Beta Meta", is_target=True)
    alpha = make_puzzle(id=22, name="Alpha Meta", is_target=True)
    informed = make_puzzle(id=23, name="Aardvark Capstone", is_target=True)
    pooled = make_puzzle(id=24, name="Aaa Pool", is_target=True)
    groups = {
        1: make_round(id=1, target_puzzle_id=21),   # feeds -> Beta (listed first)
        2: make_round(id=2, target_puzzle_id=23),   # informs -> excluded
        3: make_round(id=3, target_puzzle_id=24),   # none -> excluded
        4: make_round(id=4, target_puzzle_id=0),    # feeds, but no target
        5: make_round(id=5, target_puzzle_id=7),    # feeds itself -> excluded
        6: make_round(id=6, target_puzzle_id=22),   # feeds -> Alpha
        7: make_round(id=7, target_puzzle_id=21),   # feeds Beta again -> dedup
    }
    puz = make_puzzle(id=7, name="Feeder")
    for gid, edge in [(1, "feeds"), (2, "informs"), (3, "none"), (4, "feeds"),
                      (5, "feeds"), (6, "feeds"), (7, "feeds")]:
        puz.add_edge(gid, edge)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        _wire(mpdb, mtdb, {21: beta, 22: alpha, 23: informed, 24: pooled, 7: puz}, groups)
        result = feeds_targets(puz)
    assert [t.id for t in result] == [22, 21]


def test_feeds_targets_mini_links_to_its_parent():
    from bot.core.puzzle_service import feeds_targets
    parent = make_puzzle(id=40, name="Mini Meta")
    mini = make_puzzle(id=41, name="Mini 1", parent_puzzle_id=40)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        _wire(mpdb, mtdb, {40: parent, 41: mini}, {})
        result = feeds_targets(mini)
    assert [t.id for t in result] == [40]


# --- layout: fresh tab ------------------------------------------------------


def _merge_tuples(reqs):
    out = []
    for r in reqs:
        if "mergeCells" in r:
            g = r["mergeCells"]["range"]
            out.append((g["startRowIndex"], g["endRowIndex"],
                        g["startColumnIndex"], g["endColumnIndex"]))
    return out


def _value_at(reqs, row, col):
    for r in reqs:
        uc = r.get("updateCells")
        if not uc:
            continue
        g = uc["range"]
        if g["startRowIndex"] == row and g["startColumnIndex"] == col:
            values = uc["rows"][0]["values"]
            # update_cell sends one CellData as a dict; the links cell sends a list.
            cell = values[0] if isinstance(values, list) else values
            return cell.get("userEnteredValue")
    return None


def _bold_at(reqs, row, col):
    for r in reqs:
        rc = r.get("repeatCell")
        if not rc:
            continue
        g = rc["range"]
        if g.get("startRowIndex") == row and g.get("startColumnIndex") == col \
                and rc["fields"] == "userEnteredFormat.textFormat.bold":
            return True
    return False


@pytest.mark.parametrize("offset", [0, 1])
def test_fresh_header_splits_row3_into_progress_and_feeds(offset):
    cog = _cog()
    p = PuzzleData()
    p.id, p.name, p.url = 3, "Alpha", "http://x"
    reqs = cog._puzzle_tab_header_requests(p, 900, offset, is_target=bool(offset))
    merges = _merge_tuples(reqs)
    assert (2, 3, 1 + offset, 7 + offset) in merges      # B3:G3 progress
    assert (2, 3, 8 + offset, 17 + offset) in merges     # I3:Q3 feeds
    assert (2, 3, 1 + offset, 17 + offset) not in merges  # old B3:Q3 gone
    assert _value_at(reqs, 2, 7 + offset) == {"stringValue": "Feeds:"}
    assert _bold_at(reqs, 2, 7 + offset)


def test_generated_tab_carries_links_in_its_single_batch():
    cog = _cog()
    calls = []

    async def fake(body, archive=False):
        calls.append(body)
    cog.batch_update = fake
    cog.create_blank_tab = lambda spreadsheet_id, name, index=None: 901
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _uniq(n):
        return n
    cog.get_unique_tab_name = _uniq
    p = PuzzleData()
    p.id, p.name, p.url, p.is_target = 2, "Alpha", "http://x", False

    asyncio.run(cog.generate_puzzle_tab(
        p, is_target=False, header_links=[Link("Meta", _url("LIVE", 5))]))
    assert len(calls) == 1
    cell = _value_at(calls[0]["requests"], 2, 8)
    assert cell == {"stringValue": "Meta"}


# --- the links cell ---------------------------------------------------------


def _links_cell(req):
    uc = req["updateCells"]
    return uc["range"], uc["rows"][0]["values"][0], uc["fields"]


@pytest.mark.parametrize("offset,col", [(0, 8), (1, 9)])
def test_links_cell_links_each_name_and_not_the_separator(offset, col):
    cog = _cog()
    req = cog.header_links_request(
        900, offset, [Link("Alpha", "U1"), Link("Beta", "U2")])
    rng, cell, fields = _links_cell(req)
    assert (rng["sheetId"], rng["startRowIndex"], rng["startColumnIndex"]) == (900, 2, col)
    assert (rng["endRowIndex"], rng["endColumnIndex"]) == (3, col + 1)
    assert fields == "userEnteredValue,textFormatRuns"
    assert cell["userEnteredValue"] == {"stringValue": "Alpha, Beta"}
    assert cell["textFormatRuns"] == [
        {"startIndex": 0, "format": {"link": {"uri": "U1"}}},
        {"startIndex": 5, "format": {}},
        {"startIndex": 7, "format": {"link": {"uri": "U2"}}},
    ]


def test_links_cell_indexes_in_utf16_units():
    # Sheets counts textFormatRuns indices in UTF-16 code units: an astral
    # emoji is 2, so a code-point count would start Beta's link one too early.
    cog = _cog()
    req = cog.header_links_request(900, 0, [Link("🧩A", "U1"), Link("B", "U2")])
    _, cell, _ = _links_cell(req)
    assert [r["startIndex"] for r in cell["textFormatRuns"]] == [0, 3, 5]


def test_empty_links_clears_the_cell_with_the_same_mask():
    cog = _cog()
    req = cog.header_links_request(900, 0, [])
    rng, cell, fields = _links_cell(req)
    assert rng["startColumnIndex"] == 8
    assert fields == "userEnteredValue,textFormatRuns"
    assert "userEnteredValue" not in cell and "textFormatRuns" not in cell


# --- existing tab: re-stamp row 3 then write links --------------------------


@pytest.mark.parametrize("offset", [0, 1])
def test_existing_tab_batch_unmerges_then_merges_then_label_then_links(offset):
    cog = _cog()
    sent = []
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_solved_sheet_id = lambda: "SOLVED"

    async def fake(body, archive=False):
        sent.append((body, archive))
    cog.batch_update = fake

    asyncio.run(cog.write_header_links(
        [(SheetLocation("LIVE", 900), offset, [Link("Meta", "U1")])]))
    assert len(sent) == 1
    reqs = sent[0][0]["requests"]

    def idx(pred):
        return next(i for i, r in enumerate(reqs) if pred(r))

    unmerge = idx(lambda r: "unmergeCells" in r)
    g = reqs[unmerge]["unmergeCells"]["range"]
    assert (g["startRowIndex"], g["endRowIndex"], g["startColumnIndex"], g["endColumnIndex"]) \
        == (2, 3, 1 + offset, 17 + offset)
    progress = idx(lambda r: "mergeCells" in r
                   and r["mergeCells"]["range"]["startColumnIndex"] == 1 + offset)
    feeds = idx(lambda r: "mergeCells" in r
                and r["mergeCells"]["range"]["startColumnIndex"] == 8 + offset)
    assert _merge_tuples([reqs[progress], reqs[feeds]]) == [
        (2, 3, 1 + offset, 7 + offset), (2, 3, 8 + offset, 17 + offset)]
    label = idx(lambda r: "updateCells" in r
                and r["updateCells"]["range"]["startColumnIndex"] == 7 + offset)
    links = idx(lambda r: "updateCells" in r
                and r["updateCells"]["range"]["startColumnIndex"] == 8 + offset)
    assert unmerge < progress and unmerge < feeds
    assert max(progress, feeds) < label < links
    assert _bold_at(reqs, 2, 7 + offset)


def test_write_header_links_sends_one_batch_per_spreadsheet():
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_solved_sheet_id = lambda: "SOLVED"
    api = MagicMock()
    api.batchUpdate.side_effect = lambda spreadsheetId, body: ("REQ", spreadsheetId, body)
    with patch("bot.cogs.puzzles_gsheet.get_sheet", return_value=api), \
         patch("bot.cogs.puzzles_gsheet.execute_with_retry",
               new=AsyncMock(return_value={})):
        asyncio.run(cog.write_header_links([
            (SheetLocation("LIVE", 901), 0, [Link("M", "U")]),
            (SheetLocation("SOLVED", 902), 0, []),
            (SheetLocation("LIVE", 903), 1, [Link("N", "V")]),
        ]))
    calls = api.batchUpdate.call_args_list
    assert len(calls) == 2
    by_sheet = {c.kwargs["spreadsheetId"]: c.kwargs["body"] for c in calls}
    assert set(by_sheet) == {"LIVE", "SOLVED"}

    def pages(body):
        return {r[k]["range"]["sheetId"] for r in body["requests"] for k in r
                if isinstance(r[k], dict) and "range" in r[k]}
    assert pages(by_sheet["LIVE"]) == {901, 903}
    assert pages(by_sheet["SOLVED"]) == {902}


# --- hooks ------------------------------------------------------------------


def _service_with_cog():
    from bot.core.puzzle_service import PuzzleService
    gsheet = MagicMock()
    gsheet.write_header_links = AsyncMock()
    gsheet.update_puzzle = AsyncMock()
    # The real shift rule (target / minimeta own tab -> 1), not a stub, so a
    # write at the wrong offset shows up.
    gsheet._header_shift = GoogleSheets.__new__(GoogleSheets)._header_shift
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    svc = PuzzleService(bot)
    svc.refresh_target = AsyncMock()
    svc.refresh_targets = AsyncMock(return_value=[])
    return svc, gsheet


def _offsets(gsheet):
    """{page_id: offset} from every write_header_links call."""
    return {loc.page_id: offset
            for c in gsheet.write_header_links.await_args_list
            for loc, offset, _links in c.args[0]}


def _written(gsheet):
    """{page_id: [Link, ...]} from every write_header_links call."""
    out = {}
    for c in gsheet.write_header_links.await_args_list:
        for loc, _offset, links in c.args[0]:
            out[loc.page_id] = links
    return out


@pytest.mark.asyncio
async def test_untagging_a_feeder_rewrites_its_header_with_the_remaining_targets():
    hunt = make_hunt()
    meta_a = make_puzzle(id=21, name="Alpha Meta", is_target=True)
    meta_a.google_page_id = 501
    meta_b = make_puzzle(id=22, name="Beta Meta", is_target=True)
    meta_b.google_page_id = 502
    groups = {1: make_round(id=1, target_puzzle_id=21), 2: make_round(id=2, target_puzzle_id=22)}
    feeder = make_puzzle(id=7, name="Feeder")
    feeder.google_page_id = 700
    feeder.add_edge(1, "feeds")
    feeder.add_edge(2, "feeds")
    svc, gsheet = _service_with_cog()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        _wire(mpdb, mtdb, {21: meta_a, 22: meta_b, 7: feeder}, groups)
        mhdb.get_by_attr.return_value = hunt
        await svc.untag_from(feeder, groups[1])
    assert _written(gsheet) == {700: [Link("Beta Meta", _url("SHEET1", 502))]}


@pytest.mark.asyncio
async def test_renaming_a_target_rewrites_each_feeders_header_with_the_new_name():
    hunt = make_hunt()
    meta = make_puzzle(id=21, name="Old Name", is_target=True)
    meta.google_page_id = 501
    group = make_round(id=1, target_puzzle_id=21)
    meta.add_edge(1, "target")
    f1 = make_puzzle(id=7, name="F1")
    f1.google_page_id = 701
    f1.add_edge(1, "feeds")
    f2 = make_puzzle(id=8, name="F2")
    f2.google_page_id = 702
    f2.add_edge(1, "feeds")
    pooled = make_puzzle(id=9, name="Pooled")
    pooled.google_page_id = 703
    pooled.add_edge(1, "none")
    svc, gsheet = _service_with_cog()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        _wire(mpdb, mtdb, {21: meta, 7: f1, 8: f2, 9: pooled}, {1: group},
              members_by_group={1: [meta, f1, f2, pooled]})
        await svc.rename_puzzle(None, hunt, meta, "New Name")
    new = [Link("New Name", _url("SHEET1", 501))]
    assert _written(gsheet) == {701: new, 702: new}


@pytest.mark.asyncio
async def test_a_hook_failure_never_fails_the_users_action():
    hunt = make_hunt()
    meta_a = make_puzzle(id=21, name="Alpha Meta", is_target=True)
    meta_a.google_page_id = 501
    groups = {1: make_round(id=1, target_puzzle_id=21)}
    feeder = make_puzzle(id=7, name="Feeder")
    feeder.google_page_id = 700
    feeder.add_edge(1, "feeds")
    svc, gsheet = _service_with_cog()
    gsheet.write_header_links.side_effect = RuntimeError("sheets down")
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        _wire(mpdb, mtdb, {21: meta_a, 7: feeder}, groups)
        mhdb.get_by_attr.return_value = hunt
        await svc.untag_from(feeder, groups[1])   # must not raise
    gsheet.write_header_links.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_round_feeder_gets_its_links_at_tab_generation():
    """The round-create path writes the feeds edge after the tab exists, so the
    links are computed up front and ride in tab generation (no extra call)."""
    from bot.core.puzzle_service import PuzzleService
    from bot.store.hunt_data import HuntData
    hunt = HuntData()
    hunt.id, hunt.google_sheet_id, hunt.solved_sheet_id = 10, "S1", "A1"
    hunt.url, hunt.category_id, hunt.name = None, 1, "H"
    meta = make_puzzle(id=21, name="Round Meta", is_target=True)
    meta.google_page_id = 501
    round_data = make_round(id=5, category_id=999, target_puzzle_id=21)
    round_data.type = "Metapuzzle"
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    gsheet.write_header_links = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    svc = PuzzleService(bot)
    guild = MagicMock()
    guild.channels = []
    guild.categories = [MagicMock(id=999)]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=MagicMock(id=999, channels=[]))), \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="np"), True))):
        _wire(mpdb, mtdb, {21: meta}, {5: round_data})
        mpdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(guild, hunt, round_data, "Newpuz", is_meta=False,
                                notifier=notifier)
    kwargs = gsheet.create_puzzle_spreadsheet.await_args.kwargs
    assert kwargs["header_links"] == [Link("Round Meta", _url("S1", 501))]
    gsheet.write_header_links.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_mini_gets_a_link_to_its_parent_at_tab_generation():
    hunt = make_hunt()
    parent = make_puzzle(id=40, name="Mini Meta", puzzle_type="Minimeta")
    parent.google_page_id = 400
    svc, gsheet = _service_with_cog()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    svc.refresh_minimeta = AsyncMock()
    guild = MagicMock()
    channel = MagicMock()
    channel.create_thread = AsyncMock(return_value=MagicMock(id=333, name="m"))
    guild.get_channel.return_value = channel
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        _wire(mpdb, mtdb, {40: parent}, {})
        mpdb.get_children.side_effect = lambda pid: [MagicMock(), MagicMock()]
        await svc.create_mini(guild, hunt, parent, "Mini 1")
    kwargs = gsheet.create_puzzle_spreadsheet.await_args.kwargs
    assert kwargs["header_links"] == [Link("Mini Meta", _url("SHEET1", 400))]


@pytest.mark.asyncio
@pytest.mark.parametrize("mini_count,rewritten", [(1, True), (2, False)])
async def test_first_mini_rewrites_the_minimetas_feeds_line(mini_count, rewritten):
    """The first mini's render promotes the minimeta's tab, which clears its
    header; only then is the minimeta's own Feeds line written back."""
    hunt = make_hunt()
    meta = make_puzzle(id=21, name="Round Meta", is_target=True)
    meta.google_page_id = 501
    parent = make_puzzle(id=40, name="Mini Meta", puzzle_type="Minimeta")
    parent.google_page_id = 400
    parent.add_edge(1, "feeds")
    # The copy in hand was loaded before its first mini, so is_minimeta (a
    # load-time flag) is False; a fresh DB read hydrates it True.
    hydrated = make_puzzle(id=40, name="Mini Meta", puzzle_type="Minimeta")
    hydrated.google_page_id = 400
    hydrated.add_edge(1, "feeds")
    hydrated.is_minimeta = True
    svc, gsheet = _service_with_cog()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    svc.refresh_minimeta = AsyncMock()
    guild = MagicMock()
    channel = MagicMock()
    channel.create_thread = AsyncMock(return_value=MagicMock(id=333, name="m"))
    guild.get_channel.return_value = channel
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        _wire(mpdb, mtdb, {21: meta, 40: hydrated}, {1: make_round(id=1, target_puzzle_id=21)})
        mpdb.get_children.side_effect = lambda pid: [MagicMock()] * mini_count
        await svc.create_mini(guild, hunt, parent, "Mini 1")
    expected = {400: [Link("Round Meta", _url("SHEET1", 501))]} if rewritten else {}
    assert _written(gsheet) == expected
    if rewritten:
        # The render just promoted the tab to the shifted (offset 1) header.
        assert _offsets(gsheet) == {400: 1}


def _feeder_world():
    """Meta 21 targets group 1; F1 (701) feeds it, Pooled (703) is a 'none' member."""
    meta = make_puzzle(id=21, name="Round Meta", is_target=True)
    meta.google_page_id = 501
    meta.add_edge(1, "target")
    f1 = make_puzzle(id=7, name="F1")
    f1.google_page_id = 701
    f1.add_edge(1, "feeds")
    pooled = make_puzzle(id=9, name="Pooled")
    pooled.google_page_id = 703
    pooled.add_edge(1, "none")
    group = make_round(id=1, target_puzzle_id=21)
    group.type = "Metapuzzle"
    return meta, f1, pooled, group


@pytest.mark.asyncio
async def test_tagging_an_informs_edge_writes_no_header():
    hunt = make_hunt()
    meta, f1, _pooled, group = _feeder_world()
    svc, gsheet = _service_with_cog()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        g2 = make_round(id=2, target_puzzle_id=21)
        _wire(mpdb, mtdb, {21: meta, 7: f1}, {1: group, 2: g2})
        mhdb.get_by_attr.return_value = hunt
        await svc.tag_with_edge(f1, g2, "informs")
        assert _written(gsheet) == {}
        await svc.tag_with_edge(f1, g2, "feeds")   # same target again: dedup -> no change
        assert _written(gsheet) == {}


@pytest.mark.asyncio
async def test_bulk_re_edge_rewrites_every_members_header_from_fresh_edges():
    hunt = make_hunt()
    meta, f1, pooled, group = _feeder_world()
    fresh_f1 = make_puzzle(id=7, name="F1")
    fresh_f1.google_page_id = 701
    fresh_f1.add_edge(1, "informs")
    fresh_pooled = make_puzzle(id=9, name="Pooled")
    fresh_pooled.google_page_id = 703
    fresh_pooled.add_edge(1, "informs")
    svc, gsheet = _service_with_cog()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        # DB reads after the flip return the flipped rows, not the stale copies.
        _wire(mpdb, mtdb, {21: meta, 7: fresh_f1, 9: fresh_pooled}, {1: group},
              members_by_group={1: [meta, f1, pooled]})
        await svc.bulk_re_edge(group, "informs", hunt, refresh=False)
    assert _written(gsheet) == {701: [], 703: []}


@pytest.mark.asyncio
async def test_convert_to_meta_keeps_the_puzzles_own_feeds_line_through_promote():
    hunt = make_hunt()
    meta, f1, _pooled, group = _feeder_world()
    svc, gsheet = _service_with_cog()
    gsheet.promote_tab_to_group = AsyncMock()
    svc._update_forum_tags = AsyncMock()
    svc._refresh_channel_glyph = AsyncMock()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        _wire(mpdb, mtdb, {21: meta, 7: f1}, {1: group})
        mtdb.generate_uid.return_value = "G1"
        mtdb.commit.side_effect = lambda g: setattr(g, "id", g.id or 99)
        await svc.convert_to_meta(None, hunt, f1)
    gsheet.promote_tab_to_group.assert_awaited_once_with(
        f1, header_links=[Link("Round Meta", _url("SHEET1", 501))])


@pytest.mark.asyncio
async def test_convert_to_puzzle_clears_the_feeds_line_of_its_feeders():
    hunt = make_hunt()
    meta, f1, pooled, group = _feeder_world()
    puzzles = {21: meta, 7: f1, 9: pooled}
    groups = {1: group}
    svc, gsheet = _service_with_cog()
    svc._refresh_channel_glyph = AsyncMock()
    svc._refresh_host_target_glyphs = AsyncMock()
    svc._has_contribution_table = AsyncMock(return_value=False)
    svc.notifier = MagicMock()
    svc.notifier.refresh_info = AsyncMock()
    svc.notifier.board_changed = AsyncMock()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        _wire(mpdb, mtdb, puzzles, groups, members_by_group={1: [meta, f1, pooled]})
        mtdb.delete.side_effect = lambda gid: groups.pop(gid)
        await svc.convert_to_puzzle(None, hunt, meta)
    # Only the feeds member is rewritten, and its line is now empty.
    assert _written(gsheet) == {701: []}


@pytest.mark.asyncio
async def test_deleting_a_round_clears_the_feeds_line_of_kept_feeders():
    hunt = make_hunt(layout_mode="category")
    meta, f1, pooled, group = _feeder_world()
    puzzles = {21: meta, 7: f1, 9: pooled}
    groups = {1: group}
    svc, gsheet = _service_with_cog()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        _wire(mpdb, mtdb, puzzles, groups, members_by_group={1: [meta, f1, pooled]})
        mpdb.delete.side_effect = lambda pid: puzzles.pop(pid)
        mtdb.delete.side_effect = lambda gid: groups.pop(gid)
        await svc.delete_round(None, hunt, group, keep_puzzles=True)
    assert _written(gsheet) == {701: []}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["solve", "unsolve"])
async def test_a_minimetas_move_rewrites_its_minis_links_to_the_new_tab(action):
    """A minimeta is not a target, so solve/unsolve moves its tab to the other
    spreadsheet under a new page id; its minis' up-links must follow it."""
    hunt = make_hunt()
    minimeta = make_puzzle(id=40, name="Mini Meta", puzzle_type="Minimeta",
                           solved=(action == "unsolve"), solution="X")
    minimeta.google_page_id = 400
    minimeta.on_solved_sheet = action == "unsolve"
    mini = make_puzzle(id=41, name="Mini 1", parent_puzzle_id=40)
    mini.google_page_id = 410
    svc, gsheet = _service_with_cog()
    svc._update_forum_tags = AsyncMock()
    svc.notifier = MagicMock()
    svc.notifier.board_changed = AsyncMock()

    async def move(p):
        p.google_page_id, p.on_solved_sheet = 999, True

    async def restore(p, restore_index=None):
        p.google_page_id, p.on_solved_sheet = 888, False
    gsheet.move_puzzle_to_solved_sheet = AsyncMock(side_effect=move)
    gsheet.restore_puzzle_from_solved_sheet = AsyncMock(side_effect=restore)
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb:
        _wire(mpdb, mtdb, {40: minimeta, 41: mini}, {})
        mpdb.get_children.side_effect = lambda pid: [mini] if pid == 40 else []
        mpdb.get_all_from_hunt.return_value = [minimeta, mini]
        if action == "solve":
            await svc.solve(None, hunt, minimeta, "ANSWER")
            expected = _url("ARCHIVE1", 999)
        else:
            await svc.unsolve(None, hunt, minimeta)
            expected = _url("SHEET1", 888)
    assert _written(gsheet) == {410: [Link("Mini Meta", expected)]}


def _meta_feeding_metameta():
    """Meta 21 (own tab 600, a target -> header offset 1) and metameta 30 whose
    group 3 it can feed; also a second metameta 31 (group 4) it already feeds."""
    meta = make_puzzle(id=21, name="Round Meta", is_target=True)
    meta.google_page_id = 600
    mm = make_puzzle(id=30, name="Big Meta", is_target=True)
    mm.google_page_id = 300
    mm2 = make_puzzle(id=31, name="Other Meta", is_target=True)
    mm2.google_page_id = 310
    groups = {2: make_round(id=2, target_puzzle_id=21),   # the meta's own group
              3: make_round(id=3, target_puzzle_id=30), 4: make_round(id=4, target_puzzle_id=31)}
    meta.add_edge(2, "target")
    return meta, {21: meta, 30: mm, 31: mm2}, groups


@pytest.mark.asyncio
async def test_a_single_tag_writes_once_at_the_targets_shifted_offset():
    hunt = make_hunt()
    meta, puzzles, groups = _meta_feeding_metameta()
    svc, gsheet = _service_with_cog()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        _wire(mpdb, mtdb, puzzles, groups)
        mhdb.get_by_attr.return_value = hunt
        await svc.tag_with_edge(meta, groups[3], "feeds")
    gsheet.write_header_links.assert_awaited_once()
    assert _written(gsheet) == {600: [Link("Big Meta", _url("SHEET1", 300))]}
    assert _offsets(gsheet) == {600: 1}


@pytest.mark.asyncio
async def test_tag_edge_only_sweep_and_tag_write_once_between_them():
    hunt = make_hunt()
    meta, puzzles, groups = _meta_feeding_metameta()
    meta.add_edge(4, "feeds")   # swept off group 4, then tagged into group 3
    svc, gsheet = _service_with_cog()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        _wire(mpdb, mtdb, puzzles, groups)
        mhdb.get_by_attr.return_value = hunt
        await svc.tag_edge_only(meta, groups[3], "feeds")
    gsheet.write_header_links.assert_awaited_once()
    assert _written(gsheet) == {600: [Link("Big Meta", _url("SHEET1", 300))]}


@pytest.mark.asyncio
async def test_refresh_headers_false_skips_the_write():
    hunt = make_hunt()
    meta, puzzles, groups = _meta_feeding_metameta()
    meta.add_edge(4, "feeds")
    svc, gsheet = _service_with_cog()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mtdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        _wire(mpdb, mtdb, puzzles, groups)
        mhdb.get_by_attr.return_value = hunt
        await svc.tag_edge_only(meta, groups[3], "feeds", refresh_headers=False)
        await svc.tag_with_edge(meta, groups[4], "feeds", refresh_headers=False)
        await svc.untag_from(meta, groups[4], refresh_headers=False)
    gsheet.write_header_links.assert_not_awaited()


def test_a_nameless_link_is_skipped_not_raised():
    cog = _cog()
    req = cog.header_links_request(900, 0, [Link(None, "U0"), Link("Beta", "U2")])  # type: ignore[arg-type]
    _, cell, _ = _links_cell(req)
    assert cell["userEnteredValue"] == {"stringValue": "Beta"}
    assert cell["textFormatRuns"] == [{"startIndex": 0, "format": {"link": {"uri": "U2"}}}]


# --- one bad tab must not cancel the others on its spreadsheet ---------------


def _pages_in(body):
    return {r[k]["range"]["sheetId"] for r in body["requests"] for k in r
            if isinstance(r[k], dict) and "range" in r[k]}


def test_a_failed_batch_retries_each_tab_alone_so_one_bad_tab_spares_the_rest(caplog):
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_solved_sheet_id = lambda: "SOLVED"
    sent = []

    async def fake(body, archive=False):
        pages = _pages_in(body)
        sent.append((pages, archive))
        if 902 in pages:  # the combined batch and 902's own retry both fail
            raise RuntimeError("bad tab 902")
    cog.batch_update = fake

    with caplog.at_level("WARNING"):
        failed = asyncio.run(cog.write_header_links([
            (SheetLocation("LIVE", 901), 0, [Link("M", "U")]),
            (SheetLocation("LIVE", 902), 0, [Link("N", "V")]),
            (SheetLocation("LIVE", 903), 1, [Link("O", "W")]),
        ]))

    # One combined batch, then one retry per tab, each carrying only its tab.
    assert sent == [({901, 902, 903}, False), ({901}, False),
                    ({902}, False), ({903}, False)]
    assert failed == ["902"]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and "902" in errors[0].getMessage()


def test_a_failed_batch_on_one_spreadsheet_leaves_the_others_single_call():
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_solved_sheet_id = lambda: "SOLVED"
    sent = []

    async def fake(body, archive=False):
        sent.append((_pages_in(body), archive))
        if archive and len(_pages_in(body)) > 1:
            raise RuntimeError("solved batch fails")
    cog.batch_update = fake

    failed = asyncio.run(cog.write_header_links([
        (SheetLocation("LIVE", 901), 0, [Link("M", "U")]),
        (SheetLocation("SOLVED", 911), 0, [Link("N", "V")]),
        (SheetLocation("SOLVED", 912), 0, []),
    ]))
    assert sent == [({901}, False), ({911, 912}, True), ({911}, True), ({912}, True)]
    assert failed == []


@pytest.mark.parametrize("status", [429, 503])
def test_a_quota_or_outage_failure_marks_every_tab_failed_without_per_tab_writes(status):
    # batch_update has already retried a retryable error; fanning out one write
    # per tab would only add load to a saturated quota or a down service.
    from googleapiclient.errors import HttpError
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_solved_sheet_id = lambda: "SOLVED"
    sent = []

    async def fake(body, archive=False):
        sent.append(_pages_in(body))
        raise HttpError(MagicMock(status=status), b"quota")
    cog.batch_update = fake

    failed = asyncio.run(cog.write_header_links([
        (SheetLocation("LIVE", 901), 0, [Link("M", "U")]),
        (SheetLocation("LIVE", 902), 0, [Link("N", "V")]),
        (SheetLocation("LIVE", 903), 1, [Link("O", "W")]),
    ]))
    assert sent == [{901, 902, 903}]
    assert failed == ["901", "902", "903"]


def test_the_happy_path_returns_no_failures():
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_solved_sheet_id = lambda: "SOLVED"
    cog.batch_update = AsyncMock(return_value={})
    failed = asyncio.run(cog.write_header_links([
        (SheetLocation("LIVE", 901), 0, [Link("M", "U")]),
        (SheetLocation("LIVE", 903), 0, [Link("N", "V")]),
    ]))
    assert failed == []
    cog.batch_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_header_links_returns_the_names_of_puzzles_whose_line_failed():
    svc, gsheet = _service_with_cog()
    hunt = make_hunt()
    a = make_puzzle(id=1, name="Alpha")
    b = make_puzzle(id=2, name="Bravo")
    a.google_page_id, b.google_page_id = "901", "902"
    gsheet.write_header_links.return_value = ["902"]
    with patch("bot.core.puzzle_service.puzzle_location",
               side_effect=lambda h, p: SheetLocation("LIVE", p.google_page_id)), \
         patch.object(svc, "_header_links", return_value=[]):
        failed = await svc.refresh_header_links(hunt, [a, b])
    assert failed == ["Bravo"]
