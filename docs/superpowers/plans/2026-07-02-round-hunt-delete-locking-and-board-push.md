# Round/hunt delete locking (#45) + delete_hunt board push (#54) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix review findings #45 and #54 (`docs/reviews/2026-07-02-staging-full-review.md`), both LOW severity:
- **#45**: `!delete_round`/`!delete_hunt` and their web equivalents never take any per-puzzle lock, so a concurrent `!solve`/dashboard-solve on a member puzzle (which DOES hold that puzzle's lock) can interleave with the deletion loop, writing to a sheet/DB row mid-teardown.
- **#54**: `create_hunt` and `delete_hunt` are the only `PuzzleService` mutations without `@pushes_board`, so no WebSocket invalidation fires on hunt delete — dashboard clients watching a deleted hunt keep rendering its board until a stale-poll or manual reload.

**Architecture:** For #45, add a new `puzzles_lock(puzzle_ids)` helper to `bot/core/locks.py` — the multi-id sibling of the existing single-puzzle `puzzle_lock`/`with_puzzle_mutex`, acquiring every listed puzzle's lock (in sorted order, to prevent two overlapping multi-lock holders from deadlocking each other) from the SAME shared `_PUZZLE_LOCKS` registry the single-puzzle helpers already use. Wire it into all four delete entrypoints (bang `delete_round`, bang `delete_hunt`, web `delete_tag_group`, web `delete_hunt`), wrapping confirmation + the actual service call — mirroring how `!delete_puzzle`'s existing `@with_puzzle_mutex` decorator already wraps its own confirm-then-delete flow for the single-puzzle case.

For #54, `delete_hunt(self, guild, hunt)` already receives a `hunt` parameter under that exact name, so `@pushes_board()`'s default (`hunt="hunt"`) works with zero extra wiring — just add the decorator. `create_hunt` is deliberately left unfixed: it has no `hunt` parameter to push from (it CREATES one), and the review finding itself notes the frontend gap (`useActivityFeed` never invalidates the `["hunts"]` list query) would still leave hunt *creation* invisible until reload even with a backend push — that's a separate, larger frontend fix, out of scope here. This is disclosed explicitly in the doc update (Task 5), not silently dropped.

**Tech Stack:** Python, pytest + pytest-asyncio, unittest.mock (`AsyncMock`/`MagicMock`), existing `tests/conftest.py` builders, Quart test client for web route tests.

---

## File Structure

- Modify: `bot/core/locks.py` — add `puzzles_lock`.
- Modify: `tests/test_puzzle_lock.py` — add `puzzles_lock` tests.
- Modify: `bot/cogs/puzzles.py` — wire `delete_round`/`delete_hunt` bang commands.
- Modify: `tests/test_group_type_consolidation.py` — add a locking-wired test for bang `delete_round`.
- Modify: `bot/web/actions.py` — wire `delete_tag_group`/`delete_hunt` web routes.
- Modify: `tests/test_delete_round_web.py` — update fixture to patch the new dependencies; add a locking test.
- Create: `tests/test_delete_hunt_web.py` — new file, no prior web-route-level test existed for `delete_hunt`.
- Modify: `bot/core/puzzle_service.py` — add `@pushes_board()` to `delete_hunt`.
- Modify: `tests/test_board_push_service.py` — add `"delete_hunt"` to `EXPECTED_PUSH_METHODS`, add `test_delete_hunt_emits_board_changed`.
- Modify: `docs/reviews/2026-07-02-staging-full-review.md` — status notes for #45, #54 (with the `create_hunt` caveat explicit).

---

### Task 1: `puzzles_lock` helper

**Files:**
- Modify: `bot/core/locks.py`
- Test: `tests/test_puzzle_lock.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_puzzle_lock.py`:

