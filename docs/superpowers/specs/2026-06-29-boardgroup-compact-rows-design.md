# BoardGroup compact row layout

**Date:** 2026-06-29
**Status:** Approved

## Goal

Replace the 6-column puzzle table inside each BoardGroup accordion panel with a compact 3-column layout that works at any viewport width and drops the columns that added width without enough value.

## In scope

- `BoardGroup.tsx` — row structure inside `Accordion.Panel`
- The kind-coloured left strip (new)

## Out of scope

- `PuzzleTable.tsx` (All Puzzles tab) — unchanged
- Accordion header (group name, feeder count, rebuild/delete/answers buttons)
- `SolveModal`, aggregated answers panel, `MiniPuzzlePanel` expansion row
- `PuzzleLinks`, `PuzzleRowActions`, `MetaBadge` — reused as-is

## Current layout (6 columns)

| Links (110px) | Puzzle + MetaBadge | Solution | Status | Activity | Actions (76px) |

## New layout (3 columns)

| Left ~92px | Middle flex | Right ~80px |
|---|---|---|
| 3 link icons | Kind strip + title + solution | Solve/unsolve · Notes · Minimeta badge |

### Left cell

`PuzzleLinks` component unchanged — sheet, Discord, puzzle page icon buttons.

### Middle cell

Two lines:

1. **Title line:** `MetaBadge` (text badge, existing colours) + puzzle name. Bold for meta/capstone kinds.
2. **Solution line:** monospace solution text in green when solved, dimmed placeholder text when unsolved.

### Right cell

`PuzzleRowActions` component unchanged — solve ✅ or unsolve ✗ button, notes 📝 button, minimeta count badge (Minimeta puzzles only).

### Kind strip

A 4px coloured left border on each `<Table.Tr>`, matching the MetaBadge colour for that puzzle's kind:

| Kind | Colour token | Hex approx |
|---|---|---|
| meta | grape | `#9c27b0` |
| metameta | violet | `#7c3aed` |
| capstone | orange | `#e65100` |
| runaround | red | `#c62828` |
| regular feeder | — | no strip |

The strip is always rendered (all viewport sizes). It is not a responsive variant.

Implementation: add a zero-padding `<Table.Td>` with fixed `width={4}` and `style={{ backgroundColor: kindColor, padding: 0 }}` as the first cell in each row. `kindColor` is derived from `puzzle.meta_kind` via a lookup matching `MetaBadge`'s colour map. For non-meta puzzles the cell has no background.

### Solved state

The existing `bg={p.solved ? "var(--mantine-color-green-light)" : undefined}` on `<Table.Tr>` is unchanged. The Status column is dropped — solved/unsolved is conveyed by the green row background and the presence of the unsolve button.

### Dropped columns

- **Status** — redundant with green row background + unsolve button
- **Activity** — not worth the column width in this view

## Acceptance criteria

- Each puzzle row in a BoardGroup panel has exactly 4 cells: strip · links · title+solution · actions
- Meta/capstone/metameta/runaround puzzles show a 4px coloured left strip matching their MetaBadge colour
- Regular feeder puzzles have no strip (transparent/no background on that cell)
- Solution appears under the title in monospace; green when solved
- Solved rows retain the green background
- PuzzleRowActions, PuzzleLinks, MetaBadge are reused without modification
- MiniPuzzlePanel expansion row (`colSpan={99}`) continues to work
- No change to the accordion header or any other component
