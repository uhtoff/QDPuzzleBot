# Delete Round: Fix Count, Add "Keep Puzzles" Scope — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the delete-round confirm modal's puzzle count, add a `keep_puzzles` scope to `delete_round` (default: keep every member puzzle, tearing down only the round's own target/chat channel + category, relocating kept puzzles' channels into the hunt's general category), and fix the pre-existing bug where a target-less Group round's chat channel is never deleted.

**Architecture:** All new logic lives in the existing `puzzle_service.delete_round` seam (shared by the bang and web edges) plus two small additions to `category_layout.py` (`group_chat_channel`, `move_channel_to_general`, and a `keep_channel_ids` guard on `delete_group_categories`). The web route gains a JSON body param; the bang command explicitly opts into the old full-delete behavior. Frontend gets a radio choice in the existing `TypedConfirmModal`.

**Tech Stack:** Python (Quart backend, `discord.py`), TypeScript/React (Mantine, TanStack Query) frontend, pytest (`pipenv run python -m pytest`).

**Design doc:** `docs/superpowers/specs/2026-07-02-delete-round-scope-design.md` — read it first; this plan implements it.

---

## Before you start

Run tests via `pipenv run python -m pytest` (NOT the bare `.venv` in this worktree — it's missing `python-slugify` and other deps; `pipenv run` resolves the correct environment). Always wrap in `timeout` and redirect to a file per project convention:

```bash
timeout 120 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1
```

Then read `/tmp/test-output.log`.

---

### Task 1: `category_layout.py` — new helpers + `keep_channel_ids` guard

**Files:**
- Modify: `bot/core/category_layout.py`
- Test: `tests/test_category_layout.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_category_layout.py` (file already has `import pytest`, `from unittest.mock import AsyncMock, MagicMock`, and `from bot.core import category_layout as layout` at module level — use `layout.<name>` directly, no per-test imports):

```python
# ----------------------------------------------------------------- group_chat_channel

def test_group_chat_channel_none_when_has_target():
    tag_group = MagicMock(category_id=100)
    tag_group.has_target = True
    assert layout.group_chat_channel(MagicMock(), tag_group) is None


def test_group_chat_channel_none_when_no_primary_category():
    tag_group = MagicMock(category_id=0)
    tag_group.has_target = False
    guild = MagicMock()
    guild.categories = []
    assert layout.group_chat_channel(guild, tag_group) is None


def test_group_chat_channel_finds_by_name():
    tag_group = MagicMock(category_id=100)
    tag_group.name = "Loose Ends"
    tag_group.has_target = False
    chat_ch = MagicMock()
    chat_ch.name = "loose-ends"
    primary = MagicMock(id=100)
    primary.text_channels = [chat_ch]
    guild = MagicMock()
    guild.categories = [primary]
    assert layout.group_chat_channel(guild, tag_group) is chat_ch


# ----------------------------------------------------------------- move_channel_to_general

@pytest.mark.asyncio
async def test_move_channel_to_general_no_category_returns_false():
    guild = MagicMock()
    guild.categories = []
    channel = MagicMock()
    channel.edit = AsyncMock()
    assert await layout.move_channel_to_general(guild, 999, channel, False) is False
    channel.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_to_general_full_category_returns_false():
    from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY
    general = MagicMock(id=500)
    general.channels = [MagicMock() for _ in range(MAX_CHANNELS_PER_CATEGORY)]
    guild = MagicMock()
    guild.categories = [general]
    channel = MagicMock()
    channel.edit = AsyncMock()
    assert await layout.move_channel_to_general(guild, 500, channel, False) is False
    channel.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_to_general_unsolved_positions_above_divider():
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = 5
    other = MagicMock(); other.name = "general"; other.position = 0
    general = MagicMock(id=500)
    general.channels = [div, other]
    guild = MagicMock()
    guild.categories = [general]
    channel = MagicMock()
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_general(guild, 500, channel, False)

    assert result is True
    channel.edit.assert_awaited_once_with(category=general, position=4)


@pytest.mark.asyncio
async def test_move_channel_to_general_solved_positions_after_divider():
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = 5
    other = MagicMock(); other.name = "general"; other.position = 0
    general = MagicMock(id=500)
    general.channels = [div, other]
    guild = MagicMock()
    guild.categories = [general]
    channel = MagicMock()
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_general(guild, 500, channel, True)

    assert result is True
    channel.edit.assert_awaited_once_with(category=general, position=6)


# ----------------------------------------------------------------- delete_group_categories keep_channel_ids

@pytest.mark.asyncio
async def test_delete_group_categories_keeps_protected_channel():
    keep = MagicMock(id=42); keep.delete = AsyncMock()
    doomed = MagicMock(id=43); doomed.delete = AsyncMock()
    cat = MagicMock(id=100); cat.channels = [keep, doomed]; cat.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [cat]
    dao = MagicMock()
    dao.get_for_group.return_value = [TagGroupCategoryData(group_id=5, category_id=100)]
    tag_group = MagicMock(id=5)

    result = await layout.delete_group_categories(guild, tag_group, dao, keep_channel_ids={42})

    assert result is True
    keep.delete.assert_not_called()
    doomed.delete.assert_awaited_once()
    cat.delete.assert_awaited_once()
    dao.delete_for_group.assert_called_once_with(5)
```

`SOLVE_DIVIDER` needs importing at the top of the test file (it currently imports it inline inside `test_puzzle_count_excludes_divider` and `_split_fixtures` — add a module-level import instead so the new tests can use it directly):

```python
from bot.core.discord_channels import SOLVE_DIVIDER
```

Add this import near the top of `tests/test_category_layout.py` (after the existing `from bot.store.tag_group_category_data import ...` block, before `from bot.core import category_layout as layout`). `TagGroupCategoryData` is already imported at the top of the file.

- [ ] **Step 2: Run tests to verify they fail**

```bash
timeout 60 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1
```

Expected: FAIL — `AttributeError: module 'bot.core.category_layout' has no attribute 'group_chat_channel'` (and similarly for `move_channel_to_general`; the `keep_channel_ids` test fails with `TypeError: delete_group_categories() got an unexpected keyword argument`).

- [ ] **Step 3: Implement the helpers in `category_layout.py`**

Update the import line at the top of `bot/core/category_layout.py`:

```python
from bot.core.discord_channels import DELETE_REASON, MAX_CATEGORIES, MAX_CHANNELS_PER_CATEGORY, SOLVE_DIVIDER, clean_channel_name, clean_category_name, solve_divider_position
```

Add `group_chat_channel` right after `primary_category` (before `base_name`):

```python
def group_chat_channel(guild, tag_group):
    """The dedicated text channel for a target-less (Group-type) round's
    primary category, or None if the group has a target, its primary category
    can't be resolved (forum mode, or missing), or no channel matches. Same
    lookup group_rename_plan uses for its own rename step: the channel was
    created from clean_channel_name(tag_group.name) and every rename keeps
    that invariant, so matching by name (not by category name, which can gain
    a create-time collision suffix the channel never receives) is required.
    """
    if tag_group.has_target:
        return None
    primary = primary_category(guild, tag_group)
    if primary is None:
        return None
    return discord.utils.get(primary.text_channels, name=clean_channel_name(tag_group.name))
```

Add `move_channel_to_general` right after `move_channel_to_unsolved` (before the `# ----- cleanup` section):

```python
async def move_channel_to_general(guild, general_category_id, channel, solved):
    """Move *channel* into the hunt's general category (``general_category_id``)
    — used by delete_round's keep_puzzles mode to relocate a kept puzzle's
    channel out of a round category that's about to be deleted. Positioned
    like tag_edge_only's channel-move: unsolved just above the solved
    divider, solved after it. Returns True if moved, False if there's no
    general category, it's full, or the move failed (caller should treat
    that puzzle's channel as needing manual attention)."""
    general = _category_by_id(guild, general_category_id)
    if general is None or len(general.channels) >= MAX_CHANNELS_PER_CATEGORY:
        return False
    divider_pos = solve_divider_position(general)
    if divider_pos is not False:
        position = (
            max((ch.position for ch in general.channels), default=0) + 1
            if solved else divider_pos - 1
        )
    else:
        position = max((ch.position for ch in general.channels), default=-1) + 1
    try:
        async with layout_lock:
            await channel.edit(category=general, position=position)
    except (discord.RateLimited, discord.HTTPException):
        logger.exception("move_channel_to_general: could not move channel %s",
                         getattr(channel, "id", "?"))
        return False
    return True
```

Modify `delete_group_categories` to accept `keep_channel_ids`:

```python
async def delete_group_categories(guild, tag_group, dao, keep_channel_ids=frozenset()):
    """Split state: delete every category the group owns (channels and all,
    except any channel id in *keep_channel_ids* — left in place, since
    Discord doesn't cascade-delete children when their category goes) and
    drop the join rows. Returns True if it handled deletion, False if the
    group is in single-category state (caller should run its legacy path).
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
                if ch.id in keep_channel_ids:
                    continue
                try:
                    await ch.delete(reason=DELETE_REASON)
                except Exception:
                    logger.exception(
                        "delete_group_categories: could not delete channel %s",
                        getattr(ch, "id", "?"))
            try:
                await cat.delete(reason=DELETE_REASON)
            except Exception:
                logger.exception(
                    "delete_group_categories: could not delete category %s", row.category_id)
    dao.delete_for_group(tag_group.id)
    return True
```

(This only changes the signature and adds one `if ch.id in keep_channel_ids: continue` line inside the existing loop — the default `frozenset()` keeps every current call site's behavior byte-identical.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_category_layout.py -q > /tmp/test-output.log 2>&1
```

Expected: PASS (all tests in the file, including the pre-existing ones — `test_delete_group_categories_removes_all_and_rows`, `test_delete_group_categories_deletes_child_channels`, etc. must still pass unchanged).

- [ ] **Step 5: Commit**

```bash
git add bot/core/category_layout.py tests/test_category_layout.py
git commit -m "feat(category-layout): add group_chat_channel + move_channel_to_general helpers"
```

---

### Task 2: `delete_hunt` — fix orphaned Group chat channel

**Files:**
- Modify: `bot/core/puzzle_service.py:2183-2207` (inside `delete_hunt`)
- Test: `tests/test_delete_hunt_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_delete_hunt_service.py`:

```python
@pytest.mark.asyncio
async def test_delete_hunt_deletes_group_chat_channel():
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_hunt_spreadsheet = AsyncMock()

    group = MagicMock(id=9, category_id=100)
    group.has_target = False
    category = MagicMock(id=100)
    category.channels = []
    category.delete = AsyncMock()
    guild.categories = [category]
    chat_ch = MagicMock(id=555)

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=True)) as safe_delete, \
         patch("bot.core.puzzle_service.category_layout.group_chat_channel",
               return_value=chat_ch):
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = [group]
        await svc.delete_hunt(guild, hunt)

    safe_delete.assert_any_call(guild, 555)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
