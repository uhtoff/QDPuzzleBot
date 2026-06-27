# Group-type consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse stored `tag_group.type` to `{Metapuzzle, Capstone, Runaround, Group}` and route every "is this a group channel / which group type" decision through one canonical source, fixing code-review finding #1.

**Architecture:** Add `GROUP_TYPES` + `GROUP_TYPE_INPUT_ALIASES` in `puzzle_service.py`. `Round` becomes a create-time input label that is stored as `Metapuzzle` (keeping the `" - meta"` meta-name suffix); `Metaless Round` is removed entirely (including the `!roundnometa`/`!rnm` aliases). Repoint all membership lists in `puzzles.py` at `GROUP_TYPES`, collapse the `!info` dispatch, and migrate existing rows.

**Tech Stack:** Python, discord.py cogs, Quart web routes, pytest (`pipenv run python -m pytest --compact`), MySQL migrations applied via `migrate.py`.

**Spec:** `docs/superpowers/specs/2026-06-27-group-type-consolidation-design.md`

---

## Test command conventions (this repo)

- **Single test** (no `--parallel` — worker bootstrap is pure overhead for one test):
  `timeout 120 pipenv run python -m pytest tests/FILE.py::TEST --compact > /tmp/test-output.log 2>&1`
- **File / suite** (add `--parallel`):
  `timeout 240 pipenv run python -m pytest tests/FILE.py --compact --parallel > /tmp/test-output.log 2>&1`
- Read results with `tail -n 30 /tmp/test-output.log`. Exit `124` = timeout (treat as a hang, isolate per file).
- Each task commits with the repo trailer: a second `-m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`.

## File Structure

