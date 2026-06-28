# Member Create Puzzle/Group and Web Unsolve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow any guild member to create puzzles/groups from the web dashboard (matching bang command parity), and add an unsolve button on solved puzzle rows.

**Architecture:** Two independent changes. (1) Drop the organiser gate from the `create_puzzle` and `create_meta` web API routes and widen the frontend button visibility condition. (2) Add an `onUnsolve` prop to `PuzzleRowActions` and render an unsolve `ActionIcon` on solved rows, wired through `PuzzleTable` via the already-existing `useUnsolvePuzzle` hook.

**Tech Stack:** Python/Quart (backend), React/TypeScript/Mantine (frontend), pytest + asyncio (tests)

---

## File Map

| File | Change |
|------|--------|
| `bot/web/actions.py` | Replace `_organiser_hunt` with `_visible_hunt_by_uid` in `create_puzzle` and `create_meta` |
| `tests/test_member_create_web.py` | New test file: verify member can hit create endpoints |
| `frontend/src/components/PuzzleRowActions.tsx` | Add `onUnsolve` prop; render unsolve button on solved rows |
| `frontend/src/components/PuzzleTable.tsx` | Wire `useUnsolvePuzzle`; pass `onUnsolve` to `PuzzleRowActions` |
| `frontend/src/pages/HuntBoard.tsx` | Show `CreateControls` when `!data.hunt.archived` instead of `can_manage` |

---

### Task 1: Drop organiser gate from create_puzzle and create_meta (backend)

**Files:**
- Modify: `bot/web/actions.py` (lines ~1449–1500)

The `create_puzzle` and `create_meta` routes both call `_organiser_hunt(uid)` which requires organiser role. Replace with `_visible_hunt_by_uid(uid)` — the same membership check used by solve, note, link, and status routes. Add the archived guard that `_organiser_hunt` previously provided.

- [ ] **Step 1: Write the failing test**

Create `tests/test_member_create_web.py`:

```python
"""Member-accessible create endpoints — any guild member can create puzzles/groups.

These tests verify the organiser gate is NOT present on create_puzzle and create_meta.
Pattern mirrors tests/test_edge_web.py: minimal Quart app, patched auth helpers.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_puzzle, make_round

_ASYNC_SERVICE_METHODS = [
    "create_puzzle", "create_tag_group", "refresh_unassigned_safe", "set_source_mode",
]


def _build_cog():
    cog = MagicMock()
    svc = cog.service
    for name in _ASYNC_SERVICE_METHODS:
        setattr(svc, name, AsyncMock())
    puzzle = make_puzzle(id=9, channel_id=100)
    svc.create_puzzle = AsyncMock(return_value=puzzle)
    svc.create_tag_group = AsyncMock(
        return_value=(make_round(id=5, group_code="R1"), puzzle)
    )
    return cog, puzzle


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"
    cog, puzzle = _build_cog()
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        # _visible_hunt_by_uid is patched (NOT _organiser_hunt) — member-level only
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb"),
        patch("bot.store.PuzzleJsonDb"),
        patch("bot.store.TagGroupJsonDb"),
    ]
    started = [p.start() for p in patches]
    _hdb, _pdb, _rdb = started[3], started[4], started[5]
    _pdb.get_by_attr.return_value = puzzle
    _pdb.check_duplicates_in_hunt.return_value = False
    _rdb.get_by_attr.return_value = None
    _hdb.get_by_attr.return_value = MagicMock()

    try:
        yield SimpleNamespace(
            app=app, client=app.test_client(), cog=cog, svc=cog.service,
            puzzle=puzzle,
        )
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_member_can_create_puzzle(web):
    """Any member (no organiser role) can POST /api/hunts/<uid>/puzzles."""
    resp = await web.client.post("/api/hunts/U1/puzzles", json={"name": "My Puzzle"})
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_member_can_create_group(web):
    """Any member (no organiser role) can POST /api/hunts/<uid>/metas."""
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "My Round"})
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["ok"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
timeout 60 pytest tests/test_member_create_web.py -v > /tmp/test-out.log 2>&1; cat /tmp/test-out.log
```

Expected: FAIL — the routes currently call `_organiser_hunt`, which is NOT patched here, so the requests will fail or 403.

- [ ] **Step 3: Update create_puzzle to use _visible_hunt_by_uid**

In `bot/web/actions.py`, find `create_puzzle` (around line 1449). Change the first two lines of the function body from:

```python
    ctx, err = await _organiser_hunt(uid)
    if err:
        return err
```

to:

```python
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
```

- [ ] **Step 4: Update create_meta to use _visible_hunt_by_uid**

