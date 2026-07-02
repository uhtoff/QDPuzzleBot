# delete_hunt split-round category teardown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining Discord-object half of review finding #32 (`docs/reviews/2026-07-02-staging-full-review.md`): `delete_hunt`'s per-group teardown in `bot/core/puzzle_service.py` only ever deletes a group's single `category_id` category, never consulting `TagGroupCategoryDb` — so a split round (one that outgrew a single category into separate unsolved/solved/overflow categories) leaves its extra categories standing as empty, orphaned Discord objects after `!delete_hunt`. (The `tag_group_category` DB-row half of #32, and all of #42/#55, are already fixed by the FK-cascade migration `migrations/022_delete_cascades.sql`, committed separately — this plan is Discord-object cleanup only, since a DB migration can't reach live Discord API objects.)

**Architecture:** `delete_round` (same file) already solves the identical problem via `category_layout.delete_group_categories(guild, tag_group, TagGroupCategoryDb)` — it deletes every category a split group owns (with any leftover channels) and drops the DB join rows, returning `False` for a non-split (single-category) group so the caller falls back to its legacy single-category teardown. `delete_hunt`'s non-forum branch just needs the same call inserted in the same guarded pattern. Forum-mode groups are structurally exempt (their `category_id` is a channel, never resolves as a category — same reasoning already established for `delete_round`'s forum branch and `convert_to_puzzle`'s Metapuzzle rounds), so the forum branch is untouched.

**Tech Stack:** Python, pytest + pytest-asyncio, unittest.mock (`AsyncMock`/`MagicMock`), existing `tests/conftest.py` builders (`make_hunt`, `make_round`).

---

## File Structure

