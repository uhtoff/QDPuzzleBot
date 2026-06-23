# `!reconcile` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an organiser-only `!reconcile` command that makes Discord channels and Google Sheets tabs match the database (the source of truth), healing partially-applied creates/edits.

**Architecture:** Logic lives in a ctx-free `PuzzleService.reconcile(...)` (testable without Discord), plus a small `bot/core/reconcile.py` report model. The Discord cog adds a thin `reconcile` command that parses `apply`/`orphans` args, prints the report, and gates orphan deletion behind the existing `ConfirmView`. Mirrors the `!rebuild` pattern (organiser gate, per-item try/except, summary message).

**Tech Stack:** Python 3.13, discord.py, google-api-python-client, pytest. DB via `PuzzleJsonDb`/`RoundJsonDb`. Tests stub the Discord + Sheets seams with `unittest.mock` (the existing `_cog()`/`AsyncMock`/`patch.object` style).

**Spec:** `docs/superpowers/specs/2026-06-23-reconcile-design.md`

---

## Key seams (confirmed in code — reuse, do not reinvent)

- `PuzzleService.__init__(self, bot, notifier=None)`; has `self.bot`, `self.notifier`, and `self._gsheet_cog(hunt)` which returns the `GoogleSheets` cog pointed at this hunt's spreadsheets (or `None` in tests). — `bot/core/puzzle_service.py:32-46`
- `PuzzleJsonDb.get_all_from_hunt(hunt_id) -> List[PuzzleData]` — `bot/store/mysqldb.py:287`
- `RoundJsonDb.get_all(hunt_id) -> List[RoundData]`, `RoundJsonDb.get_by_attr(id=...)` — `bot/store/mysqldb.py:179,164`
- `PuzzleData` fields: `tags: List[int]` (round ids; `tags[0]` is the home round), `channel_id`, `channel_name`, `channel_mention`, `google_page_id` (tab sheetId, a str), `archived`, `additional_sheets: List[AdditionalSheetData]` (each has `.google_page_id`). — `bot/store/puzzle_data.py`
- `RoundData` fields: `category_id`, `meta_id`. — `bot/store/round_data.py`
- `get_or_create_channel(guild=, category=, channel_name=, channel_type="text") -> (channel, created)` (idempotent by category+name+type), `clean_name(name)`, `solve_divider_position(category)`. — `bot/core/discord_channels.py`
- `notifier.welcome(guild, hunt, hunt_round=, puzzle=, channel=)` — `bot/core/notifier.py:91`
- GoogleSheets cog: `get_page_name_by_id(sheet_id, spreadsheet_id=None)` (returns title or `None`), `add_new_puzzle_sheet(puzzle, index)` (sets `puzzle.google_page_id`, returns it), `update_puzzle_info(puzzle)`, `sheet_list(spreadsheet_id=None)` (now guaranteed to contain `"sheets"`), `get_spreadsheet_id()`, `get_archive_spreadsheet_id()`, `INITIAL_OFFSET`. — `bot/cogs/puzzles_gsheet.py`
- `ConfirmView(author_id, *, timeout=30.0)` with `.value` (True/False/None) and `.message`. — `bot/cogs/ui.py`
- Cog has `self.service`, `get_hunt(ctx)`, `ctx.guild`; gates use `@commands.has_any_role(*ORGANISER_ROLES)`. `!rebuild` is at `bot/cogs/puzzles.py:618-655`.

## Test command (project convention)

```
timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1
```
Read `/tmp/test.log` for results (a timeout-killed run exits 124).

## File structure

- Create `bot/core/reconcile.py` — `ReconcileAction` + `ReconcileReport` (pure data; no Discord/Sheets imports).
- Modify `bot/core/puzzle_service.py` — add `reconcile(...)` and `delete_orphans(...)` methods + private helpers.
- Modify `bot/cogs/puzzles.py` — add the `reconcile` command + a `_format_reconcile_report` helper.
- Create `tests/test_reconcile.py` — unit tests stubbing the seams.

---

### Task 1: Report model

**Files:**
- Create: `bot/core/reconcile.py`
- Test: `tests/test_reconcile.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reconcile.py
"""Unit tests for the !reconcile feature. Stubs the Discord + Sheets seams in the
ctx-free PuzzleService, in the style of the sibling gsheet/create suites."""
from bot.core.reconcile import ReconcileReport


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.core.reconcile'`.

