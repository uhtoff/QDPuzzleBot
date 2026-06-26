# Category Overflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `tag_group` automatically span multiple Discord categories when it grows past a soft limit — first splitting its single divider-organised category into separate unsolved/solved categories, then spilling into numbered overflow categories — so creates never fail at Discord's 50-channels-per-category cap.

**Architecture:** A new join table `tag_group_category` records every category a group owns and its role (`unsolved`/`solved`) + `seq`. A new pure-ish module `bot/core/category_layout.py` owns all placement, allocation, split, cleanup, and teardown decisions against that table and live Discord state. `create_puzzle` (service), `web_post_solved_embed` / unsolve (cog), and round deletion (cog) call into this module instead of touching a single `category_id`. Small rounds (under the soft limit, no join rows) behave exactly as today (single category + `———solved———` divider).

**Tech Stack:** Python 3, `discord.py`, MySQL (raw `mysql.connector` DAOs), `pytest` + `pytest-asyncio`, `unittest.mock`.

**Design source:** `docs/superpowers/specs/2026-06-27-category-overflow-design.md`

**Conventions for the worker:**
- Run a single focused test with: `timeout 120 pipenv run python -m pytest tests/<file>::<test> -q > /tmp/test-output.log 2>&1` then read the log. (Add `--compact`/`--parallel` per project test guidance for broader runs.)
- Lint with: `pipenv run ruff check bot/ tests/`
- In a worktree, all paths below are relative to the worktree root.
- The DAOs need a live MySQL connection, so they are NOT unit-tested directly (mirroring the existing suite, which patches `TagGroupJsonDb`/`PuzzleJsonDb`). All logic tests mock the DAO and the guild/category/channel objects with `MagicMock`/`AsyncMock`, exactly like `tests/test_create_service.py`.

---

## File structure

- **Create** `migrations/014_tag_group_category.sql` — the join table. (014 is the next free number; re-verify before merging — parallel worktrees can collide on migration numbers.)
- **Create** `bot/store/tag_group_category_data.py` — `TagGroupCategoryData` dataclass.
- **Modify** `bot/store/mysqldb.py` — add `MySQLTagGroupCategoryDb` DAO.
- **Modify** `bot/store/__init__.py` — wire the `TagGroupCategoryDb` singleton + import.
- **Create** `bot/core/category_layout.py` — placement/allocation/split/cleanup/teardown logic.
- **Modify** `bot/core/puzzle_service.py` — route `create_puzzle` placement through the module.
- **Modify** `bot/cogs/puzzles.py` — solve/unsolve repositioning + round-deletion teardown through the module.
- **Modify** `bot/web/actions.py` — call the unsolve reposition after `service.unsolve`.
- **Create** `tests/test_category_layout.py` — all module logic tests.
- **Modify** `tests/test_core_constants.py` — assert the new constant (if that file guards constants; otherwise the new test file covers it).

---

## Task 1: Schema + model + DAO + singleton wiring

**Files:**
- Create: `migrations/014_tag_group_category.sql`
- Create: `bot/store/tag_group_category_data.py`
- Modify: `bot/store/mysqldb.py:143-194` (alongside `MySQLTagGroupJsonDb`)
- Modify: `bot/store/__init__.py:8-12,79-98`
- Test: `tests/test_category_layout.py`

- [ ] **Step 1: Write the migration**

`migrations/014_tag_group_category.sql`:

