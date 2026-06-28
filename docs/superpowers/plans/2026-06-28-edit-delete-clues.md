# Edit and Delete Clues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add delete (with confirm modal) and inline note-edit to evidence items in the Evidence panel, and add a confirm modal to the existing note delete in PuzzleDrawer.

**Architecture:** Backend: add `delete` override and `update_note` to `MySQLEvidenceDb`, a new `DELETE /evidence/<item_id>` endpoint, and extend the existing `PATCH /evidence/<item_id>` to accept a `note` field. Frontend: two new hooks (`useDeleteEvidence`, `useEditClueNote`), inline editing in `EvidenceRow`, `ConfirmModal` on clue and note deletes.

**Tech Stack:** Python/Quart (backend), React/Mantine/TanStack Query (frontend), MySQL.

---

### Task 1: Add `delete` and `update_note` to `MySQLEvidenceDb`

**Files:**
- Modify: `bot/store/mysqldb.py` (class `MySQLEvidenceDb`, after line 663)
- Test: `tests/test_evidence_service.py` (add new class at end of file)

- [ ] **Step 1: Write the failing tests**

Add this class to the end of `tests/test_evidence_service.py`:

```python
class TestMySQLEvidenceDbMethods:
    """MySQLEvidenceDb.delete and update_note — SQL execution and commit."""

    def _make_db(self):
        from bot.store.mysqldb import MySQLEvidenceDb
        from unittest.mock import MagicMock
        mock_mydb = MagicMock()
        mock_cursor = MagicMock()
        mock_mydb.cursor.return_value = mock_cursor
        db = MySQLEvidenceDb(dir_path=None, mydb=mock_mydb)
        return db, mock_mydb, mock_cursor

    def test_delete_executes_delete_sql_and_commits(self):
        db, mock_mydb, mock_cursor = self._make_db()
        db.delete(42)
        mock_cursor.execute.assert_called_once_with(
            "DELETE FROM evidence_items WHERE id = %s", (42,)
        )
        mock_mydb.commit.assert_called_once()
        mock_cursor.close.assert_called()

    def test_delete_nonexistent_id_does_not_raise(self):
        db, mock_mydb, mock_cursor = self._make_db()
        # Should not raise even if no rows matched
        db.delete(999)
        mock_cursor.execute.assert_called_once()

    def test_update_note_executes_update_then_select(self):
        import datetime
        from bot.store.evidence_data import EvidenceData
        db, mock_mydb, mock_cursor = self._make_db()
        row = {
            "id": 7, "hunt_id": 10, "source_puzzle_id": 5,
            "target_puzzle_id": None, "note": "new text",
            "created_at": datetime.datetime(2026, 6, 28, 12, 0, 0),
        }
        mock_cursor.fetchone.return_value = row

        result = db.update_note(7, "new text")

        calls = mock_cursor.execute.call_args_list
        assert any("UPDATE evidence_items SET note" in str(c) for c in calls)
        assert any("SELECT * FROM evidence_items WHERE id" in str(c) for c in calls)
        mock_mydb.commit.assert_called_once()
        assert isinstance(result, EvidenceData)
        assert result.note == "new text"

    def test_update_note_returns_none_for_missing_id(self):
        db, mock_mydb, mock_cursor = self._make_db()
        mock_cursor.fetchone.return_value = None

        result = db.update_note(999, "text")

        assert result is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
timeout 60 pipenv run python -m pytest tests/test_evidence_service.py::TestMySQLEvidenceDbMethods -q > /tmp/test.log 2>&1; cat /tmp/test.log
```