- Modify: `bot/core/puzzle_service.py` — `delete_hunt`'s non-forum per-group teardown branch (~line 2321-2337).
- Modify: `tests/test_delete_hunt_service.py` — add a split-round regression test; update existing tests' mocking to patch `TagGroupCategoryDb` and `category_layout.delete_group_categories` (they currently don't, since the code path never touched either).
- Read only: `docs/reviews/2026-07-02-staging-full-review.md` (finding #32) — update its status note in the final task.

---

### Task 1: Update existing delete_hunt tests to mock the new call (no behavior change yet)

**Files:**
- Test: `tests/test_delete_hunt_service.py`

- [ ] **Step 1: Read the current file in full**

Read `tests/test_delete_hunt_service.py` end to end (it's ~103 lines) to see every test's exact mocking setup before editing.

- [ ] **Step 2: Add `TagGroupCategoryDb` and `category_layout.delete_group_categories` mocks to every test that currently patches `TagGroupJsonDb`**

For each `with patch(...)` block in the file that includes `patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb` (or similar), add two more patches to the same `with`:

```python
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)) as delete_group_cats, \
```

and inside the `with` block body, set:

```python
        tgcdb.get_for_group.return_value = []
```

`AsyncMock(return_value=False)` makes `delete_group_categories` behave exactly like today's "non-split group, do nothing" case, so every existing assertion in the file (about the legacy divider/category-delete path) keeps passing unchanged — this step only adds mocking, no behavior changes yet.

This applies to (at minimum, verify against the actual file which tests currently construct a `guild`/`category` and reach the group-teardown branch):
- `test_delete_hunt_deletes_db_row_and_spreadsheet_files`
- `test_delete_hunt_returns_failed_channels`
- `test_delete_hunt_deletes_group_chat_channel`
- any other test in the file that reaches the per-group Discord teardown branch (check by reading the file — a test that passes `rdb.get_all.return_value = []` with an empty list never enters the loop and needs no changes)

- [ ] **Step 3: Run the file's tests to confirm nothing broke**

Run: `timeout 60 pipenv run python -m pytest tests/test_delete_hunt_service.py -v > /tmp/test-output.log 2>&1` from `/home/russ/projects/puzzbot/.claude/worktrees/working-2` (use plain `python -m pytest` if `pipenv` isn't set up — check `AGENTS.md` for the correct invocation)

Expected: PASS (all existing tests, unchanged behavior)

- [ ] **Step 4: Commit**

```bash
git add tests/test_delete_hunt_service.py
git commit -m "test(delete-hunt): mock TagGroupCategoryDb/delete_group_categories ahead of split-teardown fix"
```

---

### Task 2: Failing test — delete_hunt must tear down a split round's extra categories

**Files:**
- Test: `tests/test_delete_hunt_service.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_delete_hunt_service.py` (reuse whatever `_delete_service`/`_delete_guild`/`_delete_hunt_obj` helpers the file already defines at its top — read them first to match their exact signatures):

```python
@pytest.mark.asyncio
async def test_delete_hunt_tears_down_split_round_categories():
    """A split round (one that outgrew a single category into separate
    unsolved/solved/overflow categories) must have ALL its categories deleted
    by delete_hunt, not just tag_group.category_id — mirrors delete_round's
    existing use of category_layout.delete_group_categories (review #32)."""
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_hunt_spreadsheet = AsyncMock()

    group = MagicMock(id=9, category_id=100)
    guild.categories = []

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=True)) as delete_group_cats, \
         patch("bot.core.puzzle_service.category_layout.group_chat_channel",
               return_value=None):
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = [group]

        await svc.delete_hunt(guild, hunt)

    delete_group_cats.assert_awaited_once_with(guild, group, tgcdb)
```

The exact keyword/positional form of the `assert_awaited_once_with` call must match how you invoke it in Task 3's implementation — if you choose to pass `TagGroupCategoryDb` as a keyword argument instead of positional, adjust this assertion to match (see Task 3, which specifies the exact call to make — keep this test's assertion consistent with it).

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 60 pipenv run python -m pytest tests/test_delete_hunt_service.py::test_delete_hunt_tears_down_split_round_categories -v > /tmp/test-output.log 2>&1`

Expected: FAIL — `delete_group_cats.assert_awaited_once_with(...)` fails because `delete_hunt` currently never calls `category_layout.delete_group_categories` at all.

- [ ] **Step 3: Commit the test**

```bash
git add tests/test_delete_hunt_service.py
git commit -m "test(delete-hunt): add failing test for split-round category teardown (#32)"
```

---

### Task 3: Implement the fix

**Files:**
- Modify: `bot/core/puzzle_service.py` — `delete_hunt`'s non-forum branch

- [ ] **Step 1: Replace the non-forum branch**

Find this exact current code in `delete_hunt` (`bot/core/puzzle_service.py`):

```python
                else:
                    group_chat_ch = category_layout.group_chat_channel(guild, tag_group)
                    if group_chat_ch is not None:
                        await self._safe_delete_channel(guild, group_chat_ch.id)
                    category = discord.utils.get(guild.categories, id=tag_group.category_id)
                    if category is not None:
                        for ch in list(category.channels):
                            if getattr(ch, "name", "") == self._SOLVE_DIVIDER_NAME:
                                try:
                                    await ch.delete(reason=self._DISCORD_DELETE_REASON)
                                except Exception:
                                    logger.exception("delete_hunt: could not delete divider channel")
                        try:
                            await category.delete(reason=self._DISCORD_DELETE_REASON)
                        except Exception:
                            logger.exception("delete_hunt: could not delete group category %s",
                                             tag_group.category_id)
```

Replace with:

```python
                else:
                    group_chat_ch = category_layout.group_chat_channel(guild, tag_group)
                    if group_chat_ch is not None:
                        await self._safe_delete_channel(guild, group_chat_ch.id)
                    # Split rounds own several categories (unsolved/solved/overflow);
                    # delete_group_categories tears down all of them + the DB join
                    # rows (also FK-cascaded now, but Discord objects still need an
                    # explicit API call). Returns False for single-category groups,
                    # which keep the legacy divider+category teardown below.
                    if not await category_layout.delete_group_categories(
                        guild, tag_group, TagGroupCategoryDb,
                    ):
                        category = discord.utils.get(guild.categories, id=tag_group.category_id)
                        if category is not None:
                            for ch in list(category.channels):
                                if getattr(ch, "name", "") == self._SOLVE_DIVIDER_NAME:
                                    try:
                                        await ch.delete(reason=self._DISCORD_DELETE_REASON)
                                    except Exception:
                                        logger.exception("delete_hunt: could not delete divider channel")
                            try:
                                await category.delete(reason=self._DISCORD_DELETE_REASON)
                            except Exception:
                                logger.exception("delete_hunt: could not delete group category %s",
                                                 tag_group.category_id)
```

This calls `category_layout.delete_group_categories(guild, tag_group, TagGroupCategoryDb)` — three positional args, no `keep_channel_ids` (delete_hunt has no relocation concept to protect, unlike `delete_round`). Make sure Task 2's test assertion (`delete_group_cats.assert_awaited_once_with(guild, group, tgcdb)`) matches this exact positional call — if you wrote it differently, go back and align the test.

- [ ] **Step 2: Run the new test and the full file to verify**

Run: `timeout 60 pipenv run python -m pytest tests/test_delete_hunt_service.py -v > /tmp/test-output.log 2>&1`

Expected: PASS (all tests in the file, including the new split-round test)

- [ ] **Step 3: Run the broader delete-adjacent regression suite**

Run: `timeout 120 pipenv run python -m pytest tests/test_delete_hunt_service.py tests/test_delete_safety.py tests/test_delete_round_keep_puzzles.py tests/test_delete_round_web.py tests/test_group_type_consolidation.py tests/test_create_service.py tests/test_forum_layout.py -q > /tmp/test-output.log 2>&1`

Expected: PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add bot/core/puzzle_service.py
git commit -m "fix(delete-hunt): tear down split round's extra Discord categories (#32)"
```

---

### Task 4: Update the review doc's status notes for #32, #42, #55

**Files:**
- Modify: `docs/reviews/2026-07-02-staging-full-review.md` — findings #32, #42, #55

- [ ] **Step 1: Update finding #32's status**

Find `### 32.` in `docs/reviews/2026-07-02-staging-full-review.md`. Append this paragraph at the end of that finding's section:

```

**Status update (2026-07-02):** fully fixed. `migrations/022_delete_cascades.sql`
added a FK from `tag_group_category.group_id` to `tag_group.id` (ON DELETE
CASCADE), closing the DB-row leak automatically for every delete path,
present and future. `delete_hunt` was separately updated to route its
per-group Discord teardown through `category_layout.delete_group_categories`
(mirroring `delete_round`'s existing pattern), so a split round's extra
solved/overflow categories are now actually deleted rather than left
standing as empty orphans. `convert_to_puzzle` needed no code change: the
Metapuzzle-type rounds it deletes are structurally incapable of ever owning
split categories (their `category_id` is a puzzle channel id, which
`category_layout.split_group`'s category-only lookup never resolves), so
this leak was already unreachable there — the FK simply makes that
guarantee airtight against future drift.
```

- [ ] **Step 2: Update finding #42's status**

Find `### 42.` in the same file. Append:

```

**Status update (2026-07-02):** fully fixed via
`migrations/022_delete_cascades.sql`, which adds FKs from
`evidence_items.source_puzzle_id` and `.hunt_id` (ON DELETE CASCADE) and
`.target_puzzle_id` (ON DELETE SET NULL) to `puzzles`/`hunts`. Every puzzle
or hunt delete now cascades its evidence automatically at the database
level, regardless of which code path (bang, web, future additions) performs
it — no application-code change needed. Pre-existing orphans (14 of 20 rows
on this DB) were purged as part of applying the migration.
```

- [ ] **Step 3: Update finding #55's status**

Find `### 55.` in the same file. Append:

```

**Status update (2026-07-02):** fully fixed via
`migrations/022_delete_cascades.sql`, which adds a FK from
`additional_sheets.puzzle_id` to `puzzles.id` (ON DELETE CASCADE). Every
puzzle delete now cascades its additional sheets automatically at the
database level, regardless of which code path performs it — no
application-code change needed. Pre-existing orphans (101 of 118 rows on
this DB) were purged as part of applying the migration.
```

- [ ] **Step 4: Verify and commit**

Run `git diff --stat` to confirm only `docs/reviews/2026-07-02-staging-full-review.md` changed. Then:

```bash
git add docs/reviews/2026-07-02-staging-full-review.md
git commit -m "docs: note #32/#42/#55 fully fixed"
```

---

## Verification

- [ ] Run the full delete-adjacent suite once more end to end:

Run: `timeout 120 pipenv run python -m pytest tests/test_delete_hunt_service.py tests/test_delete_safety.py tests/test_delete_round_keep_puzzles.py tests/test_delete_round_web.py tests/test_group_type_consolidation.py tests/test_create_service.py tests/test_forum_layout.py tests/test_evidence_service.py tests/test_sheet_service.py --compact > /tmp/test-output.log 2>&1`

Expected: all PASS.

## Rollback / recovery notes

The FK migration (`migrations/022_delete_cascades.sql`) is already committed and applied separately from this plan's tasks — if any task here needs to be reverted, `git revert` it individually; none of these tasks touch the migration file. If the migration itself ever needs rolling back, note it purged real orphan rows (not reversible) before adding the FKs — a down-migration would only need to drop the constraints and widen `tag_group_category.group_id` back to `bigint(20) unsigned`, not restore the purged data.
