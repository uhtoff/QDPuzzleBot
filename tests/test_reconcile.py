"""Unit tests for the !reconcile feature. Stubs the Discord + Sheets seams in the
ctx-free PuzzleService, in the style of the sibling gsheet/create suites."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import bot.core.puzzle_service as ps
from bot.core.puzzle_service import PuzzleService
from bot.core.reconcile import ReconcileReport
from bot.cogs.puzzles import Puzzles
from bot.core.sheet_location import spreadsheet_for
from bot.store.puzzle_data import PuzzleData
from tests.conftest import make_hunt


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_unassigned_refresh():
    """reconcile apply also rebuilds the hunt-wide Unassigned Feeders tab. Most
    tests here exercise other steps against a MagicMock sheets cog, where the
    real rebuild would fail or create a tab; stub it file-wide. The tests that
    cover the rebuild patch the instance on top of this."""
    with patch.object(PuzzleService, "refresh_unassigned", new=AsyncMock()):
        yield


def _service(notifier=None):
    bot = MagicMock()
    bot.get_cog.return_value = None  # no GoogleSheets cog in channel tests
    return PuzzleService(bot, notifier=notifier or AsyncMock())


def _hunt(**kw):
    kw.setdefault("id", 1)
    return make_hunt(google_sheet_id="SS", solved_sheet_id="ARC", **kw)


def _puzzle(**kw):
    p = PuzzleData(name=kw.pop("name", "Tide"), hunt_id=1)
    p.tags = kw.pop("tags", [10])
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _guild_with(channels=(), categories=()):
    g = MagicMock()
    g.channels = list(channels)
    g.categories = list(categories)
    g.get_channel.side_effect = lambda cid: next(
        (c for c in g.channels if getattr(c, "id", None) == cid), None)
    return g


def _category(cid=500):
    return SimpleNamespace(id=cid, channels=[])


def _service_with_gsheet(gcog):
    bot = MagicMock()
    bot.get_cog.return_value = gcog
    return PuzzleService(bot, notifier=AsyncMock())


def _gcog(page_name=None):
    g = MagicMock()
    g.set_spreadsheet_id = MagicMock()
    g.set_solved_sheet_id = MagicMock()
    g.get_spreadsheet_id.return_value = "SS"
    g.get_solved_sheet_id.return_value = "ARC"
    # The real location rule over the stubbed hunt ids (a MagicMock's own
    # spreadsheet_of would answer a Mock, which names no spreadsheet).
    g.spreadsheet_of = lambda obj: spreadsheet_for(
        obj, g.get_spreadsheet_id(), g.get_solved_sheet_id())
    g.INITIAL_OFFSET = 999
    g.get_page_name_by_id = AsyncMock(return_value=page_name)
    g.add_new_puzzle_sheet = AsyncMock()
    g.update_puzzle_info = AsyncMock()
    g.compute_insert_index = AsyncMock(return_value=999)
    return g


# --------------------------------------------------------------------------
# Task 1: Report model
# --------------------------------------------------------------------------

def test_report_records_and_counts_actions():
    r = ReconcileReport()
    r.add("channel_create", "Tide", "no channel found")
    r.add("tab_create", "Tide", "tab missing")
    r.add("channel_create", "Time", "no channel found")
    assert len(r.actions) == 3
    counts = r.counts()
    assert counts["channel_create"] == 2
    assert counts["tab_create"] == 1


def test_report_orphans_and_failures_are_separate():
    r = ReconcileReport()
    r.add_orphan_channel(123, "stray-chan")
    r.add_orphan_tab(456, "Stray Tab")
    r.add_failure("Tide", "boom")
    assert r.orphan_channels == [(123, "stray-chan")]
    assert r.orphan_tabs == [(456, "Stray Tab")]
    assert r.failures == [("Tide", "boom")]
    assert r.has_orphans() is True


def test_report_empty_has_no_orphans():
    r = ReconcileReport()
    assert r.has_orphans() is False
    assert r.counts() == {}


# --------------------------------------------------------------------------
# Task 2: Channel reconciliation
# --------------------------------------------------------------------------

def test_channel_ok_when_id_resolves_and_in_right_category(monkeypatch):
    cat = _category(500)
    chan = SimpleNamespace(id=999, name="tide", category=cat, mention="<#999>")
    cat.channels = [chan]
    guild = _guild_with(channels=[chan], categories=[cat])
    svc = _service()
    p = _puzzle(channel_id=999, channel_name="tide", tags=[10])
    with patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)):
        report = asyncio.run(svc._reconcile_channel(guild, _hunt(), p, None, apply=True))
    assert report == []  # no actions: already correct


def test_a_channel_home_group_resolves_to_the_channels_parent_category():
    """M3. For an in-place-converted / channel-mode group, category_id names a
    CHANNEL, not a category. PuzzleService._home_category_for_puzzle looked only at
    guild.categories and returned None, so _reconcile_channel then recreated the
    puzzle's channel with category=None -- at guild ROOT, outside the hunt's
    role-gating overwrites, i.e. visible to @everyone on a gated hunt.

    category_layout._home_category has had the channel-parent fallback all along
    and the service already calls it in three other places.
    """
    cat = _category(500)
    home_channel = SimpleNamespace(id=700, name="home", category=cat)
    # category_id names the CHANNEL: no category in guild.categories matches it.
    guild = _guild_with(channels=[home_channel], categories=[cat])
    svc = _service()
    p = _puzzle(channel_id=None, channel_name="tide", tags=[10])

    with patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=700)):
        category, home_group = svc._home_category_for_puzzle(guild, p)

    assert category is cat, "a channel-home group must resolve to its parent category"
    assert home_group is not None


def test_a_category_home_group_still_resolves_directly():
    """The keeper. Two shapes, so "always use the fallback" and "use it only when
    the direct lookup misses" disagree."""
    cat = _category(500)
    guild = _guild_with(channels=[], categories=[cat])
    svc = _service()
    p = _puzzle(channel_id=None, channel_name="tide", tags=[10])

    with patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)):
        category, _ = svc._home_category_for_puzzle(guild, p)

    assert category is cat


def test_a_replacement_channel_for_a_channel_home_group_is_not_created_at_root():
    """The consequence the finding is actually about."""
    cat = _category(500)
    home_channel = SimpleNamespace(id=700, name="home", category=cat)
    guild = _guild_with(channels=[home_channel], categories=[cat])
    svc = _service(notifier=AsyncMock())
    p = _puzzle(channel_id=None, channel_name="tide", tags=[10])
    created = SimpleNamespace(id=777, name="tide", mention="<#777>", category=cat)
    goc = AsyncMock(return_value=(created, True))

    with patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=700)), \
         patch.object(ps, "get_or_create_channel", new=goc), \
         patch.object(ps, "solve_divider_position", return_value=False), \
         patch.object(ps.PuzzleJsonDb, "commit"):
        asyncio.run(svc._reconcile_channel(guild, _hunt(), p, None, apply=True))

    assert goc.await_args.kwargs.get("category") is cat, goc.await_args


def test_channel_missing_and_no_existing_creates_and_welcomes():
    guild = _guild_with(channels=[], categories=[_category(500)])
    notifier = AsyncMock()
    svc = _service(notifier=notifier)
    p = _puzzle(channel_id=None, channel_name="tide", tags=[10])
    created_chan = SimpleNamespace(id=777, name="tide", mention="<#777>", category=None)
    with patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)), \
         patch.object(ps, "get_or_create_channel",
                      new=AsyncMock(return_value=(created_chan, True))), \
         patch.object(ps, "solve_divider_position", return_value=False), \
         patch.object(ps.PuzzleJsonDb, "commit") as commit:
        actions = asyncio.run(svc._reconcile_channel(guild, _hunt(), p, None, apply=True))
    assert any(a.kind == "channel_create" for a in actions)
    assert p.channel_id == 777
    commit.assert_called()
    notifier.welcome.assert_awaited()  # welcome only on genuine creation


