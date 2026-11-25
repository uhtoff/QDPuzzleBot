# Multiple answers — Stage 1: answer rows and the answer seam

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store each puzzle answer as its own row and route every answer write through one pure seam, with **no visible behaviour change**. `puzzles.solution` and `puzzles.solved` stay exactly as readers see them today.

**Architecture:** A new `puzzle_answers` table is hydrated onto `PuzzleData.answers`. It's a non-column attribute that `MySQLPuzzleJsonDb.commit` reconciles, the same way it already reconciles `tag_edges`. A pure module, `bot/core/answers.py`, mutates `puzzle.answers` and re-derives `puzzle.solution`, `expected_answers` and `expected_answers_confirmed` so that `solved ⇔ len(answers) ≥ expected_answers` holds after every write. The five existing writers in `PuzzleService` call it instead of editing the string. Service tests that mock `PuzzleJsonDb` need no new patches, because the seam never touches the database.

**Tech Stack:** Python 3.12, discord.py, MySQL 8 (CI) / MariaDB 10.11 (prod), pytest (`asyncio_mode = auto`), basedpyright, ruff.

**Spec:** `docs/superpowers/specs/2026-11-25-multiple-answers-design.md` (stage 1 of "Build order").

## Global Constraints

- Migration file is `migrations/063_puzzle_answers.sql` (latest on staging is 062). Before merging, check other worktrees for a clashing 063.
- No MariaDB-only syntax: no `ADD COLUMN IF NOT EXISTS`. CI runs MySQL 8.0, prod runs MariaDB 10.11. Full-line `--` comments only (see `migrate.py`).
- Rule: migration → deploy → code. The generic `commit()` writes every dataclass field by name, so new *columns* on `PuzzleData` must exist before the code ships. Only `answers` goes in `SPECIAL_ATTR`.
- The derived `puzzles.solution` is the answers in `position` order joined with `/`. A puzzle with `expected_answers = 0` and no answers reads `"✅"`. An unsolved puzzle with no answers reads `""`.
- Every answer is upper-cased and stripped. Empty fragments are dropped.
- The completion mark is the literal `"✅"`. It is never stored as an answer row.
- Tests: `timeout 180 pipenv run python -m pytest <files> -q > /tmp/test.log 2>&1`. Before pushing, also run `pipenv run basedpyright bot/` (0 errors) and `pipenv run ruff check`.
- Worktree bootstrap: copy the gitignored `config.json` and `google_secrets.json` from the main checkout before running tests. Without them conftest dies with MySQL access denied (exit 4). Apply 063 to `puzzlebot_test` with `pipenv run python migrate.py migrate`.
- Out of scope for this stage: `answer_placements` (stage 3, with its own backfill from `feeds` tags; *my call* to defer it rather than create an empty table now), `expected_feeders*` (stage 4), any command, dashboard or sheet change, and deriving solved from the count (stage 2).

## Review Focus

1. **An unhydrated `PuzzleData` being committed.** Any loader that builds a puzzle without `construct_puzzles` leaves `answers` as `None`. Commit must then leave the stored answer rows untouched, never delete them. Owned by Task 2.
2. **A typed `/` inside one answer** (`!solve A/B`, or `!change_solution` to `A / B`). It must produce two answer rows whose derived string is `A/B`. The only visible change allowed is `A / B` becoming `A/B` (whitespace trimmed). Owned by Task 3.
3. **`!mark_as_complete` on a puzzle that already has answers.** The ✅ must not be appended (today it would produce `X/✅`). The answers stay, and the puzzle is solved with expected = count. Owned by Task 3.
4. **A partially solved puzzle (`solved=0`, non-empty `solution`) through the migration.** It must stay unsolved, with expected = fragments + 1 and confirmed false. Owned by Task 1.
5. **`!partial` with an empty answer.** Today this appends a trailing `/`. The seam adds nothing, so the solution string is unchanged. The status still becomes partially solved and expected becomes count + 1. Owned by Task 3.

---

