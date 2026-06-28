# Current Spec — Multi-edge tag membership

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1  (branch: feature/multi-edge-membership)
Target model — Phase 1 (tests): **Opus 4.8**;  Phase 2…10 (implementation): **Sonnet 4.6**
Started: 2026-06-28
Status: ready for handoff — Phase 1 (tests)

Authoritative references already in this worktree (READ them):
- Design: `docs/superpowers/specs/2026-06-28-multi-edge-membership-design.md`
- **Implementation plan with all exact code blocks: `docs/superpowers/plans/2026-06-28-multi-edge-membership.md`** — Phase 2+ below points at its Tasks by number; apply those code blocks verbatim.

---

## Before you start (executing model)

- **Execute only the current phase.** Do not look ahead and implement later phases.
- **Phase 1 is tests only — write NO production code.** Confirm each test fails for the *right*
  reason (a method/attribute that doesn't exist yet, or an assertion on not-yet-implemented
  behavior). A `KeyError`/`AttributeError on a column`/import fatal/“undefined function” means a
  **wrong assumption in the test** — fix the test, do not add scaffolding to route around it.
- On any **unexpected** failure, invoke `superpowers:systematic-debugging` before changing anything.
- Before claiming a phase done, invoke `superpowers:verification-before-completion` and paste the
  acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix,
  write `.ai/active/blocker.md` (what you ran, output, hypothesis) and **STOP**.
- **Worktree path discipline:** every edit/command runs against this worktree
  (`.claude/worktrees/working-1`), never the main checkout. The persistent venv/secrets/
  `node_modules` are already in place here — no bootstrap needed.

---

## Goal

Let a `(puzzle, tag_group)` membership carry several typed edges at once (a puzzle can both
`feeds` and `informs` the same target), and make the evidence-board link auto-add an `informs`
edge that never evicts `feeds`.

## In scope

- Widen `tags` unique key to `(tag_group_id, puzzle_id, edge_type)` (migration 020).
- `PuzzleData.tag_edges`: `Dict[int, str]` → `Dict[int, set]` behind accessor methods.
- Edge-granular store primitives; service readers/primitives add/remove single edges; pooled
  exclusivity (`none` vs `feeds`/`informs`).