```sql
-- 014_tag_group_category.sql
-- A tag_group can span multiple Discord categories once it grows past the
-- soft limit. This table records every category a group owns and its role.
-- Absence of rows for a group == legacy single-category (divider) layout.
CREATE TABLE IF NOT EXISTS `tag_group_category` (
  `id`          BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `group_id`    BIGINT(20) UNSIGNED NOT NULL,
  `category_id` BIGINT(20) UNSIGNED NOT NULL,
  `role`        VARCHAR(16) NOT NULL DEFAULT 'unsolved',  -- 'unsolved' | 'solved'
  `seq`         INT NOT NULL DEFAULT 0,                    -- 0,1,2… within a role
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_group_category` (`group_id`, `category_id`),
  KEY `idx_group` (`group_id`),
  KEY `idx_category` (`category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: Write the model**

`bot/store/tag_group_category_data.py`:

```python
from dataclasses import dataclass
from dataclasses_json import dataclass_json

ROLE_UNSOLVED = "unsolved"
ROLE_SOLVED = "solved"


@dataclass_json
@dataclass
class TagGroupCategoryData:
    """One Discord category owned by a tag_group, with its role and ordering.

    A group with no rows is in legacy 'single category + divider' layout.
    """
    id: int = 0
    group_id: int = 0
    category_id: int = 0
    role: str = ROLE_UNSOLVED   # 'unsolved' | 'solved'
    seq: int = 0
```

- [ ] **Step 3: Write a failing model test**

Add to a new file `tests/test_category_layout.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.store.tag_group_category_data import (
    TagGroupCategoryData, ROLE_UNSOLVED, ROLE_SOLVED,
)


def test_tag_group_category_defaults():
    row = TagGroupCategoryData(group_id=5, category_id=999)
    assert row.role == ROLE_UNSOLVED
    assert row.seq == 0
    assert row.id == 0


def test_role_constants():
    assert ROLE_UNSOLVED == "unsolved"
    assert ROLE_SOLVED == "solved"
```

- [ ] **Step 4: Run it**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1`
Expected: PASS (model already written in Steps 2).

- [ ] **Step 5: Add the DAO**

In `bot/store/mysqldb.py`, add the import near the top (with the other `from .X import Y` lines, ~line 11-16):

```python
from .tag_group_category_data import TagGroupCategoryData
```

Then add this class immediately after `MySQLTagGroupJsonDb` (after line 194):

```python
class MySQLTagGroupCategoryDb(_MySQLBaseDb):
    TABLE_NAME = 'tag_group_category'

    def get_for_group(self, group_id):
        """All category rows for a group, ordered by role then seq."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT * FROM tag_group_category WHERE group_id = %s "
                "ORDER BY role ASC, seq ASC",
                (group_id,),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [TagGroupCategoryData(**row) for row in rows]

    def get_by_category(self, category_id):
        """The single row owning a category, or None."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT * FROM tag_group_category WHERE category_id = %s LIMIT 1",
                (category_id,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
        return TagGroupCategoryData(**row) if row else None

    def delete_for_group(self, group_id):
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "DELETE FROM tag_group_category WHERE group_id = %s", (group_id,)
            )
        finally:
            cursor.close()
        self.mydb.commit()
```

Note: `commit(obj)` and `delete(id)` are inherited from `_MySQLBaseDb` and work as-is (the model's fields map 1:1 to columns; `id=0` triggers INSERT).

- [ ] **Step 6: Wire the singleton**

In `bot/store/__init__.py`, extend the mysqldb import (line 12) to include `MySQLTagGroupCategoryDb`:

```python
from .mysqldb import MySQLPuzzleJsonDb, MySQLGuildSettingsDb, MySQLTagGroupJsonDb, MySQLHuntJsonDb, MySQLAdditionalSheetsDb, MySQLChannelActivityDb, MySQLRollupJsonDb, MySQLEvidenceDb, MySQLTagGroupCategoryDb
```

And add the singleton next to `TagGroupJsonDb` (after line 81):

```python
    TagGroupCategoryDb = MySQLTagGroupCategoryDb(mydb=mydb)
```

> If `bot/store/__init__.py` has a non-mysql storage branch that constructs the other singletons too (search the file for a second `TagGroupJsonDb =`), add the matching `TagGroupCategoryDb = ...` there as well so both branches define it.

- [ ] **Step 7: Run the model test + lint**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1`
Expected: PASS.
Run: `pipenv run ruff check bot/store/ tests/test_category_layout.py`
Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add migrations/014_tag_group_category.sql bot/store/tag_group_category_data.py bot/store/mysqldb.py bot/store/__init__.py tests/test_category_layout.py
git commit -m "feat(categories): add tag_group_category table, model, DAO"
```

---

## Task 2: Naming helpers + state readers + soft limit

**Files:**
- Create: `bot/core/category_layout.py`
- Test: `tests/test_category_layout.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_category_layout.py`:

```python
from bot.core import category_layout as layout
from bot.store.tag_group_category_data import TagGroupCategoryData


def test_solved_category_name():
    assert layout.solved_category_name("round-1") == "round-1 — solved"


def test_overflow_category_name_unsolved():
    assert layout.overflow_category_name("round-1", "unsolved", 0) == "round-1"
    assert layout.overflow_category_name("round-1", "unsolved", 1) == "round-1 (2)"
    assert layout.overflow_category_name("round-1", "unsolved", 2) == "round-1 (3)"


def test_overflow_category_name_solved():
    assert layout.overflow_category_name("round-1", "solved", 0) == "round-1 — solved"
    assert layout.overflow_category_name("round-1", "solved", 1) == "round-1 — solved (2)"


def test_puzzle_count_excludes_divider():
    from bot.core.discord_channels import SOLVE_DIVIDER
    div = MagicMock(); div.name = SOLVE_DIVIDER
    a = MagicMock(); a.name = "puz-a"
    b = MagicMock(); b.name = "puz-b"
    cat = MagicMock(); cat.channels = [div, a, b]
    assert layout.puzzle_count(cat) == 2


def test_is_split_false_when_no_rows():
    dao = MagicMock()
    dao.get_for_group.return_value = []
    tag_group = MagicMock(id=5)
    assert layout.is_split(tag_group, dao) is False


def test_is_split_true_when_rows_present():
    dao = MagicMock()
    dao.get_for_group.return_value = [TagGroupCategoryData(group_id=5, category_id=9)]
    tag_group = MagicMock(id=5)
    assert layout.is_split(tag_group, dao) is True


def test_soft_limit_below_hard_cap():
    from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY
    assert layout.CATEGORY_SOFT_LIMIT < MAX_CHANNELS_PER_CATEGORY
```

- [ ] **Step 2: Run to verify failure**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: bot.core.category_layout`.

- [ ] **Step 3: Write the module skeleton**

`bot/core/category_layout.py`:

```python
"""Multi-category layout for a tag_group (round) that outgrows one Discord
category (50-channel cap). Owns placement, allocation, the single→split
transition, empty-overflow cleanup, and teardown.

A group with no `tag_group_category` rows is in the legacy single-category
layout (one category + the ———solved——— divider); callers keep today's
divider behaviour for it. Once a group splits, every category it owns has a
row recording its role ('unsolved'/'solved') and seq.
"""
import asyncio
import logging

import discord

from bot.core.discord_channels import (
    SOLVE_DIVIDER, DELETE_REASON, solve_divider_position, clean_name,
)
from bot.store.tag_group_category_data import (
    TagGroupCategoryData, ROLE_UNSOLVED, ROLE_SOLVED,
)

logger = logging.getLogger(__name__)

# Allocate/split when a category WOULD cross this, leaving headroom below the
# hard cap (MAX_CHANNELS_PER_CATEGORY = 50) so a create never fails mid-flight.
CATEGORY_SOFT_LIMIT = 45

# Serialises all category-structure mutations (create category, move channel
# between categories, delete category) this module performs.
layout_lock = asyncio.Lock()


# ----------------------------------------------------------------- naming
def solved_category_name(base_name):
    return f"{base_name} — solved"


def overflow_category_name(base_name, role, seq):
    name = solved_category_name(base_name) if role == ROLE_SOLVED else base_name
    if seq >= 1:
        name = f"{name} ({seq + 1})"
    return name


# ----------------------------------------------------------------- helpers
def puzzle_count(category):
    """Channels in a category, excluding the solved divider."""
    return sum(1 for ch in category.channels if ch.name != SOLVE_DIVIDER)


def _category_by_id(guild, category_id):
    return discord.utils.get(guild.categories, id=category_id) if category_id else None


def base_name(guild, tag_group):
    """The group's primary (unsolved seq=0) category name; fall back to the
    cleaned group name if the category has gone missing."""
    primary = _category_by_id(guild, tag_group.category_id)
    return primary.name if primary is not None else clean_name(tag_group.name)


# ----------------------------------------------------------------- state
def group_categories(tag_group, dao):
    return dao.get_for_group(tag_group.id)


def is_split(tag_group, dao):
    return len(group_categories(tag_group, dao)) > 0
```

- [ ] **Step 4: Run to verify pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
pipenv run ruff check bot/core/category_layout.py tests/test_category_layout.py
git add bot/core/category_layout.py tests/test_category_layout.py
git commit -m "feat(categories): category_layout naming + state helpers"
```

---

## Task 3: Split transition (single → unsolved + solved)

**Files:**
- Modify: `bot/core/category_layout.py`
- Test: `tests/test_category_layout.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_category_layout.py`:

```python
def _split_fixtures(divider_pos=2):
    """Build a guild + a primary category with 1 divider, 2 unsolved (pos<divider)
    and 1 solved (pos>divider) channel."""
    from bot.core.discord_channels import SOLVE_DIVIDER
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = divider_pos
    div.delete = AsyncMock()
    u1 = MagicMock(); u1.name = "u1"; u1.position = 0; u1.edit = AsyncMock()
    u2 = MagicMock(); u2.name = "u2"; u2.position = 1; u2.edit = AsyncMock()
    s1 = MagicMock(); s1.name = "s1"; s1.position = 3; s1.edit = AsyncMock()
    primary = MagicMock(id=100, position=5)
    primary.name = "round-1"
    primary.channels = [u1, div, u2, s1]
    solved_cat = MagicMock(id=200)
    solved_cat.edit = AsyncMock()
    primary.clone = AsyncMock(return_value=solved_cat)
    guild = MagicMock()
    guild.categories = [primary]
    return guild, primary, solved_cat, div, [u1, u2], [s1]


@pytest.mark.asyncio
async def test_split_group_moves_solved_deletes_divider_writes_rows():
    guild, primary, solved_cat, div, unsolved, solved = _split_fixtures()
    dao = MagicMock()
    dao.get_for_group.return_value = []           # not yet split
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.split_group(guild, tag_group, dao)

    # Solved channel moved into the new solved category; unsolved untouched.
    solved[0].edit.assert_awaited_once_with(category=solved_cat)
    unsolved[0].edit.assert_not_awaited()
    # Divider deleted.
    div.delete.assert_awaited_once()
    # Two rows written: unsolved seq0 -> primary, solved seq0 -> solved_cat.
    assert dao.commit.call_count == 2
    written = [c.args[0] for c in dao.commit.call_args_list]
    roles = {(r.role, r.seq, r.category_id) for r in written}
    assert ("unsolved", 0, 100) in roles
    assert ("solved", 0, 200) in roles


@pytest.mark.asyncio
async def test_split_group_idempotent_when_already_split():
    guild, primary, solved_cat, div, unsolved, solved = _split_fixtures()
    dao = MagicMock()
    dao.get_for_group.return_value = [TagGroupCategoryData(group_id=5, category_id=100)]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.split_group(guild, tag_group, dao)

    primary.clone.assert_not_called()
    dao.commit.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k split -q > /tmp/test-output.log 2>&1`
Expected: FAIL — `AttributeError: module 'bot.core.category_layout' has no attribute 'split_group'`.

- [ ] **Step 3: Implement `split_group`**

Append to `bot/core/category_layout.py`:

```python
# ----------------------------------------------------------------- split
async def split_group(guild, tag_group, dao):
    """Single → split: create the solved category, move solved channels into it,
    delete the divider, and record both seq=0 rows. Idempotent and resumable:
    re-runs when already split are a no-op; a reused-by-name solved category is
    not re-cloned.
    """
    if dao.get_for_group(tag_group.id):
        return  # already split
    primary = _category_by_id(guild, tag_group.category_id)
    if primary is None:
        return
    base = primary.name
    divider_pos = solve_divider_position(primary)
    async with layout_lock:
        solved_cat = discord.utils.get(
            guild.categories, name=solved_category_name(base)
        )
        if solved_cat is None:
            solved_cat = await primary.clone(name=solved_category_name(base))
            await solved_cat.edit(position=primary.position + 1)
        if divider_pos is not False:
            movers = [
                ch for ch in list(primary.channels)
                if ch.name != SOLVE_DIVIDER and ch.position > divider_pos
            ]
            for ch in movers:
                await ch.edit(category=solved_cat)
            divider = discord.utils.get(primary.channels, name=SOLVE_DIVIDER)
            if divider is not None:
                await divider.delete(reason=DELETE_REASON)
        dao.commit(TagGroupCategoryData(
            group_id=tag_group.id, category_id=primary.id,
            role=ROLE_UNSOLVED, seq=0))
        dao.commit(TagGroupCategoryData(
            group_id=tag_group.id, category_id=solved_cat.id,
            role=ROLE_SOLVED, seq=0))
```

- [ ] **Step 4: Run to verify pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k split -q > /tmp/test-output.log 2>&1`
Expected: PASS (both split tests).

- [ ] **Step 5: Lint + commit**

```bash
pipenv run ruff check bot/core/category_layout.py
git add bot/core/category_layout.py tests/test_category_layout.py
git commit -m "feat(categories): split transition (unsolved/solved)"
```

---

## Task 4: Allocation + create-time placement

**Files:**
- Modify: `bot/core/category_layout.py`
- Modify: `bot/core/puzzle_service.py:1366-1410`
- Test: `tests/test_category_layout.py`, `tests/test_create_service.py`

- [ ] **Step 1: Write failing tests for picking/allocation**

Add to `tests/test_category_layout.py`:

```python
def _cat(cid, position, n_channels, name="round-1"):
    chans = []
    for i in range(n_channels):
        ch = MagicMock(); ch.name = f"p{cid}-{i}"; chans.append(ch)
    cat = MagicMock(id=cid, position=position)
    cat.name = name
    cat.channels = chans
    cat.edit = AsyncMock()
    return cat


@pytest.mark.asyncio
async def test_pick_unsolved_single_with_room_returns_primary():
    primary = _cat(100, 5, n_channels=3)
    guild = MagicMock(); guild.categories = [primary]
    dao = MagicMock(); dao.get_for_group.return_value = []
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)
    assert result is primary
    dao.commit.assert_not_called()


@pytest.mark.asyncio
async def test_pick_unsolved_split_picks_lowest_seq_with_room():
    u0 = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)   # full
    u1 = _cat(101, 6, n_channels=3)                            # has room
    guild = MagicMock(); guild.categories = [u0, u1]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=101, role="unsolved", seq=1),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)
    assert result is u1


