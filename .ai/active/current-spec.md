# Current Spec — M7: Meta-meta + flexible aggregation (engine core)

Tool: Claude Code (handoff package)
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Target model — Phase 1 (tests): **Opus 4.8** · Phases 2+: **Sonnet 4.6**
Started: 2026-06-23
Status: ready for handoff — Phase 1 (tests)

Source plan: `/home/russ/.claude/plans/can-we-plan-m7-sparkling-newell.md` (background only;
this spec is authoritative). Roadmap: `docs/PLANS.md` M7. Substrate specs (read-only
background): `docs/superpowers/specs/2026-06-22-m5-rollups-design.md`,
`docs/superpowers/specs/2026-06-22-m6-typed-groups-design.md`.

---

## Before you start (executing model — READ THIS)

- **Execute only the current phase.** Do not skip ahead; commit at each phase boundary.
- **Required background skills:** before writing tests invoke
  `superpowers:test-driven-development` and `superpowers:writing-plans`. On ANY unexpected
  failure invoke `superpowers:systematic-debugging` BEFORE changing anything. Before
  claiming a phase done invoke `superpowers:verification-before-completion` and paste the
  acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood
  fix, write `.ai/active/blocker.md` (what failed, the exact output, your diagnosis) and
  STOP. Do not add scaffolding to route around a failing assumption.
- **A test failing because a method/attribute doesn't exist yet is the RIGHT reason.** A
  Python import error, a missing DB column, an unknown field, or an `AttributeError` on a
  real object means a *wrong assumption in the test* — fix the test, don't add machinery.
- **Branch:** start with `git switch -c feature/m7-flexible-aggregation main` (branch from
  local `main`, per `docs/WORKTREES.md` — origin/main is intentionally stale).

## Test command (the ONLY runner — pipenv, repo root on sys.path)

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

Filter a single file/case during iteration:
```bash
timeout 120 pipenv run python -m pytest tests/test_rollup_source_mode.py -q > /tmp/test.log 2>&1
```
A killed run exits 124; read `/tmp/test.log`. Tests open MySQL at import
(`bot/store/__init__.py`) — the gitignored `config.json` provides `puzzlebot_test` creds.
The final gate is the full `tests/ -q` run (no `--filter`), so every new test file must
run cleanly under that same mode (no cross-file helper imports — see anti-patterns).

---

## Goal

Activate the dormant M6/M5 typed-group substrate so a roll-up sheet aggregates *different
membership* depending on a per-rollup `source_mode` (`round` / `subtree` / `explicit`),
with `tags.edge_type` becoming live. Acceptance is the **runaround**: the same answer set
producible three ways. `source_mode` becomes the single runtime switch — the
`puzzles.metameta` flag gives way to it (backfilled to `subtree`).

## In scope (this handoff)

1. `migrations/004_source_mode_backfill.sql` — set `source_mode='subtree'` on rollups
   whose meta puzzle is `metameta=1`; and `migrations/005_tags_unique_key.sql` — dedup then
   add `UNIQUE KEY tag_uq (round_id, puzzle_id)` so membership is one-edge-per-pair.
2. `PuzzleService._collect_rollup_puzzles` + `refresh_meta` refactor to branch on
   `rollup.source_mode` (not `metapuzzle.metameta`), preserving today's output at defaults.
3. `parent_group_id` subtree walk: `collect_subtree_rounds` / `collect_subtree_puzzles`.
4. `tags.edge_type` live on read (`PuzzleData.tag_edges`) + a `tag_puzzle_with_edge` writer.
5. Thin Discord setter commands: `!nest`, `!source_mode`, `!pool`.
6. SPA setters: `PATCH /api/rollups/<round_id>` (source_mode) + a nesting endpoint, with
   minimal `BoardGroup.tsx` controls; surface `source_mode` through the board payload.

## Out of scope (do NOT do)