Expected: 4 failures (methods don't exist yet).

- [ ] **Step 3: Implement `delete` and `update_note` in `MySQLEvidenceDb`**

Add these two methods to `MySQLEvidenceDb` in `bot/store/mysqldb.py`, after the existing `set_target` method (after line 663):

```python
    def delete(self, item_id: int) -> None:
        """Delete an evidence item by id. No-op if the id does not exist."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute("DELETE FROM evidence_items WHERE id = %s", (item_id,))
        finally:
            cursor.close()
        self.mydb.commit()

    def update_note(self, item_id: int, note: str):
        """Update the note text for an evidence item. Returns updated EvidenceData or None."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(
                "UPDATE evidence_items SET note = %s WHERE id = %s",
                (note, item_id),
            )
            cursor.execute("SELECT * FROM evidence_items WHERE id = %s", (item_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        self.mydb.commit()
        return EvidenceData.import_dict(row) if row else None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_evidence_service.py::TestMySQLEvidenceDbMethods -q > /tmp/test.log 2>&1; cat /tmp/test.log
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/store/mysqldb.py tests/test_evidence_service.py
git commit -m "feat(store): add delete and update_note to MySQLEvidenceDb"
```

---

### Task 2: Add `DELETE /evidence/<item_id>` endpoint

**Files:**
- Modify: `bot/web/actions.py` (add new route after the existing PATCH at line 1665)

- [ ] **Step 1: Add the DELETE endpoint**

Add this immediately after the closing `return` of `update_evidence` (after line 1665). The pattern mirrors the existing PATCH: `login_required` gates unauthenticated access; we check item existence and the archived flag, then delete.

```python
@actions_bp.route("/evidence/<int:item_id>", methods=["DELETE"])
@login_required
async def delete_evidence(item_id):
    from bot.store import EvidenceJsonDb, HuntJsonDb
    old = EvidenceJsonDb.get_by_id(item_id)
    if old is None:
        return jsonify({"error": "not found"}), 404

    hunt_obj = HuntJsonDb.get_by_attr(id=old.hunt_id)
    if hunt_obj is None:
        return jsonify({"error": "not found"}), 404

    if hunt_obj.archived:
        return jsonify({"error": "hunt_archived"}), 403

    EvidenceJsonDb.delete(item_id)

    cog = current_app.bot.get_cog("Puzzles")
    if cog is not None:
        asyncio.create_task(cog.service.refresh_evidence(hunt_obj))

    await _audit("delete_evidence", hunt_uid=hunt_obj.uid)
    return jsonify({"ok": True})
```

- [ ] **Step 2: Run the full evidence test suite to check nothing is broken**

```bash
timeout 60 pipenv run python -m pytest tests/test_evidence_service.py tests/test_evidence_commands.py tests/test_evidence_informs.py -q > /tmp/test.log 2>&1; cat /tmp/test.log
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add bot/web/actions.py
git commit -m "feat(api): add DELETE /evidence/<id> endpoint"
```

---

### Task 3: Extend `PATCH /evidence/<item_id>` to accept `note`

**Files:**
- Modify: `bot/web/actions.py` (the existing `update_evidence` function, lines 1641–1665)

- [ ] **Step 1: Add `note` handling to the existing PATCH**

Replace the `update_evidence` function body. The new version applies `target_puzzle_id` if present, and `note` if present (either or both):

```python
@actions_bp.route("/evidence/<int:item_id>", methods=["PATCH"])
@login_required
async def update_evidence(item_id):
    from bot.store import EvidenceJsonDb, HuntJsonDb
    body = await request.get_json()
    old = EvidenceJsonDb.get_by_id(item_id)
    if old is None:
        return jsonify({"error": "not found"}), 404

    item = old
    # Apply note edit if provided
    note = (body.get("note") or "").strip() if body else ""
    if note:
        item = EvidenceJsonDb.update_note(item_id, note) or item

    # Apply target reassignment if provided (key present, even if None)
    if body and "target_puzzle_id" in body:
        old_target = old.target_puzzle_id
        target_puzzle_id = body.get("target_puzzle_id")
        item = EvidenceJsonDb.set_target(item_id, target_puzzle_id) or item

        hunt_obj = HuntJsonDb.get_by_attr(id=item.hunt_id)
        cog = current_app.bot.get_cog("Puzzles")
        if cog is not None and hunt_obj is not None:
            if item.target_puzzle_id:
                asyncio.create_task(cog.service.link_informs_for_evidence(
                    hunt_obj, item.source_puzzle_id, item.target_puzzle_id))
            if old_target and old_target != item.target_puzzle_id:
                asyncio.create_task(cog.service.unlink_informs_for_evidence(
                    hunt_obj, item.source_puzzle_id, old_target))
            asyncio.create_task(cog.service.refresh_target_for_puzzle(hunt_obj, item.target_puzzle_id))
    else:
        hunt_obj = HuntJsonDb.get_by_attr(id=item.hunt_id)
        cog = current_app.bot.get_cog("Puzzles")

    if cog is not None and hunt_obj is not None:
        asyncio.create_task(cog.service.refresh_evidence(hunt_obj))

    return jsonify({"id": item.id, "target_puzzle_id": item.target_puzzle_id, "note": item.note})
```

- [ ] **Step 2: Run the evidence test suite**

```bash
timeout 60 pipenv run python -m pytest tests/test_evidence_service.py tests/test_evidence_commands.py tests/test_evidence_informs.py -q > /tmp/test.log 2>&1; cat /tmp/test.log
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add bot/web/actions.py
git commit -m "feat(api): extend PATCH /evidence/<id> to accept note text edit"
```

---

### Task 4: Add `useDeleteEvidence` and `useEditClueNote` hooks

**Files:**
- Modify: `frontend/src/api.ts` (after `useUpdateEvidence` around line 503)

- [ ] **Step 1: Add the two new hooks after `useUpdateEvidence`**

Insert after the closing `}` of `useUpdateEvidence` (after line 503):

```typescript
export function useDeleteEvidence(huntUid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: number) =>
      apiSend<{ ok: boolean }>(`/api/evidence/${itemId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["evidence", huntUid] }),
  });
}

