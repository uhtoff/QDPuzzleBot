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
- Generalize `SolveModal`'s `puzzle` prop from `Puzzle` to a minimal target shape
  (`{ id, name, solved, solution }`) so mini puzzles can use the same modal.
- Reroute the Unsolve control to open `SolveModal` at all three entry points: `PuzzleTable`,
  `BoardGroup`, and `MiniPuzzlePanel` (a solved mini's red X opens the solved-branch modal).
- Remove `useUnsolveConfirm.tsx` (all consumers migrate to the modal).

## Out of scope

- `PuzzleDrawer` — has no unsolve action today; unchanged.
- The **unsolved**-mini inline quick-solve in `MiniPuzzlePanel` (type an answer + check) stays
  as-is; only the solved-mini unsolve/edit path moves to the modal.
- The solve/unsolve API endpoints, `useSolvePuzzle`, `useUnsolvePuzzle`, `SolveMode`,
  notifications, `showSheetWarning`, and optimistic cache updates — all unchanged.
- The `submit(mode)` guards and `markComplete()` logic — unchanged; only their placement moves.

Note: `MiniPuzzle` (`{ id, name, solved, solution, channel_id }`) structurally satisfies the
generalized prop, and minis are real puzzles server-side (the same `/solve` and `/unsolve`
endpoints accept a mini's id), so the modal's mutations work unchanged for a mini.

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

### Generalized prop type

- `SolveModal` reads only `puzzle.id`, `puzzle.name`, `puzzle.solved`, `puzzle.solution`.
  Change its prop type from `Puzzle | null` to a minimal exported alias
  `SolveTarget = Pick<Puzzle, "id" | "name" | "solved" | "solution">` (`SolveTarget | null`).
  Full `Puzzle` values still satisfy it (existing call sites unchanged), and `MiniPuzzle`
  satisfies it too.

### Entry-point rewiring

- `SolveModal` gains `const unsolve = useUnsolvePuzzle(uid);` and performs the unsolve inline
  (with `onError: notifyError`, matching the old hook).
- In `PuzzleTable.tsx` and `BoardGroup.tsx`: change `onUnsolve` to open the modal —
  `onUnsolve={setSolvePuzzle}` (same setter `onSolve` uses). Because the modal branches on
  `puzzle.solved`, a solved puzzle shows the solved branch automatically.
- In `MiniPuzzlePanel.tsx`: add a `solveMini` state (`MiniPuzzle | null`), render a
  `<SolveModal puzzle={solveMini} uid={uid} onClose={() => setSolveMini(null)} />`, and change
  the solved-mini red-X `onClick` to `setSolveMini(mini)` (replacing the direct
  `unsolvePuzzle.mutate(...)`). Keep the unsolved-mini inline solve unchanged. Drop the
  `useUnsolvePuzzle` import from this file if it becomes unused.
- Remove the `useUnsolveConfirm` import, its hook call, and the `{unsolveModal}` render from
  all files that used it.
- Delete `frontend/src/components/useUnsolveConfirm.tsx` (no remaining consumers).

## Constraints

- No API/mutation/endpoint/`SolveMode` changes; reuse `useSolvePuzzle` and `useUnsolvePuzzle`
  as-is. (The only type change is generalizing the `SolveModal` prop, above.)
- Match the existing Mantine v7 "Advanced" pattern (chevron + bold label + `Collapse`).
- Keep the diff scoped to `PuzzleEditCells.tsx`, `PuzzleTable.tsx`, `BoardGroup.tsx`,
  `MiniPuzzlePanel.tsx`, and the deleted `useUnsolveConfirm.tsx`.

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
5. Clicking the red X on a **solved mini** opens the same solved-branch modal (Mark unsolved /
   Change / Add) instead of a one-click unsolve or a confirm dialog; the unsolved-mini inline
   quick-solve is unchanged.
6. `useUnsolveConfirm.tsx` is deleted and no longer imported anywhere; the build passes
   (`npm run build`).
7. The Advanced section defaults to collapsed each time the modal opens.
