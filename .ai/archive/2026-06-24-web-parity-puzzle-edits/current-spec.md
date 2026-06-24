# Current Spec — Phase 2 of web↔bang parity: Puzzle edits

Tool: Claude Code (handoff package)
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Target model per phase: **Phase 1 (tests) = Opus 4.8** · **Phases 2–6 (implementation) = Sonnet 4.6**
Started: 2026-06-24
Status: ready for handoff — Phase 1 (tests)

Source design (approved): `docs/superpowers/specs/2026-06-24-web-parity-puzzle-edits-design.md`.
Roadmap: `.ai/plans/web-command-parity.md` (this is **Phase 2** of that 5-phase plan).
Predecessor: Phase 1 (solve panel) merged @ `1ca094c`; its handoff is the structural
template — see `.ai/archive/2026-06-24-solve-panel-parity-phase-1/`.

The numbered phases BELOW are this handoff's own TDD phases (tests first), NOT the
parity plan's phases.

## Before you start (executing model)

- Execute only the **current** phase. Do not skip ahead.
- On any *unexpected* failure, invoke `superpowers:systematic-debugging` BEFORE changing
  anything.
- Before claiming a phase done, invoke `superpowers:verification-before-completion` and
  paste the acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood
  fix, write `.ai/active/blocker.md` (what you tried, the exact output) and STOP.
- A test failing because a method doesn't exist yet is the RIGHT reason. A runtime fatal,
  a missing attribute, or an unknown payload key means a *wrong assumption in the test* —
  fix the test, don't add scaffolding.
- The four deferred design decisions are RESOLVED in `.ai/active/open-questions.md` (auth gates,
  the `safe_rename` relocation, the `set_channel_type` non-issue). Follow them; do not
  re-litigate or re-guess.

## Goal

Give the web dashboard equivalents of five Tier-A puzzle-edit bang commands: `!link`,
`!clue`, `!make_minimeta`, `!convert_to_meta`, `!rename_puzzle`/`!rename_meta`. Each flows
through the thin-controller pattern: ctx-free `PuzzleService` method → thin Quart endpoint
→ dashboard control. Bang behaviour stays byte-identical; the new endpoints reuse the same
service seams; the dashboard gets the controls.

Two seams already exist (`log_clues`, `make_minimeta`); three must be **extracted**
(`set_puzzle_url`, `convert_to_meta`, `rename_puzzle`).

## In scope

- Phase 1: failing service-level **equivalence** tests for the three extracted methods.
- Phase 2: extract `set_puzzle_url`; web `/link` endpoint + hook (simplest cascade first).
- Phase 3: web `/clues` endpoint + hook (reuses existing `log_clues`/`refresh_evidence`).
- Phase 4: web `/make-minimeta` endpoint + hook (reuses existing `make_minimeta`).
- Phase 5: extract `convert_to_meta`; web `/convert-to-meta` endpoint + hook.
- Phase 6: extract `rename_puzzle`; web `/rename` endpoint + hook (riskiest, last).

Commit order = simplest cascade → most complex (per the design doc).

## Out of scope — do NOT touch

