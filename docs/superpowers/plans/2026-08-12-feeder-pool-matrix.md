# Feeder Pool — Matrix Tab Implementation Plan (Plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each Minimeta with a non-empty feeder pool a `Feeders — <name>` matrix tab — feeders down the side, claim targets across the top, checkbox cells — and pull solver ticks back into claims with `!sync_claims`.

**Architecture:** One dedicated tab per Minimeta, so the bot owns that tab's key column outright. Columns A–C (`key`, `item`, `Len`) are bot-owned and reconciled through the existing `reconcile_table`; column D (`note`) and the tick columns from E rightward are solver-owned and preserved by that function's column-span rule. All row shaping and tick diffing lives in the pure `bot/core/feeder_pool.py`. The tab's `sheetId` is stored in a new `puzzles.feeder_tab_page_id`.

**Tech Stack:** Python 3.12, discord.py 2.7.1, MariaDB, Google Sheets API v4 (`google-api-python-client==2.198.0`), pytest (`asyncio_mode = auto`), ruff (pyflakes), basedpyright.

**Spec:** `docs/superpowers/specs/2026-08-12-feeder-pool-design.md` — authoritative, and **revised** on 2026-08-12. Read its "Sheet surfaces" section before starting; the original two-table design is superseded. Note the correction recorded there: two bot tables *can* share a tab when they occupy different column bands — what ruled the per-mini design out was that Mini tabs have no free band and no promote path to make one.

**Plan 1 (shipped, `97d2d75..d280b93`)** provides everything below the sheet layer: the two tables, the DAOs, six bang commands, confirm-on-solve, and the pure helpers `claim_state`, `rival_names`, `provided_item_ids`, `diff_ticks`. The last two are written and tested but **called by nothing yet** — this plan consumes them.

---

## Read this before writing any Sheets code

Four facts from a code recon, each of which contradicts something a careful person would otherwise assume. Getting any of them wrong corrupts a live hunt spreadsheet, and **the test suite cannot catch it** — every Sheets test in this repo asserts request shape against mocks.

1. **`reconcile_table` reads the entire key column of *the table's own range*** — `key_col = rng.get("startColumnIndex", 0)`, then `{key_letter}:{key_letter}` (`bot/cogs/puzzles_gsheet.py:1200-1210`) — and `diff_rows` prunes any key not in the caller's desired set. Two tables in *different column bands* are therefore fine; two sharing a band blank each other forever. The matrix has one table on its own tab, so this cannot arise — but do not add a second table to that tab.
2. **`select_table` falls back to a lone unnamed table regardless of the name requested** (`bot/core/sheet_tables.py:44`). The matrix tab having exactly one table keeps us clear of it — do not add a second.
3. **`rollup_lock` is a non-reentrant `asyncio.Lock` with no timeout** (`bot/core/locks.py:24`). Calling `refresh_minimeta` while holding the same key wedges the event loop permanently — no error, no timeout, until restart.
4. **`_execute_lock` serialises every Sheets call process-wide** (`bot/utils/gsheets.py:37`), because the shared `httplib2.Http` is not thread-safe. N reads block every other hunt's Sheets traffic, not merely N units of quota. The single-tab design keeps the sync to one read.

Also: **Google rejects Table names containing `-` or apostrophes** (`bot/core/sheet_tables.py:22`). `Feeder Matrix <page_id>` is safe. Tab *names* are unconstrained but must be unique — use `get_unique_tab_name`.

And a stale comment to disregard: `_add_contribution_table` (`:1097`) claims *"reconcile_table locates the Table by scanning the page, never by name"*. **That is false** — it locates strictly by name and refuses when it cannot. `tests/test_contribution_table_requests.py:74` repeats the same stale claim.

---

## File structure

