# Design: Edit and Delete Clues (Evidence Items) + Note Delete Confirmation

## Goal

Add the ability to delete and edit the note text of evidence items (clues) in the Evidence panel. Also add a confirmation modal to the existing note delete in PuzzleDrawer.

## In scope

- Delete an evidence item from the Evidence panel (with confirm modal)
- Edit the note text of an evidence item inline in the Evidence panel
- Add `ConfirmModal` to the existing note delete in `PuzzleDrawer`

## Out of scope

- Editing the source puzzle of an evidence item (delete and re-log instead)
- Editing note text in PuzzleDrawer (add-only, delete-with-confirm is sufficient)
- Bang command equivalents (`!clue` has no delete/edit counterpart — web-only)

## Relevant files

- `bot/store/mysqldb.py` — `MySQLEvidenceDb` class
- `bot/web/actions.py` — PATCH and new DELETE endpoint for `/evidence/<item_id>`
- `frontend/src/api.ts` — mutation hooks
- `frontend/src/components/EvidencePanel.tsx` — clue UI
- `frontend/src/components/PuzzleDrawer.tsx` — note delete confirm

## Required behaviour

### Backend

**`MySQLEvidenceDb.delete(item_id)`**
- Deletes the row with the given id from `evidence_items`.
- No-op if the id does not exist.

**`MySQLEvidenceDb.update_note(item_id, note)`**
- Updates the `note` column for the given id.
- Returns the updated `EvidenceData`, or `None` if not found.

**`DELETE /evidence/<item_id>`** (new, in `actions.py`)
- Requires login. Validates the item exists and belongs to a hunt the caller can see (same visibility check as the existing PATCH).
- Rejects if the hunt is archived.
- Calls `EvidenceJsonDb.delete(item_id)`.
- Fires `cog.service.refresh_evidence(hunt_obj)` as a background task.
- Returns `{"ok": True}` on success.

**`PATCH /evidence/<item_id>`** (extend existing)
- Existing behaviour for `target_puzzle_id` is unchanged.
- If `note` is present in the request body (non-empty string), calls `EvidenceJsonDb.update_note(item_id, note.strip())`.
- Both fields may be present in the same request; each is applied if present.
- Fires `refresh_evidence` after any change.

### Frontend

**`useDeleteEvidence(huntUid)`** (new hook in `api.ts`)
- Calls `DELETE /api/evidence/:id`.
- On success: invalidates `["evidence", huntUid]`.

**`useEditClueNote(huntUid)`** (new hook in `api.ts`)
- Calls `PATCH /api/evidence/:id` with `{ note }`.
- On success: invalidates `["evidence", huntUid]`.

**`EvidenceRow` in `EvidencePanel.tsx`**
- Note cell: clicking the note text enters edit mode — the text is replaced by a `TextInput` pre-filled with the current note. Enter or blur saves (calls `useEditClueNote`). Escape cancels without saving. While saving, the input is disabled.
- Delete: a trash `ActionIcon` at the end of the row opens a `ConfirmModal` ("Delete this clue?"). Confirming calls `useDeleteEvidence`.
- Both actions are hidden (or disabled) when the hunt is archived.

**`PuzzleDrawer.tsx` — note delete**
- The existing per-note trash `ActionIcon` (currently calls `erase` directly) is changed to open a `ConfirmModal` ("Delete this note?"). Confirming calls `erase.mutate`.

## Constraints

- Archived hunts must not allow edits or deletes (same guard as all other mutating endpoints).
- `refresh_evidence` is always best-effort / fire-and-forget — a failed Google Sheets sync must not fail the HTTP response.
- The existing `useUpdateEvidence` hook (for target assignment) is unchanged; the new `useEditClueNote` hook is separate.

## Acceptance criteria

- Clicking a note in the Evidence panel makes it editable inline.
- Saving (Enter/blur) updates the note in the DB and refreshes the Evidence panel.
- Escape cancels without changes.
- The trash icon on a clue row opens a confirm modal; confirming deletes it from DB and removes it from the panel.
- The trash icon on a puzzle note opens a confirm modal; confirming removes the note.
- Both clue operations update the Evidence Google Sheet tab.
- Neither clue operation is available on an archived hunt.