def test_channel_skipped_for_mini_puzzle():
    # A mini (parent_puzzle_id set) lives as a thread under its minimeta; reconcile
    # must not promote it to a standalone text channel even when none is found.
    guild = _guild_with(channels=[], categories=[_category(500)])
    svc = _service(notifier=AsyncMock())
    p = _puzzle(channel_id=None, channel_name="mini", tags=[], parent_puzzle_id=42)
    with patch.object(ps, "get_or_create_channel",
                      new=AsyncMock()) as create:
        actions = asyncio.run(svc._reconcile_channel(guild, _hunt(), p, None, apply=True))
    assert actions == []
    create.assert_not_awaited()
    assert p.channel_id is None  # untouched


def test_channel_relink_when_named_channel_exists_no_welcome():
    cat = _category(500)
    existing = SimpleNamespace(id=888, name="tide", mention="<#888>", category=cat)
    cat.channels = [existing]
    guild = _guild_with(channels=[existing], categories=[cat])
    notifier = AsyncMock()
    svc = _service(notifier=notifier)
    p = _puzzle(channel_id=123456, channel_name="tide", tags=[10])  # stale id
    with patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)), \
         patch.object(ps.PuzzleJsonDb, "commit") as commit:
        actions = asyncio.run(svc._reconcile_channel(
            guild, _hunt(), p, None, apply=True, hunt_category_ids={500}))
    assert any(a.kind == "channel_relink" for a in actions)
    assert p.channel_id == 888
    commit.assert_called()
    notifier.welcome.assert_not_awaited()  # existing channel kept — no repost


def test_channel_relink_scoped_to_hunt_categories():
    """A same-named channel that belongs to a DIFFERENT hunt's category must not
    be relinked/hijacked — only categories in hunt_category_ids are searched."""
    other_cat = _category(999)
    foreign = SimpleNamespace(id=888, name="tide", mention="<#888>", category=other_cat)
    other_cat.channels = [foreign]
    guild = _guild_with(channels=[foreign], categories=[_category(500), other_cat])
    svc = _service(notifier=AsyncMock())
    p = _puzzle(channel_id=None, channel_name="tide", tags=[10])
    with patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)), \
         patch.object(ps, "get_or_create_channel",
                      new=AsyncMock(return_value=(SimpleNamespace(id=777, name="tide", mention="<#777>"), True))), \
         patch.object(ps.PuzzleJsonDb, "commit"):
        actions = asyncio.run(svc._reconcile_channel(
            guild, _hunt(), p, None, apply=True, hunt_category_ids={500}))
    assert any(a.kind == "channel_create" for a in actions)
    assert not any(a.kind == "channel_relink" for a in actions)
    assert p.channel_id == 777


def test_channel_report_only_does_not_mutate():
    guild = _guild_with(channels=[], categories=[_category(500)])
    svc = _service()
    p = _puzzle(channel_id=None, channel_name="tide", tags=[10])
    with patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)), \
         patch.object(ps, "get_or_create_channel", new=AsyncMock()) as gocc, \
         patch.object(ps.PuzzleJsonDb, "commit") as commit:
        actions = asyncio.run(svc._reconcile_channel(guild, _hunt(), p, None, apply=False))
    assert any(a.kind == "channel_create" for a in actions)
    gocc.assert_not_awaited()
    commit.assert_not_called()


# --------------------------------------------------------------------------
# Task 3: Sheets-tab reconciliation
# --------------------------------------------------------------------------

def test_tab_ok_when_page_resolves():
    gcog = _gcog(page_name="Tide")  # tab exists
    svc = _service_with_gsheet(gcog)
    p = _puzzle(google_page_id="42", tags=[10])
    actions = asyncio.run(svc._reconcile_tabs(_hunt(), p, apply=True))
    assert actions == []
    gcog.add_new_puzzle_sheet.assert_not_awaited()


def test_tab_recreated_when_missing_and_written_back():
    gcog = _gcog(page_name=None)  # tab gone

    async def _add(puzzle, index):
        puzzle.google_page_id = "77"
        return "77"
    gcog.add_new_puzzle_sheet.side_effect = _add
    svc = _service_with_gsheet(gcog)
    p = _puzzle(google_page_id="42", tags=[10])
    with patch.object(ps.PuzzleJsonDb, "commit") as commit:
        actions = asyncio.run(svc._reconcile_tabs(_hunt(), p, apply=True))
    assert any(a.kind == "tab_create" for a in actions)
    assert p.google_page_id == "77"
    gcog.update_puzzle_info.assert_awaited()
    commit.assert_called()


def test_tab_report_only_does_not_mutate():
    gcog = _gcog(page_name=None)
    svc = _service_with_gsheet(gcog)
    p = _puzzle(google_page_id="42", tags=[10])
    actions = asyncio.run(svc._reconcile_tabs(_hunt(), p, apply=False))
    assert any(a.kind == "tab_create" for a in actions)
    gcog.add_new_puzzle_sheet.assert_not_awaited()


def test_tab_no_cog_is_noop():
    svc = _service()  # bot.get_cog -> None
    p = _puzzle(google_page_id="42", tags=[10])
    actions = asyncio.run(svc._reconcile_tabs(_hunt(), p, apply=True))
    assert actions == []


# --------------------------------------------------------------------------
# M12: reconcile() fetches each spreadsheet's tab snapshot ONCE per run,
# instead of _reconcile_tabs issuing one full spreadsheets().get per puzzle.
# --------------------------------------------------------------------------

