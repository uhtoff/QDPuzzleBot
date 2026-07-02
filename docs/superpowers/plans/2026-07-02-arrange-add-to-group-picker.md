# Arrange "Add to group" Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat, unsearchable "Add to group" list in the Arrange board's card kebab with a single "Add to group…" entry that opens a searchable, scroll-bounded picker (Modal on desktop, bottom-sheet Drawer on touch).

**Architecture:** A new self-contained `AddToGroupPicker` component holds a search field plus a bounded scrollable list of group rows, rendered inside a responsive shell (`Modal` desktop / `Drawer` mobile, chosen via `useMediaQuery`). `PuzzleCard` in `DragBoard.tsx` swaps its inline `addable.map(...)` markup for a single trigger `Menu.Item` that opens the picker via local state. No data flow, mutation, type, or backend changes — the picker receives the already-computed `addable` list and the same `onRetag` callback.

**Tech Stack:** React 18, TypeScript, Mantine v7 (`@mantine/core`, `@mantine/hooks`), Vite. No frontend test runner exists in this project (scripts are only `dev`/`build`/`preview`).

---

## Testing note (read first)

This frontend has **no test harness** (no vitest / testing-library / `*.test.tsx`). Adding one is out of scope for this narrow UI change (YAGNI). Verification for every task is therefore:

- **Typecheck + compile:** `cd frontend && npm run build` (this runs `tsc --noEmit && vite build`). Expected: exits 0, no TypeScript errors.
- **Manual check in the app:** `cd frontend && npm run dev`, open the dashboard, go to a hunt's **Arrange** tab (requires `can_manage`), and exercise the card ⋮ menu.

Wrap any long-running command in a timeout and write output to a file, e.g.
`cd frontend && timeout 180 npm run build > /tmp/fe-build.log 2>&1` then read the log.

## File structure

- **Create** `frontend/src/components/AddToGroupPicker.tsx` — the responsive searchable picker. One responsibility: given a list of addable groups (plus the self-target flags) and an `onRetag` callback, present a searchable list and fire the correct retag on selection.
- **Modify** `frontend/src/components/DragBoard.tsx` — `PuzzleCard`: replace the inline add-list markup (current lines ~156–180) with a single trigger `Menu.Item` + local `addOpen` state, and render `<AddToGroupPicker>` alongside the existing `ConfirmModal`.

---

## Task 1: Create the `AddToGroupPicker` component

**Files:**
- Create: `frontend/src/components/AddToGroupPicker.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/components/AddToGroupPicker.tsx` with exactly this content:

```tsx
import { Button, Drawer, Modal, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { useState } from "react";
import type { EdgeType, RetagMode } from "../api";

interface GroupOption {
  code: string;
  name: string;
}

interface AddToGroupPickerProps {
  opened: boolean;
  onClose: () => void;
  puzzleId: number;
  /** Groups this puzzle can be added to — the already-computed `addable` list. */
  items: GroupOption[];
  /** group_code of the group this puzzle is the target of, if any. */
  ownTargetCode: string | null;
  selfFeeds: boolean;
  selfInforms: boolean;
  onRetag: (puzzleId: number, groupCode: string, mode: RetagMode, edge?: EdgeType) => void;
}

// One selectable row: a display label plus the retag action it runs.
interface Row {
  key: string;
  label: string;
  run: () => void;
}

// Expand the addable groups into concrete rows. A group the puzzle is the
// target of expands into up to two self-edge rows (feeds / informs), mirroring
// the original kebab markup; every other group is a single plain "add" row.
function buildRows(
  items: GroupOption[],
  puzzleId: number,
  ownTargetCode: string | null,
  selfFeeds: boolean,
  selfInforms: boolean,
  onRetag: AddToGroupPickerProps["onRetag"],
): Row[] {
  const rows: Row[] = [];
  for (const m of items) {
    if (m.code === ownTargetCode) {
      if (!selfFeeds) {
        rows.push({
          key: `${m.code}-feeds`,
          label: `${m.name} (feeds itself)`,
          run: () => onRetag(puzzleId, m.code, "add", "feeds"),
        });
      }
      if (!selfInforms) {
        rows.push({
          key: `${m.code}-informs`,
          label: `${m.name} (informs itself)`,
          run: () => onRetag(puzzleId, m.code, "add", "informs"),
        });
      }
    } else {
      rows.push({
        key: m.code,
        label: m.name,
        run: () => onRetag(puzzleId, m.code, "add"),
      });
    }
  }
  return rows;
}

export function AddToGroupPicker({
  opened,
  onClose,
  puzzleId,
  items,
  ownTargetCode,
  selfFeeds,
  selfInforms,
  onRetag,
}: AddToGroupPickerProps) {
  const isMobile = useMediaQuery("(max-width: 48em)");
  const [query, setQuery] = useState("");

  const rows = buildRows(items, puzzleId, ownTargetCode, selfFeeds, selfInforms, onRetag);
  const needle = query.trim().toLowerCase();
  const filtered = needle ? rows.filter((r) => r.label.toLowerCase().includes(needle)) : rows;

  const close = () => {
    setQuery("");
    onClose();
  };

  const choose = (r: Row) => {
    r.run();
    close();
  };

  const body = (
    <Stack gap="xs">
      <TextInput
        placeholder="Search groups…"
        value={query}
        onChange={(e) => setQuery(e.currentTarget.value)}
        autoFocus
        data-autofocus
      />
      <ScrollArea.Autosize mah={260}>
        <Stack gap={2}>
          {filtered.length === 0 ? (
            <Text size="sm" c="dimmed" p="xs">
              No groups match
            </Text>
          ) : (
            filtered.map((r) => (
              <Button
                key={r.key}
                variant="subtle"
                color="gray"
                fullWidth
                justify="flex-start"
                onClick={() => choose(r)}
              >
                {r.label}
              </Button>
            ))
          )}
        </Stack>
      </ScrollArea.Autosize>
    </Stack>
  );

  if (isMobile) {
    return (
      <Drawer opened={opened} onClose={close} position="bottom" title="Add to group" size="70%">
        {body}
      </Drawer>
    );
  }
  return (
    <Modal opened={opened} onClose={close} title="Add to group" size="sm">
      {body}
    </Modal>
  );
}
```