@pytest.mark.asyncio
async def test_pick_unsolved_allocates_when_all_full():
    u0 = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)   # full
    new_cat = _cat(101, 6, n_channels=0)
    u0.clone = AsyncMock(return_value=new_cat)
    guild = MagicMock(); guild.categories = [u0]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)
    assert result is new_cat
    # New row written for unsolved seq=1.
    row = dao.commit.call_args.args[0]
    assert (row.role, row.seq, row.category_id) == ("unsolved", 1, 101)
```

- [ ] **Step 2: Run to verify failure**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k pick_unsolved -q > /tmp/test-output.log 2>&1`
Expected: FAIL — no attribute `pick_unsolved_category`.

- [ ] **Step 3: Implement allocation + pickers**

Append to `bot/core/category_layout.py`:

```python
# ----------------------------------------------------------------- placement
async def _allocate(guild, tag_group, dao, role_rows, role):
    """Create the next numbered category for *role* and record its row."""
    next_seq = max((r.seq for r in role_rows), default=-1) + 1
    name = overflow_category_name(base_name(guild, tag_group), role, next_seq)
    template = _category_by_id(guild, tag_group.category_id)
    async with layout_lock:
        existing = discord.utils.get(guild.categories, name=name)
        new_cat = existing or await template.clone(name=name)
        if existing is None:
            await new_cat.edit(position=template.position + 1)
    dao.commit(TagGroupCategoryData(
        group_id=tag_group.id, category_id=new_cat.id, role=role, seq=next_seq))
    return new_cat


async def _pick_or_allocate(guild, tag_group, dao, rows, role):
    role_rows = sorted((r for r in rows if r.role == role), key=lambda r: r.seq)
    for r in role_rows:
        cat = _category_by_id(guild, r.category_id)
        if cat is not None and puzzle_count(cat) < CATEGORY_SOFT_LIMIT:
            return cat
    return await _allocate(guild, tag_group, dao, role_rows, role)


async def pick_unsolved_category(guild, tag_group, dao):
    """Category a new puzzle should be created in. Splits the group first if a
    single category would cross the soft limit."""
    rows = dao.get_for_group(tag_group.id)
    if not rows:
        primary = _category_by_id(guild, tag_group.category_id)
        if primary is None:
            return None
        if puzzle_count(primary) + 1 <= CATEGORY_SOFT_LIMIT:
            return primary
        await split_group(guild, tag_group, dao)
        rows = dao.get_for_group(tag_group.id)
    return await _pick_or_allocate(guild, tag_group, dao, rows, ROLE_UNSOLVED)


async def pick_solved_category(guild, tag_group, dao):
    """Category a just-solved puzzle's channel should move into (split state only)."""
    rows = dao.get_for_group(tag_group.id)
    return await _pick_or_allocate(guild, tag_group, dao, rows, ROLE_SOLVED)
```

