# Rename Group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let organisers rename a group (`tag_group`) and its Discord container(s) from Discord (`!rename_group`, run in a group's target-puzzle channel or a regular group's text channel) and from the web Arrange tab (inline editor), without touching the target puzzle.

**Architecture:** One shared `PuzzleService.rename_group` (decorated `@pushes_board()`) is called by both a new `!rename_group` cog command and a new `POST /api/tag_groups/<id>/rename` endpoint — the existing `rename_puzzle`/`delete_round` parity pattern. A pure planner `category_layout.group_rename_plan` enumerates which Discord categories/channels to rename; the service loops `safe_rename` over them. The frontend adds a `useRenameGroup` hook and an inline editor on the Arrange column header.

**Tech Stack:** Python (discord.py + Quart), pytest (`asyncio_mode=auto`); React + TypeScript + Mantine + React Query (no frontend test runner — verify with `tsc --noEmit`).

**Spec:** `docs/superpowers/specs/2026-06-30-rename-group-design.md`

**Test command (backend):**
```bash
timeout 180 pipenv run python -m pytest <paths> -q > /tmp/test-rename-group.log 2>&1
```
(Read the log file for results; exit 124 = timeout/hang.)

**Build check (frontend), run from `frontend/`:**
```bash
cd frontend && npm run build > /tmp/fe-build.log 2>&1
```

---

## Reference facts (verified against the code — do not re-derive)

- `clean_name(name)` (`bot/core/discord_channels.py:24`) = `"-".join(name.lower().split())` (lowercase-hyphenated). At group creation, `new_category_name = clean_name(name)` (`puzzle_service.py:1670`), and BOTH the cloned category and a metaless Group's dedicated text channel are created with that same string. So the text channel is locatable inside the category by the old `clean_name`.
- `category_layout` already imports `clean_name`, `ROLE_SOLVED`, `ROLE_UNSOLVED`, `overflow_category_name`, `solved_category_name`, `base_name`, `primary_category`, `_category_by_id`, `group_categories`. `puzzle_service` already imports `category_layout` and `TagGroupCategoryDb` (`puzzle_service.py:20-21`).
- `overflow_category_name(base, role, seq)` (`category_layout.py:34`) → `base` for unsolved, `f"{base} — solved"` for solved, with ` (n+1)` appended when `seq >= 1`.
- `group_categories(tag_group, dao)` (`category_layout.py:87`) → `dao.get_for_group(tag_group.id)`, a list of `TagGroupCategoryData(category_id, role, seq)`. Empty list = single-category (legacy) layout.
- `TagGroupData.has_target` → `bool(target_puzzle_id)`. Metaless Group has `target_puzzle_id == 0`.
- Duplicate check: `TagGroupJsonDb.check_duplicates_in_hunt(name, hunt_obj)` (the base `_MySQLBaseDb` version, `mysqldb.py:120`) checks the `tag_group` table for a row with that `name` + `hunt_id`. **It takes a `HuntData` object** (reads `hunt_data.id`), NOT an int. `get_hunt(ctx)` returns a `HuntData`; the web endpoint loads one via `HuntJsonDb.get_by_attr(id=...)`.
- `pushes_board()` (`puzzle_service.py:46`) defaults to the param literally named `hunt`. The new method's 3rd param MUST be named `hunt`.
- `safe_rename(channel, name)` (`puzzle_service.py:178`) renames a Discord channel/category via `.edit(name=...)`, coalescing under rate limits. Categories support `.edit(name=...)` identically.
- conftest factories (`tests/conftest.py`): `make_hunt(id=10, ...)`, `make_round(id=1, name="Test Round", hunt_id=10, target_puzzle_id=0, group_code="TROUND", category_id=999)` → `TagGroupData` (note: no `.type` set by default; set it explicitly when needed), `make_puzzle(id=1, ...)`, `make_ctx`, `make_cog`. `tests/test_solve_equivalence.py` defines `_make_cog()` and `_no_guild()`.

---

## File Structure