In `bot/web/actions.py`, find `create_meta` (around line 1498). Change the first two lines of the function body from:

```python
    ctx, err = await _organiser_hunt(uid)
    if err:
        return err
```

to:

```python
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
timeout 60 pytest tests/test_member_create_web.py -v > /tmp/test-out.log 2>&1; cat /tmp/test-out.log
```

Expected: PASS both tests.

- [ ] **Step 6: Run the broader test suite to check for regressions**

```bash
timeout 120 pytest tests/ --ignore=tests/test_gsheet_concurrency.py -q > /tmp/test-out.log 2>&1; tail -20 /tmp/test-out.log
```

Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add bot/web/actions.py tests/test_member_create_web.py
git commit -m "Drop organiser gate from create_puzzle and create_meta web routes"
```

---

### Task 2: Show CreateControls to all members (frontend)

**Files:**
- Modify: `frontend/src/pages/HuntBoard.tsx` (line ~119)

The `CreateControls` component (New puzzle / New Group buttons) is currently only rendered when `data.hunt.can_manage` is true (organiser role). Change the condition to `!data.hunt.archived` so any logged-in user viewing a live hunt sees the buttons. The backend enforces the real permission check.

- [ ] **Step 1: Update the visibility condition in HuntBoard.tsx**

In `frontend/src/pages/HuntBoard.tsx`, find this line (around line 119):

```tsx
        {data.hunt.can_manage && <CreateControls detail={data} uid={uid!} />}
```

Replace it with:

```tsx
        {!data.hunt.archived && <CreateControls detail={data} uid={uid!} />}
```

- [ ] **Step 2: Build the frontend to verify no TypeScript errors**

```bash
cd /home/russ/projects/puzzbot/frontend && npm run build 2>&1 | tail -20
```

Expected: build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/HuntBoard.tsx
git commit -m "Show New Puzzle / New Group buttons to all hunt members"
```

---

### Task 3: Add unsolve button to PuzzleRowActions (frontend)

**Files:**
- Modify: `frontend/src/components/PuzzleRowActions.tsx`
- Modify: `frontend/src/components/PuzzleTable.tsx`

`PuzzleRowActions` currently shows the solve checkmark only when `editable && !p.solved`. When `editable && p.solved`, that slot is empty. Add an `onUnsolve` callback prop and render a red unsolve `ActionIcon` (`IconCircleX`) with tooltip "Mark unsolved" in that slot. Wire `useUnsolvePuzzle` in `PuzzleTable` and pass `onUnsolve` down.

- [ ] **Step 1: Update PuzzleRowActions to accept and render onUnsolve**

Replace the entire content of `frontend/src/components/PuzzleRowActions.tsx` with:

```tsx
import { ActionIcon, Badge, Group, Indicator, Table, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconCircleCheck, IconCircleX, IconNote } from "@tabler/icons-react";
import type { Puzzle } from "../types";

interface PuzzleRowActionsProps {
  puzzle: Puzzle;
  editable: boolean;
  onSolve: (puzzle: Puzzle) => void;
  onUnsolve: (puzzle: Puzzle) => void;
  onOpenNotes: (puzzle: Puzzle) => void;
  onToggleMinis: (id: number) => void;
  /** Width of the <Table.Td> wrapper. Defaults to undefined (auto). */
  tdWidth?: number;
}

/**
 * Shared row-actions cell for puzzle tables.
 *
 * Renders a single <Table.Td> containing the solve/unsolve button, notes indicator,
 * and (for Minimeta puzzles) a mini-expansion toggle.
 *
 * Place this inside the puzzle's <Table.Tr>. The Minimeta expansion row
 * (<MiniPuzzlePanel>) should be rendered as a sibling <Table.Tr> after the
 * main row using the `expandedMinis` set supplied by `useMiniExpansion`.
 */
export function PuzzleRowActions({
  puzzle: p,
  editable,
  onSolve,
  onUnsolve,
  onOpenNotes,
  onToggleMinis,
  tdWidth,
}: PuzzleRowActionsProps) {
  return (
    <Table.Td w={tdWidth}>
      <Group gap={4} wrap="nowrap">
        {editable && !p.solved && (
          <Tooltip label="Mark solved">
            <ActionIcon variant="subtle" color="green" onClick={() => onSolve(p)} aria-label="Solve">
              <IconCircleCheck size={17} />
            </ActionIcon>
          </Tooltip>
        )}
        {editable && p.solved && (
          <Tooltip label="Mark unsolved">
            <ActionIcon variant="subtle" color="red" onClick={() => onUnsolve(p)} aria-label="Unsolve">
              <IconCircleX size={17} />
            </ActionIcon>
          </Tooltip>
        )}
        <Indicator label={p.notes.length} size={16} disabled={p.notes.length === 0} color="blue">
          <ActionIcon variant="subtle" color="gray" onClick={() => onOpenNotes(p)} aria-label="Notes">
            <IconNote size={16} />
          </ActionIcon>
        </Indicator>
        {p.puzzle_type === "Minimeta" && (
          <Tooltip label={`${p.mini_puzzles.filter((m) => m.solved).length}/${p.mini_puzzles.length} minis solved`}>
            <ActionIcon
              size="sm"
              variant="subtle"
              onClick={() => onToggleMinis(p.id)}
              aria-label="Toggle minis"
            >
              <Badge color="violet" variant="light">
                {p.mini_puzzles.filter((m) => m.solved).length}/{p.mini_puzzles.length}
              </Badge>
            </ActionIcon>
          </Tooltip>
        )}
        {p.rollup_stale && (
          <Tooltip label={p.rollup_error || "This minimeta's sheet is out of date"} multiline maw={320}>
            <Badge color="red" variant="light" size="sm" leftSection={<IconAlertTriangle size={12} />} style={{ cursor: "default" }}>
              sheet stale
            </Badge>
          </Tooltip>
        )}
      </Group>
    </Table.Td>
  );
}
```

