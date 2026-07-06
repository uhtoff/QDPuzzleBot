# Extract Target To Category — Implementation Plan (Plan 1 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `!extract_target_to_category` organiser command that gives a channel-home meta its own Discord category, moving its target puzzle + exclusive plain feeders and flipping the group from channel-home to category-owning.

**Architecture:** A pure edge-selection function (`bot/core/target_extract.py`) decides *which* puzzles move; a `PuzzleService.extract_target_to_category` orchestration method clones a category from the hunt template, moves the channels, flips `TagGroupData.category_id` from the target channel id to the new category id, and triggers consolidation on vacated source categories; a thin cog command wraps it.

**Tech Stack:** Python, discord.py, MySQL DAOs, pytest (`pipenv run python -m pytest`), `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-06-extract-target-to-category-design.md` (see "Shared primitives", "Feature 1", "Channel-ordering invariant").

**Test command (always):**
```bash
timeout 180 pipenv run python -m pytest tests/test_target_extract.py -q > /tmp/test.log 2>&1
```
(A timeout-killed run exits 124; read `/tmp/test.log`.)

**Type-check gate (CI-enforced):** CI runs `pipenv run basedpyright bot/` (mode `standard`) and fails on **any** error. After every task that adds or changes code under `bot/`, run:
```bash
timeout 180 pipenv run basedpyright bot/core/puzzle_service.py bot/core/target_extract.py > /tmp/pyright.log 2>&1
```
and confirm **0 errors** (pre-existing `reportUnused*` *warnings* elsewhere are not errors and don't fail CI — do not "fix" unrelated ones). Only `bot/` is type-checked; `tests/` is not, so unused test params/`reportAttributeAccessIssue`/`reportCallIssue` on `.callback(...)` in test files don't matter to CI. Watch specifically for `reportOptionalMemberAccess`: DAO getters like `PuzzleJsonDb.get_by_attr(...)` return `PuzzleData | None`, so narrow with a `None` guard before using the result.

**Lint gate (also CI-enforced):** CI runs `pipenv run ruff check` (pyflakes `F` rules, **repo-wide including `tests/`**). After each task, run `timeout 120 pipenv run ruff check <changed files> > /tmp/ruff.log 2>&1` and expect "All checks passed!". The most common trap is an unused import (`F401`) in a new test — only import what the test actually uses.

---

## File Structure

- **Create** `bot/core/target_extract.py` — pure selection logic (no Discord/DB). One responsibility: given loaded models, return the puzzles to move.
- **Create** `tests/test_target_extract.py` — unit tests for the pure function.
- **Modify** `bot/core/puzzle_service.py` — add `extract_target_to_category(self, guild, hunt, meta)`; import `select_extract_puzzles`.
- **Create** `tests/test_extract_service.py` — service-level tests (mocked Discord + DAOs).
- **Modify** `bot/cogs/puzzles.py` — add the `extract_target_to_category` command.
- **Create** `tests/test_extract_command.py` — command-callback test.
- **Modify** `docs/commands.md` — add the command to the reference table.
- **Modify** `docs/user-guide.md` — add a short prose mention for organisers.

---

## Task 1: Pure extract-set selection

**Files:**
- Create: `bot/core/target_extract.py`
- Test: `tests/test_target_extract.py`

The rule (from the spec): move the meta's **target puzzle** plus every **exclusive plain feeder** — a member of the meta that feeds/informs it, feeds/informs *no other* group, and is *not itself any group's target* (sub-metas stay behind).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_target_extract.py`:

```python
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import (
    TagGroupData, EDGE_FEEDS, EDGE_INFORMS, EDGE_TARGET, EDGE_NONE,
)
from bot.core.target_extract import select_extract_puzzles

META_ID = 10


def _puzzle(pid, edges):
    """edges: list of (tag_group_id, edge_type)."""
    p = PuzzleData(name=f"puzzle-{pid}")
    p.id = pid
    for gid, edge in edges:
        p.add_edge(gid, edge)
    return p


def _meta(target_id=1):
    m = TagGroupData(name="the-meta")
    m.id = META_ID
    m.target_puzzle_id = target_id
    m.type = "Metapuzzle"
    return m


def test_target_puzzle_is_first():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    result = select_extract_puzzles(meta, target, [target], target_puzzle_ids={1})
    assert [p.id for p in result] == [1]


def test_exclusive_plain_feeder_is_selected():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    feeder = _puzzle(2, [(META_ID, EDGE_FEEDS)])
    result = select_extract_puzzles(meta, target, [target, feeder], target_puzzle_ids={1})
    assert [p.id for p in result] == [1, 2]


def test_informs_feeder_is_selected():
    meta = _meta()
    meta.type = "Capstone"
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    feeder = _puzzle(2, [(META_ID, EDGE_INFORMS)])
    result = select_extract_puzzles(meta, target, [target, feeder], target_puzzle_ids={1})
    assert [p.id for p in result] == [1, 2]


def test_shared_feeder_is_left_behind():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    shared = _puzzle(2, [(META_ID, EDGE_FEEDS), (99, EDGE_FEEDS)])
    result = select_extract_puzzles(meta, target, [target, shared], target_puzzle_ids={1})
    assert [p.id for p in result] == [1]


def test_sub_meta_is_left_behind():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    sub_meta = _puzzle(3, [(META_ID, EDGE_FEEDS)])  # feeds meta but is itself a target
    result = select_extract_puzzles(meta, target, [target, sub_meta], target_puzzle_ids={1, 3})
    assert [p.id for p in result] == [1]


def test_non_feeder_member_is_left_behind():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    pooled = _puzzle(4, [(META_ID, EDGE_NONE)])
    result = select_extract_puzzles(meta, target, [target, pooled], target_puzzle_ids={1})
    assert [p.id for p in result] == [1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_target_extract.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.core.target_extract'`.

- [ ] **Step 3: Write the implementation**

Create `bot/core/target_extract.py`:

```python
"""Pure selection logic for extracting a channel-home meta into its own category.

No Discord or DB access: it takes already-loaded model objects and returns the
subset of puzzles to move, so it is unit-testable without mocks. See
docs/superpowers/specs/2026-07-06-extract-target-to-category-design.md.
"""
from bot.store.tag_group_data import EDGE_FEEDS, EDGE_INFORMS

_MEMBERSHIP_EDGES = frozenset({EDGE_FEEDS, EDGE_INFORMS})


def select_extract_puzzles(meta, target_puzzle, member_puzzles, target_puzzle_ids):
    """Puzzles to move when extracting *meta* into its own category.

    Returns *target_puzzle* first, then every exclusive plain feeder of *meta*:
    a member that feeds/informs *meta*, feeds/informs no other group, and is not
    itself any group's target (sub-metas are boundaries — left in place).

    - meta: the TagGroupData being extracted.
    - target_puzzle: meta's target PuzzleData (placed first).
    - member_puzzles: PuzzleData tagged to meta (e.g.
      PuzzleJsonDb.get_all_from_tag_group(meta.id)); each carries .tags/.tag_edges.
    - target_puzzle_ids: set of puzzle ids that are some group's target in this
      hunt, so sub-metas are recognised without a per-puzzle DB lookup.
    """
    result = [target_puzzle]
    for p in member_puzzles:
        if p.id == target_puzzle.id:
            continue
        if p.id in target_puzzle_ids:
            continue  # a target/sub-meta — leave it (and its own feeders) behind
        if not (p.edges_for(meta.id) & _MEMBERSHIP_EDGES):
            continue  # not actually a feeder of meta
        if _feeds_another_group(p, meta.id):
            continue  # shared feeder — can't pull it out of the other meta
        result.append(p)
    return result


def _feeds_another_group(puzzle, meta_id):
    """True if *puzzle* feeds/informs any group other than *meta_id*."""
    for gid in puzzle.tags:
        if gid == meta_id:
            continue
        if puzzle.edges_for(gid) & _MEMBERSHIP_EDGES:
            return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_target_extract.py -q > /tmp/test.log 2>&1`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/core/target_extract.py tests/test_target_extract.py
git commit -m "feat(extract): pure extract-set selection for channel-home metas

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `PuzzleService.extract_target_to_category`

**Files:**
- Modify: `bot/core/puzzle_service.py` (add method near `convert_to_meta`, ~L1794; add import at the existing import block ~L23)
- Test: `tests/test_extract_service.py`

Orchestrates: guard → gather set → clone category from hunt template → move channels (ordering: target, unsolved feeders, divider, solved feeders) → flip `category_id` → consolidate vacated sources.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_extract_service.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import TagGroupData, EDGE_FEEDS, EDGE_TARGET


def _puzzle(pid, channel_id, edges, status="unsolved"):
    p = PuzzleData(name=f"puzzle-{pid}")
    p.id = pid
    p.channel_id = channel_id
    p.status = status
    for gid, edge in edges:
        p.add_edge(gid, edge)
    return p


def _make_channel(cid, source_category):
    ch = MagicMock()
    ch.id = cid
    ch.category = source_category
    ch.edit = AsyncMock()
    return ch


def _fresh_lock(monkeypatch):
    import bot.core.category_layout as cl
    monkeypatch.setattr(cl, "layout_lock", asyncio.Lock())


@pytest.fixture
def env(monkeypatch):
    _fresh_lock(monkeypatch)

    hunt = MagicMock()
    hunt.id = 500
    hunt.category_id = 700
    hunt.name = "Test Hunt"

    meta = TagGroupData(name="spooky-meta")
    meta.id = 10
    meta.type = "Metapuzzle"
    meta.target_puzzle_id = 1
    meta.category_id = 801  # channel-home: points at the target puzzle's channel

    # host category the meta currently shares
    host = MagicMock(); host.id = 900; host.position = 3
    template = MagicMock(); template.id = 700; template.position = 0
    template.clone = AsyncMock()
    new_cat = MagicMock(); new_cat.id = 950; new_cat.name = "spooky-meta"
    new_cat.edit = AsyncMock()
    template.clone.return_value = new_cat

    guild = MagicMock()
    guild.categories = [template, host]

    # channels: target (801) + exclusive feeder (802), both currently under host
    target_ch = _make_channel(801, host)
    feeder_ch = _make_channel(802, host)
    guild.get_channel = MagicMock(side_effect=lambda cid: {801: target_ch, 802: feeder_ch}.get(cid))

    target = _puzzle(1, 801, [(10, EDGE_TARGET)])
    feeder = _puzzle(2, 802, [(10, EDGE_FEEDS)])

    svc = PuzzleService(MagicMock())  # __init__(self, bot, notifier=None)
    return dict(svc=svc, guild=guild, hunt=hunt, meta=meta, template=template,
                host=host, new_cat=new_cat, target=target, feeder=feeder,
                target_ch=target_ch, feeder_ch=feeder_ch)


async def test_extract_clones_from_template_and_flips_category_id(env, monkeypatch):
    svc, guild, hunt, meta = env["svc"], env["guild"], env["hunt"], env["meta"]
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.get_or_create_channel", AsyncMock()), \
         patch("bot.core.puzzle_service.solve_divider_position", return_value=False), \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.return_value = None  # meta does NOT own a real category
        mcl.maybe_consolidate_category = MagicMock()
        mpdb.get_by_attr.return_value = env["target"]
        mpdb.get_all_from_tag_group.return_value = [env["target"], env["feeder"]]
        mrdb.get_all.return_value = [meta]

        new_cat = await svc.extract_target_to_category(guild, hunt, meta)

    env["template"].clone.assert_awaited_once()
    assert new_cat is env["new_cat"]
    assert meta.category_id == env["new_cat"].id       # flipped channel-home -> category
    mrdb.commit.assert_called_with(meta)
    # both the target channel and the exclusive feeder channel were reparented
    env["target_ch"].edit.assert_awaited()
    env["feeder_ch"].edit.assert_awaited()
    moved_to = {c.kwargs.get("category") for c in env["target_ch"].edit.await_args_list}
    assert env["new_cat"] in moved_to


async def test_extract_triggers_consolidation_on_vacated_source(env):
    svc, guild, hunt, meta = env["svc"], env["guild"], env["hunt"], env["meta"]
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.get_or_create_channel", AsyncMock()), \
         patch("bot.core.puzzle_service.solve_divider_position", return_value=False), \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        # None for the meta's own home; the host category for the vacated-source lookup
        mcl._category_by_id.side_effect = lambda g, cid: None if cid == meta.category_id else env["host"]
        mcl.maybe_consolidate_category = MagicMock()
        mpdb.get_by_attr.return_value = env["target"]
        mpdb.get_all_from_tag_group.return_value = [env["target"], env["feeder"]]
        mrdb.get_all.return_value = [meta]

        await svc.extract_target_to_category(guild, hunt, meta)

    mcl.maybe_consolidate_category.assert_called()  # host category consolidated


async def test_extract_refuses_when_already_owns_category(env):
    svc, guild, hunt, meta = env["svc"], env["guild"], env["hunt"], env["meta"]
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.return_value = MagicMock()  # already a real category
        with pytest.raises(RuntimeError, match="already_own_category"):
            await svc.extract_target_to_category(guild, hunt, meta)


async def test_extract_refuses_at_category_cap(env):
    svc, guild, hunt, meta = env["svc"], env["guild"], env["hunt"], env["meta"]
    guild.categories = [MagicMock() for _ in range(50)]  # == MAX_CATEGORIES
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.return_value = None
        with pytest.raises(RuntimeError, match="no_category_space"):
            await svc.extract_target_to_category(guild, hunt, meta)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_extract_service.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleService' object has no attribute 'extract_target_to_category'`.

- [ ] **Step 3: Add the import**

In `bot/core/puzzle_service.py`, add to the import block (near the other `from bot.core import ...` around L23):

```python
from bot.core.target_extract import select_extract_puzzles
```

- [ ] **Step 4: Write the method**

Add to `PuzzleService` (place it just after `convert_to_meta`, ~L1794):

```python
async def extract_target_to_category(self, guild, hunt, meta):
    """Give a channel-home *meta* its own Discord category.

    Moves meta's target channel + exclusive plain feeders into a freshly cloned
    category, flips ``meta.category_id`` from the target channel id to the new
    category id (so the group becomes an ordinary single-category owner), and
    triggers consolidation on any source categories it emptied.

    Raises RuntimeError("already_own_category") if meta already owns a real
    category, "no_category_space" at the guild category cap, or
    "no_template_category" if the hunt template category is missing.
    Returns the new discord category.
    """
    if category_layout._category_by_id(guild, meta.category_id) is not None:
        raise RuntimeError("already_own_category")
    if len(guild.categories) >= MAX_CATEGORIES:
        raise RuntimeError("no_category_space")
    ref_category = discord.utils.get(guild.categories, id=hunt.category_id)
    if ref_category is None:
        raise RuntimeError("no_template_category")

    # Which puzzles move (pure edge logic; data from the DAOs).
    target_puzzle = PuzzleJsonDb.get_by_attr(id=meta.target_puzzle_id)
    members = PuzzleJsonDb.get_all_from_tag_group(meta.id)
    all_groups = TagGroupJsonDb.get_all(hunt.id)
    target_puzzle_ids = {g.target_puzzle_id for g in all_groups if g.target_puzzle_id}
    puzzles = select_extract_puzzles(meta, target_puzzle, members, target_puzzle_ids)

    sources = set()
    async with category_layout.layout_lock:
        new_name = clean_category_name(meta.name)
        if discord.utils.get(guild.categories, name=new_name) is not None:
            new_name = new_name + " — " + clean_category_name(hunt.name)
        new_category = await ref_category.clone(name=new_name)
        await new_category.edit(position=ref_category.position + 1)
        if SOLVE_CATEGORY is False:
            await get_or_create_channel(
                guild=guild, category=new_category, channel_name=SOLVE_DIVIDER,
                channel_type="text", position=500,
            )
        divider_pos = solve_divider_position(new_category)

        # Ordering: target first, then unsolved feeders (above divider), then
        # solved feeders (below divider). Sub-metas never appear here — they are
        # excluded by select_extract_puzzles.
        unsolved = [p for p in puzzles if p.status != "solved"]
        solved = [p for p in puzzles if p.status == "solved"]
        pos = 0
        for p in unsolved:
            channel = guild.get_channel(p.channel_id)
            if channel is None:
                continue
            src = getattr(channel, "category", None)
            if src is not None and src.id != new_category.id:
                sources.add(src.id)
            await channel.edit(category=new_category, position=pos)
            pos += 1
        for p in solved:
            channel = guild.get_channel(p.channel_id)
            if channel is None:
                continue
            src = getattr(channel, "category", None)
            if src is not None and src.id != new_category.id:
                sources.add(src.id)
            below = (divider_pos + 1) if divider_pos is not False else pos
            await channel.edit(category=new_category, position=below)
            pos += 1

        meta.category_id = new_category.id
        TagGroupJsonDb.commit(meta)

    # Consolidate each vacated source so the previous owner folds/un-splits.
    for cid in sources:
        src = category_layout._category_by_id(guild, cid)
        if src is not None:
            category_layout.maybe_consolidate_category(guild, src)
    return new_category
```

Note: `discord`, `MAX_CATEGORIES`, `SOLVE_CATEGORY`, `SOLVE_DIVIDER`, `clean_category_name`, `get_or_create_channel`, `solve_divider_position`, `category_layout`, `PuzzleJsonDb`, `TagGroupJsonDb` are all already imported in `puzzle_service.py` (verify against the existing import block; do not re-import).

- [ ] **Step 5: Run tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_extract_service.py -q > /tmp/test.log 2>&1`
Expected: PASS (4 passed). If a mock detail mismatches (e.g. `edit` kwargs), read `/tmp/test.log` and align the assertion with the call — do not weaken a behavioural assertion.

- [ ] **Step 6: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_extract_service.py
git commit -m "feat(extract): PuzzleService.extract_target_to_category orchestration

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `!extract_target_to_category` command

**Files:**
- Modify: `bot/cogs/puzzles.py` (add command near `convert_to_metapuzzle`, ~L1985)
- Test: `tests/test_extract_command.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract_command.py` (note: no `import pytest` — these tests use neither `pytest.raises` nor markers, and CI's `ruff check` flags the unused import):

```python
from unittest.mock import AsyncMock, MagicMock

from bot.cogs.puzzles import Puzzles
from bot.store.tag_group_data import TagGroupData


def _cog_and_ctx(tag_group, hunt):
    cog = MagicMock()  # not spec=Puzzles: `service` is an instance attr, not on the class
    cog.get_hunt = MagicMock(return_value=hunt)
    cog.get_tag_group = MagicMock(return_value=tag_group)
    cog.service = MagicMock()
    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.send = AsyncMock()
    return cog, ctx


async def test_extract_command_calls_service_and_acks():
    meta = TagGroupData(name="spooky-meta")
    meta.id = 10
    meta.target_puzzle_id = 1
    hunt = MagicMock()
    cog, ctx = _cog_and_ctx(meta, hunt)
    new_cat = MagicMock(); new_cat.name = "spooky-meta"
    cog.service.extract_target_to_category = AsyncMock(return_value=new_cat)

    await Puzzles.extract_target_to_category.callback(cog, ctx)

    cog.service.extract_target_to_category.assert_awaited_once_with(ctx.guild, hunt, meta)
    ctx.send.assert_awaited()
    assert "spooky-meta" in ctx.send.await_args.args[0]


async def test_extract_command_refuses_outside_a_meta():
    hunt = MagicMock()
    cog, ctx = _cog_and_ctx(None, hunt)  # no tag_group in context
    cog.service.extract_target_to_category = AsyncMock()

    await Puzzles.extract_target_to_category.callback(cog, ctx)

    cog.service.extract_target_to_category.assert_not_awaited()
    ctx.send.assert_awaited()


async def test_extract_command_maps_runtime_error():
    meta = TagGroupData(name="m"); meta.id = 10; meta.target_puzzle_id = 1
    hunt = MagicMock()
    cog, ctx = _cog_and_ctx(meta, hunt)
    cog.service.extract_target_to_category = AsyncMock(
        side_effect=RuntimeError("no_category_space"))

    await Puzzles.extract_target_to_category.callback(cog, ctx)

    ctx.send.assert_awaited()
    assert "category" in ctx.send.await_args.args[0].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 180 pipenv run python -m pytest tests/test_extract_command.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: type object 'Puzzles' has no attribute 'extract_target_to_category'`.

- [ ] **Step 3: Write the command**

Add to the `Puzzles` cog (near `convert_to_metapuzzle`, ~L1985):

```python
@commands.hybrid_command(
    description="Give this meta its own category (moves its target + exclusive feeders)")
@commands.has_any_role(*ORGANISER_ROLES)
@commands.has_permissions(manage_channels=True)
async def extract_target_to_category(self, ctx):
    """*Move this meta's target puzzle and its exclusive feeders into their own category.*

    Usage: `!extract_target_to_category`
    Run in the meta's own target-puzzle channel. Organisers/admins only.
    Bang-only: no web-dashboard equivalent, by design (Discord plumbing)."""
    hunt = self.get_hunt(ctx)
    meta = self.get_tag_group(ctx)
    if hunt is None or meta is None or not meta.has_target:
        return await ctx.send(
            ":x: Run this in a meta's own puzzle channel.")
    try:
        new_category = await self.service.extract_target_to_category(
            ctx.guild, hunt, meta)
    except RuntimeError as exc:
        messages = {
            "already_own_category":
                ":information_source: This meta already has its own category.",
            "no_category_space":
                ":x: No free category slot on this server.",
            "no_template_category":
                ":x: Couldn't find the hunt's template category.",
        }
        return await ctx.send(messages.get(str(exc), f":x: Couldn't extract ({exc})."))
    await ctx.send(
        f":white_check_mark: Extracted **{meta.name}** into its own category "
        f"**{new_category.name}**.")
```

Verify `ORGANISER_ROLES` is already imported at the top of `puzzles.py` (it is, alongside `ADMIN_ROLES`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 180 pipenv run python -m pytest tests/test_extract_command.py -q > /tmp/test.log 2>&1`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full new-test set + a quick regression**

Run: `timeout 300 pipenv run python -m pytest tests/test_target_extract.py tests/test_extract_service.py tests/test_extract_command.py tests/test_convert_service.py tests/test_category_layout.py -q > /tmp/test.log 2>&1`
Expected: PASS (all). If `test_convert_service`/`test_category_layout` regress, the new import or method broke something — read `/tmp/test.log`.

- [ ] **Step 5b: Type-check gate**

Run: `timeout 180 pipenv run basedpyright bot/cogs/puzzles.py > /tmp/pyright.log 2>&1`
Expected: **0 errors** (pre-existing warnings in the file are fine). If the new command introduces a `reportOptionalMemberAccess` (e.g. from `self.get_hunt(ctx)` / `self.get_tag_group(ctx)` returning `... | None`), the `if hunt is None or meta is None or not meta.has_target:` guard already narrows both before use — keep that guard ahead of any attribute access.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_extract_command.py
git commit -m "feat(extract): !extract_target_to_category command

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Documentation

**Files:**
- Modify: `docs/commands.md` (the "Rounds & groups" table, after the `!delete_round` row ~L45)
- Modify: `docs/user-guide.md` (the "Changing your mind about what a puzzle is" section ~L126-131)

No tests (documentation only). `docs/user-guide.md` is the source for the illustrated guide served at `/guide.html` — it is generated by `frontend/build-guide.mjs` at build/deploy time (no committed `guide.html` artifact to rebuild), so editing the markdown is sufficient.

- [ ] **Step 1: Add the command to `docs/commands.md`**

In the "## Rounds & groups" table, add this row immediately **after** the `!delete_round` row (keep the existing 6-column format `| Command | Aliases | Arguments | Where | Who | Does |`):

```markdown
| `!extract_target_to_category` | | — | meta's target-puzzle channel | organisers/admins | Give this meta its own Discord category, moving its target puzzle and the puzzles that feed **only** it into a fresh category. Shared feeders and nested metas stay put. Useful when a meta promoted in place has outgrown the category it was sharing. Bang-only, by design. |
```

- [ ] **Step 2: Add a prose mention to `docs/user-guide.md`**

In the "### Changing your mind about what a puzzle is" section, add this paragraph immediately **after** the existing `!convert_to_*` paragraph (the one ending "Feeders and their tags survive the conversion.") and **before** the "Renames:" paragraph:

```markdown
Giving a meta its own category: `!extract_target_to_category` (run in the meta's own
puzzle channel) moves that meta and the puzzles that feed *only* it into a fresh Discord
category — useful when a meta you promoted in place has outgrown the category it was
sharing. Shared feeders and nested metas stay where they are.
```

- [ ] **Step 3: Sanity-check the edits**

Run: `git diff --stat docs/commands.md docs/user-guide.md`
Expected: both files show insertions only. Eyeball the two hunks to confirm the table row has exactly 6 `|`-delimited columns and the guide paragraph sits between the convert and renames paragraphs.

- [ ] **Step 4: Commit**

```bash
git add docs/commands.md docs/user-guide.md
git commit -m "docs: document !extract_target_to_category

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Verification

- [ ] Full suite green:
```bash
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```
Read `/tmp/test.log`; expect no new failures vs. the pre-change baseline.
- [ ] Type-check gate green (the CI command):
```bash
timeout 300 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1
```
Read `/tmp/pyright.log`; expect **0 errors** (warnings are fine). This must be clean or CI fails.
- [ ] Lint gate green (the CI command):
```bash
timeout 180 pipenv run ruff check > /tmp/ruff.log 2>&1
```
Read `/tmp/ruff.log`; expect "All checks passed!". This must be clean or CI fails.
- [ ] Manual smoke (optional, live bot): in a promoted-in-place meta's channel, run `!extract_target_to_category`; confirm a new category named after the meta appears, the meta + its exclusive feeders move in, shared feeders and sub-metas stay put, and the roll-up sheet is unchanged.

## Rollback / recovery notes

- Pure function (Task 1) and the new command (Task 3) are additive — reverting their commits is clean.
- Task 2 adds one method + one import to `puzzle_service.py`; revert that commit to remove it. No schema, no DAO, no migration changes anywhere in this plan.

## Out of scope (later plans)

- Emoji target-identification prefixes + backfill (Plan 2).
- `!consolidate_to` (Plan 3).
- Auto-extract on overflow (Plan 4).
- The ordering invariant is implemented here for the extracted category only; wider best-effort ordering is not retrofitted onto existing flows.