- **`convert_to_feeder`** — destructive (deletes the meta's round, orphans feeders).
  Deferred to Phase 3 / worktree-2. Do NOT implement it. (`bot/cogs/puzzles.py:1458`.)
- Batch `!clues` (multiple quoted strings) — single-clue MVP only.
- `PuzzleService.solve`/`unsolve` and the Phase-1 solve-mode endpoint. Do not alter them.
- Parity-plan Phases 3–5 (deletion, reconcile, hunt lifecycle/settings).
- Any new web-test or frontend-test harness — none exists; do NOT invent one. Web/frontend
  verified manually + by the service equivalence tests.

## Relevant files

- `bot/core/puzzle_service.py` — ADD `set_puzzle_url`, `convert_to_meta`, `rename_puzzle`
  here. `solve` (L523) is the contract to mirror (commit → cascade → `notifier.board_changed`
  → raise `SheetRefreshError`). `_gsheet_cog` (L40), `refresh_metas` (L366), `refresh_meta`
  (L234), existing `make_minimeta` (L721), `log_clues` (L1011), `refresh_evidence` (L1015).
  (read first, then edit)
- `bot/cogs/puzzles.py` — bang commands to mirror + rewire:
  - `link` (L2058) — `puzzle.url = url`; `get_gsheet_cog(ctx).update_puzzle(puzzle)`. No role gate.
  - `clue` (L1085) — `service.log_clues(hunt, puzzle, [note])` + `refresh_evidence`. No role gate.
  - `make_minimeta` (L985) — `service.make_minimeta(puzzle)`. No role gate.
  - `convert_to_meta` (L1431, gated `@has_any_role(*ADMIN_ROLES)` + `manage_channels`).
    Uses `_round_impl(ctx, puzzle.name, "Metapuzzle", False, False)` (L1321, convert branch
    L1352-1366), then `add_tag` + `metapuzzle=1` + commit + `convert_puzzle_spreadsheet` +
    `set_channel_type("Metapuzzle")`.
  - `rename_puzzle` (L2022, alias `rename_meta`, gated `@has_any_role(*ORGANISER_ROLES)`).
    Dup-check → `safe_rename` (L120) → `name` + `channel_name = clean_name(name)` → commit →
    `update_puzzle(puzzle, True)` → `update_metapuzzle` if in a round.
  - `safe_rename` (L120) + `_deferred_rename` (L138) — currently stateful on the cog
    (`self._pending_renames` L111, `self._rename_tasks` L113). **Phase 6 RELOCATES these into
    `PuzzleService`** (Q3). Callers: `puzzles.py:2031` (rename), `:2573` (archival).
  - `clean_name`, `update_metapuzzle`, `set_channel_type`, `get_gsheet_cog` — cog helpers.
  (read, then edit)
- `bot/web/actions.py` — ADD the five endpoints. `solve_puzzle` (L314) and `retag_puzzle`
  (L406) are the wrappers to copy: `_visible_hunt_for_puzzle` → archived check →
  (`require_organiser` if gated) → `puzzle_lock(puzzle_id)` → fetch puzzle → `cog.service.*`
  → `SheetRefreshError`/`HttpError` → `sheet_warning` → `_audit(...)` → best-effort
  `refresh_unassigned_safe` → `{ok, sheet_warning?}`. `require_organiser(snowflake, roles=)`
  at L394 (default `ORGANISER_ROLES`; pass `roles=ADMIN_ROLES` for convert).
- `tests/test_solve_equivalence.py` — the harness to copy: `_make_cog`, `_make_hunt`,
  `_no_guild`, and the `_run`-style with-patch block that mocks `PuzzleJsonDb`/`RoundJsonDb`/
  `HuntJsonDb`/`GuildSettingsDb` in BOTH `bot.cogs.puzzles` and `bot.core.puzzle_service`.
  (read, then APPEND)
- `tests/conftest.py` — `make_puzzle`, `make_round` helpers (read only). `make_puzzle`
  kwargs: `id, name, hunt_id, solved, solution, metapuzzle, metameta, tags, status,
  channel_id, parent_puzzle_id, puzzle_type`. NO `url`/`solve_time`/`channel_name` kwargs —
  `PuzzleData` defaults them (`url=""`, `solve_time=None`, `channel_name=""`); set them on
  the object directly if a test needs a non-default. `make_round` kwargs: `id, name, hunt_id,
  meta_id, meta_code, category_id`.
- `frontend/src/api.ts` — ADD five hooks. Pattern = `useAddSheet` (L145) / `useRetagPuzzle`
  (L264): `apiSend<{ok:boolean; sheet_warning?:string}>(url, "POST", body)` +
  `onSuccess: () => qc.invalidateQueries({queryKey:["hunt", uid]})`.
- `frontend/src/components/PuzzleDrawer*.tsx`, the puzzle card/row kebab menu, and
  `EvidencePanel` — Phase 2–6 controls (locate the exact files at each phase).

## Constraints

- Don't touch files outside the Relevant files list. Don't refactor unrelated code.
- **No public-signature changes.** New service methods are additive. Do NOT change the
  *signature* of `PuzzleService.__init__(self, bot, notifier=None)` or any existing method.
  EXCEPTION (Phase 6, Q3): adding new instance ATTRS inside `__init__` (`self._pending_renames
  = {}`, `self._rename_tasks = set()`) is allowed and required — that is not a signature change.
  If you find yourself changing a method's parameter list, STOP → `blocker.md`.
- Behaviour parity is the contract: after rewiring, each bang command must produce the SAME
  DB-state transitions. `test_solve_equivalence.py` (extended in Phase 1) is the guard — run
  it after every extraction edit.
- **migration → deploy → code**: this task adds NO columns and NO schema. If you think you
  need one, STOP → `blocker.md`.
- Run tests with the project runner only:
  `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`
  (a killed run exits 124; read the log). Tests need a reachable DB — a gitignored
  `config.json` (local `puzzlebot_test`) provides creds; if `import bot` fails on DB connect,
  that is an environment issue → `blocker.md`, do not mock around it.
- Frontend gate: `cd frontend && timeout 120 npx tsc --noEmit` exits 0.

## Test constraints (read — you do not get global instructions)

- **Reuse the exact harness in `test_solve_equivalence.py`** — the `_run`-style patcher that
  mocks the four `*JsonDb` in BOTH `bot.cogs.puzzles` and `bot.core.puzzle_service`. Invent
  no new harness.
- Call the seam directly, e.g. `await cog.service.set_puzzle_url(_no_guild(), hunt, puz, "...")`.
  `cog` from `_make_cog()`, `hunt` from `_make_hunt()`, `_no_guild()` returns None (skips
  Discord). `bot.get_cog` returns None, so `_gsheet_cog(hunt)` is None and gsheet pushes are
  skipped — do NOT assert any gsheet call in these tests.
- **Confirm every attribute before asserting it.** Allowed `make_puzzle`/`make_round` kwargs
  are listed above. `url`, `solve_time`, `channel_name` are NOT kwargs — set on the object.
  Grep `tests/conftest.py` / `bot/store/puzzle_data.py` before using any other field.
- **NEVER write a loop that resolves state "until" a condition.** If tempted, STOP →
  `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says so.
- **Each test file defines/imports every helper it calls.** You are APPENDING to
  `test_solve_equivalence.py`, which already defines `_make_cog`/`_make_hunt`/`_no_guild` —
  reuse those; do not redefine them and do not reach into a sibling module.
- **Wrap every run in `timeout`, write to a file, single statement** (no `; echo` tail). A
  run that reads as "slow" may be a hang — isolate per file.

## Phase 1 — write ALL failing tests (model: Opus 4.8). NO production code.

Append three test classes to `tests/test_solve_equivalence.py`, copying the existing `_run`
with-patch block verbatim and swapping the seam call. Skeletons below are near-verbatim; fill
in the patch block exactly as `TestWebSolvePuzzleState._run_solve` does.

```python
# ---------------------------------------------------------------------------
# set_puzzle_url (service) — mirrors !link: puzzle.url = url; commit; gsheet
# update_puzzle (skipped here, no gsheet cog). No solve_time, no status change.
# ---------------------------------------------------------------------------
class TestWebSetPuzzleUrl:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, url, rounds_by_id=None):
        # COPY the with-patch block from TestWebSolvePuzzleState._run_solve verbatim,
        # collect commits the same way, but call:
        #   await cog.service.set_puzzle_url(_no_guild(), hunt, puzzle, url)
        ...

    @pytest.mark.asyncio
    async def test_sets_url(self, cog, hunt):
        puz = make_puzzle()          # url defaults to ""
        await self._run(cog, hunt, puz, "https://example.com/p")
        assert puz.url == "https://example.com/p"

    @pytest.mark.asyncio
    async def test_commits_puzzle(self, cog, hunt):
        puz = make_puzzle(id=42)
        commits = await self._run(cog, hunt, puz, "https://x")
        assert any(c[0] == "puzzle" and c[1] == 42 for c in commits)

    @pytest.mark.asyncio
    async def test_does_not_mark_solved_or_stamp_time(self, cog, hunt):
        puz = make_puzzle(solved=False)
        await self._run(cog, hunt, puz, "https://x")
        assert puz.solved is False
        assert puz.solve_time is None