- [ ] **Step 3: Write minimal implementation**

```python
# bot/core/reconcile.py
"""Pure data model for the !reconcile feature (no Discord/Sheets imports).

A ReconcileReport accumulates the actions reconcile took (or would take, in
report-only mode), plus orphan candidates and per-item failures, so the cog edge
can render a summary and gate destructive deletes behind a confirmation.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class ReconcileAction:
    kind: str          # e.g. "channel_create", "channel_relink", "channel_move",
                       # "channel_rename", "tab_create", "tab_rename",
                       # "round_category_broken"
    target: str        # human label (puzzle/round name)
    detail: str = ""   # extra context for the report line


@dataclass
class ReconcileReport:
    actions: List[ReconcileAction] = field(default_factory=list)
    orphan_channels: List[Tuple[int, str]] = field(default_factory=list)  # (channel_id, name)
    orphan_tabs: List[Tuple[int, str]] = field(default_factory=list)      # (sheet_id, title)
    failures: List[Tuple[str, str]] = field(default_factory=list)         # (target, error)

    def add(self, kind, target, detail=""):
        self.actions.append(ReconcileAction(kind, target, detail))

    def add_orphan_channel(self, channel_id, name):
        self.orphan_channels.append((channel_id, name))

    def add_orphan_tab(self, sheet_id, title):
        self.orphan_tabs.append((sheet_id, title))

    def add_failure(self, target, error):
        self.failures.append((target, str(error)))

    def counts(self):
        return dict(Counter(a.kind for a in self.actions))

    def has_orphans(self):
        return bool(self.orphan_channels or self.orphan_tabs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/core/reconcile.py tests/test_reconcile.py
git commit -m "reconcile: report data model"
```

---

### Task 2: Channel reconciliation (resolution order: relink/move beats recreate)

**Files:**
- Modify: `bot/core/puzzle_service.py` (add `reconcile` + `_reconcile_channel`)
- Test: `tests/test_reconcile.py`

The home round is `tags[0]`. Resolution order per the spec:
1. `channel_id` resolves to a live channel → fix category (move) / name (rename) drift only. No welcome.
2. `channel_id` missing/broken but a channel named `clean_name(name)` exists in the guild → relink + move to home category. No welcome.
3. No channel anywhere → create + `notifier.welcome(...)`.

In report-only mode (`apply=False`) record the action but perform no mutation.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_reconcile.py
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot.core.puzzle_service as ps
from bot.core.puzzle_service import PuzzleService
from bot.store.puzzle_data import PuzzleData


def _service(notifier=None):
    bot = MagicMock()
    bot.get_cog.return_value = None  # no GoogleSheets cog in channel tests
    return PuzzleService(bot, notifier=notifier or AsyncMock())


def _hunt():
    return SimpleNamespace(id=1, google_sheet_id="SS", archive_google_sheet_id="ARC")


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleService' object has no attribute '_reconcile_channel'`.

- [ ] **Step 3: Implement `_reconcile_channel`**

Add to `PuzzleService` in `bot/core/puzzle_service.py` (import `ReconcileAction`/`ReconcileReport` at top: `from bot.core.reconcile import ReconcileReport, ReconcileAction`):

