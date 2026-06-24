"""Unit tests for the !reconcile feature. Stubs the Discord + Sheets seams in the
ctx-free PuzzleService, in the style of the sibling gsheet/create suites."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot.core.puzzle_service as ps
from bot.core.puzzle_service import PuzzleService
from bot.core.reconcile import ReconcileReport
from bot.cogs.puzzles import Puzzles
from bot.store.puzzle_data import PuzzleData
from tests.conftest import make_hunt


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _service(notifier=None):
    bot = MagicMock()
    bot.get_cog.return_value = None  # no GoogleSheets cog in channel tests
    return PuzzleService(bot, notifier=notifier or AsyncMock())


def _hunt():
    return make_hunt(id=1, google_sheet_id="SS", archive_google_sheet_id="ARC")


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
    g.set_archive_spreadsheet_id = MagicMock()
    g.get_spreadsheet_id.return_value = "SS"
    g.get_archive_spreadsheet_id.return_value = "ARC"
    g.INITIAL_OFFSET = 999
    g.get_page_name_by_id = MagicMock(return_value=page_name)
    g.add_new_puzzle_sheet = AsyncMock()
    g.update_puzzle_info = AsyncMock()
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
    with patch.object(ps.RoundJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)):
        report = asyncio.run(svc._reconcile_channel(guild, _hunt(), p, None, apply=True))
    assert report == []  # no actions: already correct


def test_channel_missing_and_no_existing_creates_and_welcomes():
    guild = _guild_with(channels=[], categories=[_category(500)])
    notifier = AsyncMock()
    svc = _service(notifier=notifier)
    p = _puzzle(channel_id=None, channel_name="tide", tags=[10])
    created_chan = SimpleNamespace(id=777, name="tide", mention="<#777>", category=None)
    with patch.object(ps.RoundJsonDb, "get_by_attr",
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
    with patch.object(ps.RoundJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)), \
         patch.object(ps.PuzzleJsonDb, "commit") as commit:
        actions = asyncio.run(svc._reconcile_channel(guild, _hunt(), p, None, apply=True))
    assert any(a.kind == "channel_relink" for a in actions)
    assert p.channel_id == 888
    commit.assert_called()
    notifier.welcome.assert_not_awaited()  # existing channel kept — no repost


def test_channel_report_only_does_not_mutate():
    guild = _guild_with(channels=[], categories=[_category(500)])
    svc = _service()
    p = _puzzle(channel_id=None, channel_name="tide", tags=[10])
    with patch.object(ps.RoundJsonDb, "get_by_attr",
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
# Task 4: Top-level reconcile() — drive + orphan collection
# --------------------------------------------------------------------------

def test_reconcile_collects_orphan_channel():
    cat = _category(500)
    valid = SimpleNamespace(id=111, name="tide", category=cat, type="text")
    orphan = SimpleNamespace(id=222, name="stray", category=cat, type="text")
    cat.channels = [valid, orphan]
    guild = _guild_with(channels=[valid, orphan], categories=[cat])
    svc = _service()
    p = _puzzle(channel_id=111, channel_name="tide", google_page_id="1", tags=[10])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.RoundJsonDb, "get_all",
                      return_value=[SimpleNamespace(id=10, category_id=500, meta_id=None)]), \
         patch.object(ps.RoundJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=False))
    assert (222, "stray") in report.orphan_channels
    assert (111, "tide") not in [(c, n) for c, n in report.orphan_channels]


def test_reconcile_flags_broken_round_category():
    guild = _guild_with(channels=[], categories=[])  # category 500 not present
    svc = _service()
    p = _puzzle(channel_id=111, channel_name="tide", google_page_id="1", tags=[])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.RoundJsonDb, "get_all",
                      return_value=[SimpleNamespace(id=10, category_id=500, meta_id=7)]):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=False))
    assert any(a.kind == "round_category_broken" for a in report.actions)


def test_reconcile_failure_isolated_per_puzzle():
    guild = _guild_with(channels=[], categories=[])
    svc = _service()
    p1 = _puzzle(name="Boom", channel_id=None, channel_name="boom", tags=[10])
    p2 = _puzzle(name="Fine", channel_id=None, channel_name="fine", tags=[10])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p1, p2]), \
         patch.object(ps.RoundJsonDb, "get_all", return_value=[]), \
         patch.object(svc, "_reconcile_channel",
                      side_effect=[RuntimeError("boom"), []]), \
         patch.object(svc, "_reconcile_tabs", new=AsyncMock(return_value=[])):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=True))
    assert ("Boom", "boom") in [(t, e) for t, e in report.failures] or \
           any(t == "Boom" for t, _ in report.failures)


def test_collect_orphan_tabs_skips_structural_tabs():
    """Structural tabs (REFERENCE template tab, Unassigned Puzzles, Evidence)
    and puzzle additional-sheet tabs are never orphans — only a genuinely
    unmatched tab is flagged."""
    def _sheet(sheet_id, title):
        return {"properties": {"sheetId": sheet_id, "title": title}}

    gcog = MagicMock()
    gcog.get_spreadsheet_id.return_value = "SS"
    gcog.sheet_list.return_value = {"sheets": [
        _sheet(100, "Tide"),               # valid puzzle tab
        _sheet(101, "Tide — notes"),       # puzzle additional sheet
        _sheet(200, "Unassigned Puzzles"), # structural (page-id tracked)
        _sheet(201, "Evidence"),           # structural (page-id tracked)
        _sheet(300, "REFERENCE"),          # structural template tab (untracked)
        _sheet(999, "old-puzzle"),         # genuine orphan
    ]}
    svc = _service_with_gsheet(gcog)
    hunt = make_hunt(id=1, google_sheet_id="SS", archive_google_sheet_id="ARC",
                     unassigned_sheet_page_id=200, evidence_sheet_page_id=201)
    p = _puzzle(google_page_id="100")
    p.additional_sheets = [SimpleNamespace(google_page_id="101")]
    report = ReconcileReport()
    svc._collect_orphan_tabs(hunt, [p], report)
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
    chan = SimpleNamespace(id=111, name="tide", category=cat, mention="<#111>", type="text")
    cat.channels = [chan]
    guild = _guild_with(channels=[chan], categories=[cat])
    gcog = _gcog(page_name="Tide")  # tab present
    bot = MagicMock(); bot.get_cog.return_value = gcog
    svc = PuzzleService(bot, notifier=AsyncMock())
    p = _puzzle(channel_id=111, channel_name="tide", google_page_id="42", tags=[10])
    with patch.object(ps.PuzzleJsonDb, "get_all_from_hunt", return_value=[p]), \
         patch.object(ps.RoundJsonDb, "get_all",
                      return_value=[SimpleNamespace(id=10, category_id=500, meta_id=None)]), \
         patch.object(ps.RoundJsonDb, "get_by_attr",
                      return_value=SimpleNamespace(id=10, category_id=500)):
        report = asyncio.run(svc.reconcile(guild, _hunt(), apply=True))
    assert report.counts() == {}
    assert report.has_orphans() is False
    assert report.failures == []
