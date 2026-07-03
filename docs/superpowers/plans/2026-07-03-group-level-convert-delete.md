# Group-level Convert & Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make tag-group type changes and deletion group-level operations driven from a per-group kebab in the dashboard Arrange view, and fix convert-to-Group so it fully removes the metapuzzle.

**Architecture:** A group-keyed service seam (`convert_tag_group_type`) dispatches type changes; converting a targeted group → Group tears down the meta (record + sheet + channel repurpose + pin), and a bare Group → targeted type creates a meta (delete old chat channel, create fresh via `create_puzzle`). A new organiser-gated web route exposes it; the board payload gains `group_type` + `target_puzzle_id`; the Arrange view grows a group kebab and the target-puzzle card shrinks to "Add to group…".

**Tech Stack:** Python (discord.py 2.7.1, MySQL, Google Sheets), Quart web routes, React + Mantine + @tanstack/react-query frontend.

**Reference spec:** `docs/superpowers/specs/2026-07-03-group-level-convert-delete-design.md`

**Test command (always):**
```bash
timeout 180 pipenv run python -m pytest <targets> -q > /tmp/test.log 2>&1
```
Read `/tmp/test.log` for results (killed run exits 124). Frontend build check: `cd frontend && npx tsc --noEmit`.

---

## Phase 1 — Backend: fix convert-to-Group teardown (fixes the reported bug on both bang + web)

### Task 1: `notifier.refresh_group_info` — refresh a bare-Group channel's pin