| File | Responsibility |
|---|---|
| `scripts/diagnose_feeder_matrix.py` (create) | One-off live probe. Not imported by anything. |
| `migrations/044_feeder_tab.sql` (create) | `puzzles.feeder_tab_page_id` |
| `bot/store/puzzle_data.py` (modify) | the new field |
| `bot/core/feeder_pool.py` (modify) | **Pure.** `matrix_header`, `matrix_rows`, `feeder_length`, `parse_ticks`. Still zero imports. |
| `bot/core/sheet_tables.py` (modify) | `feeder_matrix_table_name(page_id)` |
| `bot/cogs/puzzles_gsheet.py` (modify) | `ensure_feeder_matrix_tab`, `render_feeder_matrix`, `read_feeder_matrix`, `_values_batch_get` **not** needed |
| `bot/core/puzzle_service.py` (modify) | `refresh_feeder_matrix`, `sync_feeder_claims`, provided-row wiring, confirm-on-solve render |
| `bot/cogs/puzzles.py` (modify) | `!sync_claims` |
| `tests/test_feeder_matrix_pure.py` (create) | the new pure helpers |
| `tests/test_feeder_matrix_requests.py` (create) | request-shape assertions, modelled on `tests/test_contribution_table_requests.py` |
| `tests/test_feeder_matrix_service.py` (create) | service orchestration, gsheet cog mocked |

`bot/core/feeder_pool.py` **must still import nothing.** Verified by `grep -nE "^(from|import) " bot/core/feeder_pool.py` returning empty. If a helper needs an import, it belongs elsewhere — `dedup_key` already lives in `mysqldb.py` for exactly this reason.

---

## Task 1: The live probe — write it, do not run it unprompted

**Files:** Create `scripts/diagnose_feeder_matrix.py`

Nothing in this repo sets a checkbox or any data validation — `grep -rn "dataValidation\|BOOLEAN\|checkbox" bot/ tests/` returns zero hits. So the entire tick mechanism rests on `columnType: "BOOLEAN"`, which is present in the pinned client's discovery doc but **unproven here**.

**This script writes to a real Google spreadsheet. Do not run it without the user's explicit say-so, and ask which spreadsheet is safe to write to.** Model it on the existing `scripts/diagnose_addtable.py`.

- [ ] **Step 1: Read the existing probe**

Read `scripts/diagnose_addtable.py` in full and follow its shape — how it authenticates, how it targets a spreadsheet, how it reports, and how it cleans up after itself.

- [ ] **Step 2: Write the probe**

It must answer exactly these four questions and print an unambiguous verdict for each:

1. Does `addTable` accept `columnProperties` with `columnType: "BOOLEAN"` on a range spanning **only the header row**? (This is how both existing table-creation paths build a table, so it matters.)
2. When the table grows via `updateTable` with `"fields": "range"`, does the BOOLEAN column type apply to the **new** rows?
3. What does a **ticked** box read back as through `values().get`? A string `"TRUE"`, a bool, or something else?
4. What does an **all-unticked** column read back as — blank strings, `"FALSE"`, or is `values` omitted from the response entirely?

Create a scratch tab, probe, print findings, delete the scratch tab in a `finally`.

- [ ] **Step 3: Commit the script unrun**

```bash
git add scripts/diagnose_feeder_matrix.py
git commit -m "chore(probe): script to verify BOOLEAN table columns against live Sheets"
```

- [x] **Step 4: RUN — done 2026-08-12. Findings below; Tasks 4–8 are unblocked.**

Run against a scratch spreadsheet (`1B3aizPgUdd6d6mO-tpuob0SajWOgP0UHBrrOmhO_rUg`, empty,
supplied for this purpose). Raw output is in the commit that added this section.

| Q | Result |
|---|---|
| **Q1** `addTable` accepts `columnProperties` with `columnType: "BOOLEAN"` on a header-only range | **PASS** — table created, both tick columns typed |
| **Q2** the type survives `updateTable {"fields": "range"}` as the table grows | **PASS** — BOOLEAN still on indices 4 and 5 afterwards |
| **Q3** a **ticked** cell reads back through `values().get` as | the **string** `'TRUE'` |
| **Q4** an **all-unticked** BOOLEAN column reads back as | the **string** `'FALSE'` — *not* blank, and `values` **is** present |

