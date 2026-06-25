# Current Spec — Milestone C: Command surface

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Target model — Phase 1 (tests): Opus · Phases 2–4 (implementation): Sonnet
Started: 2026-06-25
Status: ready for handoff — Phase 1 (tests)

**Authoritative design:** `docs/superpowers/specs/2026-06-25-milestone-c-commands-design.md`
**Read first; this plan is the ordered execution.**

---

## Before you start (executing model)

- Execute **only the current phase**. Do not jump ahead.
- On any **unexpected** failure (a test fails for a reason other than a missing
  class/method/feature), invoke **`superpowers:systematic-debugging`** before changing anything.
- Before claiming a phase done, invoke **`superpowers:verification-before-completion`** and
  **paste the acceptance command output** into your progress note.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix,
  write `.ai/active/blocker.md` (what failed, the command, the output, your hypothesis) and STOP.
- Commit at each phase boundary with a clear message.

---

## Goal

Implement Milestone C: replace the old tag-based service methods with edge-aware primitives,
add the new bang commands (`!capstone`, `!runaround`, `!group`, `!feeds`, `!feeds_only`,
`!informs`, `!informs_only`, `!groups_with`, `!groups_only_with`, `!remove_from`, convert
family, edge-aware `!sync`), remove deprecated commands (`!pool`, `!nest`, `!source_mode`),
and extend the web API endpoints for type-aware create, edge-aware retag, and new convert routes.

No new DB migrations. Schema from A+B is the substrate.

## In scope

- `bot/core/puzzle_service.py` — `TYPE_DEFAULTS` constant; new service primitives
  `tag_with_edge`, `tag_edge_only`, `untag_from`, `bulk_re_edge`, `convert_to_type`;
  rename `convert_to_feeder` → `convert_to_puzzle`; remove `add_to_meta`,
  `move_to_meta`, `remove_from_meta`, `clear_metas`
- `bot/store/mysqldb.py` — add `bulk_update_edge_type(tag_group_id, new_edge)` DB method
  needed by `bulk_re_edge`
- `bot/cogs/puzzles.py` — extend `command_map`/`_round_impl` for new create commands;
  new membership command handlers; edge-aware `!sync`; updated convert handlers;
  remove `!pool`, `!nest`, `!source_mode` bang commands
- `bot/web/actions.py` — extend `/hunts/<uid>/metas` (type param); extend
  `/puzzles/<id>/meta` (edge + only params); add `/convert-to-capstone`,
  `/convert-to-group`, `/convert-to-runaround`, `/convert-to-puzzle` endpoints
- `tests/test_edge_primitives.py` — new test file (Phase 1)
- `tests/test_edge_web.py` — new test file for web endpoint changes (Phase 1)
- `tests/test_convert_service.py` — new test file for convert_to_type (Phase 1)

## Out of scope

- Frontend board changes — board stays Metapuzzle-only
- `!sync` web parity — bang-only
- Sheet generation (Milestone D)
- Migration (A+B are done; C has none)
- `set_parent_group` and `set_source_mode` service methods — keep them, don't rename

## Relevant files

Read only:
- `docs/superpowers/specs/2026-06-25-milestone-c-commands-design.md` — authoritative spec
- `docs/superpowers/specs/2026-06-24-taggroup-edges-and-commands-design.md` — model
- `tests/conftest.py` — `make_puzzle`, `make_round`, `make_hunt` factories
- `tests/test_solve_equivalence.py` — existing mock-based service test pattern to follow
- `tests/test_tag_edges.py` — existing real-DB edge test pattern
- `bot/store/mysqldb.py` — `tag_puzzle_with_edge`, `get_all_from_tag_group` (to understand DB layer)

Write:
- `bot/core/puzzle_service.py`
- `bot/store/mysqldb.py`
- `bot/cogs/puzzles.py`
- `bot/web/actions.py`
- `tests/test_edge_primitives.py` (new)
- `tests/test_edge_web.py` (new)
- `tests/test_convert_service.py` (new)

## Constraints

