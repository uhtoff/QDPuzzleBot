# Milestone B2 — Derive is-target; drop `metapuzzle` + `metameta` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop the denormalised `puzzles.metapuzzle` and `puzzles.metameta` columns; derive is-target from `tag_group.target_puzzle_id` instead, eager-loaded once per puzzle as a transient runtime field so every read site stays a zero-DB attribute access.

**Architecture:** Two commits. **Commit 1 (prep):** a pure mechanical rename of the store binding `RoundJsonDb` → `TagGroupJsonDb` (the A-rename renamed the class `MySQLTagGroupJsonDb` and the `tag_group` table but left this singleton named `Round`). **Commit 2 (behaviour):** the column drop. `is_target` becomes a transient `PuzzleData` field (like `tag_edges`), populated in `construct_puzzle()` via an indexed `EXISTS` on `tag_group.target_puzzle_id`, excluded from the generic `commit()` via `SPECIAL_ATTR`. All `is_metapuzzle()` readers route to the `.is_target` attribute; the four in-memory writers swap `metapuzzle = 1/False` for `is_target = True/False`; the two raw-SQL list paths derive via JOIN/EXISTS; the dead `metameta` surface (a command + a JSON key + a frontend field) is removed. Migration 013 drops both columns. The code is forward-compatible (works before the migration applies), so edit order within the working tree is free; commit 2 bundles code + migration as one behaviour unit.

**Tech Stack:** Python 3, MySQL 8.0 (puzzlebot_test), pytest, pipenv; frontend TypeScript (Mantine).

---

## Authoritative specs

- `docs/superpowers/specs/2026-06-25-milestone-b2-derive-has-target-design.md` — B2 design + audit
- `docs/superpowers/specs/2026-06-24-taggroup-edges-and-commands-design.md` — parent edge model

## Sequencing

After Milestone C (done, `c4c6091`), before Milestone D. D consumes is-target via `.is_target` only.

## Design decisions locked at plan time

