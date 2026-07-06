# Consolidate To — Implementation Plan (Plan 3 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add `!consolidate_to <destination>` — the inverse of `!extract_target_to_category` — folding a category-owning meta back into a named destination category (deleting the vacated category), reverting the meta to "channel-home", trading a scarce category slot for channel density.

**Architecture:** A `PuzzleService.consolidate_to(guild, hunt, meta, destination, force=False)` drains the meta's channels into the destination via the existing `category_layout.move_channel_to_owner` (which auto-splits/places by solved state), deletes the emptied owned categories, and sets `meta.category_id` to its target puzzle's channel id (channel-home). If the move would push the destination past the soft cap it raises `RuntimeError("would_overflow")` unless `force`; the cog catches that, confirms via `ConfirmView`, and retries with `force=True`. Nested channel-home metas need no bookkeeping — their `category_id` is a channel id, so they ride along when their channel is reparented.

**Tech Stack:** Python, discord.py, MySQL DAOs, pytest, `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-06-extract-target-to-category-design.md` ("Feature 3 — `!consolidate_to`").

**CI gates (after every task touching `bot/`):**
```bash
timeout 300 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1   # 0 errors
timeout 180 pipenv run ruff check > /tmp/ruff.log 2>&1             # All checks passed!
```
`tests/` isn't type-checked; ruff (pyflakes F) is repo-wide (no unused imports in tests). Wrap pytest in `timeout`, write to a file.

**Verified seams (from the codebase):**
- Reuse `extract_target_to_category` (`bot/core/puzzle_service.py`, ~L1873) as the inverse template: `target_puzzle = PuzzleJsonDb.get_by_attr(id=meta.target_puzzle_id)` gives the channel-home id (`target_puzzle.channel_id`); the monotonic `pos` ordering loop; `async with category_layout.layout_lock`.
- `category_layout`: `_category_by_id(guild, cid)`, `primary_category(guild, group)` (None for channel-home), `group_categories(owner, dao)` → `dao.get_for_group(owner.id)`, `is_split(owner, dao)`, `move_channel_to_owner(guild, owner, dao, channel, solved)` (True moved / False no category; auto-splits legacy owners), `pick_unsolved_category`, `split_group`, `delete_group_categories(guild, owner, dao, keep_channel_ids=...)` (deletes channels+categories+rows; returns False if not split), `puzzle_count(cat)`, `CATEGORY_SOFT_LIMIT`, `maybe_consolidate_category`, `layout_lock`.
- Drain-then-teardown template: `_teardown_converted_group_categories` (`puzzle_service.py:1379`) and `archive_round` (`bot/cogs/puzzles.py:3249-3272`) — the split branch iterates `group_categories`, the else branch iterates `primary_category(...).channels`.
- Destination resolution: `self.resolve_target_group(ctx, meta_code)` (`puzzles.py:308`) → a `TagGroupData` or None (sends help). Slash autocomplete: `group_code_autocomplete` (`puzzles.py:233`).
- Placement into destination: `move_channel_to_owner(guild, destination, TagGroupCategoryDb, channel, solved)` — same call `_relocate_kept_puzzle` uses (`puzzle_service.py:1607`).
- Confirm UI: `_confirm_destructive(self, ctx, description, *, sheets_deleted=True)` (`puzzles.py:3516`) → returns True only on confirm. Pass `sheets_deleted=False`.
- `TagGroupCategoryDb`: `get_for_group`, `get_by_category`, `delete_for_group`, `make_row`; already imported in puzzle_service.
- `PuzzleData.status == "solved"` distinguishes solved; `PuzzleJsonDb.get_all_from_tag_group(meta.id)`.

---

## File Structure

- **Modify** `bot/core/puzzle_service.py` — add `consolidate_to` + a small `_channels_to_consolidate` helper. Test `tests/test_consolidate_service.py`.
- **Modify** `bot/cogs/puzzles.py` — add `!consolidate_to` command with destination resolution + overflow confirm. Test `tests/test_consolidate_command.py`.
- **Modify** `docs/commands.md`, `docs/user-guide.md`.