def test_the_snapshot_is_fetched_once_per_spreadsheet_not_once_per_puzzle():
    """Three puzzles, not one: with a single puzzle, per-puzzle fetching and
    shared fetching both make exactly one call and the test would prove
    nothing about the fix. _collect_orphan_tabs does its own separate,
    already-single fetch of the main spreadsheet; it is mocked out here so
    its call isn't conflated with the snapshot fetch under test.

    get_page_name_by_id is wired to genuinely re-invoke sheet_list (the real
    production relationship — see puzzles_gsheet.get_page_name_by_id), not
    stubbed as a standalone no-op: if _tab_present ever fell back to it per
    puzzle, this call count would jump from 2 to 5, so the assertion below
    is sensitive to that regression, not just to whether the fetch happens
    at all."""
    gcog = _gcog(page_name="Tide")
    gcog.sheet_list = AsyncMock(return_value={"sheets": [
        {"properties": {"sheetId": 42, "title": "Tide"}},
    ]})

    async def _get_page_name_by_id(page_id, spreadsheet_id=None):
        sheets = (await gcog.sheet_list(spreadsheet_id))["sheets"]
        for s in sheets:
            if s["properties"]["sheetId"] == int(page_id):
                return s["properties"].get("title")
        return None
    gcog.get_page_name_by_id = AsyncMock(side_effect=_get_page_name_by_id)
    svc = _service_with_gsheet(gcog)
    puzzles = [_puzzle(name=f"P{i}", google_page_id="42", tags=[]) for i in range(3)]
    guild = _guild_with(channels=[], categories=[])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=puzzles), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         patch.object(svc, "_reconcile_channel", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_collect_orphan_tabs", new=AsyncMock(return_value=None)):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=True))
    # _hunt() carries both a main spreadsheet and a solved spreadsheet, so the
    # snapshot is fetched twice total -- not once per puzzle, which would be
    # 3+ calls against three puzzles.
    assert gcog.sheet_list.await_count == 2
    assert report.failures == []


def test_a_puzzle_on_the_solved_sheet_is_checked_against_the_solved_snapshot():
    """A single flat id set across both spreadsheets would pass this only by
    accident: the two page ids are each present in only their OWN
    spreadsheet's snapshot, so a correct implementation must key by
    spreadsheet id to judge both puzzles present."""
    gcog = _gcog(page_name="unused")  # per-puzzle fallback must not be reached

    async def _sheet_list(spreadsheet_id=None):
        if spreadsheet_id == "SS":
            return {"sheets": [{"properties": {"sheetId": 10}}]}
        if spreadsheet_id == "ARC":
            return {"sheets": [{"properties": {"sheetId": 20}}]}
        raise AssertionError(f"unexpected spreadsheet id {spreadsheet_id!r}")
    gcog.sheet_list = AsyncMock(side_effect=_sheet_list)
    svc = _service_with_gsheet(gcog)
    p_main = _puzzle(name="Main", google_page_id="10", on_solved_sheet=False, tags=[])
    p_solved = _puzzle(name="Archived", google_page_id="20", on_solved_sheet=True, tags=[])
    guild = _guild_with(channels=[], categories=[])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt",
                      return_value=[p_main, p_solved]), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         patch.object(svc, "_reconcile_channel", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_collect_orphan_tabs", new=AsyncMock(return_value=None)):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=True))
    assert not any(a.kind == "tab_create" for a in report.actions)
    gcog.get_page_name_by_id.assert_not_awaited()


def test_apply_recreates_a_solved_puzzles_missing_tab_on_the_solved_spreadsheet():
    """Ruling R4 (tab hunt): the real cog's location rule decides where the
    recreated tab goes. Two puzzles so the mapping is checked both ways: the
    solved one's blank tab is created on ARC, the live one's on SS."""
    from bot.cogs.puzzles_gsheet import GoogleSheets
    cog = GoogleSheets.__new__(GoogleSheets)
    cog._notify_retry = None
    cog.get_spreadsheet_id = lambda: "SS"
    cog.get_solved_sheet_id = lambda: "ARC"
    created = []

    async def _blank(spreadsheet_id, name, index=None):
        created.append((spreadsheet_id, name))
        return 77
    cog.create_blank_tab = _blank
    cog.get_unique_tab_name = AsyncMock(side_effect=lambda name, sid: name)
    cog.batch_update = AsyncMock()
    cog.compute_insert_index = AsyncMock(return_value=3)
    cog.update_puzzle_info = AsyncMock()
    cog.get_page_name_by_id = AsyncMock(return_value=None)   # both tabs are gone
    solved = _puzzle(name="Done", google_page_id="20", on_solved_sheet=True, tags=[])
    live = _puzzle(name="Live", google_page_id="10", on_solved_sheet=False, tags=[])
    svc = _service_with_gsheet(cog)
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[solved, live]), \
         patch.object(ps.PuzzleJsonDb, "commit"), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         patch.object(svc, "_reconcile_channel", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_fetch_present_tab_ids", new=AsyncMock(return_value=None)), \
         patch.object(svc, "refresh_targets", new=AsyncMock(return_value=[])), \
         patch.object(svc, "refresh_header_links", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_collect_orphan_tabs", new=AsyncMock(return_value=None)):
        asyncio.run(svc.reconcile(_guild_with(), _hunt(), apply=True))
    assert created == [("ARC", "Done"), ("SS", "Live")]


def test_a_failed_snapshot_fetch_falls_back_instead_of_aborting():
    gcog = _gcog(page_name=None)  # per-puzzle path reports the tab missing
    gcog.sheet_list = AsyncMock(side_effect=RuntimeError("quota exceeded"))
    svc = _service_with_gsheet(gcog)
    p = _puzzle(google_page_id="42", tags=[])
    guild = _guild_with(channels=[], categories=[])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         patch.object(svc, "_reconcile_channel", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_collect_orphan_tabs", new=AsyncMock(return_value=None)):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=False))
    assert any(a.kind == "tab_create" for a in report.actions)
    assert report.failures == []
    gcog.get_page_name_by_id.assert_awaited()


# --------------------------------------------------------------------------
# Task 4: Top-level reconcile() — drive + orphan collection
# --------------------------------------------------------------------------

def test_reconcile_collects_orphan_channel():
    cat = _category(500)
    # Real channels carry the ChannelType enum, not the string "text" — and
    # ChannelType.text == "text" is False, so a string here would let a broken
    # type check pass while every real channel gets skipped in production.
    valid = SimpleNamespace(id=111, name="tide", category=cat,
                            type=discord.ChannelType.text)
    orphan = SimpleNamespace(id=222, name="stray", category=cat,
                             type=discord.ChannelType.text)
    cat.channels = [valid, orphan]
    guild = _guild_with(channels=[valid, orphan], categories=[cat])
    svc = _service()
    p = _puzzle(channel_id=111, channel_name="tide", google_page_id="1", tags=[10])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.TagGroupJsonDb, "get_all",
                      return_value=[SimpleNamespace(id=10, category_id=500, target_puzzle_id=None)]), \
         patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=False))
    assert (222, "stray") in report.orphan_channels
    assert (111, "tide") not in [(c, n) for c, n in report.orphan_channels]


def test_reconcile_flags_broken_round_category():
    guild = _guild_with(channels=[], categories=[])  # category 500 not present
    svc = _service()
    p = _puzzle(channel_id=111, channel_name="tide", google_page_id="1", tags=[])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.TagGroupJsonDb, "get_all",
                      return_value=[SimpleNamespace(id=10, category_id=500, target_puzzle_id=7)]):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=False))
    assert any(a.kind == "round_category_broken" for a in report.actions)


def test_reconcile_failure_isolated_per_puzzle():
    guild = _guild_with(channels=[], categories=[])
    svc = _service()
    p1 = _puzzle(name="Boom", channel_id=None, channel_name="boom", tags=[10])
    p2 = _puzzle(name="Fine", channel_id=None, channel_name="fine", tags=[10])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p1, p2]), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         patch.object(svc, "_reconcile_channel",
                      side_effect=[RuntimeError("boom"), []]), \
         patch.object(svc, "_reconcile_tabs", new=AsyncMock(return_value=[])):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=True))
    assert ("Boom", "boom") in [(t, e) for t, e in report.failures] or \
           any(t == "Boom" for t, _ in report.failures)


async def test_collect_orphan_tabs_skips_structural_tabs():
    """Structural tabs (REFERENCE template tab, Unassigned Puzzles, Evidence)
    and puzzle additional-sheet tabs are never orphans — only a genuinely
    unmatched tab is flagged."""
    def _sheet(sheet_id, title):
        return {"properties": {"sheetId": sheet_id, "title": title}}

    gcog = MagicMock()
    gcog.get_spreadsheet_id.return_value = "SS"
    gcog.sheet_list = AsyncMock(return_value={"sheets": [
        _sheet(100, "Tide"),               # valid puzzle tab
        _sheet(101, "Tide — notes"),       # puzzle additional sheet
        _sheet(200, "Unassigned Puzzles"), # structural (page-id tracked)
        _sheet(201, "Evidence"),           # structural (page-id tracked)
        _sheet(300, "REFERENCE"),          # structural template tab (untracked)
        _sheet(999, "old-puzzle"),         # genuine orphan
    ]})
    svc = _service_with_gsheet(gcog)
    hunt = make_hunt(id=1, google_sheet_id="SS", solved_sheet_id="ARC",
                     unassigned_sheet_page_id=200, evidence_sheet_page_id=201)
    p = _puzzle(google_page_id="100")
    p.additional_sheets = [SimpleNamespace(google_page_id="101")]
    report = ReconcileReport()
    await svc._collect_orphan_tabs(hunt, [p], report)
    assert report.orphan_tabs == [(999, "old-puzzle")]


