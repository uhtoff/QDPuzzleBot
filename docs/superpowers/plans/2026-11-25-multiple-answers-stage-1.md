# Multiple answers — Stage 1: answer rows and the answer seam

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store each puzzle answer as its own row and route every answer write through one pure seam, with **no visible behaviour change**. `puzzles.solution` and `puzzles.solved` stay exactly as readers see them today.

**Architecture:** A new `puzzle_answers` table is hydrated onto `PuzzleData.answers`. A pure module, `bot/core/answers.py`, mutates `puzzle.answers`, marks it dirty, and re-derives `puzzle.solution`, `expected_answers` and `expected_answers_confirmed` so that `solved ⇔ len(answers) ≥ expected_answers` holds after every write. `MySQLPuzzleJsonDb.commit` writes the answer rows **only when the seam marked them dirty**. The five existing writers in `PuzzleService` call the seam instead of editing the string. Service tests that mock `PuzzleJsonDb` need no new patches, because the seam never touches the database.

**In this stage the rows shadow the string.** `puzzles.solution` is still the authority: whenever the seam finds the rows and the string disagree, it rebuilds the rows from the string before applying the write. That covers a solve made by old code between the backfill and the restart, a rollback, and a stale whole-object commit. The heal is removed in stage 3, when answer ids start to carry placements (see "Carried forward").

**Tech Stack:** Python 3.12, discord.py, MySQL 8 (CI) / MariaDB 10.11 (prod), pytest (`asyncio_mode = auto`), basedpyright, ruff.

**Spec:** `docs/superpowers/specs/2026-11-25-multiple-answers-design.md` (stage 1 of "Build order").

## Global Constraints

- Migration files are `migrations/064_puzzle_answers.sql` (schema) and `migrations/065_puzzle_answers_backfill.sql` (data). Latest on staging is `063_puzzle_solved_by.sql`. Before merging, check other worktrees for a clashing 064/065.
- No MariaDB-only syntax: no `ADD COLUMN IF NOT EXISTS`. CI runs MySQL 8.0, prod runs MariaDB 10.11. Full-line `--` comments only (see `migrate.py`).
- Rule: migration → deploy → code. The generic `commit()` writes every instance attribute by name, so new *columns* on `PuzzleData` must exist before the code ships. `answers` and `answers_dirty` are not columns and both go in `SPECIAL_ATTR`.
- The derived `puzzles.solution` is the answers in `position` order joined with `/`. A solved puzzle with `expected_answers = 0` and no answers reads `"✅"`. An unsolved puzzle with no answers reads `""`.
- Every answer is upper-cased and stripped. Empty fragments are dropped.
- The completion mark is the literal `"✅"`. It is never stored as an answer row.
- `created_by` is the adder's **display name** (`VARCHAR(255) NULL`), the same convention as `puzzles.solved_by` (migration 063). It is not a Discord id.
- Find code by the quoted text in each task, not by line number. A quoted anchor that is missing is a blocker to report, not something to guess around.
- Tests: `timeout 180 pipenv run python -m pytest <files> -q > /tmp/test.log 2>&1`. Before pushing, also run `pipenv run basedpyright bot/` (0 errors) and `pipenv run ruff check`.
- Out of scope for this stage: `answer_placements` (stage 3, with its own backfill from `feeds` tags; *my call* to defer it rather than create an empty table now), `expected_feeders*` (stage 4), any command, dashboard or sheet change, and deriving solved from the count (stage 2).

## Review Focus