---

## Task 1: `PuzzleService.consolidate_to`

**Files:** Modify `bot/core/puzzle_service.py`; Test `tests/test_consolidate_service.py`.

The service does: collect channels to move → overflow check (raise unless force) → move each into the destination → delete emptied owned categories → revert meta to channel-home → consolidate the (now smaller) destination if wanted.

- [ ] **Step 1: Write the failing tests** — `tests/test_consolidate_service.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import TagGroupData, EDGE_FEEDS, EDGE_TARGET


def _fresh_lock(monkeypatch):
    import bot.core.category_layout as cl
    monkeypatch.setattr(cl, "layout_lock", asyncio.Lock())


def _puzzle(pid, channel_id, edges, status="unsolved"):
    p = PuzzleData(name=f"p{pid}")
    p.id = pid
    p.channel_id = channel_id
    p.status = status
    for gid, edge in edges:
        p.add_edge(gid, edge)
    return p


def _chan(cid):
    ch = MagicMock(); ch.id = cid; ch.edit = AsyncMock()
    return ch


@pytest.fixture
def env(monkeypatch):
    _fresh_lock(monkeypatch)
    hunt = MagicMock(); hunt.id = 500
    # category-owning meta: category_id is a REAL category id (900)
    meta = TagGroupData(name="spooky-meta"); meta.id = 10
    meta.type = "Metapuzzle"; meta.target_puzzle_id = 1; meta.category_id = 900
    dest = TagGroupData(name="round-a"); dest.id = 20
    dest.type = "Metapuzzle"; dest.target_puzzle_id = 3; dest.category_id = 800
    owned_cat = MagicMock(); owned_cat.id = 900
    target_ch = _chan(801); feeder_ch = _chan(802)
    owned_cat.channels = [target_ch, feeder_ch]
    dest_cat = MagicMock(); dest_cat.id = 800; dest_cat.channels = []
    guild = MagicMock(); guild.categories = [owned_cat, dest_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {801: target_ch, 802: feeder_ch}.get(cid))
    target = _puzzle(1, 801, [(10, EDGE_TARGET)])
    feeder = _puzzle(2, 802, [(10, EDGE_FEEDS)])
    svc = PuzzleService(MagicMock())
    return dict(svc=svc, guild=guild, hunt=hunt, meta=meta, dest=dest,
                owned_cat=owned_cat, dest_cat=dest_cat, target=target, feeder=feeder,
                target_ch=target_ch, feeder_ch=feeder_ch)


async def test_consolidate_moves_channels_reverts_to_channel_home_and_deletes_category(env):
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {900: env["owned_cat"], 800: env["dest_cat"]}.get(cid)
        mcl.is_split.return_value = False                 # single-category owner
        mcl.primary_category.return_value = env["owned_cat"]
        mcl.move_channel_to_owner = AsyncMock(return_value=True)
        mcl.puzzle_count.return_value = 0                 # dest has room
        mpdb.get_by_attr.return_value = env["target"]     # meta's target puzzle
        await svc.consolidate_to(guild, hunt, meta, dest, force=False)
    # channels moved into the destination owner
    assert mcl.move_channel_to_owner.await_count == 2
    # meta reverted to channel-home = its target puzzle's channel id
    assert meta.category_id == env["target"].channel_id
    mrdb.commit.assert_any_call(meta)
    # emptied owned category deleted
    env["owned_cat"].delete.assert_awaited()


async def test_consolidate_raises_would_overflow_unless_forced(env):
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {900: env["owned_cat"], 800: env["dest_cat"]}.get(cid)
        mcl.is_split.return_value = False
        mcl.primary_category.return_value = env["owned_cat"]
        mcl.puzzle_count.return_value = 49                # dest already near cap
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.move_channel_to_owner = AsyncMock(return_value=True)
        mpdb.get_by_attr.return_value = env["target"]
        with pytest.raises(RuntimeError, match="would_overflow"):
            await svc.consolidate_to(guild, hunt, meta, dest, force=False)
        # with force=True it proceeds (no raise)
        await svc.consolidate_to(guild, hunt, meta, dest, force=True)


async def test_consolidate_channel_home_meta_moves_extract_set(env):
    # meta already channel-home: category_id is a CHANNEL id (801), not a category.
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    meta.category_id = 801
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {800: env["dest_cat"]}.get(cid)  # 801 not a category => channel-home
        mcl.puzzle_count.return_value = 0
        mcl.move_channel_to_owner = AsyncMock(return_value=True)
        mpdb.get_by_attr.return_value = env["target"]
        mpdb.get_all_from_tag_group.return_value = [env["target"], env["feeder"]]
        mrdb.get_all.return_value = [meta, dest]
        await svc.consolidate_to(guild, hunt, meta, dest, force=False)
    # extract-set (target + exclusive feeder) moved; no category to delete
    assert mcl.move_channel_to_owner.await_count == 2
    env["owned_cat"].delete.assert_not_awaited()
```