# --------------------------------------------------------------------------
# Task 5: Orphan deletion
# --------------------------------------------------------------------------

def test_delete_orphans_deletes_channels_and_tabs():
    chan = AsyncMock()
    chan.delete = AsyncMock()
    guild = MagicMock()
    guild.get_channel.return_value = chan
    gcog = _gcog()
    gcog.delete_sheet_tab = AsyncMock()
    svc = _service_with_gsheet(gcog)
    report = ReconcileReport()
    report.add_orphan_channel(222, "stray")
    report.add_orphan_tab(333, "Stray Tab")
    deleted, failures = asyncio.run(svc.delete_orphans(guild, _hunt(), report))
    assert deleted == 2
    chan.delete.assert_awaited()
    gcog.delete_sheet_tab.assert_awaited()


def test_delete_orphans_tolerates_missing_channel():
    guild = MagicMock()
    guild.get_channel.return_value = None  # already gone
    svc = _service_with_gsheet(_gcog())
    report = ReconcileReport()
    report.add_orphan_channel(222, "stray")
    deleted, failures = asyncio.run(svc.delete_orphans(guild, _hunt(), report))
    assert deleted == 0


# --------------------------------------------------------------------------
# Task 6: Report formatter
# --------------------------------------------------------------------------

def test_format_report_summarises_counts_and_orphans():
    r = ReconcileReport()
    r.add("channel_create", "Tide")
    r.add("tab_create", "Tide")
    r.add("channel_relink", "Time")
    r.add_orphan_channel(1, "stray")
    r.add_failure("Boom", "kaboom")
    text = Puzzles._format_reconcile_report(r, applied=False)
    assert "channel_create: 1" in text
    assert "tab_create: 1" in text
    assert "1 orphan channel" in text
    assert "Boom" in text  # failures surfaced


def test_format_report_names_orphans():
    r = ReconcileReport()
    r.add_orphan_channel(1, "stray-chan")
    r.add_orphan_tab(2, "Stale Tab")
    text = Puzzles._format_reconcile_report(r, applied=False)
    # The warning that suggests running `apply orphans` should name the items.
    assert "#stray-chan" in text
    assert '"Stale Tab"' in text


def test_format_report_truncates_orphan_names():
    r = ReconcileReport()
    for i in range(25):
        r.add_orphan_channel(i, f"chan-{i}")
    text = Puzzles._format_reconcile_report(r, applied=False)
    assert "#chan-19" in text
    assert "#chan-20" not in text
    assert "…and 5 more" in text


def test_orphan_confirmation_names_items_to_delete():
    r = ReconcileReport()
    r.add_orphan_channel(1, "stray-chan")
    r.add_orphan_tab(2, "Stale Tab")
    text = Puzzles._format_orphan_confirmation(r)
    assert "Delete 1 channel(s) and 1 tab(s)" in text
    assert "#stray-chan" in text
    assert '"Stale Tab"' in text


def test_orphan_confirmation_truncates_long_lists():
    r = ReconcileReport()
    for i in range(25):
        r.add_orphan_channel(i, f"chan-{i}")
    text = Puzzles._format_orphan_confirmation(r, limit=20)
    assert "#chan-0" in text
    assert "#chan-19" in text
    assert "#chan-20" not in text
    assert "…and 5 more" in text


# --------------------------------------------------------------------------
# Task 7: Idempotency / convergence
# --------------------------------------------------------------------------

def test_apply_twice_is_convergent_for_healthy_hunt():
    """A hunt whose channels + tabs already match the DB yields no actions."""
    cat = _category(500)
    chan = SimpleNamespace(id=111, name="tide", category=cat, mention="<#111>",
                           type=discord.ChannelType.text)
    cat.channels = [chan]
    guild = _guild_with(channels=[chan], categories=[cat])
    gcog = _gcog(page_name="Tide")  # tab present
    bot = MagicMock(); bot.get_cog.return_value = gcog
    svc = PuzzleService(bot, notifier=AsyncMock())
    p = _puzzle(channel_id=111, channel_name="tide", google_page_id="42", tags=[10])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.TagGroupJsonDb, "get_all",
                      return_value=[SimpleNamespace(id=10, category_id=500, target_puzzle_id=None)]), \
         patch.object(ps.TagGroupJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=True))
    assert report.counts() == {}
    assert report.has_orphans() is False
    assert report.failures == []


# --------------------------------------------------------------------------
# M6: reconcile's category scope covers the hunt general + split/overflow
# categories, not just each round's primary category_id
# --------------------------------------------------------------------------

def _cat_rows(*pairs):
    """Join-table rows as (owner_id, category_id) pairs, keyed by owner id for
    a DAO stub's get_for_group."""
    by_owner = {}
    for owner_id, category_id in pairs:
        by_owner.setdefault(owner_id, []).append(
            SimpleNamespace(group_id=owner_id, hunt_id=owner_id,
                            category_id=category_id, role="unsolved", seq=0))
    return by_owner


def _patch_category_daos(group_rows=None, hunt_rows=None):
    """Patch both category join-table DAOs at their puzzle_service lookup site."""
    group_rows = group_rows or {}
    hunt_rows = hunt_rows or {}
    return (
        patch.object(ps.TagGroupCategoryDb, "get_for_group",
                     side_effect=lambda gid: group_rows.get(gid, [])),
        patch.object(ps.HuntCategoryDb, "get_for_group",
                     side_effect=lambda hid: hunt_rows.get(hid, [])),
    )


def test_relink_finds_channel_in_a_round_overflow_category():
    """The round's channels can live in an overflow category recorded in
    tag_group_category, not in its primary category_id. Scoping the relink
    search to primary ids only makes step 2 miss the channel and step 3 create
    a DUPLICATE at guild root (M6)."""
    primary, overflow = _category(500), _category(501)
    existing = SimpleNamespace(id=111, name="tide", category=overflow,
                               mention="<#111>", type=discord.ChannelType.text)
    overflow.channels = [existing]
    guild = _guild_with(channels=[existing], categories=[primary, overflow])
    svc = _service()
    p = _puzzle(name="Tide", channel_id=None, channel_name=None, tags=[10])
    grp = SimpleNamespace(id=10, category_id=500, target_puzzle_id=None)
    g_patch, h_patch = _patch_category_daos(group_rows=_cat_rows((10, 500), (10, 501)))
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.PuzzleJsonDb, "commit"), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[grp]), \
         patch.object(ps.TagGroupJsonDb, "get_by_attr", return_value=grp), \
         g_patch, h_patch:
        report = asyncio.run(svc.reconcile(guild, _hunt(id=1), apply=False))
    kinds = [a.kind for a in report.actions]
    assert "channel_relink" in kinds
    assert "channel_create" not in kinds


def test_relink_finds_channel_in_the_hunt_general_category():
    """Channel-home groups park channels in the hunt's own general category
    (hunt.category_id) and its overflow rows — also outside every round's
    primary category_id (M6)."""
    general = _category(900)
    existing = SimpleNamespace(id=111, name="tide", category=general,
                               mention="<#111>", type=discord.ChannelType.text)
    general.channels = [existing]
    guild = _guild_with(channels=[existing], categories=[general])
    svc = _service()
    p = _puzzle(name="Tide", channel_id=None, channel_name=None, tags=[])
    g_patch, h_patch = _patch_category_daos()
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.PuzzleJsonDb, "commit"), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         g_patch, h_patch:
        report = asyncio.run(
            svc.reconcile(guild, _hunt(id=1, category_id=900), apply=False))
    kinds = [a.kind for a in report.actions]
    assert "channel_relink" in kinds
    assert "channel_create" not in kinds