1. **Rows and string disagree** (a solve by old code in the deploy window, a rollback, a stale commit). The next answer write must start from the string, so no answer the user can see is dropped. Owned by Task 3.
2. **A commit that the seam did not mark dirty** (a rename, a status change, a stale object, a newly built `PuzzleData` with `answers = None`). It must leave the stored answer rows untouched. Owned by Task 2.
3. **A typed `/` inside one answer** (`!solve A/B`, or `!change_solution` to `A / B`). It must produce two answer rows whose derived string is `A/B`. The only visible change allowed is `A / B` becoming `A/B` (whitespace trimmed). Owned by Task 3.
4. **`!mark_as_complete` on a puzzle that already has answers.** The ✅ must not be appended (today it would produce `X/✅`). The answers stay, and the puzzle is solved with expected = count. Owned by Task 3.
5. **A partially solved puzzle (`solved=0`, non-empty `solution`) through the migration.** It must stay unsolved, with expected = fragments + 1 and confirmed false. Owned by Task 1.

---

### Task 0: Rebase and bootstrap

- [ ] **Step 1: Rebase onto local staging.** The branch holds docs commits only and is unpushed: `git rebase staging`. Confirm `ls migrations | tail -1` prints `063_puzzle_solved_by.sql`.
- [ ] **Step 2: Bootstrap the worktree.** Copy the gitignored `config.json` and `google_secrets.json` from the main checkout. Without them conftest dies with MySQL access denied (exit 4).
- [ ] **Step 3: Baseline.** `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`. Record the pass count; it is the reference for Task 4.

### Task 1: Migrations 064 (schema) and 065 (backfill)

Two files, so a backfill failure can never leave a half-applied file that dies on "duplicate column" at the next deploy.

**Files:**
- Create: `migrations/064_puzzle_answers.sql`, `migrations/065_puzzle_answers_backfill.sql`
- Test: `tests/test_puzzle_answers_migration.py` (LIVE `puzzlebot_test`)

**Interfaces:**
- Produces: table `puzzle_answers(id INT AUTO_INCREMENT PK, puzzle_id INT NOT NULL FK→puzzles.id ON DELETE CASCADE, answer VARCHAR(250) NOT NULL, position INT NOT NULL, no_meta TINYINT(1) NOT NULL DEFAULT 0, created_by VARCHAR(255) NULL, created_at DATETIME NULL, KEY (puzzle_id, position))`. The position key is deliberately not UNIQUE, so a later reorder can't collide on a swap (*my call*).
- Produces: `puzzles.expected_answers INT NOT NULL DEFAULT 1` and `puzzles.expected_answers_confirmed TINYINT(1) NOT NULL DEFAULT 1`.

- [ ] **Step 1: Write the failing test.** The test inserts puzzles, executes every statement of `065_puzzle_answers_backfill.sql` (`split_statements` from `migrate.py` on the file text), and asserts the result. Cases, each as its own test:
  - `test_backfill_splits_slash_joined_answers`: solved, `"APPLE/ PEAR"` → rows `["APPLE", "PEAR"]` at positions 1 and 2; expected 2, confirmed 1.
  - `test_backfill_single_answer`: solved, `"KIWI"` → one row; expected 1.
  - `test_backfill_tick_only_has_no_rows_and_expects_zero`: solved, `"✅"` → 0 rows; expected 0.
  - `test_backfill_partial_stays_unsolved` (Review Focus 5): `solved=0`, `status='partially solved'`, `"FIG"` → 1 row; expected 2, confirmed 0.
  - `test_backfill_unsolved_empty_untouched`: unsolved with NULL or `""` → 0 rows; expected 1, confirmed 1.
  - `test_backfill_drops_empty_fragments`: solved, `"A//B/"` → rows `["A", "B"]` at positions 1 and 2; expected 2.
  - `test_backfill_is_rerunnable`: running the file twice leaves the same rows and the same counts.

- [ ] **Step 2: Run it to make sure it fails.** Run: `timeout 180 pipenv run python -m pytest tests/test_puzzle_answers_migration.py -q > /tmp/test.log 2>&1`. Expected: FAIL (file or table missing).