**Q4 contradicted the assumption in this plan.** It predicted blanks, or `values` omitted
entirely. Real behaviour is an explicit `'FALSE'` per cell. `parse_ticks` treats anything
that is not `'TRUE'` as unticked, so it is correct either way — but that was luck, not
design, so the pure tests now pin `'FALSE'` explicitly rather than only the blank case.

Two consequences worth carrying:

- **Rows are right-trimmed far less than expected.** Because every tick cell holds an
  explicit `'FALSE'`, a data row comes back full-width, and even an empty `note` in the
  middle is preserved (`['f:13', 'MARZIPAN', '8', '', 'FALSE', 'FALSE']`). The
  `idx < len(row)` guard in `parse_ticks` stays regardless — a row with no ticks at all
  after its note could still trim.
- **A cell written as `""` into a BOOLEAN column renders as an unticked checkbox and reads
  back `'FALSE'`.** So `reconcile_table` appending a row without touching the tick columns
  cannot corrupt them. Still unprobed: what a *never-written* cell in a BOOLEAN column reads
  as. `parse_ticks` handles `''` and `'FALSE'` identically, so either answer is safe.

One cosmetic note: the API omits `columnIndex` for index 0 in the `columnProperties` it
returns (`{'columnName': 'key'}`). Do not treat its absence as a missing column.

---

## Task 2: Migration 044 — where the tab id lives

**Files:** Create `migrations/044_feeder_tab.sql`; modify `bot/store/puzzle_data.py`

The Minimeta's `google_page_id` names its own puzzle tab, and `rollups` spends its one `tab_page_id` on the mini-list, so the matrix tab needs its own home.

- [ ] **Step 1: Check the number is free**

```bash
ls migrations/ | tail -3
for w in .claude/worktrees/*/; do echo "$w: $(ls $w/migrations/ 2>/dev/null | tail -1)"; done
```
Nothing named `044_*`. Parallel worktrees can claim a number and git merges the collision cleanly without noticing.

- [ ] **Step 2: Write it**

```sql
-- Feeder matrix: where a minimeta's feeder tab lives.
--
-- A minimeta with a non-empty feeder pool gets its own "Feeders — <name>" tab, a
-- matrix of feeders against claim targets. puzzles.google_page_id already names the
-- puzzle's OWN tab and rollups.tab_page_id is spent on the mini-list rollup, so the
-- matrix tab needs its own column.
--
-- NULL means "no matrix tab yet" — the overwhelming majority of puzzles, forever.
--
-- Down: ALTER TABLE puzzles DROP COLUMN feeder_tab_page_id;

ALTER TABLE `puzzles` ADD COLUMN `feeder_tab_page_id` int(11) DEFAULT NULL;
```

No FK: a Sheets `sheetId` is not a row in this database.

- [ ] **Step 3: Add the dataclass field**

Add `feeder_tab_page_id: Optional[int] = None` to `PuzzleData` in `bot/store/puzzle_data.py`, matching the surrounding style. Per `AGENTS.md`, the generic `commit()` serialises every `__dict__` field by name, so **no DAO change is needed** — but the migration must ship first or every puzzle write crashes.

- [ ] **Step 4: Apply and verify**

```bash
timeout 120 pipenv run python migrate.py migrate > /tmp/migrate.log 2>&1
timeout 60 pipenv run python -c "
from bot.store import mydb
c = mydb.dict_cursor()
c.execute('SHOW COLUMNS FROM puzzles LIKE \"feeder_tab_page_id\"')
print(c.fetchall()); c.close()"
```

- [ ] **Step 5: Full suite, then commit**

Adding a dataclass field touches every puzzle round-trip, so run everything:

```bash
timeout 400 pipenv run python -m pytest tests/ -q > /tmp/suite.log 2>&1
```
Expected: 1981 passed, unchanged. **If anything fails, report it — do not edit the failing test.**