timeout 60 pipenv run python -m pytest tests/test_delete_hunt_service.py -q > /tmp/test-output.log 2>&1
```

Expected: FAIL — `safe_delete.assert_any_call(guild, 555)` raises `AssertionError` (no such call).

- [ ] **Step 3: Implement the fix**

In `bot/core/puzzle_service.py`, inside `delete_hunt`'s per-group teardown loop, find the `else:` branch (non-forum) that currently reads:

```python
                else:
                    category = discord.utils.get(guild.categories, id=tag_group.category_id)
                    if category is not None:
                        for ch in list(category.channels):
                            if getattr(ch, "name", "") == self._SOLVE_DIVIDER_NAME:
```

Change it to:

```python
                else:
                    general_ch = category_layout.group_chat_channel(guild, tag_group)
                    if general_ch is not None:
                        await self._safe_delete_channel(guild, general_ch.id)
                    category = discord.utils.get(guild.categories, id=tag_group.category_id)
                    if category is not None:
                        for ch in list(category.channels):
                            if getattr(ch, "name", "") == self._SOLVE_DIVIDER_NAME:
```

(Everything else in the block is unchanged — only the two new lines before the existing `category = ...` line.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_delete_hunt_service.py tests/test_delete_safety.py -q > /tmp/test-output.log 2>&1
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_delete_hunt_service.py
git commit -m "fix(delete-hunt): delete a target-less Group round's chat channel"
```

