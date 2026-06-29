# BoardGroup compact row layout

**Date:** 2026-06-29
**Status:** Approved

## Goal

Make the puzzle rows inside each BoardGroup accordion panel responsive: keep the full 6-column layout on wide viewports (where the real estate is useful), and collapse to a compact 3-column layout on narrow viewports where the current layout breaks.

The kind-coloured left strip is added to **both** layouts.

## In scope

- `BoardGroup.tsx` — row structure inside `Accordion.Panel`
- The kind-coloured left strip (new, both layouts)
- Responsive switching between wide and compact row layouts

## Out of scope

- `PuzzleTable.tsx` (All Puzzles tab) — unchanged
- Accordion header (group name, feeder count, rebuild/delete/answers buttons)
- `SolveModal`, aggregated answers panel, `MiniPuzzlePanel` expansion row
- `PuzzleLinks`, `PuzzleRowActions`, `MetaBadge` — reused as-is

## Wide layout (viewport ≥ sm / 768px) — unchanged columns, strip added

| Strip (4px) | Links (110px) | Puzzle + MetaBadge | Solution | Status | Activity | Actions (76px) |

All existing columns are preserved. The only addition is the 4px kind strip as the first cell.

## Compact layout (viewport < sm / 768px) — 3 columns

| Strip (4px) | Left ~92px | Middle flex | Right ~80px |
|---|---|---|---|
| Kind strip | 3 link icons | Title + solution | Solve/unsolve · Notes · Minimeta badge |

### Middle cell (compact only)

Two lines:

1. **Title line:** `MetaBadge` text badge + puzzle name (bold for meta kinds).
2. **Solution line:** monospace solution text, green when solved; dimmed when unsolved.

### Dropped in compact layout

- **Status** — conveyed by green row background + unsolve button presence
- **Activity** — dropped to save width

### Right cell (compact)

`PuzzleRowActions` unchanged — solve/unsolve button, notes button, minimeta count badge.

## Kind strip (both layouts)

A 4px coloured left border on each `<Table.Tr>`, matching the MetaBadge colour for that puzzle's kind:

| Kind | Colour token | Hex approx |
|---|---|---|
| meta | grape | `#9c27b0` |
| metameta | violet | `#7c3aed` |
| capstone | orange | `#e65100` |
| runaround | red | `#c62828` |
| regular feeder | — | no strip |

Implementation: add a zero-padding `<Table.Td>` with fixed `width={4}` and `style={{ backgroundColor: kindColor, padding: 0 }}` as the first cell in every row. `kindColor` is derived from `puzzle.meta_kind` via a lookup matching `MetaBadge`'s colour map. For regular feeders the cell has no background (transparent).

## Responsive switching

Use Mantine's `useMediaQuery` hook with the `sm` breakpoint (`(max-width: 768px)`). When the query matches, render the compact row; otherwise render the wide row. Both share the same `sortPuzzles`, `SolveModal`, and `MiniPuzzlePanel` expansion logic.

The `MiniPuzzlePanel` expansion row uses `colSpan={99}` in both layouts and is unaffected.

## Solved state

The existing `bg={p.solved ? "var(--mantine-color-green-light)" : undefined}` on `<Table.Tr>` is unchanged in both layouts.

## Acceptance criteria

- Wide viewport (≥768px): all 6 original columns present, plus 4px kind strip as first cell
- Narrow viewport (<768px): 3-column compact layout (strip · links · title+solution · actions)
- Meta/capstone/metameta/runaround puzzles show a coloured strip matching their MetaBadge colour in both layouts
- Regular feeder puzzles have no strip colour in either layout
- Compact layout: solution appears under the title in monospace; green when solved
- Solved rows retain the green background in both layouts
- `PuzzleRowActions`, `PuzzleLinks`, `MetaBadge` reused without modification
- `MiniPuzzlePanel` expansion row continues to work in both layouts
- No change to the accordion header or any other component