- [ ] **Step 3: Write `064_puzzle_answers.sql`.** Use 063's header style. Exactly two statements, so a rerun after a failure at either point succeeds: `CREATE TABLE IF NOT EXISTS puzzle_answers (…)`, then **one** `ALTER TABLE puzzles ADD COLUMN expected_answers …, ADD COLUMN expected_answers_confirmed …`. PRE-FLIGHT (run in every environment before pushing): `information_schema` checks that the two columns and the table don't exist yet.

- [ ] **Step 4: Write `065_puzzle_answers_backfill.sql`.** Header carries:
  - PRE-FLIGHT, all must hold before pushing:
    - `SELECT MAX(LENGTH(solution)-LENGTH(REPLACE(solution,'/',''))+1) FROM puzzles` ≤ 20;
    - `SELECT MAX(CHAR_LENGTH(solution)) FROM puzzles` ≤ 250;
    - `SELECT COUNT(*) FROM puzzles WHERE solved = 1 AND (solution IS NULL OR solution = '')` — note the number. These become expected 0 and will read `✅` after their next answer write.
  - POST-CHECK, both must return 0 (or every returned row explained by an empty middle fragment such as `A//B`):
    - `SELECT COUNT(*) FROM puzzles p WHERE p.solution <> '' AND p.solution <> '✅' AND NOT EXISTS (SELECT 1 FROM puzzle_answers a WHERE a.puzzle_id = p.id)`;
    - `SELECT COUNT(*) FROM puzzles p JOIN (SELECT puzzle_id, GROUP_CONCAT(answer ORDER BY position SEPARATOR '/') j FROM puzzle_answers GROUP BY puzzle_id) a ON a.puzzle_id = p.id WHERE REPLACE(a.j,' ','') <> REPLACE(TRIM(BOTH '/' FROM p.solution),' ','')`.
  - The line "Re-runnable only until stage 2 ships: the UPDATEs below would overwrite counts people have set."

  Statements: the INSERT, then two UPDATEs. Splitting is portable SQL: join `puzzles` to a derived numbers table (`SELECT 1 n UNION ALL … SELECT 20`) on `n ≤ fragment count`, and take the fragment with `TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(solution,'/',n),'/',-1))`. Skip `''` and `'✅'`. `position` = `ROW_NUMBER() OVER (PARTITION BY p.id ORDER BY n)` over the surviving fragments. Guard with `NOT EXISTS (SELECT 1 FROM puzzle_answers a WHERE a.puzzle_id = p.id)`. `created_at` = `solve_time` when set, else NULL. `created_by` stays NULL. The UPDATEs:
  - solved puzzles: `expected_answers` = their row count, so ✅-only gives 0;
  - `solved=0` with at least one answer row: row count + 1 and `expected_answers_confirmed = 0`.

- [ ] **Step 5: Apply and run.** `pipenv run python migrate.py migrate`, then the Step 2 command. Expected: PASS.

- [ ] **Step 6: Commit.** `git add migrations/064_puzzle_answers.sql migrations/065_puzzle_answers_backfill.sql tests/test_puzzle_answers_migration.py && git commit -m "feat(answers): migrations 064/065 — puzzle_answers + expected answer count"`

### Task 2: Store — `AnswerData`, hydration, dirty-gated commit

**Files:**
- Create: `bot/store/answer_data.py`
- Modify: `bot/store/puzzle_data.py` (`PuzzleData` fields), `bot/store/mysqldb.py` (`MySQLPuzzleJsonDb.SPECIAL_ATTR`, `construct_puzzles`, `commit`)
- Test: `tests/test_puzzle_answers_store.py` (LIVE, following `tests/test_tag_edges.py` conventions)

**Interfaces:**
- Produces: `@dataclass class AnswerData: id: int = 0; answer: str = ""; position: int = 0; no_meta: bool = False; created_by: Optional[str] = None; created_at: Optional[datetime.datetime] = None` (UTC-aware on read, like `*_time` in `import_dict`).
- Produces on `PuzzleData`:
  - `answers: Optional[List[AnswerData]] = None`, where `None` means "not loaded";
  - `answers_dirty: bool = False` — transient, set only by the answer seam;
  - `expected_answers: int = 1`;
  - `expected_answers_confirmed: bool = True`.