- [ ] **Step 2: Run, confirm FAIL** (no `consolidate_to`):
`timeout 180 pipenv run python -m pytest tests/test_consolidate_service.py -q > /tmp/test.log 2>&1`

- [ ] **Step 3: Implement** in `bot/core/puzzle_service.py` (add near `extract_target_to_category`). Adapt to the real `category_layout` API; keep the described behaviour and the tests' assertions.

```python
def _channels_to_consolidate(self, guild, hunt, meta):
    """The channels to move when consolidating *meta* into a destination.

    Category-owning meta: every channel across its owned categories (primary +
    split rows) — nested channel-home metas ride along automatically. Returns
    (channels, owned_categories). Channel-home meta: its extract-set, and no
    owned categories to delete."""
    primary = category_layout.primary_category(guild, meta)
    if primary is None:
        # channel-home: move the extract-set (target + exclusive plain feeders).
        target_puzzle = PuzzleJsonDb.get_by_attr(id=meta.target_puzzle_id)
        members = PuzzleJsonDb.get_all_from_tag_group(meta.id)
        all_groups = TagGroupJsonDb.get_all(hunt.id)
        target_ids = {g.target_puzzle_id for g in all_groups if g.target_puzzle_id}
        puzzles = select_extract_puzzles(meta, target_puzzle, members, target_ids)
        channels = [guild.get_channel(p.channel_id) for p in puzzles]
        return [(c, p) for c, p in zip(channels, puzzles) if c is not None], []
    # category-owning: gather every channel across the owned category set.
    owned = [primary]
    if category_layout.is_split(meta, TagGroupCategoryDb):
        for row in category_layout.group_categories(meta, TagGroupCategoryDb):
            cat = category_layout._category_by_id(guild, row.category_id)
            if cat is not None and cat.id != primary.id:
                owned.append(cat)
    moves = []
    owned_ids = {c.id for c in owned}
    for cat in owned:
        for ch in list(cat.channels):
            if ch.name == SOLVE_DIVIDER:
                continue
            puzzle = PuzzleJsonDb.get_by_attr(channel_id=ch.id)
            moves.append((ch, puzzle))
    return moves, owned

async def consolidate_to(self, guild, hunt, meta, destination, force=False):
    """Fold *meta* into *destination*'s category, reverting meta to channel-home.

    Raises RuntimeError("would_overflow") if placing the channels would push the
    destination past the soft cap and not *force*. Raises "no_target_puzzle" if
    meta's target is missing, "bad_destination" if destination has no category."""
    target_puzzle = PuzzleJsonDb.get_by_attr(id=meta.target_puzzle_id)
    if target_puzzle is None:
        raise RuntimeError("no_target_puzzle")
    dest_primary = category_layout.primary_category(guild, destination)
    if dest_primary is None:
        # channel-home destination: resolve its host category
        dest_primary = category_layout._home_category(guild, destination)
    if dest_primary is None:
        raise RuntimeError("bad_destination")

    moves, owned = self._channels_to_consolidate(guild, hunt, meta)

    # Overflow gate (unsolved channels land in the destination's unsolved primary).
    incoming = sum(1 for _ch, p in moves if getattr(p, "status", "") != "solved")
    if not force and not category_layout.is_split(destination, TagGroupCategoryDb):
        if category_layout.puzzle_count(dest_primary) + incoming > category_layout.CATEGORY_SOFT_LIMIT:
            raise RuntimeError("would_overflow")

    async with category_layout.layout_lock:
        for channel, puzzle in moves:
            solved = getattr(puzzle, "status", "") == "solved"
            await category_layout.move_channel_to_owner(
                guild, destination, TagGroupCategoryDb, channel, solved)
        # Tear down the vacated owned categories (channels already moved out).
        if owned:
            if not await category_layout.delete_group_categories(
                    guild, meta, TagGroupCategoryDb):
                # single-category (non-split) owner: delete its primary directly.
                for cat in owned:
                    try:
                        for ch in list(cat.channels):
                            await ch.delete(reason=DELETE_REASON)
                        await cat.delete(reason=DELETE_REASON)
                    except Exception:
                        logger.warning("consolidate_to: failed to delete category %s",
                                       getattr(cat, "id", "?"), exc_info=True)
        # Revert meta to channel-home (its target puzzle's channel).
        meta.category_id = target_puzzle.channel_id
        TagGroupJsonDb.commit(meta)
    return destination
```

