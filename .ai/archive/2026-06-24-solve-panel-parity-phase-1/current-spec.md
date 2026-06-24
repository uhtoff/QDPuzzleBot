# Current Spec — Phase 1 of web↔bang parity: Solve panel completeness

Tool: Claude Code (handoff package)
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Target model per phase: **Phase 1 (tests) = Opus 4.8** · **Phases 2–4 (implementation) = Sonnet 4.6**
Started: 2026-06-24
Status: ready for handoff — Phase 1 (tests)

This is **Phase 1 of the 5-phase parity plan** in `.ai/plans/web-command-parity.md`
(read it for context only). The work here = give the web dashboard the full solve
family the bang commands have: **partial / change-solution / add-solution /
mark-complete**. The numbered phases BELOW are this handoff's own TDD phases (tests
first), not the parity plan's phases.

## Before you start (executing model)

- Execute only the **current** phase. Do not skip ahead.
- On any *unexpected* failure, invoke `superpowers:systematic-debugging` BEFORE
  changing anything.
- Before claiming a phase done, invoke `superpowers:verification-before-completion`
  and paste the acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear,
  understood fix, write `.ai/active/blocker.md` (what you tried, the exact output)
  and STOP.
- A test failing because a method doesn't exist yet is the RIGHT reason. A runtime
  fatal, a missing attribute, or an unknown payload key means a *wrong assumption in
  the test* — fix the test, don't add scaffolding.

## Goal

Three new ctx-free methods on `PuzzleService` (`bot/core/puzzle_service.py`) —
`add_partial`, `change_solution`, `add_solution` — that own the solution-mutation
logic currently inline in the Puzzles cog. The bang commands call them (behaviour
byte-identical); new web endpoints reuse them; the dashboard gets the controls.
`mark_as_complete` needs NO new method — it is `solve(..., "✅")`.

## In scope

- Phase 1: failing service-level tests for the three new methods (mirror
  `tests/test_solve_equivalence.py`).
- Phase 2: extract the three methods; rewire the bang commands to call them.
- Phase 3: web endpoint(s) for partial / change / add / mark-complete.
- Phase 4: frontend controls.

## Out of scope — do NOT touch

- Full `solve` / `unsolve` (already at parity). Do not alter `PuzzleService.solve`.
- Phases 2–5 of the parity plan (rename, link, clue UI, convert, delete, reconcile,
  hunt lifecycle, settings).
- Mini-specific solving (`!solve_mini`) — already reachable via the mini panel.
- Any new web-test or frontend-test harness — none exists; do NOT invent one
  (see Test constraints). Web/frontend are verified manually + by the service tests.

## Relevant files

- `bot/core/puzzle_service.py` — ADD the three methods here. `solve` (L523) is the
  contract to mirror; `refresh_metas` (L366), `refresh_meta`, `_gsheet_cog` (L40).
  (read first, then edit)
- `bot/cogs/puzzles.py` — `_partial_impl` (L2209), `change_solution` (L2300),
  `add_solution` (L2345), `mark_as_complete` (L2386), `_update_all_metas` (L2187),
  `_normalize_solution`. Rewire these in Phase 2. (read, then edit)
- `tests/test_solve_equivalence.py` — the seam + harness to copy. (read, then edit)
- `tests/conftest.py` — `make_puzzle`, `make_round` helpers. (read only)
- `tests/test_refresh_meta.py` — `_svc_with_sheets` pattern if a gsheet-present test
  is wanted. (read only)
- `bot/web/actions.py` — `solve_puzzle` (L316) reference; add route(s) in Phase 3.
- `frontend/src/api.ts`, `frontend/src/components/PuzzleDrawer*.tsx`,
  `frontend/src/components/MiniPuzzlePanel*.tsx` — Phase 4 controls.

## Constraints

