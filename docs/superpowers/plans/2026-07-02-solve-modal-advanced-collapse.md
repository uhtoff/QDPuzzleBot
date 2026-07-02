# Solve Modal Advanced-Collapse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `SolveModal` so the primary action sits at the top and secondary actions hide behind an "Advanced" collapse, and make the modal's solved branch reachable via the Unsolve control (adding an inline unsolve action and dropping the separate confirm dialog).

**Architecture:** Rework the single `SolveModal` component in `PuzzleEditCells.tsx` (both `solved`/`unsolved` branches gain the shared Hunt-modal "Advanced" collapse; the solved branch gains a red "Mark unsolved" primary button backed by `useUnsolvePuzzle`). Then reroute the two table entry points' `onUnsolve` to open the same modal and delete the now-unused `useUnsolveConfirm` hook. No API/mutation/type changes.

**Tech Stack:** React 18, TypeScript, Mantine v7 (`@mantine/core` `Collapse`/`Divider`, `@tabler/icons-react` chevrons), Vite. No frontend test harness (scripts: `dev`/`build`/`preview`).

---

## Testing note (read first)

This frontend has **no test harness** (no vitest/testing-library). Adding one is out of scope. Verification for every task is:

- **Typecheck + compile:** `cd frontend && timeout 180 npm run build > /tmp/fe-build.log 2>&1` (runs `tsc --noEmit && vite build`). Expected exit 0. Read the log on failure.
- **Manual check:** `cd frontend && npm run dev`, open a hunt where you `can_manage`, and exercise the Solve/Unsolve controls in the puzzle table and board.

Keep test/build commands as a single statement wrapped in `timeout`.

## File structure

- **Modify** `frontend/src/components/PuzzleEditCells.tsx` — `SolveModal`: new imports, an `advancedOpen` state, an inline unsolve action, and a restructured render for both branches. (Task 1)
- **Modify** `frontend/src/components/PuzzleTable.tsx` — reroute `onUnsolve` to open the modal; drop `useUnsolveConfirm`. (Task 2)
- **Modify** `frontend/src/components/BoardGroup.tsx` — same reroute; drop `useUnsolveConfirm`. (Task 2)
- **Delete** `frontend/src/components/useUnsolveConfirm.tsx` — no remaining consumers. (Task 2)

---

## Task 1: Restructure `SolveModal`

**Files:**
- Modify: `frontend/src/components/PuzzleEditCells.tsx`

- [ ] **Step 1: Update the imports**

At the top of `frontend/src/components/PuzzleEditCells.tsx`:

Change the `@mantine/core` import block (currently lines 2–12) to add `Collapse` and `Divider` (keep the rest, alphabetical):

```tsx
import {
  Badge,
  Button,
  Collapse,
  Divider,
  Group,
  Menu,
  Modal,
  Popover,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
```

Add a new import line for the chevron icons (place it just after the `@mantine/notifications` import, line 14):

```tsx
import { IconChevronDown, IconChevronUp } from "@tabler/icons-react";
```

Change the `../api` import (currently line 16) to also import `useUnsolvePuzzle`:

```tsx
import { useSolvePuzzle, useUnsolvePuzzle, type SolveMode } from "../api";
```

- [ ] **Step 2: Replace the entire `SolveModal` function**

Replace the whole `SolveModal` function (currently lines 118–287, from `export function SolveModal(` through its closing `}`) with exactly this. The `submit`, `handleKey`, and `markComplete` bodies are unchanged from the current file — only the state, the new `unsolvePuzzle`, the `useEffect`, and the render change.

