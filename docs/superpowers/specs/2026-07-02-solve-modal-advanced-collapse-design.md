# Solve modal — primary action up top, secondary actions behind "Advanced"

**Date:** 2026-07-02
**Status:** Design approved, pending spec review

## Goal

The `SolveModal` (`frontend/src/components/PuzzleEditCells.tsx`) crowds several actions
into one flat list. Restructure it so the everyday action is prominent at the top and the
rarer actions sit behind an "Advanced" collapse, reusing the pattern from `NewHuntModal`.

At the same time, make the modal's already-solved branch (currently unreachable dead code)
reachable via the existing Unsolve control, giving it the same shape.

## Background: current behaviour

`SolveModal` opens whenever its `puzzle` prop is non-null and branches on `puzzle.solved`:

- **Unsolved branch** (reachable): "Partial solution (optional)" + **Mark partial**, then
  "Solution" + **Mark complete**, then **Mark complete (no answer)**.
- **Solved branch** (currently unreachable): "Change solution" + **Change solution**, then
  "Add solution" + **Add solution**.

The solved branch is dead code because every entry point only opens the modal for *unsolved*
puzzles:
- `PuzzleRowActions.tsx:38` — green Solve icon renders only when `editable && !p.solved`
  (solved rows show a red **Mark unsolved** icon instead → `onUnsolve`).
- `PuzzleDrawer.tsx:235` — "Mark solved" button shows only when not solved.

