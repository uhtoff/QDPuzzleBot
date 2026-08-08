"""Unit tests for the !reconcile feature. Stubs the Discord + Sheets seams in the
ctx-free PuzzleService, in the style of the sibling gsheet/create suites."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

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