---

### Task 3: `delete_round` — `keep_puzzles` scope + chat-channel fix

**Files:**
- Modify: `bot/core/puzzle_service.py:1142-1195` (the whole `delete_round` method)
- Modify: `tests/test_solve_equivalence.py` (`TestWebDeleteRound`, lines ~789-857)
- Create: `tests/test_delete_round_keep_puzzles.py`
- Modify: `tests/test_forum_layout.py` (`test_delete_round_forum_deletes_forum_channel`)

- [ ] **Step 1: Update `TestWebDeleteRound`'s `_run` helper and existing tests (still-failing-old-behavior state)**

In `tests/test_solve_equivalence.py`, replace the `_run` method (~lines 801-824):

```python
    async def _run(self, cog, hunt, hunt_round, puzzles=None, keep_puzzles=None):
        """Run service.delete_round with mocked DBs.
        puzzles = PuzzleJsonDb.get_all_from_tag_group return value.
        keep_puzzles=None omits the kwarg entirely, exercising the service's
        own default (True); pass True/False to pin a specific mode."""
        puzzle_deletes = []
        round_deletes = []
        puzzles = puzzles or []

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.TagGroupJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.cogs.puzzles.GuildSettingsDb") as mock_gsdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.TagGroupJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
             patch("bot.core.puzzle_service.GuildSettingsDb", mock_gsdb):
            mock_pdb.delete.side_effect = lambda pid: puzzle_deletes.append(pid)
            mock_rdb.delete.side_effect = lambda rid: round_deletes.append(rid)
            mock_pdb.get_all_from_tag_group.return_value = puzzles
            mock_hdb.get_by_attr.return_value = hunt
            mock_gsdb.get.return_value = None

            kwargs = {} if keep_puzzles is None else {"keep_puzzles": keep_puzzles}
            failed = await cog.service.delete_round(_no_guild(), hunt, hunt_round, **kwargs)

        return puzzle_deletes, round_deletes, failed
```

Update the 4 existing test methods to pass `keep_puzzles=False` (pinning today's full-delete behavior explicitly, since it's no longer the default):

