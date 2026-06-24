# Current Spec — Meta-matching: "Unclassified Puzzles" worktable

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Target model: **Phase 1 (tests) = Opus 4.8** · **Phase 2+ (implementation) = Sonnet 4.6**
Started: 2026-06-24
Status: ready for handoff — Phase 1 (tests)

Design doc (authoritative background): `docs/superpowers/specs/2026-06-23-meta-matching-design.md`.

---

## Before you start (executing model)

- **Execute only the current phase.** Do not look ahead and implement early.
- **On any unexpected failure**, invoke `superpowers:systematic-debugging` BEFORE changing anything.
- **Before claiming a phase done**, invoke `superpowers:verification-before-completion` and paste the
  acceptance command output into your message.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix, write
  `.ai/active/blocker.md` (what failed, the exact output, your hypothesis) and STOP.
- **Test runner (this project uses pipenv; plain `python`/`pytest` are NOT on PATH):**
  - Whole suite: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
  - One file: `timeout 60 pipenv run python -m pytest tests/<file>.py -q > /tmp/test.log 2>&1`
  - A killed run exits 124 (hang). Read `/tmp/test.log` for results. Single statement only — no
    `; echo` tail.
  - Async tests need **no** decorator — `pytest.ini` has `asyncio_mode = auto`. Write
    `async def test_...` exactly like `tests/test_evidence_service.py`.
- **Lint:** `pipenv run ruff check <changed files>` (rule set is pyflakes `F` only).

## Goal

A per-hunt Google-Sheet **`Unclassified Puzzles`** tab (a native Sheets Table) listing every
**non-meta puzzle that feeds no meta** (solved or unsolved). The bot keeps it current by
**incremental reconcile** (append new / fill answer on solve / prune when classified), keyed on
puzzle id, so it never clobbers solvers' hand-arranged notes. The sheet is a thinking space only —
the bot NEVER reads the arrangement back; classification stays on the existing retag paths.

## In scope

- New pure helpers (row shaping + diff) — the heart of the reconcile.
- `HuntData` fields + migration for the lazily-created tab/table.
- Store query `get_unclassified(hunt_id)`.
- `PuzzleService.refresh_unclassified(hunt, show_round=False)`.
- New gsheet-cog methods (native table create + read keys + apply changes).
- Fire `refresh_unclassified` from create / solve / retag (bang + web), plus a manual rebuild command
  with a `--round` flag.
- Web parity: a dashboard endpoint + control to create an **unclassified (hunt-level) puzzle**.

## Out of scope (do NOT build)

- Reading the sheet arrangement back into the bot. Assignment is the EXISTING retag only.
- Capstones / dossier / never-auto-solve (separate spec). Do not touch `tags`/edge semantics.
- Auto-suggesting metas from title/answer.
- Any refactor of `aggregate_to_tab`, `refresh_evidence`, or existing commands.

## Relevant files

- `tests/test_evidence_service.py` (read only) — **the exact pattern to mirror** for service tests
  (patch `bot.core.puzzle_service.{HuntJsonDb,PuzzleJsonDb,...}`, mock the gsheet cog, `PuzzleService(bot)`).
- `tests/conftest.py` (read only) — `make_puzzle(id=, name=, hunt_id=, solved=, solution=, tags=, ...)`.
- `bot/core/puzzle_service.py` (read; write in Phase 4/6) — `PuzzleService.__init__(self, bot, notifier=None)`;
  `self._gsheet_cog(hunt)`; `refresh_evidence` (lines ~756-782) is the template.