(`select_extract_puzzles`, `category_layout`, `PuzzleJsonDb`, `TagGroupJsonDb`, `TagGroupCategoryDb`, `SOLVE_DIVIDER`, `DELETE_REASON`, `logger` are already imported/available — verify.)

- [ ] **Step 4: Run, confirm the 3 tests PASS.** Adjust mock/assertion details to the real `category_layout` surface if needed — do NOT weaken a behavioural assertion.

- [ ] **Step 5: Gates**
```bash
timeout 180 pipenv run basedpyright bot/core/puzzle_service.py > /tmp/pyright.log 2>&1
timeout 120 pipenv run ruff check bot/core/puzzle_service.py tests/test_consolidate_service.py > /tmp/ruff.log 2>&1
```
Watch for `reportOptionalMemberAccess` — the `target_puzzle is None` / `dest_primary is None` guards narrow before use; keep them. `PuzzleJsonDb.get_by_attr(channel_id=ch.id)` can return None (a non-puzzle channel) — `move_channel_to_owner` only needs `solved`, so guard `getattr(puzzle, "status", "")`.

- [ ] **Step 6: Commit**
```bash
git add bot/core/puzzle_service.py tests/test_consolidate_service.py
git commit -m "feat(consolidate): PuzzleService.consolidate_to drains a meta into a destination

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `!consolidate_to` command

**Files:** Modify `bot/cogs/puzzles.py`; Test `tests/test_consolidate_command.py`.

Run in the meta's own target-puzzle channel; arg = destination `meta_code`. Resolve destination; call the service; on `would_overflow`, confirm and retry with `force=True`.

- [ ] **Step 1: Write the failing test** — `tests/test_consolidate_command.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from bot.cogs.puzzles import Puzzles
from bot.store.tag_group_data import TagGroupData


def _cog_and_ctx(meta, hunt, puzzle, dest):
    cog = MagicMock()
    cog.get_hunt = MagicMock(return_value=hunt)
    cog.get_tag_group = MagicMock(return_value=meta)
    cog.get_puzzle = MagicMock(return_value=puzzle)
    cog.resolve_target_group = MagicMock(return_value=dest)
    cog.service = MagicMock()
    ctx = MagicMock(); ctx.guild = MagicMock(); ctx.send = AsyncMock()
    return cog, ctx


def _meta():
    m = TagGroupData(name="spooky-meta"); m.id = 10; m.target_puzzle_id = 1
    return m