**Files:**
- Modify: `bot/core/notifier.py` (add method to `Notifier`, `NullNotifier`, `DiscordNotifier`, `CompositeNotifier`)
- Test: `tests/test_notifier.py` (create if absent; otherwise append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notifier.py
from unittest.mock import AsyncMock, MagicMock
import pytest
from bot.core.notifier import DiscordNotifier
from tests.conftest import make_hunt, make_round


@pytest.mark.asyncio
async def test_refresh_group_info_updates_group_pin():
    bot = MagicMock()
    cog = MagicMock()
    cog.send_initial_group_channel_messages = AsyncMock()
    bot.get_cog.return_value = cog
    guild = MagicMock()
    channel = MagicMock()
    guild.get_channel_or_thread.return_value = channel
    n = DiscordNotifier(bot)
    tag_group = make_round(id=5, category_id=222)
    await n.refresh_group_info(guild, make_hunt(), tag_group)
    cog.send_initial_group_channel_messages.assert_awaited_once()
    # update=True refreshes the existing pin in place (not a new message)
    assert cog.send_initial_group_channel_messages.await_args.kwargs["update"] is True


@pytest.mark.asyncio
async def test_null_notifier_has_refresh_group_info():
    from bot.core.notifier import NullNotifier
    await NullNotifier().refresh_group_info(None, None, None)  # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_notifier.py -q > /tmp/test.log 2>&1`
Expected: FAIL (`AttributeError: ... refresh_group_info`).

- [ ] **Step 3: Implement**

The bare-Group channel is resolved from `tag_group.category_id`. In channel-mode a Group's
`category_id` is a real category id whose general text channel is `group_chat_channel`; in
forum-mode it is the group's own text channel id. Resolve via `category_layout.group_chat_channel`
first, falling back to `guild.get_channel_or_thread(tag_group.category_id)`.

Add to `Notifier` (abstract), `NullNotifier` (no-op), `CompositeNotifier` (fan-out), and
`DiscordNotifier`:

```python
# Notifier (abstract) — add alongside refresh_info:
    @abc.abstractmethod
    async def refresh_group_info(self, guild, hunt, tag_group) -> None:
        """Refresh a bare-Group channel's pinned welcome after a type change."""

# NullNotifier:
    async def refresh_group_info(self, guild=None, hunt=None, tag_group=None) -> None:
        return None

# CompositeNotifier:
    async def refresh_group_info(self, guild, hunt, tag_group) -> None:
        for n in self._notifiers:
            await n.refresh_group_info(guild, hunt, tag_group)

# DiscordNotifier:
    async def refresh_group_info(self, guild, hunt, tag_group) -> None:
        if guild is None or tag_group is None:
            return
        cog = self._cog()
        if cog is None:
            return
        from bot.core import category_layout
        channel = category_layout.group_chat_channel(guild, tag_group)
        if channel is None:
            channel = guild.get_channel_or_thread(getattr(tag_group, "category_id", 0))
        if channel is None:
            return
        try:
            from bot.store import GuildSettingsDb
            await cog.send_initial_group_channel_messages(
                None, channel,
                hunt=hunt, tag_group=tag_group, update=True,
                guild_data=GuildSettingsDb.get(guild.id),
            )
        except Exception:
            logger.exception("refresh_group_info: failed for group %s", tag_group.id)
```

Ensure `logger` is defined in `notifier.py` (it is — module already logs).

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_notifier.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/core/notifier.py tests/test_notifier.py
git commit -m "feat(notifier): refresh_group_info — refresh a bare-Group channel pin"
```

---

### Task 2: `_teardown_group_target` + wire into `convert_to_type` Group branch

**Files:**
- Modify: `bot/core/puzzle_service.py` (add `_teardown_group_target`; call it in `convert_to_type`)
- Test: `tests/test_convert_service.py` (extend `TestConvertToType`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_convert_service.py`. These drive the already-grouped Group path with a real
target puzzle. Reuse the file's existing `_make_cog_convert`, `make_puzzle`, `make_round`,
`make_hunt` imports.

```python
class TestConvertToGroupTeardown:
    @pytest.fixture
    def cog(self):
        return _make_cog_convert()

    async def _run(self, cog, gsheet=None):
        from tests.conftest import make_hunt, make_puzzle, make_round
        puz = make_puzzle(id=7, is_target=True, channel_id=555)
        group = make_round(id=42, name="Alpha", target_puzzle_id=7, category_id=999)
        group.type = "Metapuzzle"
        cog.service.set_source_mode = AsyncMock()
        cog.service.bulk_re_edge = AsyncMock(return_value="flipped 2 feeds → none")
        cog.service.notifier = MagicMock()
        cog.service.notifier.board_changed = AsyncMock()
        cog.service.notifier.refresh_group_info = AsyncMock()
        cog.service.safe_rename = AsyncMock(return_value=True)
        if gsheet is not None:
            cog.bot.get_cog.return_value = gsheet
        hunt = make_hunt()
        guild = MagicMock()
        channel = MagicMock(); channel.id = 555
        guild.get_channel.return_value = channel
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: None
            mrdb.get_by_attr.return_value = group
            mhdb.get_by_attr.return_value = hunt
            await cog.service.convert_to_type(guild, hunt, puz, "Group")
        return puz, group, mpdb, guild

    @pytest.mark.asyncio
    async def test_deletes_target_sheet(self, cog):
        gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()
        gsheet.promote_tab_to_group = AsyncMock()
        await self._run(cog, gsheet=gsheet)
        gsheet.delete_puzzle_spreadsheet.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deletes_target_record_and_clears_target(self, cog):
        _puz, group, mpdb, _guild = await self._run(cog)
        mpdb.delete.assert_called_once_with(7)
        assert group.target_puzzle_id == 0

    @pytest.mark.asyncio
    async def test_renames_channel_and_refreshes_group_pin(self, cog):
        _puz, _group, _mpdb, _guild = await self._run(cog)
        cog.service.safe_rename.assert_awaited_once()
        cog.service.notifier.refresh_group_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_group_conversion_does_not_delete_target(self, cog):
        # Regression guard: converting to Capstone must NOT tear down the target.
        from tests.conftest import make_hunt, make_puzzle, make_round
        puz = make_puzzle(id=7, is_target=True, channel_id=555)
        group = make_round(id=42, target_puzzle_id=7)
        group.type = "Metapuzzle"
        cog.service.set_source_mode = AsyncMock()
        cog.service.bulk_re_edge = AsyncMock(return_value="x")
        cog.service.notifier = MagicMock(); cog.service.notifier.board_changed = AsyncMock()
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: None
            mrdb.get_by_attr.return_value = group
            mhdb.get_by_attr.return_value = make_hunt()
            await cog.service.convert_to_type(None, make_hunt(), puz, "Capstone")
        mpdb.delete.assert_not_called()
        assert group.target_puzzle_id == 7
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_convert_service.py -q > /tmp/test.log 2>&1`
Expected: FAIL (target not deleted; `refresh_group_info`/`safe_rename` not awaited).

- [ ] **Step 3: Implement**

In `bot/core/puzzle_service.py`, add the helper (place it right after `convert_to_type`):

```python
    async def _teardown_group_target(self, guild, hunt, tag_group, target):
        """Convert a targeted group into a bare Group: delete the target meta's
        sheet + DB record, clear the group's target, and repurpose the meta's
        channel into the group's general chat channel (rename + group welcome pin).
        The DB mutation is the durable part; Discord/Sheets steps are best-effort.
        Non-forum only for the channel repurpose (forum is out of scope)."""
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            try:
                await gsheet_cog.delete_puzzle_spreadsheet(target)
            except Exception:
                logger.exception("_teardown_group_target: sheet delete failed for puzzle %s", target.id)

        channel_id = getattr(target, "channel_id", 0)
        PuzzleJsonDb.delete(target.id)
        tag_group.target_puzzle_id = 0
        TagGroupJsonDb.commit(tag_group)

        if guild is not None and channel_id and getattr(hunt, "layout_mode", None) != "forum":
            channel = guild.get_channel(channel_id)
            if channel is not None:
                await self.safe_rename(channel, clean_channel_name(tag_group.name))
                await self.notifier.refresh_group_info(guild, hunt, tag_group)
```

Then in `convert_to_type`, after the edge-flip block (the `if new_type != "Runaround": ... else: ...`
that sets `summary`) and before `await self._update_forum_tags(...)`, add:

```python
        # Group has no metapuzzle: tear down the former target now that the edge
        # flip (which excludes the target's self-edge) has run.
        if new_type == "Group" and tag_group.target_puzzle_id == puzzle.id:
            await self._teardown_group_target(guild, hunt, tag_group, puzzle)
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_convert_service.py -q > /tmp/test.log 2>&1`
Expected: PASS (all, including the 19 pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_convert_service.py
git commit -m "fix(convert): convert-to-Group tears down the metapuzzle (record+sheet+channel+pin)"
```

---

## Phase 2 — Backend: group-keyed seam + promote path + web route

### Task 3: `convert_tag_group_type` + `_promote_group_to_target`

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Test: `tests/test_convert_service.py` (new class `TestConvertTagGroupType`)

- [ ] **Step 1: Write the failing tests**

```python
class TestConvertTagGroupType:
    @pytest.fixture
    def cog(self):
        return _make_cog_convert()

    @pytest.mark.asyncio
    async def test_targeted_group_delegates_to_convert_to_type(self, cog):
        # A group with a target resolves the target and delegates to convert_to_type.
        from tests.conftest import make_hunt, make_puzzle, make_round
        target = make_puzzle(id=7, is_target=True)
        group = make_round(id=42, target_puzzle_id=7)
        cog.service.convert_to_type = AsyncMock(return_value=(group, "ok"))
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb:
            mpdb.get_by_attr.return_value = target
            g, summary = await cog.service.convert_tag_group_type(None, make_hunt(), group, "Group")
        cog.service.convert_to_type.assert_awaited_once()
        assert cog.service.convert_to_type.await_args.args[3] == "Group"
        assert summary == "ok"

    @pytest.mark.asyncio
    async def test_bare_group_to_metapuzzle_creates_meta(self, cog):
        from tests.conftest import make_hunt, make_puzzle, make_round
        group = make_round(id=42, name="Bag", target_puzzle_id=0, category_id=999)
        group.type = "Group"
        new_meta = make_puzzle(id=99, is_target=True)
        cog.service.create_puzzle = AsyncMock(return_value=new_meta)
        cog.service.set_source_mode = AsyncMock()
        cog.service.bulk_re_edge = AsyncMock(return_value="flipped 3 none → feeds")
        cog.service._safe_delete_channel = AsyncMock(return_value=True)
        cog.service.notifier = MagicMock(); cog.service.notifier.board_changed = AsyncMock()
        guild = MagicMock()
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
             patch("bot.core.puzzle_service.category_layout") as mcl:
            mpdb.commit.side_effect = lambda p: None
            mrdb.commit.side_effect = lambda r: None
            chat = MagicMock(); chat.id = 321
            mcl.group_chat_channel.return_value = chat
            g, summary = await cog.service.convert_tag_group_type(guild, make_hunt(), group, "Metapuzzle")
        cog.service.create_puzzle.assert_awaited_once()
        cog.service._safe_delete_channel.assert_awaited_once_with(guild, 321)
        assert group.target_puzzle_id == 99
        assert group.type == "Metapuzzle"

    @pytest.mark.asyncio
    async def test_bare_group_to_group_is_noop(self, cog):
        from tests.conftest import make_hunt, make_round
        group = make_round(id=42, target_puzzle_id=0)
        group.type = "Group"
        _g, summary = await cog.service.convert_tag_group_type(None, make_hunt(), group, "Group")
        assert "already" in summary.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_convert_service.py::TestConvertTagGroupType -q > /tmp/test.log 2>&1`
Expected: FAIL (`AttributeError: convert_tag_group_type`).

- [ ] **Step 3: Implement**

Add to `PuzzleService` (after `convert_to_type` / `_teardown_group_target`):

```python
    @pushes_board()
    async def convert_tag_group_type(self, guild, hunt, tag_group, new_type):
        """Change a TagGroup's type, keyed on the group (not its target puzzle).

        - Targeted group → any type: resolve the target and delegate to
          convert_to_type (which handles retype/re-edge and, for Group, teardown).
        - Bare Group → targeted type: create a meta (promote).
        Returns (tag_group, summary). Raises RuntimeError("<code>") on recoverable
        problems."""
        if tag_group.target_puzzle_id:
            target = PuzzleJsonDb.get_by_attr(id=tag_group.target_puzzle_id)
            if target is None:
                raise RuntimeError("target_missing")
            return await self.convert_to_type(guild, hunt, target, new_type)
        if new_type == "Group":
            return tag_group, "no change (already a group)"
        return await self._promote_group_to_target(guild, hunt, tag_group, new_type)

    async def _promote_group_to_target(self, guild, hunt, tag_group, new_type):
        """Bare Group → targeted type: create a meta puzzle for the group. Deletes
        the old general chat channel first so create_puzzle makes a fresh, fully
        linked meta channel (its name equals the group name, so a surviving general
        channel would be found with created=False and skip linking). Non-forum only."""
        tag_group.type = new_type
        TagGroupJsonDb.commit(tag_group)

        if guild is not None and getattr(hunt, "layout_mode", None) != "forum":
            chat = category_layout.group_chat_channel(guild, tag_group)
            if chat is not None:
                await self._safe_delete_channel(guild, chat.id)

        puzzle = await self.create_puzzle(
            guild, hunt, tag_group, tag_group.name, is_meta=True, notifier=self.notifier,
        )
        tag_group.target_puzzle_id = puzzle.id
        TagGroupJsonDb.commit(tag_group)

        default_sm = TYPE_DEFAULTS[new_type]["default_source_mode"]
        await self.set_source_mode(hunt, tag_group, default_sm)
        default_edge = TYPE_DEFAULTS[new_type]["default_edge"]
        summary = await self.bulk_re_edge(tag_group, default_edge, hunt)
        return tag_group, summary
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_convert_service.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_convert_service.py
git commit -m "feat(convert): convert_tag_group_type — group-keyed type change + bare-Group promote"
```

---

### Task 4: Web route `POST /api/tag_groups/<id>/change-type`

**Files:**
- Modify: `bot/web/actions.py` (add route after `rename_tag_group`)
- Test: `tests/test_actions_convert.py` (create) OR extend the existing web-action test file if one covers convert routes — check `tests/` first with `grep -rl convert_to_type tests/`.

- [ ] **Step 1: Write the failing test**

Mirror the existing convert-route tests. First run
`grep -rln "change-type\|convert_to_type\|_convert_to_type_handler\|delete_tag_group" tests/` to find
the established pattern and matching fixtures; model the new test on the closest sibling
(`delete_tag_group` route test is the best template — same `_visible_hunt_for_tag_group` +
`require_organiser` shape). The test asserts: a POST with `{"type":"Group"}` on a valid group calls
`cog.service.convert_tag_group_type(guild, hunt, tag_group, "Group")` and returns `{ok:true}`; an
unknown type returns 400; a non-organiser is rejected by `require_organiser`.

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_actions_convert.py -q > /tmp/test.log 2>&1`
Expected: FAIL (route missing → 404, or import error).

- [ ] **Step 3: Implement**

Add after `rename_tag_group` in `bot/web/actions.py`. Validate `type` against the canonical set
(`GROUP_TYPES` + input aliases). Import `GROUP_TYPES`, `GROUP_TYPE_INPUT_ALIASES` from
`bot.core.puzzle_service` (or reference via the cog's module). Follow `delete_tag_group`'s scaffolding
exactly (visible-hunt, archived guard, `require_organiser`, cog/guild resolution, `puzzles_lock`
over members), then:

```python
@actions_bp.route("/tag_groups/<int:tag_group_id>/change-type", methods=["POST"])
@login_required
async def change_group_type(tag_group_id):
    """Change a tag group's type (Metapuzzle/Capstone/Runaround/Group), keyed on
    the group. Organiser-gated (matches delete/rename). Body {"type": "<label>"}.
    Group-menu equivalent of the puzzle-keyed convert bang commands."""
    from bot.core.puzzle_service import GROUP_TYPES, GROUP_TYPE_INPUT_ALIASES
    ctx, err = await _visible_hunt_for_tag_group(tag_group_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    raw_type = body.get("type")
    new_type = GROUP_TYPE_INPUT_ALIASES.get(raw_type, raw_type)
    if new_type not in GROUP_TYPES:
        return jsonify({"error": "invalid_type"}), 400

    from bot.store import HuntJsonDb, PuzzleJsonDb, TagGroupJsonDb
    tag_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if tag_group is None:
        return jsonify({"error": "round_not_found"}), 404
    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    cog = current_app.bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = current_app.bot.get_guild(int(snowflake)) if snowflake else None
    member_ids = [p.id for p in PuzzleJsonDb.get_all_from_tag_group(tag_group.id)]

    warning = None
    try:
        async with puzzles_lock(member_ids):
            _group, summary = await cog.service.convert_tag_group_type(
                guild, hunt_obj, tag_group, new_type)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    except SheetRefreshError as exc:
        warning = str(exc)

    asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
    await _audit("change_group_type", hunt_uid=ctx["hunt"]["uid"],
                 detail=f"tag_group_id={tag_group_id} type={new_type}")
    resp = {"ok": True, "summary": summary}
    if warning:
        resp["sheet_warning"] = warning
    return jsonify(resp)
```

Verify `puzzles_lock`, `refresh_unassigned_safe`, `require_organiser`, `_visible_hunt_for_tag_group`,
`_discord_error_response`, `SheetRefreshError` are already imported in `actions.py` (they are — used
by `delete_tag_group` and the convert handlers). Add any missing import.

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_actions_convert.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/web/actions.py tests/test_actions_convert.py
git commit -m "feat(web): POST /tag_groups/<id>/change-type — group-keyed type change"
```

---

## Phase 3 — Backend: board payload fields

### Task 5: expose `group_type` + `target_puzzle_id` on board groups

**Files:**
- Modify: `bot/web/data.py` (`build_board` → `ensure`)
- Test: `tests/test_build_board.py` (create) or the existing build_board test file (`grep -rl build_board tests/`)

- [ ] **Step 1: Write the failing test**

Find the existing build_board test with `grep -rln build_board tests/` and model on it. Assert that a
group in the returned board carries `group_type` (from `all_groups[i]["type"]`) and
`target_puzzle_id`. Minimal shape:

```python
def test_build_board_exposes_group_type_and_target():
    from bot.web.data import build_board
    from tests.conftest import make_puzzle_row, make_round_ref
    all_groups = [{"id": 5, "name": "Alpha", "group_code": "AL", "type": "Group", "target_puzzle_id": 0}]
    board = build_board(puzzles=[], all_groups=all_groups)
    grp = next(g for g in board if g["tag_group_id"] == 5)
    assert grp["group_type"] == "Group"
    assert grp["target_puzzle_id"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_build_board.py -q > /tmp/test.log 2>&1`
Expected: FAIL (`KeyError: 'group_type'`).

- [ ] **Step 3: Implement**

In `build_board`, the `ensure(...)` inner function already has `group_info` in scope
(`group_info = {g["id"]: g for g in (all_groups or [])}`). Add two keys to the dict it builds:

```python
            info = group_info.get(tag_group_id, {})
            groups[tag_group_id] = {
                # ... existing keys ...
                "group_type": info.get("type"),
                "target_puzzle_id": info.get("target_puzzle_id") or 0,
            }
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_build_board.py tests/ -q -k "board" > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/web/data.py tests/test_build_board.py
git commit -m "feat(web): board groups expose group_type + target_puzzle_id"
```

---

## Phase 4 — Frontend: Arrange-view kebab + slimmed target card

### Task 6: types + api hook

**Files:**
- Modify: `frontend/src/types.ts` (`BoardGroup`)
- Modify: `frontend/src/api.ts` (add `useChangeGroupType`)

- [ ] **Step 1: Add types**

In `frontend/src/types.ts`, add to `interface BoardGroup`:

```ts
  group_type: string | null;
  target_puzzle_id: number | null;
```

- [ ] **Step 2: Add the hook**

In `frontend/src/api.ts`, following the `useRenameGroup` pattern:

```ts
export function useChangeGroupType(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tagGroupId, type }: { tagGroupId: number; type: string }) =>
      apiSend<{ ok: boolean; summary?: string; sheet_warning?: string }>(
        `/api/tag_groups/${tagGroupId}/change-type`, "POST", { type },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors (unused-hook warning is fine until Task 7 wires it).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(web): BoardGroup type fields + useChangeGroupType hook"
```

---

### Task 7: Group kebab menu in the Arrange `Column`

**Files:**
- Modify: `frontend/src/components/DragBoard.tsx` (`Column` component + `DragBoard` wiring)

- [ ] **Step 1: Implement the kebab**

In `DragBoard.tsx`, add to the `Column` header (the `<Group justify="space-between">` at ~line 368) a
Mantine `Menu` with an `IconDotsVertical` target, rendered only when `!isUnassigned`. Pass new
callbacks from `DragBoard` into `Column`:
- `onChangeType(tagGroupId, type)` → `useChangeGroupType`
- `onConvertToFeeder(targetPuzzleId)` → existing `useConvertToFeeder` (+ `useConvertToFeederPreview`
  for the orphan count in the confirm)
- `onDeleteGroup(group)` → open the existing `TypedConfirmModal` delete-round flow (reuse the exact
  modal shape from `BoardGroup.tsx:369-402`: keep/delete-all radio + `useDeleteRound`).

Menu contents (derive `isTargeted = (group.target_puzzle_id ?? 0) > 0`; the four labels come from
`detail.meta_types` mapped through the alias, or hardcode `["Metapuzzle","Capstone","Runaround","Group"]`
minus the current `group.group_type`):

```tsx
<Menu position="bottom-end" withinPortal shadow="md" width={210}>
  <Menu.Target>
    <ActionIcon variant="subtle" color="gray" aria-label="Group actions">
      <IconDotsVertical size={16} />
    </ActionIcon>
  </Menu.Target>
  <Menu.Dropdown>
    <Menu.Label>Change type</Menu.Label>
    {["Metapuzzle", "Capstone", "Runaround", "Group"]
      .filter((t) => t !== group.group_type)
      .map((t) => (
        <Menu.Item key={t} onClick={() => onChangeType(group, t)}>
          {t}
        </Menu.Item>
      ))}
    {isTargeted && (
      <>
        <Menu.Divider />
        <Menu.Item color="orange" onClick={() => onConvertToFeeder(group)}>
          Convert to feeder (keep puzzle)
        </Menu.Item>
      </>
    )}
    <Menu.Divider />
    <Menu.Item color="red" onClick={() => onDeleteGroup(group)}>
      Delete group…
    </Menu.Item>
  </Menu.Dropdown>
</Menu>
```

Confirmation modals (reuse `ConfirmModal` / `TypedConfirmModal` already imported in this file):
- **Change type → Group** on a targeted group: `ConfirmModal` warning "This removes the metapuzzle,
  its Google sheet, and turns its channel into the round's general channel."
- **Change type → targeted** on a bare Group: `ConfirmModal` warning "This creates a metapuzzle (new
  sheet + channel) and replaces the group's chat channel."
- Same-direction-among-targeted (e.g. Meta→Capstone): fire directly, no modal (matches today's card
  behaviour).
- **Convert to feeder**: `ConfirmModal` naming the orphaned-feeder count from
  `useConvertToFeederPreview(group.target_puzzle_id)`.
- **Delete group**: the `TypedConfirmModal` keep/delete-all flow copied from `BoardGroup`.

Handle `sheet_warning` in each mutation's `onSuccess` with `showSheetWarning` (already imported);
errors via `notifyError`.

- [ ] **Step 2: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/DragBoard.tsx
git commit -m "feat(web): group kebab in Arrange — change type, convert-to-feeder, delete group"
```

---

### Task 8: Slim the target-puzzle card to "Add to group…"

**Files:**
- Modify: `frontend/src/components/DragBoard.tsx` (`PuzzleCard`)

- [ ] **Step 1: Implement**

In `PuzzleCard`, when `puzzle.is_target`, render a menu containing only the **"Add to group…"** item
(open `AddToGroupPicker`). Remove, for the target branch only, the convert-to-* items,
convert-to-feeder, and delete-puzzle. Non-target feeder cards keep their current menu unchanged.

Practically: the target-vs-feeder split already exists in the card menu (the `is_target` branches at
~lines 179-234 vs 236+). Reduce the `is_target` branch to just the Add-to item and drop its
convert/delete items. Leave the `onConvert*`/`onDeletePuzzle` props in place if still used by the
feeder branch; remove any that become entirely unused (tsc will flag unused).

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors. Remove any now-unused imports/props tsc flags.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/DragBoard.tsx
git commit -m "feat(web): target-puzzle card in Arrange offers only Add-to-group"
```

---

## Phase 5 — Verification

### Task 9: Full backend suite + frontend build + manual smoke

- [ ] **Step 1: Backend suite**

Run: `timeout 300 pipenv run python -m pytest tests/ -q --parallel > /tmp/test.log 2>&1` (or serial if
the project's parallel flag differs — check `AGENTS.md`). Expected: all pass.

- [ ] **Step 2: Lint**

Run: `pipenv run ruff check`
Expected: clean (do not blind-`--fix` F401 on `bot/` — review per `AGENTS.md`).

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: success.

- [ ] **Step 4: Manual smoke (record results in session-summary)**

In the Arrange view against a test hunt:
- Targeted group kebab → Change type → Group: meta puzzle + its sheet gone; its channel becomes the
  round general channel with the group welcome pinned; feeders now `none`.
- Bare Group kebab → Change type → Metapuzzle: a meta puzzle appears with sheet + channel + meta pin;
  members flip to `feeds`.
- Group kebab → Convert to feeder: group gone, former meta is a plain feeder.
- Group kebab → Delete group (empty group): group removed.
- Target-puzzle card shows only "Add to group…".

- [ ] **Step 5: Final commit (if any lint/verification fixups)**

```bash
git add -A && git commit -m "chore: verification fixups for group-level convert & delete"
```

---

## Self-review notes (author)

- **Spec coverage:** teardown (Task 2), promote (Task 3), group-keyed seam (Task 3), web route
  (Task 4), payload fields (Task 5), kebab (Task 7), slimmed target card (Task 8), notifier pin
  refresh (Task 1). Delete-group reuses `delete_tag_group`; convert-to-feeder reuses the existing
  route — no new backend needed for those.
- **Bang path:** fixed for free by Task 2 (shared `convert_to_type`); no bang code changes.
- **Names are consistent:** `convert_tag_group_type`, `_teardown_group_target`,
  `_promote_group_to_target`, `refresh_group_info`, `change_group_type` route, `useChangeGroupType`,
  `group_type`/`target_puzzle_id` payload fields — used identically across tasks.
- **Test-file names are provisional** where a sibling may already exist (Tasks 4, 5): each step
  begins by grepping for the established pattern and matching it rather than inventing harness.