```python
@pytest.mark.asyncio
async def test_puzzles_lock_serializes_against_single_puzzle_holder():
    """A multi-puzzle holder and a single-puzzle holder sharing one id must not
    interleave their critical sections (review #45)."""
    from bot.core.locks import puzzle_lock, puzzles_lock

    order = []

    async def multi_holder(hold):
        async with puzzles_lock([1, 2, 3]):
            order.append("multi-start")
            await asyncio.sleep(hold)
            order.append("multi-end")

    async def single_holder(hold):
        async with puzzle_lock(2):
            order.append("single-start")
            await asyncio.sleep(hold)
            order.append("single-end")

    await asyncio.gather(multi_holder(0.02), single_holder(0.0))

    assert order[0].split("-")[0] == order[1].split("-")[0]
    assert order[2].split("-")[0] == order[3].split("-")[0]
    assert order[0].split("-")[0] != order[2].split("-")[0]


@pytest.mark.asyncio
async def test_puzzles_lock_dedupes_repeated_ids():
    """A puzzle id listed twice must only be locked once — acquiring the same
    (non-reentrant) asyncio.Lock twice in one task would hang forever."""
    from bot.core.locks import puzzles_lock

    async def holder():
        async with puzzles_lock([5, 5, 5]):
            return "ok"

    result = await asyncio.wait_for(holder(), timeout=0.1)
    assert result == "ok"


@pytest.mark.asyncio
async def test_puzzles_lock_empty_list_is_noop():
    """No puzzles to lock (e.g. an empty round) must not raise or hang."""
    from bot.core.locks import puzzles_lock

    async with puzzles_lock([]):
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 60 pipenv run python -m pytest tests/test_puzzle_lock.py -v > /tmp/test-output.log 2>&1` from `/home/russ/projects/puzzbot/.claude/worktrees/working-2` (use plain `python -m pytest` if `pipenv` isn't set up — check `AGENTS.md` for the correct invocation)

Expected: the three new tests FAIL with `ImportError`/`AttributeError` (`puzzles_lock` doesn't exist yet); the pre-existing tests in the file still pass.

- [ ] **Step 3: Commit the tests**

```bash
git add tests/test_puzzle_lock.py
git commit -m "test(locks): add failing tests for puzzles_lock (#45)"
```

- [ ] **Step 4: Implement `puzzles_lock`**

In `bot/core/locks.py`, change the import line:

```python
from contextlib import asynccontextmanager
```

to:

```python
from contextlib import AsyncExitStack, asynccontextmanager
```

Then add this function after `puzzle_lock` (before `rollup_lock`):

```python
@asynccontextmanager
async def puzzles_lock(puzzle_ids):
    """Hold EVERY listed puzzle's lock for the duration of the block (M10, #45):
    round/hunt teardown needs to serialize against a concurrent solve/retag on ANY
    of its member puzzles, not just one. Acquired in sorted order so two
    overlapping multi-puzzle holders (e.g. two round deletions sharing a puzzle)
    can't deadlock each other; a single-puzzle holder (puzzle_lock/
    with_puzzle_mutex) never blocks waiting on a second lock, so it can't join a
    cycle either. Waits if any lock is currently held; de-dupes ids so a puzzle
    listed twice is only locked once (asyncio.Lock is not reentrant)."""
    async with AsyncExitStack() as stack:
        for puzzle_id in sorted(set(puzzle_ids)):
            await stack.enter_async_context(_PUZZLE_LOCKS[puzzle_id])
        yield
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `timeout 60 pipenv run python -m pytest tests/test_puzzle_lock.py -v > /tmp/test-output.log 2>&1`

Expected: PASS (all tests, including the 3 new ones and all pre-existing ones)

- [ ] **Step 6: Commit**

```bash
git add bot/core/locks.py
git commit -m "feat(locks): add puzzles_lock for multi-puzzle round/hunt teardown (#45)"
```

---

### Task 2: Wire bang `delete_round`/`delete_hunt`

**Files:**
- Modify: `bot/cogs/puzzles.py`
- Test: `tests/test_group_type_consolidation.py`

- [ ] **Step 1: Add the import**

In `bot/cogs/puzzles.py`, find this line near the top:

```python
from bot.core.locks import _PUZZLE_LOCKS
```

Change it to:

```python
from bot.core.locks import _PUZZLE_LOCKS, puzzles_lock
```

- [ ] **Step 2: Wire `delete_round`**

Find this exact current code (the `delete_round` bang command body):

```python
        tag_group = self.get_tag_group(ctx)
        n = len(PuzzleJsonDb.get_all_from_tag_group(tag_group.id))
        if not await self._confirm_destructive(ctx, f"Delete round **{tag_group.name}** and its {n} puzzle(s), channels and sheets?"):
            return

        hunt_general_channel = self.bot.get_channel(self.get_hunt(ctx).channel_id)

        # keep_puzzles=False pinned explicitly: the service default flipped to True
        # (keep member puzzles) for the web dashboard's scoped-delete option, but the
        # bang command's confirm text above still promises "and its {n} puzzle(s)".
        failed = await self.service.delete_round(
            ctx.guild, self.get_hunt(ctx), tag_group, keep_puzzles=False)

        await hunt_general_channel.send(
            f":white_check_mark: Round {tag_group.name} successfully deleted.  "
            f"All puzzle sheets have been deleted.")
        await self._warn_failed_channels(hunt_general_channel, failed)
        self.set_puzzle(ctx, None)
        self.set_tag_group(ctx, None)
```

Replace with:

```python
        tag_group = self.get_tag_group(ctx)
        member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_tag_group(tag_group.id)]
        n = len(member_puzzle_ids)
        async with puzzles_lock(member_puzzle_ids):
            if not await self._confirm_destructive(ctx, f"Delete round **{tag_group.name}** and its {n} puzzle(s), channels and sheets?"):
                return

            hunt_general_channel = self.bot.get_channel(self.get_hunt(ctx).channel_id)

            # keep_puzzles=False pinned explicitly: the service default flipped to True
            # (keep member puzzles) for the web dashboard's scoped-delete option, but the
            # bang command's confirm text above still promises "and its {n} puzzle(s)".
            failed = await self.service.delete_round(
                ctx.guild, self.get_hunt(ctx), tag_group, keep_puzzles=False)

            await hunt_general_channel.send(
                f":white_check_mark: Round {tag_group.name} successfully deleted.  "
                f"All puzzle sheets have been deleted.")
            await self._warn_failed_channels(hunt_general_channel, failed)
        self.set_puzzle(ctx, None)
        self.set_tag_group(ctx, None)
```

- [ ] **Step 3: Wire `delete_hunt`**

Find this exact current code (the `delete_hunt` bang command body):

```python
        hunt = self.get_hunt(ctx)
        n = len(PuzzleJsonDb.get_all_from_hunt(hunt.id))
        if not await self._confirm_destructive(ctx, f"Delete the ENTIRE hunt **{hunt.name}**: {n} puzzle(s), all rounds, channels and sheets?"):
            return
        failed = await self.service.delete_hunt(ctx.guild, hunt)
        await self._warn_failed_channels(hunt_channel, failed)
        self.set_puzzle(ctx, None)
        self.set_tag_group(ctx, None)
        self.set_hunt(ctx, None)
        return True
```

Replace with:

```python
        hunt = self.get_hunt(ctx)
        member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_hunt(hunt.id)]
        n = len(member_puzzle_ids)
        async with puzzles_lock(member_puzzle_ids):
            if not await self._confirm_destructive(ctx, f"Delete the ENTIRE hunt **{hunt.name}**: {n} puzzle(s), all rounds, channels and sheets?"):
                return
            failed = await self.service.delete_hunt(ctx.guild, hunt)
            await self._warn_failed_channels(hunt_channel, failed)
        self.set_puzzle(ctx, None)
        self.set_tag_group(ctx, None)
        self.set_hunt(ctx, None)
        return True
