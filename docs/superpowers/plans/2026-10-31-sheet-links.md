# Sheet links (and the groundwork for per-puzzle sheets) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Steps 1–3 of the per-puzzle sheets design: stop one slow Google call stalling every
hunt, give the code one "where does this puzzle's sheet live" resolver, and add links between
puzzle sheets in today's tab hunts — header up-links to what a puzzle feeds, and roll-up
names linking down to each puzzle's sheet.

**Architecture:** A thread-local `Http` replaces the global execute lock in
`bot/utils/gsheets.py`. A pure `bot/core/sheet_location.py` resolves a puzzle / extra sheet /
hunt sheet to `(spreadsheet_id, page_id)` and a URL, and the three existing resolvers become
wrappers over it. `reconcile_table` learns a `Link(text, uri)` cell written as rich text; the
service passes a URL function into the roll-up renderers; a header up-link cell is stamped by
`_puzzle_tab_header_requests` and refreshed by the service on edge changes.

**Scope ruling:** the spec's seam must eventually carry *every* sheet operation. This plan
moves the **resolvers** (URLs and "which spreadsheet") onto it, which is all the links need.
Converting the cog's ContextVar-routed operations (`reconcile_table`, `generate_puzzle_tab`,
promote/demote, `ensure_*_table` … all read `self.get_spreadsheet_id()`) to explicit locations
is a wide signature change only files mode needs; it is the **first task of the files-mode
plan**, not this one.

**Tech Stack:** Python 3.12/3.13, discord.py, google-api-python-client (Sheets v4 / Drive v3),
httplib2 + google-auth-httplib2, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-per-puzzle-sheets-design.md` (approved
2026-10-31). Code map used to write this plan:
`/tmp/claude-1000/-home-russ-projects-puzzbot/3b43a6eb-90aa-4b4d-bf76-f8235ef86eed/scratchpad/sheets-code-map.md`
(scratch; facts restated below where a task depends on them).

## Global Constraints

- **Nothing that works today breaks** (user's one wince). Tab-hunt behaviour is unchanged
  apart from the new links; hunts already running keep working with no manual step.
- **No header geometry change:** `PUZZLE_TAB_CELLS` / `PUZZLE_TAB_MERGES` / `FROZEN_ROWS`,
  the Table at row 4 and `_header_shift` stay exactly as they are. New cells go only where
  nothing is written today.
- **Links are rich text, never formulas:** `userEnteredValue.stringValue` +
  `textFormatRuns[].format.link.uri`, `fields` including `textFormatRuns`. No `=HYPERLINK()`
  for the new links.
- **Link URL form:** `https://docs.google.com/spreadsheets/d/<spreadsheet id>/edit#gid=<page id>`
  — always the full URL (a solved feeder lives in a different spreadsheet from its meta).
  Built only by `bot/core/sheet_location.py`.
- **Up-links:** feeds edges only (not informs, not none); groups with `target_puzzle_id` > 0;
  never a link to itself; a mini links to its parent minimeta.
- **Write budget:** Sheets writes are capped (~60/min). New requests ride in an existing
  `batch_update` where one is already made; any new `batch_update` call is per spreadsheet,
  never per row or per puzzle in a loop. `tests/test_sheet_call_volume.py` must still pass
  unchanged.
- **Test seams stay importable:** `bot.cogs.puzzles_gsheet.get_sheet`, `get_drive`,
  `execute_with_retry`; `cog.get_spreadsheet_id` / `get_solved_sheet_id` remain monkeypatchable.
- Run tests as `timeout 600 .venv/bin/python -m pytest tests/ -q > <file> 2>&1`; before any
  push also `.venv/bin/ruff check .` and `basedpyright --pythonpath .venv/bin/python bot/`
  (CI gates on it). Worktree needs `config.json` + `google_secrets.json` copied in.
- Every changed pre-existing test needs a stated reason in the commit message; coverage maps
  one-for-one (no weakened assertions).

## Review Focus

