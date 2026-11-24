# Current Spec — two dashboard rename bugs (reported 2026-11-24)

## Goal

Fix two bugs the user found while testing on test (puzzbot.com, staging 8c24d68).

## Bug 1 — drawer rename swallows errors

Renaming a puzzle from the dashboard puzzle drawer to a name another puzzle already has
shows no error; the rename box just closes. (A rename whose CHANNEL name clashes does
prompt correctly — that is the new channel-name-clash Accept/Cancel.)

Known cause (seen while building the clash prompt): `frontend/src/components/PuzzleDrawer.tsx`
rename submit — `onError` only does `setRenameEditing(false)` and
`channelClash.handle(err, ...)`; any other error (e.g. 400 `duplicate_name` from
`POST /api/puzzles/<id>/rename`, bot/web/actions.py `rename_puzzle`) is never shown.
Fix: `if (!channelClash.handle(...)) notifyError(err)` like CreateControls.
Also note the web route returns 400 for duplicate_name while create returns 409 — leave
unless there's a reason.

## Bug 2 — renames don't reach the Unassigned Feeders roll-up

After renaming a puzzle, the hunt's "Unassigned Feeders" tab still shows the old name
until another puzzle is added or moved. Likely `PuzzleService.rename_puzzle`
(bot/core/puzzle_service.py) refreshes the metas the puzzle feeds but never calls
`refresh_unassigned` / `refresh_unassigned_safe` (create and moves do). Check both
surfaces (`!rename_puzzle` in bot/cogs/puzzles.py and the web route) go through the
service so one fix covers both. Also consider "Unassigned Puzzles" tab.

## Constraints

- Work on `staging` in the main checkout (small change); default branch flow: staging →
  user pushes main.
- Tests: `timeout … pipenv run python -m pytest …`; basedpyright bot/ before push;
  frontend `npx tsc --noEmit` + `npx vitest run`.
- Push to staging only when the user says.

## Acceptance

- Drawer rename to a duplicate name shows the "A puzzle with that name already exists." toast.
- Rename updates Unassigned Feeders immediately (test asserts the refresh is triggered).