- `bot/store/hunt_data.py` (read; write Phase 2) — `evidence_sheet_page_id: Optional[int] = None` at line 37 is the pattern.
- `bot/store/mysqldb.py` (read; write Phase 3) — existing `PuzzleJsonDb` queries to mirror.
- `bot/cogs/puzzles_gsheet.py` (read; write Phase 5) — `create_blank_tab` (line 376), `execute_with_retry`, `get_sheet()`.
- `bot/cogs/puzzles.py` (write Phase 6) — bang triggers + `!rebuild`-style command.
- `bot/web/actions.py` (write Phase 7) — fire refresh from web edges + new create-unclassified endpoint.
- `frontend/src/…` (write Phase 8) — dashboard "create unclassified puzzle" control.
- `migrations/` (write Phase 2) — next free `NNN_unclassified.sql`.
- New: `bot/core/unclassified.py`, `tests/test_unclassified_helpers.py`, `tests/test_unclassified_service.py`.

## Constraints

- **Do not touch files outside the Relevant files list.** Do not refactor neighbouring code.
- **Migration number:** `010_unclassified.sql` is the next free slot *today*, but a parallel
  `!reconcile` session may also be adding migrations. `ls migrations/` and use the next free number;
  if `010` is taken, use the next and update references. (See the design doc Open items.)
- `PuzzleService` is constructed `PuzzleService(bot, notifier=None)`. It is container-resolved in the
  app; tests build it directly as `PuzzleService(MagicMock())`. If you add a constructor arg (you
  should NOT need to), grep `PuzzleService(` and update every call site.
- Refresh is **best-effort**: a sheet error must never fail the triggering solve/create/retag. Fire it
  `asyncio.create_task(...)` exactly like the existing evidence refresh (`bot/cogs/puzzles.py:1031`).

## Test constraints (inlined — you will not read global instructions)

- **Reuse exact helpers/seams; invent none.** Mirror `tests/test_evidence_service.py`: patch the store
  classes on `bot.core.puzzle_service`, mock the gsheet cog, use `make_puzzle` from `tests.conftest`.
- **Each test file defines/imports every helper it calls** — do NOT rely on a function in a sibling
  test file. Give each file its own `_make_hunt()` etc.
- **NEVER write a loop that resolves state "until" a condition.** If tempted, STOP and write `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says so.
- **Confirm every field exists before use** — `HuntData`, `PuzzleData` (grep the dataclass). Do not
  guess column/attribute names. `make_puzzle` kwargs: `id, name, hunt_id, solved, solution, metapuzzle,
  metameta, tags, status, channel_id, parent_puzzle_id, puzzle_type` (see conftest).
- **Assert rendered values, not just structure.** For row-shaping assert the actual strings:
  uppercase answer, the integer length, and `""` for an unsolved puzzle's answer/length.
- A test failing because a function/method/field doesn't exist yet is the RIGHT reason. A runtime
  `AttributeError` on a name you assumed (e.g. a column that isn't there) means a WRONG test
  assumption — fix the test, don't add scaffolding.
- Wrap every run in `timeout`, write to `/tmp/test.log`, single statement.

---

## Phase 1 — write ALL failing tests (model: Opus 4.8). NO production code.

Create two new test files. Commit them. Confirm each fails for the RIGHT reason (missing
module/function/field — `ModuleNotFoundError: bot.core.unclassified`, `AttributeError`, etc.), NOT a
bootstrap/runtime fatal.

### File A — `tests/test_unclassified_helpers.py`

Tests for the pure helpers in the (not-yet-existing) module `bot/core/unclassified.py`.

```python
"""Tests for meta-matching pure helpers: row shaping + reconcile diff."""
from tests.conftest import make_puzzle


class TestUnclassifiedRow:
    """unclassified_row(puzzle) -> [id, name, ANSWER_UPPER, length] ; blanks when unsolved."""

    def test_solved_row_uppercases_answer_and_counts_letters_excluding_spaces(self):
        from bot.core.unclassified import unclassified_row
        p = make_puzzle(id=1024, name="Crossword Caper", solved=True, solution="red herring")
        row = unclassified_row(p)
        assert row == [1024, "Crossword Caper", "RED HERRING", 10]   # length excludes the space

    def test_unsolved_row_has_blank_answer_and_blank_length(self):
        from bot.core.unclassified import unclassified_row
        p = make_puzzle(id=1025, name="Mystery Box", solved=False, solution=None)
        row = unclassified_row(p)
        assert row == [1025, "Mystery Box", "", ""]

    def test_length_strips_spaces_only_not_punctuation(self):
        from bot.core.unclassified import unclassified_row
        p = make_puzzle(id=2, name="P", solved=True, solution="A-B C")
        # strip spaces only: "A-B C" -> "A-BC" -> 4 chars
        assert unclassified_row(p)[3] == 4


