# BoardGroup improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix group sort order (runaround → metameta → meta), default-collapse solved groups, and add responsive compact rows with a kind-coloured left strip to the By Group board view.

**Architecture:** Three independent phases — Phase 1 is a pure backend change to `build_board`'s `_rank` function; Phase 2 is a one-line frontend change to `defaultValue` in `HuntBoard`; Phase 3 adds `useMediaQuery`-driven responsive rendering and a kind strip to `BoardGroup`. All three can be committed independently.

**Tech Stack:** Python (backend, `bot/web/data.py`), React + Mantine v7 + TypeScript (frontend, `frontend/src/`)

---

## File map

| File | What changes |
|---|---|
| `bot/web/data.py` | Expand `_rank` in `build_board` to sub-rank runaround/metameta/meta |
| `tests/test_build_board.py` | New test class for sub-kind sort order |
| `frontend/src/pages/HuntBoard.tsx` | `defaultValue` — only expand unsolved groups on load |
| `frontend/src/components/BoardGroup.tsx` | `useMediaQuery`, kind strip cell, compact row branch |

---

## Task 1: Failing tests for group sub-kind sort order

**Files:**
- Modify: `tests/test_build_board.py`

- [ ] **Step 1: Add the failing test class**

Append to `tests/test_build_board.py`:

```python
# ---------------------------------------------------------------------------
# build_board — sub-kind sort order within meta tier
# ---------------------------------------------------------------------------

class TestBuildBoardSubKindOrder:
    """build_board should order groups: runaround < metameta < meta within the
    unsolved-meta and solved-meta tiers (before and after capstones/plain)."""

    def _grp(self, gid, name, edge, feeder_is_meta=False, solved=False, gtype="Metapuzzle"):
        """Return (puzzle_list, all_groups_entry) for a group with one target + one feeder."""
        target = make_puzzle_row(
            id=100 + gid, name=f"{name}-T", is_meta=True, solved=solved,
            rounds=[make_round_ref(id=gid, name=name, group_code=name, is_target_of=True)],
        )
        feeder = make_puzzle_row(
            id=200 + gid, name=f"{name}-F", is_meta=feeder_is_meta,
            rounds=[make_round_ref(id=gid, name=name, group_code=name, edges=(edge,))],
        )
        ag = {"id": gid, "name": name, "group_code": name, "type": gtype,
              "target_puzzle_id": 100 + gid}
        return [target, feeder], ag

    def test_runaround_before_metameta_before_meta_unsolved(self):
        # Runaround is stored-type; metameta has all feeds-feeders be metas;
        # regular meta has non-meta feeds-feeder.
        puzzles, ags = [], []
        for gid, name, edge, fim, gtype in [
            (3, "Meta",      "feeds", False, "Metapuzzle"),
            (2, "Metameta",  "feeds", True,  "Metapuzzle"),
            (1, "Runaround", "feeds", False, "Runaround"),
        ]:
            p, g = self._grp(gid, name, edge, fim, solved=False, gtype=gtype)
            puzzles.extend(p); ags.append(g)
        board = build_board(puzzles, None, ags)
        names = [g["name"] for g in board if g["tag_group_id"] != 0]
        assert names == ["Runaround", "Metameta", "Meta"]

    def test_runaround_before_metameta_before_meta_solved(self):
        # Same sub-ordering applies to the solved tier.
        puzzles, ags = [], []
        for gid, name, edge, fim, gtype in [
            (3, "Meta",      "feeds", False, "Metapuzzle"),
            (2, "Metameta",  "feeds", True,  "Metapuzzle"),
            (1, "Runaround", "feeds", False, "Runaround"),
        ]:
            p, g = self._grp(gid, name, edge, fim, solved=True, gtype=gtype)
            puzzles.extend(p); ags.append(g)
        board = build_board(puzzles, None, ags)
        names = [g["name"] for g in board if g["tag_group_id"] != 0]
        assert names == ["Runaround", "Metameta", "Meta"]

    def test_unsolved_sub_kinds_before_solved_sub_kinds(self):
        # Full ordering: unsolved (runaround, metameta, meta) then solved (runaround, metameta, meta).
        puzzles, ags = [], []
        for gid, name, edge, fim, gtype, solved in [
            (6, "SMeta",      "feeds", False, "Metapuzzle", True),
            (5, "SMetameta",  "feeds", True,  "Metapuzzle", True),
            (4, "SRunaround", "feeds", False, "Runaround",  True),
            (1, "UMeta",      "feeds", False, "Metapuzzle", False),
            (2, "UMetameta",  "feeds", True,  "Metapuzzle", False),
            (3, "URunaround", "feeds", False, "Runaround",  False),
        ]:
            p, g = self._grp(gid, name, edge, fim, solved=solved, gtype=gtype)
            puzzles.extend(p); ags.append(g)
        board = build_board(puzzles, None, ags)
        names = [g["name"] for g in board if g["tag_group_id"] != 0]
        assert names == ["URunaround", "UMetameta", "UMeta",
                         "SRunaround", "SMetameta", "SMeta"]
```

- [ ] **Step 2: Run the new tests — expect them to FAIL**