- **Touch only the files above.** No formatting-only diffs. No refactors of unrelated code.
- **Old service methods removed outright** — `add_to_meta`, `move_to_meta`,
  `remove_from_meta`, `clear_metas` are deleted. All callers (cog, web) updated in the
  same phase. After Phase 2, grep confirms none remain:
  `grep -rn "\.add_to_meta\|\.move_to_meta\|\.remove_from_meta\|\.clear_metas" bot/`
- **`!pool` dropped entirely** — no legacy alias. Remove the handler from cogs/puzzles.py.
- **`!nest` / `!source_mode` bang commands removed** — the service methods
  `set_parent_group` and `set_source_mode` stay (called internally by `!runaround` and
  `convert_to_type`).
- **Legacy aliases exist only at the bang command level** — `add_to_meta` as a
  `@commands.hybrid_command` alias calls `!feeds` implementation; the service has no
  `add_to_meta` wrapper.
- **`tag_with_edge` and `tag_edge_only` both update `puzzle.tags`** (the id list) AND
  `puzzle.tag_edges` (the edge dict) — the rest of the codebase reads both. Miss one
  and the board goes stale.
- **`bulk_re_edge` summary string format:** `"flipped N feeds + M none → informs"` —
  only list edge types that had non-zero count. Empty group → `"0 members"`.
- **`convert_to_type` promote-in-place:** when the puzzle has no group yet, creates a
  TagGroupData row (no Discord category/channel creation — unlike `create_tag_group`).
  Geography-neutral. Idempotent if the group already exists and type is already correct.
- **`!feeds_only` moves the channel; all other `_only` variants do not.**
- **Test runner:** `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
  (never plain `pytest` — PATH and sys.path issues). A killed run exits 124.

---

## Test constraints (inline — executing agent does not read global instructions)

- **Reuse exact helpers from `tests/conftest.py`:** `make_puzzle`, `make_round`, `make_hunt`.
  Import them. Do not redefine.
- **NEVER write a loop that resolves state "until" a condition.** If you think you need
  one, STOP and write `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says to.
- **Confirm every attribute exists before use** — check conftest factories and the
  dataclass definitions. `make_puzzle` does NOT set `tag_edges`; set it manually in each
  test: `puz.tag_edges = {gid: "feeds"}`.
- **A test file must define or import every helper it calls** — never rely on a function
  defined in a sibling test file. The parallel runner shards by file; a cross-file
  dependency is an "undefined function" fatal in that shard.
