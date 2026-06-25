# Milestone B — Edge Model & Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply migration 012 (edge value rewrites + drop `kind` column), update all code that references old edge literals or the `kind` field, add edge constants and derived helpers, and add a feeds filter to the subtree harvest.

**Architecture:** A single behaviour-changing unit: migration + code all land together. No expand/contract. The migration rewrites stored `edge_type` values (`feeds-meta`→`feeds`, `aggregates-into`→`none`), changes column defaults, and drops the `kind` column. Code changes update every literal, remove the field, and add minimal derived helpers. Tests update in lockstep — no assertions deleted, only vocabulary changed; the one test asserting the dropped column is removed.

**Tech Stack:** Python 3, MySQL 8.0 (puzzlebot_test), pytest, pipenv

---

## Authoritative specs

- `docs/superpowers/specs/2026-06-25-taggroup-schema-changes.md` — Migration 012 DDL, "With 012" code table
- `docs/superpowers/specs/2026-06-24-taggroup-edges-and-commands-design.md` — Edge model design

## Files changed in this milestone

| File | Action |
|---|---|
| `migrations/012_edge_model.sql` | Create |
| `bot/store/tag_group_data.py` | Remove `kind` field; add edge constants; add `has_target` property |
| `bot/store/rollup_data.py:33` | Default `"feeds-meta"` → `"feeds"` |
| `bot/store/mysqldb.py:377` | Fallback `"feeds-meta"` → `"feeds"` in `get_tags()` |
| `bot/core/puzzle_service.py` | Literal rewrites; remove `round_kind` param + `kind=` constructor; add `is_fed`/`is_informed`/`is_metameta` helpers; feeds filter in `collect_subtree_puzzles`; `explicit` mode `"aggregates-into"` → `"none"` |
| `bot/cogs/puzzles.py` | Remove `kind` from `command_map`; `"aggregates-into"` → `"none"` in `pool` |
| `tests/test_round_data.py` | Remove `kind` assertions (field gone) |
| `tests/test_backfill_consistency.py` | Remove `test_kind_backfill_matches_meta_shape` (column gone) |
| `tests/test_rollup_data.py` | `"feeds-meta"` → `"feeds"` in default assertion |
| `tests/test_select_rollup_members.py` | Literal rewrites; add `tag_edges` to subtree fixture |
| `tests/test_rollup_source_mode.py` | Literal rewrites; add `tag_edges` to subtree + round fixtures |
| `tests/test_tag_edges.py` | Literal rewrites throughout |
| `tests/test_m7_commands.py` | `"aggregates-into"` → `"none"` in pool assertion |

---

## Run rules (inlined)

- **Test command:** `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` (single statement, no tail)
- **Lint command:** `pipenv run ruff check`
- A run that exits 124 was killed — read the log for partial output
- Invoke `superpowers:systematic-debugging` on any unexpected failure before touching anything
- Invoke `superpowers:verification-before-completion` before claiming a task done — paste the acceptance command output
- **Two-strikes rule:** if an acceptance check fails twice without a clear fix, write `.ai/active/blocker.md` and stop

---

## Task 1: Baseline

**Files:** none changed

- [ ] **Step 1: Verify suite is still GREEN from Milestone A**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-b-baseline.log 2>&1
tail -5 /tmp/test-b-baseline.log
```

Expected: summary line showing 0 failures (392 passed from Milestone A)

- [ ] **Step 2: Confirm 011 is the highest migration**

```bash
ls migrations/
```

Expected: `011_taggroup_rename.sql` is the highest numbered file (no `012` yet)

- [ ] **Step 3: Confirm lint is clean**

```bash
pipenv run ruff check
```

Expected: exit 0

---

## Task 2: Write migration 012

**Files:**
- Create: `migrations/012_edge_model.sql`

- [ ] **Step 1: Write the migration file**

```sql
-- 012_edge_model.sql
-- Rewrite edge vocabulary: feeds-meta→feeds, aggregates-into→none.
-- Retarget column defaults. Drop now-derived kind column.

-- 1. Rewrite stored edge values (run before default change so no
--    in-flight insert lands an old value between steps).
UPDATE `tags`    SET `edge_type` = 'feeds' WHERE `edge_type` = 'feeds-meta';
UPDATE `tags`    SET `edge_type` = 'none'  WHERE `edge_type` = 'aggregates-into';
UPDATE `rollups` SET `edge_type_filter` = 'feeds'
                 WHERE `edge_type_filter` = 'feeds-meta';