- Produces: `MySQLPuzzleJsonDb._hydrate_answers(puzzles)`. It runs one `IN (...)` query, ordered by `position, id`, and sets `answers` to a list (possibly empty) on every puzzle. Call it from `construct_puzzles`.
- Produces: `MySQLPuzzleJsonDb.commit` reconciles `puzzle.answers` after the tags diff, **only when `puzzle.answers is not None and puzzle.answers_dirty`**, then sets `answers_dirty = False`. The steps:
  - delete this puzzle's rows whose id is not in the list;
  - UPDATE rows with id > 0 (`answer`, `position`, `no_meta`);
  - INSERT rows with id 0 and set their `.id` from `lastrowid`.

  It is non-atomic and self-correcting on the next dirty commit, as the tags diff already is. Document this the same way, and document why it is gated: an ordinary commit must cost no extra queries, and a stale object must not be able to delete answers.

- [ ] **Step 1: Write the failing tests:**
  - `test_hydrate_answers_in_position_order`: insert rows at positions 2 and 1, then `get_by_attr(id=…)` → `[a.answer for a in p.answers] == ["FIRST", "SECOND"]`.
  - `test_hydrate_answers_empty_list_not_none`: no rows → `p.answers == []` and `p.answers_dirty is False`.
  - `test_commit_inserts_updates_and_deletes_answers`: load a puzzle with three rows, append `AnswerData(answer="NEW", position=4)`, change row 1's text, drop row 2, set `answers_dirty = True`, commit, reload. The reloaded texts are exactly `[changed, third, "NEW"]`, rows 1 and 3 keep their ids, the new row has a non-zero id, and `answers_dirty` is False after the commit.
  - `test_commit_skips_answers_when_not_dirty` (Review Focus 2): load, change a row's text and drop another **without** setting the flag, commit, reload → rows unchanged.
  - `test_commit_with_answers_none_leaves_rows_untouched` (Review Focus 2): set `p.answers = None` and `answers_dirty = True`, commit, reload → rows unchanged.
  - `test_expected_answer_columns_round_trip`: set 3/False, commit, reload → 3/False.

- [ ] **Step 2: Run to make sure they fail.** Expected: FAIL (no attribute `answers`).

- [ ] **Step 3: Implement** the fields, the `SPECIAL_ATTR` entries `'answers'` and `'answers_dirty'`, `_hydrate_answers`, and the gated reconcile.

- [ ] **Step 4: Run these tests plus `tests/test_tag_edges.py` and `tests/test_backfill_consistency.py`.** Expected: PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(answers): AnswerData hydrated onto PuzzleData; commit writes rows only when dirty"`

### Task 3: The pure answer seam

**Files:**
- Create: `bot/core/answers.py`
- Modify: `bot/core/constants.py`, which gains `COMPLETE_MARK = "✅"`. The seam imports it; the existing literals in the cog and the TSX stay as they are this stage.
- Test: `tests/test_answers_seam.py` (pure, no DB)

**Interfaces** (all mutate the `PuzzleData` in place and never commit; every `record_*` calls `ensure_loaded` first, sets `answers_dirty = True`, and returns `None`):
- `split_answers(text: str | None) -> list[str]`: split on `/`, strip, upper-case, drop empty fragments and `COMPLETE_MARK`.
- `ensure_loaded(puzzle) -> list[AnswerData]`: returns `puzzle.answers`, never None (callers use the return value, so basedpyright needs no narrowing).
  - `answers is None` → build from `split_answers(puzzle.solution)`, positions 1..n.
  - `[a.answer.strip().upper() for a in answers] != split_answers(puzzle.solution)` → the string wins: set the texts from the string by position (the same id-preserving replace as `record_change`), set `answers_dirty = True`, and `logger.warning` the puzzle id with both versions.
  - otherwise leave them alone.