- Don't touch files outside the Relevant files list. Don't refactor unrelated code.
- **No constructor / public-signature changes.** The new methods are additive. If you
  find yourself changing `PuzzleService.__init__` or `solve`'s signature, STOP — that
  is out of scope; write `blocker.md`.
- Behaviour parity is the contract: the bang commands must produce the SAME
  DB-state transitions after rewiring. `test_solve_equivalence.py` is the guard —
  run it after every Phase 2 edit.
- Run tests with the project runner only:
  `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`
  (a killed run exits 124; read the log). Tests need a reachable DB — a gitignored
  `config.json` (local `puzzlebot_test`) provides creds; if `import bot` fails on DB
  connect, that is an environment issue → `blocker.md`, do not mock around it.

## Test constraints (read — you do not get global instructions)

- **Reuse the exact harness in `test_solve_equivalence.py`** (the `_run`-style
  patcher that mocks `PuzzleJsonDb`/`RoundJsonDb`/`HuntJsonDb` in BOTH
  `bot.cogs.puzzles` and `bot.core.puzzle_service`). Invent no new harness.
- Call the seam directly: `await cog.service.add_partial(_no_guild(), hunt, puz, "X")`.
  `cog` comes from `_make_cog()`, `hunt` from `_make_hunt()`, `_no_guild()` returns
  None (skips Discord). `bot.get_cog` returns None, so `_gsheet_cog(hunt)` is None and
  the gsheet `update_solution` push is skipped — do NOT assert it in these tests.
- **Confirm every attribute before asserting it.** `make_puzzle`/`make_round` kwargs
  in use: `id, name, solved, status, solution, tags, metapuzzle, solve_time`. Grep
  `tests/conftest.py` to confirm before using any other kwarg.
- **NEVER write a loop that resolves state "until" a condition.** If tempted, STOP →
  `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says
  so. `solve_time` is set from `datetime.now(UTC)`; assert `is not None` / `>= before`,
  do not freeze the clock.
- **Each test file defines/imports every helper it calls.** Add new helpers in-file;
  do not reach into a sibling test module's top-level functions.
- **Wrap every run in `timeout`, write to a file, single statement** (no `; echo`
  tail). A run that reads as "slow" may be a hang — isolate per file.

## Phase 1 — write ALL failing tests (model: Opus 4.8). NO production code.

Append three test classes to `tests/test_solve_equivalence.py`, copying the existing
`_run` harness shape verbatim (the with-patch block). Skeletons below — fill in the
patch block exactly as the existing `TestWebAddToMeta._run` does, swapping the seam
call.

```python
# ---------------------------------------------------------------------------
# add_partial (service) — partial-solution mutation. Mirrors _partial_impl:
# status -> "Partially Solved", append "/"+solution if a solution exists else set;
# commit; refresh tagged metas. Does NOT set solve_time (divergence from solve).
# ---------------------------------------------------------------------------
class TestWebAddPartial:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, solution, rounds_by_id=None):
        # COPY the with-patch block from TestWebSolvePuzzleState._run verbatim,
        # but call:  await cog.service.add_partial(_no_guild(), hunt, puzzle, solution)
        ...

    @pytest.mark.asyncio
    async def test_sets_status_partially_solved(self, cog, hunt):
        puz = make_puzzle(status="in progress", solution=None)
        await self._run(cog, hunt, puz, "PART")
        assert puz.status == "Partially Solved"

    @pytest.mark.asyncio
    async def test_sets_solution_when_empty(self, cog, hunt):
        puz = make_puzzle(solution=None)
        await self._run(cog, hunt, puz, "PART")
        assert puz.solution == "PART"

    @pytest.mark.asyncio
    async def test_appends_with_slash_when_existing(self, cog, hunt):
        puz = make_puzzle(solution="FIRST")
        await self._run(cog, hunt, puz, "SECOND")
        assert puz.solution == "FIRST/SECOND"

    @pytest.mark.asyncio
    async def test_does_not_set_solve_time(self, cog, hunt):
        # Divergence from solve: partial leaves solve_time untouched.
        puz = make_puzzle(solution=None, solve_time=None)
        await self._run(cog, hunt, puz, "PART")
        assert puz.solve_time is None

    @pytest.mark.asyncio
    async def test_does_not_mark_solved(self, cog, hunt):
        puz = make_puzzle(solved=False, solution=None)
        await self._run(cog, hunt, puz, "PART")
        assert puz.solved is False