-- 2. Retarget column defaults so new rows speak the new vocabulary.
ALTER TABLE `tags`
  ALTER COLUMN `edge_type` SET DEFAULT 'feeds';
ALTER TABLE `rollups`
  ALTER COLUMN `edge_type_filter` SET DEFAULT 'feeds';

-- 3. Drop kind — now derived (has-target = target_puzzle_id IS NOT NULL;
--    fed-vs-informed from incoming edges; metameta from structure).
ALTER TABLE `tag_group`
  DROP COLUMN `kind`;
```

- [ ] **Step 2: Apply the migration**

```bash
pipenv run python migrations/migrate.py migrate
```

Expected: migration applies cleanly; output shows 012 applied

- [ ] **Step 3: Verify DB state**

```bash
pipenv run python -c "
from bot.store import mydb
cur = mydb.cursor()
cur.execute('SELECT DISTINCT edge_type FROM tags')
print('tags edge_type values:', cur.fetchall())
cur.execute('SELECT DISTINCT edge_type_filter FROM rollups')
print('rollup filter values:', cur.fetchall())
cur.execute('SHOW COLUMNS FROM tag_group LIKE \"kind\"')
print('kind column (expect empty):', cur.fetchall())
cur.close()
"
```

Expected:
- `tags edge_type values` contains only `feeds` and/or `none` (no `feeds-meta`, no `aggregates-into`)
- `rollup filter values` contains only `feeds`
- `kind column` is empty (column dropped)

---

## Task 3: Add edge constants

**Files:**
- Modify: `bot/store/tag_group_data.py`

- [ ] **Step 1: Add module-level edge constants at the top of `tag_group_data.py`**

Add immediately after the `logger = ...` line:

```python
# Edge-type vocabulary constants — the three allowed values.
EDGE_FEEDS = "feeds"
EDGE_INFORMS = "informs"
EDGE_NONE = "none"
```

These are used in later tasks to replace raw string literals.

---

## Task 4: Remove `TagGroupData.kind` field and add `has_target` property

**Files:**
- Modify: `bot/store/tag_group_data.py`

- [ ] **Step 1: Remove the `kind` field from `TagGroupData` dataclass**

Current line (line 23):
```python
    kind: str = ""               # meta / metameta / metaless
```

Remove it entirely. The field no longer exists after migration 012 drops the column.

- [ ] **Step 2: Add `has_target` property to `TagGroupData`**

Add after the `archive_time` field and before the `T = TypeVar(...)` line:

```python
    @property
    def has_target(self) -> bool:
        """True if this group has a target puzzle (not a metaless/Group type)."""
        return bool(self.target_puzzle_id)
```

---

## Task 5: Update `RollupData` edge default

**Files:**
- Modify: `bot/store/rollup_data.py`

- [ ] **Step 1: Change the `edge_type_filter` default from `"feeds-meta"` to `"feeds"`**

Current (line 33):
```python
    edge_type_filter: Optional[str] = "feeds-meta"  # inert until M7
```

Replace with:
```python
    edge_type_filter: Optional[str] = "feeds"
```

---

## Task 6: Update `mysqldb.py` fallback edge literal

**Files:**
- Modify: `bot/store/mysqldb.py`

- [ ] **Step 1: Update the `get_tags()` fallback from `"feeds-meta"` to `"feeds"`**

Current (line 377):
```python
            puzzle.tag_edges[row['tag_group_id']] = row['edge_type'] or "feeds-meta"
```

Replace with:
```python
            puzzle.tag_edges[row['tag_group_id']] = row['edge_type'] or "feeds"
```

---

## Task 7: Update `puzzle_service.py` — literals, kind removal, helpers, feeds filter

**Files:**
- Modify: `bot/core/puzzle_service.py`

### 7a. Rewrite edge literals in `select_rollup_members`

- [ ] **Step 1: Update `explicit` mode filter (aggregates-into → none)**

Current (line 434):
```python
                if p.tag_edges.get(tag_group.id) == "aggregates-into"
```

Replace with:
```python
                if p.tag_edges.get(tag_group.id) == "none"
```

- [ ] **Step 2: Update `round` mode defaults (feeds-meta → feeds)**

Current (lines 438-441):
```python
            filt = rollup.edge_type_filter or "feeds-meta"
            return [
                p for p in feeders
                if p.tag_edges.get(tag_group.id, "feeds-meta") == filt
            ]