def test_orphan_scan_covers_overflow_categories():
    """A stray channel in an overflow category is as much an orphan as one in
    the primary category — the scan must use the same widened id set (M6)."""
    primary, overflow = _category(500), _category(501)
    valid = SimpleNamespace(id=111, name="tide", category=primary,
                            type=discord.ChannelType.text)
    orphan = SimpleNamespace(id=222, name="stray", category=overflow,
                             type=discord.ChannelType.text)
    primary.channels = [valid]
    overflow.channels = [orphan]
    guild = _guild_with(channels=[valid, orphan], categories=[primary, overflow])
    svc = _service()
    p = _puzzle(channel_id=111, channel_name="tide", tags=[10])
    grp = SimpleNamespace(id=10, category_id=500, target_puzzle_id=None)
    g_patch, h_patch = _patch_category_daos(group_rows=_cat_rows((10, 500), (10, 501)))
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[grp]), \
         patch.object(ps.TagGroupJsonDb, "get_by_attr", return_value=grp), \
         g_patch, h_patch:
        report = asyncio.run(svc.reconcile(guild, _hunt(id=1), apply=False))
    assert (222, "stray") in report.orphan_channels
    assert (111, "tide") not in report.orphan_channels


def test_orphan_scan_spares_the_hunt_general_channel():
    """Widening the scan to the hunt's general category brings the hunt's OWN
    channel into range; it is not a puzzle channel and must never be offered
    for deletion by `!reconcile apply-orphans` (M6)."""
    general = _category(900)
    hunt_channel = SimpleNamespace(id=777, name="hunt-general", category=general,
                                   type=discord.ChannelType.text)
    orphan = SimpleNamespace(id=222, name="stray", category=general,
                             type=discord.ChannelType.text)
    general.channels = [hunt_channel, orphan]
    guild = _guild_with(channels=[hunt_channel, orphan], categories=[general])
    svc = _service()
    g_patch, h_patch = _patch_category_daos()
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[]), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         g_patch, h_patch:
        report = asyncio.run(svc.reconcile(
            guild, _hunt(id=1, category_id=900, channel_id=777), apply=False))
    assert (777, "hunt-general") not in report.orphan_channels
    assert (222, "stray") in report.orphan_channels


# --------------------------------------------------------------------------
# Existing hunts pick up sheet links on `reconcile apply`
# --------------------------------------------------------------------------

def _run_reconcile_with_links(apply, *, fail_groups=(), link_failures=None):
    """Run reconcile over two puzzles (one meta group among two groups) and
    return (report, call order, refresh_header_links mock, refresh_targets mock)."""
    svc = _service()
    p1 = _puzzle(name="A", id=1, google_page_id="11")
    p2 = _puzzle(name="B", id=2, google_page_id="22")
    meta = SimpleNamespace(id=10, category_id=None, target_puzzle_id=2)
    plain = SimpleNamespace(id=11, category_id=None, target_puzzle_id=None)
    order = []

    async def tabs(*a, **k):
        order.append("tabs")
        return []

    async def targets(hunt, groups):
        order.append("targets")
        return list(fail_groups)

    async def links(hunt, puzzles, **k):
        order.append("links")
        return link_failures

    targets_m = AsyncMock(side_effect=targets)
    links_m = AsyncMock(side_effect=links)
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p1, p2]), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[meta, plain]), \
         patch.object(svc, "_fetch_present_tab_ids", new=AsyncMock(return_value={})), \
         patch.object(svc, "_reconcile_channel", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_reconcile_tabs", side_effect=tabs), \
         patch.object(svc, "refresh_targets", new=targets_m), \
         patch.object(svc, "refresh_header_links", new=links_m):
        report = asyncio.run(svc.reconcile(_guild_with(), _hunt(), apply=apply))
    return report, order, links_m, targets_m, (p1, p2), meta


def test_reconcile_apply_stamps_header_links_for_every_puzzle_after_healing():
    _r, order, links_m, targets_m, puzzles, meta = _run_reconcile_with_links(True)
    links_m.assert_awaited_once()
    assert list(links_m.await_args.args[1]) == list(puzzles)
    # Only the meta group is re-rendered, and links go last so a re-format
    # or recreated tab cannot lose them.
    assert list(targets_m.await_args.args[1]) == [meta]
    assert order == ["tabs", "tabs", "targets", "links"]


def test_reconcile_report_only_writes_no_links_or_rollups():
    _r, order, links_m, targets_m, *_ = _run_reconcile_with_links(False)
    links_m.assert_not_awaited()
    targets_m.assert_not_awaited()
    assert order == ["tabs", "tabs"]


def test_reconcile_apply_reports_a_failed_rollup_but_still_stamps_links():
    report, _o, links_m, *_ = _run_reconcile_with_links(True, fail_groups=[10])
    assert any("10" in t for t, _ in report.failures)
    links_m.assert_awaited_once()


def test_reconcile_apply_lists_each_tab_whose_feeds_line_failed():
    report, *_ = _run_reconcile_with_links(True, link_failures=["A", "B"])
    assert [t for t, _ in report.failures] == ["header links (A)", "header links (B)"]


def test_reconcile_apply_with_no_link_failures_reports_none():
    report, *_ = _run_reconcile_with_links(True, link_failures=[])
    assert report.failures == []


def test_reconcile_apply_refreshes_each_minimeta_table_before_the_links():
    # A minimeta's table is puzzle-keyed, not a tag-group target, so the meta
    # refresh misses it; older hunts' minimeta tables need their links too.
    svc = _service()
    mm = _puzzle(name="MM", id=1, google_page_id="11")
    m1 = _puzzle(name="m1", id=2, google_page_id="22", parent_puzzle_id=1, tags=[])
    m2 = _puzzle(name="m2", id=3, google_page_id="33", parent_puzzle_id=1, tags=[])
    other = _puzzle(name="Other", id=4, google_page_id="44")
    order = []

    async def minimeta(hunt, puzzle):
        order.append(("minimeta", puzzle.name))

    async def links(hunt, puzzles, **k):
        order.append("links")
        return []

    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[mm, m1, m2, other]), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         patch.object(svc, "_fetch_present_tab_ids", new=AsyncMock(return_value={})), \
         patch.object(svc, "_reconcile_channel", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_reconcile_tabs", new=AsyncMock(return_value=[])), \
         patch.object(svc, "refresh_targets", new=AsyncMock(return_value=[])), \
         patch.object(svc, "refresh_minimeta", new=AsyncMock(side_effect=minimeta)), \
         patch.object(svc, "refresh_header_links", new=AsyncMock(side_effect=links)):
        report = asyncio.run(svc.reconcile(_guild_with(), _hunt(), apply=True))
    assert order == [("minimeta", "MM"), "links"]
    assert report.failures == []


def test_reconcile_apply_reports_a_failed_minimeta_refresh_and_carries_on():
    svc = _service()
    mm = _puzzle(name="MM", id=1, google_page_id="11")
    m1 = _puzzle(name="m1", id=2, google_page_id="22", parent_puzzle_id=1, tags=[])
    links_m = AsyncMock(return_value=[])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[mm, m1]), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         patch.object(svc, "_fetch_present_tab_ids", new=AsyncMock(return_value={})), \
         patch.object(svc, "_reconcile_channel", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_reconcile_tabs", new=AsyncMock(return_value=[])), \
         patch.object(svc, "refresh_targets", new=AsyncMock(return_value=[])), \
         patch.object(svc, "refresh_minimeta",
                      new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(svc, "refresh_header_links", new=links_m):
        report = asyncio.run(svc.reconcile(_guild_with(), _hunt(), apply=True))
    assert [t for t, _ in report.failures] == ["minimeta roll-up (MM)"]
    links_m.assert_awaited_once()


def _run_reconcile_unassigned(apply, unassigned_side_effect=None):
    """Run reconcile with every sheet step stubbed; return (report, order)."""
    svc = _service()
    p1 = _puzzle(name="A", id=1, google_page_id="11")
    order = []

    async def targets(hunt, groups):
        order.append("targets")
        return []

    async def links(hunt, puzzles, **k):
        order.append("links")
        return []

    async def unassigned(hunt):
        order.append("unassigned")
        if unassigned_side_effect is not None:
            raise unassigned_side_effect

    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p1]), \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         patch.object(svc, "_fetch_present_tab_ids", new=AsyncMock(return_value={})), \
         patch.object(svc, "_reconcile_channel", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_reconcile_tabs", new=AsyncMock(return_value=[])), \
         patch.object(svc, "refresh_targets", new=AsyncMock(side_effect=targets)), \
         patch.object(svc, "refresh_header_links", new=AsyncMock(side_effect=links)), \
         patch.object(svc, "refresh_unassigned", new=AsyncMock(side_effect=unassigned)):
        report = asyncio.run(svc.reconcile(_guild_with(), _hunt(), apply=apply))
    return report, order


def test_reconcile_apply_rebuilds_the_unassigned_tab():
    # Awaited, not fire-and-forget: reconcile must report a failure, and the
    # dashboard's Reconcile panel is the only web route to this rebuild.
    report, order = _run_reconcile_unassigned(True)
    assert order == ["targets", "unassigned", "links"]
    assert report.failures == []


def test_reconcile_report_only_leaves_the_unassigned_tab_alone():
    _report, order = _run_reconcile_unassigned(False)
    assert "unassigned" not in order


def test_reconcile_apply_reports_a_failed_unassigned_rebuild_and_carries_on():
    report, order = _run_reconcile_unassigned(True, RuntimeError("quota"))
    assert [t for t, _ in report.failures] == ["Unassigned Feeders tab"]
    assert order[-1] == "links"


# --------------------------------------------------------------------------
# Files mode: each puzzle's own file in the hunt's Drive folders
# --------------------------------------------------------------------------

_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_FOLDER_MIME = "application/vnd.google-apps.folder"


def _files_hunt(**kw):
    return make_hunt(id=1, name="Hunt", google_sheet_id="REF", solved_sheet_id="",
                     sheet_mode="files", drive_folder_id="FOLDER",
                     solved_folder_id="SOLVED", **kw)


def _files_gcog(folders, tabs):
    """folders: {folder_id: [(file_id, name[, mimeType])]};
    tabs: {spreadsheet_id: [(sheetId, title)]} in tab order."""
    g = _gcog()
    g.get_spreadsheet_id.return_value = "REF"
    g.get_solved_sheet_id.return_value = ""

    async def _list_folder(folder_id):
        return [{"id": e[0], "name": e[1],
                 "mimeType": e[2] if len(e) > 2 else _SHEET_MIME}
                for e in folders.get(folder_id, [])]

    async def _sheet_list(spreadsheet_id=None):
        if spreadsheet_id not in tabs:
            raise AssertionError(f"unexpected read of {spreadsheet_id!r}")
        return {"sheets": [{"properties": {"sheetId": sid, "title": title, "index": i}}
                           for i, (sid, title) in enumerate(tabs[spreadsheet_id])]}
    g.list_folder = AsyncMock(side_effect=_list_folder)
    g.sheet_list = AsyncMock(side_effect=_sheet_list)
    g.get_page_name_by_id = AsyncMock(side_effect=AssertionError("per-tab lookup"))
    g.get_file = AsyncMock(return_value=None)   # default: the file is gone (404)
    g.move_file = AsyncMock()
    g.rename_file = AsyncMock()
    g.delete_file = AsyncMock()
    g.delete_sheet_tab = AsyncMock()
    g.mini_insert_index = AsyncMock(return_value=1)
    return g


def _run_files(gcog, puzzles, *, apply, loose=(), hunt=None):
    svc = _service_with_gsheet(gcog)
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=puzzles), \
         patch.object(ps.PuzzleJsonDb, "commit") as commit, \
         patch.object(ps.TagGroupJsonDb, "get_all", return_value=[]), \
         patch.object(ps.SheetsJsonDb, "get_by_hunt", return_value=list(loose)), \
         patch.object(svc, "_reconcile_channel", new=AsyncMock(return_value=[])), \
         patch.object(svc, "refresh_targets", new=AsyncMock(return_value=[])), \
         patch.object(svc, "refresh_minimeta", new=AsyncMock()), \
         patch.object(svc, "refresh_header_links", new=AsyncMock(return_value=[])):
        report = asyncio.run(svc.reconcile(_guild_with(), hunt or _files_hunt(),
                                           apply=apply))
    return report, commit


