# Current Spec

## Goal

Two UI improvements on the dashboard's "rearrange" (arrange) tab, which renders
`frontend/src/components/DragBoard.tsx`:

1. **Retagging grey-out applies to every queued puzzle.** Today the greyed-out
   "Retagging…" state shows on only one card even when multiple retags are in
   flight.
2. **Drag a puzzle onto the Unassigned column to clear it from all metas.** The
   ⋮-menu "Remove from {meta}" remains for selective single-meta removal (a
   puzzle in two metas where only one should be cleared).

## In scope

- Frontend: pending-state tracking + making the Unassigned column a drop target.
- Backend: a new `"clear"` mode on `POST /api/puzzles/{id}/meta` that removes a
  puzzle from all metas atomically.

## Out of scope

- The ⋮ menu and the existing `add` / `move` / `remove` semantics.
- Discord channel-move behavior (clear does not move channels, matching
  remove/add).
- Any unrelated refactor of DragBoard or the puzzle service.

## Relevant files

- `frontend/src/components/DragBoard.tsx` — arrange tab UI, drag/drop, pending state.
- `frontend/src/api.ts` — `RetagMode` type + `useRetagPuzzle` mutation (~L261).
- `bot/web/actions.py` — `retag_puzzle()` route handler (L382–456).
- `bot/core/puzzle_service.py` — `move_to_meta` / `add_to_meta` / `remove_from_meta`
  (L382–426); new `clear_metas` goes here.

## Required behaviour

### Issue 1 — pending state per puzzle

- Replace the single `retagPuzzleId` (derived from `retag.variables`, which only
  reflects the latest mutate call) with a locally-tracked `Set<number>` of
  pending puzzle ids in `DragBoard`.
- `runRetag` adds the id before `retag.mutate(...)` and removes it in a
  per-invocation `onSettled` (fires on both success and error).
- `PuzzleCard` receives `isPending={pendingIds.has(p.id)}`. Per-card visuals
  (opacity 0.5, "Retagging…" loader, drag disabled) are unchanged — they now
  apply to every queued card.

### Issue 2 — drag to Unassigned clears all metas

**Backend**

- Add `"clear"` to the allowed modes and the dispatch in `retag_puzzle`.
  `meta_code` is optional/ignored when `mode == "clear"`; do not require a valid
  `meta_round` for that mode (skip the `bad_meta_code` gate and the
  `cannot_remove_own_meta` gate, which are meta-specific).
- New `PuzzleService.clear_metas(puzzle)`, modelled on `remove_from_meta`:
  - If `puzzle.is_metapuzzle()` → raise a guarded error mapped to HTTP 400
    (`cannot_unassign_meta`). A metapuzzle cannot be unassigned (mirrors the
    `move` protection that never clears a metapuzzle's tags).
  - Otherwise: capture `old_tags = list(puzzle.tags)`; if non-empty,
    `puzzle.tags.clear()` and `PuzzleJsonDb.commit(puzzle)`.
  - `refresh_metas(hunt, [RoundJsonDb.get_by_attr(id=t) for t in old_tags])`
    (matching `remove_from_meta`); `notifier.board_changed(hunt)`; raise
    `SheetRefreshError(failed)` on refresh failure.
  - No Discord channel move.
- Keep the existing audit entry and best-effort `refresh_unassigned_safe`.

**Frontend**

- Add `"clear"` to `RetagMode` in `api.ts`.
- Make the Unassigned column (`group.meta_code === null`) a valid drop target:
  remove the `droppable`/`disabled: !droppable` gating so it registers via
  `useDroppable` and reflects `isOver`. Keep its dimmed resting style.
- In `onDragEnd`, when the target `metaCode` is `null`:
  - If the dragged puzzle has no metas → no-op (avoid an empty clear call).
  - If it is a metapuzzle → skip with a brief notification ("a meta can't be
    unassigned").
  - Otherwise confirm (`Remove "X" from all metas: A, B?`) then
    `runRetag(puzzleId, "", "clear")`.
- Existing meta→meta move flow (including the multi-meta "move loses other
  memberships" confirm) is unchanged.

## Constraints

- Match existing service structure (`remove_from_meta` is the template); the tag
  mutation commits before sheet refresh; sheet failures are non-fatal warnings.
- Frontend follows existing dnd-kit + Mantine patterns already in DragBoard.
- Small, reviewable commits: (1) issue 1, (2) issue 2 backend, (3) issue 2 frontend.

## Acceptance criteria

- Queue two retags quickly → both cards show the greyed "Retagging…" state until
  each settles.
- Drag a feeder onto Unassigned → after confirm, it leaves all metas and lands in
  the Unassigned column; affected meta rollups refresh once.
- Drag a multi-meta feeder onto Unassigned → confirm lists all its metas; all are
  cleared.
- Dragging a metapuzzle onto Unassigned → blocked (notification), no tag change.
- ⋮ "Remove from {meta}" still removes from a single meta only.
- A puzzle already unassigned dropped on Unassigned → no API call.