```

- [ ] **Step 4: Run the existing bang-level delete tests to confirm no regressions**

Run: `timeout 60 pipenv run python -m pytest tests/test_group_type_consolidation.py -v > /tmp/test-output.log 2>&1` from `/home/russ/projects/puzzbot/.claude/worktrees/working-2`

Expected: PASS. `test_delete_round_allows_capstone_channel` and `test_delete_round_bang_passes_keep_puzzles_false` both already mock `PuzzleJsonDb.get_all_from_tag_group.return_value = []`, so `puzzles_lock([])` is a no-op and these should pass unchanged. If either fails, read the failure carefully — it likely means the mock needs `PuzzleDb.get_all_from_tag_group` to be called with the exact same `tag_group.id`, which it already is; do not weaken the test to make it pass, investigate why first.

- [ ] **Step 5: Write a new test asserting the lock is actually taken with real puzzle ids**

Append to `tests/test_group_type_consolidation.py`:

```python
async def test_delete_round_bang_locks_member_puzzles():
    """delete_round must hold every member puzzle's lock for the duration of
    confirm+delete, not just proceed unlocked (review #45)."""
    from bot.cogs.puzzles import Puzzles
    from tests.conftest import make_puzzle

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
    members = [make_puzzle(id=1), make_puzzle(id=2)]

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb, \
         patch("bot.cogs.puzzles.puzzles_lock") as lock_mock:
        PuzzleDb.get_all_from_tag_group.return_value = members
        lock_mock.return_value.__aenter__ = AsyncMock(return_value=None)
        lock_mock.return_value.__aexit__ = AsyncMock(return_value=None)
        await Puzzles.delete_round.callback(cog, ctx)

    lock_mock.assert_called_once_with([1, 2])