```tsx
export function SolveModal({ puzzle, uid, onClose }: { puzzle: Puzzle | null; uid: string; onClose: () => void }) {
  const solve = useSolvePuzzle(uid);
  const unsolve = useUnsolvePuzzle(uid);
  // One input per action button.
  const [partialText, setPartialText] = useState("");
  const [fullText, setFullText] = useState("");
  const [changeText, setChangeText] = useState("");
  const [addText, setAddText] = useState("");
  // Secondary actions live behind an "Advanced" collapse (mirrors NewHuntModal).
  const [advancedOpen, setAdvancedOpen] = useState(false);

  function submit(mode: SolveMode) {
    if (!puzzle) return;
    const value =
      mode === "partial" ? partialText : mode === "full" ? fullText : mode === "change" ? changeText : addText;

    // Guards mirror cog behaviour.
    if (mode !== "partial" && !value.trim()) return; // optional for partial only
    if ((mode === "full" || mode === "partial") && puzzle.solved) return; // already solved
    if ((mode === "change" || mode === "add") && !puzzle.solved) return; // not yet solved

    solve.mutate(
      { puzzleId: puzzle.id, solution: value.trim(), mode },
      {
        onSuccess: (data) => {
          const msg =
            mode === "full"
              ? `Marked \"${puzzle.name}\" solved`
              : mode === "partial"
                ? `Marked \"${puzzle.name}\" partially solved`
                : mode === "change"
                  ? `Changed solution for \"${puzzle.name}\"`
                  : `Added solution to \"${puzzle.name}\"`;
          notifications.show({ message: msg, color: mode === "partial" ? "blue" : "green" });
          showSheetWarning(data);
          setPartialText("");
          setFullText("");
          setChangeText("");
          setAddText("");
          onClose();
        },
        onError: notifyError,
      },
    );
  }

  function handleKey(e: React.KeyboardEvent, mode: SolveMode) {
    if (e.key === "Enter") submit(mode);
  }

  // Solve a puzzle that has no text answer (interactions, etc.): record a tick as
  // the solution. Mirrors the bot's !mark_as_complete (full solve with "✅").
  function markComplete() {
    if (!puzzle || puzzle.solved) return;
    solve.mutate(
      { puzzleId: puzzle.id, solution: "✅", mode: "full" },
      {
        onSuccess: (data) => {
          notifications.show({ message: `Marked \"${puzzle.name}\" solved`, color: "green" });
          showSheetWarning(data);
          onClose();
        },
        onError: notifyError,
      },
    );
  }

  // Clear the solution and move the channel back to unsolved. Replaces the old
  // useUnsolveConfirm dialog — opening this modal is the deliberate step.
  function unsolvePuzzle() {
    if (!puzzle) return;
    unsolve.mutate({ puzzleId: puzzle.id }, { onError: notifyError });
    onClose();
  }

  // When the puzzle changes: prefill change with current solution, collapse Advanced.
  useEffect(() => {
    if (puzzle?.solution && puzzle.solved) {
      setChangeText(puzzle.solution);
    }
    setAdvancedOpen(false);
  }, [puzzle?.id]);

  const isSolved = puzzle?.solved ?? false;

  const advancedToggle = (
    <>
      <Divider />
      <Group onClick={() => setAdvancedOpen((o) => !o)} style={{ cursor: "pointer" }}>
        {advancedOpen ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
        <Text size="sm" fw={600}>
          Advanced
        </Text>
      </Group>
    </>
  );

  return (
    <Modal
      opened={puzzle !== null}
      onClose={onClose}
      title={puzzle ? (isSolved ? puzzle.name : `Solve — ${puzzle.name}`) : "Solve"}
      centered
    >
      {puzzle && (
        <Stack>
          {isSolved && (
            <Text size="sm" c="dimmed">
              Current solution: {puzzle.solution}
            </Text>
          )}

          {!isSolved ? (
            /* --- unsolved: Answer/Solve primary, rest behind Advanced --- */
            <Stack gap="sm">
              <TextInput
                label="Answer"
                description="Posts the SOLVED message to the channel and archives the sheet."
                value={fullText}
                onChange={(e) => setFullText(e.currentTarget.value)}
                onKeyDown={(e) => handleKey(e, "full")}
              />
              <Button color="green" onClick={() => submit("full")} loading={solve.isPending} disabled={!fullText.trim()}>
                Solve
              </Button>

              {advancedToggle}
              <Collapse in={advancedOpen}>
                <Stack gap="sm" pb="sm">
                  <Button color="green" variant="outline" onClick={markComplete} loading={solve.isPending}>
                    Mark complete (no answer)
                  </Button>

                  <TextInput
                    label="Partial solution (optional)"
                    description="Records a partial answer without fully solving the puzzle."
                    value={partialText}
                    onChange={(e) => setPartialText(e.currentTarget.value)}
                    onKeyDown={(e) => handleKey(e, "partial")}
                  />
                  <Button color="blue" variant="light" onClick={() => submit("partial")} loading={solve.isPending}>
                    Mark partial
                  </Button>
                </Stack>
              </Collapse>
            </Stack>
          ) : (
            /* --- solved: Mark unsolved primary, change/add behind Advanced --- */
            <Stack gap="sm">
              <Button color="red" onClick={unsolvePuzzle} loading={unsolve.isPending}>
                Mark unsolved
              </Button>
              <Text size="sm" c="dimmed">
                Clears the solution and moves the channel back to unsolved. You can re-solve it later.
              </Text>

              {advancedToggle}
              <Collapse in={advancedOpen}>
                <Stack gap="sm" pb="sm">
                  <TextInput
                    label="Change solution"
                    description="Replaces the current solution."
                    value={changeText}
                    onChange={(e) => setChangeText(e.currentTarget.value)}
                    onKeyDown={(e) => handleKey(e, "change")}
                  />
                  <Button
                    color="orange"
                    variant="light"
                    onClick={() => submit("change")}
                    loading={solve.isPending}
                    disabled={!changeText.trim()}
                  >
                    Change solution
                  </Button>

                  <TextInput
                    label="Add solution"
                    description="Appends to the existing solution."
                    value={addText}
                    onChange={(e) => setAddText(e.currentTarget.value)}
                    onKeyDown={(e) => handleKey(e, "add")}
                  />
                  <Button
                    color="teal"
                    variant="light"
                    onClick={() => submit("add")}
                    loading={solve.isPending}
                    disabled={!addText.trim()}
                  >
                    Add solution
                  </Button>
                </Stack>
              </Collapse>
            </Stack>
          )}
        </Stack>
      )}
    </Modal>
  );
}
```

- [ ] **Step 3: Typecheck / compile**

Run: `cd frontend && timeout 180 npm run build > /tmp/fe-build.log 2>&1` then read `/tmp/fe-build.log`. Expected: exit 0, no TypeScript errors. If it fails, read the log and fix. (Note: `useUnsolvePuzzle` is exported from `frontend/src/api.ts` at line 289 — the import will resolve.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/PuzzleEditCells.tsx
git commit -m "feat(solve): primary action up top, secondary actions behind Advanced"
```

