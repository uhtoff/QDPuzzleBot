# Member Create Puzzle/Group and Web Unsolve Design

## Goal

Allow any guild member who can see a hunt to create puzzles and groups from the web dashboard, matching the existing bang command behaviour. Add an unsolve button to the web dashboard for solved puzzles.

## In scope

- Drop the organiser gate from the `create_puzzle` and `create_meta` web API endpoints.
- Show "New puzzle" and "New Group" buttons to any logged-in user viewing a non-archived hunt.
- Add an unsolve button in `PuzzleRowActions` that appears on solved puzzles in place of the solve checkmark.

## Out of scope

- Changes to any other permission gates (rename, delete, retag, etc. remain organiser-only).
- New API fields (no `can_create` flag added).
- Any bang command changes.

## Relevant files

- `bot/web/actions.py` — `create_puzzle`, `create_meta`, `_organiser_hunt`
- `frontend/src/pages/HuntBoard.tsx` — `CreateControls` visibility gate
- `frontend/src/components/PuzzleRowActions.tsx` — solve/unsolve button rendering
- `frontend/src/components/PuzzleTable.tsx` — wires `onSolve`; needs `onUnsolve`
- `frontend/src/api.ts` — `useUnsolvePuzzle` already exists

## Required behaviour

### Create puzzle/group

The `create_puzzle` (`POST /hunts/<uid>/puzzles`) and `create_meta` (`POST /hunts/<uid>/metas`) endpoints currently call `_organiser_hunt`, which requires organiser role. Replace with `_visible_hunt_by_uid` — the same membership check used by solve, note, and link endpoints.

On the frontend, `CreateControls` is currently rendered only when `data.hunt.can_manage`. Change the condition to `!data.hunt.archived` so any authenticated user viewing a live hunt sees the "New puzzle" and "New Group" buttons.

The backend's hunt-visibility check (which gates the entire board response) is the effective membership guard — no additional frontend gating is needed.

### Unsolve

`PuzzleRowActions` currently shows a green solve checkmark only when `editable && !p.solved`. When `editable && p.solved`, nothing is shown in that slot.

Add an `onUnsolve` prop to `PuzzleRowActions`. When `editable && p.solved`, render an unsolve `ActionIcon` (red/subtle, `IconCircleX`) with tooltip "Mark unsolved" in the same slot. Wire `useUnsolvePuzzle` in `PuzzleTable` and pass `onUnsolve` down to `PuzzleRowActions`.

The backend `POST /puzzles/<id>/unsolve` endpoint is already member-accessible and needs no changes.

## Constraints

- No new API fields on the hunt detail response.
- The organiser gate must remain on all other mutation endpoints (rename, delete, retag, convert, etc.).
- Unsolve is only shown when `editable` (non-archived hunt), matching the solve button's guard.

## Acceptance criteria

- A regular member (no organiser role) can create a puzzle or group from the web dashboard on a live hunt.
- The "New puzzle" / "New Group" buttons are hidden on archived hunts.
- A solved puzzle row shows an unsolve button; clicking it marks the puzzle unsolved and updates the board.
- The solve checkmark does not appear on already-solved puzzles.
- Organiser-only actions (rename, delete, retag, etc.) remain unchanged.