- **Create** `category_layout.group_rename_plan` in `bot/core/category_layout.py` — pure planner: `(tag_group, new_name) → [(discord_obj, new_name_str)]`.
- **Create** `PuzzleService.rename_group` in `bot/core/puzzle_service.py` — orchestration + DB commit + board push.
- **Create** `Puzzles.rename_group` cog command + `_resolve_group_for_rename` helper in `bot/cogs/puzzles.py`.
- **Create** `rename_tag_group` endpoint in `bot/web/actions.py`.
- **Create** `useRenameGroup` in `frontend/src/api.ts`.
- **Modify** `frontend/src/components/DragBoard.tsx` — inline editor on the column header.
- **Tests**: extend `tests/test_solve_equivalence.py` (service), `tests/test_group_type_consolidation.py` (cog) or a new `tests/test_rename_group.py`; new `tests/test_rename_group_web.py` (web). Frontend: build check only.

---

### Task 1: `category_layout.group_rename_plan` (pure planner)

**Files:**
- Modify: `bot/core/category_layout.py`
- Test: `tests/test_rename_group.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_rename_group.py`:

```python
from types import SimpleNamespace

import pytest

from bot.core import category_layout
from tests.conftest import make_round


class _FakeCat:
    def __init__(self, id, name, text_channels=()):
        self.id = id
        self.name = name
        self.text_channels = list(text_channels)


class _FakeChan:
    def __init__(self, id, name):
        self.id = id
        self.name = name


class _FakeGuild:
    def __init__(self, categories=(), channels=()):
        self.categories = list(categories)
        self._channels = {c.id: c for c in channels}

    def get_channel(self, cid):
        return self._channels.get(cid)


class _FakeDao:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def get_for_group(self, group_id):
        return self._rows


def test_plan_meta_single_category_renames_only_category():
    # has_target -> no text channel; single category (no split rows).
    cat = _FakeCat(id=999, name="old-round")
    guild = _FakeGuild(categories=[cat])
    group = make_round(id=1, name="old round", target_puzzle_id=7, category_id=999)
    plan = category_layout.group_rename_plan(guild, group, "New Round", _FakeDao())
    assert plan == [(cat, "new-round")]


def test_plan_metaless_renames_category_and_text_channel():
    chan = _FakeChan(id=500, name="old-group")
    cat = _FakeCat(id=999, name="old-group", text_channels=[chan])
    guild = _FakeGuild(categories=[cat])
    group = make_round(id=2, name="old group", target_puzzle_id=0, category_id=999)
    plan = category_layout.group_rename_plan(guild, group, "New Group", _FakeDao())
    assert plan == [(cat, "new-group"), (chan, "new-group")]


def test_plan_split_round_renames_all_categories():
    from bot.store.tag_group_category_data import ROLE_SOLVED, ROLE_UNSOLVED
    unsolved = _FakeCat(id=10, name="old-round")
    solved = _FakeCat(id=11, name="old-round — solved")
    guild = _FakeGuild(categories=[unsolved, solved])
    group = make_round(id=3, name="old round", target_puzzle_id=7, category_id=10)
    rows = [
        SimpleNamespace(category_id=10, role=ROLE_UNSOLVED, seq=0),
        SimpleNamespace(category_id=11, role=ROLE_SOLVED, seq=0),
    ]
    plan = category_layout.group_rename_plan(guild, group, "New Round", _FakeDao(rows))
    assert (unsolved, "new-round") in plan
    assert (solved, "new-round — solved") in plan
    assert len(plan) == 2  # has_target -> no text channel
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_rename_group.py -q > /tmp/test-rename-group.log 2>&1`
Expected: FAIL — `AttributeError: module 'bot.core.category_layout' has no attribute 'group_rename_plan'`.

- [ ] **Step 3: Implement the planner**

Add to `bot/core/category_layout.py` (after `delete_group_categories`, near the other group helpers). `discord`, `clean_name`, `base_name`, `primary_category`, `_category_by_id`, `group_categories`, `overflow_category_name` are already in this module:

```python
def group_rename_plan(guild, tag_group, new_name, dao):
    """Return the ordered list of ``(discord_object, new_name)`` renames for a
    group rename — GROUP ONLY.

    Renames every category the group owns (the single primary category, or every
    split unsolved/solved/overflow category) and, for a metaless Group, its
    dedicated text channel. NEVER includes the target puzzle's channel. Objects
    Discord can no longer resolve are skipped. Forum layout (category_id is the
    text channel) is handled best-effort but is out of active scope.
    """
    new_base = clean_name(new_name)
    old_base = base_name(guild, tag_group)
    plan = []

    rows = group_categories(tag_group, dao)
    if rows:
        for row in rows:
            cat = _category_by_id(guild, row.category_id)
            if cat is not None:
                plan.append((cat, overflow_category_name(new_base, row.role, row.seq)))
    else:
        primary = primary_category(guild, tag_group)
        if primary is not None:
            plan.append((primary, new_base))

    if not tag_group.has_target:
        primary = primary_category(guild, tag_group)
        if primary is not None:
            # Channel-mode Group: the dedicated text channel lives inside the
            # category, created with the same clean_name as the category.
            ch = discord.utils.get(primary.text_channels, name=old_base)
            if ch is not None:
                plan.append((ch, new_base))
        else:
            # Forum-mode Group: category_id IS the text channel.
            ch = guild.get_channel(tag_group.category_id)
            if ch is not None:
                plan.append((ch, new_base))

    return plan
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_rename_group.py -q > /tmp/test-rename-group.log 2>&1`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/core/category_layout.py tests/test_rename_group.py
git commit -m "feat(rename-group): add category_layout.group_rename_plan planner"
```

---

### Task 2: `PuzzleService.rename_group`

**Files:**
- Modify: `bot/core/puzzle_service.py` (add method near `rename_puzzle` ~line 1357)
- Test: `tests/test_solve_equivalence.py` (add a `TestRenameGroup` class, mirroring `TestWebRenamePuzzle`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_solve_equivalence.py` (it already imports `MagicMock, patch`, `pytest`, `make_hunt`, `make_round`, and defines `_make_cog`, `_no_guild`). Add `AsyncMock` to the `unittest.mock` import if absent:

```python
class TestRenameGroup:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return make_hunt()

    async def _run(self, cog, hunt, group, name, guild=None):
        commits = []
        with patch("bot.cogs.puzzles.TagGroupJsonDb") as mock_rdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb", mock_rdb):
            mock_rdb.commit.side_effect = lambda r: commits.append(("group", r.id))
            await cog.service.rename_group(guild, hunt, group, name)
        return commits

    @pytest.mark.asyncio
    async def test_sets_name(self, cog, hunt):
        grp = make_round(id=1, name="Old", target_puzzle_id=7)
        await self._run(cog, hunt, grp, "New Name")
        assert grp.name == "New Name"

    @pytest.mark.asyncio
    async def test_commits_group(self, cog, hunt):
        grp = make_round(id=42, name="Old", target_puzzle_id=7)
        commits = await self._run(cog, hunt, grp, "New")
        assert ("group", 42) in commits

    @pytest.mark.asyncio
    async def test_guild_none_skips_discord(self, cog, hunt):
        # With guild=None the planner/safe_rename must not be called.
        grp = make_round(id=3, name="Old", target_puzzle_id=7)
        with patch("bot.core.puzzle_service.category_layout") as mock_cl:
            cog.service.safe_rename = AsyncMock()
            with patch("bot.cogs.puzzles.TagGroupJsonDb") as mock_rdb, \
                 patch("bot.core.puzzle_service.TagGroupJsonDb", mock_rdb):
                await cog.service.rename_group(None, hunt, grp, "New")
            mock_cl.group_rename_plan.assert_not_called()
            cog.service.safe_rename.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_guild_renames_each_container(self, cog, hunt):
        grp = make_round(id=4, name="Old", target_puzzle_id=0)
        obj_a, obj_b = MagicMock(), MagicMock()
        guild = MagicMock()
        cog.service.safe_rename = AsyncMock()
        with patch("bot.core.puzzle_service.category_layout") as mock_cl, \
             patch("bot.cogs.puzzles.TagGroupJsonDb") as mock_rdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb", mock_rdb):
            mock_cl.group_rename_plan.return_value = [(obj_a, "new"), (obj_b, "new")]
            await cog.service.rename_group(guild, hunt, grp, "New")
        assert cog.service.safe_rename.await_count == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_solve_equivalence.py::TestRenameGroup -q > /tmp/test-rename-group.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleService' object has no attribute 'rename_group'`.

