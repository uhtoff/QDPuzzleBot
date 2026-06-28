# Multi-edge Tag Membership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `(puzzle, tag_group)` membership carry several typed edges at once (so a puzzle can both `feeds` and `informs` the same target), and make the evidence-board link auto-add an `informs` edge that never evicts `feeds`.

**Architecture:** Widen the `tags` unique key from `(tag_group_id, puzzle_id)` to `(tag_group_id, puzzle_id, edge_type)`. In memory, `PuzzleData.tag_edges` becomes `Dict[int, set[str]]` behind accessor methods (`has_edge`/`edges_for`/`add_edge`/`remove_edge`). Edge writes become add/remove of a single edge instead of replacing a slot. `tag_edges` is the single source of truth; `puzzle.tags` mirrors its keys. Contribution rendering iterates per-edge so feeds+informs both render.

**Tech Stack:** Python 3 / discord.py / Quart (bot + web), MySQL (`tags` table), pytest (`pipenv run python -m pytest`), React + Mantine + TanStack Query (frontend, `tsc --noEmit`).

**Spec:** `docs/superpowers/specs/2026-06-28-multi-edge-membership-design.md`

**Conventions:**
- Run a single focused test: `timeout 120 pipenv run python -m pytest tests/test_X.py -q > /tmp/t.log 2>&1` then read `/tmp/t.log`.
- Run the suite: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/t.log 2>&1`.
- Tests use plain factory helpers from `tests/conftest.py` (`make_puzzle`, `make_round`, `make_hunt`), NOT fixtures. `asyncio_mode = auto`, so `async def test_*` needs no decorator (existing tests use `@pytest.mark.asyncio` anyway — match the file you edit).
- Frontend: `cd frontend && timeout 180 npx tsc --noEmit`.

---

## PHASE 1 — Multi-edge data model

### Task 1: `PuzzleData.tag_edges` → `set` with accessor helpers

**Files:**
- Modify: `bot/store/puzzle_data.py:48` (field) and add methods near `add_tag` (`:86`)
- Test: `tests/test_puzzle_data_edges.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_puzzle_data_edges.py`:
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
    p = PuzzleData()
    assert p.edges_for(123) == set()
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_puzzle_data_edges.py -q > /tmp/t.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleData' object has no attribute 'add_edge'`.

- [ ] **Step 3: Change the field type and add accessors**

In `bot/store/puzzle_data.py`, change line 48:
```python
    tag_edges: Dict[int, set] = field(default_factory=dict)  # tag_group_id -> set of edge_type
```
Replace the `add_tag` method (`:86-88`) with:
```python
    def add_tag(self, tag):
        if tag not in self.tags:
            self.tags.append(tag)

    def edges_for(self, tag_group_id) -> set:
        """The set of edge types this puzzle has to a group ({} if not a member)."""
        return self.tag_edges.get(tag_group_id, set())

    def has_edge(self, tag_group_id, edge) -> bool:
        return edge in self.tag_edges.get(tag_group_id, set())

    def add_edge(self, tag_group_id, edge):
        """Add one edge to a group (in-memory), keeping tags + tag_edges in sync."""
        self.tag_edges.setdefault(tag_group_id, set()).add(edge)
        self.add_tag(tag_group_id)

    def remove_edge(self, tag_group_id, edge):
        """Remove one edge; drop the whole membership if no edges remain."""
        edges = self.tag_edges.get(tag_group_id)
        if not edges:
            return
        edges.discard(edge)
        if not edges:
            self.tag_edges.pop(tag_group_id, None)
            if tag_group_id in self.tags:
                self.tags.remove(tag_group_id)
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_puzzle_data_edges.py -q > /tmp/t.log 2>&1`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/store/puzzle_data.py tests/test_puzzle_data_edges.py
git commit -m "feat(edges): PuzzleData.tag_edges as a per-group edge set with accessors"
```

---

### Task 2: Migration 020 — widen the `tags` unique key

**Files:**
- Create: `migrations/020_multi_edge_tags.sql`

- [ ] **Step 1: Confirm the next migration number**

Run: `ls migrations/ | sort | tail -3`
Expected: highest is `019_target_edge.sql`. If a `020_*` already exists (parallel worktree), use the next free number and adjust the filename below.

- [ ] **Step 2: Write the migration**

Create `migrations/020_multi_edge_tags.sql`:
```sql
-- Multi-edge memberships: a (puzzle, group) pair may carry several typed edges
-- (e.g. feeds + informs). Widen the uniqueness to include edge_type. Existing rows
-- are already unique under the wider key (they were unique under (tag_group_id,
-- puzzle_id) and edge_type is NOT NULL), so no dedup/backfill is required.
ALTER TABLE `tags`
  DROP KEY `tag_uq`,
  ADD UNIQUE KEY `tag_uq` (`tag_group_id`, `puzzle_id`, `edge_type`);