### Task 1: Migration 063

**Files:**
- Create: `migrations/063_puzzle_answers.sql`
- Test: `tests/test_puzzle_answers_migration.py` (LIVE `puzzlebot_test`)

**Interfaces:**
- Produces: table `puzzle_answers(id INT AUTO_INCREMENT PK, puzzle_id INT NOT NULL FK→puzzles.id ON DELETE CASCADE, answer VARCHAR(250) NOT NULL, position INT NOT NULL, no_meta TINYINT(1) NOT NULL DEFAULT 0, created_by BIGINT NULL, created_at DATETIME NULL, KEY (puzzle_id, position))`. The position key is deliberately not UNIQUE, so a later reorder can't collide on a swap (*my call*). `created_by` stays NULL until stage 2 passes the author.
- Produces: `puzzles.expected_answers INT NOT NULL DEFAULT 1` and `puzzles.expected_answers_confirmed TINYINT(1) NOT NULL DEFAULT 1`.

- [ ] **Step 1: Write the failing test.** The migration is plain SQL, so the test replays the backfill. It inserts puzzles, runs the backfill statements (read from the file, everything after the marker comment `-- BACKFILL`), and asserts the result. Use `split_statements` from `migrate.py` on that slice. Cases, each as its own test:
  - `test_backfill_splits_slash_joined_answers`: solved, `"APPLE/ PEAR"` → rows `["APPLE", "PEAR"]` at positions 1 and 2; expected 2, confirmed 1.
  - `test_backfill_single_answer`: solved, `"KIWI"` → one row; expected 1.
  - `test_backfill_tick_only_has_no_rows_and_expects_zero`: solved, `"✅"` → 0 rows; expected 0.
  - `test_backfill_partial_stays_unsolved`: `solved=0`, `status='partially solved'`, `"FIG"` → 1 row; expected 2, confirmed 0.
  - `test_backfill_unsolved_empty_untouched`: unsolved with NULL or `""` → 0 rows; expected 1, confirmed 1.
  - `test_backfill_drops_empty_fragments`: solved, `"A//B/"` → rows `["A", "B"]`; expected 2.
  - `test_backfill_is_rerunnable`: running the backfill twice leaves the same row count. The INSERT skips puzzles that already have answer rows.

- [ ] **Step 2: Run it to make sure it fails.** Run: `timeout 180 pipenv run python -m pytest tests/test_puzzle_answers_migration.py -q > /tmp/test.log 2>&1`. Expected: FAIL (file or table missing).

- [ ] **Step 3: Write `063_puzzle_answers.sql`.** Use 062's header style, including a PRE-FLIGHT block that must be run in every environment:
  - `information_schema` checks that the columns and table don't exist yet;
  - `SELECT MAX(LENGTH(solution)-LENGTH(REPLACE(solution,'/',''))+1) FROM puzzles` must be ≤ 20;
  - `SELECT MAX(CHAR_LENGTH(solution)) FROM puzzles` must be ≤ 250.

  Then add a POST-CHECK. Statement order: CREATE TABLE IF NOT EXISTS, then the two ALTERs, then `-- BACKFILL`, then the INSERT, then the two UPDATEs. Splitting is portable SQL: join `puzzles` to a derived numbers table (`SELECT 1 n UNION ALL … SELECT 20`) on `n ≤ fragment count`, and take the fragment with `TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(solution,'/',n),'/',-1))`. Skip `''` and `'✅'`. Number positions with `ROW_NUMBER()`, or by the `n` of the surviving fragments renumbered in a second pass. Both engines support window functions; pick whichever the test proves. Guard with `NOT EXISTS (SELECT 1 FROM puzzle_answers a WHERE a.puzzle_id = p.id)`. The UPDATEs:
  - solved puzzles: `expected_answers` = their row count, so ✅-only gives 0;
  - `solved=0` with a non-empty solution: row count + 1 and `expected_answers_confirmed = 0`.

  `created_at` = `solve_time` when set, else NULL.