class TestDiffRows:
    """diff_rows(current_ids, desired_rows) -> (appends, updates, prunes).
    desired_rows are [id, name, answer, length] lists (id is element 0)."""

    def test_new_desired_id_is_appended(self):
        from bot.core.unclassified import diff_rows
        desired = [[1, "A", "", ""], [2, "B", "ANS", 3]]
        appends, updates, prunes = diff_rows(current_ids=[1], desired_rows=desired)
        assert [r for r in appends] == [[2, "B", "ANS", 3]]
        assert prunes == []

    def test_missing_current_id_is_pruned(self):
        from bot.core.unclassified import diff_rows
        desired = [[1, "A", "", ""]]
        appends, updates, prunes = diff_rows(current_ids=[1, 9], desired_rows=desired)
        assert prunes == [9]
        assert appends == []

    def test_id_in_both_is_an_update(self):
        from bot.core.unclassified import diff_rows
        desired = [[1, "A", "ANS", 3]]
        appends, updates, prunes = diff_rows(current_ids=[1], desired_rows=desired)
        assert updates == [[1, "A", "ANS", 3]]
        assert appends == [] and prunes == []
```

> NOTE the exact return contract: `diff_rows` returns `(appends, updates, prunes)` where appends and
> updates are full row lists and prunes is a list of ids. Implement to THIS contract in Phase 4.

### File B — `tests/test_unclassified_service.py`

Tests for `PuzzleService.refresh_unclassified` and the new `HuntData` fields. Mirror
`tests/test_evidence_service.py` exactly (patch store classes on `bot.core.puzzle_service`, mock the
gsheet cog, build `PuzzleService(bot)`).

```python
"""Tests for meta-matching service layer + HuntData fields."""
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_puzzle


def _make_hunt(id=10):
    from bot.store.hunt_data import HuntData
    h = HuntData()
    h.id = id
    h.google_sheet_id = "SHEET1"
    h.unclassified_sheet_page_id = None
    h.unclassified_table_id = None
    return h


class TestHuntDataFields:
    def test_defaults(self):
        from bot.store.hunt_data import HuntData
        h = HuntData()
        assert h.unclassified_sheet_page_id is None
        assert h.unclassified_table_id is None