```bash
git add migrations/044_feeder_tab.sql bot/store/puzzle_data.py
git commit -m "feat(db): record a minimeta's feeder matrix tab"
```

---

## Task 3: Pure — matrix shaping and tick parsing

**Files:** Modify `bot/core/feeder_pool.py`; create `tests/test_feeder_matrix_pure.py`

All four functions are pure derivation, fully testable, and are the only part of this plan the suite can genuinely gate. Follow TDD: test first, watch it fail, implement, watch it pass.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the matrix shaping helpers. No DB, no Discord, no Sheets."""
from dataclasses import dataclass

from bot.core.feeder_pool import feeder_length, matrix_header, matrix_rows, parse_ticks


@dataclass
class Item:
    id: int
    text: str
    note: str = ""


@dataclass
class Target:
    id: int
    name: str


@dataclass
class Claim:
    item_id: int
    target_id: int
    confirmed: bool = False


MINIMETA = Target(100, "Matching Meta")
MINI_1, MINI_2 = Target(101, "Mini 1"), Target(102, "Mini 2")
ALBATROSS = Item(12, "ALBATROSS", "fits SEA")
MARZIPAN = Item(13, "MARZIPAN")


def test_feeder_length_excludes_spaces_but_keeps_punctuation():
    """Matches bot/core/unassigned.py's rule, which the Unassigned table already uses."""
    assert feeder_length("SEVENTH HEAVEN") == 13
    assert feeder_length("O'CLOCK") == 7
    assert feeder_length("") == ""


def test_matrix_header_puts_main_first_then_minis_in_order():
    header = matrix_header(MINIMETA, [MINI_1, MINI_2])
    assert header == ["key", "item", "Len", "note", "main", "Mini 1", "Mini 2"]


def test_matrix_header_with_no_minis_still_has_main():
    assert matrix_header(MINIMETA, []) == ["key", "item", "Len", "note", "main"]


def test_matrix_rows_are_bot_owned_columns_only():
    """Only key/item/Len — note and the tick cells are the solver's and must never
    appear in the desired rows, or reconcile_table would overwrite them."""
    rows = matrix_rows([ALBATROSS, MARZIPAN])
    assert rows == [["f:12", "ALBATROSS", 9], ["f:13", "MARZIPAN", 8]]


def test_matrix_rows_preserve_pool_order():
    rows = matrix_rows([MARZIPAN, ALBATROSS])
    assert [r[0] for r in rows] == ["f:13", "f:12"]


def test_parse_ticks_maps_each_column_to_its_target():
    """Two targets and two items, arranged so a transposed column or row fails."""
    header = ["key", "item", "Len", "note", "main", "Mini 1", "Mini 2"]
    grid = [
        ["f:12", "ALBATROSS", "9", "fits SEA", "", "TRUE", ""],
        ["f:13", "MARZIPAN", "8", "", "TRUE", "", "TRUE"],
    ]
    ticks = parse_ticks(header, grid, {"main": 100, "Mini 1": 101, "Mini 2": 102})
    assert ticks == {100: [13], 101: [12], 102: [13]}


def test_parse_ticks_treats_only_TRUE_as_ticked():
    """values().get returns FORMATTED_VALUE strings, so a tick is the string 'TRUE'."""
    header = ["key", "item", "Len", "note", "main"]
    grid = [["f:12", "A", "1", "", "FALSE"], ["f:13", "B", "1", "", ""]]
    assert parse_ticks(header, grid, {"main": 100}) == {100: []}


def test_parse_ticks_tolerates_short_rows():
    """A row is right-trimmed by the API when its trailing cells are empty, so a row
    can be shorter than the header."""
    header = ["key", "item", "Len", "note", "main", "Mini 1"]
    grid = [["f:12", "A", "1"]]
    assert parse_ticks(header, grid, {"main": 100, "Mini 1": 101}) == {100: [], 101: []}