```

- [ ] **Step 3: Apply it to the test DB and verify**

Run the project's migration runner against `puzzlebot_test` the same way the suite's conftest expects the schema (check `AGENTS.md` "migrations" / how `conftest.py` provisions the DB; if migrations are applied by a script, run it). Verify:
Run: `pipenv run python -c "import bot.store.mysqldb as m" ` (smoke import) — and confirm the suite still imports in Task 4.
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add migrations/020_multi_edge_tags.sql
git commit -m "feat(edges): migration 020 widens tags unique key to include edge_type"
```

---

### Task 3: Store primitives become edge-granular

**Files:**
- Modify: `bot/store/mysqldb.py` — `get_tags` (`:417`), `tag_puzzle_with_edge` (`:452`), `delete_tag` (`:466`), add `delete_tag_edge`, `bulk_update_edge_type` (`:478`), `get_all_from_tag_group` (`:343`), `MySQLPuzzleJsonDb.commit` (`:490`)

These are DB-integration methods (verified by the full suite against `puzzlebot_test`, not isolated unit tests). Make all edits, then run the suite in Task 4's verification.

- [ ] **Step 1: `get_tags` builds a set per group**

Replace the loop in `get_tags` (`:422-424`):
```python
        for row in rows:
            gid = row['tag_group_id']
            if gid not in puzzle.tags:
                puzzle.tags.append(gid)
            puzzle.tag_edges.setdefault(gid, set()).add(row['edge_type'] or "feeds")
```

- [ ] **Step 2: `tag_puzzle_with_edge` is add-only; add `delete_tag_edge`**

`tag_puzzle_with_edge` (`:452`) keeps its INSERT but the wider key makes it add a distinct `(g,p,edge)` row (the `ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)` is now a no-op on exact dup). Update the docstring:
```python
    def tag_puzzle_with_edge(self, puzzle_id, tag_group_id, edge_type):
        """Add one membership edge (idempotent). With migration 020's
        UNIQUE (tag_group_id, puzzle_id, edge_type), each edge is its own row, so a
        puzzle may hold several edges to one group."""
```
Add a new method directly after `delete_tag` (`:476`):
```python
    def delete_tag_edge(self, puzzle_id, tag_group_id, edge_type):
        """Delete a single typed edge row (leaves the pair's other edges intact)."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "DELETE FROM tags WHERE puzzle_id = %s AND tag_group_id = %s AND edge_type = %s",
                (puzzle_id, tag_group_id, edge_type),
            )
        finally:
            cursor.close()
        self.mydb.commit()
```
(`delete_tag` stays as the "remove the whole membership" primitive — all edges for the pair.)

- [ ] **Step 3: `get_all_from_tag_group` dedupes members**

Replace the query (`:347`) so a feeds+informs puzzle is returned once:
```python
        cursor.execute(
            "SELECT puzzles.* FROM puzzles WHERE puzzles.id IN "
            "(SELECT puzzle_id FROM tags WHERE tag_group_id = %s)",
            (tag_group_id,),
        )
```

- [ ] **Step 4: `bulk_update_edge_type` is collision-safe**

`UPDATE tags SET edge_type=X WHERE tag_group_id=G` can now violate the unique key (two rows for a pair both becoming `X`). Reimplement as delete-then-insert of the converted edge, scoped to what `bulk_re_edge` needs (convert feeds→new for feeders):
```python
    def bulk_update_edge_type(self, tag_group_id, new_edge):
        """Convert members' feeds/informs/none edges to new_edge for a group, without
        creating duplicate (tag_group_id, puzzle_id, new_edge) rows. The target's
        'target' self-edge is left untouched here (bulk_re_edge re-pins it)."""
        cursor = self.mydb.cursor()
        try:
            # Puzzles that currently contribute (feeds/informs/none) to this group.
            cursor.execute(
                "SELECT DISTINCT puzzle_id FROM tags "
                "WHERE tag_group_id = %s AND edge_type IN ('feeds','informs','none')",
                (tag_group_id,),
            )
            pids = [r[0] for r in cursor.fetchall()]
            if pids:
                placeholders = ",".join(["%s"] * len(pids))
                cursor.execute(
                    f"DELETE FROM tags WHERE tag_group_id = %s "
                    f"AND edge_type IN ('feeds','informs','none') "
                    f"AND puzzle_id IN ({placeholders})",
                    (tag_group_id, *pids),
                )
                cursor.executemany(
                    "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)",
                    [(tag_group_id, pid, new_edge) for pid in pids],
                )
        finally:
            cursor.close()
        self.mydb.commit()
```

