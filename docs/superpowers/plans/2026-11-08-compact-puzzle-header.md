# Compact Puzzle Header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-hunt `header_style` setting (`full` / `compact`, new hunts compact) whose value is copied onto each new puzzle as `header_layout`. Compact puzzles get a three-cell header (A1 linked title, A2 status, A3 feeds) instead of today's labelled A–Q band, and every header write site follows the puzzle's own flag.

**Architecture:** First a structural commit: one per-layout `HeaderGeometry` replaces the write sites' direct use of `config.puzzle_cell_*` / `FEEDS_LINKS_A1` / `FROZEN_ROWS`. `GoogleSheets._header_geometry(puzzle, page_id)` picks the geometry from `puzzle.header_layout`, and the full geometry reproduces today's requests exactly. Compact behaviour then plugs into that seam one operation at a time. Compact promote/demote and compact target creation each run as a single atomic batchUpdate, so "the bot's own contribution Table is on the tab" means "the header is in column B". That lets the flag plus Table presence replace the `"Puzzle Title:"` probe.

**Tech Stack:** Python 3.12, discord.py 2.7, Quart, MySQL/MariaDB (`migrate.py`), Google Sheets v4 batchUpdate, pytest + unittest.mock, React/Mantine + vitest.

**Spec:** `docs/superpowers/specs/2026-11-08-compact-puzzle-header-design.md` (authoritative). Write-site inventory: `.ai/active/header-inventory.md` (coverage map at the end of this plan).

## Global Constraints

- Work only in `/home/russ/projects/puzzbot/.claude/worktrees/compact-header` (branch `compact-header`). Commit at the end of every task. Do not push.
- **Full puzzles keep today's behaviour exactly.** For a puzzle whose `header_layout` is not `"compact"`, every batchUpdate body must be identical to today's, with the same requests in the same order. Existing tests pass unchanged. The only exception is the guard test named in Task 8, whose amendment is stated there.
- Existing tests patch `bot.cogs.puzzles_gsheet.config` and the cog methods `update_cell`, `_solution_bg_request`, `_header_shift`, `change_tab_colour`. So the full path must keep reading the module-global `config` **at call time** (never cache it at import) and keep calling those `self.` methods.
- Values: `"full"`, `"compact"`. Constants live in `bot/core/constants.py`: `HEADER_FULL = "full"`, `HEADER_COMPACT = "compact"`, `HEADER_STYLES = (HEADER_FULL, HEADER_COMPACT)`, `DEFAULT_HEADER_STYLE = HEADER_COMPACT`. Readable labels are `"Full header"` / `"Compact header"`.
- Compact copy, verbatim: A2 `Unsolved`; A3 prefix `Feeds: ` (with a trailing space), separator `, `, empty text `No links`. A1 holds the puzzle name only. The answer in A2 has no prefix.
- Compact formatting: `HEADER_GREY` on the three cells only, `HEADER_BORDER` on all edges of those cells only, A2 bold only when solved (`SOLUTION_GREEN` background), whole-tab font `JetBrains Mono`, header column width `LABEL_COL_WIDTH` (220). No frozen rows, no labels, no merges.
- Meta/minimeta main tabs put the compact cells in column B via the existing `_header_shift` / `TARGET_HEADER_SHIFT`. Additional sheets are always column A.
- The layout is read from `puzzle.header_layout`, never inferred from sheet contents. `_tab_layout` stays the full-layout probe only.
- Migration number is **060** (058 is reserved by `drop-dead-columns`). Use plain `ADD COLUMN` because CI runs MySQL 8.0 and rejects MariaDB's `IF NOT EXISTS`.
- Test runs: `timeout 600 pipenv run python -m pytest <files> -q > /tmp/x.log 2>&1`, one statement, then read `/tmp/x.log`. `-n` is not available.
- Before the final commit: `pipenv run basedpyright bot/` reports 0 errors and `pipenv run ruff check` is clean. Do not blind-fix F401.
- Command help: when a command's arguments change, update its docstring and its `docs/commands.md` row in the same task.

## Review Focus

1. **A compact tab promoted twice** (render after convert, or a render retried after a failure) must not insert columns a second time. Expected: a tab that already has its own Table gets no batch at all. Test: Task 7 `test_compact_promote_is_a_no_op_when_the_table_exists`.
2. **A mixed hunt**: one refresh batch on one spreadsheet holds an old full feeder and a new compact feeder. Expected: the full tab gets today's row-3 re-stamp, and the compact tab gets only its A3 cell (no unmerge or label at H3). Test: Task 6 `test_mixed_layouts_in_one_spreadsheet_batch`.
3. **Answers and names that look like formulas or contain non-BMP characters** (`=A1`, `"quoted"`, emoji). Expected: A1/A2/A3 are written as `stringValue` and the rich-text run indices count UTF-16 units. Tests: Task 3 `test_compact_feeds_runs_count_utf16_units`, Task 5 `test_compact_solve_writes_a_formula_looking_answer_as_text`.
4. **Bad setting values**: `!update_settings header_style Compact-ish`, PATCH `{"header_style": "Compact"}`, `!update_puzzle_settings header_layout compact`. Expected: each is refused with a reason and nothing is stored. Tests: Task 8 `test_update_settings_rejects_an_unknown_header_style`, `test_settings_header_style_must_be_a_known_value`, `test_update_puzzle_settings_refuses_header_layout`.
5. **A compact meta whose create batch fails** (transient 500 past the retries). Expected: no half-made meta tab with its header in B and no Table. The failure propagates and the caller's existing cleanup deletes the tab. Test: Task 3 `test_compact_target_create_failure_propagates_with_nothing_deferred`.

---

## File structure

| File | Responsibility | Tasks |
|---|---|---|
| `migrations/060_header_style.sql` | new columns + backfill | 1 |
| `bot/core/constants.py` | header style constants | 1 |
| `bot/store/hunt_data.py`, `bot/store/puzzle_data.py` | `header_style` / `header_layout` fields | 1 |
| `bot/store/mysqldb.py` | `SPECIAL_ATTR` + `set_header_style` | 1 |
| `bot/cogs/sheet_layout.py` | `HeaderGeometry` + compact geometries | 2, 3 |
| `bot/cogs/puzzles_gsheet.py` | geometry seam + every write site | 2–7 |
| `bot/core/puzzle_service.py` | flag at create, `refresh_header_links` layouts, `create_hunt(header_style)` | 4, 6, 8 |
| `bot/cogs/puzzles.py` | `!hunt , full`, `!update_settings header_style`, refuse `header_layout` | 8 |
| `bot/web/actions.py`, `bot/web/data.py`, `bot/web/api.py` | create + PATCH + payload | 8 |
| `frontend/src/{types.ts,api.ts,pages/HuntList.tsx,pages/HuntBoard.tsx,components/AdminSettingsPanel.tsx}` | create choice + settings panel | 9 |
| `docs/commands.md`, `docs/user-guide.md` | docs | 10 |
| tests: `test_header_style_store.py` (live DB), `test_header_geometry.py`, `test_compact_header.py`, `test_header_style_surfaces.py`, plus additions to `test_sheet_call_volume.py` | | 1–8 |

---

### Task 1: Migration 060, constants, data fields, store setter