- [ ] **Step 2: Wire useUnsolvePuzzle in PuzzleTable and pass onUnsolve**

In `frontend/src/components/PuzzleTable.tsx`:

2a. Find the import line for api hooks (look for `useUpdatePuzzle`, `useRetagPuzzle`, etc.) and add `useUnsolvePuzzle` to it. For example, if the import reads:

```ts
import { useUpdatePuzzle, useRetagPuzzle, ... } from "../api";
```

Add `useUnsolvePuzzle` to that same import.

2b. In the `PuzzleTable` component body, alongside `const updateMut = useUpdatePuzzle(...)` and `const retag = useRetagPuzzle(...)`, add:

```ts
  const unsolveMut = useUnsolvePuzzle(detail.hunt.uid);
```

2c. Add a handler function (alongside the existing solve handler pattern) for triggering unsolve with error notification. Find where `setSolvePuzzle` is used as `onSolve` and add an `onUnsolve` handler. Look for `notifyError` and `notifySuccess` imports at the top of the file and use them:

```ts
  function handleUnsolve(puzzle: Puzzle) {
    unsolveMut.mutate(
      { puzzleId: puzzle.id },
      { onError: notifyError },
    );
  }
```

2d. Find the `<PuzzleRowActions` JSX (around line 383) and add `onUnsolve={handleUnsolve}`:

```tsx
                <PuzzleRowActions
                  puzzle={p}
                  editable={editable}
                  onSolve={setSolvePuzzle}
                  onUnsolve={handleUnsolve}
                  onOpenNotes={onOpenNotes}
                  onToggleMinis={toggleMinis}
                />
```

- [ ] **Step 3: Check what's imported at the top of PuzzleTable.tsx**

Before editing, verify which imports exist:

```bash
head -25 /home/russ/projects/puzzbot/frontend/src/components/PuzzleTable.tsx
```

Use exactly the import names already present in that file. If `notifyError` / `notifySuccess` are not imported, add them from `"../util"`. If `Puzzle` type is not imported, add it from `"../types"`.

- [ ] **Step 4: Build to verify no TypeScript errors**

```bash
cd /home/russ/projects/puzzbot/frontend && npm run build 2>&1 | tail -20
```

Expected: build succeeds with no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PuzzleRowActions.tsx frontend/src/components/PuzzleTable.tsx
git commit -m "Add unsolve button to solved puzzle rows in web dashboard"
```

---

## Verification

After all tasks are complete:

- [ ] Run the full backend test suite:
  ```bash
  timeout 120 pytest tests/ --ignore=tests/test_gsheet_concurrency.py -q > /tmp/test-out.log 2>&1; tail -20 /tmp/test-out.log
  ```
- [ ] Confirm frontend builds clean:
  ```bash
  cd /home/russ/projects/puzzbot/frontend && npm run build 2>&1 | tail -10
  ```
- [ ] Manual smoke test (if dev server is accessible):
  - Log in as a non-organiser member — confirm "New puzzle" and "New Group" buttons appear on a live hunt
  - Confirm buttons are absent on an archived hunt
  - Confirm a solved puzzle row shows a red X; clicking it unsolves it
  - Confirm an unsolved puzzle row shows the green checkmark as before