```python
    @pytest.mark.asyncio
    async def test_deletes_round_db_record(self, cog, hunt):
        rnd = make_round(id=5)
        _, round_deletes, _ = await self._run(cog, hunt, rnd, keep_puzzles=False)
        assert 5 in round_deletes

    @pytest.mark.asyncio
    async def test_deletes_all_puzzle_db_records(self, cog, hunt):
        rnd = make_round(id=5)
        p1 = make_puzzle(id=1, tags=[5])
        p2 = make_puzzle(id=2, tags=[5])
        puzzle_deletes, _, _ = await self._run(cog, hunt, rnd, puzzles=[p1, p2], keep_puzzles=False)
        assert set(puzzle_deletes) == {1, 2}

    @pytest.mark.asyncio
    async def test_empty_round_deletes_cleanly(self, cog, hunt):
        rnd = make_round(id=5)
        puzzle_deletes, round_deletes, failed = await self._run(cog, hunt, rnd, puzzles=[], keep_puzzles=False)
        assert puzzle_deletes == []
        assert 5 in round_deletes
        assert failed == []

    @pytest.mark.asyncio
    async def test_no_guild_skips_channel_delete(self, cog, hunt):
        """Guild=None → no Discord teardown, all DB deletes still happen."""
        rnd = make_round(id=5)
        p = make_puzzle(id=1, channel_id=888, tags=[5])
        puzzle_deletes, round_deletes, failed = await self._run(cog, hunt, rnd, puzzles=[p], keep_puzzles=False)
        assert 1 in puzzle_deletes
        assert 5 in round_deletes
        assert failed == []
```

Add two new tests for the (now-default) keep_puzzles behavior, right after `test_no_guild_skips_channel_delete`:

```python
    @pytest.mark.asyncio
    async def test_default_keeps_feeder_puzzles(self, cog, hunt):
        """keep_puzzles defaults to True: a target-less round's feeders keep
        their DB rows (guild=None also skips channel relocation)."""
        rnd = make_round(id=5, target_puzzle_id=0)
        p1 = make_puzzle(id=1, tags=[5])
        p2 = make_puzzle(id=2, tags=[5])
        puzzle_deletes, round_deletes, failed = await self._run(cog, hunt, rnd, puzzles=[p1, p2])
        assert puzzle_deletes == []
        assert 5 in round_deletes
        assert failed == []

    @pytest.mark.asyncio
    async def test_default_deletes_only_target_puzzle(self, cog, hunt):
        rnd = make_round(id=5, target_puzzle_id=1)
        target = make_puzzle(id=1, is_target=True, tags=[5])
        feeder = make_puzzle(id=2, tags=[5])
        puzzle_deletes, round_deletes, failed = await self._run(cog, hunt, rnd, puzzles=[target, feeder])
        assert puzzle_deletes == [1]
        assert 5 in round_deletes
```

- [ ] **Step 2: Run tests to verify the new ones fail (old ones still pass — they test unchanged `keep_puzzles=False` behavior)**

```bash
timeout 60 pipenv run python -m pytest tests/test_solve_equivalence.py -k TestWebDeleteRound -q > /tmp/test-output.log 2>&1
```

Expected: the 4 renamed-behavior tests PASS (current code ignores the not-yet-existing `keep_puzzles` kwarg... actually it will FAIL with `TypeError: delete_round() got an unexpected keyword argument 'keep_puzzles'` — that's fine, confirms the signature doesn't exist yet).

- [ ] **Step 3: Write the new keep_puzzles-with-guild test file**

Create `tests/test_delete_round_keep_puzzles.py`:

```python
"""delete_round's keep_puzzles=True (default) path: deletes only the round's
own target puzzle (or, for a target-less Group round, its dedicated chat
channel), relocates every other member puzzle's Discord channel into the
hunt's general category instead of deleting it, and leaves their DB
rows/sheets untouched. Forum-layout rounds ignore keep_puzzles entirely (no
"move to general category" equivalent for threads) and always full-delete.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY, SOLVE_DIVIDER
from tests.conftest import make_hunt, make_puzzle, make_round


def _svc():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def _general_category(cat_id=500, divider_pos=1):
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = divider_pos
    general_ch = MagicMock(); general_ch.name = "general"; general_ch.position = 0
    cat = MagicMock(id=cat_id)
    cat.channels = [general_ch, div]
    return cat


@pytest.mark.asyncio
async def test_keep_puzzles_deletes_target_and_relocates_feeder():
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=1)

    target = make_puzzle(id=1, is_target=True, channel_id=901, tags=[5])
    feeder = make_puzzle(id=2, channel_id=902, tags=[5], solved=False)

    target_ch = MagicMock(id=901); target_ch.delete = AsyncMock()
    feeder_ch = MagicMock(id=902); feeder_ch.category_id = 100; feeder_ch.edit = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [target_ch, feeder_ch]
    round_cat.delete = AsyncMock()
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {901: target_ch, 902: feeder_ch}.get(cid))

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [target, feeder]
        tgcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group)

    pdb.delete.assert_called_once_with(1)                                       # only the target's DB row
    feeder_ch.edit.assert_awaited_once_with(category=general_cat, position=0)    # unsolved: divider_pos(1) - 1
    target_ch.delete.assert_awaited_once()
    assert failed == []


@pytest.mark.asyncio
async def test_keep_puzzles_relocation_failure_reported():
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    feeder = make_puzzle(id=2, name="Feeder", channel_id=902, tags=[5])
    feeder_ch = MagicMock(id=902); feeder_ch.category_id = 100; feeder_ch.edit = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch]
    round_cat.text_channels = []             # no dedicated chat channel to find
    round_cat.delete = AsyncMock()
    general_cat = MagicMock(id=500)
    general_cat.channels = [MagicMock() for _ in range(MAX_CHANNELS_PER_CATEGORY)]  # full

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group)

    feeder_ch.edit.assert_not_awaited()
    feeder_ch.delete.assert_not_called()      # never destroyed — just left where it is
    assert failed == ["Feeder"]
    pdb.delete.assert_not_called()            # feeder's DB row untouched despite the relocation failure


@pytest.mark.asyncio
async def test_keep_puzzles_group_type_deletes_chat_channel_keeps_feeder():
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)
    tag_group.name = "Loose Ends"

    feeder = make_puzzle(id=2, channel_id=902, tags=[5])
    feeder_ch = MagicMock(id=902); feeder_ch.category_id = 100; feeder_ch.edit = AsyncMock()
    chat_ch = MagicMock(id=800); chat_ch.name = "loose-ends"; chat_ch.delete = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch, chat_ch]
    round_cat.text_channels = [chat_ch]
    round_cat.delete = AsyncMock()
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch, 800: chat_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group)

    chat_ch.delete.assert_awaited_once()
    feeder_ch.edit.assert_awaited_once_with(category=general_cat, position=0)
    pdb.delete.assert_not_called()
    assert failed == []


@pytest.mark.asyncio
async def test_forum_layout_ignores_keep_puzzles_full_deletes():
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="forum")
    tag_group = make_round(id=5, category_id=777, target_puzzle_id=0)

    p1 = make_puzzle(id=1, channel_id=901, tags=[5])
    p1_ch = MagicMock(id=901); p1_ch.delete = AsyncMock()
    forum_ch = MagicMock(id=777); forum_ch.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = []          # forum channel is NOT a category
    guild.get_channel = MagicMock(side_effect=lambda cid: {901: p1_ch, 777: forum_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [p1]

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=True)

    pdb.delete.assert_called_once_with(1)     # keep_puzzles=True ignored for forum — full delete
    forum_ch.delete.assert_awaited_once()
    assert failed == []
```