- **Wrap every test run in `timeout`, write to a file, single statement.**
- **Phase 1 acceptance:** the filter run exits non-zero AND the error is
  `AttributeError` / `ImportError` (method doesn't exist yet), NOT a missing fixture,
  undefined variable, or column name that doesn't exist. If the test itself errors on
  import or setup for a reason other than the missing feature, **fix the test**.

---

## Phase 1 — Write all failing tests (Opus)

Write three new test files. Commit them. Confirm each fails for the right reason
(method/attribute not found) before Phase 2 begins.

### File 1: `tests/test_edge_primitives.py`

Pattern: mock `PuzzleJsonDb`, `RoundJsonDb`, `HuntJsonDb` exactly as in
`TestWebAddToMeta` / `TestWebMoveToMeta` in `tests/test_solve_equivalence.py`. Use
`_make_cog()` from `test_solve_equivalence` as a model — reproduce a minimal version
**locally** (do not import from a sibling test file):

```python
def _make_cog_c():
    from bot.cogs.puzzles import Puzzles
    from unittest.mock import MagicMock
    bot = MagicMock()
    bot.get_cog.return_value = None
    return Puzzles(bot)
```

**`TestTagWithEdge`** — `service.tag_with_edge(puzzle, group, edge)`:
- `test_adds_group_to_tags` — puzzle starts with `tags=[]`, after call `group.id in puz.tags`
- `test_sets_tag_edges_dict` — `puz.tag_edges[group.id] == "feeds"` after call
- `test_idempotent_edge_update` — call twice with different edge; second call changes `tag_edges` in place; `tags` has only one entry for that group
- `test_informs_edge` — same as above with `edge="informs"`

**`TestTagEdgeOnly`** — `service.tag_edge_only(puzzle, group, edge, guild=None)`:
- `test_sets_target_edge` — target group gets edge set
- `test_removes_other_same_type_edges` — puzzle has `tag_edges={3: "feeds", 5: "feeds"}`,
  call with `group.id=5, edge="feeds"`; after call `3 not in puz.tags` (other feeds edge gone),
  `5 in puz.tags`
- `test_leaves_different_type_edges` — puzzle has `tag_edges={3: "feeds", 4: "informs"}`,
  call with `group.id=5, edge="feeds"`; after call `4 in puz.tags` (informs untouched)
- `test_no_channel_move_without_guild` — guild=None; confirm no channel.edit call attempted

**`TestUntagFrom`** — `service.untag_from(puzzle, group, edge=None)`:
- `test_unconditional_remove` — puzzle `tags=[5]`, call with no edge param; `5 not in puz.tags`
- `test_type_guarded_removes_matching` — `tag_edges={5: "feeds"}`, call with `edge="feeds"`; `5 not in puz.tags`
- `test_type_guarded_noop_on_mismatch` — `tag_edges={5: "informs"}`, call with `edge="feeds"`; `5 still in puz.tags`
- `test_leaves_other_tags` — `tags=[3, 5]`, remove 5; `3 in puz.tags`

**`TestBulkReEdge`** — `service.bulk_re_edge(tag_group, new_edge, hunt) -> str`:
Setup: mock `PuzzleJsonDb.get_all_from_tag_group` to return a list of puzzles with varying
`tag_edges`. Mock `PuzzleJsonDb.bulk_update_edge_type` (new DB method) to be a no-op.

- `test_returns_summary_string_mixed` — 3 puzzles with feeds, 2 with none; call `bulk_re_edge(group, "informs", hunt)`; returns `"flipped 3 feeds + 2 none → informs"`
- `test_returns_summary_single_type` — 7 puzzles all feeds; returns `"flipped 7 feeds → informs"`
- `test_empty_group` — 0 puzzles; returns `"0 members"`
- `test_calls_bulk_update` — confirm `PuzzleJsonDb.bulk_update_edge_type(group.id, "informs")` was called

### File 2: `tests/test_convert_service.py`

**`TestConvertToType`** — `service.convert_to_type(guild, hunt, puzzle, new_type)`:

Setup helper (local to this file):
```python
def _make_cog_convert():
    from bot.cogs.puzzles import Puzzles
    from unittest.mock import MagicMock
    bot = MagicMock()
    bot.get_cog.return_value = None
    return Puzzles(bot)
```

For each test, mock `RoundJsonDb`, `PuzzleJsonDb`, `HuntJsonDb` and
`cog.service.set_source_mode` (AsyncMock). `RoundJsonDb.get_by_attr.return_value = None`
for the "bare puzzle" path (no existing group); set to a `make_round()` for tests that
already have a group.

- `test_promote_in_place_creates_group` — bare puzzle (no group), `new_type="Metapuzzle"`;
  confirm `RoundJsonDb.commit` called; returned `tag_group` has `type == "Metapuzzle"`
- `test_sets_source_mode_round_for_metapuzzle` — confirm `set_source_mode` called with
  `mode="round"` for `new_type="Metapuzzle"`
- `test_capstone_sets_source_mode_round` — `new_type="Capstone"`, source_mode=round
- `test_runaround_sets_source_mode_subtree` — `new_type="Runaround"`, source_mode=subtree
- `test_capstone_calls_bulk_re_edge_informs` — mock `cog.service.bulk_re_edge` (AsyncMock);
  `new_type="Capstone"`; confirm called with `new_edge="informs"`
- `test_metapuzzle_calls_bulk_re_edge_feeds` — `new_type="Metapuzzle"`; bulk_re_edge called
  with `new_edge="feeds"`
- `test_runaround_does_not_call_bulk_re_edge` — `new_type="Runaround"`; bulk_re_edge NOT called
- `test_returns_summary_string` — mock `bulk_re_edge` to return `"flipped 3 feeds → informs"`;
  `convert_to_type` returns `(tag_group, "flipped 3 feeds → informs")`
- `test_group_type_bulk_re_edge_none` — `new_type="Group"`; bulk_re_edge called with `new_edge="none"`

**`TestConvertToPuzzle`** — `service.convert_to_puzzle(guild, hunt, puzzle)`:
(this is the renamed `convert_to_feeder` — the method must exist under the new name)

- `test_renamed_exists` — `hasattr(cog.service, 'convert_to_puzzle')` is True
- `test_old_name_removed` — `hasattr(cog.service, 'convert_to_feeder')` is False

### File 3: `tests/test_edge_web.py`

Use the Quart test client pattern from existing web tests. Read
`tests/test_create_equivalence.py` or `tests/test_solve_equivalence.py` for the exact
app/client fixture. **Reproduce the fixture locally** — do not import from sibling files.

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture
def app_and_client():
    # Mirror the pattern from test_create_equivalence.py or test_solve_equivalence.py
    # to get a Quart test client with a mocked bot/cog.
    ...
```

- `test_create_meta_default_type` — POST `/api/hunts/<uid>/metas` with `{"name": "Foo"}`;
  confirm `create_tag_group` called with `round_type="Metapuzzle"` (default)
- `test_create_meta_capstone_type` — body `{"name": "Foo", "type": "Capstone"}`;
  confirm `create_tag_group` called with `round_type="Capstone"`
- `test_retag_default_edge_feeds` — POST `/api/puzzles/<id>/meta` with
  `{"group_code": "X", "mode": "add"}`; confirm `tag_with_edge` called with `edge="feeds"`
- `test_retag_informs_edge` — body `{"group_code": "X", "mode": "add", "edge": "informs"}`;
  confirm `tag_with_edge` called with `edge="informs"`
- `test_retag_add_only_true` — body `{"mode": "add", "group_code": "X", "only": true}`;
  confirm `tag_edge_only` called
- `test_retag_mode_move_compat` — body `{"mode": "move", "group_code": "X"}`;
  confirm `tag_edge_only` called with `edge="feeds"` (backward compat)
- `test_convert_to_capstone_endpoint_exists` — POST `/api/puzzles/<id>/convert-to-capstone`
  returns 200 or 4xx (not 404/405); route is registered
- `test_convert_to_capstone_returns_summary` — mock `convert_to_type` to return
  `(make_round(), "flipped 3 feeds → informs")`; response JSON has `"summary"` key

**Acceptance (Phase 1):**
```
timeout 60 pipenv run python -m pytest tests/test_edge_primitives.py tests/test_convert_service.py tests/test_edge_web.py -q > /tmp/test-p1.log 2>&1
```
Expected: non-zero exit (methods don't exist yet). Each failure must be
`AttributeError`/`ImportError` for the missing method/route — NOT a fixture crash,
missing conftest helper, or undefined variable. Read `/tmp/test-p1.log` to confirm.

Commit: `test(taggroup): Phase 1 failing tests for Milestone C edge primitives`

---

## Phase 2 — Service layer (Sonnet)

**`bot/store/mysqldb.py`** — add to `MySQLPuzzleJsonDb`:

```python
def bulk_update_edge_type(self, tag_group_id, new_edge):
    with self.conn() as cursor:
        cursor.execute(
            "UPDATE tags SET edge_type = %s WHERE tag_group_id = %s",
            (new_edge, tag_group_id),
        )
```

**`bot/core/puzzle_service.py`** — in order:

1. Add module-level constant after imports:
```python
TYPE_DEFAULTS = {
    "Round":      {"default_edge": "feeds",   "default_source_mode": "round"},
    "Metapuzzle": {"default_edge": "feeds",   "default_source_mode": "round"},
    "Capstone":   {"default_edge": "informs", "default_source_mode": "round"},
    "Runaround":  {"default_edge": "feeds",   "default_source_mode": "subtree"},
    "Group":      {"default_edge": "none",    "default_source_mode": "round"},
}
```

2. Add `tag_with_edge(self, puzzle, group, edge)`:
   - Call `PuzzleJsonDb.tag_puzzle_with_edge(puzzle.id, group.id, edge)` (already in mysqldb)
   - If `group.id not in puzzle.tags`: `puzzle.tags.append(group.id)`
   - `puzzle.tag_edges[group.id] = edge`
   - `PuzzleJsonDb.commit(puzzle)`
   - `hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)`
   - `await self.refresh_targets(hunt, [group])`
   - `await self.notifier.board_changed(hunt)`
   - Raise `SheetRefreshError` if refresh failed

3. Add `tag_edge_only(self, puzzle, group, edge, guild=None)`:
   - Collect `other_groups`: all group ids in `puzzle.tag_edges` where
     `puzzle.tag_edges[gid] == edge` and `gid != group.id`
   - For each: `RoundJsonDb.get_by_attr(id=gid)` → call `self.untag_from(puzzle, other_group, edge)`
     (the type-guarded form — safe if edge drifted)
   - If `edge == "feeds"` and `guild` is not None and `puzzle.channel_id`:
     move channel to `group.category_id` (same logic as old `move_to_meta`)
   - Call `self.tag_with_edge(puzzle, group, edge)` for the target group
   - All affected groups passed to `refresh_targets` collectively

4. Add `untag_from(self, puzzle, group, edge=None)`:
   - If `edge` is not None and `puzzle.tag_edges.get(group.id) != edge`: return (no-op)
   - Guard: if `puzzle.is_metapuzzle()` and `len(puzzle.tags) <= 1`: raise appropriate error
     (mirror old `remove_from_meta` guard)
   - Delete the tags row: execute `DELETE FROM tags WHERE tag_group_id=%s AND puzzle_id=%s`
     via a new `PuzzleJsonDb.delete_tag(puzzle_id, tag_group_id)` DB method (add to mysqldb)
   - `puzzle.tags.remove(group.id)` if present; `puzzle.tag_edges.pop(group.id, None)`
   - `PuzzleJsonDb.commit(puzzle)`
   - Refresh, board-push

5. Add `bulk_re_edge(self, tag_group, new_edge, hunt) -> str`:
   - `members = PuzzleJsonDb.get_all_from_tag_group(tag_group.id)`
   - Count existing edges: `counter = {}` → `counter[p.tag_edges.get(tag_group.id, "feeds")] += 1`
     for each member
   - If no members: return `"0 members"`
   - `PuzzleJsonDb.bulk_update_edge_type(tag_group.id, new_edge)`
   - Build summary: `"flipped " + " + ".join(f"{v} {k}" for k, v in counter.items() if v) + f" → {new_edge}"`
   - `await self.refresh_target(hunt, tag_group)`
   - `await self.notifier.board_changed(hunt)`
   - Return summary string

6. Add `convert_to_type(self, guild, hunt, puzzle, new_type) -> tuple`:
   - Look up `tag_group = RoundJsonDb.get_by_attr(target_puzzle_id=puzzle.id)`
   - If None (bare puzzle): create `TagGroupData(name=puzzle.name, hunt_id=hunt.id,
     category_id=puzzle.channel_id, type=new_type)`; `RoundJsonDb.commit(tag_group)`;
     set `tag_group.target_puzzle_id = puzzle.id`; generate group_code; commit again;
     `puzzle.metapuzzle = 1`; `puzzle.add_tag(tag_group.id)`; `PuzzleJsonDb.commit(puzzle)`
   - Else: `tag_group.type = new_type`; `RoundJsonDb.commit(tag_group)`
   - `default_sm = TYPE_DEFAULTS[new_type]["default_source_mode"]`
   - `await self.set_source_mode(hunt, tag_group, default_sm)` (already exists)
   - If `new_type != "Runaround"`:
     `default_edge = TYPE_DEFAULTS[new_type]["default_edge"]`
     `summary = await self.bulk_re_edge(tag_group, default_edge, hunt)`
   - Else: `summary = "no edge change (runaround)"`
   - `await self.notifier.board_changed(hunt)`
   - Return `(tag_group, summary)`

7. Rename `convert_to_feeder` → `convert_to_puzzle` (simple rename; body unchanged).

8. **Delete** `add_to_meta`, `move_to_meta`, `remove_from_meta`, `clear_metas`.
   After deleting, run:
   ```
   grep -rn "\.add_to_meta\|\.move_to_meta\|\.remove_from_meta\|\.clear_metas" bot/
   ```
   Any hits are callers that must be updated in this phase. Fix them before committing.

**Acceptance (Phase 2):**
```
timeout 180 pipenv run python -m pytest tests/test_edge_primitives.py tests/test_convert_service.py -q > /tmp/test-p2.log 2>&1
```
Must pass. Then:
```
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-p2-full.log 2>&1
```
Must still pass (no regressions from deleted methods). `pipenv run ruff check` → exit 0.

Commit: `feat(taggroup): service edge primitives + convert_to_type (Milestone C Phase 2)`

---

## Phase 3 — Bang commands (Sonnet)

Edit `bot/cogs/puzzles.py`:

1. **Extend `command_map` in `_round_impl`** with new entries:
   ```python
   "capstone": ("Capstone", True, None),
   "cs":       ("Capstone", True, None),
   "runaround": ("Runaround", True, None),
   "ra":        ("Runaround", True, None),
   "group": ("Group", False, None),
   # "rnm" already present → change to map to Group/False
   "roundnometa": ("Group", False, None),  # keep for compat
   ```
   For "Runaround" post-create: after `create_tag_group` returns, call
   `await self.service.set_source_mode(hunt, new_round, "subtree")`.

2. **Add `@commands.command(aliases=["r","mp","metapuzzle","roundnometa","rnm","capstone","cs","runaround","ra","group"])` to the `round` command** (or split into separate hybrid commands if the pattern requires it — look at how the existing command is registered and follow it).

3. **Replace `add_to_meta` / `move_to_meta` / `remove_from_meta` handlers** with new
   `!feeds`, `!feeds_only`, `!informs`, `!informs_only`, `!groups_with`, `!groups_only_with`,
   `!remove_from` handlers. Each follows the same pattern as the old handler:
   - Check `get_channel_type(ctx) != "Hunt"` guard
   - `puzzle = self.get_puzzle(ctx)`
   - `target_group = RoundJsonDb.get_by_attr(group_code=meta_code)`
   - Call the appropriate service primitive
   - `asyncio.create_task(self.service.refresh_unassigned_safe(hunt))`
   - Send success message
   
   Legacy aliases: the `add_to_meta` command name (and `add_to_round`) must exist as
   `aliases` on the `!feeds` handler so muscle-memory users aren't broken.
   Similarly `move_to_meta`/`move_to_round` on `!feeds_only`, and
   `remove_from_meta`/`remove_from_round` on `!remove_from`.

4. **Replace `sync_meta` handler** with edge-aware `!sync`:
   - Aliases: `sync_meta`, `sync_feeders`, `sync_round`
   - Read `target_group` from the channel's category
   - `default_edge = TYPE_DEFAULTS.get(target_group.type, {}).get("default_edge", "feeds")`
   - For each puzzle in the category (skip `target_group.target_puzzle_id`):
     call `await self.service.tag_edge_only(puzzle, target_group, default_edge, guild=None)`
     (no channel move during sync)
   - Collect summaries per puzzle; report aggregate
   
5. **Update convert handlers** (`convert_to_meta`, add `convert_to_capstone`,
   `convert_to_group`, `convert_to_runaround`):
   - Each calls `await self.service.convert_to_type(ctx.guild, hunt, puzzle, "<Type>")`
   - Success message includes the summary string from the return value

6. **Rename `convert_to_feeder` handler → `convert_to_puzzle`**; keep `convert_to_feeder`
   as an alias. Two-step confirm flow unchanged.

7. **Delete `pool` handler** entirely.

8. **Delete `nest` and `source_mode` handlers** entirely. Keep the `set_parent_group`
   and `set_source_mode` service method calls inside `_round_impl`/convert handlers.

**Acceptance (Phase 3):**
```
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-p3.log 2>&1
```
Must pass. `pipenv run ruff check` → exit 0.
Sanity: `grep -n "def pool\|def nest\b\|def source_mode\b" bot/cogs/puzzles.py` → nothing.

Commit: `feat(taggroup): new bang commands + convert family (Milestone C Phase 3)`

---

## Phase 4 — Web endpoints (Sonnet)

Edit `bot/web/actions.py`:

1. **`/hunts/<uid>/metas` (POST)** — add `type` body param:
   ```python
   group_type = (body.get("type") or "Metapuzzle").strip()
   if group_type not in TYPE_DEFAULTS:
       return jsonify({"error": "bad_type"}), 400
   ```
   Pass as `round_type=group_type` to `create_tag_group`.
   Import `TYPE_DEFAULTS` from `bot.core.puzzle_service`.

2. **`/puzzles/<id>/meta` (POST)** — extend dispatch:
   - Read `edge = body.get("edge", "feeds")`; validate in `{"feeds", "informs", "none"}`
   - Read `only = bool(body.get("only", False))`
   - `mode=move` → treat as `mode=add, only=True` (backward compat)
   - New dispatch:
     - `mode=add, only=False` → `tag_with_edge(puzzle, group, edge)`
     - `mode=add, only=True` → `tag_edge_only(puzzle, group, edge, guild)`
     - `mode=remove` → `untag_from(puzzle, group)` (edge-agnostic, no change)
     - `mode=clear` → loop untag (no change)
   - Response: add `"summary"` key for add/only paths if applicable (optional).

3. **New convert endpoints** — follow the existing `convert_to_meta` pattern exactly:
   ```python
   @actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-capstone", methods=["POST"])
   @login_required
   async def convert_to_capstone(puzzle_id):
       # same auth + lock boilerplate as convert_to_meta
       # call: await cog.service.convert_to_type(None, hunt_obj, puzzle, "Capstone")
       # return: {"ok": True, "summary": summary}
   ```
   Add `convert_to_group` (→ "Group") and `convert_to_runaround` (→ "Runaround") the same way.

4. **`/convert-to-feeder` → `/convert-to-puzzle`** — add new route at
   `/puzzles/<id>/convert-to-puzzle`; keep old `/convert-to-feeder` route as-is (backward
   compat for the frontend which still calls it). Both call `convert_to_puzzle`.
   Same for the `/preview` endpoint.

5. **Update `convert_to_meta` endpoint** to call `convert_to_type(... "Metapuzzle")`
   instead of `cog.service.convert_to_meta(...)`. Since `convert_to_meta` service method
   still exists (it's only the old tag-based primitives that are removed), verify first:
   `grep -n "def convert_to_meta" bot/core/puzzle_service.py`. If it's been folded into
   `convert_to_type`, update the call.

**Acceptance (Phase 4):**
```
timeout 180 pipenv run python -m pytest tests/test_edge_web.py tests/ -q > /tmp/test-p4.log 2>&1
```
Must pass. `pipenv run ruff check` → exit 0.

Commit: `feat(taggroup): web API edge + type params + new convert endpoints (Milestone C Phase 4)`

---

## Verification (whole milestone)

- Full suite green: `timeout 180 pipenv run python -m pytest tests/ -q` → 0 failures.
- `pipenv run ruff check` → exit 0.
- Old methods gone: `grep -rn "\.add_to_meta\|\.move_to_meta\|\.remove_from_meta\|\.clear_metas\|def pool\|def nest\b\|def source_mode\b" bot/` → nothing (except comments).
- New commands present: `grep -n "def feeds\b\|def feeds_only\|def informs\b\|def informs_only\|def groups_with\|def remove_from\b\|def sync\b\|convert_to_capstone\|convert_to_group\|convert_to_runaround\|convert_to_puzzle" bot/cogs/puzzles.py` → all listed.
- `TYPE_DEFAULTS` present: `grep -n "TYPE_DEFAULTS" bot/core/puzzle_service.py` → found.

## Rollback / recovery notes

- Each phase is its own commit — revert the last commit to back out a phase.
- No migration to undo.
- If blocked, write `.ai/active/blocker.md` and STOP (two-strikes rule).