class TestRefreshUnclassified:
    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_no_gsheet_cog_is_noop(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        await svc.refresh_unclassified(_make_hunt())   # must not raise

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_creates_tab_and_table_lazily(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        PuzzleDb.get_unclassified.return_value = []
        gsheet = MagicMock()
        gsheet.create_blank_tab = AsyncMock(return_value=42)
        gsheet.ensure_unclassified_table = AsyncMock(return_value=7)
        gsheet.read_unclassified_keys = AsyncMock(return_value=[])
        gsheet.apply_unclassified_changes = AsyncMock()
        bot = MagicMock(); bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)
        await svc.refresh_unclassified(hunt)
        assert hunt.unclassified_sheet_page_id == 42
        assert hunt.unclassified_table_id == 7
        HuntDb.commit.assert_called_once_with(hunt)

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_reconcile_shapes_rows_uppercase_and_blank_unsolved(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt(); hunt.unclassified_sheet_page_id = 42; hunt.unclassified_table_id = 7
        PuzzleDb.get_unclassified.return_value = [
            make_puzzle(id=1, name="Solved One", solved=True, solution="cat"),
            make_puzzle(id=2, name="Open Two", solved=False, solution=None),
        ]
        gsheet = MagicMock()
        gsheet.read_unclassified_keys = AsyncMock(return_value=[])     # nothing in sheet yet
        gsheet.apply_unclassified_changes = AsyncMock()
        bot = MagicMock(); bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)
        await svc.refresh_unclassified(hunt)
        # appends carry uppercased answer + length, blank for unsolved
        appends = gsheet.apply_unclassified_changes.call_args.kwargs.get("appends") \
            or gsheet.apply_unclassified_changes.call_args[0][2]
        assert [1, "Solved One", "CAT", 3] in appends
        assert [2, "Open Two", "", ""] in appends

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_classified_row_is_pruned(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt(); hunt.unclassified_sheet_page_id = 42; hunt.unclassified_table_id = 7
        PuzzleDb.get_unclassified.return_value = []          # pool now empty
        gsheet = MagicMock()
        gsheet.read_unclassified_keys = AsyncMock(return_value=[5])    # id 5 still in sheet
        gsheet.apply_unclassified_changes = AsyncMock()
        bot = MagicMock(); bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)
        await svc.refresh_unclassified(hunt)
        prunes = gsheet.apply_unclassified_changes.call_args.kwargs.get("prunes") \
            or gsheet.apply_unclassified_changes.call_args[0][3]
        assert 5 in prunes
```

> The service must call `gsheet.apply_unclassified_changes(page_id, table_id, appends, updates, prunes)`
> (positional or keyword — keep your impl consistent with the asserts above; if you change the
> signature, change the asserts in the SAME phase). The diff itself comes from `diff_rows`.

### Phase 1 acceptance

1. `timeout 60 pipenv run python -m pytest tests/test_unclassified_helpers.py tests/test_unclassified_service.py -q > /tmp/test.log 2>&1` — **exits non-zero**, failures are `ModuleNotFoundError: No module named 'bot.core.unclassified'` and `AttributeError`/missing-field (the RIGHT reason).
2. The two files import and collect without a bootstrap/syntax fatal: the same command shows real test
   failures, not a collection error. If you see a collection/import fatal in the test file itself
   (not in the code-under-test), fix the test.
3. Commit: `git add tests/test_unclassified_helpers.py tests/test_unclassified_service.py && git commit` (no production code in this commit).

---

## Phase 2 — schema + HuntData fields (model: Sonnet 4.6)

1. `ls migrations/` → next free number (expected `010`). Create `migrations/010_unclassified.sql`:
   `ALTER TABLE hunts ADD COLUMN unclassified_sheet_page_id INT NULL, ADD COLUMN unclassified_table_id INT NULL;`
2. `bot/store/hunt_data.py` — add `unclassified_sheet_page_id: Optional[int] = None` and
   `unclassified_table_id: Optional[int] = None` next to `evidence_sheet_page_id` (line 37). Confirm
   `import_dict` reads all dataclass fields generically (it does for evidence — mirror).
- **Acceptance:** `timeout 60 pipenv run python -m pytest tests/test_unclassified_service.py::TestHuntDataFields -q > /tmp/test.log 2>&1` passes.

## Phase 3 — store query `get_unclassified` (model: Sonnet 4.6)

1. `bot/store/mysqldb.py` — add `PuzzleJsonDb.get_unclassified(self, hunt_id) -> list[PuzzleData]`.
   SQL: puzzles in the hunt with `metapuzzle = 0` that have NO tag into a round with a non-zero
   `meta_id`. Mirror an existing `get_*` query's cursor/close/import_dict shape. (DB-hitting code is
   NOT unit-tested here — verified at deploy; the service tests mock this method.)
- **Acceptance:** `pipenv run ruff check bot/store/mysqldb.py` clean; whole suite still green
  (`timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`).

## Phase 4 — pure helpers `bot/core/unclassified.py` (model: Sonnet 4.6)

1. Create `bot/core/unclassified.py` with `unclassified_row(puzzle)` and
   `diff_rows(current_ids, desired_rows)` to the exact contracts asserted in Phase 1.
   - `unclassified_row`: `[puzzle.id, puzzle.name, answer, length]` where `answer =
     (puzzle.solution or "").upper() if puzzle.solved else ""`, and `length = len(answer.replace(" ", ""))
     if answer else ""`.
   - `diff_rows`: appends = desired rows whose id ∉ current_ids; prunes = current_ids ∉ desired ids;
     updates = desired rows whose id ∈ current_ids.
- **Acceptance:** `timeout 60 pipenv run python -m pytest tests/test_unclassified_helpers.py -q > /tmp/test.log 2>&1` all pass.

## Phase 5 — gsheet native-table methods (model: Sonnet 4.6)

1. `bot/cogs/puzzles_gsheet.py` — add (mirroring `create_blank_tab` + `execute_with_retry` + `get_sheet()`):
   - `ensure_unclassified_table(spreadsheet_id, page_id, show_round=False) -> table_id` — `addTable`
     with columns `id`(hidden), `Puzzle`, `Answer`, `Length`(number), `Round`(hidden unless show_round),
     `Notes`. Idempotent (no-op/return existing if already present).
   - `read_unclassified_keys(spreadsheet_id, page_id) -> list[int]` — `values().get` on the id column,
     skip the header, ints.
   - `apply_unclassified_changes(page_id, table_id, appends, updates, prunes)` — `batchUpdate`:
     append rows (bot columns only), update answer/length cells for `updates` by id, delete whole rows
     for `prunes` by id. Writes ONLY bot columns; never the Notes/user columns.
- **Acceptance:** `pipenv run ruff check bot/cogs/puzzles_gsheet.py` clean; whole suite green. (Live
  Sheets behaviour is validated by smoke-running the bot at review, not pytest — note it in your phase report.)

## Phase 6 — service method + bang triggers (model: Sonnet 4.6)

1. `bot/core/puzzle_service.py` — add `async def refresh_unclassified(self, hunt, show_round=False)`
   mirroring `refresh_evidence`: resolve `self._gsheet_cog(hunt)`; if None return; lazily
   `create_blank_tab` + `ensure_unclassified_table` and commit hunt when page id unset; `pool =
   PuzzleJsonDb.get_unclassified(hunt.id)`; `desired = [unclassified_row(p) for p in pool]`; `keys =
   await gsheet.read_unclassified_keys(...)`; `appends, updates, prunes = diff_rows(keys, desired)`;
   `await gsheet.apply_unclassified_changes(...)`.
2. `bot/cogs/puzzles.py` — fire `asyncio.create_task(self.service.refresh_unclassified(hunt))`
   (best-effort) after the bang create / solve / retag paths, and add a `!rebuild`-style command
   accepting a `--round` flag (passes `show_round=True`). Mirror the `!clue` fire pattern (`:1031`).
- **Acceptance:** `timeout 60 pipenv run python -m pytest tests/test_unclassified_service.py -q > /tmp/test.log 2>&1`
  all pass; whole suite green; `pipenv run ruff check` clean.

## Phase 7 — web parity: fire refresh + create-unclassified endpoint (model: Sonnet 4.6)

1. `bot/web/actions.py` — fire `refresh_unclassified` from the web create/solve/retag edges (mirror
   the evidence fire-and-forget already added there).
2. Add a web endpoint to create an **unclassified (hunt-level) puzzle**: call
   `cog.service.create_puzzle(guild, hunt, None, name)` (round_data=None → no meta tag). Mirror the
   existing `create_mini` endpoint's auth/lock/shape.
- **Acceptance:** whole suite green; `pipenv run ruff check bot/web/actions.py` clean.

## Phase 8 — dashboard control (model: Sonnet 4.6)

1. `frontend/src/…` — a "create unclassified puzzle" control calling the Phase 7 endpoint (mirror the
   existing mini-create control). No frontend test infra exists.
- **Acceptance:** `cd frontend && npm run build` succeeds (tsc clean).

## Verification (final)

- `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` — all green.
- `pipenv run ruff check` — clean on all changed files.
- `cd frontend && npm run build` — clean.
- Re-check the migration number against `migrations/` for collisions before merge.