# ---------------------------------------------------------------------------
# convert_to_meta (service) — mirrors !convert_to_meta + _round_impl convert
# branch: create a RoundData wrapping the puzzle (type "Metapuzzle"), meta_id =
# puzzle.id, generate meta_code; then puzzle.add_tag(round.id), metapuzzle=1,
# commit. gsheet restructure (convert_puzzle_spreadsheet) + channel-type live at
# the edge / are skipped with no gsheet cog. Method returns (round, puzzle).
# NOTE: _round_impl calls RoundJsonDb.generate_uid for meta_code — the harness
# must give RoundJsonDb.generate_uid a return value (e.g. "MCODE1").
# ---------------------------------------------------------------------------
class TestWebConvertToMeta:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, rounds_by_id=None):
        # COPY the with-patch block; ALSO set mock_rdb.generate_uid.return_value = "MCODE1".
        # Collect committed rounds (mock_rdb.commit side_effect appends ("round", r)).
        # Call:  return await cog.service.convert_to_meta(_no_guild(), hunt, puzzle)
        ...

    @pytest.mark.asyncio
    async def test_marks_metapuzzle(self, cog, hunt):
        puz = make_puzzle(metapuzzle=0)
        await self._run(cog, hunt, puz)
        assert puz.metapuzzle == 1
        assert puz.is_metapuzzle() is True

    @pytest.mark.asyncio
    async def test_creates_round_with_meta_id(self, cog, hunt):
        puz = make_puzzle(id=7)
        new_round, _ = await self._run(cog, hunt, puz)
        assert new_round.meta_id == 7
        assert new_round.type == "Metapuzzle"

    @pytest.mark.asyncio
    async def test_tags_puzzle_to_new_round(self, cog, hunt):
        puz = make_puzzle(id=7, tags=[])
        new_round, _ = await self._run(cog, hunt, puz)
        assert new_round.id in puz.tags