- [ ] **Step 4: Apply and run.** `pipenv run python migrate.py migrate`, then the Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit.** `git add migrations/063_puzzle_answers.sql tests/test_puzzle_answers_migration.py && git commit -m "feat(answers): migration 063 — puzzle_answers + expected answer count"`

### Task 2: Store — `AnswerData`, hydration, commit reconcile

**Files:**
- Create: `bot/store/answer_data.py`
- Modify: `bot/store/puzzle_data.py` (`PuzzleData` fields), `bot/store/mysqldb.py` (`MySQLPuzzleJsonDb.SPECIAL_ATTR`, `construct_puzzles`, `commit`)
- Test: `tests/test_puzzle_answers_store.py` (LIVE, following `tests/test_tag_edges.py` conventions)

**Interfaces:**
- Produces: `@dataclass class AnswerData: id: int = 0; answer: str = ""; position: int = 0; no_meta: bool = False; created_by: Optional[int] = None; created_at: Optional[datetime.datetime] = None` (UTC-aware on read, like `*_time` in `import_dict`).
- Produces on `PuzzleData`:
  - `answers: Optional[List[AnswerData]] = None`, where `None` means "not loaded";
  - `expected_answers: int = 1`;
  - `expected_answers_confirmed: bool = True`.
- Produces: `MySQLPuzzleJsonDb._hydrate_answers(puzzles)`. It runs one `IN (...)` query, ordered by `position, id`, and sets `answers` to a list (possibly empty) on every puzzle. Call it from `construct_puzzles`.
- Produces: `MySQLPuzzleJsonDb.commit` reconciles `puzzle.answers` after the tags diff, **only when it is not None**. The steps:
  - delete this puzzle's rows whose id is not in the list;
  - UPDATE rows with id > 0 (`answer`, `position`, `no_meta`);
  - INSERT rows with id 0 and set their `.id` from `lastrowid`.

  It is non-atomic and self-correcting on the next commit, as the tags diff already is. Document this the same way.

- [ ] **Step 1: Write the failing tests:**
  - `test_hydrate_answers_in_position_order`: insert rows at positions 2 and 1, then `get_by_attr(id=…)` → `[a.answer for a in p.answers] == ["FIRST", "SECOND"]`.
  - `test_hydrate_answers_empty_list_not_none`: no rows → `p.answers == []`.
  - `test_commit_inserts_updates_and_deletes_answers`: load, append `AnswerData(answer="NEW", position=3)`, change row 1's text, drop row 2, commit, reload. The reloaded texts are exact, and the new row has a non-zero id.
  - `test_commit_with_answers_none_leaves_rows_untouched` (Review Focus 1): build a `PuzzleData` via `import_dict(row)` without hydrating (answers None), commit it, then reload. The rows are unchanged.
  - `test_expected_answer_columns_round_trip`: set 3/False, commit, reload → 3/False.

- [ ] **Step 2: Run to make sure they fail.** Expected: FAIL (no attribute `answers`).

- [ ] **Step 3: Implement** the fields, the `SPECIAL_ATTR` entry `'answers'`, `_hydrate_answers`, and the commit reconcile.

- [ ] **Step 4: Run these tests plus `tests/test_tag_edges.py` and `tests/test_backfill_consistency.py`.** Expected: PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(answers): AnswerData hydrated onto PuzzleData and reconciled on commit"`

### Task 3: The pure answer seam

**Files:**
- Create: `bot/core/answers.py`
- Modify: `bot/core/constants.py`, which gains `COMPLETE_MARK = "✅"`. The seam imports it; the existing literals in the cog and the TSX stay as they are this stage.
- Test: `tests/test_answers_seam.py` (pure, no DB)

**Interfaces** (all mutate the `PuzzleData` in place, never commit, and return `None` unless stated):
- `split_answers(text: str | None) -> list[str]`: split on `/`, strip, upper-case, drop empty fragments and `COMPLETE_MARK`.
- `ensure_loaded(puzzle) -> None`: if `puzzle.answers is None`, set it from `split_answers(puzzle.solution)` with positions 1..n. This is a fallback for mocked tests and unhydrated callers.
- `derive_solution(puzzle) -> str`:
  - answers joined with `/` in position order;
  - else `COMPLETE_MARK` if `puzzle.expected_answers == 0 and puzzle.solved`;
  - else `""`.