```python
    def _home_category(self, guild, puzzle):
        """Discord category the puzzle's single channel belongs in = tags[0]'s round.
        Returns (category_or_None, home_round_or_None)."""
        if not puzzle.tags:
            return None, None
        home_round = RoundJsonDb.get_by_attr(id=puzzle.tags[0])
        if home_round is None or not home_round.category_id:
            return None, home_round
        category = discord.utils.get(guild.categories, id=home_round.category_id)
        return category, home_round

    async def _reconcile_channel(self, guild, hunt, puzzle, report, *, apply):
        """Make the puzzle's Discord channel match the DB. Returns the list of
        actions taken/needed. Resolution order: keep+fix existing > relink named >
        create. Welcome is reposted ONLY when a brand-new channel is created."""
        actions = []
        category, home_round = self._home_category(guild, puzzle)
        want_name = clean_name(puzzle.name)

        # 1. Stored id resolves to a live channel: fix category/name drift only.
        live = guild.get_channel(puzzle.channel_id) if puzzle.channel_id else None
        if live is not None:
            if category is not None and getattr(live, "category", None) is not category:
                actions.append(ReconcileAction("channel_move", puzzle.name,
                                               f"-> category {category.id}"))
                if apply:
                    await live.edit(category=category)
            if live.name != want_name:
                actions.append(ReconcileAction("channel_rename", puzzle.name,
                                               f"{live.name} -> {want_name}"))
                if apply:
                    await live.edit(name=want_name)
            return actions

        # 2. A channel with the right name already exists somewhere: relink + move.
        existing = discord.utils.get(guild.channels, name=want_name)
        if existing is not None:
            actions.append(ReconcileAction("channel_relink", puzzle.name,
                                           f"-> #{existing.id}"))
            if apply:
                if category is not None and getattr(existing, "category", None) is not category:
                    await existing.edit(category=category)
                puzzle.channel_id = existing.id
                puzzle.channel_name = existing.name
                puzzle.channel_mention = existing.mention
                PuzzleJsonDb.commit(puzzle)
            return actions

        # 3. Nothing found: create + welcome.
        actions.append(ReconcileAction("channel_create", puzzle.name, "no channel found"))
        if apply:
            text_channel, created = await get_or_create_channel(
                guild=guild, category=category, channel_name=want_name, channel_type="text",
            )
            if created and category is not None:
                divider = solve_divider_position(category)
                if divider is not False:
                    await text_channel.edit(position=divider)
            puzzle.channel_id = text_channel.id
            puzzle.channel_name = text_channel.name
            puzzle.channel_mention = text_channel.mention
            PuzzleJsonDb.commit(puzzle)
            if created and self.notifier is not None:
                await self.notifier.welcome(
                    guild, hunt, hunt_round=home_round, puzzle=puzzle, channel=text_channel,
                )
        return actions
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: PASS (Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_reconcile.py
git commit -m "reconcile: channel relink/move/create logic"
```

---

### Task 3: Sheets-tab reconciliation

**Files:**
- Modify: `bot/core/puzzle_service.py` (add `_reconcile_tabs`)
- Test: `tests/test_reconcile.py`

Pick spreadsheet by `puzzle.archived`. If `google_page_id` is set and
`get_page_name_by_id(google_page_id, spreadsheet)` resolves, the tab is fine.
If missing/gone, recreate via `add_new_puzzle_sheet` (sets a fresh
`google_page_id`), then `update_puzzle_info(puzzle)`, then commit. Same loop for
`additional_sheets`. No-op (record nothing) when there is no GoogleSheets cog.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_reconcile.py

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: ... '_reconcile_tabs'`.

- [ ] **Step 3: Implement `_reconcile_tabs`**

```python
    async def _reconcile_tabs(self, hunt, puzzle, *, apply):
        """Ensure the puzzle's Sheets tab(s) exist. Recreate a missing tab and write
        the fresh sheetId back to the DB. No-op without a GoogleSheets cog."""
        actions = []
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return actions
        spreadsheet = (gsheet_cog.get_archive_spreadsheet_id() if puzzle.archived
                       else gsheet_cog.get_spreadsheet_id())

        def _tab_present(page_id):
            if not page_id:
                return False
            return gsheet_cog.get_page_name_by_id(page_id, spreadsheet) is not None

        if not _tab_present(puzzle.google_page_id):
            actions.append(ReconcileAction("tab_create", puzzle.name, "main tab missing"))
            if apply:
                await gsheet_cog.add_new_puzzle_sheet(puzzle, gsheet_cog.INITIAL_OFFSET)
                await gsheet_cog.update_puzzle_info(puzzle)
                PuzzleJsonDb.commit(puzzle)
        return actions
```

Note: additional-sheet recreation is intentionally deferred — `add_new_puzzle_sheet` rebuilds the primary puzzle tab; extra working sheets are rare and out of the incident's scope. If needed later, loop `puzzle.additional_sheets` with the same present/recreate pattern. (Spec lists them; keep this as a known follow-up, not a placeholder in shipped code.)

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_reconcile.py
git commit -m "reconcile: recreate missing Sheets tab from DB"
```