1. **A feeder solved mid-hunt:** its row in the meta's table must link to its tab on the
   *solved* spreadsheet after the solve, not the deleted live tab (ordering in `service.solve`).
2. **A hunt without a solved spreadsheet** (`solved_sheet_id == ""`): every link stays on the
   live spreadsheet; never a `/d//edit` URL.
3. **A pruned/blanked roll-up row** must lose its link, not keep a stale one on an empty cell.
4. **Promote/demote** (convert a puzzle to/from a meta) wipes header rows 1–3 cols 0..25 and
   re-stamps; the up-link cell must survive by being part of the re-stamp.
5. **A target renamed or deleted / converted back to a puzzle:** its feeders' up-links must
   update or disappear.

---

### Task 1: Thread-local Http instead of the global execute lock

**Files:**
- Modify: `bot/utils/gsheets.py` (`_execute_lock`, `_execute_locked`, `execute_sync`, `execute_with_retry`)
- Modify: comments naming `_execute_lock` at `bot/core/puzzle_service.py` (~3732) and `tests/test_delete_hunt_service.py` (~213)
- Test: `tests/test_gsheets_thread_http.py` (new)

**Interfaces:**
- Produces: `_thread_http() -> google_auth_httplib2.AuthorizedHttp` (one per thread, cached in a
  `threading.local()`, built from `_credentials()` and a fresh `httplib2.Http()`);
  `_execute(request)` = `request.execute(http=_thread_http())`, used by `execute_sync` and
  `execute_with_retry` in place of `_execute_locked`. Public names unchanged.

Background: the lock exists because the shared `httplib2.Http` inside the built service is not
thread-safe (concurrent `.execute()` corrupted the C heap, commit e01c81f). Request objects are
built on the event-loop thread with no I/O; only `.execute()` needs its own Http.
`HttpRequest.execute(http=None, num_retries=0)` accepts a per-call Http.

- [ ] **Step 1: failing tests** — `test_same_thread_reuses_http` (two calls on one thread →
  same object); `test_threads_get_distinct_http` (two `threading.Thread`s → different objects);
  `test_execute_passes_thread_http` (a `MagicMock` request: `execute` called once with
  `http=<that thread's object>`); `test_execute_with_retry_runs_off_loop_with_thread_http`
  (patch `_thread_http`; `await execute_with_retry(req)` → `req.execute.assert_called_once_with(http=...)`).
  Patch `_credentials` so no secrets file is read.
- [ ] **Step 2:** run, expect failures (no `_thread_http`).
- [ ] **Step 3:** implement; delete `_execute_lock` / `_execute_locked`; update the two comments.
- [ ] **Step 4:** run the new file, then the whole suite.
- [ ] **Step 5: commit** `perf(sheets): per-thread Http instead of one global execute lock`.

### Task 2: The sheet-location resolver (no behaviour change)

**Files:**
- Create: `bot/core/sheet_location.py`
- Modify: `bot/cogs/puzzles.py` — `get_puzzle_sheet` (~560), `get_additional_sheet_spreadsheet` (~575), `create_hunt_sheet_list` (~1896) — bodies delegate to the resolver; signatures unchanged
- Test: `tests/test_sheet_location.py` (new); existing `tests/test_additional_sheet_location.py` must pass unchanged

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class SheetLocation:
      spreadsheet_id: str
      page_id: str | int | None
      @property
      def url(self) -> str | None: ...   # None when spreadsheet_id is falsy or page_id is None/""
  def puzzle_location(hunt, puzzle) -> SheetLocation
  def additional_sheet_location(hunt, sheet) -> SheetLocation
  def hunt_sheet_location(hunt, page_id) -> SheetLocation
  ```
- Rules (today's, restated): a puzzle is on `hunt.solved_sheet_id` iff `puzzle.on_solved_sheet`
  **and** `hunt.solved_sheet_id` is non-empty, else `hunt.google_sheet_id`; page id is
  `puzzle.google_page_id`. An extra sheet uses its **own** `on_solved_sheet`. Hunt sheets are
  always on `hunt.google_sheet_id`. `url` uses `bot.utils.urls.spreadsheet_url` (which passes a
  legacy full-URL `google_sheet_id` through) — so `url` never builds `/d//edit`.