def test_parse_ticks_ignores_a_column_with_no_known_target():
    """A solver may add their own column; it must not be read as a claim."""
    header = ["key", "item", "Len", "note", "main", "scratch"]
    grid = [["f:12", "A", "1", "", "TRUE", "TRUE"]]
    assert parse_ticks(header, grid, {"main": 100}) == {100: [12]}


def test_parse_ticks_skips_rows_with_an_unparsable_key():
    """Blanked prune rows survive on the sheet; they must not become claims."""
    header = ["key", "item", "Len", "note", "main"]
    grid = [["", "", "", "", "TRUE"], ["f:13", "B", "1", "", "TRUE"]]
    assert parse_ticks(header, grid, {"main": 100}) == {100: [13]}
```

- [ ] **Step 2: Run, confirm it fails**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_matrix_pure.py -q > /tmp/test.log 2>&1
```
Expected: `ImportError: cannot import name 'feeder_length'`.

- [ ] **Step 3: Implement**

Append to `bot/core/feeder_pool.py`. **No imports** — the module's purity is a design requirement stated in its docstring.

```python
KEY_PREFIX = "f:"
BOT_COLUMNS = ["key", "item", "Len"]
NOTE_COLUMN = "note"
MAIN_COLUMN = "main"


def feeder_length(text):
    """Letter count excluding spaces, punctuation kept — '' for empty text.

    Deliberately the same rule as bot/core/unassigned.py's unassigned_row, because
    both columns answer the same solver question and disagreeing would be worse than
    either choice.
    """
    if not text:
        return ""
    return len(text.replace(" ", ""))


def matrix_header(minimeta, minis):
    """The matrix tab's header row: bot columns, note, then one column per target.

    `main` is the Minimeta's own tick column — how "the main meta uses this feeder
    directly" is expressed. Minis follow in the order given (creation order), so a new
    mini appends at the far right and nothing already on the sheet moves.
    """
    return BOT_COLUMNS + [NOTE_COLUMN, MAIN_COLUMN] + [m.name for m in minis]


def matrix_rows(items):
    """Desired rows for reconcile_table — the BOT-OWNED columns only.

    note and the tick cells are the solver's. Including them here would hand them to
    reconcile_table's write range and overwrite a solver's work on every render.
    """
    return [[f"{KEY_PREFIX}{i.id}", i.text, feeder_length(i.text)] for i in items]


def parse_ticks(header, grid, target_by_column):
    """{target_id: [item_id, ...]} read off the matrix.

    `header` is the sheet's header row, `grid` the data rows beneath it, and
    `target_by_column` maps a header label to a claim target id — a column whose label
    is absent is a solver's own and is ignored.

    Values arrive as FORMATTED_VALUE strings, so a ticked box is the string 'TRUE';
    the API also right-trims rows, so a row can be shorter than the header. Rows whose
    key does not parse are skipped: reconcile_table blanks pruned rows rather than
    deleting them, so a blanked row outlives its feeder.
    """
    columns = [
        (idx, target_by_column[label])
        for idx, label in enumerate(header)
        if label in target_by_column
    ]
    ticks = {target_id: [] for _, target_id in columns}
    for row in grid:
        if not row or not str(row[0]).startswith(KEY_PREFIX):
            continue
        try:
            item_id = int(str(row[0])[len(KEY_PREFIX):])
        except ValueError:
            continue
        for idx, target_id in columns:
            cell = row[idx] if idx < len(row) else ""
            if str(cell).strip().upper() == "TRUE":
                ticks[target_id].append(item_id)
    return ticks
```

- [ ] **Step 4: Run, confirm 10 passed**

- [ ] **Step 5: Mutation checks (REQUIRED)**

Each must fail **exactly** the named test. Restore by editing the line back, never `git checkout --`.

1. In `parse_ticks`, drop the `if idx < len(row)` guard → `test_parse_ticks_tolerates_short_rows` fails with `IndexError`.
2. In `matrix_rows`, append `i.note` to the row → `test_matrix_rows_are_bot_owned_columns_only` fails. This one guards a solver's work, so prove it.
3. In `matrix_header`, put minis before `main` → `test_matrix_header_puts_main_first_then_minis_in_order` fails.