---

### Task 4: Top-level `reconcile()` — drive all puzzles, rounds, and orphan collection

**Files:**
- Modify: `bot/core/puzzle_service.py` (add `reconcile`)
- Test: `tests/test_reconcile.py`

`reconcile(guild, hunt, *, apply)` iterates puzzles (channel + tabs, each wrapped
in try/except → `report.add_failure`), checks each round's category resolves
(`round_category_broken` action when not), and collects orphan candidates. It
NEVER deletes — orphans are only recorded.

Orphan channels: text channels under the hunt's round categories whose id is in no
puzzle's `channel_id`, excluding the round category objects themselves and system
channels (name containing the solved-divider em-dash `———` or equal to a round's
own name marker). Orphan tabs: tabs in the hunt spreadsheet whose `sheetId` matches
no puzzle `google_page_id`, excluding hidden/template sheets.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_reconcile.py

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: ... 'reconcile'`.

- [ ] **Step 3: Implement `reconcile` + orphan helpers**

```python
    async def reconcile(self, guild, hunt, *, apply):
        """Make Discord + Sheets match the DB for an entire hunt. Returns a
        ReconcileReport. Never deletes — orphans are only collected. Each puzzle is
        isolated: one failure is recorded and the run continues."""
        report = ReconcileReport()
        puzzles = PuzzleJsonDb.get_all_from_hunt(hunt.id)

        for puzzle in puzzles:
            try:
                for action in await self._reconcile_channel(guild, hunt, puzzle, report, apply=apply):
                    report.actions.append(action)
                for action in await self._reconcile_tabs(hunt, puzzle, apply=apply):
                    report.actions.append(action)
            except Exception as error:  # noqa: BLE001 — isolate per puzzle, keep going
                logger.exception("reconcile: failed for puzzle %s", puzzle.name)
                report.add_failure(puzzle.name, error)

        rounds = RoundJsonDb.get_all(hunt.id)
        for hunt_round in rounds:
            if hunt_round.category_id and discord.utils.get(
                    guild.categories, id=hunt_round.category_id) is None:
                report.add("round_category_broken",
                           getattr(hunt_round, "meta_code", None) or str(hunt_round.id),
                           f"category {hunt_round.category_id} missing")

        self._collect_orphan_channels(guild, rounds, puzzles, report)
        self._collect_orphan_tabs(hunt, puzzles, report)
        return report

    def _collect_orphan_channels(self, guild, rounds, puzzles, report):
        valid_ids = {p.channel_id for p in puzzles if p.channel_id}
        category_ids = {r.category_id for r in rounds if r.category_id}
        for category in guild.categories:
            if category.id not in category_ids:
                continue
            for channel in getattr(category, "channels", []):
                if getattr(channel, "type", "text") != "text":
                    continue
                if channel.id in valid_ids:
                    continue
                if "———" in getattr(channel, "name", ""):  # solved-divider marker
                    continue
                report.add_orphan_channel(channel.id, channel.name)

    def _collect_orphan_tabs(self, hunt, puzzles, report):
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return
        spreadsheet = gsheet_cog.get_spreadsheet_id()
        valid = {str(p.google_page_id) for p in puzzles if p.google_page_id and not p.archived}
        try:
            sheets = gsheet_cog.sheet_list(spreadsheet)["sheets"]
        except Exception:  # noqa: BLE001 — orphan scan is best-effort
            logger.exception("reconcile: could not list tabs for orphan scan")
            return
        for sheet in sheets:
            props = sheet.get("properties", {})
            if props.get("hidden"):           # template/system tabs are hidden
                continue
            sheet_id = props.get("sheetId")
            if str(sheet_id) in valid:
                continue
            report.add_orphan_tab(sheet_id, props.get("title", str(sheet_id)))
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_reconcile.py
git commit -m "reconcile: hunt-wide drive + round-category + orphan collection"
```

---

### Task 5: Orphan deletion (called only after confirmation)

**Files:**
- Modify: `bot/core/puzzle_service.py` (add `delete_orphans`)
- Test: `tests/test_reconcile.py`

`delete_orphans(guild, hunt, report)` deletes the channels/tabs already recorded in
the report. Tolerant of already-missing items. Returns `(deleted, failures)` counts.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_reconcile.py
from bot.core.reconcile import ReconcileReport


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: ... 'delete_orphans'`.