- `derive_solution(puzzle) -> str`:
  - answers joined with `/` in position order;
  - else `COMPLETE_MARK` if `puzzle.expected_answers == 0 and puzzle.solved`;
  - else `""`.
- `record_solve(puzzle, text: str, *, by: str | None = None) -> None`: the `solve()` semantics. If `text` is `COMPLETE_MARK` and there are no answers, set expected 0. Otherwise append `split_answers(text)`, then set expected = count and confirmed True. Set `solved = True` and re-derive `solution`.
- `record_partial(puzzle, text: str, *, by: str | None = None) -> None`: the `add_partial()` semantics. Append, set expected = count + 1 and confirmed False, set `solved = False`, re-derive.
- `record_add(puzzle, text: str, *, by: str | None = None) -> None`: the `add_solution()` semantics. Append, set expected = count, re-derive. Leaves `solved` alone: both edges only reach it on a solved puzzle.
- `record_change(puzzle, text: str, *, by: str | None = None) -> None`: the `change_solution()` semantics. Replace **by position, keeping ids**: for the overlapping positions set `.answer` on the existing row (id, position, `created_by`, `created_at` kept); drop surplus rows; append the extras as new rows. Then expected = count, re-derive. Leaves `solved` alone, as `record_add` does. Accepted oddity: a change to text with no answer in it (`"/"`) on a solved puzzle leaves no rows, expected 0, and reads `✅`.
- `record_unsolve(puzzle) -> None`: set answers to `[]`, expected 1, confirmed True, `solved = False`, `solution = ""`.
- New rows get `id = 0`, `position = max(existing) + 1…`, `created_at = now(UTC)`, `created_by = by`.

- [ ] **Step 1: Write the failing tests.** Each builds a puzzle with `make_puzzle` and asserts `(solution, [answers], expected_answers, expected_answers_confirmed, solved)` exactly, and that `answers_dirty` is True:
  - `test_solve_fresh`: `record_solve(p, "kiwi", by="Ann")` → `("KIWI", ["KIWI"], 1, True, True)`, and the row's `created_by == "Ann"`.
  - `test_solve_appends_to_partial`: partial `"FIG"`, then solve `"lemon"` → `("FIG/LEMON", ["FIG","LEMON"], 2, True, True)`.
  - `test_solve_typed_slash_makes_two_answers` (Review Focus 3): solve `"a / b"` → `("A/B", ["A","B"], 2, True, True)`.
  - `test_solve_with_empty_text_solves_existing_partial`: partial `"FIG"`, then solve `""` → `("FIG", ["FIG"], 1, True, True)`.
  - `test_complete_on_unanswered`: solve `"✅"` → `("✅", [], 0, True, True)`.
  - `test_complete_on_answered_does_not_append_tick` (Review Focus 4): partial `"FIG"`, then solve `"✅"` → `("FIG", ["FIG"], 1, True, True)`.
  - `test_partial_counts_one_more`: partial `"fig"` → `("FIG", ["FIG"], 2, False, False)`; a second partial `"lime"` → `("FIG/LIME", …, 3, False, False)`.
  - `test_partial_empty_adds_nothing`: partial `""` on a fresh puzzle → `("", [], 1, False, False)`. (Today this appends a trailing `/`.)
  - `test_add_to_solved`: solved `"KIWI"`, add `"pear"` → `("KIWI/PEAR", …, 2, True, True)`.
  - `test_change_keeps_ids_by_position`: solved, rows `A` (id 11) and `B` (id 12). Change `"a/c"` → texts `["A","C"]`, ids `[11, 12]`. Change `"x/y/z"` → ids `[11, 12, 0]`, positions `[1, 2, 3]`. Change `"q"` → texts `["Q"]`, ids `[11]`, `("Q", ["Q"], 1, True, True)`.
  - `test_unsolve_clears`: → `("", [], 1, True, False)`.
  - `test_ensure_loaded_from_legacy_string`: `answers=None`, `solution="X/Y"` → answers `["X","Y"]` at positions 1 and 2.
  - `test_ensure_loaded_string_wins_on_drift` (Review Focus 1): rows `["FIG"]` (id 5), `solution="FIG/LEMON"`, solved → after `record_add(p, "pear")` the solution is `"FIG/LEMON/PEAR"`, the first row still has id 5, and a warning was logged (`caplog`).
  - `test_ensure_loaded_leaves_matching_rows_alone`: rows `["APPLE","PEAR"]`, `solution="APPLE/ PEAR"` → `ensure_loaded` returns the same row objects, `answers_dirty` stays False, nothing logged.
  - `test_invariant_holds_after_every_operation`: one sequence (partial, partial, solve, add, change, unsolve, solve ✅). After each step, assert `p.solved == (len(p.answers) >= p.expected_answers)` and `p.solution == derive_solution(p)`.