- [ ] **Step 5: `commit` reconciles edges, not just groups**

Replace the tag-reconcile body of `MySQLPuzzleJsonDb.commit` (`:492-512`) so the `tags` table matches `puzzle.tag_edges` exactly (the source of truth):
```python
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(
            "SELECT tag_group_id, edge_type FROM tags WHERE puzzle_id = %s",
            (puzzle.id,),
        )
        db_pairs = {(r['tag_group_id'], r['edge_type']) for r in cursor.fetchall()}
        mem_pairs = {
            (gid, edge)
            for gid, edges in puzzle.tag_edges.items()
            for edge in edges
        }
        for gid, edge in db_pairs - mem_pairs:
            cursor.execute(
                "DELETE FROM tags WHERE puzzle_id = %s AND tag_group_id = %s AND edge_type = %s",
                (puzzle.id, gid, edge),
            )
        for gid, edge in mem_pairs - db_pairs:
            cursor.execute(
                "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)",
                (gid, puzzle.id, edge),
            )
        cursor.close()
        self.mydb.commit()
```

- [ ] **Step 6: Do NOT commit yet**

These store changes leave the suite RED on their own (e.g. `get_tags` now yields sets but the
service readers still compare to strings). They are an atomic unit with Task 4 and are
committed together at the end of Task 4, after the full suite is green. Leave them staged in
the working tree.

---

### Task 4: Service readers/writers use edge sets

**Files:**
- Modify: `bot/core/puzzle_service.py` (readers + the four edge primitives)
- Modify: `tests/test_edge_primitives.py` (assertions now expect sets)

- [ ] **Step 1: Convert the single-edge readers**

Apply each exact change in `bot/core/puzzle_service.py`:

| Line | Before | After |
|------|--------|-------|
| 114 | `p.tag_edges.get(tag_group_id) == "feeds"` | `p.has_edge(tag_group_id, "feeds")` |
| 125 | `p.tag_edges.get(tag_group_id) == "informs"` | `p.has_edge(tag_group_id, "informs")` |
| 138 | `p.tag_edges.get(tag_group.id) == "feeds" and p.id in target_puzzle_ids` | `p.has_edge(tag_group.id, "feeds") and p.id in target_puzzle_ids` |
| 608 | `if p.tag_edges.get(tag_group.id) == "none"` | `if p.has_edge(tag_group.id, "none")` |
| 621 | `if p.tag_edges.get(tag_group.id, "feeds") in contributing` | `if (p.edges_for(tag_group.id) or {"feeds"}) & set(contributing)` |
| 929 | `p.tag_edges.get(rnd.id, "feeds") == "feeds"` | `(p.has_edge(rnd.id, "feeds") or not p.edges_for(rnd.id))` |

For `_sole_informs_target` (`:1828-1834`) and `refresh_informs_targets` (`:1906-1911`), replace the `for gid, edge in (puzzle.tag_edges or {}).items(): if edge == "informs"` loops with:
```python
        for gid in list(puzzle.tags):
            if not puzzle.has_edge(gid, "informs"):
                continue
            group = TagGroupJsonDb.get_by_attr(id=gid)
            ...   # body unchanged
```

- [ ] **Step 2: Convert the edge primitives**