- [ ] **Step 3: Implement the service method**

Add to `bot/core/puzzle_service.py`, immediately after `rename_puzzle` (~line 1391). `category_layout` and `TagGroupCategoryDb` are already imported at the top of the module:

```python
    @pushes_board()
    async def rename_group(self, guild, hunt, tag_group, name):
        """Rename a group: rename its Discord container(s) and set tag_group.name.

        GROUP ONLY — never renames the target puzzle, its channel, or its sheet
        tab. Caller must check for duplicate names before calling. Discord renames
        are skipped when guild is None (web/test path). Plan is computed from the
        group's CURRENT name, so it must run before the name is mutated.
        """
        if guild is not None:
            for obj, new_name in category_layout.group_rename_plan(
                guild, tag_group, name, TagGroupCategoryDb
            ):
                await self.safe_rename(obj, new_name)

        tag_group.name = name
        TagGroupJsonDb.commit(tag_group)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_solve_equivalence.py::TestRenameGroup -q > /tmp/test-rename-group.log 2>&1`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_solve_equivalence.py
git commit -m "feat(rename-group): add PuzzleService.rename_group"
```

---

### Task 3: `!rename_group` cog command

**Files:**
- Modify: `bot/cogs/puzzles.py` (add command near `rename_puzzle` ~line 2295; add `_resolve_group_for_rename` helper near the accessors ~line 341)
- Test: `tests/test_rename_group.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rename_group.py`:

```python
from unittest.mock import AsyncMock, patch

from tests.conftest import make_cog, make_ctx, make_hunt, make_puzzle


def _rename_cog(channel_type, tag_group, puzzle=None):
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.get_channel_type = lambda ctx: channel_type
    cog.get_tag_group = lambda ctx: tag_group
    cog.get_puzzle = lambda ctx: puzzle
    cog.get_hunt = lambda ctx: make_hunt()
    cog.service.rename_group = AsyncMock()
    return cog, Puzzles


@pytest.mark.asyncio
async def test_rename_group_from_target_puzzle_channel():
    grp = make_round(id=7, name="Old", target_puzzle_id=70)
    puz = make_puzzle(id=70)
    cog, Puzzles = _rename_cog("Metapuzzle", grp, puz)
    ctx = make_ctx()
    ctx.channel.send = AsyncMock()  # success path awaits ctx.channel.send
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.rename_group.callback(cog, ctx, group_name="New Name")
    cog.service.rename_group.assert_awaited_once()
    assert cog.service.rename_group.await_args.args[3] == "New Name"


@pytest.mark.asyncio
async def test_rename_group_from_regular_group_channel():
    grp = make_round(id=8, name="Old", target_puzzle_id=0)
    cog, Puzzles = _rename_cog("Group", grp, None)
    ctx = make_ctx()
    ctx.channel.send = AsyncMock()  # success path awaits ctx.channel.send
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.rename_group.callback(cog, ctx, group_name="New Name")
    cog.service.rename_group.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_group_rejects_non_group_channel():
    # In a feeder puzzle channel: tag_group present but its target != this puzzle.
    grp = make_round(id=9, name="Old", target_puzzle_id=70)
    puz = make_puzzle(id=99)  # not the target
    cog, Puzzles = _rename_cog("Puzzle", grp, puz)
    ctx = make_ctx()
    await Puzzles.rename_group.callback(cog, ctx, group_name="New Name")
    cog.service.rename_group.assert_not_awaited()
    assert ctx.send.await_count == 1