# ---------------------------------------------------------------------------
# change_solution (service) — already-solved branch of cog change_solution:
# set solution = new (replace), stamp solve_time, commit, refresh metas.
# (The unsolved fallback stays in the cog and is NOT this method's job.)
# ---------------------------------------------------------------------------
class TestWebChangeSolution:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, solution, rounds_by_id=None):
        # COPY the with-patch block; call:
        #   await cog.service.change_solution(_no_guild(), hunt, puzzle, solution)
        ...

    @pytest.mark.asyncio
    async def test_replaces_solution(self, cog, hunt):
        puz = make_puzzle(solved=True, solution="OLD")
        await self._run(cog, hunt, puz, "NEW")
        assert puz.solution == "NEW"

    @pytest.mark.asyncio
    async def test_stamps_solve_time(self, cog, hunt):
        before = datetime.datetime.now(tz=datetime.timezone.utc)
        puz = make_puzzle(solved=True, solution="OLD")
        await self._run(cog, hunt, puz, "NEW")
        assert puz.solve_time is not None and puz.solve_time >= before

    @pytest.mark.asyncio
    async def test_commits_puzzle(self, cog, hunt):
        puz = make_puzzle(id=42, solved=True, solution="OLD")
        commits = await self._run(cog, hunt, puz, "NEW")
        # _run must collect commits like TestWebSolvePuzzleState._run does.
        assert any(c[0] == "puzzle" and c[1] == 42 for c in commits)


# ---------------------------------------------------------------------------
# add_solution (service) — already-solved branch of cog add_solution:
# append "/"+solution, stamp solve_time, commit, refresh metas.
# ---------------------------------------------------------------------------
class TestWebAddSolution:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, solution, rounds_by_id=None):
        # COPY the with-patch block; call:
        #   await cog.service.add_solution(_no_guild(), hunt, puzzle, solution)
        ...

    @pytest.mark.asyncio
    async def test_appends_with_slash(self, cog, hunt):
        puz = make_puzzle(solved=True, solution="FIRST")
        await self._run(cog, hunt, puz, "SECOND")
        assert puz.solution == "FIRST/SECOND"

    @pytest.mark.asyncio
    async def test_stamps_solve_time(self, cog, hunt):
        before = datetime.datetime.now(tz=datetime.timezone.utc)
        puz = make_puzzle(solved=True, solution="FIRST")
        await self._run(cog, hunt, puz, "SECOND")
        assert puz.solve_time is not None and puz.solve_time >= before