- [ ] **Step 2: Typecheck / compile**

Run: `cd frontend && timeout 180 npm run build > /tmp/fe-build.log 2>&1`
Expected: exit 0. (The component is not yet imported anywhere, so this only proves it compiles in isolation.) If it fails, read `/tmp/fe-build.log` and fix.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/AddToGroupPicker.tsx
git commit -m "feat(arrange): add searchable AddToGroupPicker component"
```

---

## Task 2: Wire the picker into `PuzzleCard`

**Files:**
- Modify: `frontend/src/components/DragBoard.tsx`

- [ ] **Step 1: Import the picker**

In `frontend/src/components/DragBoard.tsx`, add this import next to the other local component imports (near the existing `import { ConfirmModal } from "./ConfirmModal";` around line 47):

```tsx
import { AddToGroupPicker } from "./AddToGroupPicker";
```

- [ ] **Step 2: Add open-state to `PuzzleCard`**

In `PuzzleCard`, just after the existing `pendingAction` state declaration (around line 83):

```tsx
  const [pendingAction, setPendingAction] = useState<{ title: string; message: string; onConfirm: () => void } | null>(null);
  // Controls the "Add to group" searchable picker overlay.
  const [addOpen, setAddOpen] = useState(false);
```

(The `pendingAction` line already exists — add only the two new lines below it.)

- [ ] **Step 3: Replace the inline add-to-group markup with a single trigger**

Find this block in the `Menu.Dropdown` (currently lines ~156–180):

```tsx
            <Menu.Label>Add to group</Menu.Label>
            {addable.length === 0 ? (
              <Menu.Item disabled>Already in all groups</Menu.Item>
            ) : (
              addable.map((m) =>
                m.code === ownTargetCode ? (
                  <>
                    {!selfFeeds && (
                      <Menu.Item key={`${m.code}-feeds`} onClick={() => onRetag(puzzle.id, m.code, "add", "feeds")}>
                        {m.name} (feeds itself)
                      </Menu.Item>
                    )}
                    {!selfInforms && (
                      <Menu.Item key={`${m.code}-informs`} onClick={() => onRetag(puzzle.id, m.code, "add", "informs")}>
                        {m.name} (informs itself)
                      </Menu.Item>
                    )}
                  </>
                ) : (
                  <Menu.Item key={m.code} onClick={() => onRetag(puzzle.id, m.code, "add")}>
                    {m.name}
                  </Menu.Item>
                )
              )
            )}
```

Replace the entire block above with:

```tsx
            {addable.length === 0 ? (
              <Menu.Item disabled>Already in all groups</Menu.Item>
            ) : (
              <Menu.Item onClick={() => setAddOpen(true)}>Add to group…</Menu.Item>
            )}