- [ ] **Step 4: Run to verify pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k "pick_unsolved or pick_solved" -q > /tmp/test-output.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Wire `create_puzzle` to the picker**

In `bot/core/puzzle_service.py`, add the import near the other `from bot.core...` imports at the top of the file (search for `from bot.core.discord_channels import`):

```python
from bot.core import category_layout
from bot.store import TagGroupCategoryDb
```

Then in `create_puzzle` (lines 1366-1375), replace the category resolution + hard `category_full` raise:

```python
        # No round (an unassigned puzzle) → drop the channel in the hunt's own category
        # rather than at the guild root.
        category_id = round_data.category_id if round_data else hunt.category_id
        category = discord.utils.get(guild.categories, id=category_id) if category_id else None

        # Space checks before we create any Google sheet (avoids orphan sheets).
        if len(guild.channels) >= MAX_CHANNELS_PER_GUILD:
            raise RuntimeError("guild_full")
        if category is not None and len(category.channels) >= MAX_CHANNELS_PER_CATEGORY:
            raise RuntimeError("category_full")
```

with:

```python
        # No round (an unassigned puzzle) → drop the channel in the hunt's own category
        # rather than at the guild root.
        if round_data is not None:
            # Round puzzles flow through the multi-category layout: this splits the
            # round and/or allocates an overflow category as needed, so a single
            # category never hits the 50 cap.
            category = await category_layout.pick_unsolved_category(
                guild, round_data, TagGroupCategoryDb,
            )
        else:
            category_id = hunt.category_id
            category = discord.utils.get(guild.categories, id=category_id) if category_id else None

        # Guild-wide ceiling still applies (overflow can't conjure channel slots).
        if len(guild.channels) >= MAX_CHANNELS_PER_GUILD:
            raise RuntimeError("guild_full")
        # Hunt category (no round) keeps the simple hard guard.
        if round_data is None and category is not None and len(category.channels) >= MAX_CHANNELS_PER_CATEGORY:
            raise RuntimeError("category_full")
```