```

- [ ] **Step 6: Run the new test to verify it passes**

Run: `timeout 60 pipenv run python -m pytest tests/test_group_type_consolidation.py::test_delete_round_bang_locks_member_puzzles -v > /tmp/test-output.log 2>&1`

Expected: PASS. If `make_ctx`/`make_cog`/`make_hunt`/`make_round` aren't already imported at the top of the file, check the existing imports (`from tests.conftest import make_cog, make_ctx, make_hunt, make_round`) — `make_puzzle` needs adding to that import line (or import it locally inside the test as shown above; either is fine, prefer matching the file's existing style — check whether other tests in the file import conftest helpers locally or at module level, and follow that convention rather than introducing a second style).

- [ ] **Step 7: Run the full bang-cog delete test suite for regressions**

Run: `timeout 60 pipenv run python -m pytest tests/test_group_type_consolidation.py tests/test_delete_safety.py -v > /tmp/test-output.log 2>&1`

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_group_type_consolidation.py
git commit -m "fix(delete): lock member puzzles during bang delete_round/delete_hunt (#45)"
```

---

### Task 3: Wire web `delete_tag_group`/`delete_hunt`

**Files:**
- Modify: `bot/web/actions.py`
- Modify: `tests/test_delete_round_web.py`
- Create: `tests/test_delete_hunt_web.py`

- [ ] **Step 1: Add the import**

In `bot/web/actions.py`, find:

```python
from bot.core.locks import puzzle_lock
```

Change to:

```python
from bot.core.locks import puzzle_lock, puzzles_lock
```

- [ ] **Step 2: Wire the `delete_hunt` web route**

Find this exact current code:

```python
    from bot.store import HuntJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None

    try:
        failed = await cog.service.delete_hunt(guild, hunt_obj)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("delete_hunt", hunt_uid=uid,
                 detail=f"name={hunt_obj.name}")
```

(this is inside the `delete_hunt` route — the `/hunts/<uid>` DELETE handler, not the `delete_tag_group` one further down)

Replace with:

```python
    from bot.store import HuntJsonDb, PuzzleJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None

    member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_hunt(hunt_obj.id)]

    try:
        async with puzzles_lock(member_puzzle_ids):
            failed = await cog.service.delete_hunt(guild, hunt_obj)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("delete_hunt", hunt_uid=uid,
                 detail=f"name={hunt_obj.name}")
```

- [ ] **Step 3: Wire the `delete_tag_group` web route**

Find this exact current code:

```python
    from bot.store import HuntJsonDb, TagGroupJsonDb

    tag_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if tag_group is None:
        return jsonify({"error": "round_not_found"}), 404

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
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

Replace with:

```python
    from bot.store import HuntJsonDb, PuzzleJsonDb, TagGroupJsonDb

    tag_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if tag_group is None:
        return jsonify({"error": "round_not_found"}), 404

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None

    body = await request.get_json(silent=True) or {}
    keep_puzzles = bool(body.get("keep_puzzles", True))

    member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_tag_group(tag_group.id)]

    try:
        async with puzzles_lock(member_puzzle_ids):
            failed_channels = await cog.service.delete_round(
                guild, hunt_obj, tag_group, keep_puzzles=keep_puzzles)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
```

- [ ] **Step 4: Update `tests/test_delete_round_web.py`'s fixture**

Read the current fixture (top of the file). It currently does NOT patch `bot.store.PuzzleJsonDb` or `bot.web.actions.puzzles_lock` — the new code calls `PuzzleJsonDb.get_all_from_tag_group` for real, which would hit a live DB in tests. Add these two patches to the `patches` list inside the `web` fixture:

```python
        patch("bot.store.PuzzleJsonDb") as pdb_mock,
        patch("bot.web.actions.puzzles_lock", _noop_lock),
```

and configure the mock: `pdb_mock.get_all_from_tag_group.return_value = []` (add this near the other `rdb.get_by_attr.return_value = ...` setup line, before `for p in patches: p.start()`).

Add this helper near the top of the file (after the imports, mirroring `tests/test_delete_puzzle_web.py`'s `_noop_lock`):

```python
from contextlib import asynccontextmanager


@asynccontextmanager
async def _noop_lock(_ids):
    yield
```

Update the fixture's `yield` line to also expose `pdb_mock` if useful for the new test in Step 5 (`yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx, rdb=rdb, pdb=pdb_mock)`).

- [ ] **Step 5: Run the existing delete_round web tests to confirm no regressions**

Run: `timeout 60 pipenv run python -m pytest tests/test_delete_round_web.py -v > /tmp/test-output.log 2>&1` from `/home/russ/projects/puzzbot/.claude/worktrees/working-2`

Expected: PASS (both pre-existing tests, unchanged behavior since `_noop_lock` makes the new locking invisible to them).

- [ ] **Step 6: Add a test asserting the web route actually locks the right puzzles**

Append to `tests/test_delete_round_web.py`:

```python
@pytest.mark.asyncio
async def test_delete_round_web_locks_member_puzzles(web):
    from unittest.mock import call

    web.pdb.get_all_from_tag_group.return_value = [
        MagicMock(id=1), MagicMock(id=2),
    ]
    with patch("bot.web.actions.puzzles_lock") as lock_mock:
        lock_mock.return_value.__aenter__ = AsyncMock(return_value=None)
        lock_mock.return_value.__aexit__ = AsyncMock(return_value=None)
        resp = await web.client.delete("/api/tag_groups/5")
    assert resp.status_code == 200
    lock_mock.assert_called_once_with([1, 2])
```

Note: this test replaces the fixture's `_noop_lock` patch with its own real `puzzles_lock` mock for just this one test — that's fine, the `with patch(...)` inside the test function takes precedence for its duration since it's applied after (and nested inside) the fixture's patches.

- [ ] **Step 7: Run the new test**

Run: `timeout 60 pipenv run python -m pytest tests/test_delete_round_web.py -v > /tmp/test-output.log 2>&1`

Expected: PASS (all tests in the file)

- [ ] **Step 8: Create `tests/test_delete_hunt_web.py`**

This is a new file — no prior web-route-level test existed for `delete_hunt`. Write it mirroring `tests/test_delete_round_web.py`'s structure exactly (fixture shape, patch list, `_noop_lock` helper):

```python
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt


@asynccontextmanager
async def _noop_lock(_ids):
    yield


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    cog = MagicMock()
    cog.service.delete_hunt = AsyncMock(return_value=[])
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()
    pdb = MagicMock()
    pdb.get_all_from_hunt.return_value = []

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.web.actions.puzzles_lock", _noop_lock),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx, pdb=pdb)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_delete_hunt_web_deletes(web):
    resp = await web.client.delete("/api/hunts/U1")
    assert resp.status_code == 200
    web.cog.service.delete_hunt.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_hunt_web_locks_member_puzzles(web):
    web.pdb.get_all_from_hunt.return_value = [MagicMock(id=3), MagicMock(id=1)]
    with patch("bot.web.actions.puzzles_lock") as lock_mock:
        lock_mock.return_value.__aenter__ = AsyncMock(return_value=None)
        lock_mock.return_value.__aexit__ = AsyncMock(return_value=None)
        resp = await web.client.delete("/api/hunts/U1")
    assert resp.status_code == 200
    lock_mock.assert_called_once_with([3, 1])
```

Before finalizing, verify the route name for `_visible_hunt_by_uid` and `require_admin` match exactly what `bot/web/actions.py`'s `delete_hunt` route actually calls (read the route to confirm — it was shown in this plan's Step 2 "before" code as `ctx, err = await _visible_hunt_by_uid(uid)` and `perm = await require_admin(...)`, but double-check the actual current file since it may have shifted).

- [ ] **Step 9: Run the new test file**

Run: `timeout 60 pipenv run python -m pytest tests/test_delete_hunt_web.py -v > /tmp/test-output.log 2>&1`

Expected: PASS (both tests)

- [ ] **Step 10: Run the full web-route regression suite**

Run: `timeout 120 pipenv run python -m pytest tests/test_delete_round_web.py tests/test_delete_hunt_web.py tests/test_delete_puzzle_web.py tests/test_solve_equivalence.py -q > /tmp/test-output.log 2>&1`

Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add bot/web/actions.py tests/test_delete_round_web.py tests/test_delete_hunt_web.py
git commit -m "fix(delete): lock member puzzles during web delete_round/delete_hunt (#45)"
```

---

### Task 4: `delete_hunt` board push (#54)

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Test: `tests/test_board_push_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_board_push_service.py`:

```python
@pytest.mark.asyncio
async def test_delete_hunt_emits_board_changed():
    notifier = _notifier()
    svc = _svc(notifier)
    hunt = _hunt()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.HuntJsonDb"):
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = []
        await svc.delete_hunt(guild=None, hunt=hunt)

    notifier.board_changed.assert_awaited_once_with(hunt)
```