- `tag_edge_only` (the `!feeds_only` / `!informs_only` / `!groups_only_with` path) strips only the
  *named* edge type from OTHER groups (per-edge `untag_from(..., edge=...)`), leaving any coexisting
  edge on those groups intact; it ADDS the edge to the target (keeping the target's other edges).
  The bang `!remove_tag` (numeric group id) stays whole-membership — per-edge removal is the web
  debug menu (Phase 10) + `_only`.
- Contribution table renders feeds + informs rows per member.
- Board API returns `rounds[].edges` (list) instead of scalar `edge_type`; update all readers.
- Frontend debug badge renders the edge set; per-edge toggle menu.
- Evidence link (`update_evidence`) auto-adds an `informs` edge + unlink lifecycle.

## Out of scope (DO NOT touch)

- `tag_group.target_puzzle_id` and the target model — left exactly as-is.
- `evidence_items.target_puzzle_id` / `rollups.target_puzzle_id` — unrelated columns; do not change.
- Any refactor not named in a step. No drive-by cleanups.
- `bulk_re_edge` semantics stay *replace* (flip feeders feeds→informs), not additive.

## Relevant files

Write/modify:
- `bot/store/puzzle_data.py` — `tag_edges` type + accessors
- `bot/store/mysqldb.py` — `get_tags`, `tag_puzzle_with_edge`, `delete_tag`, new `delete_tag_edge`, `bulk_update_edge_type`, `get_all_from_tag_group`, `MySQLPuzzleJsonDb.commit`
- `bot/core/puzzle_service.py` — readers + `tag_with_edge`/`tag_edge_only`/`untag_from`/`bulk_re_edge`, new `link_informs_for_evidence`/`unlink_informs_for_evidence`
- `bot/core/contribution.py` — `contribution_rows`
- `bot/cogs/puzzles_gsheet.py` — `render_contribution` self-edge guard (`:1447`)
- `bot/web/data.py` — `get_hunt_puzzles` rounds aggregation (`:221`), `derive_meta_kind` (`:290`), `:416`, `:444`
- `bot/web/actions.py` — `retag_puzzle` remove-edge (`:872`), `update_evidence` (`:1641`)
- `migrations/020_multi_edge_tags.sql` (create)
- `frontend/src/types.ts`, `frontend/src/components/PuzzleTable.tsx`, `frontend/src/components/DragBoard.tsx`, `frontend/src/api.ts`
- Tests: `tests/test_puzzle_data_edges.py` (new), `tests/test_contribution.py`, `tests/test_evidence_informs.py` (new), `tests/test_edge_web.py`, `tests/test_edge_primitives.py`, `tests/test_meta_kind.py`

Read only (for context / seams):
- `tests/conftest.py` (read only) — `make_puzzle`, `make_round`, `make_hunt` factories
- `AGENTS.md` (read only) — test harness rules
- `docs/superpowers/plans/2026-06-28-multi-edge-membership.md` (read only) — exact impl code

## Constraints

- Do not touch files outside the list above.
- Do not refactor or rename beyond what a step states.
- `tag_edges` becomes `Dict[int, set]`. After Phase 2, NO code may treat it as a string:
  grep `tag_edges` across `bot/` and `tests/` and ensure no `.get(...) == "..."` or
  `[gid] = "..."` patterns remain — use `has_edge`/`edges_for`/`add_edge`/`remove_edge`.
- No constructor signature changes are planned. If you find you need one, write `blocker.md`
  and STOP (grep `PuzzleData(` / `TagGroupData(` first).

## Test constraints (inlined — you do NOT read global instructions)

- **Runner (the gate):** `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
  then read `/tmp/test.log`. Focused: `timeout 120 pipenv run python -m pytest tests/test_X.py -q > /tmp/t.log 2>&1`.
  Use **`pipenv run python -m pytest`** (plain `pytest` can't import `bot`). Single statement, no
  `; echo` tail. A killed run exits 124 (likely a hang) — isolate per file.
- **Harness (pin these; invent none):**
  - `pytest.ini` sets `asyncio_mode = auto` → `async def test_*` needs no decorator (existing files
    still use `@pytest.mark.asyncio`; match the file you edit).
  - Factories, not fixtures: import `make_puzzle`, `make_round` (a `TagGroupData`), `make_hunt`
    from `tests/conftest.py`.
  - Patch DAOs at the **service import site**: `patch("bot.core.puzzle_service.PuzzleJsonDb")`,
    `...TagGroupJsonDb`, `...HuntJsonDb` (NOT `bot.store`). `MagicMock` auto-creates new attrs, so a
    not-yet-existing DAO method (e.g. `delete_tag_edge`) can be stubbed on the mock in Phase 1.
  - `AsyncMock()` for awaitables; assert with `assert_awaited_once()` / `assert_not_awaited()`.
- **Anti-patterns — forbidden:**
  - Reuse exact helpers/seams; invent none.
  - NEVER write a loop that resolves state "until" a condition. If tempted, write `blocker.md`.
  - NEVER modify test infrastructure or stub randomness unless a step says to.
  - Confirm every attribute/payload key exists before use (grep the dataclass/handler).
  - **Each test file defines or imports every helper it calls** — never rely on a top-level
    function in a sibling file (the gate shards by file; a cross-file helper becomes an
    “undefined function” fatal). `tests/test_edge_primitives.py` already keeps helpers local — keep
    new files self-contained too.
  - When asserting rendered output, assert the exact string AND the absence of the wrong one.

---

## PHASE 1 — Write ALL failing tests (Opus 4.8). NO production code. Commit at the end.

Write each test/edit below, then run the per-file acceptance. Each must FAIL for the right reason.

### 1a. `tests/test_puzzle_data_edges.py` (CREATE)

Near-verbatim:
```python
from bot.store.puzzle_data import PuzzleData


def test_add_edge_tracks_set_and_membership():
    p = PuzzleData()
    p.add_edge(5, "feeds")
    p.add_edge(5, "informs")
    assert p.edges_for(5) == {"feeds", "informs"}
    assert 5 in p.tags
    assert p.tags.count(5) == 1


def test_has_edge():
    p = PuzzleData()
    p.add_edge(5, "feeds")
    assert p.has_edge(5, "feeds") is True
    assert p.has_edge(5, "informs") is False
    assert p.has_edge(9, "feeds") is False


def test_remove_edge_drops_membership_when_empty():
    p = PuzzleData()
    p.add_edge(5, "feeds")
    p.add_edge(5, "informs")
    p.remove_edge(5, "feeds")
    assert p.edges_for(5) == {"informs"}
    assert 5 in p.tags
    p.remove_edge(5, "informs")
    assert p.edges_for(5) == set()
    assert 5 not in p.tags
    assert 5 not in p.tag_edges


def test_edges_for_missing_returns_empty_set():
    assert PuzzleData().edges_for(123) == set()
```
Acceptance: `timeout 120 pipenv run python -m pytest tests/test_puzzle_data_edges.py -q > /tmp/t.log 2>&1`
→ FAIL with `AttributeError: 'PuzzleData' object has no attribute 'add_edge'`.

### 1b. `tests/test_contribution.py` (MODIFY)

This file's `_p(...)` helper passes `tag_edges` as `{gid: "feeds"}` (strings). Convert EVERY existing
call's edge value to a **set** (`{5: "feeds"}` → `{5: {"feeds"}}`, etc.) — grep the file for
`tag_edges={` and `tag_edges=` and update each. Then add:
```python
def test_feeds_and_informs_member_emits_both_rows():
    p = _p(8, "Octo", solution="ANSWER", tag_edges={5: {"feeds", "informs"}})
    evidence = [{"id": 1, "source_puzzle_id": 8, "content": "a clue"}]
    rows = contribution_rows(tag_group_id=5, members=[p], evidence=evidence)
    keys = {r[0] for r in rows}
    assert "feeds:8" in keys
    assert "informs:8:1" in keys


def test_bare_informs_with_no_evidence_still_renders():
    p = _p(9, "Nine", tag_edges={5: {"informs"}})
    rows = contribution_rows(tag_group_id=5, members=[p], evidence=[])
    assert rows == [["informs:9", "Nine", "Information", ""]]
```
Acceptance: `timeout 120 pipenv run python -m pytest tests/test_contribution.py -q > /tmp/t.log 2>&1`
→ FAIL: the new + converted tests fail because `contribution_rows` still compares the edge as a
string (`edge == "feeds"` is False for a set), so it emits no rows. Right reason (behavior).

### 1c. `tests/test_evidence_informs.py` (CREATE — self-contained)

```python
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from tests.conftest import make_hunt, make_puzzle, make_round


@pytest.mark.asyncio
async def test_link_adds_informs_edge_without_evicting_feeds():
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)
    cog.service.refresh_target = AsyncMock()
    cog.service.refresh_targets = AsyncMock(return_value=[])
    source = make_puzzle(id=8, tags=[5]); source.tag_edges = {5: {"feeds"}}
    group = make_round(id=5, target_puzzle_id=9)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        mpdb.get_by_attr.return_value = source
        mrdb.get_by_attr.return_value = group
        mpdb.tag_puzzle_with_edge.return_value = None
        mpdb.commit.return_value = None
        mhdb.get_by_attr.return_value = make_hunt()
        await cog.service.link_informs_for_evidence(
            make_hunt(), source_puzzle_id=8, target_puzzle_id=9)
    assert source.has_edge(5, "informs")
    assert source.has_edge(5, "feeds")
```
Acceptance: `timeout 120 pipenv run python -m pytest tests/test_evidence_informs.py -q > /tmp/t.log 2>&1`
→ FAIL: `AttributeError: ... has no attribute 'link_informs_for_evidence'` (and `has_edge` missing).

### 1d. `tests/test_edge_web.py` (MODIFY — add two tests; reuse the file's `web` fixture and `_edge_of`)

```python
@pytest.mark.asyncio
async def test_retag_add_keeps_existing_edge(web):
    own = make_round(id=7, group_code="X", category_id=999, target_puzzle_id=0)
    own.hunt_id = 10
    web.TagGroupJsonDb.get_by_attr.return_value = own
    resp = await web.client.post(
        "/api/puzzles/5/meta", json={"group_code": "X", "mode": "add", "edge": "informs"}
    )
    assert resp.status_code == 200
    web.svc.tag_with_edge.assert_called_once()
    assert _edge_of(web.svc.tag_with_edge.call_args) == "informs"


@pytest.mark.asyncio
async def test_retag_remove_single_edge(web):
    own = make_round(id=7, group_code="X", category_id=999, target_puzzle_id=0)
    own.hunt_id = 10
    web.TagGroupJsonDb.get_by_attr.return_value = own
    resp = await web.client.post(
        "/api/puzzles/5/meta", json={"group_code": "X", "mode": "remove", "edge": "informs"}
    )
    assert resp.status_code == 200
    _, kwargs = web.svc.untag_from.call_args
    assert kwargs.get("edge") == "informs"
```
Acceptance: `timeout 120 pipenv run python -m pytest tests/test_edge_web.py -k "keeps_existing or remove_single" -q > /tmp/t.log 2>&1`
→ FAIL: `remove` currently calls `untag_from` with no `edge` kwarg. (The `add` test may already pass
if `tag_with_edge` is dispatched — that is fine; the remove test must fail.)

### 1e. `tests/test_edge_primitives.py` (MODIFY)

Apply the conversions in **plan Task 4, Step 3** exactly (the `_service_mocks` `delete_tag_edge`
stub; every `tag_edges = {gid: "x"}` → `{gid: {"x"}}`; assertions `== "x"` → `== {"x"}`; the
idempotent test becomes “adds a second edge” asserting `{"feeds","informs"}`; the two new
pooled-exclusivity tests; AND the `_only` strip test `test_strips_only_named_edge_leaving_coexisting`
in `TestTagEdgeOnly`, which locks that `feeds_only` removes only the feeds edge from another group
and leaves a coexisting informs there).
Acceptance: `timeout 120 pipenv run python -m pytest tests/test_edge_primitives.py -q > /tmp/t.log 2>&1`
→ FAIL on the set assertions (current code stores a string and replaces the slot).

### Phase 1 commit

Confirm each file also *imports cleanly* under the gate (no collection error):
`timeout 300 pipenv run python -m pytest tests/test_puzzle_data_edges.py tests/test_contribution.py tests/test_evidence_informs.py tests/test_edge_web.py tests/test_edge_primitives.py -q > /tmp/t.log 2>&1`
(expected: failures as above, NO collection/import errors). Then:
```bash
git add tests/test_puzzle_data_edges.py tests/test_contribution.py tests/test_evidence_informs.py tests/test_edge_web.py tests/test_edge_primitives.py
git commit -m "test(edges): failing tests for multi-edge membership (Phase 1)"
```

---

## PHASE 2…10 — Implementation (Sonnet 4.6)

Each phase: open the named files, apply the exact code from the referenced **plan Task**
(`docs/superpowers/plans/2026-06-28-multi-edge-membership.md`), run the acceptance command, commit.
Do NOT modify the Phase-1 tests except where a phase explicitly says so (e.g. board-shape fixtures).

### Phase 2 — PuzzleData accessors + type  → plan **Task 1** (skip its Steps 1–2; tests exist)
- Apply Task 1 Step 3 (field type `Dict[int, set]` + `add_tag`/`edges_for`/`has_edge`/`add_edge`/`remove_edge`).
- Acceptance: `timeout 120 pipenv run python -m pytest tests/test_puzzle_data_edges.py -q > /tmp/t.log 2>&1` → PASS.
- Commit: `git commit -am "feat(edges): PuzzleData.tag_edges as edge set with accessors"`.

### Phase 3 — Migration 020  → plan **Task 2**
- Create `migrations/020_multi_edge_tags.sql` (Task 2 Step 2). Confirm `019` is the head first
  (`ls migrations/ | sort | tail -3`); if a `020_*` exists, use the next free number.
- Apply it to `puzzlebot_test` the way conftest expects the schema (see `AGENTS.md`). The pytest
  suite is mocked and does NOT need the migration to run, but the manual smoke (Verification) does.
- Acceptance: migration file present + applies cleanly. Commit: `git add migrations/020_multi_edge_tags.sql && git commit -m "feat(edges): migration 020 widens tags unique key"`.

### Phase 4 — Store primitives + service readers/primitives  → plan **Task 3 + Task 4**
- Apply ALL of Task 3 (store: `get_tags`, `tag_puzzle_with_edge` docstring, new `delete_tag_edge`,
  `get_all_from_tag_group`, `bulk_update_edge_type`, `commit`) and ALL of Task 4 (service readers
  table + the four primitives + `_drop_local_edge` + self-`target` `add_edge`).
- These land together (the conversion is red mid-way — that's expected).
- Acceptance: `timeout 120 pipenv run python -m pytest tests/test_edge_primitives.py -q` → PASS, then
  the full gate `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` → PASS.
  If any failure mentions `tag_edges`, grep and fix that site to use `has_edge`/`edges_for`.
- Commit: `git commit -am "feat(edges): edge-set store primitives + service readers"`.

### Phase 5 — Contribution rendering  → plan **Task 5** (skip Steps 1–2; tests exist)
- Apply Task 5 Step 3 (`contribution_rows` per-edge) and Step 4 (`render_contribution` guard `:1447`).
- Acceptance: `timeout 120 pipenv run python -m pytest tests/test_contribution.py -q` → PASS.
- Commit: `git commit -am "feat(edges): contribution table renders feeds + informs per member"`.

### Phase 6 — Board API + meta-kind shape migration  → plan **Task 6**
- Apply Task 6 Step 3 (aggregate `get_hunt_puzzles` rounds into `edges`), the other `data.py`
  readers (`derive_meta_kind` `:290`, `:416`, `:444`), and update `tests/test_meta_kind.py`
  fixtures (`"edge_type": "feeds"` → `"edges": ["feeds"]`) so its `derive_meta_kind` tests stay green.
- (No new Phase-1 test for the aggregation itself — it is covered by the updated `test_meta_kind`
  consumer tests + the frontend + manual smoke.)
- Acceptance: full gate `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` → PASS.
- Commit: `git commit -am "feat(edges): board API returns rounds[].edges"`.

### Phase 7 — Frontend badge + DragBoard  → plan **Task 7**
- Apply Task 7: `types.ts` (`edges: string[]`), `PuzzleTable.tsx` (`edgeCodes`/`edgeCode`/
  `edgeColor`/`edgeTitle`, the `disabled` line), and `DragBoard.tsx:87`
  (`ownTarget?.edges.includes("feeds")`).
- Acceptance: `cd frontend && timeout 180 npx tsc --noEmit` → exit 0.
- Commit: `git commit -am "feat(edges): debug badges render edge set; DragBoard reads edges"`.

### Phase 8 — retag remove single edge  → plan **Task 8** (skip Steps 1–2; tests exist)
- Apply Task 8 Step 3 (`untag_from(..., edge=explicit_edge)` in the `remove` branch).
- Acceptance: `timeout 120 pipenv run python -m pytest tests/test_edge_web.py -q` → PASS.
- Commit: `git commit -am "feat(edges): retag remove accepts an optional single edge"`.

### Phase 9 — Evidence informs hint  → plan **Task 9** (skip Steps 1–2; test exists)
- Apply Task 9 Step 3 (`link_informs_for_evidence`/`unlink_informs_for_evidence`) and Step 4
  (`update_evidence` wiring + bang `log_clues` parity).
- Acceptance: `timeout 120 pipenv run python -m pytest tests/test_evidence_informs.py tests/test_edge_web.py -q` → PASS.
- Commit: `git commit -am "feat(edges): evidence link auto-adds an informs hint"`.

### Phase 10 — Frontend per-edge menu  → plan **Task 10**
- Apply Task 10 (api.ts edge-on-remove; `EdgeBadge` per-edge toggles; `onRemove(edge?)`).
- Acceptance: `cd frontend && timeout 180 npx tsc --noEmit` → exit 0.
- Commit: `git commit -am "feat(edges): per-edge toggle menu for debug edges"`.

## Final verification (run before declaring done)

- `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` → all pass.
- `cd frontend && timeout 180 npx tsc --noEmit` → exit 0.
- Manual smoke per the plan's "Verification" section (test site): feeder set to Feeds+Informs shows
  `F+I` and both rows render on the target tab; evidence link surfaces info without a manual tag and
  without evicting feeds; `bulk_re_edge` makes no duplicate rows
  (`SELECT tag_group_id,puzzle_id,COUNT(*) FROM tags GROUP BY 1,2 HAVING COUNT(*)>1` shows only
  intended multi-edge pairs).