- [ ] **Step 4: Run to verify all new tests fail**

```bash
timeout 60 pipenv run python -m pytest tests/test_delete_round_keep_puzzles.py -q > /tmp/test-output.log 2>&1
```

Expected: FAIL (`TypeError: delete_round() got an unexpected keyword argument 'keep_puzzles'`).

- [ ] **Step 5: Rewrite `delete_round` in `bot/core/puzzle_service.py`**

Replace the entire method (lines 1142-1195) with:

```python
    @pushes_board()
    async def delete_round(self, guild, hunt, tag_group, keep_puzzles=True):
        """Delete a group's Discord category (including its solved-divider channel)
        and the group DB record. Per-puzzle/channel failures are isolated: one
        failure is logged and the run continues. Guild may be None (skips all
        Discord operations). Returns the list of puzzle names whose Discord
        channel could not be deleted or relocated.

        keep_puzzles (default True): only tear down the round's own footprint —
        its target puzzle (sheet + DB + channel) if it has one, or its dedicated
        chat channel if it doesn't (a target-less Group round) — and relocate
        every other member puzzle's Discord channel into the hunt's general
        category instead of deleting it (their DB rows/sheets are untouched
        either way). Pass keep_puzzles=False for the old all-or-nothing
        behavior that deletes every member puzzle. Forum-layout rounds always
        do the full delete regardless of keep_puzzles — forum threads have no
        "move to the general category" equivalent.
        """
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

        relocation_failed_channel_ids = set()
        if narrow and guild is not None:
            owned_category_ids = {tag_group.category_id} if tag_group.category_id else set()
            owned_category_ids |= {
                row.category_id for row in TagGroupCategoryDb.get_for_group(tag_group.id)
            }
            for puzzle in to_keep:
                if not puzzle.channel_id:
                    continue
                channel = guild.get_channel(puzzle.channel_id)
                if channel is None or channel.category_id not in owned_category_ids:
                    continue
                if not await category_layout.move_channel_to_general(
                    guild, hunt.category_id, channel, puzzle.solved
                ):
                    failed_channels.append(puzzle.name)
                    relocation_failed_channel_ids.add(puzzle.channel_id)

            general_ch = category_layout.group_chat_channel(guild, tag_group)
            if general_ch is not None:
                await self._safe_delete_channel(guild, general_ch.id)

        TagGroupJsonDb.delete(tag_group.id)

        if guild is not None:
            if hunt.layout_mode == "forum":
                # Forum mode: delete the forum channel (cascades its posts)
                forum_ch = guild.get_channel(tag_group.category_id)
                if forum_ch is not None:
                    try:
                        await forum_ch.delete(reason=self._DISCORD_DELETE_REASON)
                    except Exception:
                        logger.exception("delete_round: could not delete forum %s",
                                         tag_group.category_id)
            else:
                # Split rounds own several categories (unsolved/solved/overflow); delete
                # them all + drop the join rows. delete_group_categories returns False for
                # single-category rounds, which keep the legacy divider+category teardown.
                # keep_channel_ids protects any narrow-mode relocation failure from being
                # swept up in the blanket per-category channel delete.
                if not await category_layout.delete_group_categories(
                    guild, tag_group, TagGroupCategoryDb,
                    keep_channel_ids=relocation_failed_channel_ids,
                ) and tag_group.category_id:
                    category = discord.utils.get(guild.categories, id=tag_group.category_id)
                    if category is not None:
                        for ch in list(category.channels):
                            if getattr(ch, "name", "") == self._SOLVE_DIVIDER_NAME:
                                try:
                                    await ch.delete(reason=self._DISCORD_DELETE_REASON)
                                except Exception:
                                    logger.exception("delete_round: could not delete divider channel")
                        try:
                            await category.delete(reason=self._DISCORD_DELETE_REASON)
                        except Exception:
                            logger.exception("delete_round: could not delete category %s",
                                             tag_group.category_id)
        return failed_channels
```

