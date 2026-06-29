# Puzzle Ordering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sort all puzzle lists so targets pin to the top of their group, unsolved precede solved, and within those bands puzzles sort by priority then meta kind then id.

**Architecture:** A single `sortPuzzles(puzzles, groupCode?)` utility is added to `util.ts` and called at three render sites — `BoardGroup`, `PuzzleTable` (inside its existing `filtered` memo), and `DragBoard`. No backend changes, no new state.

**Tech Stack:** TypeScript, React 18, Vite (no frontend test runner — verification is `tsc --noEmit` + visual check in browser)

---

### Task 1: Add `sortPuzzles` to `util.ts`

**Files:**
- Modify: `frontend/src/util.ts`

The sort applies five levels in order:
1. Target pinning (only when `groupCode` is provided — a puzzle is the target when any of its `rounds` has `group_code === groupCode` AND `is_target_of === true`)
2. Unsolved (`solved: false`) before solved (`solved: true`)
3. Priority rank: `"high"` → 0, anything else → 1, `"low"` → 2, `null` → 3
4. Meta kind rank: `"runaround"` → 0, `"metameta"` → 1, `"meta"` → 2, `"capstone"` → 3, `null` → 4
5. `id` ascending

- [ ] **Step 1: Add the import and the function to `util.ts`**

Open `frontend/src/util.ts`. At the top, add the `Puzzle` import (it comes from `./types`). Then append the following at the end of the file:

```typescript
import type { Puzzle } from "./types";

const PRIORITY_RANK: Record<string, number> = { high: 0, low: 2 };
const META_KIND_RANK: Record<string, number> = {
  runaround: 0,
  metameta: 1,
  meta: 2,
  capstone: 3,
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
      const aT = a.rounds.some((r) => r.group_code === groupCode && r.is_target_of);
      const bT = b.rounds.some((r) => r.group_code === groupCode && r.is_target_of);
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

Note: `util.ts` currently has no imports. Add the `import type { Puzzle }` line at the very top of the file.

- [ ] **Step 2: Type-check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors (or only pre-existing errors unrelated to util.ts).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/util.ts
git commit -m "feat: add sortPuzzles utility to util.ts"
```

---

### Task 2: Sort puzzles in `BoardGroup`

**Files:**
- Modify: `frontend/src/components/BoardGroup.tsx`

`BoardGroup` renders `group.puzzles.map(...)` directly. We sort first, passing `group.group_code` so the target pins to the top.

- [ ] **Step 1: Import `sortPuzzles` in `BoardGroup.tsx`**

Find the existing import line:
```typescript
import { notifyError, relativeTime, statusColor } from "../util";
```
Replace it with:
```typescript
import { notifyError, relativeTime, sortPuzzles, statusColor } from "../util";
```

- [ ] **Step 2: Sort puzzles before rendering**

In `BoardGroup.tsx`, inside `<Table.Tbody>`, find:
```typescript
{group.puzzles.map((p) => (
```
Replace with:
```typescript
{sortPuzzles(group.puzzles, group.group_code).map((p) => (
```