```

Replace with:
```python
            filt = rollup.edge_type_filter or "feeds"
            return [
                p for p in feeders
                if p.tag_edges.get(tag_group.id, "feeds") == filt
            ]
```

### 7b. Add feeds filter to `collect_subtree_puzzles`

- [ ] **Step 3: Update `collect_subtree_puzzles` to filter to `feeds` edges only**

Current body of the inner loop (lines 630-633):
```python
            for p in PuzzleJsonDb.get_all_from_tag_group(rnd.id):
                if p.id not in seen_ids and p.solved:
                    seen_ids.add(p.id)
                    puzzles.append(p)
```

Replace with:
```python
            for p in PuzzleJsonDb.get_all_from_tag_group(rnd.id):
                if p.id not in seen_ids and p.solved and p.tag_edges.get(rnd.id) == "feeds":
                    seen_ids.add(p.id)
                    puzzles.append(p)
```

### 7c. Remove `round_kind` parameter and `kind=` constructor set in `create_tag_group`

- [ ] **Step 4: Remove `round_kind` from the signature of `create_tag_group`**

Current signature (line 1294-1295):
```python
    async def create_tag_group(self, guild, hunt, name, notifier=None,
                               meta_puzzle_name=None, round_type="Metapuzzle", round_kind="meta"):
```

Replace with:
```python
    async def create_tag_group(self, guild, hunt, name, notifier=None,
                               meta_puzzle_name=None, round_type="Metapuzzle"):
```

- [ ] **Step 5: Remove the docstring mention of `round_kind`**

Current (line 1303):
```python
        ``round_type`` / ``round_kind`` let callers specify group classification.
```

Replace with:
```python
        ``round_type`` lets callers specify group classification.
```

- [ ] **Step 6: Remove `kind=round_kind` from the `TagGroupData` constructor call**

Current (line 1342):
```python
                type=round_type, kind=round_kind,
```

Replace with:
```python
                type=round_type,
```

### 7d. Remove `kind="meta"` from `convert_to_meta` constructor

- [ ] **Step 7: Remove `kind="meta"` from the constructor in the convert-to-meta path**

Current (line 1028):
```python
            type="Metapuzzle",
            kind="meta",
```

Replace with:
```python
            type="Metapuzzle",
```

### 7e. Add derived helpers

- [ ] **Step 8: Add `is_fed`, `is_informed`, and `is_metameta` helpers to `puzzle_service.py`**

Add these as module-level functions near the top of the file (after imports), or as methods on `PuzzleService`. Module-level pure functions are preferable for testability. Add them just before the `PuzzleService` class definition:

```python
def is_fed(tag_group_id: int, puzzles_in_group: list) -> bool:
    """Return True if any member of the group has a 'feeds' edge to it.

    A group with any feeds-edge member is a 'meta' in the old vocabulary.
    puzzles_in_group is the list returned by PuzzleJsonDb.get_all_from_tag_group.
    """
    return any(
        p.tag_edges.get(tag_group_id) == "feeds"
        for p in puzzles_in_group
    )


def is_informed(tag_group_id: int, puzzles_in_group: list) -> bool:
    """Return True if any member of the group has an 'informs' edge to it.

    A group with any informs-edge member is a 'capstone' in the design vocabulary.
    """
    return any(
        p.tag_edges.get(tag_group_id) == "informs"
        for p in puzzles_in_group
    )


def is_metameta(tag_group, all_tag_groups: list, puzzles_in_group: list) -> bool:
    """Return True if the group's feeders are themselves meta groups (have targets).

    A group is metameta when its feeding members are target puzzles of other groups
    — i.e. the group aggregates meta answers, not feeder answers.
    """
    target_puzzle_ids = {g.target_puzzle_id for g in all_tag_groups if g.target_puzzle_id}
    return any(
        p.tag_edges.get(tag_group.id) == "feeds" and p.id in target_puzzle_ids
        for p in puzzles_in_group
    )
```

---

## Task 8: Update `puzzles.py` cog — kind removal + pool literal

**Files:**
- Modify: `bot/cogs/puzzles.py`

### 8a. Remove `kind` from `command_map`

- [ ] **Step 1: Remove `kind` from the `command_map` tuple and unpacking**

Current `command_map` (lines 1221-1229):
```python
        # command_map: key → (group_type, kind, create_meta_puzzle, meta_name_suffix)
        command_map = {
            "r": ("Round", "meta", True, " - meta"),
            "round": ("Round", "meta", True, " - meta"),
            "mp": ("Metapuzzle", "meta", True, None),
            "metapuzzle": ("Metapuzzle", "meta", True, None),
            "roundnometa": ("Metaless Round", "metaless", False, None),
            "rnm": ("Metaless Round", "metaless", False, None),
        }