async def test_consolidate_calls_service_and_acks():
    meta = _meta(); hunt = MagicMock()
    target = MagicMock(); target.id = 1
    dest = TagGroupData(name="round-a"); dest.id = 20
    cog, ctx = _cog_and_ctx(meta, hunt, target, dest)
    cog.service.consolidate_to = AsyncMock(return_value=dest)

    await Puzzles.consolidate_to.callback(cog, ctx, "ROUNDA")

    cog.service.consolidate_to.assert_awaited_once_with(ctx.guild, hunt, meta, dest, force=False)
    ctx.send.assert_awaited()


async def test_consolidate_refuses_outside_target_channel():
    meta = _meta(); hunt = MagicMock()
    feeder = MagicMock(); feeder.id = 999          # not the meta's target
    dest = TagGroupData(name="round-a"); dest.id = 20
    cog, ctx = _cog_and_ctx(meta, hunt, feeder, dest)
    cog.service.consolidate_to = AsyncMock()

    await Puzzles.consolidate_to.callback(cog, ctx, "ROUNDA")

    cog.service.consolidate_to.assert_not_awaited()
    ctx.send.assert_awaited()


async def test_consolidate_confirms_on_would_overflow_then_forces():
    meta = _meta(); hunt = MagicMock()
    target = MagicMock(); target.id = 1
    dest = TagGroupData(name="round-a"); dest.id = 20
    cog, ctx = _cog_and_ctx(meta, hunt, target, dest)
    cog.service.consolidate_to = AsyncMock(side_effect=[RuntimeError("would_overflow"), dest])
    cog._confirm_destructive = AsyncMock(return_value=True)

    await Puzzles.consolidate_to.callback(cog, ctx, "ROUNDA")

    assert cog.service.consolidate_to.await_count == 2
    # second call forces
    assert cog.service.consolidate_to.await_args_list[1].kwargs.get("force") is True


async def test_consolidate_overflow_cancelled_does_not_force():
    meta = _meta(); hunt = MagicMock()
    target = MagicMock(); target.id = 1
    dest = TagGroupData(name="round-a"); dest.id = 20
    cog, ctx = _cog_and_ctx(meta, hunt, target, dest)
    cog.service.consolidate_to = AsyncMock(side_effect=RuntimeError("would_overflow"))
    cog._confirm_destructive = AsyncMock(return_value=False)

    await Puzzles.consolidate_to.callback(cog, ctx, "ROUNDA")

    assert cog.service.consolidate_to.await_count == 1   # no forced retry
    ctx.send.assert_awaited()
```

- [ ] **Step 2: Run, confirm FAIL.**

- [ ] **Step 3: Add the command** to the `Puzzles` cog (near `extract_target_to_category`). Attach the destination autocomplete like the edge commands do (`@app_commands.autocomplete(destination=group_code_autocomplete)`):

```python
@commands.hybrid_command(
    description="Fold this meta back into another category (frees a category slot)")
@app_commands.autocomplete(destination=group_code_autocomplete)
@commands.has_any_role(*ORGANISER_ROLES)
@commands.has_permissions(manage_channels=True)
async def consolidate_to(self, ctx, destination: str):
    """*Fold this meta into <destination>'s category, freeing this meta's category.*

    Usage: `!consolidate_to <destination-code>`
    Run in the meta's own target-puzzle channel. Organisers/admins only.
    Not done lightly: it deletes this meta's category and moves its puzzles."""
    hunt = self.get_hunt(ctx)
    meta = self.get_tag_group(ctx)
    puzzle = self.get_puzzle(ctx)
    if (hunt is None or meta is None or not meta.has_target
            or puzzle is None or meta.target_puzzle_id != puzzle.id):
        return await ctx.send(
            ":x: Run this in a metapuzzle's own target-puzzle channel.")
    dest = self.resolve_target_group(ctx, destination)
    if dest is None:
        return  # resolve_target_group already sent help/disambiguation
    if dest.id == meta.id:
        return await ctx.send(":x: Can't consolidate a meta into itself.")
    try:
        await self.service.consolidate_to(ctx.guild, hunt, meta, dest, force=False)
    except RuntimeError as exc:
        if str(exc) == "would_overflow":
            if not await self._confirm_destructive(
                    ctx,
                    f"Consolidating **{meta.name}** into **{dest.name}** will overflow "
                    f"it (it'll split into an extra category, reducing the saving). "
                    f"Proceed?",
                    sheets_deleted=False):
                return await ctx.send(":x: Consolidation cancelled.")
            try:
                await self.service.consolidate_to(ctx.guild, hunt, meta, dest, force=True)
            except RuntimeError as exc2:
                return await ctx.send(f":x: Couldn't consolidate ({exc2}).")
        else:
            return await ctx.send(f":x: Couldn't consolidate ({exc}).")
    await ctx.send(
        f":white_check_mark: Consolidated **{meta.name}** into **{dest.name}**.")