- **`is_target` seam:** a transient `PuzzleData` field, **eager-loaded** in the single read seam `construct_puzzle()`. Read sites do `puzzle.is_target` (no DB call). Resolves the ~50-fixture test problem: tests set `make_puzzle(is_target=True)`, no DB mocking.
- **Naming — `is_target`, not `has_target`:** the puzzle *is* the target of a group; the group *has* a target. B already shipped `TagGroupData.has_target` (`bool(self.target_puzzle_id)`) on the group side, so the puzzle-side field must be `is_target` to stay distinct and accurate.
- **No edge-type conflation (verified, do not re-litigate):** an audit of all 19 `is_metapuzzle()`/`.metapuzzle` readers (notifier, gsheet sheet-layout, web actions convert/clear guards, puzzles-cog convert guards, puzzle_service) confirmed **every site is target-safe** — each needs only "is this puzzle the target of *any* tag_group", never "fed-meta specifically". `metapuzzle == 1` was always set in lockstep with `target_puzzle_id` for *all* targeted group types (fed metas, capstones, runarounds), and the feeds-vs-informs distinction lives solely in the edge layer (`tag_edges`, `is_fed`/`is_informed`, `convert_to_type`'s edge-flip) — never in these guards. So plain `is_target` is correct at every site; **no reader should route through `is_fed`**.
- **Rename `RoundJsonDb` → `TagGroupJsonDb`:** separate prep commit (Task 2), B2 builds on it.
- **`is_meta` param on `create_puzzle`:** kept (body swapped), so the returned in-memory meta object reports `is_target=True` without a re-read.
- **`metameta`:** dead (never written, its one reader always returns `None`). Removed entirely, including the `is_metameta` JSON key and frontend field (rendered nowhere). `is_meta` JSON key stays, now backed by the derived value (drives the visible META badge).

## Files changed

| File | Commit | Action |
|---|---|---|
| `bot/store/__init__.py` + 28 other files | 1 (rename) | `RoundJsonDb` → `TagGroupJsonDb` (186 refs) |
| `migrations/013_drop_role_flags.sql` | 2 | Create |
| `bot/store/puzzle_data.py` | 2 | Drop `metapuzzle`/`metameta` fields + `is_metapuzzle()`; add transient `is_target` |
| `bot/store/mysqldb.py` | 2 | Add `is_target` to puzzle `SPECIAL_ATTR`; add `get_is_target()`; call it in `construct_puzzle()`; `get_unassigned` SQL |
| `bot/core/puzzle_service.py` | 2 | 4 writer swaps + readers `:594,:1142` |
| `bot/core/notifier.py` | 2 | readers `:119,:146,:157` |
| `bot/cogs/puzzles_gsheet.py` | 2 | readers `:227,:234,:498`; raw `:362,:743` |
| `bot/web/actions.py` | 2 | readers `:523,:835,:906,:943,:997,:999,:1090` |
| `bot/cogs/puzzles.py` | 2 | readers `:1324,:1349,:1374,:1399,:1427`; remove dead `update_metameta` `:507-517` |
| `bot/web/data.py` | 2 | board query derive `:165,:177,:196`; drop `is_metameta` `:197` |
| `frontend/src/types.ts` | 2 | drop `is_metameta` `:65` |
| `tests/conftest.py` | 2 | `make_puzzle` + `make_puzzle_row` |
| `tests/test_aggregate_to_tab.py` | 2 | local `make_puzzle` + fixtures |
| 13 other test files | 2 | fixture/assertion rewrites (Task 11) |

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

- [ ] **Step 1: Verify suite is GREEN**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-b2-baseline.log 2>&1
tail -5 /tmp/test-b2-baseline.log
```

Expected: summary line, 0 failures.

- [ ] **Step 2: Confirm 012 is the highest migration**

```bash
ls migrations/
```

Expected: `012_edge_model.sql` highest, no `013`.

- [ ] **Step 3: Re-verify the audit still holds after C** (the spec demands this)

```bash
# metameta still never written (only dataclass default + dead reader + JSON):
grep -rn "metameta" bot/ --include="*.py"
# Expected writers: NONE. Only puzzle_data.py default, puzzles.py:514 get_by_attr(metameta=1),
# web/data.py select+emit, and the add_metametapuzzle_data RENDERER (which is NOT the column).

# every is_metapuzzle() reader uses is-target meaning (no "fed vs informed" logic):
grep -rn "is_metapuzzle" bot/ --include="*.py"

# the only place still named "Round" is the binding (class already renamed):
grep -rn "RoundJsonDb" bot/store/__init__.py
```

If C introduced a NEW `metameta` writer or a `is_metapuzzle()` reader that means "fed" rather than "is-target", STOP and update this plan before proceeding.

---

## Task 2: Prep commit — rename `RoundJsonDb` → `TagGroupJsonDb`

**Files:** `bot/store/__init__.py` + every `.py` referencing `RoundJsonDb` (186 refs across 29 files). Pure mechanical, zero behaviour change.

- [ ] **Step 1: Rename the binding and all references**

`RoundJsonDb` is a distinct identifier (never appears in strings/comments), so a word-boundary `sed` is safe.

```bash
grep -rl "RoundJsonDb" bot/ tests/ --include="*.py" \
  | xargs sed -i 's/\bRoundJsonDb\b/TagGroupJsonDb/g'
```

- [ ] **Step 2: Verify no `RoundJsonDb` remains and the binding reads correctly**

```bash
grep -rn "RoundJsonDb" bot/ tests/ --include="*.py"
# Expected: NO output.
grep -n "TagGroupJsonDb = MySQLTagGroupJsonDb" bot/store/__init__.py
# Expected: line 81 now reads  TagGroupJsonDb = MySQLTagGroupJsonDb(mydb=mydb)
```

- [ ] **Step 3: Run the suite (rename must be behaviour-neutral)**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-b2-rename.log 2>&1
tail -5 /tmp/test-b2-rename.log
```

Expected: same count as baseline, 0 failures.

- [ ] **Step 4: Lint**

```bash
pipenv run ruff check
```

Expected: exit 0.

- [ ] **Step 5: Commit the rename on its own**

```bash
git add -A
git commit -m "refactor(store): rename RoundJsonDb binding to TagGroupJsonDb (vestige of A rename)"
```

> Everything below is **commit 2** — do not commit until Task 12.

---

## Task 3: `PuzzleData` — drop role-flag fields, add transient `is_target`, drop `is_metapuzzle()`

**Files:**
- Modify: `bot/store/puzzle_data.py`

- [ ] **Step 1: Remove the two role-flag fields**

Current (lines 36-37):
```python
    metapuzzle: int = 0
    metameta: int = 0
```

Remove both lines.

- [ ] **Step 2: Add the transient `is_target` field**

It must live in `__dict__` (so `SPECIAL_ATTR` can skip it on write and the loader can set it). Add it immediately after the `tag_edges` field (line 50), mirroring that transient-field comment style:

```python
    is_target: bool = False  # transient: derived at load from tag_group.target_puzzle_id (not a column)
```

- [ ] **Step 3: Remove the `is_metapuzzle()` method**

Current (lines 87-88):
```python
    def is_metapuzzle(self) -> bool:
        return self.metapuzzle == 1
```

Remove both lines. Callers move to the `is_target` attribute in Tasks 5-8.

> No test yet — `import_dict` ignores absent columns (it only sets attrs present in the row), and `is_target` defaults `False` until the loader populates it (Task 4). Verified by the suite at Task 12.

---

## Task 4: Eager-load `is_target` in the store load path; exclude it from `commit()`

**Files:**
- Modify: `bot/store/mysqldb.py`

- [ ] **Step 1: Add `is_target` to the puzzle store's `SPECIAL_ATTR`**

The generic `commit()` (line 41) writes every `__dict__` attr except those in `SPECIAL_ATTR`. `is_target` has no column, so it must be skipped.

Current (line 238):
```python
    SPECIAL_ATTR = ['tags', 'additional_sheets', 'tag_edges']
```

Replace with:
```python
    SPECIAL_ATTR = ['tags', 'additional_sheets', 'tag_edges', 'is_target']
```

- [ ] **Step 2: Add the `get_is_target` enrichment method**

Add immediately after `get_additional_sheets` (after line 390), matching that method's cursor style:

```python
    def get_is_target(self, puzzle):
        """Derive is-target: True iff some tag_group targets this puzzle.

        Eager-loaded here (the single read seam) so is-meta reads stay free
        attribute accesses. Indexed lookup on tag_group.target_puzzle_id."""
        cursor = self.mydb.cursor()
        cursor.execute(
            "SELECT 1 FROM tag_group WHERE target_puzzle_id = %s LIMIT 1",
            (puzzle.id,),
        )
        puzzle.is_target = cursor.fetchone() is not None
        cursor.close()
```

- [ ] **Step 3: Call it from `construct_puzzle` (the single seam all reads funnel through)**

Current (lines 240-244):
```python
    def construct_puzzle(self, row):
        puzzle = PuzzleData.import_dict(row)
        self.get_tags(puzzle)
        self.get_additional_sheets(puzzle)
        return puzzle
```

Replace with:
```python
    def construct_puzzle(self, row):
        puzzle = PuzzleData.import_dict(row)
        self.get_tags(puzzle)
        self.get_additional_sheets(puzzle)
        self.get_is_target(puzzle)
        return puzzle
```

---

## Task 5: `puzzle_service.py` — writer swaps + readers

**Files:**
- Modify: `bot/core/puzzle_service.py`

> Note: line `:392` `metapuzzle = PuzzleJsonDb.get_by_attr(...)` is a **local variable name**, not a column — do NOT touch it.

- [ ] **Step 1: `convert_to_type` promote (writer, line 658)**

Current:
```python
            puzzle.metapuzzle = 1
```
Replace with:
```python
            puzzle.is_target = True
```
(The lines above already set `tag_group.target_puzzle_id = puzzle.id`, so this is behaviour-preserving.)

- [ ] **Step 2: `convert_to_puzzle` demote (writer, line 903)**

Current:
```python
        puzzle.metapuzzle = False
```
Replace with:
```python
        puzzle.is_target = False
```
(The lines above delete the targeting group via `get_by_attr(target_puzzle_id=puzzle.id)`, so a re-read would also derive `False`.)

- [ ] **Step 3: `convert_to_meta` (reader line 1142 + writer line 1157)**

Reader (line 1142):
```python
        if puzzle.is_metapuzzle():
```
→
```python
        if puzzle.is_target:
```

Writer (line 1157):
```python
        puzzle.metapuzzle = 1
```
→
```python
        puzzle.is_target = True
```
(Line 1147 already set `new_round.target_puzzle_id = puzzle.id`.)

- [ ] **Step 4: `create_puzzle` (writer line 1359 — keep the `is_meta` param)**

Current (lines 1358-1359):
```python
        if is_meta:
            new_puzzle.metapuzzle = 1
```
Replace with:
```python
        if is_meta:
            new_puzzle.is_target = True
```
(Keep the `is_meta` parameter and its sole caller at `:1469` unchanged — `is_meta` now sets the transient field so the returned in-memory meta reports `is_target` before any re-read. The caller sets `target_puzzle_id` immediately after, keeping the DB consistent.)

- [ ] **Step 5: Reader at line 594**

Current:
```python
        if puzzle.is_metapuzzle() and len(puzzle.tags) <= 1:
```
→
```python
        if puzzle.is_target and len(puzzle.tags) <= 1:
```

---

## Task 6: Route remaining `is_metapuzzle()` readers to `.is_target`

These are pure call→attribute swaps (`puzzle.is_metapuzzle()` → `puzzle.is_target`). Preserve surrounding operators (`not`, `is False`).

- [ ] **Step 1: `bot/core/notifier.py`** — lines 119, 146, 157

```python
# :119   ... if puzzle and puzzle.is_metapuzzle()      → ... if puzzle and puzzle.is_target
# :146   if puzzle.is_metapuzzle():                     → if puzzle.is_target:
# :157   ... if puzzle.is_metapuzzle()                  → ... if puzzle.is_target
```

- [ ] **Step 2: `bot/cogs/puzzles_gsheet.py`** — lines 227, 234, 498

```python
# :227   if new_puzzle.is_metapuzzle():                 → if new_puzzle.is_target:
# :234   if new_puzzle.is_metapuzzle():                 → if new_puzzle.is_target:
# :498   if puzzle_data.is_metapuzzle() is False ...    → if puzzle_data.is_target is False ...
```

- [ ] **Step 3: `bot/web/actions.py`** — lines 523, 835, 906, 943, 997, 999, 1090

```python
# :523   if puzzle.is_metapuzzle():                          → if puzzle.is_target:
# :835   if mode == "clear" and puzzle.is_metapuzzle():      → ... and puzzle.is_target:
# :906   if not puzzle.is_metapuzzle():                      → if not puzzle.is_target:
# :943   if not puzzle.is_metapuzzle():                      → if not puzzle.is_target:
# :997   if error_if_not_meta and not puzzle.is_metapuzzle():→ ... and not puzzle.is_target:
# :999   if not error_if_not_meta and puzzle.is_metapuzzle():→ ... and puzzle.is_target:
# :1090  if not puzzle.is_metapuzzle():                      → if not puzzle.is_target:
```

- [ ] **Step 4: `bot/cogs/puzzles.py`** — lines 1324, 1349, 1374, 1399, 1427

```python
# :1324  elif puzzle.is_metapuzzle():                   → elif puzzle.is_target:
# :1349  elif not puzzle.is_metapuzzle():               → elif not puzzle.is_target:
# :1374  elif not puzzle.is_metapuzzle():               → elif not puzzle.is_target:
# :1399  elif not puzzle.is_metapuzzle():               → elif not puzzle.is_target:
# :1427  elif puzzle.is_metapuzzle() is False:          → elif puzzle.is_target is False:
```

- [ ] **Step 5: Verify no `is_metapuzzle` callers remain**

```bash
grep -rn "is_metapuzzle" bot/
```
Expected: NO output.

---

## Task 7: Raw `.metapuzzle` column readers (gsheet) → `.is_target`

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py`

These read the field directly (`puzzle.metapuzzle == 1`) rather than via `is_metapuzzle()`.

- [ ] **Step 1: line 362**

```python
        if puzzle.metapuzzle == 1:
```
→
```python
        if puzzle.is_target:
```

- [ ] **Step 2: line 743**

```python
            if round_puzzle.metapuzzle == 1:      # metas roll up via their feeders, not here
```
→
```python
            if round_puzzle.is_target:      # metas roll up via their feeders, not here
```

- [ ] **Step 3: Verify no raw `.metapuzzle` reads remain in bot/**

```bash
grep -rn "\.metapuzzle\b" bot/
```
Expected: NO output (the local variable at puzzle_service `:392` is `metapuzzle =`, not `.metapuzzle`).

---

## Task 8: Raw-SQL list paths — derive is-target via `EXISTS`

The hot list endpoints derive query-side (no Python per-row lookup).

### 8a. Board query — `bot/web/data.py`

**Files:** Modify `bot/web/data.py`

> The existing `LEFT JOIN tag_group` is on the rounds the puzzle is tagged *into* — NOT whether the puzzle is itself a target. Use a separate correlated `EXISTS`.

- [ ] **Step 1: Replace the `metapuzzle`/`metameta` columns with a derived `is_target` column**

Current (line 165):
```python
        "puzzles.google_page_id, puzzles.metapuzzle, puzzles.metameta, "
```
Replace with:
```python
        "puzzles.google_page_id, "
        "EXISTS(SELECT 1 FROM tag_group mt WHERE mt.target_puzzle_id = puzzles.id) AS is_target, "
```

- [ ] **Step 2: Update the `ORDER BY`**

Current (line 177):
```python
        "ORDER BY puzzles.metapuzzle DESC, puzzles.solved ASC, puzzles.id ASC",
```
Replace with:
```python
        "ORDER BY is_target DESC, puzzles.solved ASC, puzzles.id ASC",
```

- [ ] **Step 3: Emit `is_meta` from the derived column; drop `is_metameta`**

Current (lines 196-197):
```python
                "is_meta": bool(row["metapuzzle"]),
                "is_metameta": bool(row["metameta"]),
```
Replace with:
```python
                "is_meta": bool(row["is_target"]),
```

### 8b. Unassigned query — `bot/store/mysqldb.py`

**Files:** Modify `bot/store/mysqldb.py`

- [ ] **Step 4: Replace the `p.metapuzzle = 0` predicate with a derived NOT EXISTS**

Current (line 357):
```python
            "   AND p.metapuzzle = 0"
```
Replace with:
```python
            "   AND NOT EXISTS (SELECT 1 FROM tag_group mt WHERE mt.target_puzzle_id = p.id)"
```

---

## Task 9: Remove the dead `metameta` surface

### 9a. Dead `update_metameta` command — `bot/cogs/puzzles.py`

**Files:** Modify `bot/cogs/puzzles.py`

The command reads `get_by_attr(metameta=1)` (always `None`; will error once the column is gone) and is the only `metameta` reader. The real metameta concept has its own structural helper (`puzzle_service.is_metameta`, unaffected). The `add_metametapuzzle_data` renderer is still used by `refresh` (`puzzle_service.py:496`) — **keep it**; only remove the dead command.

- [ ] **Step 1: Remove `_update_metameta_impl` and `update_metameta`**

Remove lines 507-517 entirely:
```python
    async def _update_metameta_impl(self, ctx, metameta, hunt_id):
        all_puzzles = PuzzleJsonDb.get_all_from_hunt(hunt_id)
        await self.get_gsheet_cog(ctx).add_metametapuzzle_data(metameta, all_puzzles)

    @commands.command()
    async def update_metameta(self, ctx):
        hunt_id = self.get_hunt(ctx).id
        meta_meta_puzzle = PuzzleJsonDb.get_by_attr(metameta=1)
        if meta_meta_puzzle:
            await self._update_metameta_impl(ctx, meta_meta_puzzle, hunt_id)
        await ctx.send(":white_check_mark: Meta meta updated")
```

(Leave the `# --- legacy create twins removed ... ---` comment at line 505 in place.)

### 9b. Frontend `is_metameta` field — `frontend/src/types.ts`

**Files:** Modify `frontend/src/types.ts`

`is_metameta` is declared but rendered by no component (`is_meta` drives the META badge and stays).

- [ ] **Step 2: Remove the unused field**

Current (line 65):
```typescript
  is_metameta: boolean;
```
Remove the line.

- [ ] **Step 3: Confirm nothing references it**

```bash
grep -rn "is_metameta" frontend/src/
```
Expected: NO output.

---

## Task 10: Migration 013 — drop the columns

**Files:**
- Create: `migrations/013_drop_role_flags.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 013_drop_role_flags.sql
-- is-target is now derived from tag_group.target_puzzle_id (eager-loaded);
-- metameta was dead (never written). Drop both denormalised role-flag columns.
ALTER TABLE `puzzles` DROP COLUMN `metapuzzle`;
ALTER TABLE `puzzles` DROP COLUMN `metameta`;
```

- [ ] **Step 2: Apply it**

```bash
pipenv run python migrations/migrate.py migrate
```

Expected: 013 applies cleanly.

- [ ] **Step 3: Verify the columns are gone**

```bash
pipenv run python -c "
from bot.store import mydb
cur = mydb.cursor()
cur.execute('SHOW COLUMNS FROM puzzles LIKE \"metapuzzle\"')
print('metapuzzle (expect empty):', cur.fetchall())
cur.execute('SHOW COLUMNS FROM puzzles LIKE \"metameta\"')
print('metameta (expect empty):', cur.fetchall())
cur.close()
"
```

Expected: both empty.

---

## Task 11: Update tests

**Files:** `tests/conftest.py`, `tests/test_aggregate_to_tab.py`, plus the 13 files below.

### 11a. `tests/conftest.py` — the shared factories

- [ ] **Step 1: `make_puzzle` — swap the two role-flag kwargs for `is_target`**

Current signature (lines 16-17) and body (lines 31-32):
```python
    metapuzzle=0,
    metameta=0,
    ...
    p.metapuzzle = metapuzzle
    p.metameta = metameta
```

Replace the signature pair with:
```python
    is_target=False,
```
and the body pair with:
```python
    p.is_target = is_target
```

- [ ] **Step 2: `make_puzzle_row` — drop the `is_metameta` JSON key**

Current: parameter `is_metameta=False,` (line 64) and dict entry `"is_metameta": is_metameta,` (line 83). Remove both (the dashboard JSON no longer carries `is_metameta`). Leave `is_meta` handling intact.

### 11b. `tests/test_aggregate_to_tab.py` — local `make_puzzle` copy

- [ ] **Step 3: Same swap in the local factory (lines 19-27)**

Replace params `metapuzzle=0, metameta=0` with `is_target=False`, and body lines 24-25:
```python
    p.metapuzzle = metapuzzle
    p.metameta = metameta
```
with:
```python
    p.is_target = is_target
```

Then in the test bodies, replace fixture kwargs:
- line 163: `make_puzzle(id=1, name="MetaMeta", metameta=1)` → `make_puzzle(id=1, name="MetaMeta", is_target=True)`
- line 185: `make_puzzle(id=1, name="MM", metameta=1)` → `make_puzzle(id=1, name="MM", is_target=True)`
- line 186: `make_puzzle(id=2, name="AMeta", metapuzzle=1, tags=[10])` → `make_puzzle(id=2, name="AMeta", is_target=True, tags=[10])`

(The `add_metapuzzle_data`/`add_metametapuzzle_data` renderer-call lines are unchanged — they test which renderer fires, not the column.)

### 11c. Fixture kwarg swaps (`metapuzzle=1` → `is_target=True`; drop `metameta=1`)

- [ ] **Step 4: `tests/test_refresh_meta.py`** — lines 61, 71, 87, 109, 123, 137, 153, 168, 181, 196, 230-231. Replace every `metapuzzle=1` with `is_target=True`; at line 87 `make_puzzle(id=1, metapuzzle=1, metameta=1)` → `make_puzzle(id=1, is_target=True)`.

- [ ] **Step 5: `tests/test_round_status.py`** — line 27: `make_puzzle(id=1, metapuzzle=1, tags=[5])` → `make_puzzle(id=1, is_target=True, tags=[5])`.

- [ ] **Step 6: `tests/test_create_service.py`** — lines 93, 127: `metapuzzle=1` → `is_target=True`.

- [ ] **Step 7: `tests/test_board_push_service.py`** — line 205: `metapuzzle=1` → `is_target=True`.

- [ ] **Step 8: `tests/test_edge_web.py`** — line 64: `metapuzzle=1` → `is_target=True`.

- [ ] **Step 9: `tests/test_convert_service.py`** — line 72: `make_puzzle(id=7, metapuzzle=0)` → `make_puzzle(id=7)` (default `is_target=False`). Test names mentioning `metapuzzle` (lines 92, 117) are cosmetic — leave as-is.

- [ ] **Step 10: `tests/test_rollup_source_mode.py`** — lines 56, 71, 95: `make_puzzle(id=1, metapuzzle=1)` → `make_puzzle(id=1, is_target=True)`. Mock/assert lines for the renderers (29, 30, 62, 63, 85, 108) are unchanged.

### 11d. Assertion rewrites

- [ ] **Step 11: `tests/test_solve_equivalence.py`**

Fixtures (lines 399, 408: `metapuzzle=0` → drop kwarg / default; lines 645, 651-652, 659, 666, 674, 686, 694, 706: `metapuzzle=1` → `is_target=True`; line 951 `metapuzzle=0` → drop kwarg).

Assertions:
- line 648: `assert not puz.metapuzzle` → `assert not puz.is_target`
- line 688: `assert not puz.metapuzzle` → `assert not puz.is_target`
- line 953: `assert puz.metapuzzle == 1` → `assert puz.is_target`
- line 954: `assert puz.is_metapuzzle() is True` → `assert puz.is_target is True`

> `test_marks_metapuzzle` (line 951) exercises a promote path changed in Task 5 to set `puzzle.is_target = True`, so the rewritten assertion holds on the in-memory object.

### 11e. `import_dict` dict tests — drop the absent keys

- [ ] **Step 12: `tests/test_minimeta_service.py`** — line 30: the dict literal `"metapuzzle": 0, "metameta": 0, ...`. Remove the `"metapuzzle": 0, "metameta": 0,` portion (those columns no longer exist; `import_dict` ignores them but keep the fixture honest).

### 11f. `tests/test_metameta_backfill_bridge.py`

- [ ] **Step 13: Keep the file, update the fixture**

This file guards a **still-live** behaviour: `refresh` branches on `rollup.source_mode == 'subtree'` (walking the parent branch) rather than the old `metameta` flag, and must NOT call `get_all_from_hunt`. That source-mode branching is unaffected by B2, so the test stays valuable.

- Line 53: `make_puzzle(id=1, metapuzzle=1, metameta=1)` → `make_puzzle(id=1, is_target=True)`.
- Line 61 assertion `gsheet.add_metametapuzzle_data.assert_awaited_once()` is unchanged.

(Do NOT delete this file. Its assertion is about source_mode, not the dropped columns.)

### 11g. Remaining renderer-mock files

- [ ] **Step 14: `tests/test_select_rollup_members.py`** (lines 26-27, 54-55, 86-87, 102-103) and **`tests/test_refresh_minimeta.py`** (lines 26, 57) only mock/assert the `add_metapuzzle_data`/`add_metametapuzzle_data` renderers — **no change needed**. Confirm by grep in Step 15.

- [ ] **Step 15: Verify no test still references the dropped flags as data**

```bash
grep -rn "\.metapuzzle\b\|\.metameta\b\|is_metapuzzle\|metapuzzle=\|metameta=" tests/
```
Expected: only renderer method names (`add_metapuzzle_data`, `add_metametapuzzle_data`) and cosmetic test-name strings remain — no `.metapuzzle`/`.metameta` attribute reads, no `metapuzzle=`/`metameta=` kwargs, no `is_metapuzzle`.

### 11h. Focused derivation test (new — the spec requires it)

`get_is_target` is genuinely new behaviour, so it gets a real live-DB test (same
convention as `tests/test_tag_edges.py` / `test_backfill_consistency.py`: insert
FK-valid rows, assert through the store load path, tear down in a `finally`).

> **TDD note:** this is the natural test-first anchor for Task 4 — an executing
> agent may write it immediately after Task 4 and watch it go red→green, then keep
> it here in the test commit.

- [ ] **Step 16: Create `tests/test_is_target_derivation.py`**

```python
"""B2: PuzzleData.is_target is derived (eager-loaded) from tag_group.target_puzzle_id.

LIVE MySQL (puzzlebot_test), same convention as test_tag_edges.py: insert
FK-valid rows, assert through the store load path, tear down in finally.

A is-target puzzle is, structurally, just "some tag_group targets it" — so the
fed-meta, capstone, and runaround cases from the spec all reduce to the single
positive case here; a bare puzzle and a metaless/converted Group (no target) both
reduce to the negative case.
"""
from bot.store import PuzzleJsonDb, mydb


def _insert_puzzle(hunt_id=10, name="HasTargetTest"):
    cur = mydb.cursor()
    cur.execute("INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)", (hunt_id, name))
    pid = cur.lastrowid
    cur.close()
    mydb.commit()
    return pid


def _insert_tag_group(hunt_id=10, target_puzzle_id=None):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tag_group (hunt_id, target_puzzle_id) VALUES (%s, %s)",
        (hunt_id, target_puzzle_id),
    )
    rid = cur.lastrowid
    cur.close()
    mydb.commit()
    return rid


def _delete_puzzle(pid):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
    cur.close()
    mydb.commit()


def _delete_tag_group(rid):
    cur = mydb.cursor()
    cur.execute("DELETE FROM tag_group WHERE id = %s", (rid,))
    cur.close()
    mydb.commit()


def test_is_target_true_when_a_tag_group_targets_the_puzzle():
    pid = _insert_puzzle(name="DerivedMeta")
    rid = _insert_tag_group(target_puzzle_id=pid)
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=pid)
        assert puzzle.is_target is True
    finally:
        _delete_tag_group(rid)
        _delete_puzzle(pid)


def test_is_target_false_for_bare_puzzle():
    pid = _insert_puzzle(name="BarePuzzle")
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=pid)
        assert puzzle.is_target is False
    finally:
        _delete_puzzle(pid)


def test_is_target_false_for_metaless_group_no_target():
    # A metaless/converted Group has no target_puzzle_id → not is-target.
    pid = _insert_puzzle(name="GroupMember")
    rid = _insert_tag_group(target_puzzle_id=None)
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=pid)
        assert puzzle.is_target is False
    finally:
        _delete_tag_group(rid)
        _delete_puzzle(pid)
```

- [ ] **Step 17: Run the new test**

```bash
timeout 60 pipenv run python -m pytest tests/test_is_target_derivation.py -q > /tmp/test-b2-derive.log 2>&1
tail -10 /tmp/test-b2-derive.log
```

Expected: 3 passed.

---

## Task 12: Verification and commit (commit 2)

- [ ] **Step 1: Full suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-b2-final.log 2>&1
tail -10 /tmp/test-b2-final.log
```

Expected: 0 failures (count may shift only if a test was meaningfully restructured; none deleted here). On any failure invoke `superpowers:systematic-debugging`.

- [ ] **Step 2: Lint**

```bash
pipenv run ruff check
```

Expected: exit 0.

- [ ] **Step 3: Frontend builds**

```bash
cd frontend && npm run build 2>&1 | tail -15; cd ..
```

Expected: clean build (no reference to the removed `is_metameta` field).

- [ ] **Step 4: Acceptance greps**

```bash
# columns gone
pipenv run python -c "
from bot.store import mydb
cur = mydb.cursor()
cur.execute('SHOW COLUMNS FROM puzzles LIKE \"meta%\"')
print('meta* columns (expect empty):', cur.fetchall())
cur.close()
"
# no raw column reads / dead reader / old method survive in bot/
grep -rn "\.metapuzzle\b\|\.metameta\b\|is_metapuzzle\|get_by_attr(metameta" bot/
# Expected: NO output.
# is_target seam present
grep -rn "def get_is_target\|is_target" bot/store/mysqldb.py
```

- [ ] **Step 5: Board ordering — manual acceptance (no async unit harness exists)**

`get_hunt_puzzles` orders by the derived `is_target DESC`. `build_board` operates on
already-fetched rows (`test_build_board.py` covers it via `is_meta`), so the SQL ordering
itself is not unit-tested. Spot-check it against a hunt that has a meta:

```bash
pipenv run python -c "
import asyncio
from bot.web.data import get_hunt_puzzles
rows = asyncio.run(get_hunt_puzzles(<HUNT_ID>, None))
print([(r['name'], r['is_meta']) for r in rows[:5]])
"
```

Expected: targets (`is_meta True`) sort ahead of non-targets. If the project has no
convenient hunt id, confirm via the running dashboard board (metas still float to the top).

- [ ] **Step 6: Commit the behaviour change**

```bash
git add migrations/013_drop_role_flags.sql \
        bot/store/puzzle_data.py bot/store/mysqldb.py \
        bot/core/puzzle_service.py bot/core/notifier.py \
        bot/cogs/puzzles_gsheet.py bot/cogs/puzzles.py \
        bot/web/actions.py bot/web/data.py \
        frontend/src/types.ts \
        tests/
git commit -m "feat(taggroup): derive is-target from tag_group; drop metapuzzle/metameta (B2)"
```

---

## Verification (whole milestone)

- `SHOW COLUMNS FROM puzzles LIKE 'meta%'` → empty (both columns dropped).
- Full suite GREEN: `timeout 180 pipenv run python -m pytest tests/ -q` → 0 failures.
- `pipenv run ruff check` → exit 0.
- Frontend builds clean; META badge still renders for targets (`is_meta` derived).
- `grep -rn "\.metapuzzle\b\|\.metameta\b\|is_metapuzzle\|get_by_attr(metameta" bot/` → no output.
- `grep -rn "RoundJsonDb" bot/ tests/` → no output (rename complete).
- `PuzzleData.is_target` is a transient field; in `SPECIAL_ATTR`; populated by `construct_puzzle`/`get_is_target`.
- Read sites do `puzzle.is_target` (zero DB calls); list endpoints derive via `EXISTS`.

## Rollback

- Revert commit 2 to undo the behaviour change; revert commit 1 to undo the rename.
- To reverse migration 013 against `puzzlebot_test`:
  ```sql
  ALTER TABLE `puzzles` ADD COLUMN `metapuzzle` int DEFAULT 0;
  ALTER TABLE `puzzles` ADD COLUMN `metameta`  int DEFAULT 0;
  UPDATE `puzzles` p SET p.metapuzzle = 1
    WHERE EXISTS (SELECT 1 FROM tag_group mt WHERE mt.target_puzzle_id = p.id);
  -- metameta stays 0 (it was always 0).
  ```