```

(Note: the standalone `<Menu.Label>Add to group</Menu.Label>` is removed — it is redundant now that the single item is self-describing. Leave everything after this block — the `{canRemove && ...}` divider and all convert/minimeta/delete items — unchanged.)

- [ ] **Step 4: Render the picker alongside `ConfirmModal`**

Find the existing `ConfirmModal` render near the end of `PuzzleCard`'s returned fragment (around lines 319–332):

```tsx
    {pendingAction && (
      <ConfirmModal
        opened={pendingAction != null}
        onClose={() => setPendingAction(null)}
        onConfirm={() => {
          pendingAction.onConfirm();
          setPendingAction(null);
        }}
        title={pendingAction.title}
        message={pendingAction.message}
        confirmLabel="Confirm"
        confirmColor="grape"
      />
    )}
```

Immediately **before** that `{pendingAction && ...}` block, insert:

```tsx
    {addable.length > 0 && (
      <AddToGroupPicker
        opened={addOpen}
        onClose={() => setAddOpen(false)}
        puzzleId={puzzle.id}
        items={addable}
        ownTargetCode={ownTargetCode}
        selfFeeds={selfFeeds}
        selfInforms={selfInforms}
        onRetag={onRetag}
      />
    )}
```

- [ ] **Step 5: Typecheck / compile**

Run: `cd frontend && timeout 180 npm run build > /tmp/fe-build.log 2>&1`
Expected: exit 0, no TypeScript errors. If it fails, read `/tmp/fe-build.log` and fix.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/DragBoard.tsx
git commit -m "feat(arrange): open searchable picker from card Add-to-group menu"
```

---

## Task 3: Manual verification in the running app

**Files:** none (verification only — no commit unless a fix is needed).

- [ ] **Step 1: Start the dev server**

Run: `cd frontend && npm run dev` (leave running; open the printed local URL). Log in / view a hunt where you `can_manage`, and switch to the **Arrange** tab.

- [ ] **Step 2: Verify the desktop picker (wide window)**

Check each:
- A card's ⋮ menu now shows a single **"Add to group…"** entry (no long inline list), and the Remove/convert/minimeta/delete actions are all visible and not pushed off-screen.
- Clicking "Add to group…" opens a centred **Modal** titled "Add to group" with a focused search field.
- Typing filters the list case-insensitively; clearing the field restores the full list.
- Clicking a group adds the puzzle to it (the card's group membership updates as before) and the picker closes.
- Typing text that matches nothing shows **"No groups match"**.

- [ ] **Step 3: Verify the empty state**

Find (or arrange) a puzzle already in every group. Its ⋮ menu shows a disabled **"Already in all groups"** row and **no** "Add to group…" entry.

- [ ] **Step 4: Verify the self-target rows**

On a meta/target puzzle that does not yet self-feed/self-inform, open the picker and confirm it lists **"<name> (feeds itself)"** and/or **"<name> (informs itself)"** rows, and selecting one applies the self-edge (same result as the old menu items).

- [ ] **Step 5: Verify the touch bottom sheet**

Narrow the browser window (or use device-emulation ≤ 48em / ~768px). Re-open "Add to group…" and confirm it now appears as a **bottom-sheet Drawer** with full-width rows and a working search field.

- [ ] **Step 6: Record the result**

If everything passes, note it in `.ai/active/session-summary.md` (or the PR description). If a step fails, fix the relevant component, re-run `npm run build`, and re-verify before considering the task done.

---

## Self-review checklist (already applied)

- **Spec coverage:** short kebab (Task 2 Step 3) ✓; searchable picker (Task 1) ✓; Modal desktop / Drawer mobile (Task 1) ✓; empty state stays in kebab (Task 2 Step 3 + guard in Step 4) ✓; self-target rows preserved (Task 1 `buildRows`) ✓; no-match state (Task 1 body) ✓; no data/API/type change (picker takes `addable` + `onRetag`) ✓.
- **Type consistency:** `onRetag` signature matches `CardProps.onRetag` in `DragBoard.tsx`; `GroupOption {code,name}` is structurally identical to `DragBoard`'s `MetaOption`, so passing `addable` type-checks; `EdgeType`/`RetagMode` imported from `../api` as in `DragBoard.tsx`.
- **Placeholders:** none — every step has full code or an exact command.

## Rollback / recovery notes

Both tasks are isolated commits. To revert, `git revert` the two commits (or drop `AddToGroupPicker.tsx` and restore the original `addable.map(...)` block in `DragBoard.tsx`). No migrations, schema, or API changes are involved.