```

Replace with:
```python
        # command_map: key → (group_type, create_meta_puzzle, meta_name_suffix)
        command_map = {
            "r": ("Round", True, " - meta"),
            "round": ("Round", True, " - meta"),
            "mp": ("Metapuzzle", True, None),
            "metapuzzle": ("Metapuzzle", True, None),
            "roundnometa": ("Metaless Round", False, None),
            "rnm": ("Metaless Round", False, None),
        }
```

- [ ] **Step 2: Update the tuple unpacking on line 1231**

Current:
```python
        group_type, kind, create_meta, suffix = command_map[command_key]
```

Replace with:
```python
        group_type, create_meta, suffix = command_map[command_key]
```

- [ ] **Step 3: Remove `round_kind=kind` from the `create_tag_group` call**

Current (line 1246):
```python
                round_kind=kind,
```

Remove this line entirely (the `create_tag_group` signature no longer accepts `round_kind`).

### 8b. Update `pool` command literals

- [ ] **Step 4: Update `"aggregates-into"` → `"none"` in the `pool` command**

Three locations in the `pool` command body:

Line 1181 (docstring):
```python
        """*Tag puzzle as aggregates-into for its meta: !pool (run in a puzzle channel)*"""
```
Replace with:
```python
        """*Tag puzzle with 'none' edge for its meta: !pool (run in a puzzle channel)*"""
```

Line 1190 (comment):
```python
        # Find rounds this puzzle belongs to and tag them with aggregates-into edge
```
Replace with:
```python
        # Find rounds this puzzle belongs to and tag them with none edge
```

Line 1195:
```python
                PuzzleJsonDb.tag_puzzle_with_edge(puzzle.id, rid, "aggregates-into")
```
Replace with:
```python
                PuzzleJsonDb.tag_puzzle_with_edge(puzzle.id, rid, "none")
```

Line 1202:
```python
            await ctx.send(":white_check_mark: Tagged as aggregates-into. Sheets updated.")
```
Replace with:
```python
            await ctx.send(":white_check_mark: Tagged as none (pool member). Sheets updated.")
```

---

## Task 9: Update tests

**Files:**
- Modify: `tests/test_round_data.py`
- Modify: `tests/test_backfill_consistency.py`
- Modify: `tests/test_rollup_data.py`
- Modify: `tests/test_select_rollup_members.py`
- Modify: `tests/test_rollup_source_mode.py`
- Modify: `tests/test_tag_edges.py`
- Modify: `tests/test_m7_commands.py`

### 9a. `test_round_data.py` — remove `kind` assertions

- [ ] **Step 1: Remove assertions referencing `.kind` from `test_round_data.py`**

The file currently has three `kind`-related assertions:
- `assert r.kind == ""` in `test_typed_group_defaults`
- `"kind": "meta"` dict key in `test_import_dict_round_trips_typed_group_fields`
- `assert r.kind == "meta"` in `test_import_dict_round_trips_typed_group_fields`

After removing `kind` from `TagGroupData`, the field no longer exists and `import_dict` no longer populates it. Remove all three:

Remove the `assert r.kind == ""` line from `test_typed_group_defaults`:
```python
def test_typed_group_defaults():
    r = TagGroupData()
    assert r.status == "active"
    assert r.parent_group_id is None
```

In `test_import_dict_round_trips_typed_group_fields`, remove the `"kind": "meta"` key from the dict and the `assert r.kind == "meta"` assertion:
```python
def test_import_dict_round_trips_typed_group_fields():
    r = TagGroupData.import_dict({
        "id": 3, "hunt_id": 10, "target_puzzle_id": 5, "group_code": "ABC123",
        "status": "solved", "parent_group_id": 9,
    })
    assert r.status == "solved"
    assert r.parent_group_id == 9
    assert r.group_code == "ABC123"