@pytest.mark.asyncio
async def test_rename_group_rejects_duplicate_name():
    grp = make_round(id=10, name="Old", target_puzzle_id=70)
    puz = make_puzzle(id=70)
    cog, Puzzles = _rename_cog("Metapuzzle", grp, puz)
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.check_duplicates_in_hunt.return_value = True
        await Puzzles.rename_group.callback(cog, ctx, group_name="Dupe")
    cog.service.rename_group.assert_not_awaited()
    assert ctx.send.await_count == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_rename_group.py -q > /tmp/test-rename-group.log 2>&1`
Expected: FAIL — `AttributeError: ... 'Puzzles' ... has no attribute 'rename_group'`.

- [ ] **Step 3: Implement the helper + command**

Add the helper next to the accessors in `bot/cogs/puzzles.py` (after `set_puzzle`, ~line 341):

```python
    def _resolve_group_for_rename(self, ctx):
        """Resolve the group to rename from the current channel, or None.

        Two valid contexts: a regular group's main text channel (channel_type
        'Group'), or a group's TARGET puzzle channel (the resolved tag_group's
        target_puzzle_id matches the current puzzle). Feeder/other channels -> None.
        """
        tag_group = self.get_tag_group(ctx)
        if tag_group is None:
            return None
        if self.get_channel_type(ctx) == "Group":
            return tag_group
        puzzle = self.get_puzzle(ctx)
        if puzzle is not None and tag_group.target_puzzle_id == puzzle.id:
            return tag_group
        return None
```

Add the command near `rename_puzzle` (~line 2318). Note: NO `@with_puzzle_mutex` (groups aren't in the per-puzzle lock registry — mirror `delete_round`, which is also lock-free for group ops):

```python
    @commands.hybrid_command(aliases=["rename_round", "rename_metapuzzle"],
                             description="Rename this group, its category and channel")
    @commands.has_any_role(*ORGANISER_ROLES)
    async def rename_group(self, ctx, *, group_name: str):
        """*Rename this group and its Discord category/channel.*

        Usage: `!rename_group new name`
        Example: `!rename_group the wonderland round`
        Run from the group's target-puzzle channel, or from a regular group's
        channel. Organisers/admins only. Aliases: `!rename_round`,
        `!rename_metapuzzle`. Does NOT rename the target puzzle."""
        tag_group = self._resolve_group_for_rename(ctx)
        if tag_group is None:
            await ctx.send(
                ":x: Run this from a group's target-puzzle channel, or from a "
                "regular group's channel.")
            return
        if TagGroupJsonDb.check_duplicates_in_hunt(group_name, self.get_hunt(ctx)):
            await ctx.send(
                f":exclamation: A group called **{group_name}** already exists in "
                f"this hunt, please use a different name")
            return
        await self.service.rename_group(
            ctx.guild, self.get_hunt(ctx), tag_group, group_name)
        await ctx.channel.send(":white_check_mark: I've renamed the group.")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_rename_group.py -q > /tmp/test-rename-group.log 2>&1`
Expected: PASS (all rename-group tests).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_rename_group.py
git commit -m "feat(rename-group): add !rename_group cog command"
```

---

### Task 4: `POST /api/tag_groups/<id>/rename` web endpoint

**Files:**
- Modify: `bot/web/actions.py` (add endpoint near `delete_tag_group` ~line 1285)
- Test: `tests/test_rename_group_web.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_rename_group_web.py` (mirrors `tests/test_delete_puzzle_web.py`):

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
    cog.service.rename_group = AsyncMock()
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    rdb = MagicMock()
    rdb.get_by_attr.return_value = make_round(id=5, name="Old")
    rdb.check_duplicates_in_hunt.return_value = False
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
async def test_rename_group_success(web):
    resp = await web.client.post("/api/tag_groups/5/rename", json={"name": "New Name"})
    assert resp.status_code == 200
    assert (await resp.get_json())["ok"] is True
    web.cog.service.rename_group.assert_awaited_once()
    assert web.cog.service.rename_group.await_args.args[3] == "New Name"


