# delete_round full-delete (`keep_puzzles=False`) fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix review findings #29 and #30 in `docs/reviews/2026-07-02-staging-full-review.md`: `delete_round`'s full-delete path (`keep_puzzles=False`, still used by `!delete_round` and the web's "Round + all puzzles" option) can (a) orphan a nested meta's `target_puzzle_id` by deleting that meta's target puzzle as an ordinary member puzzle, and (b) leave stale contribution rows on other metas' sheets because deleted feeder puzzles never trigger `refresh_targets`.

**Architecture:** `bot/core/puzzle_service.py`'s `delete_round` already solves both problems correctly for the `keep_puzzles=True` (narrow) path — it only ever deletes the round's own target puzzle and relocates everything else. Extend the same protection to the full-delete path: exclude any member puzzle that is a *different* tag group's target from `to_delete` (relocate it like narrow mode does), and after the deletion loop, call the existing `refresh_targets` helper (used by `delete_puzzle`) on every *other* tag group that a deleted puzzle was tagged into.

**Tech Stack:** Python, pytest + pytest-asyncio, unittest.mock (`AsyncMock`/`MagicMock`), existing `tests/conftest.py` builders (`make_puzzle`, `make_round`, `make_hunt`).

---

## File Structure

- Modify: `bot/core/puzzle_service.py` — `delete_round` (~line 1163) and its docstring.
- Modify: `tests/test_delete_round_keep_puzzles.py` — despite the filename this file already holds full-delete-path tests too (see `test_delete_round_uses_layout_teardown_for_split`-style patterns in `tests/test_create_service.py`); add new tests here since it already has the right mocking scaffolding (`_svc`, `_general_category`) for `delete_round`.
- Read only: `docs/reviews/2026-07-02-staging-full-review.md` (findings #29, #30) — update their status note in Task 5.

---

### Task 1: Failing test — full delete must not orphan a nested meta's target puzzle (#30)

**Files:**
- Test: `tests/test_delete_round_keep_puzzles.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_delete_round_keep_puzzles.py`:

```python
@pytest.mark.asyncio
async def test_full_delete_protects_foreign_target_puzzle():
    """keep_puzzles=False deletes every ordinary member puzzle, but a member
    puzzle that is itself a DIFFERENT tag group's target (a nested meta whose
    target puzzle also feeds this round) must be relocated, not deleted —
    deleting it would orphan the nested group's target_puzzle_id (review #30)."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    ordinary = make_puzzle(id=2, name="Ordinary", channel_id=902, tags=[5])
    nested_meta_target = make_puzzle(
        id=3, name="Nested Meta", is_target=True, channel_id=903, tags=[5])

    ordinary_ch = MagicMock(id=902); ordinary_ch.delete = AsyncMock()
    nested_ch = MagicMock(id=903); nested_ch.category_id = 100; nested_ch.edit = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [ordinary_ch, nested_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(
        side_effect=lambda cid: {902: ordinary_ch, 903: nested_ch}.get(cid))

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [ordinary, nested_meta_target]
        tgcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=False)

    pdb.delete.assert_called_once_with(2)                                        # only the ordinary puzzle's DB row
    ordinary_ch.delete.assert_awaited_once()                                     # ordinary puzzle: deleted
    nested_ch.edit.assert_awaited_once_with(category=general_cat, position=0)    # nested meta target: relocated
    nested_ch.delete.assert_not_called()
    assert failed == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 60 python -m pytest tests/test_delete_round_keep_puzzles.py::test_full_delete_protects_foreign_target_puzzle -v > /tmp/test-output.log 2>&1`

Expected: FAIL — `pdb.delete` is called with both 2 and 3 (current code has no guard), so `assert_called_once_with(2)` fails.

- [ ] **Step 3: Commit the test**

```bash
git add tests/test_delete_round_keep_puzzles.py
git commit -m "test(delete-round): add failing test for foreign-target orphan bug (#30)"
```

---

### Task 2: Implement the #30 guard

**Files:**
- Modify: `bot/core/puzzle_service.py:1163-1183` (the `to_delete`/`to_keep` computation and the relocation gate)

- [ ] **Step 1: Replace the to_delete/to_keep computation**

Current code (`bot/core/puzzle_service.py`, inside `delete_round`):

```python
        narrow = keep_puzzles and hunt.layout_mode != "forum"
        all_puzzles = PuzzleJsonDb.get_all_from_tag_group(tag_group.id)

        if narrow:
            target_puzzle = next(
                (p for p in all_puzzles
                 if tag_group.target_puzzle_id and p.id == tag_group.target_puzzle_id),
                None,
            )
            to_delete = [target_puzzle] if target_puzzle else []
            to_keep = [p for p in all_puzzles if p is not target_puzzle]
        else:
            to_delete = all_puzzles
            to_keep = []
```

Replace with:

```python
        narrow = keep_puzzles and hunt.layout_mode != "forum"
        all_puzzles = PuzzleJsonDb.get_all_from_tag_group(tag_group.id)

        if narrow:
            target_puzzle = next(
                (p for p in all_puzzles
                 if tag_group.target_puzzle_id and p.id == tag_group.target_puzzle_id),
                None,
            )
            to_delete = [target_puzzle] if target_puzzle else []
            to_keep = [p for p in all_puzzles if p is not target_puzzle]
        else:
            # A member puzzle that is itself a DIFFERENT tag group's target (a
            # nested meta whose target puzzle also happens to feed this round)
            # must never be deleted here: that would orphan the nested group's
            # target_puzzle_id, the same case delete_puzzle forbids outright.
            # Only this round's own target (if any) is fair game for full delete.
            to_delete = [
                p for p in all_puzzles
                if not p.is_target or p.id == tag_group.target_puzzle_id
            ]
            to_keep = [p for p in all_puzzles if p not in to_delete]
```

- [ ] **Step 2: Widen the relocation gate to cover full-delete protected puzzles**

Current code a few lines down:

```python
        relocation_failed_channel_ids = set()
        if narrow and guild is not None:
```

Replace with:

```python
        relocation_failed_channel_ids = set()
        if to_keep and guild is not None:
```

(`to_keep` is only ever non-empty in narrow mode or when the new guard above kept a foreign target puzzle, so this widens the existing relocation logic to both cases without changing narrow-mode behavior.)

- [ ] **Step 3: Run the new test to verify it passes**

Run: `timeout 60 python -m pytest tests/test_delete_round_keep_puzzles.py::test_full_delete_protects_foreign_target_puzzle -v > /tmp/test-output.log 2>&1`

Expected: PASS

- [ ] **Step 4: Run the full delete_round test files to check for regressions**

Run: `timeout 120 python -m pytest tests/test_delete_round_keep_puzzles.py tests/test_delete_round_web.py tests/test_group_type_consolidation.py tests/test_create_service.py tests/test_forum_layout.py --compact > /tmp/test-output.log 2>&1`

Expected: PASS (all tests, no regressions)

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py
git commit -m "fix(delete-round): protect nested meta target puzzles in full delete (#30)"
```

---

### Task 3: Failing test — full delete must refresh other tag groups fed by deleted puzzles (#29)

**Files:**
- Test: `tests/test_delete_round_keep_puzzles.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_delete_round_keep_puzzles.py`:

```python
@pytest.mark.asyncio
async def test_full_delete_refreshes_other_fed_targets():
    """keep_puzzles=False deletes a feeder puzzle that also feeds ANOTHER
    round's meta; that other meta's target must be refreshed so its
    contribution table doesn't keep a stale row for the deleted puzzle
    (review #29)."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)
    other_group = make_round(id=9, category_id=200, target_puzzle_id=0)

    # Feeder is tagged into both this round (5, being deleted) and another
    # round (9, NOT being deleted) — deleting it must refresh group 9.
    feeder = make_puzzle(id=2, name="Feeder", channel_id=902, tags=[5, 9])
    feeder_ch = MagicMock(id=902); feeder_ch.delete = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = [round_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "refresh_targets", AsyncMock(return_value=[])) as refresh_mock, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []
        tgdb.get_by_attr.side_effect = lambda id: other_group if id == 9 else tag_group

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=False)

    refresh_mock.assert_awaited_once()
    refreshed_ids = {g.id for g in refresh_mock.await_args.args[1]}
    assert refreshed_ids == {9}          # group 5 (being deleted) is excluded
    assert failed == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 60 python -m pytest tests/test_delete_round_keep_puzzles.py::test_full_delete_refreshes_other_fed_targets -v > /tmp/test-output.log 2>&1`

Expected: FAIL — `refresh_mock.assert_awaited_once()` fails because `delete_round` never calls `refresh_targets`.

- [ ] **Step 3: Commit the test**

```bash
git add tests/test_delete_round_keep_puzzles.py
git commit -m "test(delete-round): add failing test for missing refresh_targets on full delete (#29)"
```

---

### Task 4: Implement the #29 refresh_targets call

**Files:**
- Modify: `bot/core/puzzle_service.py` — the `to_delete` deletion loop inside `delete_round`

- [ ] **Step 1: Collect fed-into tag group ids while deleting, then refresh them**

Current deletion loop:

```python
        failed_channels = []
        for puzzle in to_delete:
            gsheet_cog = self._gsheet_cog(hunt)
            if gsheet_cog is not None:
                try:
                    await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
                except Exception:
                    logger.exception("delete_round: sheet delete failed for puzzle %s", puzzle.id)
            PuzzleJsonDb.delete(puzzle.id)
            if guild is not None and puzzle.channel_id:
                if not await self._safe_delete_channel(guild, puzzle.channel_id):
                    failed_channels.append(puzzle.name)
```

Replace with:

```python
        failed_channels = []
        other_fed_group_ids = set()
        for puzzle in to_delete:
            gsheet_cog = self._gsheet_cog(hunt)
            if gsheet_cog is not None:
                try:
                    await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
                except Exception:
                    logger.exception("delete_round: sheet delete failed for puzzle %s", puzzle.id)
            other_fed_group_ids.update(tag for tag in puzzle.tags if tag != tag_group.id)
            PuzzleJsonDb.delete(puzzle.id)
            if guild is not None and puzzle.channel_id:
                if not await self._safe_delete_channel(guild, puzzle.channel_id):
                    failed_channels.append(puzzle.name)

        if other_fed_group_ids:
            other_target_groups = [
                TagGroupJsonDb.get_by_attr(id=gid) for gid in other_fed_group_ids
            ]
            failed_refresh = await self.refresh_targets(hunt, other_target_groups)
            if failed_refresh:
                logger.error(
                    "delete_round: failed to refresh target groups %s after deleting "
                    "member puzzles of group %s", failed_refresh, tag_group.id)
```

- [ ] **Step 2: Run both new tests to verify they pass**

Run: `timeout 60 python -m pytest tests/test_delete_round_keep_puzzles.py::test_full_delete_refreshes_other_fed_targets tests/test_delete_round_keep_puzzles.py::test_full_delete_protects_foreign_target_puzzle -v > /tmp/test-output.log 2>&1`

Expected: PASS (both)

- [ ] **Step 3: Run the full delete_round-adjacent test files to check for regressions**

Run: `timeout 120 python -m pytest tests/test_delete_round_keep_puzzles.py tests/test_delete_round_web.py tests/test_group_type_consolidation.py tests/test_create_service.py tests/test_forum_layout.py tests/test_solve_equivalence.py --compact > /tmp/test-output.log 2>&1`

Expected: PASS (all tests, no regressions)

- [ ] **Step 4: Commit**

```bash
git add bot/core/puzzle_service.py
git commit -m "fix(delete-round): refresh other fed targets on full delete (#29)"
```

---

### Task 5: Update delete_round's docstring and the review doc's status notes

**Files:**
- Modify: `bot/core/puzzle_service.py` — `delete_round` docstring
- Modify: `docs/reviews/2026-07-02-staging-full-review.md` — findings #29 and #30

- [ ] **Step 1: Add a short docstring note**

In `delete_round`'s docstring, after the existing `keep_puzzles` paragraph, add:

```
        Full delete (keep_puzzles=False) still protects any member puzzle that
        is a DIFFERENT tag group's target (relocated instead of deleted, same
        as narrow mode) and refreshes every other tag group fed by a deleted
        puzzle, so neither path can orphan a nested meta's target_puzzle_id
        or leave a stale contribution row on an unrelated meta's sheet.
```

- [ ] **Step 2: Update finding #29's status note**

In `docs/reviews/2026-07-02-staging-full-review.md`, find the `### 29.` section's `**Status: PARTIALLY MITIGATED**` paragraph. Append a new paragraph:

```

**Status update (2026-07-02):** fully fixed. `delete_round`'s full-delete
path (`keep_puzzles=False`) now collects every OTHER tag group fed by a
deleted member puzzle and calls `refresh_targets` on them after the deletion
loop, closing the gap described above for both `!delete_round` and the web's
"Round + all puzzles" option.
```

- [ ] **Step 3: Update finding #30's status note**

In the same file, find the `### 30.` section's `**Status: PARTIALLY MITIGATED**` paragraph. Append a new paragraph:

```

**Status update (2026-07-02):** fully fixed. `delete_round`'s full-delete
path now excludes any member puzzle that is a DIFFERENT tag group's target
from deletion (relocating it instead, same as narrow mode), so the
failure scenario above can no longer happen via either `!delete_round` or
the web's "Round + all puzzles" option.
```

- [ ] **Step 4: Commit**

```bash
git add bot/core/puzzle_service.py docs/reviews/2026-07-02-staging-full-review.md
git commit -m "docs(delete-round): note #29/#30 fully fixed"
```

---

## Verification

- [ ] Run the full delete-round-adjacent suite once more end to end:

Run: `timeout 120 python -m pytest tests/test_delete_round_keep_puzzles.py tests/test_delete_round_web.py tests/test_group_type_consolidation.py tests/test_create_service.py tests/test_forum_layout.py tests/test_solve_equivalence.py tests/test_board_push_service.py --compact > /tmp/test-output.log 2>&1`

Expected: all PASS.

## Rollback / recovery notes

Each task is its own commit; if a later task's change is wrong, `git revert` that commit alone — Tasks 2 and 4 touch disjoint code regions of the same function so they should not conflict with each other on revert.