def _own(name, pid, file_id, page="0", solved=False):
    return _puzzle(name=name, id=pid, google_sheet_id=file_id, google_page_id=page,
                   solved=solved, tags=[])


def _healthy_files_hunt():
    a = _own("A", 1, "FA")
    b = _own("B", 2, "FB", solved=True)
    mm = _own("MM", 3, "FMM", page="100")
    m1 = _puzzle(name="m1", id=4, google_sheet_id="FMM", google_page_id="101",
                 parent_puzzle_id=3, tags=[])
    m2 = _puzzle(name="m2", id=5, google_sheet_id="FMM", google_page_id="102",
                 parent_puzzle_id=3, tags=[])
    folders = {
        "FOLDER": [("FA", "A"), ("FMM", "MM"), ("REF", "Hunt Reference"),
                   ("SOLVED", "Solved", _FOLDER_MIME)],
        "SOLVED": [("FB", "B")],
    }
    tabs = {
        "FMM": [(100, "MM"), (101, "m1"), (102, "m2")],
        "REF": [(1, "REFERENCE"), (500, "Loose notes")],
    }
    return [a, b, mm, m1, m2], folders, tabs


def test_a_healthy_files_hunt_reports_nothing():
    """Spec acceptance 7, and the read budget: each folder listed once, one
    read of the minimeta's file (two minis, still one), the reference read by
    the orphan scan -- no read of any other puzzle's file."""
    puzzles, folders, tabs = _healthy_files_hunt()
    gcog = _files_gcog(folders, tabs)
    report, _ = _run_files(gcog, puzzles, apply=False,
                           loose=[SimpleNamespace(google_page_id="500")])
    assert report.counts() == {}
    assert report.stray_files == []
    assert report.orphan_tabs == []
    assert report.has_orphans() is False
    assert report.failures == []
    assert sorted(c.args[0] for c in gcog.list_folder.await_args_list) == ["FOLDER", "SOLVED"]
    assert sorted(c.args[0] for c in gcog.sheet_list.await_args_list) == ["FMM", "REF"]


def test_apply_on_a_healthy_files_hunt_changes_nothing():
    puzzles, folders, tabs = _healthy_files_hunt()
    gcog = _files_gcog(folders, tabs)
    gcog.create_puzzle_file = AsyncMock()
    report, _ = _run_files(gcog, puzzles, apply=True,
                           loose=[SimpleNamespace(google_page_id="500")])
    assert report.counts() == {}
    gcog.create_puzzle_file.assert_not_awaited()
    gcog.move_file.assert_not_awaited()
    gcog.rename_file.assert_not_awaited()
    gcog.add_new_puzzle_sheet.assert_not_awaited()


def test_a_folder_with_no_id_is_never_listed():
    """An empty folder id must not reach list_folder (Drive would read it as
    the service account's root); with no Solved folder a solved puzzle
    belongs in the hunt folder."""
    b = _own("B", 2, "FB", solved=True)
    gcog = _files_gcog({"FOLDER": [("FB", "B")]}, {"REF": []})
    report, _ = _run_files(gcog, [b], apply=False,
                           hunt=make_hunt(id=1, google_sheet_id="REF", solved_sheet_id="",
                                          sheet_mode="files", drive_folder_id="FOLDER",
                                          solved_folder_id=""))
    assert [c.args[0] for c in gcog.list_folder.await_args_list] == ["FOLDER"]
    assert report.counts() == {}