```

### 9b. `test_backfill_consistency.py` — remove dropped-column test

- [ ] **Step 2: Remove `test_kind_backfill_matches_meta_shape` from `test_backfill_consistency.py`**

The `kind` column no longer exists in the DB after migration 012. Remove the entire function and the column reference from the SQL query:

Remove the function `test_kind_backfill_matches_meta_shape` entirely (lines 24-34).

Update `_rounds()` to remove `r.kind` from the SELECT:
```python
def _rounds():
    from bot.store import mydb
    cur = mydb.cursor(dictionary=True)
    cur.execute(
        "SELECT r.id, r.target_puzzle_id, r.status, r.solve_time, r.archive_time "
        "FROM tag_group r"
    )
    rows = cur.fetchall()
    cur.close()
    return rows
```

Remove the now-unused `LEFT JOIN puzzles` — the query doesn't need `meta_metameta` anymore.

### 9c. `test_rollup_data.py` — update default assertion

- [ ] **Step 3: Update `"feeds-meta"` → `"feeds"` in the default assertion**

Current (line 11):
```python
    assert r.edge_type_filter == "feeds-meta"
```

Replace with:
```python
    assert r.edge_type_filter == "feeds"
```

### 9d. `test_select_rollup_members.py` — literal rewrites + subtree fixture

- [ ] **Step 4: Update all edge literals in `test_select_rollup_members.py`**

In `test_round_mode_returns_feeds_meta_members_without_rendering` (line 49):
```python
        feeder.tag_edges = {5: "feeds-meta"}
```
→
```python
        feeder.tag_edges = {5: "feeds"}
```

In `test_round_mode_excludes_non_matching_edge_type` (lines 64, 66) — also update the test description comment:
```python
        # An 'none' edge is NOT a feeds feeder; round mode drops it.
        ...
        feeder.tag_edges = {5: "feeds"}
        pooled = make_puzzle(id=3, name="Pooled", solution="P")
        pooled.tag_edges = {5: "none"}
```

In `test_subtree_mode_returns_branch_solved_puzzles_without_rendering` — `collect_subtree_puzzles` now filters by feeds edge; the `solved` puzzle needs `tag_edges` set. After `solved = make_puzzle(...)`, add:
```python
        solved.tag_edges = {6: "feeds"}
```

In `test_explicit_mode_returns_only_aggregates_into_members` — update literal and description:
```python
    async def test_explicit_mode_returns_only_none_edge_members(self):
        ...
        pooled.tag_edges = {5: "none"}
        other = make_puzzle(id=3, name="Feeder", solution="F")
        other.tag_edges = {5: "feeds"}
```

In `test_falsy_source_mode_defaults_to_round` (line 111):
```python
        feeder.tag_edges = {5: "feeds"}
```

### 9e. `test_rollup_source_mode.py` — literal rewrites + fixture fixups

- [ ] **Step 5: Update `test_rollup_source_mode.py`**

`test_round_mode_calls_add_metapuzzle_data` — no fixture change needed. The round-mode filter uses `p.tag_edges.get(tag_group_id, "feeds")` with `"feeds"` as the default, so a puzzle with empty `tag_edges` passes the filter automatically.

In `test_subtree_mode_aggregates_branch_solved_puzzles` — the `collect_subtree_puzzles` function now checks `p.tag_edges.get(rnd.id) == "feeds"` (no default), so a puzzle with no `tag_edges` is excluded. Add `tag_edges` to `solved`:
```python
        solved = make_puzzle(id=2, name="Branch", solved=True, solution="X")
        solved.tag_edges = {6: "feeds"}
```

In `test_explicit_mode_uses_aggregates_into_members`:
- Rename to `test_explicit_mode_uses_none_edge_members` (optional but accurate)
- Change `pooled.tag_edges = {5: "aggregates-into"}` → `pooled.tag_edges = {5: "none"}`
- Change `other.tag_edges = {5: "feeds-meta"}` → `other.tag_edges = {5: "feeds"}`
- Update comment: `# 'aggregates-into' are aggregated` → `# 'none' edge members are aggregated`

### 9f. `test_tag_edges.py` — literal rewrites

- [ ] **Step 6: Update all edge literals in `test_tag_edges.py`**

In `test_get_tags_populates_tag_edges`:
- `_insert_tag(rid_default, pid)` comment (line 82): `# defaults to feeds-meta` → `# defaults to feeds`
- `_insert_tag(rid_aggr, pid, edge_type="aggregates-into")` → `edge_type="none"`
- `assert puzzle.tag_edges[rid_default] == "feeds-meta"` → `== "feeds"`
- `assert puzzle.tag_edges[rid_aggr] == "aggregates-into"` → `== "none"`