- Later tasks consume `puzzle_location(hunt, p).url`.

- [ ] **Step 1: failing tests** — live puzzle; solved puzzle with solved sheet; solved flag but
  empty `solved_sheet_id` → live; extra on solved sheet while its puzzle is live (and vice
  versa); hunt sheet; empty page id → `url is None`; legacy full-URL `google_sheet_id` passes
  through `spreadsheet_url` unchanged.
- [ ] **Step 2:** run, expect import failure.
- [ ] **Step 3:** implement; re-point the three `bot/cogs/puzzles.py` resolvers to it.
- [ ] **Step 4:** run new + `tests/test_additional_sheet_location.py` + full suite.
- [ ] **Step 5: commit** `refactor(sheets): one resolver for where a puzzle's sheet lives`.

### Task 3: Two links that ignore the solved spreadsheet

**Files:**
- Modify: `bot/cogs/puzzles.py` `send_state` (~3386); `bot/core/puzzle_service.py` mini welcome (~2688)
- Test: add to `tests/test_sheet_location.py` or the nearest existing test of each (find with `rg -l "send_state" tests`)

Both build `hunt.google_sheet_id` + page id unconditionally, so a solved puzzle's link is dead.
Route both through `puzzle_location(hunt, puzzle).url`. (Ruling: a bug fix, deliberately its own
commit so the seam commit stays behaviour-free.)

- [ ] **Step 1: failing test** — a solved puzzle on a hunt with a solved sheet: the produced
  link contains the solved spreadsheet id and the puzzle's page id. Also a live puzzle → unchanged.
- [ ] **Steps 2–4:** run (fail), fix, run full suite.
- [ ] **Step 5: commit** `fix(links): state and mini-welcome links follow a solved puzzle's tab`.

### Task 4: Link cells in `reconcile_table`

**Files:**
- Create: `Link` in `bot/core/sheet_location.py`: `@dataclass(frozen=True) class Link: text: str; uri: str | None`
- Modify: `bot/cogs/puzzles_gsheet.py` `reconcile_table` (~1202) nested `_cell` (~1294) and every `updateCells` it builds
- Test: `tests/test_contribution_table_requests.py` (add cases next to the reconcile tests ~101-165)

**Interfaces:**
- Consumes: nothing new. Produces: any `desired_rows` cell may be a `Link`. `_cell` renders a
  `Link` with a uri as `{"userEnteredValue": {"stringValue": text}, "textFormatRuns":
  [{"startIndex": 0, "format": {"link": {"uri": uri}}}]}`; a `Link` with `uri=None` as plain
  `stringValue` text. **Every** `updateCells` in `reconcile_table` (updates, prunes, append)
  uses `fields: "userEnteredValue,textFormatRuns"`, so a plain or blank cell clears an old link.
- Row keys: `norm_rows` stringifies `r[0]` only; a `Link` is never a key.

- [ ] **Step 1: failing tests** — a row with a `Link` name cell → that cell carries
  `textFormatRuns` with the uri and `stringValue` = text; plain cells have no runs; a pruned row
  and an appended row use `fields == "userEnteredValue,textFormatRuns"`; `Link(text, None)` →
  plain text.
- [ ] **Step 2–4:** run (fail), implement, run file + full suite. Existing assertions on
  `fields == "userEnteredValue"` in this file change to the new mask — state the reason (links
  must be clearable) in the commit; each such test keeps its other assertions.
- [ ] **Step 5: commit** `feat(sheets): roll-up tables can hold rich-text link cells`.

### Task 5: Roll-up names link to each puzzle's sheet

