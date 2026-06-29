# BoardGroup improvements: group sort order, default collapse, compact rows

**Date:** 2026-06-29
**Status:** Approved

## Goal

Three related improvements to the By Group board view:

1. **Group sort order** — runaround → metameta → meta within the "meta" tier (backend fix)
2. **Default collapse solved groups** — accordion panels whose target puzzle is solved start collapsed
3. **Compact responsive rows** — wide viewport keeps existing 6-column layout (plus kind strip); narrow viewport collapses to 3-column layout

## In scope

- `bot/web/data.py` — `_rank` inside `build_board` (group sort order)
- `frontend/src/pages/HuntBoard.tsx` — `defaultValue` prop (default collapse)
- `frontend/src/components/BoardGroup.tsx` — row layout, kind strip, responsive switching

## Out of scope

- `PuzzleTable.tsx` (All Puzzles tab) — unchanged
- Accordion header (group name, feeder count, rebuild/delete/answers buttons)
- `SolveModal`, aggregated answers panel, `MiniPuzzlePanel` expansion row
- `PuzzleLinks`, `PuzzleRowActions`, `MetaBadge` — reused as-is
- Puzzle-level sort order within groups — already landed, unchanged

---

## Phase 1 — Group sort order (backend)

### Current behaviour

`_rank` in `build_board` distinguishes only meta vs capstone:

| Rank | Groups |
|---|---|
| 0 | Unassigned Feeders |
| 1 | Unsolved meta (all sub-kinds) |
| 2 | Unsolved capstone |
| 3 | Plain group (no target) |
| 4 | Solved meta (all sub-kinds) |
| 5 | Solved capstone |

### Required behaviour

Sub-rank within meta by kind — runaround first, then metameta, then regular meta:

| Rank | Groups |
|---|---|
| 0 | Unassigned Feeders |
| 1 | Unsolved runaround |
| 2 | Unsolved metameta |
| 3 | Unsolved meta |
| 4 | Unsolved capstone |
| 5 | Plain group (no target) |
| 6 | Solved runaround |
| 7 | Solved metameta |
| 8 | Solved meta |
| 9 | Solved capstone |

Within each rank: existing creation-order sort (gid ascending) unchanged.

### Implementation

Extend `_rank` with a `_meta_sub_rank(gid)` helper that mirrors `derive_meta_kind`'s logic at the group level, using data already in scope inside `build_board`:

- Build `feeder_pids_by_group: dict[int, list[int]]` — puzzle IDs that feed (feeds edge) each group
- Build `is_meta_by_id: dict[int, bool]` — from the flat puzzle list
- `_meta_sub_rank(gid)`:
  - `group_info.get(gid, {}).get("type") == "Runaround"` → 0 (runaround)
  - all feeder pids are metas → 1 (metameta)
  - any feeder pid → 2 (regular meta)
  - else → 2 (fallback; no feeds feeders yet)

Update `_rank` to use sub-rank for the unsolved/solved meta cases.

---

## Phase 2 — Default collapse solved groups (frontend)

### Current behaviour

`HuntBoard.tsx` line ~135:
```tsx
defaultValue={boardGroups.map((g) => String(g.tag_group_id))}
```
All groups start expanded.

### Required behaviour

Groups whose `target_solved` is `true` start collapsed; unsolved groups start expanded (unchanged).

### Implementation

```tsx
defaultValue={boardGroups.filter((g) => !g.target_solved).map((g) => String(g.tag_group_id))}
```

The `Accordion` is already `multiple`, so this is the only change needed. User can still manually expand/collapse any group — this only controls the initial state.

---

## Phase 3 — Responsive rows and kind strip (frontend)

### Wide layout (viewport ≥ 768px) — kind strip added, columns unchanged

| Strip (4px) | Links (110px) | Puzzle + MetaBadge | Solution | Status | Activity | Actions (76px) |

Adds the 4px kind strip cell as the new first cell. All existing columns preserved.

### Compact layout (viewport < 768px) — 3 columns

| Strip (4px) | Links ~92px | Title + solution | Actions ~80px |

Dropped columns in compact: **Status** (conveyed by green row + unsolve button) and **Activity**.

### Middle cell (compact)

Two lines:
1. `MetaBadge` text badge + puzzle name (bold for meta kinds)
2. Monospace solution text — green when solved, dimmed placeholder when unsolved

### Kind strip (both layouts)

A 4px coloured first `<Table.Td>` (`width={4}`, `padding: 0`) on every row. Colour derived from `puzzle.meta_kind`:

| Kind | Colour |
|---|---|
| runaround | red `#c62828` |
| metameta | violet `#7c3aed` |
| meta | grape `#9c27b0` |
| capstone | orange `#e65100` |
| regular feeder | none (transparent) |

### Responsive switching

Mantine `useMediaQuery("(max-width: 768px)")` inside `BoardGroup`. When narrow, render compact rows; otherwise render wide rows. Both paths share `sortPuzzles`, `SolveModal`, `MiniPuzzlePanel` expansion, and the kind strip.

`MiniPuzzlePanel` expansion row uses `colSpan={99}` — unaffected by either layout.

### Solved state

`bg={p.solved ? "var(--mantine-color-green-light)" : undefined}` on `<Table.Tr>` — unchanged in both layouts.

---

## Acceptance criteria

### Phase 1
- Unsolved runarounds appear before unsolved metametas, which appear before unsolved metas in the By Group accordion
- Solved runarounds, metametas, and metas follow the same sub-ordering at the bottom
- Within each rank, creation order (gid ascending) is preserved
- No change to capstone, plain group, or unassigned ordering

### Phase 2
- On initial page load, accordion panels with `target_solved === true` are collapsed
- Accordion panels with `target_solved === false` (or no target) are expanded
- User can manually expand/collapse any panel after load

### Phase 3
- Wide viewport (≥768px): all 6 original columns present, plus 4px kind strip as first cell
- Narrow viewport (<768px): 3-column compact layout (strip · links · title+solution · actions)
- Kind strip colour matches MetaBadge colour for meta/metameta/capstone/runaround puzzles in both layouts
- Regular feeder puzzles have no strip colour in either layout
- Compact layout: solution appears under the title in monospace; green when solved
- Solved rows retain the green row background in both layouts
- `PuzzleRowActions`, `PuzzleLinks`, `MetaBadge` reused without modification
- `MiniPuzzlePanel` expansion row works in both layouts
- No change to accordion header or any other component