export function useEditClueNote(huntUid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, note }: { itemId: number; note: string }) =>
      apiSend<{ id: number; note: string; target_puzzle_id: number | null }>(
        `/api/evidence/${itemId}`,
        "PATCH",
        { note }
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["evidence", huntUid] }),
  });
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /home/russ/projects/puzzbot/.claude/worktrees/working-1/frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(frontend): add useDeleteEvidence and useEditClueNote hooks"
```

---

### Task 5: Update `EvidenceRow` — inline note edit + ConfirmModal delete

**Files:**
- Modify: `frontend/src/components/EvidencePanel.tsx`

- [ ] **Step 1: Rewrite `EvidencePanel.tsx` with the new functionality**

The complete updated file:

```tsx
import { useMemo, useState } from "react";
import {
  ActionIcon,
  Box,
  Button,
  Group,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import type { EvidenceItem, HuntDetail } from "../types";
import { useDeleteEvidence, useEditClueNote, useEvidence, useLogClue, useUpdateEvidence } from "../api";
import { ConfirmModal } from "./ConfirmModal";

interface Props {
  uid: string;
  detail: HuntDetail;
}

export function EvidencePanel({ uid, detail }: Props) {
  const { data: items = [], isLoading, error } = useEvidence(uid);
  const updateEvidence = useUpdateEvidence(uid);
  const logClue = useLogClue(uid);
  const deleteEvidence = useDeleteEvidence(uid);
  const editClueNote = useEditClueNote(uid);
  const [filterSource, setFilterSource] = useState<string | null>(null);

  const [cluePuzzleId, setCluePuzzleId] = useState<string | null>(null);
  const [clueNote, setClueNote] = useState("");

  const editable = !detail.hunt.archived;

  function submitClue() {
    if (!cluePuzzleId || !clueNote.trim()) return;
    logClue.mutate(
      { puzzleId: Number(cluePuzzleId), note: clueNote.trim() },
      { onSuccess: () => { setCluePuzzleId(null); setClueNote(""); } }
    );
  }

  const allPuzzles = useMemo(() => {
    const seen = new Map<number, string>();
    detail.board.forEach((g) => g.puzzles.forEach((p) => seen.set(p.id, p.name)));
    return Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name }));
  }, [detail]);

  const sourceOptions = useMemo(() => {
    const seen = new Map<number, string>();
    items.forEach((i) => seen.set(i.source_puzzle_id, i.source_puzzle_name));
    return [{ value: "", label: "All sources" }, ...Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name }))];
  }, [items]);

  const filtered = filterSource
    ? items.filter((i) => String(i.source_puzzle_id) === filterSource)
    : items;

  if (isLoading) return <Text size="sm" c="dimmed">Loading evidence…</Text>;
  if (error) return <Text size="sm" c="red">Failed to load evidence.</Text>;

  return (
    <Stack gap="sm">
      <Group gap="sm">
        <Select
          size="xs"
          placeholder="Filter by source puzzle"
          data={sourceOptions}
          value={filterSource ?? ""}
          onChange={(v) => setFilterSource(v || null)}
          clearable
          w={240}
        />
        <Text size="xs" c="dimmed">{filtered.length} item{filtered.length !== 1 ? "s" : ""}</Text>
      </Group>

      {editable && (
        <Group gap="sm">
          <Select
            size="xs"
            placeholder="Source puzzle …"
            data={allPuzzles}
            value={cluePuzzleId ?? ""}
            onChange={(v) => setCluePuzzleId(v || null)}
            w={240}
          />
          <TextInput
            size="xs"
            placeholder="Log a clue…"
            value={clueNote}
            onChange={(e) => setClueNote(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && submitClue()}
            style={{ flex: 1 }}
          />
          <Button size="xs" onClick={submitClue} loading={logClue.isPending}>Log clue</Button>
        </Group>
      )}

      {filtered.length === 0 ? (
        <Box p="md">
          <Text size="sm" c="dimmed">No clues logged yet. Use <code>!clue</code> or <code>!clues</code> in any puzzle channel.</Text>
        </Box>
      ) : (
        <Table striped highlightOnHover withTableBorder withColumnBorders fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Source Puzzle</Table.Th>
              <Table.Th>Note</Table.Th>
              <Table.Th>Target Puzzle</Table.Th>
              <Table.Th>Logged At</Table.Th>
              {editable && <Table.Th w={40} />}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.map((item) => (
              <EvidenceRow
                key={item.id}
                item={item}
                puzzleOptions={allPuzzles}
                editable={editable}
                onSetTarget={(targetId) =>
                  updateEvidence.mutate({ itemId: item.id, targetPuzzleId: targetId })
                }
                onEditNote={(note) =>
                  editClueNote.mutate({ itemId: item.id, note })
                }
                onDelete={() =>
                  deleteEvidence.mutate(item.id)
                }
              />
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

function EvidenceRow({
  item,
  puzzleOptions,
  editable,
  onSetTarget,
  onEditNote,
  onDelete,
}: {
  item: EvidenceItem;
  puzzleOptions: { value: string; label: string }[];
  editable: boolean;
  onSetTarget: (targetId: number | null) => void;
  onEditNote: (note: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const loggedAt = item.created_at ? item.created_at.slice(0, 16).replace("T", " ") : "";

  function startEdit() {
    setDraft(item.note);
    setEditing(true);
  }

  function commitEdit() {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== item.note) {
      onEditNote(trimmed);
    }
    setEditing(false);
  }

  function cancelEdit() {
    setEditing(false);
  }

  return (
    <Table.Tr>
      <Table.Td>{item.source_puzzle_name}</Table.Td>
      <Table.Td>
        {editable && editing ? (
          <TextInput
            size="xs"
            value={draft}
            onChange={(e) => setDraft(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitEdit();
              if (e.key === "Escape") cancelEdit();
            }}
            onBlur={commitEdit}
            autoFocus
          />
        ) : (
          <Text
            size="sm"
            style={editable ? { cursor: "pointer" } : undefined}
            onClick={editable ? startEdit : undefined}
            title={editable ? "Click to edit" : undefined}
          >
            {item.note}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Select
          size="xs"
          placeholder="Assign target…"
          data={puzzleOptions}
          value={item.target_puzzle_id ? String(item.target_puzzle_id) : null}
          onChange={(v) => onSetTarget(v ? Number(v) : null)}
          searchable
          clearable
          w={200}
          disabled={!editable}
        />
      </Table.Td>
      <Table.Td>{loggedAt}</Table.Td>
      {editable && (
        <Table.Td>
          <ActionIcon
            variant="subtle"
            color="red"
            size="sm"
            onClick={() => setConfirmOpen(true)}
            aria-label="Delete clue"
          >
            <IconTrash size={14} />
          </ActionIcon>
          <ConfirmModal
            opened={confirmOpen}
            onClose={() => setConfirmOpen(false)}
            onConfirm={() => { setConfirmOpen(false); onDelete(); }}
            title="Delete clue"
            message="Delete this clue? This cannot be undone."
            confirmLabel="Delete"
            confirmColor="red"
          />
        </Table.Td>
      )}
    </Table.Tr>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /home/russ/projects/puzzbot/.claude/worktrees/working-1/frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/EvidencePanel.tsx
git commit -m "feat(ui): inline note edit and delete with confirm in EvidencePanel"
```

---

### Task 6: Add `ConfirmModal` to note delete in `PuzzleDrawer`

**Files:**
- Modify: `frontend/src/components/PuzzleDrawer.tsx`

The existing note delete is a direct `erase.mutate(...)` call on a trash `ActionIcon`. We need to gate it through a `ConfirmModal`. The drawer can hold one state variable for the note index pending deletion.

- [ ] **Step 1: Add `eraseIndex` state and swap the trash icon for a confirm flow**

Add the import for `ConfirmModal` and a state variable, then replace the note row's ActionIcon.

At the top of `PuzzleDrawer.tsx`, add `ConfirmModal` to imports:

```tsx
import { ConfirmModal } from "./ConfirmModal";
```

Inside the `PuzzleDrawer` function body, add state (after the existing `const [deleteOpen, setDeleteOpen] = useState(false);` line):

```tsx
const [eraseIndex, setEraseIndex] = useState<number | null>(null);
```

Replace the note-list render (the `puzzle.notes.map` section) — change:

```tsx
              {puzzle.notes.map((note, i) => (
                <Group key={`${i}-${note}`} justify="space-between" wrap="nowrap" align="flex-start">
                  <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                    {note}
                  </Text>
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    onClick={() => erase.mutate({ puzzleId: puzzle.id, index: i + 1 })}
                    loading={erase.isPending}
                    aria-label="Delete note"
                  >
                    <IconTrash size={14} />
                  </ActionIcon>
                </Group>
              ))}
```

to:

```tsx
              {puzzle.notes.map((note, i) => (
                <Group key={`${i}-${note}`} justify="space-between" wrap="nowrap" align="flex-start">
                  <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                    {note}
                  </Text>
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    onClick={() => setEraseIndex(i + 1)}
                    aria-label="Delete note"
                  >
                    <IconTrash size={14} />
                  </ActionIcon>
                </Group>
              ))}
```

Then add the `ConfirmModal` just before the closing `</Stack>` of the drawer's content (before the existing `<SolveModal .../>` line):

```tsx
          <ConfirmModal
            opened={eraseIndex !== null}
            onClose={() => setEraseIndex(null)}
            onConfirm={() => {
              if (eraseIndex !== null && puzzle) {
                erase.mutate({ puzzleId: puzzle.id, index: eraseIndex });
              }
              setEraseIndex(null);
            }}
            title="Delete note"
            message="Delete this note?"
            confirmLabel="Delete"
            confirmColor="red"
          />
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /home/russ/projects/puzzbot/.claude/worktrees/working-1/frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PuzzleDrawer.tsx
git commit -m "feat(ui): add confirm modal to note delete in PuzzleDrawer"
```

---

### Verification

- [ ] **Run the full backend test suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; cat /tmp/test.log
```

Expected: all pass, no regressions.

- [ ] **Build the frontend**

```bash
cd /home/russ/projects/puzzbot/.claude/worktrees/working-1/frontend && npm run build 2>&1 | tail -20
```

Expected: successful build, no TypeScript errors.