`tag_with_edge` (`:685-688`) — add the edge instead of replacing:
```python
        PuzzleJsonDb.tag_puzzle_with_edge(puzzle.id, group.id, edge)
        # Pooled exclusivity: 'none' and contributing edges can't coexist.
        if edge in ("feeds", "informs"):
            self._drop_local_edge(puzzle, group, "none")
        elif edge == "none":
            for e in ("feeds", "informs"):
                self._drop_local_edge(puzzle, group, e)
        puzzle.add_edge(group.id, edge)
        PuzzleJsonDb.commit(puzzle)
```
Add a small helper on the service (near `tag_with_edge`):
```python
    def _drop_local_edge(self, puzzle, group, edge):
        """Remove one edge in DB + memory (pooled-exclusivity helper)."""
        if puzzle.has_edge(group.id, edge):
            PuzzleJsonDb.delete_tag_edge(puzzle.id, group.id, edge)
            puzzle.remove_edge(group.id, edge)
```
`untag_from` (`:746-754`) — `edge=None` removes the whole membership; `edge=X` removes that one edge:
```python
        if edge is not None and not puzzle.has_edge(group.id, edge):
            return
        if puzzle.is_target and len(puzzle.tags) <= 1 and (
            edge is None or puzzle.edges_for(group.id) == {edge}
        ):
            raise ValueError("cannot remove the last tag from a metapuzzle")
        if edge is None:
            PuzzleJsonDb.delete_tag(puzzle.id, group.id)
            puzzle.tag_edges.pop(group.id, None)
            if group.id in puzzle.tags:
                puzzle.tags.remove(group.id)
        else:
            PuzzleJsonDb.delete_tag_edge(puzzle.id, group.id, edge)
            puzzle.remove_edge(group.id, edge)
```
`tag_edge_only` (`:705-709`) — the sweep compares membership of the swept edge:
```python
        for gid in list(puzzle.tags):
            if gid != group.id and edge in puzzle.edges_for(gid):
                other_group = TagGroupJsonDb.get_by_attr(id=gid)
                if other_group is not None:
                    await self.untag_from(puzzle, other_group, edge=edge)
                    other_groups.append(other_group)
```
`bulk_re_edge` counter (`:776-779`) — count each member's contributing edges:
```python
        counter = {}
        for p in members:
            for current in (p.edges_for(tag_group.id) or {"feeds"}):
                if current in ("feeds", "informs", "none"):
                    counter[current] = counter.get(current, 0) + 1
```
The self-`target` assigns at `:824-825`, `:1362-1363`, `:1606-1608` change `puzzle.tag_edges[gid] = "target"` → `puzzle.add_edge(gid, "target")`.

- [ ] **Step 3: Update existing primitive tests for sets**

In `tests/test_edge_primitives.py`, the `_service_mocks` helper must stub the new seam — add inside the `with patch(...)` block (near `:65`):
```python
        mpdb.delete_tag_edge.return_value = None
```
Update each assertion/setup from scalar to set, e.g.:
- `:85,93,103,115,135,166` `puz.tag_edges = {}` → unchanged (empty dict ok).
- `:98` `assert puz.tag_edges[5] == "feeds"` → `assert puz.tag_edges[5] == {"feeds"}`
- `:109` `assert puz.tag_edges[5] == "informs"` → idempotent test now asserts coexistence: `assert puz.tag_edges[5] == {"feeds", "informs"}` (two `tag_with_edge` calls now ADD). Rename the test to `test_adds_second_edge`.
- `:119,139,171` `== "feeds"`/`"informs"` → `== {"feeds"}` / `{"informs"}`.
- `:145,156,186,195,204,213,235,239,286,288` setups `puz.tag_edges = {3: "feeds", ...}` → `{3: {"feeds"}, ...}`.

Also add two new tests to `TestTagWithEdge` covering pooled exclusivity (a spec acceptance criterion):
```python
    @pytest.mark.asyncio
    async def test_adding_feeds_clears_none(self, cog):
        puz = make_puzzle(tags=[5]); puz.tag_edges = {5: {"none"}}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.tag_with_edge(puz, grp, "feeds")
        assert puz.tag_edges[5] == {"feeds"}        # none dropped

    @pytest.mark.asyncio
    async def test_setting_none_clears_contributing(self, cog):
        puz = make_puzzle(tags=[5]); puz.tag_edges = {5: {"feeds", "informs"}}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.tag_with_edge(puz, grp, "none")
        assert puz.tag_edges[5] == {"none"}         # feeds + informs dropped
```

- [ ] **Step 4: Run the edge-primitive tests**

Run: `timeout 120 pipenv run python -m pytest tests/test_edge_primitives.py -q > /tmp/t.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Run the full suite and fix any remaining `tag_edges`-as-string assumptions**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/t.log 2>&1`
Expected: PASS. If failures reference `tag_edges`, fix that site (use `has_edge`/`edges_for`) — search with `grep -rn "tag_edges" bot/ tests/` and ensure no `.get(...) ==` or `[gid] = "..."` patterns remain.

- [ ] **Step 6: Commit the atomic store+service conversion**

This commit includes Task 3's store changes (left staged) plus this task's service changes —
the suite is green only with both:
```bash
git add bot/store/mysqldb.py bot/core/puzzle_service.py tests/test_edge_primitives.py
git commit -m "feat(edges): edge-set store primitives + service readers (add/remove)"
```

---

### Task 5: Contribution table renders all of a member's edges

**Files:**
- Modify: `bot/core/contribution.py:34-47`, `bot/cogs/puzzles_gsheet.py:1447-1451`
- Test: `tests/test_contribution.py` (extend; create if absent)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_contribution.py` (create with this import if it doesn't exist):
```python
from bot.core.contribution import contribution_rows
from tests.conftest import make_puzzle