**Files:**
- Modify: `bot/core/contribution.py` `contribution_rows(tag_group_id, members, evidence, link_for=None)`
- Modify: `bot/core/unassigned.py` `unassigned_row(puzzle, link=None)`
- Modify: `bot/cogs/puzzles_gsheet.py` `render_contribution(..., link_for=None)` (~1688), `render_minimeta_contribution(minimeta, members, link_for=None)` (~1659)
- Modify: `bot/core/puzzle_service.py` `_render_rollup` (~887), `refresh_minimeta` (~830), `refresh_unassigned` (~3432) — pass `link_for = lambda p: puzzle_location(hunt, p).url`
- Test: existing unit tests of `contribution_rows` / `unassigned_row` (find: `rg -l "contribution_rows|unassigned_row" tests`), `tests/test_refresh_minimeta.py`, `tests/test_refresh_meta.py`

**Interfaces:**
- `link_for: Callable[[PuzzleData], str | None] | None`. When given, the name cell (index 1)
  of every row naming a puzzle becomes `Link(p.name, link_for(p))`; sourceless evidence rows
  (`source_label`) stay plain. When `None`, rows are exactly as today (strings) — so callers
  that don't pass it are unaffected.

- [ ] **Step 1: failing tests** — `contribution_rows` with two feeders (one live, one solved)
  and an informer: each name cell is a `Link` whose uri is the matching `link_for` result, in
  row order; free evidence row unchanged. Same for `unassigned_row` and the minimeta rows.
  Service: `_render_rollup` passes a `link_for` that yields the solved-sheet URL for a solved
  member (assert on the captured `render_contribution` kwargs).
- [ ] **Steps 2–4:** run (fail), implement, run full suite.
- [ ] **Step 5: commit** `feat(sheets): roll-up names link to each puzzle's sheet`.

### Task 6: Re-render targets after a solved feeder's tab moves

**Files:**
- Modify: `bot/core/puzzle_service.py` `solve` (~1395-1478)
- Test: `tests/test_sheet_failure_isolation.py` / `tests/test_sheet_refresh_error_split.py` (extend) or a new `tests/test_solve_link_order.py`

Today `solve` runs `refresh_target` per tag and `refresh_minimeta(parent)` **before**
`move_puzzle_to_solved_sheet`, so the links rendered point at the tab the move deletes. Move the
target and minimeta refreshes to **after** the sheet move (and its commits), keeping the
separate failure isolation (`failed_group_ids` / `failed_puzzle_ids`) and the single
`SheetRefreshError` at the end. If the move fails, the refreshes still run (they link to the
live tab, which still exists). `unsolve` already restores before refreshing — leave it.

- [ ] **Step 1: failing test** — record call order on a MagicMock cog: `move_puzzle_to_solved_sheet`
  precedes every `render_contribution`; a failing move still leads to the refreshes and to
  `SheetRefreshError` naming the puzzle; a failing refresh still lets the move happen.
- [ ] **Steps 2–4:** run (fail), reorder, run full suite. Any existing test pinning the old
  order changes with the stated reason (links must see the post-move tab).
- [ ] **Step 5: commit** `fix(solve): refresh roll-ups after the solved tab moves, so links follow it`.

### Task 7: Header up-links

**Files:**
- Modify: `bot/core/puzzle_service.py` — new `feeds_targets(puzzle) -> list[PuzzleData]` (model on `refresh_informs_targets` ~3327) and `refresh_header_links(hunt, puzzles)`; calls from the hook points below
- Modify: `bot/cogs/puzzles_gsheet.py` — `header_links_request(page_id, offset, links: list[Link]) -> dict` and stamp it inside `_puzzle_tab_header_requests` (~1400) when given links; a `write_header_links(hunt_locs: list[tuple[SheetLocation, offset, list[Link]]])` that sends one `batch_update` **per spreadsheet**
- Test: `tests/test_header_links.py` (new); `tests/test_contribution_table_requests.py` header-geometry tests must pass unchanged

**Interfaces:**
- `feeds_targets(puzzle)`: for each `gid in puzzle.tags` with `puzzle.has_edge(gid, "feeds")`,
  the group's `target_puzzle_id` puzzle if > 0 and not the puzzle itself; plus, for a mini
  (`parent_puzzle_id`), its parent. Ordered by target name; de-duplicated.