- `record_solve(puzzle, text: str) -> None`: the `solve()` semantics. If `text` is `COMPLETE_MARK` and there are no answers, set expected 0. Otherwise append `split_answers(text)`, then set expected = count and confirmed True. Set `solved = True` and re-derive `solution`.
- `record_partial(puzzle, text: str) -> None`: the `add_partial()` semantics. Append, set expected = count + 1 and confirmed False, set `solved = False`, re-derive.
- `record_add(puzzle, text: str) -> None`: the `add_solution()` semantics. Append, set expected = count, re-derive.
- `record_change(puzzle, text: str) -> None`: the `change_solution()` semantics. Replace all answers with `split_answers(text)` (new rows, id 0), set expected = count, re-derive.
- `record_unsolve(puzzle) -> None`: set answers to `[]`, expected 1, confirmed True, `solved = False`, `solution = ""`.
- New rows get `position = max(existing) + 1…` and `created_at = now(UTC)`.

- [ ] **Step 1: Write the failing tests.** Each builds a puzzle with `make_puzzle` and asserts `(solution, [answers], expected_answers, expected_answers_confirmed, solved)` exactly:
  - `test_solve_fresh`: `record_solve(p, "kiwi")` → `("KIWI", ["KIWI"], 1, True, True)`.
  - `test_solve_appends_to_partial`: partial `"FIG"`, then solve `"lemon"` → `("FIG/LEMON", ["FIG","LEMON"], 2, True, True)`.
  - `test_solve_typed_slash_makes_two_answers` (Review Focus 2): solve `"a / b"` → `("A/B", ["A","B"], 2, True, True)`.
  - `test_solve_with_empty_text_solves_existing_partial`: partial `"FIG"`, then solve `""` → `("FIG", ["FIG"], 1, True, True)`.
  - `test_complete_on_unanswered`: solve `"✅"` → `("✅", [], 0, True, True)`.
  - `test_complete_on_answered_does_not_append_tick` (Review Focus 3): partial `"FIG"`, then solve `"✅"` → `("FIG", ["FIG"], 1, True, True)`.
  - `test_partial_counts_one_more`: partial `"fig"` → `("FIG", ["FIG"], 2, False, False)`; a second partial `"lime"` → `("FIG/LIME", …, 3, False, False)`.
  - `test_partial_empty_adds_nothing` (Review Focus 5): partial `""` on a fresh puzzle → `("", [], 1, False, False)`.
  - `test_add_to_solved`: solved `"KIWI"`, add `"pear"` → `("KIWI/PEAR", …, 2, True, True)`.
  - `test_change_replaces_all`: solved `"A/B"`, change `"c"` → `("C", ["C"], 1, True, True)`, and the new row has id 0.
  - `test_unsolve_clears`: → `("", [], 1, True, False)`.
  - `test_ensure_loaded_from_legacy_string`: `answers=None`, `solution="X/Y"` → answers `["X","Y"]` at positions 1 and 2.
  - `test_invariant_holds_after_every_operation`: one sequence (partial, partial, solve, add, change, unsolve, solve ✅). After each step, assert `p.solved == (len(p.answers) >= p.expected_answers)` and `p.solution == derive_solution(p)`.

- [ ] **Step 2: Run to make sure they fail.** Expected: FAIL (module missing).

- [ ] **Step 3: Implement `bot/core/answers.py`** to the signatures above.

- [ ] **Step 4: Run.** Expected: PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(answers): pure answer seam keeping solution, solved and expected count in step"`

### Task 4: Route the five writers through the seam

