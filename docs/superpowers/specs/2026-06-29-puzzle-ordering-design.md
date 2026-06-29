# Puzzle Ordering Design

Date: 2026-06-29

## Goal

Apply a consistent, deterministic sort order to all puzzle lists on the dashboard so the most
actionable puzzles surface first.

## In scope

- `BoardGroup` component (By-group accordion view) — sort each group's puzzle list
- `PuzzleTable` component (All-puzzles flat table) — sort the filtered result
- `DragBoard` component (Arrange view, per-column card lists) — sort each column

## Out of scope

- Backend changes — all sorting is frontend/display only
- Persisted drag order — DragBoard drag-and-drop still retags group membership; it does not
  persist a display position
- Sorting BoardGroup-level groups (group order is already handled in HuntBoard.tsx)

## Sort key (applied to every puzzle list)

Five-level sort, evaluated in order:

| Level | Field | Order |
|-------|-------|-------|
| 1 | **Target pinning** (TagGroup views only) | Target puzzle first; non-targets after |
| 2 | **Solved** | Unsolved (false) before solved (true) |
| 3 | **Priority** | high=0 · normal/other=1 · low=2 · null=3 |
| 4 | **Meta kind** | runaround=0 · metameta=1 · meta=2 · capstone=3 · feeder/null=4 |
| 5 | **ID** | Ascending (creation order) |

**Target identification** — a puzzle is the target of a group when:
`puzzle.rounds.some(r => r.group_code === group.group_code && r.is_target_of)`

This is only evaluated when a `groupCode` is passed to the sort function; flat/global lists skip
level 1.

## Implementation

### New utility function — `util.ts`

```typescript
const PRIORITY_RANK: Record<string, number> = { high: 0, low: 2 };
const META_KIND_RANK: Record<string, number> = {
  runaround: 0, metameta: 1, meta: 2, capstone: 3,
};

function priorityRank(p: Puzzle): number {
  if (!p.priority) return 3;
  return PRIORITY_RANK[p.priority.toLowerCase()] ?? 1;
}

function metaKindRank(p: Puzzle): number {
  if (!p.meta_kind) return 4;
  return META_KIND_RANK[p.meta_kind] ?? 4;
}

export function sortPuzzles(puzzles: Puzzle[], groupCode?: string | null): Puzzle[] {
  return [...puzzles].sort((a, b) => {
    if (groupCode != null) {
      const aT = a.rounds.some(r => r.group_code === groupCode && r.is_target_of);
      const bT = b.rounds.some(r => r.group_code === groupCode && r.is_target_of);
      if (aT !== bT) return aT ? -1 : 1;
    }
    if (a.solved !== b.solved) return a.solved ? 1 : -1;
    const pd = priorityRank(a) - priorityRank(b);
    if (pd !== 0) return pd;
    const md = metaKindRank(a) - metaKindRank(b);
    if (md !== 0) return md;
    return a.id - b.id;
  });
}
```

### Call sites

**`BoardGroup.tsx`** — sort before rendering:
```typescript
const sorted = sortPuzzles(group.puzzles, group.group_code);
// replace group.puzzles.map(...) with sorted.map(...)
```

**`PuzzleTable.tsx`** — append sort to the existing `filtered` useMemo:
```typescript
const filtered = useMemo(() => {
  // ... existing filter logic ...
  return sortPuzzles(result);  // no groupCode — flat list
}, [...]);
```

**`DragBoard.tsx`** — sort per column at render time:
```typescript
{sortPuzzles(group.puzzles, group.group_code).map((p) => (
  <PuzzleCard key={...} puzzle={p} ... />
))}
```

## Constraints

- Do not mutate the source array — always spread before sorting
- No backend changes
- `PuzzleTable` already wraps filtering in `useMemo`; sorting extends that memo — no extra render cost
- DragBoard sort is inline at render; no state needed since it derives from `detail` which is
  already reactive

## Acceptance criteria

1. In "By group" view, the meta/capstone/runaround target of each group appears first in its group
2. Unsolved puzzles appear before solved ones in every view
3. Within unsolved (and within solved), puzzles are ordered: high-priority → normal → low → null,
   then by meta kind (runaround → metameta → meta → capstone → feeder), then by id
4. The flat "All puzzles" table follows the same order (minus target-pinning)
5. DragBoard columns follow the same order including target-pinning
6. No regressions in filtering, drag-and-drop, or mutations