---

## Task 2: Reroute Unsolve to the modal and remove `useUnsolveConfirm`

**Files:**
- Modify: `frontend/src/components/PuzzleTable.tsx`
- Modify: `frontend/src/components/BoardGroup.tsx`
- Delete: `frontend/src/components/useUnsolveConfirm.tsx`

- [ ] **Step 1: Rewire `PuzzleTable.tsx`**

Make these four edits in `frontend/src/components/PuzzleTable.tsx`:

1. Delete the import line (line 23):
```tsx
import { useUnsolveConfirm } from "./useUnsolveConfirm";
```

2. Delete the hook call (line 178):
```tsx
  const { request: requestUnsolve, modal: unsolveModal } = useUnsolveConfirm(detail.hunt.uid);
```

3. Change the `onUnsolve` prop (line 390) from:
```tsx
                  onUnsolve={(p) => requestUnsolve({ id: p.id, name: p.name })}
```
to:
```tsx
                  onUnsolve={setSolvePuzzle}
```

4. Delete the `{unsolveModal}` render (line 412):
```tsx
      {unsolveModal}
```

(`setSolvePuzzle` and the `<SolveModal .../>` at line 411 already exist and are unchanged. `onSolve={setSolvePuzzle}` at line 389 confirms the setter has the right shape for `onUnsolve` too.)

- [ ] **Step 2: Rewire `BoardGroup.tsx`**

Make these edits in `frontend/src/components/BoardGroup.tsx`:

1. Delete the import line (line 31):
```tsx
import { useUnsolveConfirm } from "./useUnsolveConfirm";
```

2. Delete the hook call (line 72):
```tsx
  const { request: requestUnsolve, modal: unsolveModal } = useUnsolveConfirm(detail.hunt.uid);
```