```

**Phase 1 acceptance:**
`timeout 180 pipenv run python -m pytest tests/test_solve_equivalence.py -q > /tmp/test.log 2>&1`
- Exits non-zero. The NEW tests fail with `AttributeError`/`does not exist` on
  `cog.service.add_partial` / `change_solution` / `add_solution` (right reason).
- The existing tests in the file still PASS (you only added classes; you changed no
  production code). If an existing test now fails, you broke the harness copy — fix
  the test, not the app.
Commit: `test(parity): failing service tests for solve variants`.

## Phase 2 — extract service methods + rewire bang (model: Sonnet 4.6)

1. In `bot/core/puzzle_service.py`, add the three methods, each mirroring `solve`'s
   structure (mutate → `PuzzleJsonDb.commit(puzzle)` → refresh tagged metas with
   failure isolation → `notifier.board_changed(hunt)` → raise `SheetRefreshError`
   if any meta failed):
   - `add_partial(self, guild, hunt, puzzle, solution)`: `status="Partially Solved"`;
     `puzzle.solution = puzzle.solution + "/" + solution if puzzle.solution else solution`;
     commit; refresh metas; NO solve_time, NO gsheet push (match `_partial_impl`).
   - `change_solution(self, guild, hunt, puzzle, solution)`: `puzzle.solution = solution`;
     `solve_time = datetime.now(UTC)`; commit; refresh metas; then
     `gsheet = self._gsheet_cog(hunt); if gsheet: await gsheet.update_solution(puzzle)`.
   - `add_solution(self, guild, hunt, puzzle, solution)`: `puzzle.solution += "/" + solution`;
     `solve_time = now`; commit; refresh metas; gsheet `update_solution` (guarded).
   For meta refresh reuse the pattern in `_update_all_metas`:
   `await self.refresh_metas(hunt, [RoundJsonDb.get_by_attr(id=t) for t in puzzle.tags])`.
2. Rewire the cog: `_partial_impl` calls `self.service.add_partial(...)` then keeps its
   embed / `send_initial_puzzle_channel_messages` / `_warn_stale_sheets`.
   `change_solution` (already-solved branch) and `add_solution` (already-solved branch)
   call the service methods then keep their embeds + `get_gsheet_cog(ctx).update_solution`
   ONLY if the service no longer does it — to avoid a double sheet write, let the
   service own the gsheet push and DROP the cog's duplicate `update_solution` call.
   Keep the unsolved-fallbacks (`_solve_impl` / `_partial_impl`) exactly as they are.
3. **Acceptance:**
   `timeout 180 pipenv run python -m pytest tests/test_solve_equivalence.py tests/test_refresh_meta.py -q > /tmp/test.log 2>&1`
   → all green (new + existing). Then full suite:
   `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` → green.
   Commit: `feat(parity): extract solve-variant service methods, rewire bang`.

## Phase 3 — web endpoint(s) (model: Sonnet 4.6)

1. In `bot/web/actions.py`, extend the solve route. Add a `mode` to
   `POST /api/puzzles/<id>/solve`: `full` (default = current `solve`), `partial`,
   `change`, `add`. `mark`-complete = frontend sends `mode:"full", solution:"✅"`
   (no new mode). Dispatch to the matching `cog.service.*` method. Mirror
   `solve_puzzle`'s auth (guild member, hunt-not-archived), `puzzle_lock`, the
   `SheetRefreshError`/`HttpError` → `sheet_warning` handling, `_audit`, best-effort
   `refresh_unassigned_safe`, and `{ ok, sheet_warning? }` response. Validate
   empty-solution per the cog's guards (e.g. `change`/`add` reject empty).
2. **Acceptance:** `python3 -c "import ast; ast.parse(open('bot/web/actions.py').read())"`
   exits 0; full pytest suite still green. Manual: POST each mode against a dev puzzle
   (or defer to Phase 4 click-through). Commit: `feat(parity): web solve-variant modes`.

## Phase 4 — frontend controls (model: Sonnet 4.6)

1. `frontend/src/api.ts`: add the mode to the solve mutation payload type.
2. PuzzleDrawer (and MiniPuzzlePanel where it has solve): add controls for add-partial,
   change-solution, add-solution, mark-complete. Disable change/add unless already
   solved; disable partial/solve when solved — match the cog guards.
3. **Acceptance:** `cd frontend && timeout 120 npx tsc --noEmit` exits 0. Manual
   click-through: on a solved puzzle change the solution and append an extra; on an
   unsolved puzzle mark partial then mark complete. Commit:
   `feat(parity): solve-variant controls in dashboard`.

## Verification (whole handoff)

- Full suite green: `timeout 300 pipenv run python -m pytest tests/ -q`.
- `frontend` `tsc --noEmit` clean.
- Equivalence: bang and web produce identical DB state for each variant (the new
  service tests are the proof for the service seam).
- Manual dashboard click-through of all four variants.