> The divider repositioning at lines 1406-1410 (`solve_divider_position(category)` → `text_channel.edit(position=divider)`) stays unchanged: in single state the picker returns the primary (with divider), so new channels still land above the divider; in split state there is no divider, so `solve_divider_position` returns `False` and the block is skipped.

- [ ] **Step 6: Add a service-level placement test**

Add to `tests/test_create_service.py`:

```python
@pytest.mark.asyncio
async def test_create_puzzle_uses_layout_picker_for_round():
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    target_cat = MagicMock(id=777)
    target_cat.channels = []
    guild.categories = [target_cat]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=target_cat)) as pick, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="newpuz"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(guild, _svc_hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier)
    pick.assert_awaited_once()
    # The channel was created in the category the picker chose.
    from bot.core.puzzle_service import get_or_create_channel  # noqa: F401
```

> Note: the existing `test_create_puzzle_commits_row_creates_sheet_and_notifies` and `test_create_puzzle_orphan_cleanup_on_channel_failure` pass `round_data` and stub `get_or_create_channel`. They will now also hit `pick_unsolved_category`, which with their `guild.categories = [MagicMock(id=999)]` (no `.channels` length issues — `MagicMock` len fails). **Update those two tests** to give the category `channels=[]`: change `cat = MagicMock(id=999)` / `guild.categories = [MagicMock(id=999)]` to set `channels=[]`, OR patch `pick_unsolved_category` in them too. Prefer patching the picker (one line) to keep them focused on sheet/cleanup behaviour:
>
> ```python
>     with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
>          patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
>                AsyncMock(return_value=MagicMock(id=999, channels=[]))), \
>          patch("bot.core.puzzle_service.get_or_create_channel", ...):
> ```

- [ ] **Step 7: Run the create tests**

Run: `timeout 120 pipenv run python -m pytest tests/test_create_service.py -q > /tmp/test-output.log 2>&1`
Expected: PASS (all four, including the new one). If the two legacy tests fail on `len(category.channels)`, apply the picker-patch fix above and re-run.

- [ ] **Step 8: Lint + commit**

```bash
pipenv run ruff check bot/core/category_layout.py bot/core/puzzle_service.py tests/
git add bot/core/category_layout.py bot/core/puzzle_service.py tests/test_category_layout.py tests/test_create_service.py
git commit -m "feat(categories): create-time placement via layout picker"
```

---

## Task 5: Solve/unsolve repositioning across categories

**Files:**
- Modify: `bot/cogs/puzzles.py:685-723` (solve) and add an unsolve reposition method
- Modify: `bot/web/actions.py:1616` (call unsolve reposition)
- Modify: `bot/cogs/puzzles.py:2341` (call unsolve reposition after bang `!unsolve`)
- Test: `tests/test_category_layout.py`

The cog resolves a channel's group via the primary category first, then the join table (a solved/overflow category isn't `tag_group.category_id`). Add a module helper for that lookup so it's testable.

- [ ] **Step 1: Write failing test for channel→group resolution**

Add to `tests/test_category_layout.py`:

```python
def test_group_id_for_category_uses_dao_row():
    dao = MagicMock()
    dao.get_by_category.return_value = TagGroupCategoryData(
        group_id=42, category_id=200, role="solved", seq=0)
    assert layout.group_id_for_category(200, dao) == 42


def test_group_id_for_category_none_when_unknown():
    dao = MagicMock()
    dao.get_by_category.return_value = None
    assert layout.group_id_for_category(999, dao) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k group_id_for_category -q > /tmp/test-output.log 2>&1`
Expected: FAIL — no attribute `group_id_for_category`.

- [ ] **Step 3: Implement the resolver + move helpers**

Append to `bot/core/category_layout.py`:

```python
# ----------------------------------------------------------------- solve/unsolve moves
def group_id_for_category(category_id, dao):
    """The tag_group id owning *category_id* via the join table, or None."""
    row = dao.get_by_category(category_id)
    return row.group_id if row is not None else None


async def move_channel_to_solved(guild, tag_group, dao, channel):
    """Split state: move a just-solved channel into a solved category with room.
    Returns the source category it left (for empty-overflow cleanup), or None."""
    source = channel.category
    target = await pick_solved_category(guild, tag_group, dao)
    if target is None or (source is not None and target.id == source.id):
        return None
    async with layout_lock:
        await channel.edit(category=target)
    return source


async def move_channel_to_unsolved(guild, tag_group, dao, channel):
    """Split state: move a just-unsolved channel back to an unsolved category."""
    source = channel.category
    target = await pick_unsolved_category(guild, tag_group, dao)
    if target is None or (source is not None and target.id == source.id):
        return None
    async with layout_lock:
        await channel.edit(category=target)
    return source
```

- [ ] **Step 4: Run to verify pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k group_id_for_category -q > /tmp/test-output.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Add move tests**

Add to `tests/test_category_layout.py`:

```python
@pytest.mark.asyncio
async def test_move_channel_to_solved_moves_into_solved_category():
    solved_cat = _cat(200, 6, n_channels=0)
    guild = MagicMock(); guild.categories = [solved_cat]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=200, role="solved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    source = MagicMock(id=100)
    channel = MagicMock(); channel.category = source; channel.edit = AsyncMock()

    returned_source = await layout.move_channel_to_solved(guild, tag_group, dao, channel)
    channel.edit.assert_awaited_once_with(category=solved_cat)
    assert returned_source is source


@pytest.mark.asyncio
async def test_move_channel_to_solved_noop_if_already_there():
    solved_cat = _cat(200, 6, n_channels=0)
    guild = MagicMock(); guild.categories = [solved_cat]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=200, role="solved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    channel = MagicMock(); channel.category = solved_cat; channel.edit = AsyncMock()

    returned = await layout.move_channel_to_solved(guild, tag_group, dao, channel)
    channel.edit.assert_not_awaited()
    assert returned is None
```

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k move_channel -q > /tmp/test-output.log 2>&1`
Expected: PASS.

- [ ] **Step 6: Wire solve reposition in the cog**

In `bot/cogs/puzzles.py`, add the imports near the top (with the other `from bot.core...` / `from bot.store...` imports):

```python
from bot.core import category_layout
from bot.store import TagGroupCategoryDb, TagGroupJsonDb  # TagGroupJsonDb may already be imported
```

(If `TagGroupJsonDb` is already imported, only add `category_layout` and `TagGroupCategoryDb`.)

Add a helper method to the `Puzzles` cog (near `get_tag_from_category`, ~line 766):

```python
    def _tag_group_for_channel(self, channel):
        """Resolve the tag_group owning a channel's category — primary category
        first, then the join table for solved/overflow categories."""
        if channel.category is None:
            return None
        tg = TagGroupJsonDb.get_by_attr(category_id=channel.category.id)
        if tg is not None:
            return tg
        gid = category_layout.group_id_for_category(channel.category.id, TagGroupCategoryDb)
        return TagGroupJsonDb.get_by_attr(id=gid) if gid else None
```

Then replace the solve reposition block in `web_post_solved_embed` (lines 709-716):

```python
        elif channel is not None and channel.category is not None:
            try:
                divider = self.get_solve_divider_position(None, channel.category)
                if divider is not False:
                    async with self.position_lock:
                        await channel.edit(position=divider + 1)
            except Exception:
                logger.exception("web_post_solved_embed: failed to reposition channel")
```

with:

```python
        elif channel is not None and channel.category is not None:
            try:
                tag_group = self._tag_group_for_channel(channel)
                if tag_group is not None and category_layout.is_split(tag_group, TagGroupCategoryDb):
                    source = await category_layout.move_channel_to_solved(
                        guild, tag_group, TagGroupCategoryDb, channel,
                    )
                    if source is not None:
                        await category_layout.cleanup_empty_overflow(
                            guild, tag_group, TagGroupCategoryDb, source,
                        )
                else:
                    # Single (divider) state — unchanged behaviour.
                    divider = self.get_solve_divider_position(None, channel.category)
                    if divider is not False:
                        async with self.position_lock:
                            await channel.edit(position=divider + 1)
            except Exception:
                logger.exception("web_post_solved_embed: failed to reposition channel")
```

> `guild` is the in-scope parameter of `web_post_solved_embed`. `cleanup_empty_overflow` is added in Task 6; until then this references a not-yet-defined function, so the `getattr` guard below keeps Task 5 green on its own (the unit tests for this task don't exercise the cleanup branch).

To keep tasks independently runnable, gate the cleanup call so Task 5 is green on its own by temporarily using `getattr`:

```python
                    if source is not None and hasattr(category_layout, "cleanup_empty_overflow"):
                        await category_layout.cleanup_empty_overflow(
                            guild, tag_group, TagGroupCategoryDb, source,
                        )
```

(Replace the `getattr` guard with a direct call once Task 6 lands — noted in Task 6.)

- [ ] **Step 7: Wire unsolve reposition**

Add a cog method next to `web_post_solved_embed`:

```python
    async def web_post_unsolved(self, guild, hunt, puzzle):
        """Best-effort: move a just-unsolved channel back to an unsolved category
        (split state) or above the divider (single state). Never raises."""
        channel = guild.get_channel_or_thread(puzzle.channel_id) if (guild and puzzle.channel_id) else None
        if channel is None or isinstance(channel, discord.Thread) or channel.category is None:
            return
        try:
            tag_group = self._tag_group_for_channel(channel)
            if tag_group is not None and category_layout.is_split(tag_group, TagGroupCategoryDb):
                await category_layout.move_channel_to_unsolved(
                    guild, tag_group, TagGroupCategoryDb, channel,
                )
            else:
                divider = self.get_solve_divider_position(None, channel.category)
                if divider is not False:
                    async with self.position_lock:
                        await channel.edit(position=divider)
        except Exception:
            logger.exception("web_post_unsolved: failed to reposition channel")
```

In `bot/web/actions.py` at line 1616, after `await cog.service.unsolve(guild, hunt_obj, puzzle)`, add:

```python
            await cog.web_post_unsolved(guild, hunt_obj, puzzle)
```

In `bot/cogs/puzzles.py` at the bang `!unsolve` command (after `await self.service.unsolve(self.get_guild(ctx), hunt, puzzle)`, line 2341), add:

```python
            await self.web_post_unsolved(self.get_guild(ctx), hunt, puzzle)
```

- [ ] **Step 8: Run module tests + lint**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1`
Expected: PASS.
Run: `pipenv run ruff check bot/core/category_layout.py bot/cogs/puzzles.py bot/web/actions.py`
Expected: exit 0.

- [ ] **Step 9: Commit**

```bash
git add bot/core/category_layout.py bot/cogs/puzzles.py bot/web/actions.py tests/test_category_layout.py
git commit -m "feat(categories): solve/unsolve channel moves across categories"
```

---

## Task 6: Empty-overflow cleanup