- [ ] **Step 3: Type-check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/BoardGroup.tsx
git commit -m "feat: sort puzzles in BoardGroup (target first, unsolved first, priority, kind, id)"
```

---

### Task 3: Sort puzzles in `PuzzleTable`

**Files:**
- Modify: `frontend/src/components/PuzzleTable.tsx`

`PuzzleTable` has a `filtered` useMemo that filters puzzles. We append `sortPuzzles` to its return value — no `groupCode` because this is a flat global list.

- [ ] **Step 1: Import `sortPuzzles` in `PuzzleTable.tsx`**

Find:
```typescript
import { answerLength, notifyError, priorityColor, relativeTime, statusColor } from "../util";
```
Replace with:
```typescript
import { answerLength, notifyError, priorityColor, relativeTime, sortPuzzles, statusColor } from "../util";
```

- [ ] **Step 2: Wrap the filtered result with sortPuzzles**

Find the `filtered` useMemo (around line 187). Its current return is:
```typescript
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return puzzles.filter((p) => {
      if (hideSolved && p.solved) return false;
      if (statuses.length && !statuses.includes(p.status || "")) return false;
      if (priorities.length && !priorities.includes(p.priority || "")) return false;
      if (types.length && !types.includes(p.puzzle_type || "")) return false;
      if (q) {
        const hay = `${p.name} ${p.solution ?? ""} ${p.rounds.map((r) => r.name).join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [puzzles, search, statuses, priorities, types, hideSolved]);
```

Replace with:
```typescript
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const result = puzzles.filter((p) => {
      if (hideSolved && p.solved) return false;
      if (statuses.length && !statuses.includes(p.status || "")) return false;
      if (priorities.length && !priorities.includes(p.priority || "")) return false;
      if (types.length && !types.includes(p.puzzle_type || "")) return false;
      if (q) {
        const hay = `${p.name} ${p.solution ?? ""} ${p.rounds.map((r) => r.name).join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    return sortPuzzles(result);
  }, [puzzles, search, statuses, priorities, types, hideSolved]);
```

- [ ] **Step 3: Type-check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/PuzzleTable.tsx
git commit -m "feat: sort puzzles in PuzzleTable (unsolved first, priority, kind, id)"
```

---

### Task 4: Sort puzzles in `DragBoard`

**Files:**
- Modify: `frontend/src/components/DragBoard.tsx`

In `DragBoard`, each `Column` renders `group.puzzles.map(...)`. We sort per-column passing `group.group_code`.

- [ ] **Step 1: Import `sortPuzzles` in `DragBoard.tsx`**

Find:
```typescript
import { notifyError, showSheetWarning, statusColor } from "../util";
```
Replace with:
```typescript
import { notifyError, showSheetWarning, sortPuzzles, statusColor } from "../util";
```

- [ ] **Step 2: Sort per-column puzzles**

In `DragBoard.tsx`, find the column rendering section (around line 571):
```typescript
            {detail.board.map((group) => (
              <Column key={group.tag_group_id} group={group}>
                {group.puzzles.map((p) => (
```
Replace just the inner map:
```typescript
            {detail.board.map((group) => (
              <Column key={group.tag_group_id} group={group}>
                {sortPuzzles(group.puzzles, group.group_code).map((p) => (
```

- [ ] **Step 3: Type-check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/DragBoard.tsx
git commit -m "feat: sort puzzles in DragBoard columns (target first, unsolved first, priority, kind, id)"
```

---

### Task 5: Visual verification

No automated frontend tests exist. Verify manually against the running dev server.

- [ ] **Step 1: Start the dev server**

```bash
cd frontend && npm run dev
```

Open the browser at the local URL (default `http://localhost:5173`). Navigate to a hunt with a mix of solved/unsolved puzzles across multiple groups.

- [ ] **Step 2: Verify By-group view**

Check each group accordion:
- The meta/capstone/runaround that is the group's target appears first in its group
- Unsolved puzzles appear before solved ones
- Within unsolved: high-priority puzzles appear before normal, which appear before low/null
- Within same priority: runaround > metameta > meta > capstone > feeder
- Solved section follows the same sub-ordering

- [ ] **Step 3: Verify All-puzzles view**

Switch to "All puzzles" tab:
- No target-pinning (flat list — no group context)
- Unsolved before solved
- Within bands: priority then meta kind then id

- [ ] **Step 4: Verify Arrange (DragBoard) view**

Switch to "Arrange" tab (organiser only):
- Each column: target card pinned to top
- Below target: unsolved then solved, ordered by priority/kind/id
- Drag a card to another column — after release and re-render, order re-applies correctly

- [ ] **Step 5: Final build check**

```bash
cd frontend && npm run build 2>&1 | tail -20
```

Expected: build succeeds with no TypeScript errors.