- [ ] **Step 2: Run to make sure they fail.** Expected: FAIL (module missing).

- [ ] **Step 3: Implement `bot/core/answers.py`** to the signatures above.

- [ ] **Step 4: Run.** Expected: PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(answers): pure answer seam keeping solution, solved and expected count in step"`

### Task 4: Route the five writers through the seam

**Files:**
- Modify: `bot/core/puzzle_service.py`. Replace the direct `puzzle.solution` / `puzzle.solved` edits with the matching `record_*`, found by this quoted text:
  - `solve`: the block from `if puzzle.solution and solution:` through `puzzle.solved = True` → `record_solve(puzzle, solution, by=solved_by)`. The guard `if solution == "" and puzzle.solution in [None, ""]: return False` stays *before* it, and `puzzle.solved_by = solved_by` stays.
  - `unsolve`: `puzzle.solution = ""` and `puzzle.solved = False` → `record_unsolve(puzzle)`. `puzzle.solved_by = None` stays.
  - `add_partial`: the `if puzzle.solution:` / `else:` block → `record_partial(puzzle, solution)`.
  - `change_solution`: `puzzle.solution = solution` → `record_change(puzzle, solution)`. Its announcement then compares and sends `puzzle.solution` (the derived string), not the raw `solution`.
  - `add_solution`: `puzzle.solution += "/" + solution` → `record_add(puzzle, solution)`.

  Everything else in those methods (status labels, solve_time, `old_solution` capture, refreshes, archive) is unchanged.
- Test: the existing `tests/test_solve_equivalence.py`, `tests/test_change_add_solution_impl.py`, `tests/test_solve_puzzle_web_invariants.py`, `tests/test_unsolve_web_warning.py`, `tests/test_web_solution_update_message.py` and `tests/test_solved_by.py` must pass **unchanged**. That is the "no visible change" gate. Add `tests/test_service_answer_seam.py` for the new state.

**Interfaces:**
- Consumes: Task 3's `record_solve`, `record_partial`, `record_add`, `record_change`, `record_unsolve`.

- [ ] **Step 1: Write the failing tests** in `tests/test_service_answer_seam.py`. Use the patch block from `tests/test_solve_equivalence.py` (patch `bot.core.puzzle_service.PuzzleJsonDb` etc.). Then:
  - `test_service_solve_records_answer_rows`: after `await service.solve(guild, hunt, p, "kiwi", solved_by="Ann")`, `[a.answer for a in p.answers] == ["KIWI"]`, `p.answers[0].created_by == "Ann"` and `p.expected_answers == 1`.
  - `test_service_partial_then_solve_counts`: `add_partial "fig"`, then `solve "lemon"` → answers `["FIG","LEMON"]`, expected 2, `p.solution == "FIG/LEMON"`.
  - `test_service_unsolve_clears_answers`: `p.answers == []`, `p.solution == ""`.
  - `test_service_commit_carries_dirty_answers`: capture the state inside a `PuzzleJsonDb.commit` side effect; at the first commit of a solve, `answers` is a list and `answers_dirty` is True.