Also update `EXPECTED_PUSH_METHODS` near the top of the file: add `"delete_hunt"` to the set, keeping it alphabetically sorted within its natural grouping (it belongs right after `"delete_puzzle"`, before `"delete_round"`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 60 pipenv run python -m pytest tests/test_board_push_service.py -v > /tmp/test-output.log 2>&1` from `/home/russ/projects/puzzbot/.claude/worktrees/working-2`

Expected: TWO failures — `test_delete_hunt_emits_board_changed` (notifier never called) and `test_every_mutation_carries_the_push_decorator` (the set now expects `delete_hunt` decorated, but it isn't yet).

- [ ] **Step 3: Commit the tests**

```bash
git add tests/test_board_push_service.py
git commit -m "test(board-push): add failing tests for delete_hunt push (#54)"
```

- [ ] **Step 4: Add the decorator**

In `bot/core/puzzle_service.py`, find:

```python
    async def delete_hunt(self, guild, hunt) -> list:
        """Tear down a hunt: puzzles, rounds, spreadsheet files, Discord channels, DB row.

        Per-puzzle channel failures are isolated and returned as a list of puzzle names.
        """
```

Replace with:

```python
    @pushes_board()
    async def delete_hunt(self, guild, hunt) -> list:
        """Tear down a hunt: puzzles, rounds, spreadsheet files, Discord channels, DB row.

        Per-puzzle channel failures are isolated and returned as a list of puzzle names.
        """
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 60 pipenv run python -m pytest tests/test_board_push_service.py -v > /tmp/test-output.log 2>&1`

Expected: PASS (all tests, including the two that were failing)

- [ ] **Step 6: Run the broader delete_hunt-adjacent suite for regressions**

Run: `timeout 120 pipenv run python -m pytest tests/test_board_push_service.py tests/test_delete_hunt_service.py tests/test_delete_safety.py tests/test_delete_hunt_web.py -q > /tmp/test-output.log 2>&1`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add bot/core/puzzle_service.py
git commit -m "fix(board-push): emit board_changed on delete_hunt (#54)"
```

---

### Task 5: Update the review doc's status notes for #45, #54

**Files:**
- Modify: `docs/reviews/2026-07-02-staging-full-review.md`

- [ ] **Step 1: Update finding #45's status**

Find `### 45.` in `docs/reviews/2026-07-02-staging-full-review.md`. Append this paragraph at the end of that finding's section:

```

**Status update (2026-07-02):** fully fixed. A new `puzzles_lock(puzzle_ids)`
helper (`bot/core/locks.py`) holds every member puzzle's lock — from the same
shared `_PUZZLE_LOCKS` registry `puzzle_lock`/`with_puzzle_mutex` already use —
for the duration of confirm-plus-delete, on all four entrypoints: bang
`!delete_round`, bang `!delete_hunt`, and both web equivalents. A concurrent
`!solve`/dashboard-solve on any member puzzle now blocks until teardown
completes (or the reverse, if the solve got there first), closing the
interleaving race described above.
```

- [ ] **Step 2: Update finding #54's status**

Find `### 54.` in the same file. Append:

```

**Status update (2026-07-02):** partially fixed. `delete_hunt` now carries
`@pushes_board()`, so a WebSocket `board_changed` event fires on hunt delete
and connected dashboard clients stop rendering a deleted board immediately
instead of waiting on the 30s poll or a manual reload. `create_hunt` is
deliberately left unfixed: it has no pre-call `hunt` object to push from (it
creates one), and — as this finding's own analysis already noted — the
frontend's `useActivityFeed` only invalidates the `["hunt", uid]` query, never
`["hunts"]`, so a bang `!hunt` creation would still not appear on an open hunt
list even with a backend push. Fixing hunt-creation visibility needs a
frontend query-invalidation change, out of scope for this backend-only fix.
```

- [ ] **Step 3: Verify and commit**

Run `git diff --stat` to confirm only the review doc changed. Then:

```bash
git add docs/reviews/2026-07-02-staging-full-review.md
git commit -m "docs: note #45 fully fixed, #54 partially fixed (create_hunt caveat)"
```

---

## Verification

- [ ] Run the full delete/locking/board-push-adjacent suite once more end to end:

Run: `timeout 180 pipenv run python -m pytest tests/test_puzzle_lock.py tests/test_group_type_consolidation.py tests/test_delete_safety.py tests/test_delete_round_web.py tests/test_delete_hunt_web.py tests/test_delete_puzzle_web.py tests/test_board_push_service.py tests/test_delete_hunt_service.py tests/test_delete_round_keep_puzzles.py --compact > /tmp/test-output.log 2>&1`

Expected: all PASS.

## Rollback / recovery notes

Each task is its own set of commits; revert individually if needed. Task 1 (the `puzzles_lock` helper) is a pure addition with no callers yet — safe to revert on its own only if Tasks 2/3 (which depend on it) are reverted first or were never applied.