def test_feeds_and_informs_member_emits_both_rows():
    p = make_puzzle(id=8, name="Octo", solution="ANSWER", tags=[5])
    p.tag_edges = {5: {"feeds", "informs"}}
    evidence = [{"id": 1, "source_puzzle_id": 8, "content": "a clue"}]
    rows = contribution_rows(5, [p], evidence)
    keys = {r[0] for r in rows}
    assert "feeds:8" in keys
    assert "informs:8:1" in keys


def test_bare_informs_with_no_evidence_still_renders():
    p = make_puzzle(id=9, name="Nine", tags=[5])
    p.tag_edges = {5: {"informs"}}
    rows = contribution_rows(5, [p], [])
    assert rows == [["informs:9", "Nine", "Information", ""]]
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_contribution.py -q > /tmp/t.log 2>&1`
Expected: FAIL (a feeds+informs member currently emits only one row).

- [ ] **Step 3: Iterate per edge**

Replace the member loop in `contribution_rows` (`bot/core/contribution.py:34-47`):
```python
    for p in members:
        edges = p.edges_for(tag_group_id)
        if "feeds" in edges:
            rows.append([f"feeds:{p.id}", p.name, "Feeder", _feeder_answer(p)])
        if "none" in edges:
            rows.append([f"none:{p.id}", p.name, "Group", ""])
        if "informs" in edges:
            data = ev_by_source.get(p.id, [])
            if not data:
                rows.append([f"informs:{p.id}", p.name, "Information", ""])
            else:
                for e in data:
                    rows.append([f"informs:{p.id}:{e['id']}", p.name,
                                 "Information", e["content"]])
```

- [ ] **Step 4: Update `render_contribution`'s self-edge guard**

In `bot/cogs/puzzles_gsheet.py`, replace the guard (`:1447-1451`):
```python
        contributing = [
            p for p in members
            if p.id != target_puzzle.id
            or p.edges_for(tag_group_id) & {"feeds", "informs"}
        ]
```

- [ ] **Step 5: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_contribution.py -q > /tmp/t.log 2>&1`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/core/contribution.py bot/cogs/puzzles_gsheet.py tests/test_contribution.py
git commit -m "feat(edges): contribution table renders feeds + informs rows per member"
```

---

### Task 6: Board API returns an edge set per group

**Files:**
- Modify: `bot/web/data.py:221-232` (rounds aggregation)
- Modify: `tests/conftest.py:126-134` (`make_round_ref`) and any test asserting `edge_type` on a round ref
- Test: `tests/test_dashboard_data.py` (or wherever `get_hunt_puzzles` is tested — `grep -rln get_hunt_puzzles tests/`)

- [ ] **Step 1: Write/extend the failing test**

Find the dashboard data test (`grep -rln "get_hunt_puzzles\|rounds\[" tests/`). Add a test that a puzzle with two tag rows to one group yields one round entry with both edges:
```python
async def test_multi_edge_round_aggregates_into_edges_list(...):
    # Arrange two tags rows (feeds, informs) for the same (puzzle, group) in the
    # fixture DB / fetch_all stub, then:
    puzzles = await get_hunt_puzzles(hunt_id, guild_snowflake)
    rounds = puzzles[0]["rounds"]
    assert len(rounds) == 1
    assert set(rounds[0]["edges"]) == {"feeds", "informs"}
```
(Match the existing test's arrangement style — DB-backed or `fetch_all` patched.)

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_dashboard_data.py -q > /tmp/t.log 2>&1`
Expected: FAIL — current code appends one round per tag row with scalar `edge_type`.

- [ ] **Step 3: Aggregate rounds by group id**

Replace the rounds-append block in `bot/web/data.py` (`:221-232`):
```python
        if row["tag_group_id"] is not None:
            gid = row["tag_group_id"]
            existing = next((r for r in puz["rounds"] if r["id"] == gid), None)
            if existing is None:
                existing = {
                    "id": gid,
                    "name": row["tag_group_name"],
                    "group_code": row["tag_group_group_code"],
                    "is_target_of": row["tag_group_target_puzzle_id"] == pid,
                    "edges": [],
                    "group_type": row["tag_group_type"],
                }
                puz["rounds"].append(existing)
            if row["tag_edge_type"] and row["tag_edge_type"] not in existing["edges"]:
                existing["edges"].append(row["tag_edge_type"])
```
Then update the **other `rounds[].edge_type` readers** in `bot/web/data.py` (grep
`edge_type` to confirm):
- `:290` (`derive_meta_kind`) `group_feeders...append((p["id"], r["edge_type"]))` → emit one
  tuple per edge: `for e in r["edges"]: group_feeders.setdefault(r["id"], []).append((p["id"], e))`
