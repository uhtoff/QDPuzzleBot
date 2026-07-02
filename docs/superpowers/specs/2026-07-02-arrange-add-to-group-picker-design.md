# Arrange tab — searchable "Add to group" picker

**Date:** 2026-07-02
**Status:** Design approved, pending spec review

## Goal

On the Arrange tab, the per-card ⋮ (kebab) menu's "Add to group" section renders every
eligible group as a flat, unsearchable list of `Menu.Item`s. On large hunts (up to ~30
groups) this list overflows the viewport and pushes the menu's other actions (Remove from
group, convert, minimeta, delete) off-screen, and finding the right group means scanning a
long list.

Replace the flat list with a single **"Add to group…"** entry that opens a searchable,
scroll-bounded picker. Desktop-first, but touch-capable via a responsive bottom sheet.

## In scope

- The "Add to group" affordance inside `PuzzleCard`'s kebab menu in `DragBoard.tsx`.
- A new searchable picker component (search field + bounded scrollable list of groups).
- Responsive rendering: popover on desktop, bottom-sheet drawer on narrow/touch viewports.

## Out of scope

- Remove-from-group, convert-to-meta, minimeta, and delete actions in the same kebab — unchanged.
- Drag-and-drop board behaviour, columns, counts — unchanged.
- The `metas` / `addable` computation, the retag mutation, types, and any backend/API — unchanged.
- The self-target special case's *semantics* (self-feed / self-inform) — preserved as-is, only relocated into the picker.

## Relevant files

- `frontend/src/components/DragBoard.tsx`
  - `PuzzleCard` (~line 76) — the draggable card and its kebab `Menu` (~lines 142–299).
  - "Add to group" section (~lines 156–180): `Menu.Label`, the `addable.map(...)` list, and
    the `<Menu.Item disabled>Already in all groups</Menu.Item>` empty state (~line 158).
  - `addable` computation (~lines 89–96) and `ownTargetCode` self-target special case.
  - `metas` derived from `detail.board` (~lines 454–456), passed to `PuzzleCard` via `metas` prop (~line 663).
- Retag path (unchanged, for reference): `onRetag` → `runRetag` (~line 458) → `useRetagPuzzle` in `frontend/src/api.ts`.
- Reusable patterns already in the codebase: Mantine `Popover` (`PuzzleDrawer.tsx:179`,
  `PuzzleEditCells.tsx:80`), `TextInput`, `ScrollArea`, `Drawer`/`Modal`
  (`ConfirmModal.tsx`, `PuzzleEditCells.tsx:118`), `useMediaQuery`.

## Required behaviour

### Kebab menu entry (decides up front)

- If `addable.length === 0`: render the existing disabled **"Already in all groups"** row
  directly in the kebab. Do **not** render an "Add to group…" entry. (Empty state stays in
  the kebab; it never appears inside the picker.)
- If `addable.length >= 1`: render a single **"Add to group…"** entry that opens the picker.
  The picker can therefore assume it always has at least one option.

### The picker (one component, responsive)

- **Desktop-first:** a `Popover` anchored near the card/menu, containing:
  - a `TextInput` pinned at the top, autofocused, filtering the list by group name
    (case-insensitive substring match on `name`);
  - a bounded `ScrollArea` (fixed max-height, ~5 rows visible) listing matching groups as
    full-width clickable rows.
- **Touch / narrow viewport** (detected via `useMediaQuery`): the *same* picker renders as a
  bottom sheet — Mantine `Drawer position="bottom"` (or full-screen `Modal`) — with search
  pinned at top and full-width, finger-sized rows, so the on-screen keyboard slides up
  beneath it cleanly.
- **Selecting a group** calls the existing `onRetag(puzzle.id, code, "add")` — same mutation,
  one action, then the picker closes.
- **Self-target special case:** when a listed group's `code === ownTargetCode`, its row(s)
  carry the existing self-feed / self-inform behaviour (not a plain add), exactly as the
  current menu items do. The picker preserves whatever action the current code attaches to
  that case.
- **No-match state:** if the search text matches no group, show a simple "No groups match"
  line inside the picker (distinct from the kebab-level "Already in all groups", which the
  picker never shows because it only opens when `addable` is non-empty).

## Constraints

- No changes to data flow, `addable`/`metas` derivation, the retag mutation, types, or backend.
- Preserve existing Mantine styling conventions and reuse existing components/patterns where practical.
- Keep the diff narrow and focused on the "Add to group" affordance only.
- Follow the project's existing terminology in user-facing copy ("group").

## Acceptance criteria

1. On a hunt with many groups, opening a card's ⋮ menu shows a short menu: "Add to group…"
   plus the unchanged Remove/convert/minimeta/delete actions — none pushed off-screen.
2. "Add to group…" opens a picker with a search field; typing narrows the group list;
   clicking a group adds the puzzle to it (same result as before) and closes the picker.
3. On a narrow/touch viewport the picker appears as a bottom sheet with large rows and a
   working on-screen keyboard for search.
4. When the puzzle is already in every group, the kebab shows a disabled "Already in all
   groups" row and no "Add to group…" entry.
5. The self-feed / self-inform behaviour for the puzzle's own target group is preserved.
6. No regressions to drag-and-drop, counts, or the other kebab actions.