- **The aggregated-answers read VIEW + copy-to-clipboard component → M7.5.** Not here.
- **Channel-less group creation** (deferred at M6). Nest/aggregate only EXISTING rounds,
  resolved by `meta_code`. Do not create category-less groups; do not touch the ~12
  `get_by_attr(category_id)` call sites.
- M8 `part-of` / M9 `reveals` edge behaviour — those edge_types stay dormant.
- **Do NOT change `PuzzleData.tags` from `List[int]`** (Risk below).
- **Do NOT thread `edge_type` through the generic `commit()` tag set-difference logic**
  (`mysqldb.py:357-380`) or the `solve`/`move_to_meta`/`add_to_meta`/`remove_from_meta`
  loops. Use the separate `tag_puzzle_with_edge` writer.

## Relevant files

- `bot/core/puzzle_service.py` — `refresh_meta` (lines ~38-70), `_find_or_create_rollup`,
  `set_parent_group` (~162-193), `solve`/`*_to_meta`. **(write)**
- `bot/cogs/puzzles_gsheet.py` — `aggregate_to_tab` (~656), `add_metapuzzle_data` (~686),
  `add_metametapuzzle_data` (~699). Keep the two renderers intact. **(read mostly)**
- `bot/store/mysqldb.py` — `get_tags` (336-343), `commit` tag-insert (357-380),
  `get_all_from_round` (298), `get_all_from_hunt` (286), `RoundJsonDb.get_all` (178). **(write)**
- `bot/store/puzzle_data.py` — add `tag_edges` field. **(write)**
- `bot/store/rollup_data.py` — `RollupData` (has `source_mode`/`edge_type_filter`). **(read)**
- `bot/store/round_data.py` — `RoundData` (`parent_group_id`, `kind`). **(read)**
- `bot/cogs/puzzles.py` — add `!nest`/`!source_mode`/`!pool` (god-cog; keep edits focused). **(write)**
- `bot/web/actions.py`, `bot/web/api.py`, `bot/web/data.py` — SPA setters + board payload. **(write)**
- `frontend/src/components/BoardGroup.tsx`, `frontend/src/types.ts`,
  `frontend/src/api.ts` — minimal setter controls. **(write)**