- `:416` `any(rnd["edge_type"] == "feeds" for rnd in puz["rounds"])` → `any("feeds" in rnd["edges"] for rnd in puz["rounds"])`
- `:444` `feeder_edges.setdefault(gid, set()).add(rnd["edge_type"])` → `feeder_edges.setdefault(gid, set()).update(rnd["edges"])`

Also update `tests/test_meta_kind.py` fixtures (manually-built round dicts at lines ~16, 27,
69, 70, 78, 87): `"edge_type": "feeds"` → `"edges": ["feeds"]`.

- [ ] **Step 4: Update `make_round_ref` and any `edge_type` round-ref assertions**

In `tests/conftest.py` (`:126-134`) change `make_round_ref` to emit `edges`:
```python
def make_round_ref(id, name, group_code="RC", is_target_of=False, edges=("feeds",)):
    """Build a round reference dict as embedded in a puzzle row."""
    return {
        "id": id,
        "name": name,
        "group_code": group_code,
        "is_target_of": is_target_of,
        "edges": list(edges),
    }
```
Then `grep -rn "edge_type=" tests/` for callers of `make_round_ref` and switch them to `edges=(...)`.

- [ ] **Step 5: Run to verify it passes**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/t.log 2>&1`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/web/data.py tests/conftest.py tests/
git commit -m "feat(edges): board API returns rounds[].edges (aggregated edge set)"
```

---

### Task 7: Frontend renders the edge set (read-only)

**Files:**
- Modify: `frontend/src/types.ts` (`PuzzleTagGroup`)
- Modify: `frontend/src/components/PuzzleTable.tsx` (`edgeCode`/`edgeColor`/`edgeTitle`/`EdgeBadge` read from `edges`)

- [ ] **Step 1: Update the type**

In `frontend/src/types.ts`, on `PuzzleTagGroup` replace `edge_type?: string | null` with:
```ts
  edges: string[];
```

- [ ] **Step 2: Read the set in `edgeCode`/`edgeColor`/`edgeTitle`**

In `PuzzleTable.tsx`, rework `edgeCode` (`:43-51`) to join codes:
```ts
const EDGE_CODE: Record<string, string> = { feeds: "F", informs: "I", none: "N", target: "T" };

function edgeCodes(r: PuzzleTagGroup): string[] {
  const codes = r.edges.map((e) => EDGE_CODE[e] ?? "?");
  // is_target_of is authoritative for target-ness even if the self-edge self-feeds.
  if (r.is_target_of && !codes.includes("T")) codes.unshift("T");
  return codes;
}

function edgeCode(r: PuzzleTagGroup): string {
  return edgeCodes(r).join("+") || "?";
}
```
Update `edgeColor` to use `"grape"` when more than one code, else the single colour (keep existing `EDGE_COLOR`):
```ts
function edgeColor(r: PuzzleTagGroup): string {
  const codes = edgeCodes(r);
  if (codes.length > 1) return "grape";
  return EDGE_COLOR[codes[0]] ?? "red";
}
```
Update `edgeTitle` to list the edges (`r.edges.join(", ")`, prefixed `target · ` when `is_target_of`). Update `EdgeBadge` to call `edgeColor(r)` (not `edgeColor(code)`).

Also fix the other frontend `edge_type` reader `DragBoard.tsx:87`:
```ts
  const selfFeeds = ownTarget?.edges.includes("feeds") ?? false;
```

- [ ] **Step 3: Keep the existing menu compiling**

The current `EdgeBadge` menu still reads `r.edge_type` in the option `disabled` check
(`disabled={o.edge === (r.edge_type ?? "")}`). The full toggle rework is Task 10, but to keep
this intermediate state compiling, change that line now to read the set:
```tsx
            disabled={r.edges.includes(o.edge)}
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && timeout 180 npx tsc --noEmit`
Expected: exit 0. Fix any other `edge_type` readers it reports (`DragBoard.tsx` uses only
`is_target_of`, so should be clean).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/PuzzleTable.tsx
git commit -m "feat(edges): debug badges render the per-group edge set (e.g. F+I)"
```

---

## PHASE 2 — Evidence-driven informs hint + per-edge menu

### Task 8: `retag_puzzle` add/remove a single edge

**Files:**
- Modify: `bot/web/actions.py:794-899` (`retag_puzzle`)
- Test: `tests/test_edge_web.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_edge_web.py`:
```python
@pytest.mark.asyncio
async def test_retag_add_keeps_existing_edge(web):
    # add informs should call tag_with_edge (which now ADDS, not replaces).
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
    # untag_from called with the edge kwarg
    _, kwargs = web.svc.untag_from.call_args
    assert kwargs.get("edge") == "informs"