**Files:**
- Modify: `bot/core/category_layout.py`
- Modify: `bot/cogs/puzzles.py` (replace the Task 5 `getattr` guard with a direct call; add cleanup to puzzle-delete path)
- Test: `tests/test_category_layout.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_category_layout.py`:

```python
@pytest.mark.asyncio
async def test_cleanup_deletes_empty_overflow_and_row():
    overflow = _cat(201, 7, n_channels=0)
    overflow.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [overflow]
    row = TagGroupCategoryData(id=9, group_id=5, category_id=201, role="solved", seq=1)
    dao = MagicMock(); dao.get_by_category.return_value = row
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.cleanup_empty_overflow(guild, tag_group, dao, overflow)
    overflow.delete.assert_awaited_once()
    dao.delete.assert_called_once_with(9)


@pytest.mark.asyncio
async def test_cleanup_keeps_primary_seq0():
    primary = _cat(100, 5, n_channels=0)
    primary.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [primary]
    row = TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0)
    dao = MagicMock(); dao.get_by_category.return_value = row
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.cleanup_empty_overflow(guild, tag_group, dao, primary)
    primary.delete.assert_not_awaited()
    dao.delete.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_keeps_nonempty_overflow():
    overflow = _cat(201, 7, n_channels=3)
    overflow.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [overflow]
    row = TagGroupCategoryData(id=9, group_id=5, category_id=201, role="solved", seq=1)
    dao = MagicMock(); dao.get_by_category.return_value = row
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.cleanup_empty_overflow(guild, tag_group, dao, overflow)
    overflow.delete.assert_not_awaited()
    dao.delete.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k cleanup -q > /tmp/test-output.log 2>&1`
Expected: FAIL — no attribute `cleanup_empty_overflow`.

- [ ] **Step 3: Implement cleanup**

Append to `bot/core/category_layout.py`:

```python
# ----------------------------------------------------------------- cleanup
async def cleanup_empty_overflow(guild, tag_group, dao, category):
    """Delete *category* and its join row if it is an OVERFLOW category (seq>=1)
    that now holds zero puzzle channels. Never touches a primary (seq=0) category
    and never reverts split→single. Best-effort: logs and swallows Discord errors.
    """
    if category is None:
        return
    row = dao.get_by_category(category.id)
    if row is None or row.seq < 1:
        return
    if puzzle_count(category) > 0:
        return
    try:
        async with layout_lock:
            await category.delete(reason=DELETE_REASON)
        dao.delete(row.id)
    except Exception:
        logger.exception("cleanup_empty_overflow: failed to remove category %s", category.id)
```

- [ ] **Step 4: Run to verify pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k cleanup -q > /tmp/test-output.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Replace the Task 5 `getattr` guard with a direct call**

In `bot/cogs/puzzles.py` `web_post_solved_embed`, change:

```python
                    if source is not None and hasattr(category_layout, "cleanup_empty_overflow"):
                        await category_layout.cleanup_empty_overflow(
                            guild, tag_group, TagGroupCategoryDb, source,
                        )
```

to:

```python
                    if source is not None:
                        await category_layout.cleanup_empty_overflow(
                            guild, tag_group, TagGroupCategoryDb, source,
                        )
```

- [ ] **Step 6: Run cleanup tests + lint**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1`
Expected: PASS.
Run: `pipenv run ruff check bot/core/category_layout.py bot/cogs/puzzles.py`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add bot/core/category_layout.py bot/cogs/puzzles.py tests/test_category_layout.py
git commit -m "feat(categories): empty-overflow category cleanup"
```

---

## Task 7: Round deletion across all categories

**Files:**
- Modify: `bot/core/category_layout.py`
- Modify: `bot/cogs/puzzles.py:2559-2566` (`_delete_round_category`)
- Test: `tests/test_category_layout.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_category_layout.py`:

```python
@pytest.mark.asyncio
async def test_delete_group_categories_removes_all_and_rows():
    primary = _cat(100, 5, n_channels=0); primary.delete = AsyncMock()
    solved = _cat(200, 6, n_channels=0); solved.delete = AsyncMock()
    overflow = _cat(201, 7, n_channels=0); overflow.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [primary, solved, overflow]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=200, role="solved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=201, role="solved", seq=1),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.delete_group_categories(guild, tag_group, dao)

    primary.delete.assert_awaited_once()
    solved.delete.assert_awaited_once()
    overflow.delete.assert_awaited_once()
    dao.delete_for_group.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_delete_group_categories_single_state_uses_legacy_only():
    # No join rows → returns False so the caller runs its legacy single-category path.
    guild = MagicMock(); guild.categories = []
    dao = MagicMock(); dao.get_for_group.return_value = []
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    assert await layout.delete_group_categories(guild, tag_group, dao) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k delete_group -q > /tmp/test-output.log 2>&1`
Expected: FAIL — no attribute `delete_group_categories`.

- [ ] **Step 3: Implement teardown**

Append to `bot/core/category_layout.py`:

```python
# ----------------------------------------------------------------- teardown
async def delete_group_categories(guild, tag_group, dao):
    """Split state: delete every category the group owns (channels and all) and
    drop the join rows. Returns True if it handled deletion, False if the group
    is in single-category state (caller should run its legacy path).
    """
    rows = dao.get_for_group(tag_group.id)
    if not rows:
        return False
    async with layout_lock:
        for row in rows:
            cat = _category_by_id(guild, row.category_id)
            if cat is None:
                continue
            for ch in list(cat.channels):
                await ch.delete(reason=DELETE_REASON)
            await cat.delete(reason=DELETE_REASON)
    dao.delete_for_group(tag_group.id)
    return True
```

- [ ] **Step 4: Run to verify pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k delete_group -q > /tmp/test-output.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Wire into `_delete_round_category`**