- [ ] **Step 3a: Check whether the gsheet cog has a delete-tab method**

Run: `grep -n "def delete_sheet_tab\|deleteSheet\|def delete_tab\|def delete_sheet" bot/cogs/puzzles_gsheet.py`

If a tab-delete method exists, use its exact name in Step 3b instead of `delete_sheet_tab`. If none exists, add this minimal helper to `GoogleSheets` (in `bot/cogs/puzzles_gsheet.py`), then use it:

```python
    async def delete_sheet_tab(self, sheet_id, spreadsheet_id=None):
        """Delete a tab by sheetId via batchUpdate. Tolerates an already-gone tab."""
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        body = {"requests": [{"deleteSheet": {"sheetId": int(sheet_id)}}]}
        req = get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        await execute_with_retry(req, on_retry=self._notify_retry)
```

- [ ] **Step 3b: Implement `delete_orphans`**

```python
    async def delete_orphans(self, guild, hunt, report):
        """Delete the orphan channels/tabs recorded in `report`. Tolerant of
        already-missing items. Returns (deleted_count, failure_count)."""
        deleted = 0
        failures = 0
        for channel_id, name in report.orphan_channels:
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            try:
                await channel.delete(reason="reconcile: orphan channel (no DB puzzle)")
                deleted += 1
            except Exception:  # noqa: BLE001
                logger.exception("reconcile: failed to delete orphan channel %s", channel_id)
                failures += 1

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            spreadsheet = gsheet_cog.get_spreadsheet_id()
            for sheet_id, title in report.orphan_tabs:
                try:
                    await gsheet_cog.delete_sheet_tab(sheet_id, spreadsheet)
                    deleted += 1
                except Exception:  # noqa: BLE001
                    logger.exception("reconcile: failed to delete orphan tab %s", sheet_id)
                    failures += 1
        return deleted, failures
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py bot/cogs/puzzles_gsheet.py tests/test_reconcile.py
git commit -m "reconcile: orphan deletion (post-confirmation)"
```

---

### Task 6: Cog command `!reconcile` + report formatter

**Files:**
- Modify: `bot/cogs/puzzles.py` (add `reconcile` command + `_format_reconcile_report`)
- Test: `tests/test_reconcile.py`

Argument grammar: `!reconcile` (report) · `!reconcile apply` (apply non-destructive)
· `!reconcile apply orphans` (apply + confirm-gated orphan delete). The formatter is
pure and unit-tested; the command body follows `!rebuild`'s structure.

- [ ] **Step 1: Write the failing test (formatter only)**

```python
# add to tests/test_reconcile.py
from bot.cogs.puzzles import Puzzles
from bot.core.reconcile import ReconcileReport


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py::test_format_report_summarises_counts_and_orphans -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: ... '_format_reconcile_report'`.

- [ ] **Step 3: Implement formatter + command**

Add a `staticmethod` formatter and the command to the `Puzzles` cog in
`bot/cogs/puzzles.py` (near `rebuild`, ~line 655). Ensure `ConfirmView` is imported
(it already is — used at line 2631) and `ORGANISER_ROLES` is in scope (used by
`rebuild`).