```
(`_edge_of` already exists in this file; confirm it reads the 3rd positional arg of the call.)

- [ ] **Step 2: Run to verify they fail**

Run: `timeout 120 pipenv run python -m pytest tests/test_edge_web.py -q > /tmp/t.log 2>&1`
Expected: FAIL — `remove` ignores `edge` today.

- [ ] **Step 3: Thread `edge` through `remove`**

In `retag_puzzle` (`bot/web/actions.py`), in the `elif mode == "remove":` branch (`:872-873`) pass the edge:
```python
            elif mode == "remove":
                await cog.service.untag_from(puzzle, target_group, edge=explicit_edge)
```
(`explicit_edge` is `None` when omitted → whole-membership removal, unchanged.)

- [ ] **Step 4: Run to verify they pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_edge_web.py -q > /tmp/t.log 2>&1`
Expected: PASS (all, including the prior 22).

- [ ] **Step 5: Commit**

```bash
git add bot/web/actions.py tests/test_edge_web.py
git commit -m "feat(edges): retag remove accepts an optional single edge"
```

---

### Task 9: Evidence link auto-adds an informs edge

**Files:**
- Modify: `bot/web/actions.py:1641-1657` (`update_evidence`)
- Modify: `bot/core/puzzle_service.py` — add `link_informs_for_evidence` / `unlink_informs_for_evidence` helpers
- Test: `tests/test_evidence_informs.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_evidence_informs.py` (mirror `test_edge_primitives.py`'s `_service_mocks` style):
```python
import contextlib
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
        mrdb.get_by_attr.return_value = group   # group whose target is puzzle 9
        mpdb.tag_puzzle_with_edge.return_value = None
        mpdb.commit.return_value = None
        mhdb.get_by_attr.return_value = make_hunt()
        await cog.service.link_informs_for_evidence(make_hunt(), source_puzzle_id=8, target_puzzle_id=9)
    assert source.has_edge(5, "informs")
    assert source.has_edge(5, "feeds")   # not evicted
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_informs.py -q > /tmp/t.log 2>&1`
Expected: FAIL — method does not exist.

- [ ] **Step 3: Implement the helpers**

Add to `PuzzleService` (near `refresh_target_for_puzzle`, `bot/core/puzzle_service.py:1917`):
```python
    async def link_informs_for_evidence(self, hunt, source_puzzle_id, target_puzzle_id):
        """Auto-add an 'informs' edge from the source puzzle to the group whose target
        is target_puzzle_id, so routed evidence renders. Never evicts feeds. Best-effort."""
        if not (source_puzzle_id and target_puzzle_id):
            return
        group = TagGroupJsonDb.get_by_attr(target_puzzle_id=target_puzzle_id)
        source = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
        if group is None or source is None:
            return
        if not source.has_edge(group.id, "informs"):
            await self.tag_with_edge(source, group, "informs")

    async def unlink_informs_for_evidence(self, hunt, source_puzzle_id, target_puzzle_id):
        """Remove the auto-added 'informs' edge only if the source has no remaining
        evidence routed to that target (a manual informs is preserved)."""
        if not (source_puzzle_id and target_puzzle_id):
            return
        remaining = EvidenceJsonDb.get_all(
            hunt.id, source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
        if remaining:
            return
        group = TagGroupJsonDb.get_by_attr(target_puzzle_id=target_puzzle_id)
        source = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
        if group is None or source is None:
            return
        if source.has_edge(group.id, "informs"):
            await self.untag_from(source, group, edge="informs")
```
(Confirm `EvidenceJsonDb` is imported in `puzzle_service.py`; it is used at `:634/1847`.)

- [ ] **Step 4: Call them from `update_evidence`**

In `bot/web/actions.py` `update_evidence` (`:1647-1656`), capture the old target before setting, then route the hint:
```python
    old = EvidenceJsonDb.get_by_attr(id=item_id)
    old_target = old.target_puzzle_id if old else None
    item = EvidenceJsonDb.set_target(item_id, target_puzzle_id)
    if item is None:
        return jsonify({"error": "not found"}), 404
    cog = current_app.bot.get_cog("Puzzles")
    hunt_obj = HuntJsonDb.get_by_attr(id=item.hunt_id)
    if cog is not None and hunt_obj is not None:
        if item.target_puzzle_id:
            asyncio.create_task(cog.service.link_informs_for_evidence(
                hunt_obj, item.source_puzzle_id, item.target_puzzle_id))
        if old_target and old_target != item.target_puzzle_id:
            asyncio.create_task(cog.service.unlink_informs_for_evidence(
                hunt_obj, item.source_puzzle_id, old_target))
        asyncio.create_task(cog.service.refresh_evidence(hunt_obj))
        asyncio.create_task(cog.service.refresh_target_for_puzzle(hunt_obj, item.target_puzzle_id))
```
(Verify `EvidenceJsonDb.get_by_attr(id=...)` exists; if not, use the existing single-item read. Match the bang path in `log_clues` (`:1844-1849`) so `!clue` routing also benefits — add a `link_informs_for_evidence` call there if the source isn't already an informs member.)

- [ ] **Step 5: Run to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_informs.py tests/test_edge_web.py -q > /tmp/t.log 2>&1`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/web/actions.py bot/core/puzzle_service.py tests/test_evidence_informs.py
git commit -m "feat(edges): evidence link auto-adds an informs hint (coexists with feeds)"
```

---

### Task 10: Frontend per-edge toggle menu

**Files:**
- Modify: `frontend/src/api.ts` (`useRetagPuzzle` already supports `edge`; ensure `mode:"remove"` can carry `edge`)
- Modify: `frontend/src/components/PuzzleTable.tsx` (`EdgeBadge` menu = per-edge toggles)

- [ ] **Step 1: Allow `edge` on remove in the mutation**

In `frontend/src/api.ts`, the `useRetagPuzzle` body already spreads `edge`; confirm the `mutationFn` includes `edge` for every mode (it does via `...(edge ? { edge } : {})`). No change needed if so; otherwise thread it.

- [ ] **Step 2: Rework the menu to per-edge toggles**

In `PuzzleTable.tsx`, replace the single-select `opts.map(...)` menu in `EdgeBadge` with toggles for `feeds`/`informs` (add if absent → `mode:"add"`; remove if present → `mode:"remove"` with that edge), keeping `none` (pooled) and the target's "No self-feed" option. Drive the checked state from `r.edges`:
```tsx
const FEEDER_TOGGLES: { edge: EdgeType; label: string }[] = [
  { edge: "feeds", label: "Feeds" },
  { edge: "informs", label: "Informs" },
];
// inside EdgeBadge dropdown, for a non-target membership:
{FEEDER_TOGGLES.map((o) => {
  const on = r.edges.includes(o.edge);
  return (
    <Menu.Item
      key={o.edge}
      leftSection={on ? "✓" : ""}
      onClick={() => (on ? onRemove(o.edge) : onPick(o.edge))}
    >
      {o.label}
    </Menu.Item>
  );
})}
```
Change `onRemove` to take an optional edge: `onRemove: (edge?: EdgeType) => void`, and at the call site wire `mode:"remove"` with that edge (omit edge for "Remove from group"). Keep the target options (`TARGET_EDGE_OPTS`) as-is.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && timeout 180 npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts frontend/src/components/PuzzleTable.tsx
git commit -m "feat(edges): per-edge toggle menu for debug edges"
```

---

## Verification

- [ ] Full backend suite green: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/t.log 2>&1`
- [ ] Frontend typecheck: `cd frontend && timeout 180 npx tsc --noEmit`
- [ ] Manual smoke (test site, per `docs/dashboard.md`): in the dashboard with Debug edges on, set a feeder to both Feeds and Informs → badge shows `F+I`; on the target's sheet tab both a Feeder row and an Information row appear.
- [ ] Evidence smoke: link source→target in the Evidence panel → the source's note appears in the target's contribution table with no manual tag, and any existing `feeds` is still present.
- [ ] `bulk_re_edge` (convert a round to capstone) still flips feeders feeds→informs with no duplicate rows (`SELECT tag_group_id, puzzle_id, COUNT(*) FROM tags GROUP BY 1,2 HAVING COUNT(*)>1` returns only intentional multi-edge pairs).

## Rollback / recovery notes

- The migration is non-destructive (key widening only). To roll back schema: `ALTER TABLE tags DROP KEY tag_uq, ADD UNIQUE KEY tag_uq (tag_group_id, puzzle_id);` — but only after deleting any genuine multi-edge rows (`DELETE t2 FROM tags t1 JOIN tags t2 ON t1.tag_group_id=t2.tag_group_id AND t1.puzzle_id=t2.puzzle_id AND t1.id<t2.id;`), else the narrower key fails.
- Phase 1 is independently shippable; Phase 2 depends on it. Revert by phase if needed.