**Files:**
- Modify: `bot/core/puzzle_service.py`. In `solve` (≈1751), `unsolve` (≈1865), `add_partial` (≈2446), `change_solution` (≈2466) and `add_solution` (≈2495), replace the direct `puzzle.solution` / `puzzle.solved` edits with `ensure_loaded(puzzle)` followed by the matching `record_*`. Everything else in those methods (status labels, solve_time, announcements using `old_solution`, refreshes, archive) is unchanged. `solve()` keeps its "empty text and no existing solution → return False" guard *before* calling the seam.
- Test: the existing `tests/test_solve_equivalence.py`, `tests/test_change_add_solution_impl.py`, `tests/test_solve_puzzle_web_invariants.py`, `tests/test_unsolve_web_warning.py` and `tests/test_web_solution_update_message.py` must pass **unchanged**. That is the "no visible change" gate. Add `tests/test_service_answer_seam.py` for the new state.

**Interfaces:**
- Consumes: Task 3's `ensure_loaded`, `record_solve`, `record_partial`, `record_add`, `record_change`, `record_unsolve`.

- [ ] **Step 1: Write the failing tests** in `tests/test_service_answer_seam.py`. Use the patch block from `tests/test_solve_equivalence.py` (patch `bot.core.puzzle_service.PuzzleJsonDb` etc.). Then:
  - `test_service_solve_records_answer_rows`: after `await service.solve(guild, hunt, p, "kiwi")`, `[a.answer for a in p.answers] == ["KIWI"]` and `p.expected_answers == 1`.
  - `test_service_partial_then_solve_counts`: `add_partial "fig"`, then `solve "lemon"` → answers `["FIG","LEMON"]`, expected 2, `p.solution == "FIG/LEMON"`.
  - `test_service_unsolve_clears_answers`: `p.answers == []`, `p.solution == ""`.
  - `test_service_commit_carries_answers`: the object passed to `PuzzleJsonDb.commit` has `answers` set (not None).

- [ ] **Step 2: Run them plus the five existing files.** Expected: the new tests FAIL, the existing ones PASS.

- [ ] **Step 3: Implement** the five call-site swaps.

- [ ] **Step 4: Run the new file, the five existing files, then the whole suite.** `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`. Expected: all PASS. If a pre-existing test fails, report it rather than changing it. Any change to an existing test's assertions needs a stated reason.

- [ ] **Step 5: Gates.** `pipenv run basedpyright bot/` → 0 errors; `pipenv run ruff check` → clean.

- [ ] **Step 6: Commit.** `git commit -m "refactor(answers): solve/partial/add/change/unsolve write through the answer seam"`

### Task 5: Docs touch-up

**Files:**
- Modify: `docs/PLANS.md`. In the three superseded rows ("Multiple answers, step 1", "step 2", "feeders worktable"), point to the spec and mark stage 1 done with its commit.

- [ ] **Step 1:** Edit those rows and commit: `git commit -m "docs(plans): multiple answers stage 1 done; rows point at the spec"`

## Verification (stage end)

- Full suite green, basedpyright at 0, ruff clean.
- On `puzzlebot_test` after migrating: `SELECT COUNT(*) FROM puzzles p WHERE p.solution <> '' AND p.solution <> '✅' AND NOT EXISTS (SELECT 1 FROM puzzle_answers a WHERE a.puzzle_id = p.id)` → 0.
- Manual spot check: `!solve`, `!partial`, `!add_solution`, `!change_solution`, `!unsolve` and `!mark_as_complete` on test (puzzbot.com) show exactly the same messages and sheet values as before.

## Rollback

063 only adds a table and columns. Code from before stage 1 ignores them. Because the deploy hook runs migrate first, you roll back by reverting the code commits. Leave the table in place, since dropping it loses nothing that the solution string doesn't still hold.

**Rolling forward again after a rollback:** while the old code runs, it edits `puzzles.solution` without touching the answer rows, so those rows go stale. Before re-deploying stage 1, run `DELETE FROM puzzle_answers` and re-run 063's backfill section by hand. The INSERT is guarded per puzzle, so it refills everything.