```bash
timeout 60 pipenv run python -m pytest tests/test_build_board.py::TestBuildBoardSubKindOrder -v > /tmp/test.log 2>&1; tail -20 /tmp/test.log
```

Expected: 3 failures (FAILED — `_rank` doesn't sub-rank yet).

---

## Task 2: Implement group sub-kind sort order

**Files:**
- Modify: `bot/web/data.py` (lines ~460–498)

- [ ] **Step 1: Replace the ranking block**

In `bot/web/data.py`, find the block that begins at the comment `# Per-group signals for column ordering` (around line 460) and replace from there through the end of `build_board` (the `return` line) with:

```python
    # Per-group signals for ranking: feeder edge types, which puzzle ids feed
    # each group (for metameta detection), and whether each target is solved.
    feeder_edges = {}    # gid -> set of feeder edge types
    feeder_pids = {}     # gid -> list[int] of puzzle ids with a "feeds" edge
    target_solved = {}   # gid -> bool
    is_meta_by_id = {p["id"]: p["is_meta"] for p in puzzles}

    for puz in puzzles:
        for rnd in puz["rounds"]:
            gid = rnd["id"]
            if rnd["is_target_of"]:
                target_solved[gid] = puz["solved"]
            else:
                feeder_edges.setdefault(gid, set()).update(rnd["edges"])
                if "feeds" in rnd["edges"]:
                    feeder_pids.setdefault(gid, []).append(puz["id"])

    def _is_capstone(gid):
        edges = feeder_edges.get(gid, set())
        if "feeds" in edges:
            return False
        if "informs" in edges:
            return True
        return group_info.get(gid, {}).get("type") == "Capstone"

    def _meta_sub_rank(gid):
        # 0 = runaround (stored type), 1 = metameta (all feeds-feeders are metas),
        # 2 = regular meta.
        if group_info.get(gid, {}).get("type") == "Runaround":
            return 0
        pids = feeder_pids.get(gid, [])
        if pids and all(is_meta_by_id.get(pid) for pid in pids):
            return 1
        return 2

    def _rank(gid):
        # Group order:
        #   0  Unassigned Feeders
        #   1  unsolved runaround
        #   2  unsolved metameta
        #   3  unsolved meta
        #   4  unsolved capstone
        #   5  plain (no target)
        #   6  solved runaround
        #   7  solved metameta
        #   8  solved meta
        #   9  solved capstone
        if gid == 0:
            return 0
        has_target = bool(group_info.get(gid, {}).get("target_puzzle_id")) or gid in target_solved
        if not has_target:
            return 5
        if _is_capstone(gid):
            return 9 if target_solved.get(gid) else 4
        sub = _meta_sub_rank(gid)
        return (6 + sub) if target_solved.get(gid) else (1 + sub)

    # Within a rank, oldest first — group id is auto-increment (creation order).
    return [groups[rid] for rid in sorted(order, key=lambda gid: (_rank(gid), gid))]
```

- [ ] **Step 2: Run new tests — expect PASS**

```bash
timeout 60 pipenv run python -m pytest tests/test_build_board.py::TestBuildBoardSubKindOrder -v > /tmp/test.log 2>&1; tail -20 /tmp/test.log
```

Expected: 3 passed.

- [ ] **Step 3: Run full test_build_board suite — all should pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_build_board.py -v > /tmp/test.log 2>&1; tail -30 /tmp/test.log
```

Expected: all existing tests still pass (the new ranks are compatible: capstone was 2/5, now 4/9; plain was 3, now 5; meta was 1/4, now 1–3/6–8).

- [ ] **Step 4: Run full suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; tail -20 /tmp/test.log
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/web/data.py tests/test_build_board.py
git commit -m "feat: sub-rank groups by kind (runaround > metameta > meta)"
```

---

## Task 3: Default-collapse solved groups

**Files:**
- Modify: `frontend/src/pages/HuntBoard.tsx`

- [ ] **Step 1: Find and update the defaultValue prop**

In `frontend/src/pages/HuntBoard.tsx`, find this line (around line 135):

```tsx
<Accordion variant="separated" multiple defaultValue={boardGroups.map((g) => String(g.tag_group_id))}>
```

Replace with:

```tsx
<Accordion variant="separated" multiple defaultValue={boardGroups.filter((g) => !g.target_solved).map((g) => String(g.tag_group_id))}>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/HuntBoard.tsx
git commit -m "feat: default-collapse solved groups in By Group accordion"
```

---

## Task 4: Add kind strip and responsive rows to BoardGroup

**Files:**
- Modify: `frontend/src/components/BoardGroup.tsx`

- [ ] **Step 1: Add useMediaQuery import**

Find the existing `@mantine/hooks` import in `BoardGroup.tsx`:

```tsx
import { useClipboard, useDisclosure } from "@mantine/hooks";
```

Replace with:

```tsx
import { useClipboard, useDisclosure, useMediaQuery } from "@mantine/hooks";
```

- [ ] **Step 2: Add the KIND_STRIP_COLOR map and helper**

After the imports block, before the `interface Props` declaration, add:

```tsx
const KIND_STRIP_COLOR: Record<string, string> = {
  runaround: "var(--mantine-color-red-6)",
  metameta: "var(--mantine-color-violet-6)",
  capstone: "var(--mantine-color-orange-6)",
  meta: "var(--mantine-color-grape-6)",
};

function kindStrip(p: { meta_kind?: string | null }): string | undefined {
  return p.meta_kind ? KIND_STRIP_COLOR[p.meta_kind] : undefined;
}
```

- [ ] **Step 3: Add useMediaQuery call inside the component**

Inside `BoardGroup`, after the existing hook calls (after `useUnsolvePuzzle`, `useUpdatePuzzle`, etc.), add:

```tsx
const isNarrow = useMediaQuery("(max-width: 768px)") ?? false;
```

- [ ] **Step 4: Replace the table body rows**

In `BoardGroup.tsx`, find the `<Table.Tbody>` (inside `<Accordion.Panel>`) and replace the entire `{sortPuzzles(group.puzzles, group.group_code).map((p) => (` block with:

```tsx
{sortPuzzles(group.puzzles, group.group_code).map((p) => (
  <Fragment key={`${group.tag_group_id}-${p.id}`}>
    <Table.Tr bg={p.solved ? "var(--mantine-color-green-light)" : undefined}>
      {/* Kind strip — both layouts */}
      <Table.Td w={4} p={0} style={{ backgroundColor: kindStrip(p) }} />
      {isNarrow ? (
        <>
          {/* Compact layout: links | title+solution | actions */}
          <Table.Td w={92}>
            <PuzzleLinks
              puzzle={p}
              sheetBase={detail.hunt.sheet_base_url}
              archiveSheetBase={detail.hunt.archive_sheet_base_url}
            />
          </Table.Td>
          <Table.Td>
            <Group gap={6} wrap="nowrap">
              <MetaBadge puzzle={p} />
              <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
            </Group>
            <Text ff="monospace" size="xs" c={p.solved ? "green" : "dimmed"} mt={2}>
              {p.solution || "—"}
            </Text>
          </Table.Td>
          <PuzzleRowActions
            puzzle={p}
            editable={editable}
            onSolve={setSolvePuzzle}
            onUnsolve={handleUnsolve}
            onOpenNotes={onOpenNotes}
            onToggleMinis={toggleMinis}
            tdWidth={80}
          />
        </>
      ) : (
        <>
          {/* Wide layout: all original columns */}
          <Table.Td w={110}>
            <PuzzleLinks
              puzzle={p}
              sheetBase={detail.hunt.sheet_base_url}
              archiveSheetBase={detail.hunt.archive_sheet_base_url}
            />
          </Table.Td>
          <Table.Td>
            <Group gap={6}>
              <MetaBadge puzzle={p} />
              <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
            </Group>
          </Table.Td>
          <Table.Td>
            <Text ff="monospace" size="sm">
              {p.solution || ""}
            </Text>
          </Table.Td>
          <Table.Td w={130}>
            {editable ? (
              <StatusCell
                value={p.status}
                onChange={(status) => updateMut.mutate({ puzzleId: p.id, patch: { status } })}
              />
            ) : (
              p.status && (
                <Badge variant="light" color={statusColor(p.status)}>
                  {p.status}
                </Badge>
              )
            )}
          </Table.Td>
          <Table.Td w={90}>
            <Text size="xs" c="dimmed">
              {activityFor(p)}
            </Text>
          </Table.Td>
          <PuzzleRowActions
            puzzle={p}
            editable={editable}
            onSolve={setSolvePuzzle}
            onUnsolve={handleUnsolve}
            onOpenNotes={onOpenNotes}
            onToggleMinis={toggleMinis}
            tdWidth={76}
          />
        </>
      )}
    </Table.Tr>
    {p.puzzle_type === "Minimeta" && expandedMinis.has(p.id) && (
      <Table.Tr>
        <Table.Td colSpan={99} p={0}>
          <MiniPuzzlePanel parentId={p.id} minis={p.mini_puzzles} uid={detail.hunt.uid} />
        </Table.Td>
      </Table.Tr>
    )}
  </Fragment>
))}
```

- [ ] **Step 5: Build and verify no TypeScript errors**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/BoardGroup.tsx
git commit -m "feat: responsive BoardGroup rows with kind-colour left strip"
```

---

## Verification

- [ ] Run the full Python test suite one final time:

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; tail -10 /tmp/test.log
```

Expected: all pass.

- [ ] Build the frontend:

```bash
cd frontend && npm run build 2>&1 | tail -20
```

Expected: build succeeds with no errors.

- [ ] Manual check in browser (narrow viewport ~600px wide):
  - By Group tab: rows show 3 columns (links | title+solution | actions)
  - Kind strip visible for meta/capstone/runaround/metameta puzzles
  - Regular feeder rows have no strip

- [ ] Manual check in browser (wide viewport ~1200px):
  - By Group tab: 6 original columns + kind strip as leftmost cell
  - Solved groups start collapsed; unsolved groups start expanded
  - Runaround group (if present) appears before metameta, which appears before meta