@pytest.mark.asyncio
async def test_rename_group_empty_name_400(web):
    resp = await web.client.post("/api/tag_groups/5/rename", json={"name": "  "})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "empty_name"
    web.cog.service.rename_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_group_duplicate_400(web):
    web.rdb.check_duplicates_in_hunt.return_value = True
    resp = await web.client.post("/api/tag_groups/5/rename", json={"name": "Dupe"})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "duplicate_name"
    web.cog.service.rename_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_group_archived_403(web):
    web.ctx["hunt"]["archived"] = True
    resp = await web.client.post("/api/tag_groups/5/rename", json={"name": "New"})
    assert resp.status_code == 403
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_rename_group_web.py -q > /tmp/test-rename-group.log 2>&1`
Expected: FAIL — 404 (route not registered) on the success test.

- [ ] **Step 3: Implement the endpoint**

Add to `bot/web/actions.py`, after `delete_tag_group` (~line 1285). Mirrors `delete_tag_group`'s guards + `rename_puzzle`'s name/duplicate validation; lock-free like `delete_tag_group`:

```python
@actions_bp.route("/tag_groups/<int:tag_group_id>/rename", methods=["POST"])
@login_required
async def rename_tag_group(tag_group_id):
    """Rename a tag group + its Discord container(s), via the bot (mirrors
    !rename_group). GROUP ONLY — never renames the target puzzle. Organiser+ only."""
    ctx, err = await _visible_hunt_for_tag_group(tag_group_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm is not None:
        return perm

    body = await request.get_json(silent=True) or {}
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import HuntJsonDb, TagGroupJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if TagGroupJsonDb.check_duplicates_in_hunt(new_name, hunt_obj):
        return jsonify({"error": "duplicate_name"}), 400

    tag_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if tag_group is None:
        return jsonify({"error": "round_not_found"}), 404

    cog = current_app.bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = current_app.bot.get_guild(int(snowflake)) if snowflake else None

    try:
        await cog.service.rename_group(guild, hunt_obj, tag_group, new_name)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("rename_group", hunt_uid=ctx["hunt"]["uid"],
                 detail=f"tag_group_id={tag_group_id} name={new_name}")
    return jsonify({"ok": True})
```

(Verify `_discord_error_response` is already used by `delete_tag_group` in this file — it is. If `discord` is not imported at module top, it is — `import discord` at `actions.py:11`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_rename_group_web.py -q > /tmp/test-rename-group.log 2>&1`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/web/actions.py tests/test_rename_group_web.py
git commit -m "feat(rename-group): add POST /api/tag_groups/<id>/rename endpoint"
```

---

### Task 5: Frontend `useRenameGroup` hook

**Files:**
- Modify: `frontend/src/api.ts` (add near `useRenamePuzzle` ~line 213)

- [ ] **Step 1: Add the hook**

Add to `frontend/src/api.ts` (next to `useRenamePuzzle`):

```ts
export function useRenameGroup(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tagGroupId, name }: { tagGroupId: number; name: string }) =>
      apiSend<{ ok: boolean }>(
        `/api/tag_groups/${tagGroupId}/rename`, "POST", { name },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build > /tmp/fe-build.log 2>&1`
Expected: exit 0 (`tsc --noEmit` clean, vite build succeeds). Read `/tmp/fe-build.log` on failure.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(rename-group): add useRenameGroup api hook"
```

---

### Task 6: Frontend Arrange inline editor

**Files:**
- Modify: `frontend/src/components/DragBoard.tsx`

- [ ] **Step 1: Wire the hook + callback into `DragBoard`**

In `frontend/src/components/DragBoard.tsx`:

1. Add `TextInput` to the `@mantine/core` import block; add `IconEdit` to the `@tabler/icons-react` import (alongside `IconDotsVertical`).
2. Add `useRenameGroup` to the `../api` import block.
3. Inside `DragBoard` (near the other hook instantiations ~line 366-375), add:

```tsx
  const renameGroup = useRenameGroup(uid);

  function onRenameGroup(tagGroupId: number, name: string) {
    renameGroup.mutate(
      { tagGroupId, name },
      {
        onSuccess: () =>
          notifications.show({ message: "Group renamed.", color: "green" }),
        onError: notifyError,
      },
    );
  }
```

4. Pass it down where `Column` is rendered (~line 572): add the prop `onRenameGroup={onRenameGroup}` to the `<Column ... />` element.

- [ ] **Step 2: Add the inline editor to `Column`**

Replace the `Column` signature and its header (`DragBoard.tsx:325` and the `group.name` `<Text>` block ~346-356) so the name becomes click-to-edit. The Unassigned column (`group_code == null`) stays non-editable. Mirror the `PuzzleDrawer` title editor:

```tsx
function Column({
  group,
  children,
  onRenameGroup,
}: {
  group: BoardGroup;
  children: ReactNode;
  onRenameGroup: (tagGroupId: number, name: string) => void;
}) {
  const isUnassigned = group.group_code == null;
  const [editing, setEditing] = useState(false);
  const [temp, setTemp] = useState("");
  const { setNodeRef, isOver } = useDroppable({
    id: `meta:${group.group_code}`,
    data: { groupCode: group.group_code },
  });
```

Then, in the header, replace the existing name `<Text fw={600} size="sm" lineClamp={1}>{group.name}</Text>` with:

```tsx
          {editing ? (
            <TextInput
              size="xs"
              value={temp}
              onChange={(e) => setTemp(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const newName = temp.trim();
                  if (newName && newName !== group.name) {
                    onRenameGroup(group.tag_group_id, newName);
                  }
                  setEditing(false);
                }
                if (e.key === "Escape") setEditing(false);
              }}
              onBlur={() => setEditing(false)}
              autoFocus
            />
          ) : (
            <Text
              fw={600}
              size="sm"
              lineClamp={1}
              style={isUnassigned ? undefined : { cursor: "pointer" }}
              onClick={
                isUnassigned
                  ? undefined
                  : () => {
                      setEditing(true);
                      setTemp(group.name);
                    }
              }
            >
              {group.name}
            </Text>
          )}
```

(Leave the `group_code` badge and the `num_solved_feeders/num_feeders` text exactly as they are. `useState` is already imported at the top of the file.)

- [ ] **Step 3: Type-check**

Run: `cd frontend && npm run build > /tmp/fe-build.log 2>&1`
Expected: exit 0. Read `/tmp/fe-build.log` on failure (watch for unused-import or prop-type errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/DragBoard.tsx
git commit -m "feat(rename-group): inline group rename editor on Arrange tab"
```

---

### Task 7: Full verification

- [ ] **Step 1: Run the full backend suite**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test-rename-group.log 2>&1`
Expected: PASS (no regressions). On failure, read the tail of `/tmp/test-rename-group.log`; isolate any wedged file under its own `timeout`.

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npm run build > /tmp/fe-build.log 2>&1`
Expected: exit 0.

- [ ] **Step 3: Review the diff**

Run: `git --no-pager log --oneline staging..HEAD` and `git --no-pager diff staging..HEAD --stat` — confirm 6 feature commits touch only the intended files.

---

## Verification

- Backend: `timeout 300 pipenv run python -m pytest tests/ -q` is green.
- Frontend: `cd frontend && npm run build` exits 0.
- Manual acceptance (per spec): `!rename_group` from a target-puzzle channel renames the category + group name, leaves the target puzzle alone; from a regular group's channel renames the category + its text channel; an unrelated channel errors; the Arrange tab inline editor renames any non-Unassigned group live.

## Rollback / recovery notes

- All work is on `feature/rename-group` (off `staging`). Each task is its own commit; revert individually if needed.
- No DB schema/migration changes — pure additive code. No `group_code`/edge/sheet changes.