Today **Unsolve** (the red icon in `PuzzleTable` and `BoardGroup`) goes through
`useUnsolveConfirm` — a shared hook that shows a `ConfirmModal` ("Unsolve puzzle? This clears
the solution…") then calls `useUnsolvePuzzle`.

## In scope

- Restructure both branches of `SolveModal` with an "Advanced" collapse.
- Add an **unsolve** action to the solved branch (`useUnsolvePuzzle`), dropping the separate
  confirm dialog.
- Reroute the Unsolve control at the two table entry points to open `SolveModal` instead of
  the confirm modal.
- Remove `useUnsolveConfirm.tsx` (becomes unused).

## Out of scope

- `PuzzleDrawer` (no unsolve action there today) and `MiniPuzzlePanel` (inline mini solve) —
  unchanged.
- The solve/unsolve API endpoints, `useSolvePuzzle`, `useUnsolvePuzzle`, `SolveMode`,
  notifications, `showSheetWarning`, and optimistic cache updates — all unchanged.
- The `submit(mode)` guards and `markComplete()` logic — unchanged; only their placement moves.

## Relevant files

- `frontend/src/components/PuzzleEditCells.tsx` — `SolveModal` (lines 118–287). Restructure.
- `frontend/src/components/PuzzleTable.tsx` — uses `useUnsolveConfirm` (import 23, hook 178,
  `onUnsolve` 390, `{unsolveModal}` 412) and opens `SolveModal` via `solvePuzzle` state (411).
- `frontend/src/components/BoardGroup.tsx` — uses `useUnsolveConfirm` (import 31, hook 72,
  `onUnsolve` 239 & 289, `{unsolveModal}` 366) and `SolveModal` at 309.
- `frontend/src/components/useUnsolveConfirm.tsx` — to be removed.
- Pattern reference: `frontend/src/pages/HuntList.tsx` `NewHuntModal` "Advanced" collapse
  (state 68, toggle 137–143) — also mirrored in `AdminSettingsPanel.tsx`.

## Required behaviour

### Advanced-collapse pattern (both branches)

Reuse the established convention exactly:
- A `<Divider />`, then a `<Group onClick={() => setAdvancedOpen(o => !o)} style={{ cursor: "pointer" }}>`
  containing `IconChevronUp`/`IconChevronDown` (`size={16}`, open/closed) and
  `<Text size="sm" fw={600}>Advanced</Text>`, then `<Collapse in={advancedOpen}>` wrapping a
  `<Stack gap="sm" pb="sm">`.
- One shared `advancedOpen` state (`useState(false)`) in `SolveModal`, reset to `false`
  whenever the modal opens for a different puzzle (extend the existing `[puzzle?.id]` effect).
  Default collapsed.

### Unsolved branch (Solve)

Order top-to-bottom:
1. **Primary, always visible:** `TextInput` labeled **"Answer"** (keeps the existing
   `description` "Posts the SOLVED message to the channel and archives the sheet."), bound to
   `fullText`; below it a green primary `Button` **"Solve"** → `submit("full")`, disabled
   until `fullText.trim()`, `loading={solve.isPending}`. Enter in the field submits `"full"`.
2. **Advanced collapse:**
   - `Button` **"Mark complete (no answer)"** (green, `variant="outline"`) → `markComplete()`.
   - `TextInput` **"Partial solution (optional)"** (existing description) bound to
     `partialText`; below it `Button` **"Mark partial"** (blue, `variant="light"`) →
     `submit("partial")`. Enter submits `"partial"`.

### Solved branch (reached via Unsolve)

Order top-to-bottom:
1. Keep the dimmed **"Current solution: <solution>"** line at the top.
2. **Primary, always visible:** red `Button` **"Mark unsolved"** → calls the unsolve mutation
   (`useUnsolvePuzzle`) for `puzzle.id`, then `onClose()`; `loading` while pending. Directly
   below, a description `Text size="sm" c="dimmed"`: "Clears the solution and moves the channel
   back to unsolved. You can re-solve it later." (No separate confirm dialog.)
3. **Advanced collapse:**
   - `TextInput` **"Change solution"** (existing description, prefilled with current solution
     via the existing effect) bound to `changeText`; `Button` **"Change solution"** (orange,
     `variant="light"`) → `submit("change")`, disabled until `changeText.trim()`.
   - `TextInput` **"Add solution"** (existing description) bound to `addText`; `Button`
     **"Add solution"** (teal, `variant="light"`) → `submit("add")`, disabled until
     `addText.trim()`.

### Modal title

- Unsolved: **"Solve — <name>"** (unchanged).
- Solved: **"<name>"** (drop the "Solve —" prefix, since the primary action is now unsolve).

### Entry-point rewiring

- `SolveModal` gains `const unsolve = useUnsolvePuzzle(uid);` and performs the unsolve inline
  (with `onError: notifyError`, matching the old hook).
- In `PuzzleTable.tsx` and `BoardGroup.tsx`: change `onUnsolve` to open the modal —
  `onUnsolve={(p) => setSolvePuzzle(p)}` (same setter `onSolve` uses). Because the modal
  branches on `puzzle.solved`, a solved puzzle shows the solved branch automatically.
- Remove the `useUnsolveConfirm` import, the `const { request, modal } = useUnsolveConfirm(...)`
  call, and the `{unsolveModal}` render from both files.
- Delete `frontend/src/components/useUnsolveConfirm.tsx` (no remaining consumers).

## Constraints

- No API/mutation/type changes; reuse `useSolvePuzzle` and `useUnsolvePuzzle` as-is.
- Match the existing Mantine v7 "Advanced" pattern (chevron + bold label + `Collapse`).
- Keep the diff scoped to the four files above.

## Acceptance criteria

1. Clicking Solve on an unsolved puzzle shows an **"Answer"** field + green **"Solve"** button
   at the top; **Mark complete (no answer)** and the **partial** controls are hidden under a
   collapsed **"Advanced"** section that matches the Hunt-modal styling.
2. Clicking **Unsolve** on a solved puzzle now opens the modal (no confirm dialog) showing the
   current solution, a red **"Mark unsolved"** primary button with explanatory copy, and a
   collapsed **"Advanced"** section containing **Change solution** and **Add solution**.
3. "Mark unsolved" unsolves the puzzle (same result as the old confirm flow) and closes the
   modal; errors surface via `notifyError`.
4. All four solve modes (full/partial/change/add) and `markComplete()` behave exactly as before
   — same notifications, sheet warnings, guards, and Enter-to-submit.
5. `useUnsolveConfirm.tsx` is deleted and no longer imported anywhere; the build passes
   (`npm run build`).
6. The Advanced section defaults to collapsed each time the modal opens.