def test_a_failed_folder_listing_is_a_failure_not_a_wave_of_file_creates():
    a = _own("A", 1, "FA")
    b = _own("B", 2, "FB")
    gcog = _files_gcog({}, {"REF": []})
    gcog.list_folder = AsyncMock(side_effect=RuntimeError("drive down"))
    gcog.create_puzzle_file = AsyncMock()
    report, _ = _run_files(gcog, [a, b], apply=True)
    assert report.counts() == {}
    assert len(report.failures) == 1
    gcog.create_puzzle_file.assert_not_awaited()


def test_a_missing_file_is_reported_file_create_without_touching_drive():
    no_id = _own("NoId", 1, "")
    gone = _own("Gone", 2, "GONE")
    gcog = _files_gcog({"FOLDER": [], "SOLVED": []}, {"REF": []})
    gcog.create_puzzle_file = AsyncMock()
    report, commit = _run_files(gcog, [no_id, gone], apply=False)
    assert [(a.kind, a.target) for a in report.actions] == [
        ("file_create", "NoId"), ("file_create", "Gone")]
    gcog.create_puzzle_file.assert_not_awaited()
    commit.assert_not_called()


def test_apply_creates_a_missing_file_in_the_folder_for_its_solved_state():
    """Two puzzles so the folder choice is a mapping: unsolved -> hunt folder,
    solved -> Solved (no follow-up move needed)."""
    open_ = _own("Open", 1, "")
    done = _own("Done", 2, "GONE", solved=True)
    gcog = _files_gcog({"FOLDER": [], "SOLVED": []}, {"REF": []})

    async def _create(puzzle, folder_id, header_links=None):
        puzzle.google_sheet_id = f"NEW-{puzzle.name}"
        puzzle.google_page_id = "0"
    gcog.create_puzzle_file = AsyncMock(side_effect=_create)
    report, commit = _run_files(gcog, [open_, done], apply=True)
    assert [(c.args[0].name, c.args[1]) for c in gcog.create_puzzle_file.await_args_list] == [
        ("Open", "FOLDER"), ("Done", "SOLVED")]
    assert open_.google_sheet_id == "NEW-Open"
    assert done.google_sheet_id == "NEW-Done"
    committed = [c.args[0] for c in commit.call_args_list]
    assert open_ in committed and done in committed
    assert report.counts() == {"file_create": 2}


def _http_error(status):
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = status
    resp.reason = "x"
    return HttpError(resp, b"{}")


def _live_elsewhere(name="Elsewhere", parents=("SUB",), trashed=False):
    return {"id": "x", "name": name, "parents": list(parents), "trashed": trashed}


def test_a_live_file_outside_both_folders_is_moved_not_recreated():
    """F1: in neither listing is not gone. Report and apply: move home with
    from_folder=None (all actual parents removed), no copy, id unchanged."""
    a = _own("A", 1, "FA")
    done = _own("Done", 2, "FD", solved=True)
    gcog = _files_gcog({"FOLDER": [], "SOLVED": []}, {"REF": []})
    gcog.get_file = AsyncMock(side_effect=lambda fid: _live_elsewhere(
        name="A" if fid == "FA" else "Done"))
    gcog.create_puzzle_file = AsyncMock()
    report, _ = _run_files(gcog, [a, done], apply=False)
    assert [(x.kind, x.target) for x in report.actions] == [
        ("file_move", "A"), ("file_move", "Done")]
    gcog.move_file.assert_not_awaited()
    gcog.create_puzzle_file.assert_not_awaited()
    report, commit = _run_files(gcog, [a, done], apply=True)
    assert [(c.args, c.kwargs) for c in gcog.move_file.await_args_list] == [
        (("FA", "FOLDER", None), {}), (("FD", "SOLVED", None), {})]
    gcog.create_puzzle_file.assert_not_awaited()
    assert (a.google_sheet_id, done.google_sheet_id) == ("FA", "FD")
    commit.assert_not_called()


def test_a_live_file_elsewhere_with_a_wrong_name_is_also_renamed():
    a = _own("A", 1, "FA")
    gcog = _files_gcog({"FOLDER": [], "SOLVED": []}, {"REF": []})
    gcog.get_file = AsyncMock(return_value=_live_elsewhere(name="Old name"))
    report, _ = _run_files(gcog, [a], apply=True)
    assert [x.kind for x in report.actions] == ["file_move", "file_rename"]
    gcog.rename_file.assert_awaited_once_with("FA", "A")


def test_a_trashed_file_is_recreated():
    a = _own("A", 1, "FA")
    gcog = _files_gcog({"FOLDER": [], "SOLVED": []}, {"REF": []})
    gcog.get_file = AsyncMock(return_value=_live_elsewhere(trashed=True))
    report, _ = _run_files(gcog, [a], apply=False)
    assert [(x.kind, x.target) for x in report.actions] == [("file_create", "A")]


def test_a_failed_file_lookup_is_a_failure_with_no_create_and_no_move():
    """Never create (or move) on uncertainty; the other puzzle is unaffected."""
    a = _own("A", 1, "FA")
    b = _own("B", 2, "FB")
    gcog = _files_gcog({"FOLDER": [("FB", "B")], "SOLVED": []}, {"REF": []})
    gcog.get_file = AsyncMock(side_effect=_http_error(500))
    gcog.create_puzzle_file = AsyncMock()
    report, _ = _run_files(gcog, [a, b], apply=True)
    assert report.counts() == {}
    assert len(report.failures) == 1 and "A" in report.failures[0][0]
    gcog.create_puzzle_file.assert_not_awaited()
    gcog.move_file.assert_not_awaited()
    gcog.get_file.assert_awaited_once_with("FA")


def test_each_puzzle_gets_its_own_file_decision():
    """One live-elsewhere, one 404 (and a healthy one that is never looked up)."""
    live = _own("Live", 1, "FL")
    gone = _own("Gone", 2, "FG")
    ok = _own("Ok", 3, "FO")
    gcog = _files_gcog({"FOLDER": [("FO", "Ok")], "SOLVED": []}, {"REF": []})

    async def _get(fid):
        return _live_elsewhere(name="Live") if fid == "FL" else None
    gcog.get_file = AsyncMock(side_effect=_get)
    gcog.create_puzzle_file = AsyncMock()
    report, _ = _run_files(gcog, [live, gone, ok], apply=False)
    assert [(x.kind, x.target) for x in report.actions] == [
        ("file_move", "Live"), ("file_create", "Gone")]
    assert sorted(c.args[0] for c in gcog.get_file.await_args_list) == ["FG", "FL"]


def test_a_file_in_the_wrong_folder_is_moved_both_ways():
    """Two instances, so a reversed from/to would fail one of them."""
    open_in_solved = _own("Open", 1, "FO")
    solved_in_live = _own("Done", 2, "FD", solved=True)
    folders = {"FOLDER": [("FD", "Done")], "SOLVED": [("FO", "Open")]}
    gcog = _files_gcog(folders, {"REF": []})
    report, _ = _run_files(gcog, [open_in_solved, solved_in_live], apply=False)
    assert report.counts() == {"file_move": 2}
    gcog.move_file.assert_not_awaited()

    gcog = _files_gcog(folders, {"REF": []})
    report, _ = _run_files(gcog, [open_in_solved, solved_in_live], apply=True)
    assert [c.args for c in gcog.move_file.await_args_list] == [
        ("FO", "FOLDER", "SOLVED"), ("FD", "SOLVED", "FOLDER")]