```

Verify `app_commands`, `group_code_autocomplete`, `resolve_target_group`, `ORGANISER_ROLES`, `_confirm_destructive` are already available in the file (they are).

- [ ] **Step 4: Run tests, verify PASS; then gates**
```bash
timeout 200 pipenv run python -m pytest tests/test_consolidate_command.py tests/test_consolidate_service.py -q > /tmp/test.log 2>&1
timeout 180 pipenv run basedpyright bot/cogs/puzzles.py bot/core/puzzle_service.py > /tmp/pyright.log 2>&1
timeout 120 pipenv run ruff check bot/cogs/puzzles.py tests/test_consolidate_command.py > /tmp/ruff.log 2>&1
```

- [ ] **Step 5: Commit**
```bash
git add bot/cogs/puzzles.py tests/test_consolidate_command.py
git commit -m "feat(consolidate): !consolidate_to command with overflow confirm

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Documentation

**Files:** Modify `docs/commands.md` (Rounds & groups table, near `!extract_target_to_category`), `docs/user-guide.md`.

- [ ] **Step 1: `docs/commands.md`** — add after the `!extract_target_to_category` row (match the 6-column shape):
```markdown
| `!consolidate_to` | | `destination-code` | meta's target-puzzle channel | organisers/admins | Fold this meta back into `<destination>`'s category and delete this meta's category (frees a category slot). Confirms first if the destination would overflow. Inverse of `!extract_target_to_category`. |
```
- [ ] **Step 2: `docs/user-guide.md`** — one short paragraph near the extract mention: `!consolidate_to <destination>` is the reverse of extract — run in the meta's channel, it moves the meta and its puzzles into the destination's category and frees this meta's category slot; use it when you're short on categories but have channel headroom.
- [ ] **Step 3: Commit**
```bash
git add docs/commands.md docs/user-guide.md
git commit -m "docs: document !consolidate_to

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Verification

- [ ] Full suite: `timeout 400 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` — no new failures.
- [ ] Type gate: `timeout 300 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1` — 0 errors.
- [ ] Lint gate: `timeout 180 pipenv run ruff check > /tmp/ruff.log 2>&1` — clean.
- [ ] Manual smoke (optional, live): extract a meta into its own category, add a couple of feeders, then `!consolidate_to <round>` from the meta's channel → the meta + feeders move into the round's category, the meta's category is deleted, and the meta shows as channel-home again; force-confirm path when the destination is near the cap.

## Notes / edge cases to keep in mind

- Nested channel-home metas inside the drained category need NO bookkeeping: their `category_id` is a channel id, and reparenting the channel keeps its id, so they remain channel-home inside the destination automatically.
- The glyph rides along on the moved channels (extract/consolidate never rename), so targets keep their emoji.
- After consolidation the meta is channel-home in the destination — exactly the state Plan 4's auto-extract can later pull back out under pressure.

## Out of scope

- Auto-extract on overflow (Plan 4).
- No web-dashboard surface (Discord plumbing).
- Consolidating a meta that does not own a category and has no destination category is rejected (`bad_destination`).