```python
    @staticmethod
    def _format_reconcile_report(report, *, applied):
        verb = "Applied" if applied else "Would apply"
        lines = [f"**Reconcile — {verb.lower()}:**"]
        counts = report.counts()
        if counts:
            lines += [f"- {kind}: {n}" for kind, n in sorted(counts.items())]
        else:
            lines.append("- no channel/tab changes needed")
        if report.orphan_channels:
            lines.append(f":wastebasket: {len(report.orphan_channels)} orphan channel"
                         f"{'s' if len(report.orphan_channels) != 1 else ''} "
                         "(not deleted unless you run `!reconcile apply orphans`)")
        if report.orphan_tabs:
            lines.append(f":wastebasket: {len(report.orphan_tabs)} orphan tab"
                         f"{'s' if len(report.orphan_tabs) != 1 else ''} "
                         "(not deleted unless you run `!reconcile apply orphans`)")
        if report.failures:
            lines.append(":warning: failures: "
                         + ", ".join(f"{t} ({e})" for t, e in report.failures))
        return "\n".join(lines)

    @commands.hybrid_command(description="Reconcile Discord + Sheets against the DB (organiser only)")
    @commands.has_any_role(*ORGANISER_ROLES)
    async def reconcile(self, ctx, mode: str = "", scope: str = ""):
        """*(Organiser) Heal Discord channels + Sheets tabs from the DB.
        `!reconcile` reports; `!reconcile apply` fixes; `!reconcile apply orphans`
        also deletes orphans (with confirmation).*"""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        apply = mode.lower() == "apply"
        want_orphans = scope.lower() == "orphans"

        report = await self.service.reconcile(ctx.guild, hunt, apply=apply)
        await ctx.send(self._format_reconcile_report(report, applied=apply))

        if apply and want_orphans and report.has_orphans():
            desc = (f"Delete {len(report.orphan_channels)} channel(s) and "
                    f"{len(report.orphan_tabs)} tab(s) with no matching puzzle?")
            if await self._confirm_destructive(ctx, desc):
                deleted, failed = await self.service.delete_orphans(ctx.guild, hunt, report)
                msg = f":white_check_mark: Deleted {deleted} orphan(s)."
                if failed:
                    msg += f" :warning: {failed} could not be deleted."
                await ctx.send(msg)
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: PASS (all reconcile tests).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_reconcile.py
git commit -m "reconcile: !reconcile command + report formatter"
```

---

### Task 7: Idempotency / convergence test + full suite

**Files:**
- Test: `tests/test_reconcile.py`

- [ ] **Step 1: Write the convergence test**

```python
# add to tests/test_reconcile.py

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
```

- [ ] **Step 2: Run the full reconcile suite**

Run: `timeout 120 pipenv run python -m pytest tests/test_reconcile.py -q > /tmp/test.log 2>&1`
Expected: PASS (all).

- [ ] **Step 3: Run the broader puzzle/gsheet/create suites for regressions**

Run: `timeout 180 pipenv run python -m pytest tests/test_reconcile.py tests/test_create_service.py tests/test_gsheet_metadata.py tests/test_sheet_service.py tests/test_confirm_view.py tests/test_sheet_list_guard.py -q > /tmp/test.log 2>&1`
Expected: PASS (no regressions).

- [ ] **Step 4: Commit**

```bash
git add tests/test_reconcile.py
git commit -m "reconcile: idempotency/convergence test"
```

---

## Verification

- `timeout 180 pipenv run python -m pytest tests/ -q` (full suite — needs the local `puzzlebot_test` DB reachable) green, or at minimum the suites in Task 7 Step 3.
- Manual smoke (test Discord, `puzzlebot_test`): run `!reconcile` in a hunt channel → report only, no mutations. Break a puzzle (clear its `channel_id` in the DB) → `!reconcile apply` recreates the channel and reposts the welcome. Re-run `!reconcile apply` → reports no changes.

## Rollback / recovery notes

- Pure additive feature: new module `bot/core/reconcile.py`, new methods on
  `PuzzleService`, a new cog command, optional new `delete_sheet_tab` helper. Revert
  the Task commits to remove it; nothing existing is modified destructively.
- `!reconcile` (no args) is always safe (read-only). Destructive behaviour is gated
  behind `apply orphans` + `ConfirmView`.

## Self-review notes

- **Spec coverage:** missing artifacts (Tasks 2,3), stale attributes (Task 2 move/rename; tab rename noted), broken ID links (Task 2 step 1/2, Task 3 page check), orphans report + opt-in delete (Tasks 4,5,6), report-then-confirm (Task 6), one-channel-in-`tags[0]` rule (Task 2 `_home_category`), round category report-only (Task 4), meta roll-ups delegated to `!rebuild` (not re-implemented). Additional-sheet recreation is explicitly deferred (Task 3 note) — the only spec item not shipped; flagged, not silently dropped.
- **Known heuristics to validate during execution:** orphan-channel exclusion uses the `———` solved-divider marker; orphan-tab exclusion uses the `hidden` property. Confirm these match the codebase's actual conventions when implementing Task 4 (grep for the divider string and template-sheet setup), and adjust the predicate if needed.