- The cell: row 0, column `HEADER_LAST_COL + offset` (column R on a feeder/mini, S on a
  target/minimeta header — `offset` from `_header_shift`), text `"Feeds: " + ", ".join(names)`
  with one `textFormatRuns` entry per name (`startIndex` at each name, link to
  `puzzle_location(hunt, target).url`, and a run with no link after each name so commas
  aren't linked); bold-free; `fields "userEnteredValue,textFormatRuns"`. No targets → the cell
  is written empty (clears an old line). Placement: **see open question in the hand-off — if the
  user prefers another spot, only this paragraph changes.**
- `refresh_header_links(hunt, puzzles)`: for each puzzle with a page id, compute links and
  write them via `write_header_links`, grouped by spreadsheet (≤2 calls: live + solved).
- Hook points (call after the existing commit/refresh, best-effort — a failure is logged, never
  fails the user's action):
  - `tag_with_edge`, `untag_from` → the puzzle;
  - `bulk_re_edge` → every member of the group;
  - `convert_to_type`, `convert_to_meta`, `convert_to_puzzle`, delete of a target → every
    puzzle that had a feeds edge to it (collect before the change for delete/convert-down);
  - rename of a target (`rename_puzzle`) → every puzzle that feeds it;
  - mini creation → the mini.
  - New-tab creation passes the links into `_puzzle_tab_header_requests` (no extra call);
    promote/demote re-stamp through the same function.

- [ ] **Step 1: failing tests** — `feeds_targets`: feeds vs informs vs none, two metas sorted,
  self-feed excluded, `target_puzzle_id` 0 skipped, mini → parent. Request shape: column index
  17 at offset 0 and 18 at offset 1; runs link each name to its URL and not the separator; empty
  list writes an empty cell with the same fields mask. Batching: three puzzles, two live and one
  solved → exactly two `batch_update` calls with the right spreadsheet ids. Hooks: untagging a
  feeder rewrites its header with the remaining targets; renaming a target rewrites each
  feeder's header with the new name.
- [ ] **Steps 2–4:** run (fail), implement, run full suite incl. `test_sheet_call_volume.py`.
- [ ] **Step 5: commit** `feat(sheets): puzzle headers link to the puzzles they feed`.

### Task 8: Existing hunts pick the links up

**Files:**
- Modify: `bot/core/puzzle_service.py` reconcile path (`_reconcile_tabs` ~602 or the `!reconcile apply` entry) — after tabs are healed, `refresh_header_links(hunt, all puzzles with tabs)` and re-render roll-ups as `!rebuild` does
- Modify: `docs/commands.md` (`!reconcile`, `!rebuild` rows), `docs/user-guide.md` (sheet navigation: header "Feeds:" links, names in roll-ups link to sheets)
- Test: `tests/test_reconcile.py` (extend)

Ruling (from the spec): no one-off sweep — running hunts get down-links on their next roll-up
render (any solve/tag/`!rebuild`) and header links on `!reconcile apply`, which the docs tell
organisers to run once.

- [ ] **Step 1: failing test** — `reconcile apply` calls `refresh_header_links` once with every
  puzzle that has a page id; dry-run (`!reconcile` without apply) writes nothing.
- [ ] **Steps 2–4:** run (fail), implement, docs, full suite + ruff + basedpyright.
- [ ] **Step 5: commit** `feat(reconcile): stamp header links on existing hunts; docs`.

---

## Manual acceptance (after merge + staging push)

1. On a test hunt: a feeder tagged to a meta shows "Feeds: <meta>" right of its header; the
   link opens the meta tab. Tag it to a second meta → both, alphabetical; untag one → one.
2. The meta's table: each feeder's name links to its tab. Solve a feeder (hunt with a solved
   sheet) → its name now opens the tab on the solved spreadsheet.
3. Convert the meta back to a puzzle → feeders' "Feeds:" line drops it. Rename a meta →
   feeders show the new name.
4. A mini shows "Feeds: <minimeta>". Unassigned Feeders names link to their sheets.
5. On an older hunt, `!reconcile apply` adds the header links.