- [ ] **Step 6: Verify purity, then commit**

```bash
grep -nE "^(from|import) " bot/core/feeder_pool.py
```
Must return **nothing**.

```bash
git add bot/core/feeder_pool.py tests/test_feeder_matrix_pure.py
git commit -m "feat(core): shape the feeder matrix and parse its ticks"
```

---

## Task 4: Creating the matrix tab

**BLOCKED on Task 1's probe findings.** Do not start until they are known.

**Files:** Modify `bot/core/sheet_tables.py`, `bot/cogs/puzzles_gsheet.py`; create `tests/test_feeder_matrix_requests.py`

- [ ] **Step 1: The table name helper**

In `bot/core/sheet_tables.py`, beside `contribution_table_name`:

```python
def feeder_matrix_table_name(page_id):
    """The name of a feeder matrix tab's Table.

    Page-derived for the same reason as contribution_table_name: Table names must be
    unique across the whole spreadsheet, and Google rejects '-' and apostrophes.
    """
    return f"Feeder Matrix {page_id}"
```

- [ ] **Step 2: `ensure_feeder_matrix_tab`**

In `bot/cogs/puzzles_gsheet.py`. **Model it on `ensure_unassigned_table` (`:926`), not on `_add_contribution_table`** — it is the only existing path that passes `columnProperties`, which is where the BOOLEAN column type goes.

Behaviour:
- Idempotent. If `minimeta.feeder_tab_page_id` is set and that tab still exists, return it.
- Otherwise create a tab named via `get_unique_tab_name(f"Feeders — {minimeta.name}")`, write the header row, and `addTable` spanning **all** columns (bot + note + every tick column) with `columnProperties` giving each tick column `columnType: "BOOLEAN"`.
- Return the new `sheetId` for the caller to persist.

Creating the table spanning only the header row is what both existing paths do; `reconcile_table`'s `updateTable` (`"fields": "range"`) grows it and never touches `columnProperties`, so the column types survive.

**Wrap creation in try/except and defer to lazy self-heal**, as `generate_puzzle_tab` does for `_add_contribution_table` — transient 500s on table creation are documented as normal here and must not abort a user-facing operation.

- [ ] **Step 3: Tests — request shape only, and say so**

Create `tests/test_feeder_matrix_requests.py`, modelled on `tests/test_contribution_table_requests.py` (read its `_capture_batch` / `_capture_lowlevel` helpers and reuse the pattern). Assert:
- the `addTable` request's range spans every column, not just the bot-owned ones;
- every tick column carries `columnType: "BOOLEAN"` at the right index;
- `main` is the first tick column;
- a second call with `feeder_tab_page_id` already set issues **no** requests.