- **Modify** `bot/core/puzzle_service.py` — canonical constants; drop `TYPE_DEFAULTS["Round"]`; simplify `is_group`.
- **Modify** `bot/cogs/puzzles.py` — repoint membership lists; `command_map` + decorator + docstrings; collapse `!info`; delete dead round welcome builder; drop unused `TagGroupData` import.
- **Modify** `bot/web/actions.py` — `create_meta` accepts the `Round` input alias, stores `Metapuzzle`.
- **Create** `tests/test_group_type_consolidation.py` — new behaviour tests (kept in one file; the parallel runner shards by file).
- **Modify** `tests/test_edge_web.py` — flip the `Round` web-create assertion.
- **Modify** `tests/test_group_channel_type.py`, `tests/test_forum_layout.py` — remove `Metaless Round` tests + stale "FAIL until Phase 2" comments (also clears finding #5 in these files).
- **Create** `migrations/016_collapse_group_types.sql` — data migration.

---

### Task 1: Canonical constants in `puzzle_service.py`

**Files:**
- Modify: `bot/core/puzzle_service.py:92` (after `TYPE_DEFAULTS`), `:1596` (`is_group`)

- [ ] **Step 1: Add the constants below `TYPE_DEFAULTS`**

After the closing `}` of `TYPE_DEFAULTS` (line 92), insert:

```python

# Stored tag_group.type values. "Round" is intentionally NOT here: it is a
# create-time input label (see GROUP_TYPE_INPUT_ALIASES) stored as "Metapuzzle".
# Becomes tuple(TYPE_DEFAULTS) once the legacy "Round" key is dropped (see plan task 6).
GROUP_TYPES = ("Metapuzzle", "Capstone", "Runaround", "Group")

# Create-time input labels that are not stored types -> the stored type they map to.
GROUP_TYPE_INPUT_ALIASES = {"Round": "Metapuzzle"}
```

- [ ] **Step 2: Simplify `is_group` (line 1596)**

```python
        is_group = round_type == "Group"
```
(was `is_group = round_type in ("Group", "Metaless Round")`)

- [ ] **Step 3: Verify the service/forum/web suites still pass**

Run: `timeout 240 pipenv run python -m pytest tests/test_convert_service.py tests/test_forum_config.py tests/test_forum_tags.py tests/test_edge_web.py --compact --parallel > /tmp/test-output.log 2>&1`
Expected: PASS (no behaviour change yet; `Round` is still a valid `TYPE_DEFAULTS` key).

- [ ] **Step 4: Commit**

```bash
git add bot/core/puzzle_service.py
git commit -m "refactor: add canonical GROUP_TYPES + input aliases; simplify is_group" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Bang `!r`/`!round` → Metapuzzle; drop `!roundnometa`/`!rnm`

**Files:**
- Modify: `bot/cogs/puzzles.py:1476-1502` (`_round_impl` docstring + `command_map`), `:1545-1558` (decorator + command docstring)
- Test: `tests/test_group_type_consolidation.py`

- [ ] **Step 1: Write the failing tests** (create the file)

```python
"""Group-type consolidation (code-review finding #1).

Plain ``@pytest.mark.asyncio`` callback tests + ``def`` guard tests. Helpers are
local; the parallel runner shards by file.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_cog, make_ctx, make_round


@pytest.mark.asyncio
async def test_round_alias_creates_metapuzzle_with_suffix():
    cog = make_cog()
    cog.bot = MagicMock()
    cog.service.create_tag_group = AsyncMock(return_value=(make_round(), MagicMock()))
    await cog._round_impl(make_ctx(), "the-bog", "r")
    kwargs = cog.service.create_tag_group.call_args.kwargs
    assert kwargs["round_type"] == "Metapuzzle"
    assert kwargs["meta_puzzle_name"] == "the-bog - meta"


@pytest.mark.asyncio
async def test_group_alias_creates_group_no_meta():
    cog = make_cog()
    cog.bot = MagicMock()
    cog.service.create_tag_group = AsyncMock(return_value=(make_round(), None))
    await cog._round_impl(make_ctx(), "loose-ends", "group")
    kwargs = cog.service.create_tag_group.call_args.kwargs
    assert kwargs["round_type"] == "Group"
    assert kwargs["meta_puzzle_name"] is False


def test_roundnometa_aliases_removed():
    from bot.cogs.puzzles import Puzzles
    assert "roundnometa" not in Puzzles.round.aliases
    assert "rnm" not in Puzzles.round.aliases
```

- [ ] **Step 2: Run to verify they fail**

Run: `timeout 120 pipenv run python -m pytest tests/test_group_type_consolidation.py --compact > /tmp/test-output.log 2>&1`
Expected: FAIL — `round_type` is `"Round"` (not `"Metapuzzle"`); `"roundnometa"`/`"rnm"` still present.

- [ ] **Step 3: Update `command_map` (lines 1492-1502)**

Replace the dict body with (note `"r"`/`"round"` now map to `Metapuzzle`, and the `roundnometa`/`rnm` entries are gone):

```python
        command_map = {
            "r": ("Metapuzzle", True, " - meta"),
            "round": ("Metapuzzle", True, " - meta"),
            "mp": ("Metapuzzle", True, None),
            "metapuzzle": ("Metapuzzle", True, None),
            "capstone": ("Capstone", True, None),
            "cs": ("Capstone", True, None),
            "runaround": ("Runaround", True, None),
            "ra": ("Runaround", True, None),
            "group": ("Group", False, None),
        }
```

- [ ] **Step 4: Update the decorator alias list (lines 1545-1546)**

```python
    @commands.command(aliases=["r", "mp", "metapuzzle",
                               "capstone", "cs", "runaround", "ra", "group"])
```

- [ ] **Step 5: Update the two docstrings**

In `_round_impl` (lines 1480-1486) remove the `!roundnometa / !rnm → Metaless Round` line, and change the `!r / !round` line to:
```
          !r / !round       → Metapuzzle (creates meta puzzle named "name - meta")
```
In the `round` command docstring (lines 1553-1558) remove the bullet:
```
        • `!rnm` / `!roundnometa name` — a round with no meta puzzle
```

- [ ] **Step 6: Run to verify pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_group_type_consolidation.py --compact > /tmp/test-output.log 2>&1`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_group_type_consolidation.py
git commit -m "feat: Round is a create-time alias of Metapuzzle; drop roundnometa/rnm" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Repoint membership lists at `GROUP_TYPES`; drop `PUZZLE_GROUPS` + unused import

**Files:**
- Modify: `bot/cogs/puzzles.py:14-15` (import), `:29` (puzzle_service import), `:83` (delete `PUZZLE_GROUPS`), `:1254-1255` (`sync`), `:1739`/`:1750` (`get/save_settings`), `:1901` (`list_puzzles`), `:2963` (`delete_round`)
- Test: `tests/test_group_type_consolidation.py`

- [ ] **Step 1: Add the failing guard tests** (append to the new file)

```python
@pytest.mark.asyncio
async def test_delete_round_allows_capstone_channel():
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Capstone"
    cog.get_tag_group = lambda ctx: make_round(id=7)
    cog._confirm_destructive = AsyncMock(return_value=False)  # decline -> stop after guard
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_tag_group.return_value = []
        await Puzzles.delete_round.callback(cog, ctx)
    cog._confirm_destructive.assert_awaited_once()                 # guard passed
    assert all("main group channel" not in str(c) for c in ctx.send.call_args_list)


def test_get_settings_routes_capstone_to_tag_group():
    cog = make_cog()
    sentinel = object()
    cog.get_channel_type = lambda ctx: "Capstone"
    cog.get_tag_group = lambda ctx: sentinel
    assert cog.get_settings(make_ctx()) is sentinel
```

- [ ] **Step 2: Run to verify they fail**

Run: `timeout 120 pipenv run python -m pytest "tests/test_group_type_consolidation.py::test_delete_round_allows_capstone_channel" "tests/test_group_type_consolidation.py::test_get_settings_routes_capstone_to_tag_group" --compact > /tmp/test-output.log 2>&1`
Expected: FAIL — `delete_round` sends the rejection (guard rejects `Capstone`); `get_settings` falls through to `get_guild_data` (AttributeError on the stub).

- [ ] **Step 3: Extend the puzzle_service import (line 29)**

```python
from bot.core.puzzle_service import TYPE_DEFAULTS, GROUP_TYPES
```

- [ ] **Step 4: Drop unused `TagGroupData` from the store import (lines 14-15)** — clears finding #6

```python
from bot.store import PuzzleData, PuzzleJsonDb, GuildSettingsDb, \
    TagGroupJsonDb, HuntData, HuntJsonDb, TagGroupCategoryDb, resolve_group
```

- [ ] **Step 5: Delete the `PUZZLE_GROUPS` class attribute (line 83)** and repoint its readers:

- `get_settings` (1739) and `save_settings` (1750): `self.get_channel_type(ctx) in self.PUZZLE_GROUPS` → `self.get_channel_type(ctx) in GROUP_TYPES`
- `delete_round` (2963): `self.get_channel_type(ctx) not in self.PUZZLE_GROUPS` → `self.get_channel_type(ctx) not in GROUP_TYPES`
- `sync` (1254-1255): replace the inline list with:
```python
        if self.get_channel_type(ctx) not in GROUP_TYPES:
```
- `list_puzzles` (1901): replace the inline list (it also accepts puzzle channels) with:
```python
        if self.get_channel_type(ctx) in (*GROUP_TYPES, "Puzzle"):
```

- [ ] **Step 6: Run the new tests + lint**

Run: `timeout 120 pipenv run python -m pytest tests/test_group_type_consolidation.py --compact > /tmp/test-output.log 2>&1`
Expected: PASS (5 tests).
Run: `pipenv run ruff check bot/cogs/puzzles.py`
Expected: no errors (the `F401 TagGroupData` is gone).

- [ ] **Step 7: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_group_type_consolidation.py
git commit -m "fix: route group-channel membership checks through GROUP_TYPES (finding #1)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Collapse `!info` dispatch; remove dead round welcome builder

**Files:**
- Modify: `bot/cogs/puzzles.py:2257-2266` (`info`), delete `:2148-2187` (`send_initial_round_channel_messages`)
- Test: `tests/test_group_type_consolidation.py`, `tests/test_group_channel_type.py`, `tests/test_forum_layout.py`

- [ ] **Step 1: Add the failing `!info` test** (append to the new file)

```python
@pytest.mark.asyncio
async def test_info_capstone_calls_metapuzzle_handler():
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Capstone"
    cog.send_initial_metapuzzle_channel_messages = AsyncMock()
    await Puzzles.info.callback(cog, make_ctx())
    cog.send_initial_metapuzzle_channel_messages.assert_awaited_once()
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest "tests/test_group_type_consolidation.py::test_info_capstone_calls_metapuzzle_handler" --compact > /tmp/test-output.log 2>&1`
Expected: FAIL — `Capstone` hits the else-branch; the metapuzzle handler is never awaited.

- [ ] **Step 3: Rewrite the `!info` dispatch (lines 2257-2266)**

```python
        ch_type = self.get_channel_type(ctx)
        if ch_type == "Hunt":
            await self.send_initial_hunt_channel_messages(ctx, ctx.channel, update=update)
        elif ch_type in ("Metapuzzle", "Capstone", "Runaround"):
            await self.send_initial_metapuzzle_channel_messages(ctx, ctx.channel, update=update)
        elif ch_type == "Group":
            await self.send_initial_group_channel_messages(ctx, ctx.channel, update=update)
        elif ch_type == "Puzzle":
            await self.send_initial_puzzle_channel_messages(ctx, ctx.channel, update=update)
        else:
            await ctx.send(":x: This does not appear to be a hunt channel")
```

- [ ] **Step 4: Delete `send_initial_round_channel_messages` (lines 2148-2187)** — now unreferenced.

- [ ] **Step 5: Clean the stale/Metaless tests** (overlaps finding #5)

In `tests/test_group_channel_type.py`:
- Remove the module-docstring lines 7-9 (`These FAIL until Phase 2: ...`).
- Delete `test_legacy_metaless_round_type_routes_to_group` (lines 87-111).
- In the comment block above the `!info` tests (lines 114-118), replace with:
```python
# ---------------------------------------------------------------------------
# !info dispatch — a Group channel routes to the group welcome handler.
# ---------------------------------------------------------------------------
```
- In `test_info_group_calls_group_handler`, remove the line
  `cog.send_initial_metaless_round_channel_messages = AsyncMock()`.
- Delete `test_info_metaless_round_calls_group_handler` (lines 136-148).

In `tests/test_forum_layout.py` (lines 164-166), replace the stale comment with:
```python
# ---------------------------------------------------------------------------
# Group general-channel group type + service-owned welcomes.
# ---------------------------------------------------------------------------
```

- [ ] **Step 6: Run the touched test files**

Run: `timeout 240 pipenv run python -m pytest tests/test_group_type_consolidation.py tests/test_group_channel_type.py tests/test_forum_layout.py --compact --parallel > /tmp/test-output.log 2>&1`
Expected: PASS (no remaining `Metaless Round` references; `!info` routes Capstone/Runaround).

- [ ] **Step 7: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_group_channel_type.py tests/test_forum_layout.py tests/test_group_type_consolidation.py
git commit -m "fix: !info routes Capstone/Runaround/Group; drop dead round welcome + Metaless tests" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Web `create_meta` accepts the `Round` input alias

**Files:**
- Modify: `bot/web/actions.py:1503-1534`
- Test: `tests/test_edge_web.py:123-130`

- [ ] **Step 1: Flip the existing web-create assertion (lines 123-130)**

```python
@pytest.mark.asyncio
async def test_create_meta_round_alias_stores_metapuzzle_with_suffix(web):
    # "Round" is a create-time alias: stored as Metapuzzle, meta named "<name> - meta".
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Round"})
    assert resp.status_code == 200
    kwargs = web.svc.create_tag_group.call_args.kwargs
    assert kwargs.get("round_type") == "Metapuzzle"
    assert kwargs.get("meta_puzzle_name") == "Foo - meta"
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest "tests/test_edge_web.py::test_create_meta_round_alias_stores_metapuzzle_with_suffix" --compact > /tmp/test-output.log 2>&1`
Expected: FAIL — route currently passes `round_type="Round"`.

- [ ] **Step 3: Map the alias in `create_meta` (lines 1503-1534)**

```python
    group_type = (body.get("type") or "Metapuzzle").strip()
    from bot.core.puzzle_service import TYPE_DEFAULTS, GROUP_TYPE_INPUT_ALIASES
    if group_type not in TYPE_DEFAULTS and group_type not in GROUP_TYPE_INPUT_ALIASES:
        return jsonify({"error": "bad_type"}), 400
    stored_type = GROUP_TYPE_INPUT_ALIASES.get(group_type, group_type)
```
Then in the `try` block, keep the suffix logic keyed on the incoming label but pass `stored_type`:
```python
        if group_type == "Group":
            meta_puzzle_name = False
        elif group_type == "Round":
            meta_puzzle_name = name + " - meta"
        else:
            meta_puzzle_name = name
        new_round, puzzle = await cog.service.create_tag_group(
            guild, hunt_obj, name,
            notifier=DiscordNotifier(bot),
            meta_puzzle_name=meta_puzzle_name,
            round_type=stored_type,
        )
        if stored_type == "Runaround":
            await cog.service.set_source_mode(hunt_obj, new_round, "subtree")
```

- [ ] **Step 4: Run the web suite**

Run: `timeout 240 pipenv run python -m pytest tests/test_edge_web.py --compact --parallel > /tmp/test-output.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/web/actions.py tests/test_edge_web.py
git commit -m "fix: web create_meta maps Round input to stored Metapuzzle (keeps - meta suffix)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Drop the legacy `Round` key from `TYPE_DEFAULTS`

**Files:**
- Modify: `bot/core/puzzle_service.py:87` (remove `Round` row), `:GROUP_TYPES` (make derived)

- [ ] **Step 1: Remove the `"Round"` entry from `TYPE_DEFAULTS` (line 87)**

Delete the line:
```python
    "Round":      {"default_edge": "feeds",   "default_source_mode": "round"},
```

- [ ] **Step 2: Make `GROUP_TYPES` derived (DRY)**

```python
# Stored tag_group.type values == the TYPE_DEFAULTS keys.
GROUP_TYPES = tuple(TYPE_DEFAULTS)
```

- [ ] **Step 3: Verify the full suite**

Run: `timeout 300 pipenv run python -m pytest --compact --parallel > /tmp/test-output.log 2>&1`
Expected: PASS. (No code stores or looks up `Round` now — bang stores `Metapuzzle`, web maps via the alias.)

- [ ] **Step 4: Commit**

```bash
git add bot/core/puzzle_service.py
git commit -m "refactor: drop legacy Round key; GROUP_TYPES derives from TYPE_DEFAULTS" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Data migration

**Files:**
- Create: `migrations/016_collapse_group_types.sql`

- [ ] **Step 1: Verify the next migration number (collision check)**

Run: `ls migrations/ | sort | tail -3`
Expected: highest is `015_hunt_layout_mode.sql`. If a `016_*` already exists (parallel worktree), use the next free number and update this filename accordingly.

- [ ] **Step 2: Verify the live storage backend before relying on SQL**

Run: `grep -rn "class TagGroupJsonDb\|LADDER_SPOT_DATA_DIR\|tag_group" bot/store/*.py | head`
Confirm `tag_group` lives in MySQL (`bot/store/mysqldb.py`). If a JSON-file store holds live rows, add an equivalent data fix for it (out of scope if MySQL is the only live store — note the finding either way).

- [ ] **Step 3: Write the migration**

```sql
-- Collapse group types (code-review finding #1):
--   Round         -> Metapuzzle  (Round is now a create-time alias only)
--   Metaless Round -> Group      (retired entirely)
-- Spec: docs/superpowers/specs/2026-06-27-group-type-consolidation-design.md
UPDATE tag_group SET type = 'Metapuzzle' WHERE type = 'Round';
UPDATE tag_group SET type = 'Group'      WHERE type = 'Metaless Round';
```

- [ ] **Step 4: Apply + verify against the test DB**

Run the project's migration runner (`python migrate.py` / per `README`), then:
Run: `# expect zero rows` — query `SELECT DISTINCT type FROM tag_group;` and confirm the result is a subset of `{Metapuzzle, Capstone, Runaround, Group}` with no `Round`/`Metaless Round`.

- [ ] **Step 5: Commit**

```bash
git add migrations/016_collapse_group_types.sql
git commit -m "migration: collapse Round->Metapuzzle, Metaless Round->Group" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] `pipenv run ruff check` → no errors.
- [ ] `timeout 300 pipenv run python -m pytest --compact --parallel > /tmp/test-output.log 2>&1` → full suite green.
- [ ] `grep -rn '"Metaless Round"\|"Round"' bot/` → only `forum_config.py`'s defensive `("Round","Metapuzzle")` remains (acceptable; see Risks).

## Self-review notes

- **Spec coverage:** canonical constants (T1/T6), Round→Metapuzzle alias + suffix (T2 bang, T5 web), Metaless removal incl. aliases (T2) + string removal (T3/T4), stale-list fixes for delete_round/settings/list_puzzles/sync (T3) and `!info` incl. Capstone/Runaround bug (T4), dead round welcome (T4), migration + backend check (T7), test updates incl. finding-#5 comment cleanup (T4). All spec sections mapped.
- **Type consistency:** `GROUP_TYPES` and `GROUP_TYPE_INPUT_ALIASES` names are used identically across `puzzle_service.py`, `puzzles.py:29`, and `actions.py`. `create_tag_group(..., round_type=, meta_puzzle_name=)` kwargs match existing signature.
- **Green-at-each-commit ordering:** `TYPE_DEFAULTS["Round"]` removal (T6) is sequenced *after* the web route accepts the alias (T5) and the bang stores Metapuzzle (T2), so no task leaves a broken `Round` path.
- **Out of scope / Risk:** `forum_config.py`'s `t in ("Round","Metapuzzle")` is left as a harmless defensive branch (treats any stray legacy `Round` row as a meta); simplifying it is optional and not required for finding #1.