- [ ] **Step 6: Run all delete_round-related tests**

```bash
timeout 120 pipenv run python -m pytest tests/test_solve_equivalence.py tests/test_delete_round_keep_puzzles.py tests/test_forum_layout.py tests/test_group_type_consolidation.py tests/test_create_service.py -q > /tmp/test-output.log 2>&1
```

Expected: PASS. If `test_delete_round_forum_deletes_forum_channel` in `tests/test_forum_layout.py` fails, check whether it needs `keep_puzzles=False` passed explicitly — it shouldn't, since forum mode ignores `keep_puzzles` regardless of value, but if it fails, update its call site to `await svc.delete_round(guild, _forum_hunt(), tag_group, keep_puzzles=False)` for clarity (no behavior change either way — forum always full-deletes).

- [ ] **Step 7: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_solve_equivalence.py tests/test_delete_round_keep_puzzles.py tests/test_forum_layout.py
git commit -m "feat(delete-round): add keep_puzzles scope (default: keep member puzzles)"
```

---

### Task 4: Bang command — pin `keep_puzzles=False`

**Files:**
- Modify: `bot/cogs/puzzles.py:3054`
- Test: `tests/test_group_type_consolidation.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_group_type_consolidation.py`, change the import line at the top:

```python
from tests.conftest import make_cog, make_ctx, make_hunt, make_round
```

Add a new test after `test_delete_round_allows_capstone_channel`:

```python
async def test_delete_round_bang_passes_keep_puzzles_false():
    from bot.cogs.puzzles import Puzzles
    tag_group = make_round(id=7)
    cog = make_cog(hunt=make_hunt(channel_id=999))
    cog.bot = MagicMock()
    cog.bot.get_channel.return_value = MagicMock(send=AsyncMock())
    cog.get_channel_type = lambda ctx: "Group"
    cog.get_tag_group = lambda ctx: tag_group
    cog._confirm_destructive = AsyncMock(return_value=True)
    cog.service.delete_round = AsyncMock(return_value=[])
    ctx = make_ctx()
    cog.environment = {ctx.message.id: {"puzzle": None, "tag_group": tag_group}}
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_tag_group.return_value = []
        await Puzzles.delete_round.callback(cog, ctx)
    cog.service.delete_round.assert_awaited_once()
    assert cog.service.delete_round.await_args.kwargs["keep_puzzles"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
timeout 60 pipenv run python -m pytest tests/test_group_type_consolidation.py -q > /tmp/test-output.log 2>&1
```

Expected: FAIL — `KeyError: 'keep_puzzles'` (the current call passes it positionally-absent, so `.kwargs` has no such key).

- [ ] **Step 3: Update the bang call site**

In `bot/cogs/puzzles.py`, inside `delete_round` (~line 3054), change:

```python
        failed = await self.service.delete_round(ctx.guild, self.get_hunt(ctx), tag_group)
```

to:

```python
        failed = await self.service.delete_round(
            ctx.guild, self.get_hunt(ctx), tag_group, keep_puzzles=False)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_group_type_consolidation.py -q > /tmp/test-output.log 2>&1
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_group_type_consolidation.py
git commit -m "fix(bang): delete_round keeps its full-delete behavior explicitly"
```

---

### Task 5: Web route — accept `keep_puzzles` in the request body

**Files:**
- Modify: `bot/web/actions.py:1293-1332` (`delete_tag_group`)
- Create: `tests/test_delete_round_web.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_delete_round_web.py` (mirrors the fixture in `tests/test_rename_group_web.py`):

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_round


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    cog = MagicMock()
    cog.service.delete_round = AsyncMock(return_value=[])
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    rdb = MagicMock()
    rdb.get_by_attr.return_value = make_round(id=5, name="Old")
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_tag_group", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.TagGroupJsonDb", rdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx, rdb=rdb)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_delete_round_defaults_to_keep_puzzles(web):
    resp = await web.client.delete("/api/tag_groups/5")
    assert resp.status_code == 200
    web.cog.service.delete_round.assert_awaited_once()
    assert web.cog.service.delete_round.await_args.kwargs["keep_puzzles"] is True


@pytest.mark.asyncio
async def test_delete_round_explicit_all_puzzles(web):
    resp = await web.client.delete("/api/tag_groups/5", json={"keep_puzzles": False})
    assert resp.status_code == 200
    assert web.cog.service.delete_round.await_args.kwargs["keep_puzzles"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
timeout 60 pipenv run python -m pytest tests/test_delete_round_web.py -q > /tmp/test-output.log 2>&1
```

Expected: FAIL — `KeyError: 'keep_puzzles'` (current route calls `delete_round(guild, hunt_obj, tag_group)` with no such kwarg).

- [ ] **Step 3: Update the route**

In `bot/web/actions.py`, inside `delete_tag_group`, find:

```python
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None

    try:
        failed_channels = await cog.service.delete_round(guild, hunt_obj, tag_group)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
```

Replace with:

```python
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None

    body = await request.get_json(silent=True) or {}
    keep_puzzles = bool(body.get("keep_puzzles", True))

    try:
        failed_channels = await cog.service.delete_round(
            guild, hunt_obj, tag_group, keep_puzzles=keep_puzzles)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
```

Also update the docstring above `delete_tag_group` to mention the new param — change:

```python
    """Permanently delete a tag group: all its puzzle DB records, sheet tabs, Discord
    channels, the group Discord category, and the group DB record. Per-puzzle
    failures are isolated. Returns failed_channels (names whose Discord channel
    could not be deleted) alongside ok:true so the caller can surface what remains.
    Organiser-gated, audited, board pushed."""
```

to:

```python
    """Delete a tag group. Body `keep_puzzles` (default true): keep every
    member puzzle's DB record/sheet/channel, deleting only the round's own
    target puzzle (or dedicated chat channel, for a target-less Group round)
    and relocating kept puzzles' channels into the hunt's general category;
    pass false to delete every member puzzle's DB record, sheet tab, and
    Discord channel too. Either way the group's Discord category and DB
    record are always deleted. Per-puzzle/channel failures are isolated.
    Returns failed_channels (names whose Discord channel could not be
    deleted or relocated) alongside ok:true so the caller can surface what
    remains. Organiser-gated, audited, board pushed."""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_delete_round_web.py -q > /tmp/test-output.log 2>&1
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/web/actions.py tests/test_delete_round_web.py
git commit -m "feat(web): delete_tag_group accepts keep_puzzles (default true)"
```

---

### Task 6: Frontend — `useDeleteRound` takes `keepPuzzles`

**Files:**
- Modify: `frontend/src/api.ts:455-467`

- [ ] **Step 1: Update the hook**

In `frontend/src/api.ts`, replace:

```ts
export interface DeleteRoundResult {
  ok: boolean;
  failed_channels?: string[];
}

export function useDeleteRound(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (roundId: number) =>
      apiSend<DeleteRoundResult>(`/api/tag_groups/${roundId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}
```

with:

```ts
export interface DeleteRoundResult {
  ok: boolean;
  failed_channels?: string[];
}

export function useDeleteRound(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ roundId, keepPuzzles }: { roundId: number; keepPuzzles: boolean }) =>
      apiSend<DeleteRoundResult>(`/api/tag_groups/${roundId}`, "DELETE", {
        keep_puzzles: keepPuzzles,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(frontend): useDeleteRound takes a keepPuzzles scope"
```

(No frontend test runner is configured in this project — `frontend/package.json` has no `test` script. Type-checking happens in Task 7's verification step, which covers both files together.)

---

### Task 7: Frontend — fix count, add scope radio to the confirm modal

**Files:**
- Modify: `frontend/src/components/BoardGroup.tsx`

- [ ] **Step 1: Add the `Radio` import**

Change:

```tsx
import {
  Accordion,
  ActionIcon,
  Badge,
  Box,
  Group,
  Table,
  Text,
  Tooltip,
} from "@mantine/core";
```

to:

```tsx
import {
  Accordion,
  ActionIcon,
  Badge,
  Box,
  Group,
  Radio,
  Table,
  Text,
  Tooltip,
} from "@mantine/core";
```

- [ ] **Step 2: Add scope state and rewrite `runDeleteRound`**

Change:

```tsx
  const deleteRoundMut = useDeleteRound(detail.hunt.uid);
  const [deleteRoundOpen, setDeleteRoundOpen] = useState(false);
  const isNarrow = useMediaQuery("(max-width: 768px)") ?? false;
```

to:

```tsx
  const deleteRoundMut = useDeleteRound(detail.hunt.uid);
  const [deleteRoundOpen, setDeleteRoundOpen] = useState(false);
  const [deleteAllPuzzles, setDeleteAllPuzzles] = useState(false);
  const isNarrow = useMediaQuery("(max-width: 768px)") ?? false;
```

Change:

```tsx
  function runDeleteRound() {
    deleteRoundMut.mutate(group.tag_group_id, {
      onSuccess: (data) => {
        if (data.failed_channels && data.failed_channels.length > 0) {
          notifications.show({
            message: `Round "${group.name}" deleted. Could not delete channels for: ${data.failed_channels.join(", ")}.`,
            color: "yellow",
            autoClose: 10000,
          });
        } else {
          notifications.show({ message: `Round "${group.name}" permanently deleted.`, color: "green" });
        }
        setDeleteRoundOpen(false);
      },
      onError: notifyError,
    });
  }
```

to:

```tsx
  function runDeleteRound() {
    deleteRoundMut.mutate(
      { roundId: group.tag_group_id, keepPuzzles: !deleteAllPuzzles },
      {
        onSuccess: (data) => {
          if (data.failed_channels && data.failed_channels.length > 0) {
            notifications.show({
              message: `Round "${group.name}" deleted. Could not delete/relocate channels for: ${data.failed_channels.join(", ")}.`,
              color: "yellow",
              autoClose: 10000,
            });
          } else {
            notifications.show({ message: `Round "${group.name}" permanently deleted.`, color: "green" });
          }
          setDeleteRoundOpen(false);
        },
        onError: notifyError,
      },
    );
  }
```

- [ ] **Step 3: Fix the count and add the scope radio in the modal**

Change:

```tsx
      <TypedConfirmModal
        opened={deleteRoundOpen}
        onClose={() => setDeleteRoundOpen(false)}
        onConfirm={runDeleteRound}
        title={`Delete round "${group.name}"`}
        confirmPhrase={group.name}
        confirmLabel="Delete round"
        loading={deleteRoundMut.isPending}
      >
        <Text size="sm" c="red">
          ⚠️ This permanently deletes <b>{group.num_feeders} puzzle{group.num_feeders === 1 ? "" : "s"}</b>,
          their Discord channels and Google sheet tabs, and the round&rsquo;s Discord category.
          This cannot be undone.
        </Text>
      </TypedConfirmModal>
```

to:

```tsx
      <TypedConfirmModal
        opened={deleteRoundOpen}
        onClose={() => setDeleteRoundOpen(false)}
        onConfirm={runDeleteRound}
        title={`Delete round "${group.name}"`}
        confirmPhrase={group.name}
        confirmLabel="Delete round"
        loading={deleteRoundMut.isPending}
      >
        <Radio.Group
          value={deleteAllPuzzles ? "all" : "keep"}
          onChange={(v) => setDeleteAllPuzzles(v === "all")}
          label="What should happen to this round's puzzles?"
        >
          <Group mt="xs">
            <Radio value="keep" label="Round only — keep its puzzles" />
            <Radio value="all" label={`Round + all ${group.puzzles.length} puzzle${group.puzzles.length === 1 ? "" : "s"}`} />
          </Group>
        </Radio.Group>
        {deleteAllPuzzles ? (
          <Text size="sm" c="red">
            ⚠️ This permanently deletes <b>{group.puzzles.length} puzzle{group.puzzles.length === 1 ? "" : "s"}</b>,
            their Discord channels and Google sheet tabs, and the round&rsquo;s Discord category.
            This cannot be undone.
          </Text>
        ) : (
          <Text size="sm" c="red">
            ⚠️ This permanently deletes the round&rsquo;s target puzzle (or dedicated chat channel,
            if it has no target) and its Discord category. Every other puzzle in this round keeps
            its sheet and Discord channel — those channels move into the hunt&rsquo;s general
            category. This cannot be undone.
          </Text>
        )}
      </TypedConfirmModal>
```

- [ ] **Step 4: Type-check the frontend**

```bash
cd frontend && timeout 120 npx tsc --noEmit > /tmp/test-output.log 2>&1; cd ..
```

Expected: no errors. Read `/tmp/test-output.log` if it fails.

- [ ] **Step 5: Manually verify in the browser**

Per project convention (`AGENTS.md` / global instructions): start the dev server, open a hunt with at least one round that has feeder puzzles and a target, and:
- Open the delete-round modal — confirm the puzzle count in "Round + all N puzzles" matches the round's total member count (target included).
- Confirm "Round only" is selected by default and its warning text shows.
- Run "Round only" on a test round; confirm the round disappears from the board, its target/chat channel + category are gone in Discord, and its feeder puzzles are still present elsewhere (e.g. in "Unassigned Feeders" or wherever they now land) with working channels.
- Run "Round + all N puzzles" on another test round; confirm full deletion still works as before.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/BoardGroup.tsx
git commit -m "feat(frontend): delete-round modal — fix count, add keep-puzzles scope"
```

---

### Task 8: Final verification

- [ ] **Step 1: Run the full backend test suite**

```bash
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1
```

Expected: PASS (no regressions). Read the tail of `/tmp/test-output.log` for a summary; if anything unrelated to this work is already failing on `main`, note it rather than trying to fix it here.

- [ ] **Step 2: Review the full diff**

```bash
git diff main --stat
```

Confirm only the files listed across Tasks 1-7 changed, plus the two spec/plan docs.

- [ ] **Step 3: Update `.ai/active/` handoff (if it exists) or report completion**

If `.ai/active/session-summary.md` exists for this task, update its Status line and Next steps to "done, ready for review." Otherwise, summarize for the user: what changed, commits made, tests run, and the still-open "must be done from the main group channel" bug (Task 3's design-doc note) awaiting the requested live diagnostic (`Channel type: ...` console log or `!info` output).