# ---------------------------------------------------------------------------
# rename_puzzle (service) — set name, set channel_name (cleaned), commit, gsheet
# update_puzzle(puzzle, True), refresh the round's meta. The service ALSO owns the
# Discord channel rename via self.safe_rename, but it is guarded on `guild is not
# None` — so under _no_guild() the channel rename is skipped (same as solve). These
# tests therefore assert ONLY the DB transition; do not assert any rename call.
# ---------------------------------------------------------------------------
class TestWebRenamePuzzle:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, name, rounds_by_id=None):
        # COPY the with-patch block; call:
        #   await cog.service.rename_puzzle(_no_guild(), hunt, puzzle, name)
        ...

    @pytest.mark.asyncio
    async def test_sets_name(self, cog, hunt):
        puz = make_puzzle(name="Old Name")
        await self._run(cog, hunt, puz, "New Name")
        assert puz.name == "New Name"

    @pytest.mark.asyncio
    async def test_commits_puzzle(self, cog, hunt):
        puz = make_puzzle(id=42, name="Old")
        commits = await self._run(cog, hunt, puz, "New")
        assert any(c[0] == "puzzle" and c[1] == 42 for c in commits)
```

**Phase 1 acceptance:**
`timeout 180 pipenv run python -m pytest tests/test_solve_equivalence.py -q > /tmp/test.log 2>&1`
- Exits non-zero. The NEW tests fail with `AttributeError`/"does not exist" on
  `cog.service.set_puzzle_url` / `convert_to_meta` / `rename_puzzle` (the right reason).
- The existing tests in the file still PASS (you only appended classes; you changed no
  production code). If an existing test now fails, you broke the harness copy — fix the test,
  not the app.
- Also confirm the file runs under the full-suite runner mode (the same `pytest tests/` mode
  the final gate uses) without an import/bootstrap fatal — run the line above; a collection
  error is a wrong-assumption signal, not a "method missing" signal.

Commit: `test(parity): failing service tests for puzzle-edit extractions`.

## Phase 2 — link (model: Sonnet 4.6)

1. `bot/core/puzzle_service.py`: add
   `async def set_puzzle_url(self, guild, hunt, puzzle, url)`: `puzzle.url = url`;
   `PuzzleJsonDb.commit(puzzle)`; `gsheet = self._gsheet_cog(hunt); if gsheet: await
   gsheet.update_puzzle(puzzle)`; `await self.notifier.board_changed(hunt)`. (No
   `SheetRefreshError` — `link` does not raise; keep it simple and match the bang.)
2. Rewire the bang `link` (L2058) to call `self.service.set_puzzle_url(...)` then keep its
   `send_state` rendering. Confirm `update_puzzle` is not double-called.
3. `bot/web/actions.py`: add `POST /api/puzzles/<id>/link`, body `{url}`. Copy `solve_puzzle`'s
   skeleton MINUS the mode dispatch; **no role gate** (mirror bang — any hunt member);
   `puzzle_lock`, `_audit("link", ...)`, best-effort `refresh_unassigned_safe`,
   `{ok, sheet_warning?}`.
4. `frontend/src/api.ts`: add `useSetPuzzleLink(uid)` (copy `useAddSheet`). Wire a
   `TypeCell`-style editable URL field in the drawer.
5. **Acceptance:** `timeout 180 pipenv run python -m pytest tests/test_solve_equivalence.py -q
   > /tmp/test.log 2>&1` (the `set_puzzle_url` class now passes; rename/convert still fail —
   expected); `python3 -c "import ast; ast.parse(open('bot/web/actions.py').read())"` exits 0;
   `cd frontend && timeout 120 npx tsc --noEmit` exits 0.
   Commit: `feat(parity): web set-puzzle-link (service + endpoint + drawer)`.

## Phase 3 — clue (model: Sonnet 4.6)

`log_clues` + `refresh_evidence` already exist — NO new service method.
1. `bot/web/actions.py`: add `POST /api/puzzles/<id>/clues`, body `{note}`. No role gate.
   In the `puzzle_lock`: `cog.service.log_clues(hunt_obj, puzzle, [note.strip()])`;
   `asyncio.create_task(cog.service.refresh_evidence(hunt_obj))`; `_audit("clue", ...)`;
   `{ok}`. Reject empty `note` with 400 `empty_note` (mirror the bang's guard).
2. `frontend/src/api.ts`: add `useLogClue(uid)` invalidating `["hunt", uid]` AND
   `["evidence", uid]` (see `useUpdateEvidence` at L335 for the evidence key). Add a "Log clue"
   input + submit into the existing `EvidencePanel`.
3. **Acceptance:** ast-parse `actions.py` exits 0; full suite green
   (`timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`);
   `tsc --noEmit` clean. Commit: `feat(parity): web log-clue (endpoint + evidence panel)`.

## Phase 4 — make_minimeta (model: Sonnet 4.6)

`make_minimeta` already exists (L721, sync, flips `metapuzzle`-style flag) — NO new method.
1. `bot/web/actions.py`: add `POST /api/puzzles/<id>/make-minimeta` (no body). In the
   `puzzle_lock`: `cog.service.make_minimeta(puzzle)`; `_audit("make_minimeta", ...)`;
   best-effort `refresh_unassigned_safe`; `{ok}`.
   **Auth (RESOLVED Q1):** gate with `require_organiser(ctx["guild_snowflake"])` (default
   `ORGANISER_ROLES` = "Organisers and up"). Deliberate tightening over the ungated bang —
   the user chose to gate structural ops on the web to organiser+.
2. `frontend/src/api.ts`: add `useMakeMinimeta(uid)`. Add "Make minimeta" to the puzzle
   card/row kebab menu behind a confirm dialog.
3. **Acceptance:** ast-parse exits 0; full suite green; `tsc --noEmit` clean.
   Commit: `feat(parity): web make-minimeta (endpoint + kebab control)`.

## Phase 5 — convert_to_meta (model: Sonnet 4.6) — read open-questions Q2 first

1. `bot/core/puzzle_service.py`: add `async def convert_to_meta(self, guild, hunt, puzzle)`
   that owns the DB + gsheet cascade currently split across `_round_impl` (convert branch,
   L1352-1366) and `convert_to_meta` (L1434):
   - guard: if `puzzle.is_metapuzzle()` raise/return a sentinel (the endpoint maps it to 400
     `already_meta`).
   - build the round: `RoundData(name=puzzle.name, hunt_id=hunt.id, category_id=puzzle.channel_id,
     type="Metapuzzle", kind="meta")`; `RoundJsonDb.commit(new_round)`;
     `new_round.meta_code = RoundJsonDb.generate_uid('meta_code', 6, puzzle.name)`;
     `new_round.meta_id = puzzle.id`; `RoundJsonDb.commit(new_round)`.
   - `puzzle.add_tag(new_round.id)`; `puzzle.metapuzzle = 1`; `PuzzleJsonDb.commit(puzzle)`.
   - gsheet: `gsheet = self._gsheet_cog(hunt); if gsheet: await
     gsheet.convert_puzzle_spreadsheet(puzzle)`.
   - `await self.notifier.board_changed(hunt)`; `return new_round, puzzle`.
   No channel rename — `convert_to_meta` deliberately omits it (the dashboard META label
   supersedes the old name prefix; renames burn the rate-limit bucket — see `puzzles.py:1449`).
   `set_channel_type` is NOT a real operation: it only updates the cog's request-scoped
   `self.environment[ctx.message.id]` cache (L292). The web has no such cache and does NOT
   call it — the persistent effect (`metapuzzle=1`) is owned by the service. (RESOLVED Q4.)
2. Rewire the bang `convert_to_meta` (L1434) to call `self.service.convert_to_meta(...)` then
   keep `set_channel_type("Metapuzzle")`, `info(update=True)`, and its embed (these stay in the
   bang — they are cog request-cache + Discord embed concerns). The bang's own `_round_impl`
   convert branch is now redundant for this path — leave `_round_impl` intact for the
   round/mp/rnm commands; only `convert_to_meta` stops using its convert branch.
3. `bot/web/actions.py`: add `POST /api/puzzles/<id>/convert-to-meta` (no body). Gate with
   `require_organiser(ctx["guild_snowflake"])` (default `ORGANISER_ROLES` — RESOLVED Q2: the
   user chose organiser+ for both structural ops; this is looser than the bang's ADMIN +
   `manage_channels` gate, a deliberate choice). `puzzle_lock`; reject `already_meta` with 400;
   `_audit("convert_to_meta", ...)`; best-effort `refresh_unassigned_safe`; `{ok, sheet_warning?}`.
4. `frontend/src/api.ts`: add `useConvertToMeta(uid)`. Add "Convert to meta" to the kebab
   menu behind a confirm dialog whose text states it creates a meta round.
5. **Acceptance:** `timeout 180 pipenv run python -m pytest tests/test_solve_equivalence.py -q`
   (convert class now passes); full suite green; ast-parse exits 0; `tsc --noEmit` clean.
   Commit: `feat(parity): web convert-to-meta (service + endpoint + kebab control)`.

## Phase 6 — rename (model: Sonnet 4.6) — riskiest; read open-questions Q3 first

**Q3 RESOLVED: move `safe_rename` + its deferred-rename state into the service.** The service
is NOT Discord-agnostic — it already renames channels (`puzzle_service.py:82`
`await live.edit(name=want_name)`), repositions channels, archives threads. The deferred-rename
machine is a pure Discord rate-limit workaround and belongs next to those `channel.edit` calls.
The move is contained (verified): 2 state attrs, 2 methods, 2 cog call sites.

1. **Relocate `safe_rename` into `PuzzleService`** (`bot/cogs/puzzles.py` → `bot/core/puzzle_service.py`):
   - Move the 2 state attrs from the cog `__init__` (`puzzles.py:111` `self._pending_renames = {}`,
     `:113` `self._rename_tasks = set()`) into `PuzzleService.__init__` (add as instance attrs —
     NO constructor signature change).
   - Move `safe_rename` (`puzzles.py:120`) and `_deferred_rename` (`:138`) verbatim onto
     `PuzzleService`. Keep `safe_rename`'s optional `ctx=None` param (the web/service path passes
     None → no user-facing "rate-limited" message, which is fine).
   - Update BOTH existing cog call sites to `self.service.safe_rename(...)`:
     `puzzles.py:2031` (rename_puzzle) and `:2573` (solved-channel archival rename).
2. `bot/core/puzzle_service.py`: add
   `async def rename_puzzle(self, guild, hunt, puzzle, name)` owning the FULL cascade like `solve`:
   - duplicate guard: the caller (bang/endpoint) does the dup-check before calling, OR the
     method returns a sentinel — keep the dup-check at the caller to match the bang (it sends a
     user-facing message). Do NOT duplicate `check_duplicates_in_hunt` logic into the service.
   - `puzzle.name = name`; `puzzle.channel_name = <cleaned name>`. `clean_name` is a cog helper;
     pass the already-cleaned name in as a 5th arg, OR move `clean_name` too if trivial and
     pure — confirm it has no cog state first (grep it). Do NOT reimplement it.
   - channel rename (now in-service): `if guild is not None and puzzle.channel_id: channel =
     guild.get_channel(puzzle.channel_id); if channel: await self.safe_rename(channel, puzzle.channel_name)`.
     Guarded on `guild is not None` exactly like `solve` (L547) so the `_no_guild()` test path skips it.
   - `PuzzleJsonDb.commit(puzzle)`.
   - `gsheet = self._gsheet_cog(hunt); if gsheet: await gsheet.update_puzzle(puzzle, True)`.
   - refresh the puzzle's round meta with failure isolation like `solve` (iterate `puzzle.tags`,
     `refresh_meta`, collect failures); `await self.notifier.board_changed(hunt)`;
     raise `SheetRefreshError(failed)` if any meta failed.
3. Rewire the bang `rename_puzzle` (L2022): keep the duplicate-name check + its user message,
   then call `self.service.rename_puzzle(guild, hunt, puzzle, name)` (catch `SheetRefreshError`
   → `_warn_stale_sheets`). The bang no longer calls `safe_rename` directly — the service does.
4. `bot/web/actions.py`: add `POST /api/puzzles/<id>/rename`, body `{name}`. Gate with
   `require_organiser(...)` (default `ORGANISER_ROLES` — matches the bang). Duplicate check
   (`PuzzleJsonDb.check_duplicates_in_hunt(name, hunt_id)`) → 400 `duplicate_name`. Then a SINGLE
   `cog.service.rename_puzzle(guild, hunt_obj, puzzle, name)` call (the service now does the
   channel rename internally, tolerating a deferred rename — no edge handling needed);
   `SheetRefreshError` → `sheet_warning`; `_audit("rename", ...)`; `{ok, sheet_warning?}`.
5. `frontend/src/api.ts`: add `useRenamePuzzle(uid)`. Inline-edit on the drawer title.
6. **Acceptance:** `timeout 180 pipenv run python -m pytest tests/test_solve_equivalence.py -q`
   (rename class now passes — all three extraction classes green); full suite green
   (`timeout 300 pipenv run python -m pytest tests/ -q`) — pay special attention that NO test
   relied on `safe_rename`/`_pending_renames` living on the cog (grep tests first); ast-parse
   exits 0; `tsc --noEmit` clean.
   Commit: `feat(parity): move safe_rename to service; web rename-puzzle`.

## Verification (whole handoff)

- Full suite green: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`.
- `cd frontend && timeout 120 npx tsc --noEmit` clean.
- Equivalence: bang and web produce identical DB state for link / convert / rename (the new
  service tests prove the extracted seams); clue / make_minimeta reuse seams already tested.
- Manual dashboard click-through of all five controls.
- `convert_to_feeder` untouched.

## Acceptance criteria (from the design doc)

- Each of the five commands works from the dashboard and produces DB state identical to its
  bang equivalent.
- Full pytest suite green; `tsc --noEmit` clean.
- No change to Phase-1 solve behaviour; `convert_to_feeder` untouched.