- `tests/conftest.py` — `make_puzzle`, `make_round` helpers (read; reuse, don't redefine).
- `tests/test_refresh_meta.py` — the seam pattern to copy (`_svc_with_sheets`, `_patched`). **(read)**

## Constraints

- **Migration → deploy → code.** `004` ships/commits before any code reads `source_mode`
  as the branch switch. (The columns themselves already exist from `002`/`003`.)
- Logic belongs in `bot/core/`; bang-commands + web handlers are thin edges that call it.
- Don't refactor unrelated code; don't touch files outside the list; leave the dirty
  `Pipfile.lock` (if any) out of commits.
- **Constructor/signature changes:** if you add a required arg to any `PuzzleService`
  method or `RollupData`/`PuzzleData` field, grep every call site (`grep -rn
  "_collect_rollup_puzzles\|tag_puzzle_with_edge\|\.refresh_meta(" bot/`) and update
  hand-built ones. `PuzzleData()`/`RollupData()` are built by hand in tests and the store
  — new fields MUST have defaults (`field(default_factory=...)` for the map).

## Test constraints (anti-patterns — inlined; the executing agent does not read globals)

- **Reuse the exact seams; invent none.** Copy `tests/test_refresh_meta.py`'s
  `_svc_with_sheets()` and `_patched()` context managers and `conftest.make_puzzle` /
  `make_round`. Do NOT build a new mock harness.
- **NEVER write a loop that resolves state "until" a condition.** If you think you need
  one, STOP and write `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says to.
- **Confirm every column/attribute/payload key exists before use.** Specifically:
  `RollupData.source_mode` / `.edge_type_filter` exist (`rollup_data.py:29-30`);
  `RoundData.parent_group_id` / `.kind` exist (`round_data.py:23-25`); `tags.edge_type`
  exists (migration `003`). The `tags` table STARTS with only a **non-unique**
  `KEY(round_id, puzzle_id)` (`000_base_schema.sql:115`); **Phase 2 migration `005` replaces
  it with `UNIQUE KEY tag_uq (round_id, puzzle_id)`**, after which the edge writer uses
  `INSERT ... ON DUPLICATE KEY UPDATE edge_type=VALUES(edge_type)`. The edge-writer test
  (`test_tag_edges.py`) targets that upsert — confirm `005` is applied
  (`migrate.py status`) before relying on it.
- **When a decision goes through a shared handler, OPEN it.** `refresh_meta` is the shared
  render seam; pin that `_collect_rollup_puzzles` returns the puzzle list the EXISTING
  `add_metapuzzle_data(metapuzzle, feeders)` / `add_metametapuzzle_data(metapuzzle,
  all_puzzles)` mocks already assert against (see `test_refresh_meta.py:77,90`). A wrong
  shape silently changes what the mock receives.
- **A test file must define or import every helper it calls** — import only from
  `tests.conftest` (as the existing files do) or define module-local helpers with UNIQUE
  names. The parallel gate shards by file; a cross-test-file helper becomes an "undefined
  name" fatal. Do not rely on a helper defined in a sibling test file.
- **Assert behaviour, not just structure.** For `source_mode` routing, assert WHICH
  renderer was awaited AND that the wrong one was `assert_not_awaited()` (mirror
  `test_refresh_meta.py:78,91`). For the subtree walk assert the exact puzzle-id set.
- **Wrap every run in `timeout`, write to `/tmp/test.log`, single statement** (no `; echo`
  tail). A run that reads as slow may be a hang — isolate per file.

---

## Phase 1 — Write ALL failing tests (Opus 4.8). NO production code.

Create the test files below. Each must FAIL for the right reason (the new method/field
doesn't exist yet → `AttributeError`/`ImportError` on the seam under test) — NOT a harness
fatal. Commit at the end of this phase with no production changes.

### File `tests/test_rollup_source_mode.py` — engine routing (pure, mocked cog)

Copy the seam from `tests/test_refresh_meta.py` verbatim: the `_hunt()`,
`_svc_with_sheets()`, and `_patched()` helpers (define them module-local here; do not
import from the sibling test file). Then:

```python
class TestSourceModeRouting:
    @pytest.mark.asyncio
    async def test_round_mode_calls_add_metapuzzle_data(self):
        # rollup.source_mode == "round" (the default) -> today's path, unchanged.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        feeders = [make_puzzle(id=2, name="Feeder", solution="ANS")]
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_round.return_value = feeders
            await svc.refresh_meta(_hunt(), meta_round)
        gsheet.add_metapuzzle_data.assert_awaited_once_with(metapuzzle, feeders)
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subtree_mode_aggregates_branch_solved_puzzles(self):
        # rollup.source_mode == "subtree": collect solved puzzles across the
        # parent_group_id branch and render via the metameta (3-col) path.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        existing = RollupData(id=9, hunt_id=10, round_id=5, source_mode="subtree")
        # child round 6 nested under round 5; its solved feeder is id=2.
        child = make_round(id=6, meta_id=0); child.parent_group_id = 5
        solved = make_puzzle(id=2, name="Branch", solved=True, solution="X")
        with _patched(rollup_existing=existing) as (pdb, _h, rdb, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            rdb.get_all.return_value = [meta_round, child]   # RoundJsonDb.get_all(hunt_id)
            pdb.get_all_from_round.side_effect = lambda rid: {5: [], 6: [solved]}[rid]
            await svc.refresh_meta(_hunt(), meta_round)
        # the metameta renderer received exactly the branch's solved puzzles
        args = gsheet.add_metametapuzzle_data.await_args
        assert args.args[0] is metapuzzle
        assert [p.id for p in args.args[1]] == [2]
        gsheet.add_metapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_explicit_mode_uses_aggregates_into_members(self):
        # rollup.source_mode == "explicit": only puzzles whose edge into THIS round
        # is 'aggregates-into' are aggregated.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        existing = RollupData(id=9, hunt_id=10, round_id=5, source_mode="explicit")
        pooled = make_puzzle(id=2, name="Pooled", solution="P")
        pooled.tag_edges = {5: "aggregates-into"}
        other = make_puzzle(id=3, name="Feeder", solution="F")
        other.tag_edges = {5: "feeds-meta"}
        with _patched(rollup_existing=existing) as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_round.return_value = [pooled, other]
            await svc.refresh_meta(_hunt(), meta_round)
        # Only the aggregates-into member is rendered (assert the id set on whichever
        # renderer the explicit path uses — pin it in Phase 2 and assert here).
        # EXPECT: pooled (id=2) present, other (id=3) absent.
```

> Note for the test author: the `explicit` path's renderer/columns are a Phase-2 design
> decision. Pin it to the 2-col `add_metapuzzle_data` shape (pool = name+solution) and
> assert `add_metapuzzle_data.await_args.args[1]` is `[pooled]`. If that shape feels
> wrong when you read the renderer, write `blocker.md` rather than guessing a new one.

### File `tests/test_metameta_backfill_bridge.py` — flag gives way to source_mode (pure)

```python
class TestMetametaBridge:
    @pytest.mark.asyncio
    async def test_metameta_round_with_subtree_mode_uses_branch_not_hunt_scan(self):
        # After migration 004 a metameta round has source_mode='subtree'. refresh_meta
        # must branch on source_mode, NOT on metapuzzle.metameta, so it walks the
        # parent_group_id branch (get_all + get_all_from_round) rather than
        # get_all_from_hunt.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        metameta = make_puzzle(id=1, metapuzzle=1, metameta=1)
        existing = RollupData(id=9, hunt_id=10, round_id=5, source_mode="subtree")
        with _patched(rollup_existing=existing) as (pdb, _h, rdb, _ru):
            pdb.get_by_attr.return_value = metameta
            rdb.get_all.return_value = [meta_round]
            pdb.get_all_from_round.return_value = []
            await svc.refresh_meta(_hunt(), meta_round)
        pdb.get_all_from_hunt.assert_not_called()   # no hunt-wide scan post-M7
        gsheet.add_metametapuzzle_data.assert_awaited_once()
```

### File `tests/test_subtree_walk.py` — containment walk (pure, mocked RoundJsonDb)

```python
class TestSubtreeWalk:
    @pytest.mark.asyncio
    async def test_collects_descendants_deduped_and_solved_only(self):
        svc = PuzzleService(MagicMock())
        root = make_round(id=1, meta_id=10)
        a = make_round(id=2); a.parent_group_id = 1
        b = make_round(id=3); b.parent_group_id = 2          # grandchild
        unrelated = make_round(id=99); unrelated.parent_group_id = None
        p_solved = make_puzzle(id=50, solved=True, solution="S")
        p_unsolved = make_puzzle(id=51, solved=False)
        with patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
            rdb.get_all.return_value = [root, a, b, unrelated]
            # p_solved appears in BOTH a and b -> must be deduped to one row.
            pdb.get_all_from_round.side_effect = lambda rid: {
                1: [], 2: [p_solved, p_unsolved], 3: [p_solved]}.get(rid, [])
            puzzles = await svc.collect_subtree_puzzles(root)
        assert sorted(p.id for p in puzzles) == [50]   # solved-only, deduped

    @pytest.mark.asyncio
    async def test_depth_cap_guards_a_broken_chain(self):
        # A pre-existing cycle in parent_group_id must not hang; cap at 64.
        svc = PuzzleService(MagicMock())
        root = make_round(id=1)
        loop = make_round(id=2); loop.parent_group_id = 1
        root.parent_group_id = 2                       # 1<->2 cycle
        with patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
            rdb.get_all.return_value = [root, loop]
            pdb.get_all_from_round.return_value = []
            await svc.collect_subtree_puzzles(root)     # must return, not hang
```

### File `tests/test_tag_edges.py` — edge_type live (LIVE MySQL; mirror test_round_data.py)

Read `tests/test_round_data.py` first for the live-DB fixture/cleanup convention and COPY
it. Then:
- `test_get_tags_populates_tag_edges`: insert a puzzle + two tags (one default
  `feeds-meta`, one explicitly `aggregates-into`); load the puzzle; assert `puzzle.tags`
  still contains both round_ids (unchanged) AND `puzzle.tag_edges` maps each round_id to
  its edge_type.
- `test_tag_puzzle_with_edge_inserts_then_updates`: call `tag_puzzle_with_edge(pid, rid,
  'aggregates-into')` (insert), then again with `'feeds-meta'` (update-in-place); assert a
  single tags row whose edge_type is the latest value. This relies on the `005` UNIQUE key +
  `ON DUPLICATE KEY UPDATE` upsert — the test setup must run migrations (or assert `005`
  applied) so the unique constraint exists.

> If you cannot reach `puzzlebot_test`, write `blocker.md` and STOP — do not mock the DB
> for these store tests.

### Phase 1 acceptance

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```
- The four new files COLLECT and RUN under the full (non-filtered) run — no
  ImportError/collection fatal, no "undefined name", no DB-bootstrap error.
- The new tests FAIL, and each failure is an `AttributeError`/`assert` on the missing seam
  (`_collect_rollup_puzzles` routing, `collect_subtree_puzzles`, `PuzzleData.tag_edges`,
  `tag_puzzle_with_edge`) — NOT a harness/import fatal. Paste the failing lines.
- **All pre-existing tests still PASS.**
- Commit: `M7 phase 1: failing tests for source_mode engine + edge_type + subtree walk`.

---

## Phase 2 — Migrations 004 + 005 + edge_type on read + edge writer (Sonnet 4.6)

1. Write `migrations/004_source_mode_backfill.sql`: data-only `UPDATE` setting
   `source_mode='subtree'` on `rollups` rows whose `round_id` joins to a `rounds` whose
   `meta_id` joins to a `puzzles` with `metameta=1`. No `ALTER TABLE`.
2. Write `migrations/005_tags_unique_key.sql`: enforce one membership per `(round_id,
   puzzle_id)` so the edge writer can upsert and the `tag_edges` map (one edge_type per
   pair) is DB-backed. **Dedup first, then swap the non-unique key for a unique one** — safe
   to dedup NOW because every existing tags row is still the default `edge_type='feeds-meta'`
   (nothing has written `aggregates-into` yet), so no meaningful edge_type variation can be
   lost:
   ```sql
   DELETE t1 FROM tags t1 JOIN tags t2
     ON t1.round_id = t2.round_id AND t1.puzzle_id = t2.puzzle_id AND t1.id > t2.id;
   ALTER TABLE tags DROP KEY tag_index, ADD UNIQUE KEY tag_uq (round_id, puzzle_id);
   ```
   (`tag_index` was already `(round_id, puzzle_id)` non-unique; `tag_uq` fully replaces it.)
   Apply both: `pipenv run python migrate.py migrate` then `migrate.py status` (shows `004`
   and `005`). Commit the two SQL files.
3. `bot/store/puzzle_data.py`: add `tag_edges: Dict[int, str] = field(default_factory=dict)`
   to `PuzzleData` (import `field`, `Dict`). Additive; defaults keep all construction sites
   working.
4. `bot/store/mysqldb.py` `get_tags` (336-343): `SELECT tags.round_id, tags.edge_type`;
   keep appending `round_id` to `puzzle.tags` AND set `puzzle.tag_edges[round_id] =
   row['edge_type']`.
5. `bot/store/mysqldb.py`: add `tag_puzzle_with_edge(self, puzzle_id, round_id, edge_type)`
   to `MySQLPuzzleJsonDb` — a single idempotent upsert relying on the `005` unique key:
   ```sql
   INSERT INTO tags (round_id, puzzle_id, edge_type) VALUES (%s, %s, %s)
     ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)
   ```
   then `self.mydb.commit()`. Export via `bot/store/__init__.py` if commands call it through
   `PuzzleJsonDb`. (Note: the generic `commit()` tag set-difference at `mysqldb.py:366-378`
   stays untouched — it still inserts `(round_id, puzzle_id)` pairs that default to
   `feeds-meta`, which the unique key tolerates since it only ever inserts genuinely-missing
   pairs.)
- **Acceptance:** `tests/test_tag_edges.py` passes (insert-then-update collapses to one row
  via the upsert); `pipenv run python migrate.py status` shows `004` and `005` applied; full
  suite green.
- Commit: `M7 phase 2: edge_type live + tag upsert + 004 backfill + 005 unique key`.

## Phase 3 — Subtree walk (Sonnet 4.6)

In `bot/core/puzzle_service.py` add:
- `async def collect_subtree_rounds(self, root_round)` — fetch `RoundJsonDb.get_all(
  root_round.hunt_id)` once; BFS from `root_round.id` collecting rounds whose
  `parent_group_id` chains up to root; depth-cap 64 (mirror `set_parent_group`).
- `async def collect_subtree_puzzles(self, root_round)` — union
  `PuzzleJsonDb.get_all_from_round(r.id)` across those rounds, dedupe by `puzzle.id`,
  keep `solved` only.
- **Acceptance:** `tests/test_subtree_walk.py` passes; full suite green.
- Commit: `M7 phase 3: parent_group_id subtree walk`.

## Phase 4 — Engine refactor: refresh_meta branches on source_mode (Sonnet 4.6)

In `bot/core/puzzle_service.py` `refresh_meta`, replace the `if metapuzzle.metameta:` /
`else:` block (~60-65) with a call to a new
`async def _collect_rollup_puzzles(self, hunt, hunt_round, rollup, metapuzzle)` that
branches on `rollup.source_mode` and DELEGATES to the existing renderers so the mocks
match:
- `round` (default): `feeders = PuzzleJsonDb.get_all_from_round(hunt_round.id)` filtered to
  members whose `tag_edges.get(hunt_round.id, 'feeds-meta') == rollup.edge_type_filter`;
  `await gsheet_cog.add_metapuzzle_data(metapuzzle, feeders)`.
- `subtree`: `branch = await self.collect_subtree_puzzles(hunt_round)`;
  `await gsheet_cog.add_metametapuzzle_data(metapuzzle, branch)`.
- `explicit`: `pool = [p for p in get_all_from_round(hunt_round.id) if
  p.tag_edges.get(hunt_round.id) == 'aggregates-into']`;
  `await gsheet_cog.add_metapuzzle_data(metapuzzle, pool)`.
- Default-resolution safety: if `rollup.source_mode` is falsy treat as `round`. (No
  `metapuzzle.metameta` branch remains — the 004 backfill already set `subtree`.)
- **Acceptance:** `tests/test_rollup_source_mode.py` + `tests/test_metameta_backfill_bridge.py`
  + the unchanged `tests/test_refresh_meta.py` all pass; full suite green.
- Commit: `M7 phase 4: refresh_meta branches on source_mode (metameta flag retired)`.

## Phase 5 — Thin Discord setter commands (Sonnet 4.6)

Add to `bot/cogs/puzzles.py`, organiser-gated exactly like `!rebuild`, resolving rounds by
`meta_code` via `RoundJsonDb.get_by_attr(meta_code=...)` (copy the resolution from the
existing retag/rebuild commands), each calling the service then surfacing
`SheetRefreshError` with the existing stale-sheet warning helper:
- `!nest <child_code> <parent_code|none>` → `service.set_parent_group(child, parent_id)`;
  print the `ValueError` cycle message on rejection.
- `!source_mode <code> <round|subtree|explicit>` → load/create the rollup
  (`RollupsJsonDb.get_by_attr(round_id=...)` else `RollupData(...)`), validate the value
  against the three literals (reject others with a usage message), set `source_mode`,
  `RollupsJsonDb.commit`, then `await service.refresh_meta(hunt, round)`.
- `!pool <code>` (run in a puzzle channel) → resolve the channel's puzzle,
  `PuzzleJsonDb.tag_puzzle_with_edge(puzzle.id, round.id, 'aggregates-into')`, then refresh.
- **Acceptance:** add a thin test (mock the service) asserting each command resolves the
  round and calls the right service method with the right args, and that `!source_mode foo`
  is rejected without calling the service. Full suite green.
- Commit: `M7 phase 5: !nest / !source_mode / !pool setter commands`.

## Phase 6 — SPA setters (Sonnet 4.6)

- `bot/web/data.py`: surface `source_mode` in `get_rollups_by_round` (add to the SELECT and
  the returned dict) and carry it onto each group in `build_board`.
- `bot/web/actions.py` + `bot/web/api.py`: `PATCH /api/rollups/<round_id>` (organiser-gated,
  mirror `rebuild_rollup`'s round→hunt resolution + auth) setting `source_mode` then
  `refresh_meta`; and a nesting setter endpoint calling `set_parent_group`.
- `frontend/src/types.ts`: add `source_mode` to the board group type.
- `frontend/src/api.ts` + `frontend/src/components/BoardGroup.tsx`: a Mantine `Select`
  (round/subtree/explicit) + a nest control, organiser-gated like the rebuild button;
  invalidate `["hunt", uid]` on success.
- **Acceptance:** `bot/web/data.py` board-payload test asserts `source_mode` present (extend
  `tests/test_build_board.py`); `pipenv --version >/dev/null` then
  `cd frontend && npx tsc --noEmit` clean; full Python suite green.
- Commit: `M7 phase 6: SPA source_mode + nesting setters`.

---

## Verification (whole task)

- Full suite green every phase boundary: `timeout 180 pipenv run python -m pytest tests/ -q`.
- `migrate.py status` shows `004` and `005` applied.
- Frontend typechecks: `cd frontend && npx tsc --noEmit`.
- **Runaround acceptance (the done-definition):** the unit assertions in
  `test_rollup_source_mode.py` prove a roll-up produces the expected answer set in all
  three modes (`round` via feeds-meta, `subtree` via the branch, `explicit` via the pool).
  If you want an end-to-end check, manually: `!nest`, `!source_mode <code> subtree`, solve
  a branch feeder, confirm the meta sheet rebuilds with the branch's solved answers and the
  dashboard stale badge clears.

## Risks (carry forward)

1. **`PuzzleData.tags` typing** — biggest regression risk. Keep `List[int]`; add the
   parallel `tag_edges` map only. Never retype `tags`.
2. **`tags` uniqueness** — `005` adds `UNIQUE KEY (round_id, puzzle_id)` after a dedup. The
   dedup is information-safe ONLY while all rows are the default `feeds-meta` (true now);
   if you somehow run it after `aggregates-into` rows exist it could drop a real edge —
   `005` must land before any edge writer runs. If the `ALTER` fails on a duplicate the
   dedup `DELETE` missed, write `blocker.md` (don't loosen the key).
3. **Metameta compat** — `add_metametapuzzle_data` stays intact (its existing tests mock
   it); after 004 + Phase 4 metameta rounds render via the `subtree` branch, which must
   reproduce the hunt's solved-answer view. If a real metameta round's branch isn't fully
   nested under it via `parent_group_id`, its subtree may differ from the old hunt-wide
   scan — flag in `blocker.md` if a seeded metameta case regresses (a one-time nesting
   backfill may be needed; do NOT invent it without confirming).
4. **Channel-less groups out of scope** — resolve groups by `meta_code` only.