In `bot/cogs/puzzles.py`, replace `_delete_round_category` (lines 2559-2566):

```python
    async def _delete_round_category(self, ctx, tag_group):
        """Delete a round's category and its solved-divider channel (if present)."""
        category = discord.utils.get(self.get_guild(ctx).categories, id=tag_group.category_id)
        if self.SOLVE_CATEGORY is False:
            solved_divider = self.get_solved_channel(category)
            if solved_divider:
                await solved_divider.delete(reason=self.DELETE_REASON)
        await category.delete(reason=self.DELETE_REASON)
```

with:

```python
    async def _delete_round_category(self, ctx, tag_group):
        """Delete a round's categories. Split rounds own several categories
        (unsolved/solved/overflow) tracked in tag_group_category; single rounds
        keep the legacy one-category + divider teardown."""
        guild = self.get_guild(ctx)
        if await category_layout.delete_group_categories(guild, tag_group, TagGroupCategoryDb):
            return
        category = discord.utils.get(guild.categories, id=tag_group.category_id)
        if category is None:
            return
        if self.SOLVE_CATEGORY is False:
            solved_divider = self.get_solved_channel(category)
            if solved_divider:
                await solved_divider.delete(reason=self.DELETE_REASON)
        await category.delete(reason=self.DELETE_REASON)
```

> (Adds a `category is None` guard that wasn't there before — harmless and avoids an AttributeError if the category was already removed.)

- [ ] **Step 6: Run + lint**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1`
Expected: PASS.
Run: `pipenv run ruff check bot/core/category_layout.py bot/cogs/puzzles.py`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add bot/core/category_layout.py bot/cogs/puzzles.py tests/test_category_layout.py
git commit -m "feat(categories): delete all of a split round's categories"
```

---

## Task 8: Guild-ceiling guard + full verification

**Files:**
- Modify: `bot/core/category_layout.py` (raise a clear error when no category budget remains)
- Test: `tests/test_category_layout.py`

Overflow allocation consumes from the guild's `MAX_CATEGORIES` budget. Allocation must fail loudly rather than letting `clone()` throw an opaque Discord error.

- [ ] **Step 1: Write failing test**

Add to `tests/test_category_layout.py`:

```python
@pytest.mark.asyncio
async def test_allocate_raises_when_no_category_budget():
    from bot.core.discord_channels import MAX_CATEGORIES
    u0 = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)
    guild = MagicMock()
    guild.categories = [u0] * MAX_CATEGORIES   # at the ceiling
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    with pytest.raises(RuntimeError, match="no_category_space"):
        await layout.pick_unsolved_category(guild, tag_group, dao)
```

- [ ] **Step 2: Run to verify failure**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k no_category_budget -q > /tmp/test-output.log 2>&1`
Expected: FAIL — no `RuntimeError` raised.

- [ ] **Step 3: Add the guard in `_allocate`**

In `bot/core/category_layout.py`, add the import to the existing `from bot.core.discord_channels import (...)` block:

```python
from bot.core.discord_channels import (
    SOLVE_DIVIDER, DELETE_REASON, MAX_CATEGORIES,
    solve_divider_position, clean_name,
)
```

Then at the top of `_allocate`, before computing `next_seq`:

```python
async def _allocate(guild, tag_group, dao, role_rows, role):
    """Create the next numbered category for *role* and record its row."""
    if len(guild.categories) >= MAX_CATEGORIES:
        raise RuntimeError("no_category_space")
    next_seq = max((r.seq for r in role_rows), default=-1) + 1
    ...
```

- [ ] **Step 4: Run to verify pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_category_layout.py -k no_category_budget -q > /tmp/test-output.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Full focused suite for touched areas**

Run: `timeout 180 pipenv run python -m pytest tests/test_category_layout.py tests/test_create_service.py tests/test_discord_channels.py tests/test_delete_safety.py -q --compact > /tmp/test-output.log 2>&1`
Expected: PASS (no regressions).

- [ ] **Step 6: Full suite + lint**

Run: `timeout 300 pipenv run python -m pytest tests/ -q --compact --parallel > /tmp/test-output.log 2>&1`
Expected: PASS. Read the tail of the log for any failures.
Run: `pipenv run ruff check bot/ tests/`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add bot/core/category_layout.py tests/test_category_layout.py
git commit -m "feat(categories): guard category ceiling on overflow allocation"
```

---

## Verification (whole feature)

- A round grown past `CATEGORY_SOFT_LIMIT` (manually drive `create_puzzle`, or set the constant low in a scratch run) produces separate `<name>` and `<name> — solved` categories with the divider gone and solved channels relocated; further growth adds `<name> (2)` / `<name> — solved (2)`.
- Solving moves a channel into the solved side; unsolving moves it back; emptying an overflow category deletes it and its row.
- A small round (under the soft limit) is unchanged: one category + `———solved———` divider, divider repositioning on solve.
- `SHOW TABLES LIKE 'tag_group_category'` exists; `SHOW COLUMNS FROM tag_group_category` shows `id, group_id, category_id, role, seq`.
- Deleting a split round removes all its categories and leaves no `tag_group_category` rows (`SELECT * FROM tag_group_category WHERE group_id = <id>` empty).
- `pipenv run ruff check bot/ tests/` exit 0; full pytest suite green.

## Rollback / recovery notes

- Revert the feature commits.
- Reverse migration 014: `DROP TABLE tag_group_category;`. Groups that had split keep their extra Discord categories, but with the code gone they fall back to single-`category_id` behaviour; orphan categories must be cleaned up manually (this is a one-way risk — note it before merging to a live guild).
- The split transition and cleanup are best-effort and idempotent: re-running `create_puzzle`/solve after a partial Discord failure converges (re-detects state and finishes), so recovery is "trigger the same action again".