def test_a_misnamed_file_is_renamed_to_the_puzzle_name():
    a = _own("Tide", 1, "FA")
    gcog = _files_gcog({"FOLDER": [("FA", "Old name")]}, {"REF": []})
    report, _ = _run_files(gcog, [a], apply=False)
    assert [(x.kind, x.target) for x in report.actions] == [("file_rename", "Tide")]
    gcog.rename_file.assert_not_awaited()

    gcog = _files_gcog({"FOLDER": [("FA", "Old name")]}, {"REF": []})
    _run_files(gcog, [a], apply=True)
    gcog.rename_file.assert_awaited_once_with("FA", "Tide")


def test_a_stray_file_is_reported_and_never_deleted():
    """Neither the reference, the Solved subfolder nor a puzzle's file is a
    stray; `apply orphans` (delete_orphans) leaves the stray alone."""
    a = _own("A", 1, "FA")
    folders = {"FOLDER": [("FA", "A"), ("REF", "Hunt Reference"),
                          ("SOLVED", "Solved", _FOLDER_MIME), ("STRAY1", "notes")],
               "SOLVED": [("STRAY2", "old copy")]}
    gcog = _files_gcog(folders, {"REF": [(999, "old-tab")]})
    report, _ = _run_files(gcog, [a], apply=True)
    assert report.stray_files == [("STRAY1", "notes"), ("STRAY2", "old copy")]
    assert report.orphan_tabs == [(999, "old-tab")]
    guild = _guild_with()
    svc = _service_with_gsheet(gcog)
    deleted, failures = asyncio.run(svc.delete_orphans(guild, _files_hunt(), report))
    assert deleted == 1       # the orphan tab only
    gcog.delete_sheet_tab.assert_awaited_once_with(999, "REF")
    gcog.delete_file.assert_not_awaited()
    assert report.stray_files == [("STRAY1", "notes"), ("STRAY2", "old copy")]


def test_stray_files_alone_are_not_orphans_to_delete():
    gcog = _files_gcog({"FOLDER": [("STRAY", "notes")]}, {"REF": []})
    report, _ = _run_files(gcog, [], apply=False)
    assert report.stray_files == [("STRAY", "notes")]
    assert report.has_orphans() is False


def _minimeta_family(mm_file):
    mm = _own("MM", 3, mm_file, page="100")
    m1 = _puzzle(name="m1", id=4, google_sheet_id=mm_file, google_page_id="101",
                 parent_puzzle_id=3, tags=[])
    m2 = _puzzle(name="m2", id=5, google_sheet_id=mm_file, google_page_id="102",
                 parent_puzzle_id=3, tags=[])
    return mm, m1, m2


def test_a_missing_minimeta_file_is_one_file_create_not_a_tab_create_per_mini():
    mm, m1, m2 = _minimeta_family("GONE")
    gcog = _files_gcog({"FOLDER": [], "SOLVED": []}, {"REF": []})
    report, _ = _run_files(gcog, [m1, mm, m2], apply=False)
    assert [(a.kind, a.target) for a in report.actions] == [("file_create", "MM")]


def test_recreating_a_minimeta_file_recreates_its_minis_tabs_with_new_ids():
    """Two minis: each lands in the new file, by the mini index rule over the
    minis already recreated, in order, and is committed with the new id."""
    mm, m1, m2 = _minimeta_family("GONE")
    gcog = _files_gcog({"FOLDER": [], "SOLVED": []}, {"REF": []})

    async def _create(puzzle, folder_id, header_links=None):
        puzzle.google_sheet_id = "NEWFILE"
        puzzle.google_page_id = "900"
    gcog.create_puzzle_file = AsyncMock(side_effect=_create)
    seen = []

    async def _index(minimeta, minis):
        seen.append((minimeta.name, [m.name for m in minis]))
        return len(minis) + 1
    gcog.mini_insert_index = AsyncMock(side_effect=_index)
    pages = iter(["901", "902"])

    async def _add(puzzle, index, **kw):
        assert puzzle.google_sheet_id == "NEWFILE"   # addressed at the new file
        puzzle.google_page_id = next(pages)
    gcog.add_new_puzzle_sheet = AsyncMock(side_effect=_add)
    report, commit = _run_files(gcog, [m1, mm, m2], apply=True)
    assert [(c.args[0].name, c.args[1]) for c in gcog.add_new_puzzle_sheet.await_args_list] == [
        ("m1", 1), ("m2", 2)]
    assert seen == [("MM", []), ("MM", ["m1"])]
    assert (m1.google_sheet_id, m1.google_page_id) == ("NEWFILE", "901")
    assert (m2.google_sheet_id, m2.google_page_id) == ("NEWFILE", "902")
    committed = [c.args[0] for c in commit.call_args_list]
    assert mm in committed and m1 in committed and m2 in committed
    assert report.counts() == {"file_create": 1}


def test_a_missing_mini_tab_is_found_by_one_read_and_recreated():
    mm, m1, m2 = _minimeta_family("FMM")
    folders = {"FOLDER": [("FMM", "MM")], "SOLVED": []}
    tabs = {"FMM": [(100, "MM"), (101, "m1")], "REF": []}     # m2's tab gone
    gcog = _files_gcog(folders, tabs)
    report, _ = _run_files(gcog, [mm, m1, m2], apply=False)
    assert [(a.kind, a.target) for a in report.actions] == [("tab_create", "m2")]
    assert [c.args[0] for c in gcog.sheet_list.await_args_list].count("FMM") == 1

    gcog = _files_gcog(folders, tabs)
    gcog.mini_insert_index = AsyncMock(return_value=2)
    _run_files(gcog, [mm, m1, m2], apply=True)
    gcog.add_new_puzzle_sheet.assert_awaited_once_with(m2, 2)


def _loose_sheet_orphan_scan(hunt, spreadsheet_id):
    gcog = MagicMock()
    gcog.get_spreadsheet_id.return_value = spreadsheet_id
    gcog.sheet_list = AsyncMock(return_value={"sheets": [
        {"properties": {"sheetId": 100, "title": "Tide"}},
        {"properties": {"sheetId": 500, "title": "Loose notes"}},
        {"properties": {"sheetId": 501, "title": "Loose answers"}},
        {"properties": {"sheetId": 999, "title": "old-puzzle"}},
    ]})
    svc = _service_with_gsheet(gcog)
    p = _puzzle(google_page_id="100")
    p.additional_sheets = []
    report = ReconcileReport()
    loose = [SimpleNamespace(google_page_id="500"), SimpleNamespace(google_page_id="501")]
    with patch.object(ps.SheetsJsonDb, "get_by_hunt", return_value=loose) as by_hunt:
        asyncio.run(svc._collect_orphan_tabs(hunt, [p], report))
    by_hunt.assert_called_once_with(hunt.id)
    gcog.sheet_list.assert_awaited_once_with(spreadsheet_id)
    return report


def test_a_loose_hunt_sheet_is_not_an_orphan_tab_in_a_tab_hunt():
    """Data-loss fix: `apply orphans` deleted every loose hunt sheet."""
    report = _loose_sheet_orphan_scan(_hunt(), "SS")
    assert report.orphan_tabs == [(999, "old-puzzle")]


def test_a_loose_hunt_sheet_is_not_an_orphan_tab_in_a_files_hunt():
    report = _loose_sheet_orphan_scan(_files_hunt(), "REF")
    assert report.orphan_tabs == [(999, "old-puzzle")]


def test_format_report_names_stray_files_as_never_deleted():
    r = ReconcileReport()
    r.add("file_move", "Tide")
    r.add_stray_file("F1", "notes")
    r.add_stray_file("F2", "old copy")
    text = Puzzles._format_reconcile_report(r, applied=False)
    assert "file_move: 1" in text
    assert "2 stray files" in text
    assert '"notes"' in text and '"old copy"' in text
    assert "never deleted" in text