- [ ] **Step 2: Run them plus the six existing files.** Expected: the new tests FAIL, the existing ones PASS.

- [ ] **Step 3: Implement** the five call-site swaps.

- [ ] **Step 4: Run the new file, the six existing files, then the whole suite.** `timeout 600 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`. Expected: all PASS, count = Task 0 baseline + the new tests. If a pre-existing test fails, report it rather than changing it. Any change to an existing test's assertions needs a stated reason.

- [ ] **Step 5: Gates.** `pipenv run basedpyright bot/` → 0 errors; `pipenv run ruff check` → clean.

- [ ] **Step 6: Commit.** `git commit -m "refactor(answers): solve/partial/add/change/unsolve write through the answer seam"`

### Task 5: Docs touch-up

**Files:**
- Modify: `docs/PLANS.md`. In the three superseded rows ("Multiple answers, step 1", "step 2", "feeders worktable"), point to the spec and mark stage 1 done with its commit.

- [ ] **Step 1:** Edit those rows and commit: `git commit -m "docs(plans): multiple answers stage 1 done; rows point at the spec"`

## Verification (stage end)

- Full suite green, basedpyright at 0, ruff clean.
- On `puzzlebot_test` after migrating: both POST-CHECK queries from 065 return 0.
- Manual spot check: `!solve`, `!partial`, `!add_solution`, `!change_solution`, `!unsolve` and `!mark_as_complete` on test (puzzbot.com) show exactly the same messages and sheet values as before.

## Rollback

064 only adds a table and columns, and code from before stage 1 ignores them. Roll back by reverting the code commits and leave the table in place. While old code runs it edits `puzzles.solution` without touching the answer rows, so those rows go stale. Nothing needs doing about that before re-deploying: the seam rebuilds a puzzle's rows from the string on its next answer write.

## Carried forward

- **Before stage 2 derives solved from the count — not only before stage 3 removes the heal —** rows and counts that drifted and were never written again are still stale. For example, an old-code solve in the deploy window or during a rollback leaves `solved = 1`, the string `X`, no rows and expected 1, which a count-derived reader would see as unsolved. Stage 2 must either derive through `ensure_loaded` (the string wins), or run 065's second POST-CHECK query and repair what it lists from the string before it ships. Stage 3 then makes the rows the authority. Normalise legacy lowercase rows in the same repair, because 065 copied fragments without `UPPER`.
- `created_by` is filled only by `solve()` in this stage (it has `solved_by`). Stage 2 passes the author on the other paths.
- **`!update_puzzle_setting` can still write `solution`, `solved` and `expected_answers*` directly** (`bot/cogs/puzzles.py`, `update_settings`). In stage 1 the next seam write heals this. Once stage 2 derives solved, a raw write breaks `solved ⇔ len(answers) ≥ expected_answers` and nothing repairs it. Stage 2 must refuse these keys through `_UNSTORED_SETTING_KEYS`, or route them through the seam.
- **`tests/test_puzzle_answers_migration.py` re-runs 065's table-wide UPDATEs on `puzzlebot_test`.** It must be retired or scoped when stage 2 ships, on the same deadline as 065's own "re-runnable only until stage 2" note.
- **Small visible changes accepted in stage 1, beyond the three in Review Focus:**
  - adding an answer to a ✅-only puzzle now reads `X`, not `✅/X`;
  - `!solve /` on a fresh puzzle reads `✅`, and `!solve A/` loses its trailing `/`;
  - `!show_puzzle_settings` also lists `answers`, `answers_dirty` and `expected_*`.
- **A single answer longer than 250 characters** fails the answer-row INSERT after the puzzles row has committed. The command errors, the string is kept, and the rows heal on the next write. Revisit this if the rows become the authority.