Open the file with a docstring stating plainly that these assert **request shape against mocks** and verify nothing about what Google does — mirroring the honesty of `test_contribution_table_requests.py`'s own header.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(sheets): create a minimeta's feeder matrix tab"
```

---

## Task 5: Rendering the matrix

**BLOCKED on Task 1.** **Files:** `bot/cogs/puzzles_gsheet.py`, `bot/core/puzzle_service.py`

- [ ] **Step 1: `render_feeder_matrix(minimeta, items, minis)`**

```python
await self.reconcile_table(
    page_id=minimeta.feeder_tab_page_id,
    desired_rows=matrix_rows(items),
    data_col_count=len(BOT_COLUMNS),      # 3 — note and ticks are the solver's
    numeric_cols=(2,),                    # Len, so the column sorts numerically
    grey_below=True,
    table_name=feeder_matrix_table_name(minimeta.feeder_tab_page_id),
)
```

`numeric_cols` is required, not cosmetic: a number-typed Table column rejects a string, and `feeder_length("")` returns `""` — `reconcile_table` writes a blank cell for a non-numeric value in a numeric column, which is exactly the case it was built for (`:1222`).

Also rewrite the header row when the mini set has changed, and widen the Table's range so new tick columns fall inside it.

- [ ] **Step 2: `refresh_feeder_matrix(hunt, minimeta)` on the service**

Model on `refresh_minimeta` (`bot/core/puzzle_service.py:721`): guard, resolve the gsheet cog, ensure the tab, render, then badge via `_mark_rollup` on the Minimeta's **existing** rollup record — `rollups` has `UNIQUE(target_puzzle_id)`, so there is exactly one badge per puzzle and it is shared with the mini-list.

**Take `rollup_lock(("minimeta", minimeta.id))` XOR call `refresh_minimeta` — never both.** Re-entry on a non-reentrant lock with no timeout wedges the bot permanently.

- [ ] **Step 3: Confirmed ticks are written by the bot**

Confirmed claims are bot-owned: the render must write `TRUE` into a confirmed claim's cell, so that unticking it is undone on the next render. This is the only case where the bot writes into a solver-owned column, and it is deliberate — `diff_ticks` already refuses to drop a confirmed claim on the read side.

- [ ] **Step 4: Tests + commit**

Service test with the gsheet cog mocked: renders with the right arguments, badges on failure, re-raises. Then:

```bash
git commit -m "feat(sheets): render the feeder matrix"
```

---

## Task 6: `!sync_claims`

**BLOCKED on Task 1** — steps 3 and 4 depend on the read-back format.

**Files:** `bot/cogs/puzzles_gsheet.py`, `bot/core/puzzle_service.py`, `bot/cogs/puzzles.py`, `docs/commands.md`, `tests/test_command_gating.py`

**The name is `!sync_claims`.** `!sync_feeders` is already an alias of `!sync` (`bot/cogs/puzzles.py:2017`) and discord.py raises `CommandRegistrationError` at cog load on a duplicate — reusing it fails the bot at startup rather than shadowing.

- [ ] **Step 1: `read_feeder_matrix(page_id)`**

One `_values_get` over the whole tab. Return `(header, grid)`. Handle the API omitting `values` entirely on an empty range — every existing caller writes `resp.get("values") or []`.

- [ ] **Step 2: `sync_feeder_claims(hunt, minimeta)`**

1. Read the tab once.
2. `parse_ticks(header, grid, known_target_ids)` — pass the Minimeta's id plus its Minis' ids. Columns are identified by the id in their header label, never by name, so a rename cannot silently drop a column's ticks.
3. Per target, `diff_ticks(pool_item_ids, current_item_ids, ticked_item_ids, confirmed_item_ids)` and apply. **Route through `diff_ticks`** — it already encodes ignore-stale-ticks and never-drop-a-confirmed-claim, and re-deriving either is how they drift.
4. Adopt changed notes via `FeederPoolDb.set_note` (built in Plan 1, currently unused).
5. Re-render the matrix and the contribution table.
6. Return a report: claims added, dropped, newly contested, still homeless.

- [ ] **Step 3: The command**

Ungated (it is a refresh), following the `!feeders` shape and using `_feeder_context`. Add to `docs/commands.md` and to `tests/test_command_gating.py`'s map — that file asserts the whole map and will flag the omission.

- [ ] **Step 4: Tests**

Service-level with the gsheet cog mocked, and at minimum: a tick creates a claim; an untick removes an unconfirmed one; an untick does **not** remove a confirmed one; a tick on an absent feeder is ignored. Use two targets and two items throughout.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(commands): pull matrix ticks back into claims with !sync_claims"
```

---

## Task 7: Provided rows

**Files:** `bot/cogs/puzzles_gsheet.py`, `bot/core/puzzle_service.py`, `tests/test_refresh_minimeta.py`

- [ ] **Step 1: Change the signature**

`render_minimeta_contribution(minimeta, members)` (`bot/cogs/puzzles_gsheet.py:1518`) takes no claims. Adding provided rows changes it, and **two call sites pin the current shape**: `bot/core/puzzle_service.py:741` and `tests/test_refresh_minimeta.py`'s `assert_awaited_once_with(minimeta, minis)`.