3. Change BOTH `onUnsolve` props (lines 239 and 289) from:
```tsx
                        onUnsolve={(p) => requestUnsolve({ id: p.id, name: p.name })}
```
to:
```tsx
                        onUnsolve={setSolvePuzzle}
```

4. Delete the `{unsolveModal}` render (line 366):
```tsx
      {unsolveModal}
```

- [ ] **Step 3: Delete the now-unused hook**

```bash
git rm frontend/src/components/useUnsolveConfirm.tsx
```

- [ ] **Step 4: Verify nothing else imports it**

Run: `grep -rn "useUnsolveConfirm" frontend/src`
Expected: no output (no remaining references).

- [ ] **Step 5: Typecheck / compile**

Run: `cd frontend && timeout 180 npm run build > /tmp/fe-build.log 2>&1` then read `/tmp/fe-build.log`. Expected: exit 0, no TypeScript errors. A common failure here is an unused-import error if a reference was missed — read the log and fix.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/PuzzleTable.tsx frontend/src/components/BoardGroup.tsx frontend/src/components/useUnsolveConfirm.tsx
git commit -m "feat(solve): open Solve modal for unsolve; drop useUnsolveConfirm"
```

---

## Task 3: Manual verification

**Files:** none (verification only — no commit unless a fix is needed).

- [ ] **Step 1: Start the dev server** — `cd frontend && npm run dev`, open the dashboard, view a hunt where you `can_manage`.

- [ ] **Step 2: Unsolved puzzle** — click Solve (green check) on an unsolved puzzle. Confirm:
  - Top shows an **"Answer"** field + green **"Solve"** button (disabled until text entered).
  - A collapsed **"Advanced"** section (chevron + bold label) below a divider; expanding it reveals **"Mark complete (no answer)"** and the **"Partial solution (optional)"** field + **"Mark partial"**.
  - Entering an answer and clicking Solve (or pressing Enter in the field) solves the puzzle with the SOLVED notification, then closes.
  - "Mark complete (no answer)" and "Mark partial" behave as before.

- [ ] **Step 3: Solved puzzle** — click Unsolve (red X) on a solved puzzle. Confirm:
  - No confirm dialog — the modal opens directly, titled with the puzzle name, showing "Current solution: …".
  - A red **"Mark unsolved"** primary button with the explanatory copy beneath it.
  - A collapsed **"Advanced"** section containing **"Change solution"** (prefilled) + **"Add solution"**.
  - Clicking **"Mark unsolved"** unsolves the puzzle and closes the modal.
  - Change/Add solution still work.

- [ ] **Step 4: Advanced resets** — open the modal, expand Advanced, close it, reopen on another puzzle — Advanced is collapsed again.

- [ ] **Step 5: Record the result** — note pass/fail in the PR description or `.ai/active`. Fix and rebuild if any step fails.

---

## Self-review checklist (already applied)

- **Spec coverage:** Advanced pattern both branches (Task 1 Step 2) ✓; unsolved Answer/Solve top + Advanced (Task 1) ✓; solved Mark-unsolved top + copy + Advanced change/add (Task 1) ✓; title branch (Task 1 render) ✓; `advancedOpen` reset on open (Task 1 useEffect) ✓; reroute onUnsolve → modal + remove hook + delete file (Task 2) ✓; no API/mutation/type change (reuses `useSolvePuzzle`/`useUnsolvePuzzle`) ✓.
- **Type consistency:** `onUnsolve={setSolvePuzzle}` matches the existing `onSolve={setSolvePuzzle}` binding (`PuzzleRowActions` props are `(puzzle: Puzzle) => void`; `setSolvePuzzle` accepts `Puzzle`). `useUnsolvePuzzle(uid)` returns a mutation with `.mutate({ puzzleId })` and `.isPending`, matching the old hook's usage.
- **Placeholders:** none.

## Rollback / recovery notes

Two isolated commits plus one file deletion. To revert: `git revert` both commits (the deletion of `useUnsolveConfirm.tsx` is restored by reverting Task 2's commit). No migrations, schema, or API changes.