In `test_tag_puzzle_with_edge_inserts_then_updates`:
- `PuzzleJsonDb.tag_puzzle_with_edge(pid, rid, "aggregates-into")` → `"none"`
- `PuzzleJsonDb.tag_puzzle_with_edge(pid, rid, "feeds-meta")` → `"feeds"`
- `assert rows[0]["edge_type"] == "feeds-meta"` → `== "feeds"`

### 9g. `test_m7_commands.py` — pool assertion

- [ ] **Step 7: Update pool assertion in `test_m7_commands.py`**

Current (line 131):
```python
        pdb.tag_puzzle_with_edge.assert_called_once_with(42, 5, "aggregates-into")
```

Replace with:
```python
        pdb.tag_puzzle_with_edge.assert_called_once_with(42, 5, "none")
```

---

## Task 10: Verification and commit

- [ ] **Step 1: Run the full suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-b-final.log 2>&1
tail -10 /tmp/test-b-final.log
```

Expected: same count as baseline, 0 failures. If any fail, invoke `superpowers:systematic-debugging`.

- [ ] **Step 2: Run ruff**

```bash
pipenv run ruff check
```

Expected: exit 0

- [ ] **Step 3: Run acceptance sanity checks**

```bash
# Edge values in DB are clean
pipenv run python -c "
from bot.store import mydb
cur = mydb.cursor()
cur.execute('SELECT DISTINCT edge_type FROM tags')
print('tags:', cur.fetchall())
cur.execute('SELECT DISTINCT edge_type_filter FROM rollups')
print('rollups:', cur.fetchall())
cur.execute('SHOW COLUMNS FROM tag_group LIKE \"kind\"')
print('kind (expect empty):', cur.fetchall())
cur.close()
"

# No old literals survive in bot/ code
grep -rn "feeds-meta\|aggregates-into" bot/ --include="*.py"
# Expected: no output (zero matches)

# kind field gone from dataclass and constructors
grep -rn "kind=\|\.kind\b" bot/ --include="*.py" | grep -v "reconcile\|actions.py"
# Expected: no output
```

- [ ] **Step 4: Commit**

```bash
git add migrations/012_edge_model.sql \
        bot/store/tag_group_data.py bot/store/rollup_data.py bot/store/mysqldb.py \
        bot/core/puzzle_service.py bot/cogs/puzzles.py \
        tests/test_round_data.py tests/test_backfill_consistency.py \
        tests/test_rollup_data.py tests/test_select_rollup_members.py \
        tests/test_rollup_source_mode.py tests/test_tag_edges.py \
        tests/test_m7_commands.py

git commit -m "feat(taggroup): migration 012 + edge model (feeds/none, drop kind, feeds filter)"
```

---

## Verification (whole milestone)

- Full suite GREEN: `timeout 180 pipenv run python -m pytest tests/ -q` → 0 failures
- `pipenv run ruff check` → exit 0
- `SELECT DISTINCT edge_type FROM tags` ⊆ {feeds, informs, none}
- `SELECT DISTINCT edge_type_filter FROM rollups` ⊆ {feeds}
- `kind` column absent from `tag_group`
- No `"feeds-meta"` or `"aggregates-into"` literals remain in `bot/`
- Derived helpers `is_fed` / `is_informed` / `is_metameta` exist in `puzzle_service.py`
- `TagGroupData.has_target` property exists
- Edge constants `EDGE_FEEDS` / `EDGE_INFORMS` / `EDGE_NONE` exist in `tag_group_data.py`

## Rollback

- Revert the single commit to undo all code changes
- To undo the DB changes: reverse the migration against `puzzlebot_test`
  ```sql
  ALTER TABLE `tag_group` ADD COLUMN `kind` varchar(20) DEFAULT NULL;
  UPDATE `tags`    SET `edge_type` = 'feeds-meta'     WHERE `edge_type` = 'feeds';
  UPDATE `tags`    SET `edge_type` = 'aggregates-into' WHERE `edge_type` = 'none';
  UPDATE `rollups` SET `edge_type_filter` = 'feeds-meta' WHERE `edge_type_filter` = 'feeds';
  ALTER TABLE `tags`    ALTER COLUMN `edge_type`        SET DEFAULT 'feeds-meta';
  ALTER TABLE `rollups` ALTER COLUMN `edge_type_filter` SET DEFAULT 'feeds-meta';
  ```