Changing that test is legitimate — the contract genuinely changed — but per the house rule, **state the reason in the commit message and confirm the assertion did not get weaker**: it must still pin the arguments exactly, not become a wildcard.

- [ ] **Step 2: Append provided rows**

For each id from `provided_item_ids(minimeta.id, claims)`:

```python
[f"provided:{item.id}", "—", "Provided", item.text]
```

`provided:` is a fresh key namespace, clear of `feeds:` / `none:` / `informs:` / `informs:free:`. The `—` in the Source column follows the existing sourceless-evidence precedent (`bot/core/contribution.py:66`).

- [ ] **Step 3: Tests**

A feeder claimed to the Minimeta appears; one superseded by a confirmed claim elsewhere does not; the whole pool never appears. Two items, one superseded and one not.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(sheets): render provided rows on the minimeta's contribution table"
```

---

## Task 8: Wire the lifecycle

**Files:** `bot/core/puzzle_service.py`

The matrix must re-render when its content changes. Hook `refresh_feeder_matrix` into: `add_feeder_items`, `edit_feeder_item`, `remove_feeder_item`, `claim_feeder`, `unclaim_feeder`, `create_mini`, `delete_mini`, and `confirm_feeder_claims`.

- [ ] **Step 1: Best-effort, never fatal**

Every render is best-effort and badged, matching `create_mini`'s treatment of `refresh_minimeta` (`bot/core/puzzle_service.py:2552`). **A Sheets hiccup must never fail a solve.**

- [ ] **Step 2: Skip an empty pool**

No pool, no tab, no render. A Minimeta that never uses this feature must behave exactly as it does today — that is acceptance criterion 1 and it is already green.

- [ ] **Step 3: Full suite, lint, types, commit**

```bash
timeout 400 pipenv run python -m pytest tests/ -q > /tmp/suite.log 2>&1
timeout 60 pipenv run ruff check > /tmp/ruff.log 2>&1
timeout 240 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1
```

```bash
git commit -m "feat(service): keep the feeder matrix in step with the pool"
```

---

## Verification

- [ ] Full suite green; **no pre-existing test edited to make it pass**, with the single documented exception of `test_refresh_minimeta.py`'s signature assertion in Task 7.
- [ ] `ruff check` clean; `basedpyright bot/` **0 errors**.
- [ ] `grep -nE "^(from|import) " bot/core/feeder_pool.py` returns nothing.
- [ ] All three Task 3 mutation checks went red on exactly the named test, and were restored.
- [ ] A Minimeta with no pool renders nothing new and behaves exactly as before.
- [ ] **The live probe has been run and its findings recorded**, and Tasks 4–7 match what it found rather than what this plan assumed.
- [ ] **A manual pass on a real hunt sheet**: create a pool, tick cells, sync, solve a mini, unsolve it. The suite cannot verify any of this.

## What this plan cannot prove

Every Sheets test here asserts **request shape against mocks**. That is real coverage of column indices, ranges and ordering — and no coverage at all of what Google does with those requests. Specifically unverified until the probe and the manual pass: whether the checkbox renders, what a tick reads back as, whether `addTable` accepts `columnType` on a header-only range, and how the tab behaves once a solver sorts or filters it.

Do not report this plan as done on a green suite alone. Say which parts are gate-verified and which are inspection-only.

## Rollback / recovery notes

- Each task commits separately; `git revert` of one commit is the unit of undo.
- Migration 044 reverts with `ALTER TABLE puzzles DROP COLUMN feeder_tab_page_id;`.
- The matrix tab is additive: deleting the tab and nulling `feeder_tab_page_id` returns a Minimeta to Plan 1 behaviour, with the pool and every claim intact in the database.
- Nothing in this plan changes the claim/confirm rules — those shipped in Plan 1 and are covered by tests that do not touch Sheets.