**Files:**
- Create: `migrations/060_header_style.sql`
- Modify: `bot/core/constants.py`, `bot/store/hunt_data.py` (after `voice_per_puzzle`), `bot/store/puzzle_data.py` (after `google_sheet_id`), `bot/store/mysqldb.py` (`MySQLHuntJsonDb.SPECIAL_ATTR` ~L232, new setter beside `set_voice_per_puzzle` ~L393; extend the comment at ~L1005)
- Modify: `AGENTS.md` (add `test_header_style_store.py` to the Live-DB tests list)
- Test: `tests/test_header_style_store.py` (live `puzzlebot_test`; mirror `tests/test_voice_store.py`'s `_exec`/`_guild`/`_hunt`/`_cleanup` helpers, with a distinct `GUILD_SNOW`)

**Interfaces:**
- Produces: `HEADER_FULL`, `HEADER_COMPACT`, `HEADER_STYLES`, `DEFAULT_HEADER_STYLE`, `HEADER_STYLE_LABELS = {"full": "Full header", "compact": "Compact header"}` in `bot.core.constants`.
- Produces: `HuntData.header_style: str = "full"`. The dataclass default is full so that an in-memory HuntData (tests, `make_hunt()`) creates full puzzles. New hunts get `DEFAULT_HEADER_STYLE` from `create_hunt` (Task 8), and the column default is the backstop.
- Produces: `PuzzleData.header_layout: str = "full"`. This field is committed by the generic `commit()` (not SPECIAL), because it is written once, at insert.
- Produces: `MySQLHuntJsonDb.set_header_style(hunt_id: int, style: str) -> None`, which raises `ValueError` for a style not in `HEADER_STYLES`. `'header_style'` is added to `MySQLHuntJsonDb.SPECIAL_ATTR`, so a stale HuntData commit cannot revert it (the voice_per_puzzle precedent).

- [ ] **Step 1: Write the migration.** Use 051's header format. The header must say: PRE-FLIGHT `SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND ((TABLE_NAME='hunts' AND COLUMN_NAME='header_style') OR (TABLE_NAME='puzzles' AND COLUMN_NAME='header_layout'))` must return 0. POST-CHECK: `SELECT header_style, COUNT(*) FROM hunts GROUP BY header_style` returns only `full`; `SHOW COLUMNS FROM hunts LIKE 'header_style'` shows Default `compact`; `SELECT header_layout, COUNT(*) FROM puzzles GROUP BY header_layout` returns only `full`. Run the pre-flight in every environment before pushing, because a defaulted column does not prove itself. Body:

```sql
ALTER TABLE `hunts` ADD COLUMN `header_style` VARCHAR(10) NOT NULL DEFAULT 'full';
ALTER TABLE `hunts` ALTER COLUMN `header_style` SET DEFAULT 'compact';
ALTER TABLE `puzzles` ADD COLUMN `header_layout` VARCHAR(10) NOT NULL DEFAULT 'full';
```

- [ ] **Step 2: Apply it to the test DB.** Run `pipenv run python migrate.py migrate`, then `pipenv run python migrate.py status`. Expected: 060 is applied. Run the POST-CHECK queries against `puzzlebot_test` and record their output in the commit body.

- [ ] **Step 3: Write the failing live-DB tests.**

```python
def test_a_new_hunt_row_defaults_to_compact():
    hid = _hunt(guild, "hs1")              # INSERT without header_style
    assert HuntJsonDb.get_by_attr(id=hid).header_style == "compact"

def test_set_header_style_round_trips_and_rejects_unknown():
    HuntJsonDb.set_header_style(hid, "full")
    assert HuntJsonDb.get_by_attr(id=hid).header_style == "full"
    with pytest.raises(ValueError):
        HuntJsonDb.set_header_style(hid, "Compact")
    assert HuntJsonDb.get_by_attr(id=hid).header_style == "full"

def test_a_stale_hunt_commit_never_reverts_header_style():
    stale = HuntJsonDb.get_by_attr(id=hid)          # reads "compact"
    HuntJsonDb.set_header_style(hid, "full")
    HuntJsonDb.commit(stale)
    assert HuntJsonDb.get_by_attr(id=hid).header_style == "full"

def test_a_puzzle_row_defaults_full_and_commit_stores_compact():
    pid = _puzzle(hid, "raw")              # raw INSERT, no header_layout
    assert PuzzleJsonDb.get_by_attr(id=pid).header_layout == "full"
    p = PuzzleData(name="made", hunt_id=hid); p.header_layout = "compact"
    PuzzleJsonDb.commit(p)
    assert PuzzleJsonDb.get_by_attr(id=p.id).header_layout == "compact"
```

Wrap each test in `try/finally: _cleanup(guild)`, as in `test_voice_store.py`.

- [ ] **Step 4: Run them and confirm they fail.** Run `timeout 600 pipenv run python -m pytest tests/test_header_style_store.py -q > /tmp/x.log 2>&1`. Expected: FAIL (`AttributeError: header_style` / no `set_header_style`).
- [ ] **Step 5: Implement.** Add the constants, the two fields (each with a one-line comment naming migration 060), the SPECIAL_ATTR entry (update its comment so it names `set_header_style`), and `set_header_style` (its own `UPDATE`, validating against `HEADER_STYLES`).
- [ ] **Step 6: Run the new file and then the whole suite.** Run `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/x.log 2>&1`. Expected: all pass, except possibly `test_update_settings_voice.py::test_every_field_the_generic_commit_skips_is_refused_with_a_pointer`, which goes red because `header_style` is now SPECIAL. If it is red, it is fixed in Task 8. Mark it `xfail(strict=True, reason="header_style branch lands in Task 8")`. Do not edit its assertion here.
- [ ] **Step 7: Commit.** Message: `feat(header): migration 060 header_style/header_layout + store setter`.

---

### Task 2: Geometry seam (pure refactor, no behaviour change)

This is the structural step. **No test file may change in this task**, except the one new pin file. The whole existing suite must stay green without edits, which is the evidence that the full geometry reproduces today's requests.

**Files:**
- Modify: `bot/cogs/sheet_layout.py` (add the dataclass; module docstring gains a line saying the full layout is described by `HeaderGeometry`)
- Modify: `bot/cogs/puzzles_gsheet.py`: `_tab_format_requests` L224, `_dynamic_bold_requests` L269, `_header_border_request` L292, `_solution_bg_request` L305, `update_puzzle_info` L318, `restore_puzzle_from_solved_sheet` L637, `update_solution` L696, `move_puzzle_to_solved_sheet` L706, `demote_tab_to_puzzle` L1139 (value reads), `_add_contribution_table` L1473, `_puzzle_tab_header_requests` L1777, `header_links_request` L1822, `promote_tab_to_target` L2041 (value reads)
- Test: `tests/test_header_geometry.py`

**Interfaces:**
- Produces, in `bot/cogs/sheet_layout.py`:

```python
@dataclass(frozen=True)
class HeaderGeometry:
    layout: str            # HEADER_FULL | HEADER_COMPACT
    title_a1: Any          # name/title cell (full: config.puzzle_cell_name)
    status_a1: Any         # solution/status cell (full: config.puzzle_cell_solution)
    progress_a1: Any       # full: config.puzzle_cell_progress; compact: None
    feeds_a1: str | None   # full: FEEDS_LINKS_A1; compact main: "A3"; compact extra: None
    frozen_rows: int       # full: FROZEN_ROWS; compact: 0
    band_rows: int         # rows the header occupies (grey/border): full FROZEN_ROWS
    band_cols: int         # columns bordered from the header column: full 17 (HEADER_LAST_COL)
    grey_whole_rows: bool  # full: True (grey spans the whole rows); compact: False
```

- Produces, on `GoogleSheets`:
  - `_full_geometry(self) -> HeaderGeometry`, built at call time from `config.puzzle_cell_name/solution/progress`, `sheet_layout.FEEDS_LINKS_A1`, `sheet_layout.FROZEN_ROWS` and `self.HEADER_LAST_COL`.
  - `_header_geometry(self, puzzle, page_id=None) -> HeaderGeometry`. In this task it always returns `_full_geometry()`; Task 3 adds the compact branch.
  - `_tab_format_requests(self, page_id, label_col, geom=None)`, `_dynamic_bold_requests(self, page_id, offset, geom=None)`, `_header_border_request(self, page_id, offset, geom=None)`. `geom=None` means `_full_geometry()`, so direct test calls keep working.
  - `_title_request(self, puzzle, page_id, offset, geom, name=None) -> dict`. Full: today's `update_cell('=HYPERLINK(...)', row(title_a1), col(title_a1)+offset, FORMULA_INPUT, page_id)`.
  - `_solve_header_requests(self, puzzle, page_id, offset, geom, *, own_tab: bool) -> list`. Full: `[update_cell(solution @status), update_cell("Solved" @progress)] + ([_solution_bg_request(puzzle, SOLUTION_GREEN)] if own_tab else [])`.
  - `_unsolve_header_requests(self, puzzle, page_id, offset, geom, *, own_tab: bool) -> list`. Full: the two `""` writes, plus `[_solution_bg_request(puzzle, HEADER_GREY)]` if `own_tab`.
  - `_link_runs(self, prefix: str, links: list[Link]) -> tuple[str, list]`. Returns the text and the `textFormatRuns`. With `prefix=""` the output is exactly today's runs. With a prefix, the first run is `{"startIndex": 0, "format": {}}` and the link runs start at `_utf16_len(prefix)`.
  - `header_links_request(self, page_id, offset, links, geom=None) -> dict`, with the feeds cell taken from `geom.feeds_a1`.
  - `_contribution_table_requests(self, page_id, header_row) -> list`, returning the `[updateCells(CONTRIBUTION_HEADER), addTable]` pair that `_add_contribution_table` sends today. `_add_contribution_table` builds its body from it.
  - `_hide_key_column_request(self, page_id) -> dict`, the `updateDimensionProperties hiddenByUser` request `_add_contribution_table` sends today. `_add_contribution_table` uses it.

- [ ] **Step 1: Write the pin test** (it fails only because the names don't exist yet).

```python
def test_full_geometry_reads_the_patched_config_at_call_time():
    cog = GoogleSheets.__new__(GoogleSheets)
    with patch("bot.cogs.puzzles_gsheet.config") as cfg:
        cfg.puzzle_cell_name, cfg.puzzle_cell_solution, cfg.puzzle_cell_progress = "C1", "J1", "C3"
        g = cog._header_geometry(make_puzzle())
    assert (g.layout, g.title_a1, g.status_a1, g.progress_a1) == ("full", "C1", "J1", "C3")
    assert (g.feeds_a1, g.frozen_rows, g.band_rows, g.band_cols, g.grey_whole_rows) \
        == ("I3", 3, 3, 17, True)

def test_link_runs_without_prefix_match_the_feeds_cell():
    cog = GoogleSheets.__new__(GoogleSheets)
    text, runs = cog._link_runs("", [Link("A", "u1"), Link("B", None)])
    assert text == "A, B"
    assert runs == [{"startIndex": 0, "format": {"link": {"uri": "u1"}}},
                    {"startIndex": 1, "format": {}}, {"startIndex": 3, "format": {}}]
```

- [ ] **Step 2: Run it and confirm it fails.** Run `timeout 600 pipenv run python -m pytest tests/test_header_geometry.py -q > /tmp/x.log 2>&1`. Expected: FAIL (`AttributeError`).
- [ ] **Step 3: Implement the seam.** Route the listed sites through it. Rules:
  - Every site that built a cell address from `config.puzzle_cell_*` or `sheet_layout.FEEDS_LINKS_A1` now takes it from `geom` (`geom = self._header_geometry(puzzle, page)`).
  - The solve/unsolve loops splice `_solve_/_unsolve_header_requests` in exactly where the inline `update_cell`s were, so the move and tab-colour requests stay after them in the same order. Extras use `own_tab=False` and offset `0`, as today.
  - `_puzzle_tab_header_requests` calls `_title_request` at the position where the inline HYPERLINK `update_cell` was.
  - Promote/demote keep the literal `"B2"` flavour ref. They take progress/solution refs from `self._full_geometry()`.
  - Leave the five `_add_contribution_table(header_row=sheet_layout.FROZEN_ROWS)` call sites and `_feeds_line_requests` / `_tab_layout` unchanged.
- [ ] **Step 4: Run the whole suite unchanged.** Run `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/x.log 2>&1`. Expected: everything that passed after Task 1 still passes, plus the 2 new tests. Then confirm with `git diff --stat` that only `bot/cogs/sheet_layout.py`, `bot/cogs/puzzles_gsheet.py` and `tests/test_header_geometry.py` changed.
- [ ] **Step 5: Run the checks.** Run `pipenv run basedpyright bot/` (expected: 0 errors) and `pipenv run ruff check` (expected: clean).
- [ ] **Step 6: Commit.** Message: `refactor(sheets): per-layout HeaderGeometry seam for puzzle-tab header writes (no behaviour change)`.

---

### Task 3: Compact header at tab creation (tabs, files, files retry, meta)

**Files:**
- Modify: `bot/cogs/sheet_layout.py`, `bot/cogs/puzzles_gsheet.py` (`_header_geometry`, `_tab_format_requests`, `_header_border_request`, `header_links_request`, `_puzzle_tab_header_requests`, `generate_puzzle_tab`)
- Test: `tests/test_compact_header.py` (create a file-local `_cog()` and `_capture_batch()` as in `tests/test_contribution_table_requests.py`; for files mode reuse the setup shape of `tests/test_files_mode_create.py` `_files_hunt`/`_sheet`, copied locally)
- Test: `tests/test_sheet_call_volume.py` (add one test)

**Interfaces:**
- Consumes: Task 2's `HeaderGeometry`, `_header_geometry`, `_title_request`, `_link_runs`, `_contribution_table_requests`, `_hide_key_column_request`, and the geometry-aware format/border helpers.
- Produces:
  - `sheet_layout.COMPACT_GEOMETRY = HeaderGeometry("compact", "A1", "A2", None, "A3", 0, 3, 1, False)`.
  - `sheet_layout.COMPACT_EXTRA_GEOMETRY = dataclasses.replace(COMPACT_GEOMETRY, feeds_a1=None, band_rows=2)`.
  - `_header_geometry` returns `COMPACT_GEOMETRY` when `getattr(puzzle, "header_layout", None) == HEADER_COMPACT`. When `page_id` is given and `int(page_id) != int(puzzle.google_page_id or 0)` (an extra), it returns `COMPACT_EXTRA_GEOMETRY` instead. Any other value, including a MagicMock attribute, gets the full geometry.
  - `_status_cell_request(self, page_id, offset, geom, answer: str | None) -> dict`.
  - `_compact_header_requests(self, puzzle, page_id, offset, geom, *, header_links=None, title=None) -> list`.
  - `header_links_request(..., geom=COMPACT_GEOMETRY)` returns the compact A3 cell.

Pinned request shapes (exact copy):

```python
# A1 title (offset = header column): rich text, never a formula; no uri -> runs [{"startIndex":0,"format":{}}]
{"updateCells": {"rows": [{"values": [{"userEnteredValue": {"stringValue": name},
     "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": url}}}]}]}],
  "fields": "userEnteredValue,textFormatRuns", "range": <A1 + offset, 1x1>}}
# A2 status: answer None -> "Unsolved", HEADER_GREY, bold False; answer -> answer, SOLUTION_GREEN, bold True
{"updateCells": {"rows": [{"values": [{"userEnteredValue": {"stringValue": text},
     "userEnteredFormat": {"backgroundColor": colour, "textFormat": {"bold": bold}}}]}],
  "fields": "userEnteredValue,userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold",
  "range": <A2 + offset, 1x1>}}
# A3: links -> "Feeds: " + ", ".join(names), runs from _link_runs("Feeds: ", links);
#     none (after dropping nameless links) -> {"userEnteredValue": {"stringValue": "No links"}}
#     fields always "userEnteredValue,textFormatRuns" (clears stale runs)
```

`_compact_header_requests` order: title, status (`answer = puzzle.solution if puzzle.solved else None`, so a reconcile re-create of a solved puzzle shows its answer), feeds (only if `geom.feeds_a1`), `*_tab_format_requests(page_id, offset, geom)`, `_header_border_request(page_id, offset, geom)`. For compact, `_tab_format_requests` emits no `frozenRowCount` request (when `geom.frozen_rows == 0`) and greys only `rows 0..band_rows × cols offset..offset+band_cols`. It still emits the whole-tab font and the 220 px width at `label_col`. The border covers the same band.

`_puzzle_tab_header_requests` returns `_compact_header_requests(puzzle, page_id, offset, geom, header_links=header_links)` for a compact puzzle; for a compact puzzle the header_links default is still the A3 write. In `generate_puzzle_tab`, a **compact target** appends `*_contribution_table_requests(page_id, sheet_layout.FROZEN_ROWS), _hide_key_column_request(page_id)` to the same header batch and skips the deferred `_add_contribution_table` block. That is the atomic invariant Task 7 relies on.

- [ ] **Step 1: Write the failing tests** in `tests/test_compact_header.py`:
  - `test_compact_feeder_tab_is_three_cells_and_nothing_else`. Call `generate_puzzle_tab` for `make_puzzle(name="Alpha")`, with `header_layout="compact"` and `url="https://h/alpha"`, `is_target=False`, `header_links=[Link("Alpha Meta","U1")]`. Assert:
    - the title cell equals the pinned A1 shape at range `(0,1,0,1)`;
    - the A2 status reads `Unsolved` with grey and bold False;
    - A3 is `Feeds: Alpha Meta` with runs `[{"startIndex":0,"format":{}}, {"startIndex":7,"format":{"link":{"uri":"U1"}}}]`;
    - no request contains `mergeCells` or `frozenRowCount`, and none is a `formulaValue`;
    - the grey `repeatCell` range is `{"sheetId":P,"startRowIndex":0,"endRowIndex":3,"startColumnIndex":0,"endColumnIndex":1}`;
    - the `updateBorders` range is the same rectangle;
    - the width request is for column 0 at 220;
    - every `updateCells`/`repeatCell` that has column bounds lies inside rows 0–2 of column 0.
  - `test_compact_tab_without_links_says_no_links`. Pass `header_links=None`; A3 is `{"userEnteredValue":{"stringValue":"No links"}}`.
  - `test_compact_feeds_runs_count_utf16_units`. Links `[Link("🧩 Meta","U1"), Link("B","U2")]`; the runs start at `[0, 7, 14, 16]`. (`"🧩 Meta"` is 7 UTF-16 units.)
  - `test_compact_target_has_header_in_b_and_table_in_the_same_batch`. `is_target=True`; exactly one `batch_update` happens after `create_blank_tab`. It holds the title at column 1, an `addTable` whose range has `startRowIndex == 3`, and a `hiddenByUser` request for column 0. `_add_contribution_table` is never called (patch it as an `AsyncMock` and assert it was not awaited).
  - `test_compact_target_create_failure_propagates_with_nothing_deferred`. `batch_update` raises `RuntimeError`, so `generate_puzzle_tab` raises and `_add_contribution_table` is not awaited.
  - `test_compact_files_create_and_its_retry_both_stamp_the_compact_header`. Use `create_puzzle_file` with the first header batch raising `HttpError`. The retry batch (`page_id=` path) contains the compact A1/A2/A3 cells and the template trim. Neither batch contains `mergeCells`.
  - `test_full_puzzle_with_magicmock_layout_gets_the_full_header`. A puzzle whose `header_layout` is a `MagicMock` produces today's `Puzzle Title:` label cell.
- [ ] **Step 2: Add the call-volume pin** to `tests/test_sheet_call_volume.py`. Test `test_compact_tab_create_budget`: `create_puzzle_spreadsheet` for a compact feeder in a tabs hunt makes 1 `create_blank_tab` and 2 `batch_update`s (the header batch, then `update_puzzle_info`'s title rewrite, as for full). The same create for a compact target makes 1 `create_blank_tab` and 2 `batch_update`s, and no `_add_contribution_table`.
- [ ] **Step 3: Run the new tests and confirm they fail.** Run `timeout 600 pipenv run python -m pytest tests/test_compact_header.py tests/test_sheet_call_volume.py -q > /tmp/x.log 2>&1`. Expected: the new tests FAIL.
- [ ] **Step 4: Implement** per the Interfaces block. `update_puzzle_info` already routes through `_title_request` (Task 2); add its compact branch (the pinned A1 shape at `title_a1 + offset`) here, because create calls it.
- [ ] **Step 5: Run the new tests, then the whole suite.** Run `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/x.log 2>&1`. Expected: all pass.
- [ ] **Step 6: Commit.** Message: `feat(header): compact three-cell header on tab/file creation`.

---

### Task 4: Flag on create, reconcile reads the row, compact additional sheets

**Files:**
- Modify: `bot/core/puzzle_service.py`: `create_puzzle` (~L3251, set the flag after building `new_puzzle`, before `create_puzzle_spreadsheet`), `create_mini` (~L2996)
- Modify: `bot/cogs/puzzles_gsheet.py`: `create_additional_spreadsheet` L1114
- Test: `tests/test_compact_header.py`, `tests/test_create_service.py`-style service tests in a new section of `tests/test_compact_header.py` (patch DAOs at `bot.core.puzzle_service.*`, per AGENTS.md)

**Interfaces:**
- Consumes: `HuntData.header_style`, `PuzzleData.header_layout` (Task 1), `_compact_header_requests` and `COMPACT_EXTRA_GEOMETRY` (Task 3).
- Produces: `PuzzleService._header_layout_for(hunt) -> str` (staticmethod). It returns `hunt.header_style` if that is in `HEADER_STYLES`, else `HEADER_FULL`. Both create paths set `puzzle.header_layout = self._header_layout_for(hunt)` before any sheets call. The generic insert `commit()` that follows persists it. Reconcile (`_reconcile_tabs` tab_create ~L619, file_create ~L765) is **not changed**: it passes the stored row, so an old puzzle's re-created tab stays full.
- Decision (spec gap): minis do have tabs (`create_mini` → `create_puzzle_spreadsheet`), so minis take the hunt's style like any puzzle.

`create_additional_spreadsheet` for a compact puzzle:
1. `name = await get_unique_tab_name(sheet_name, spreadsheet_id)`.
2. `sheet_id = await create_blank_tab(spreadsheet_id, name, index + 1)`. This replaces `add_new_sheet`, which duplicates the hidden "Tab template" and its full labelled header.
3. One `batch_update` of `_compact_header_requests(puzzle, sheet_id, 0, COMPACT_EXTRA_GEOMETRY, title=name_arg or puzzle.name)`.
4. No `update_puzzle_info` call.

Full puzzles keep `add_new_sheet` + `update_puzzle_info`, byte for byte.

- [ ] **Step 1: Write the failing tests.**
  - `test_create_puzzle_copies_the_hunt_style_before_the_sheet_call`. Use two hunts (`header_style="compact"` and `"full"`). The `create_puzzle_spreadsheet` AsyncMock's `side_effect` records `puzzle.header_layout` at call time; the recorded values are `"compact"` then `"full"`.
  - `test_create_mini_copies_the_hunt_style`. Same approach for `create_mini`.
  - `test_unknown_hunt_style_creates_a_full_puzzle`. A hunt with `header_style="weird"` gives `header_layout == "full"`.
  - `test_reconcile_recreates_an_old_full_puzzle_full_in_a_compact_hunt`. The hunt is `"compact"` and the stored puzzle is `header_layout="full"` with its tab missing. `add_new_puzzle_sheet` is awaited with that puzzle, which still has `header_layout == "full"`.
  - `test_compact_extra_sheet_is_a_blank_tab_with_title_and_status`. Call `create_additional_spreadsheet(puzzle, "Grid")` on a compact solved puzzle (`solution="ANSWER"`). Assert:
    - `create_blank_tab` is awaited with index `idx+1`, and `add_new_sheet` is not awaited;
    - the one batch holds A1 `"Grid"` linked to `puzzle.url`, and A2 `"ANSWER"` with bold and green;
    - there is no A3 cell;
    - the grey and border ranges are rows 0–2 of column 0;
    - `update_puzzle_info` is not awaited.
  - `test_full_extra_sheet_still_duplicates_the_template`. For a full puzzle, `add_new_sheet` and `update_puzzle_info` are awaited as today.
- [ ] **Step 2: Run them and confirm they fail.** Run `timeout 600 pipenv run python -m pytest tests/test_compact_header.py -q > /tmp/x.log 2>&1`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** per the Interfaces block.
- [ ] **Step 4: Run the whole suite.** Run `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/x.log 2>&1`. Expected: all pass.
- [ ] **Step 5: Commit.** Message: `feat(header): puzzles take the hunt's header style at creation; compact extra sheets`.

---

### Task 5: Solve, unsolve, change/add answer, rename on compact puzzles

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py`: `_solve_header_requests`, `_unsolve_header_requests`, `update_solution`, `_title_request` (rename uses `update_puzzle_info`; it already has the compact branch from Task 3, so only a test is added)
- Test: `tests/test_compact_header.py`, `tests/test_files_mode_lifecycle.py` (add one compact solve/unsolve case beside the existing full ones)

**Interfaces:**
- Consumes: `_status_cell_request` (Task 3), and `_header_geometry(puzzle, page_id)` returning `COMPACT_EXTRA_GEOMETRY` for extras.
- Produces, compact branches:
  - `_solve_header_requests` → `[_status_cell_request(page_id, offset, geom, puzzle.solution)]` for the main tab and extras alike.
  - `_unsolve_header_requests` → `[_status_cell_request(page_id, offset, geom, None)]`.
  - `update_solution` writes the value only: `update_cell(puzzle.solution, row(geom.status_a1), col(geom.status_a1)+offset, STRING_INPUT, page)`. That already falls out of Task 2's routing for the main tab. For compact puzzles it also writes the same value-only cell on each extra with a page id, in the same batch. Full keeps the main tab only.
  - Never emit `_solution_bg_request` for compact; the status cell carries its own background.

- [ ] **Step 1: Write the failing tests.** Use a file-local helper `_cells(requests) -> set[(row, col)]`, built from every `updateCells`/`repeatCell` range that has column bounds.
  - `test_compact_solve_touches_only_the_status_cell_on_main_and_extras`. The puzzle is a compact feeder with `solution="BLUE"` and extras `["e1"]`; call `move_puzzle_to_solved_sheet`. Main and extra each get exactly the A2 cell `"BLUE"`, bold, `SOLUTION_GREEN`, at column 0. `_cells` ⊆ `{(1, 0)}` on both pages, and no range has column 8 or row 2 in column 1.
  - `test_compact_meta_solve_writes_b2`. Use `is_target=True` and the real `_header_shift`; the status lands at `(1, 1)`.
  - `test_compact_unsolve_restores_unsolved_grey_not_bold`. Call `restore_puzzle_from_solved_sheet` on main + extra; each gets `"Unsolved"`, `HEADER_GREY`, `bold False`.
  - `test_compact_solve_writes_a_formula_looking_answer_as_text`. With `solution="=A1"`, the value key is `stringValue`.
  - `test_compact_change_answer_updates_main_and_extras_value_only`. Call `update_solution`; there are two requests, both with `fields == "userEnteredValue"`, at `(1, 0)` on the main page and on `e1`.
  - `test_compact_rename_rewrites_a1_only`. Call `update_puzzle_info(puzzle, True)`; the requests are the A1 rich-text cell plus the tab-title `updateSheetProperties`, and there is no `formulaValue`.
  - `test_full_puzzle_solve_is_unchanged`. A full puzzle still gets I1/B3 + `_solution_bg_request`. This is a guard; the existing lifecycle tests cover the details.
  - In `tests/test_files_mode_lifecycle.py`: `test_files_compact_solve_and_unsolve_write_a2`, a files-hunt compact puzzle. The solve batch goes to the puzzle's own spreadsheet with the A2 cell, and the file move is unchanged.
- [ ] **Step 2: Run them and confirm they fail.** Run `timeout 600 pipenv run python -m pytest tests/test_compact_header.py tests/test_files_mode_lifecycle.py -q > /tmp/x.log 2>&1`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the compact branches.
- [ ] **Step 4: Run the whole suite.** Run `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/x.log 2>&1`. Expected: all pass.
- [ ] **Step 5: Commit.** Message: `feat(header): compact solve/unsolve/answer/rename write only A1-A2`.

---

### Task 6: Feeds refresh on compact puzzles

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py`: `write_header_links` L1884
- Modify: `bot/core/puzzle_service.py`: `refresh_header_links` ~L3744
- Test: `tests/test_compact_header.py`

**Interfaces:**
- Consumes: `header_links_request(..., geom=COMPACT_GEOMETRY)` (Task 3).
- Produces:
  - `write_header_links(self, tabs, layouts: Mapping[str, str] | None = None) -> list[str]`. `tabs` keeps its `(SheetLocation, offset, links)` 3-tuples, because existing tests unpack them. A page whose `layouts.get(str(page_id)) == HEADER_COMPACT` gets `[header_links_request(int(page_id), offset, links, sheet_layout.COMPACT_GEOMETRY)]`. Every other page gets today's `_feeds_line_requests`, with no row-3 re-stamp for compact.
  - `refresh_header_links` builds `layouts = {str(p.google_page_id): p.header_layout for p in puzzles if p.header_layout == HEADER_COMPACT}` and passes `layouts=layouts` **only when it is non-empty**, so an all-full call keeps today's exact call shape.

- [ ] **Step 1: Write the failing tests.**
  - `test_mixed_layouts_in_one_spreadsheet_batch`. Call `write_header_links([(Loc("LIVE",901),0,[Link("M","U")]), (Loc("LIVE",902),0,[Link("M","U")])], layouts={"902":"compact"})`. There is one batch. The requests for page 901 include `unmergeCells` and the H3 label. The requests for page 902 are exactly one `updateCells` at `(2,0)` with text `"Feeds: M"`, and there are no `unmergeCells`/`mergeCells`/`updateBorders` for page 902.
  - `test_compact_refresh_with_no_links_writes_no_links`.
  - `test_refresh_header_links_passes_layouts_for_compact_puzzles_only`. Use the `_service_with_cog()` shape from `tests/test_header_links.py`, copied locally. With a compact and a full feeder, `write_header_links.await_args.kwargs["layouts"] == {"<compact page>": "compact"}`. With two full feeders, `"layouts" not in await_args.kwargs`.
  - `test_compact_meta_refresh_writes_b3`. A compact target with the real `_header_shift` gets offset 1, so the cell is at `(2,1)`.
- [ ] **Step 2: Run them and confirm they fail.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the whole suite.** Run `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/x.log 2>&1`. Expected: all pass, `tests/test_header_links.py` and `tests/test_sheet_routing.py` unchanged.
- [ ] **Step 5: Commit.** Message: `feat(header): Feeds refresh rewrites only A3 on compact tabs`.

---

### Task 7: Promote to meta/minimeta and demote back, compact

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py`: `promote_tab_to_target` L2041, `_ensure_contribution_table` L2131, `demote_tab_to_puzzle` L1139
- Test: `tests/test_compact_header.py`, `tests/test_sheet_call_volume.py`

**Interfaces:**
- Consumes: `_contribution_table_requests`, `_hide_key_column_request` (Task 2), `header_links_request` + `COMPACT_GEOMETRY` (Task 3).
- Produces:
  - `_own_table_present(self, page_id, spreadsheet_id) -> bool`, one `sheet_list` read with `select_table(s.get("tables"), contribution_table_name(page_id))`.
  - `_promote_compact(self, puzzle, spreadsheet_id, header_links=None, *, known_tableless=False) -> None`.
  - `_demote_compact(self, puzzle, spreadsheet_id, header_links=None) -> None`.
  - `promote_tab_to_target` and `demote_tab_to_puzzle` dispatch to these for compact puzzles **before** any `get_page_name_by_id` / `_tab_layout` / `_values_get`. In `_ensure_contribution_table`, after the "tab_name is None" branch, a compact puzzle calls `_promote_compact(puzzle, spreadsheet_id, known_tableless=True)` and returns `None`; no `_tab_layout` call.

`_promote_compact`:
1. If not `known_tableless` and `_own_table_present` is true → return with no write. This makes it idempotent.
2. Otherwise send **one** batch, in this order. `W = CONTRIBUTION_WIDTH` (4), `S = TARGET_HEADER_SHIFT` (1).
   - `insertDimension` COLUMNS `0..W`, `inheritFromBefore: False`.
   - `{"cutPaste": {"source": {"sheetId": P, "startRowIndex": 0, "endRowIndex": 3, "startColumnIndex": W, "endColumnIndex": W+1}, "destination": {"sheetId": P, "rowIndex": 0, "columnIndex": S}, "pasteType": "PASTE_NORMAL"}}`. This moves the values, links, green and bold, grey and borders.
   - The 220 px width on column `S`.
   - If `header_links is not None`: `header_links_request(P, S, header_links, COMPACT_GEOMETRY)`.
   - `*_contribution_table_requests(P, sheet_layout.FROZEN_ROWS)`.
   - `_hide_key_column_request(P)`.

   Nothing else in rows 1–3 is unmerged or cleared.

`_demote_compact`:
1. If `_own_table_present` is false → `logger.warning(...)` and return. A retry after success is a no-op.
2. Otherwise send **one** batch:
   - `cutPaste` source rows 0..3, column `S` → destination `(0, W)`. That is where promote's cut left the original A1:A3 empty.
   - `deleteDimension` COLUMNS `0..W`, which removes the Table, the key column and the header column, so column E becomes column A.
   - If `header_links is not None`: `header_links_request(P, 0, header_links, COMPACT_GEOMETRY)`.

   No width request: column E kept the 220 px width from before promote.

- [ ] **Step 1: Write the failing tests.**
  - `test_compact_promote_is_one_atomic_batch_moving_only_the_three_cells`. Assert:
    - exactly one `batch_update`;
    - the request index of `insertDimension` < `cutPaste` < `addTable`;
    - the cutPaste source is `(rows 0–3, col 4)` and the destination is `(0, 1)`;
    - `addTable` `startRowIndex == 3`;
    - there is no `unmergeCells`, and no `updateCells` without `rows` (the old wipe);
    - `_values_get` and `_tab_layout` are not awaited (patch both as AsyncMock).
  - `test_compact_promote_is_a_no_op_when_the_table_exists`. `sheet_list` returns the page with `tables=[{"name": "Contributions 900", ...}]`; zero `batch_update` calls.
  - `test_compact_promote_rewrites_b3_when_links_given`. A `header_links` updateCells appears at `(2, 1)` after the cutPaste.
  - `test_ensure_contribution_table_promotes_a_tableless_compact_tab_without_probing`. One `sheet_list` read, one promote batch, `_tab_layout` not awaited.
  - `test_compact_demote_moves_b_back_before_deleting_columns`. One batch; the request index of `cutPaste` (source col 1 → destination col 4) < `deleteDimension(0..4)`; no `unmergeCells`.
  - `test_compact_demote_without_a_table_does_nothing`. Zero `batch_update` calls.
  - `test_full_promote_still_probes`. A full puzzle still awaits `_tab_layout`. This guard keeps the full path untouched.
  - In `tests/test_sheet_call_volume.py`, `test_compact_promote_and_demote_budgets`: promote is 1 `sheet_list` + 1 `batch_update`, and so is demote.
- [ ] **Step 2: Run them and confirm they fail.** Run `timeout 600 pipenv run python -m pytest tests/test_compact_header.py tests/test_sheet_call_volume.py -q > /tmp/x.log 2>&1`. Expected: FAIL.
- [ ] **Step 3: Implement.** The `promote_tab_to_target` / `demote_tab_to_puzzle` docstrings gain one paragraph on the compact path and the atomic invariant (compact header in B ⇔ own Table present).
- [ ] **Step 4: Run the whole suite.** Run `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/x.log 2>&1`. Expected: all pass; `tests/test_tab_geometry_probe.py` and `tests/test_contribution_table_requests.py` unchanged.
- [ ] **Step 5: Commit.** Message: `feat(header): compact promote/demote move only the three header cells, atomically`.

---

### Task 8: Setting surfaces: `!hunt`, `!update_settings`, service, web API

**Files:**
- Modify: `bot/core/puzzle_service.py`, `create_hunt` ~L4188: new kwarg `header_style: str = DEFAULT_HEADER_STYLE`. Raise `RuntimeError("bad_header_style")` when it is not in `HEADER_STYLES`, beside the `bad_sheet_mode` check, before any Discord call. After `HuntJsonDb.commit(new_hunt)`, call `HuntJsonDb.set_header_style(new_hunt.id, header_style)` and set `new_hunt.header_style`, best-effort in the same style as the voice block (`logger.exception` on failure). Docstring line added.
- Modify: `bot/cogs/puzzles.py`:
  - `hunt` L723: the trailing-token loop gains `, full` (case-insensitive, any order with `, files`/`, voice`) → `header_style = HEADER_FULL`; otherwise `DEFAULT_HEADER_STYLE`. Docstring `Usage: !hunt hunt-name:hunt-url[, role-name][, files][, voice][, full]`, plus one sentence: "Append `, full` for the original labelled sheet header (new hunts get the compact one)."
  - The `create_hunt` wrapper L773 takes and forwards `header_style`.
  - `update_settings` L2978: a `HuntData` + `header_style` branch placed next to the voice_per_puzzle branch.
  - `_UNSTORED_SETTING_KEYS[PuzzleData]` gains `"header_layout"`.
- Modify: `bot/web/actions.py`:
  - `_CREATE_HUNT_OPTIONAL` gets `"header_style"`; a value not in `HEADER_STYLES` → 400 `{"error": "bad_header_style"}`.
  - `_SETTINGS_KEYS` gets `"header_style"`; PATCH pops it like `voice_per_puzzle`. A non-member → 400 `{"error": "bad_value", "field": "header_style"}`. On success, after `update_hunt_settings`, call `HuntJsonDb.set_header_style(hunt_obj.id, v)` and set `hunt_obj.header_style = v`.
- Modify: `bot/web/data.py` `get_hunt_by_uid` SELECT (add `header_style`); `bot/web/api.py` hunt payload (`"header_style": hunt.get("header_style") or "full"`).
- Modify: `tests/test_update_settings_voice.py::test_every_field_the_generic_commit_skips_is_refused_with_a_pointer`. Remove Task 1's xfail mark and change `- {"voice_per_puzzle"}` to `- {"voice_per_puzzle", "header_style"}`. **Stated reason:** `header_style` is now a narrow-setter column that `update_settings` stores through its own branch, exactly like `voice_per_puzzle`. The guard's falsifying power for every other SPECIAL_ATTR is unchanged, and the new branch is pinned by the tests below.
- Test: `tests/test_header_style_surfaces.py`. Copy the fixtures locally from `test_sheet_mode_surfaces.py` (`_hunt_cog`, `_run_hunt`, `web`, `_body`), `test_update_settings_voice.py` (`env`) and `test_voice_web.py` (`_detail`, `_hrow`).

**Interfaces:**
- Consumes: `HEADER_*` constants and `HuntJsonDb.set_header_style` (Task 1).
- Produces: `PuzzleService.create_hunt(..., header_style=DEFAULT_HEADER_STYLE)`; the hunt JSON has `hunt.header_style: "full" | "compact"`; `POST /api/hunts` and `PATCH /api/hunts/<uid>/settings` accept `header_style`. The `!setup_server` and dashboard setup flows get the default (compact) with no choice, since the spec lists only `!hunt` and the create form.

Copy for the `update_settings` branch:
- Refusal: `` f":x: Cannot set `header_style={setting_value}`, needs `full` or `compact`." ``.
- Success: `` f":white_check_mark: Updated `header_style={new}` from old value: `{old}`. Puzzles created from now on get the {HEADER_STYLE_LABELS[new].lower()}; existing puzzles keep theirs." ``.
- `header_layout` refusal pointer: `` "It's fixed when the puzzle is created, from the hunt's `header_style` (`!update_settings header_style full|compact`); an existing tab isn't converted." ``

- [ ] **Step 1: Write the failing tests.**
  - `test_hunt_default_is_compact`. `_run_hunt("m:https://h/")` gives kwargs `header_style == "compact"`.
  - `test_hunt_full_token`, parametrized over `"m:https://h/, full"`, `", FULL"`, `", full, files"`, `", files, full, voice"`, `", Team A, full"`. Each gives `header_style == "full"`, the other tokens are still parsed, and the role is `"Team A"` only in the last case.
  - `test_create_hunt_wrapper_forwards_header_style_to_service`.
  - `test_service_create_hunt_rejects_bad_header_style_before_discord`. Expect `RuntimeError("bad_header_style")`, and `guild.create_category` not awaited.
  - `test_service_create_hunt_stores_the_style`, for `full` and for the default. `HuntJsonDb.set_header_style` is called with `(id, style)`, and the returned hunt carries it.
  - `test_update_settings_stores_header_style_through_its_setter`. Parametrize over `("compact","compact"), ("FULL","full")`. Expect `hdb.set_header_style(10, expected)`, `save_settings` not called, and the reply starts with `` ":white_check_mark: Updated `header_style=" ``.
  - `test_update_settings_rejects_an_unknown_header_style`. With `"banana"`: the setter is not called and the reply starts with `":x:"`.
  - `test_update_puzzle_settings_refuses_header_layout`. `PuzzleJsonDb.commit` is not called, and the reply contains `` "`header_style`" ``.
  - `test_web_create_hunt_forwards_header_style` / `test_web_create_hunt_rejects_bad_header_style` (400, service not awaited).
  - `test_settings_accepts_header_style`. `set_header_style(10, "full")` is called, and `header_style` is not forwarded to `update_hunt_settings`.
  - `test_settings_header_style_must_be_a_known_value`. `"Compact"` → 400, setter not called.
  - `test_hunt_detail_carries_header_style`. `_detail(_hrow(header_style="full"))["header_style"] == "full"`; a missing key gives `"full"`.
- [ ] **Step 2: Run them and confirm they fail.** Run `timeout 600 pipenv run python -m pytest tests/test_header_style_surfaces.py tests/test_update_settings_voice.py -q > /tmp/x.log 2>&1`. Expected: the new tests FAIL, and the amended guard is still xfail.
- [ ] **Step 3: Implement**, and amend the guard test as stated.
- [ ] **Step 4: Run the whole suite.** Run `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/x.log 2>&1`. Expected: all pass, with no xfail left.
- [ ] **Step 5: Run the checks.** Run `pipenv run basedpyright bot/` (expected: 0 errors) and `pipenv run ruff check` (expected: clean).
- [ ] **Step 6: Commit.** Message: `feat(header): header_style on !hunt, !update_settings and the hunt API`.

---

### Task 9: Dashboard: create-hunt choice and settings panel

**Files:**
- Modify: `frontend/src/types.ts` (~L207: `header_style: "full" | "compact";`), `frontend/src/api.ts` (`CreateHuntPayload` ~L969 and `HuntSettingsPatch` ~L775: `header_style?: "full" | "compact";`)
- Modify: `frontend/src/components/AdminSettingsPanel.tsx`:
  - export `HEADER_STYLE_OPTIONS = [{ value: "compact", label: "Compact header" }, { value: "full", label: "Full header" }] as const`;
  - `SETTING_LABELS.header_style = "header style"`;
  - prop `headerStyle`, and `FormValues.header_style`, included in the initial/resync state and in both dependency arrays;
  - a Mantine `Select` labelled "Header style", with description "Applies to puzzles created from now on; existing puzzles keep their header.", placed after the voice checkbox;
  - `patch.header_style` only when it changed.
- Modify: `frontend/src/pages/HuntBoard.tsx` (~L224: `headerStyle={data.hunt.header_style}`)
- Modify: `frontend/src/pages/HuntList.tsx`: `useState<"full" | "compact">("compact")`; a `Select` "Header style" using `HEADER_STYLE_OPTIONS`, with description "Compact: title, status and feeds in three cells. Full: the original labelled header."; always `payload.header_style = headerStyle`.
- Test: `frontend/src/components/AdminSettingsPanel.labels.test.ts`

**Interfaces:**
- Consumes: Task 8's API fields.

- [ ] **Step 1: Write the failing test.**

```ts
it("labels the header style setting and its choices", () => {
  expect(settingLabel("header_style")).toBe("header style");
  expect(HEADER_STYLE_OPTIONS.map((o) => [o.value, o.label])).toEqual([
    ["compact", "Compact header"], ["full", "Full header"]]);
});
```

- [ ] **Step 2: Run it and confirm it fails.** Run `cd frontend && timeout 300 npm test > /tmp/fe.log 2>&1`. Expected: FAIL (no export).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the frontend checks.** Run `cd frontend && timeout 300 npm test > /tmp/fe.log 2>&1`, then `cd frontend && timeout 600 npm run build > /tmp/fe-build.log 2>&1`. Expected: vitest passes; `tsc --noEmit` and `vite build` succeed. Component rendering and the PATCH/POST wiring have no runner here (vitest covers pure functions only). Report them as inspection-only and check them by reading the diff.
- [ ] **Step 5: Commit.** Message: `feat(dashboard): header style choice on hunt create and in Admin settings`.

---

### Task 10: Docs and final verification

**Files:**
- Modify: `docs/commands.md`:
  - `!hunt` row (L68): add `[, voice][, full]` to the args, and the sentence "append `, full` for the original labelled sheet header (new hunts get the compact header)";
  - `!update_settings` row (L294): "`header_style full|compact` sets the header new puzzles get (existing puzzles keep theirs); a puzzle's own `header_layout` can't be changed".
- Modify: `docs/user-guide.md`:
  - "Starting a hunt" (~L527) mentions `, full`;
  - the "Links between sheets" bullet (~L535) becomes: "In each puzzle's sheet header a **Feeds:** line links to the metas it feeds: under **Solution** on the full header, in cell A3 (B3 on a meta) on the compact header."
  - Add a short "Sheet header style" paragraph in §7: what compact shows (A1 linked title, A2 status/answer, A3 feeds); that new hunts default to it; that it is set by `, full`, `!update_settings header_style`, or the dashboard; and that a change applies only to puzzles created afterwards.
- Modify: `bot/cogs/sheet_layout.py` module docstring: state that the cell roles listed describe the **full** layout, and that the compact layout is `COMPACT_GEOMETRY`.

- [ ] **Step 1: Make the doc edits.**
- [ ] **Step 2: Run the full gate.**
  - `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/x.log 2>&1`: all pass.
  - `pipenv run basedpyright bot/`: 0 errors.
  - `pipenv run ruff check`: clean.
  - `cd frontend && timeout 600 npm run build > /tmp/fe-build.log 2>&1`: succeeds, and the guide renders (the build's `prebuild` step).
- [ ] **Step 3: Check spec acceptance against the tests.** Map each Acceptance bullet in the spec to a test name from Tasks 3–8 and write the map into the commit body. A manual Discord/Sheets smoke test (a compact tabs hunt and a compact files hunt; solve, unsolve, rename, tag, promote and demote a puzzle) is for the user after deploy to staging. List it, do not claim it.
- [ ] **Step 4: Commit.** Message: `docs(header): compact header in commands.md and the user guide`.

---

## Inventory coverage (`.ai/active/header-inventory.md`, 53 rows)

| Rows | Covered by |
|---|---|
| 1 `_puzzle_tab_header_requests` | T2 (title/format/bold/border via geometry), T3 (compact dispatch) |
| 2 `generate_puzzle_tab` | T3 (compact; compact target Table in the same batch) |
| 4 `create_puzzle_spreadsheet`, 5 `create_puzzle_file` + retry | T3 tests (tabs budget, files + retry) |
| 6, 7, 50 reconcile tab/file create | T4 (unchanged code; reads the stored flag; test) |
| 8 `create_puzzle`, 9 `create_mini` | T4 (flag set before the sheets call) |
| 11, 15, 43 solve/unsolve incl. extras; 13 `update_solution`; 14 `_solution_bg_request` | T2 (routing), T5 (compact) |
| 17 `update_puzzle_info` | T2 (`_title_request`), T3/T5 (compact A1) |
| 20 `header_links_request` | T2 (geometry), T3 (compact A3) |
| 21 `_feeds_line_requests`, 22 `write_header_links`, 23 `refresh_header_links` | T6 |
| 26 promote, 28 `_ensure_contribution_table`, 32 demote, 44 header reads | T2 (full refs via geometry), T7 (compact) |
| 35 `_add_contribution_table` | T2 (extract `_contribution_table_requests` / `_hide_key_column_request`), used by T3/T7 |
| 42 `create_additional_spreadsheet` | T4 (compact extras: blank tab + A1/A2) |
| 46 `_tab_format_requests`, 47 bold, 48 border | T2 (geometry), T3 (compact band) |
| 51 config `puzzle_cell_*` | T2 (full geometry reads them at call time; keys unchanged) |
| 52 store note | T1 (field added; comment extended) |

**Unaffected (no change needed; behaviour reached through a covered site, or not a puzzle header):**
- 3 `add_new_puzzle_sheet` (wrapper)
- 10 unassigned/evidence tabs
- 12 and 16 service solve/unsolve entries
- 18 rename/url callers
- 19 `rename_group` (no header write)
- 24 `refresh_header_links` callers, 25 link inputs
- 27 `promote_tab_to_group` (wrapper)
- 29 render callers
- 30 `make_minimeta` (DB flag only; the layout was fixed at create)
- 31 and 45 `_tab_layout` (still the full-layout probe; compact bypasses it in T7)
- 33 demote callers (the service's `_has_contribution_table` gate is unchanged)
- 34 convert/consolidate
- 36–38 `reconcile_table`, table helpers, grey-below (driven by the Table's range)
- 39–41 tab/file moves and the archive solved sheet
- 49 tab ordering
- 53 files-mode template trim (still keeps "Tab template" for full extras)
