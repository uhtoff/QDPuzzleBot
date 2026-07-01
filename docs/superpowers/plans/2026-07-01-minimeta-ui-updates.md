# Mini-meta UI updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface a minimeta's attached-mini count on the toggle badge, give minimetas a MINIMETA badge + kind strip, and add a guarded "remove minimeta status" flow (web + bang) that tears down the minis and rollup — all gated on a new server-derived `is_minimeta` flag rather than the editable `puzzle_type`.

**Architecture:** A minimeta is identified by a derived boolean `is_minimeta = owns mini children OR puzzle_type == "Minimeta"`, computed server-side in `bot/web/data.py` (mirroring the existing `meta_kind` derivation) and consumed by the React frontend. The teardown service reuses existing seams: `delete_puzzle` per mini and `demote_tab_to_puzzle` (the inverse of the promote that a minimeta tab already underwent) to strip the rollup table.

**Tech Stack:** Python 3 / Quart (bot + web), pytest (mocked, `asyncio_mode=auto`); React + TypeScript + Mantine v7 + TanStack Query (frontend, typechecked via `tsc --noEmit`).

---

## Conventions for this plan

**Backend tests** (run from repo root):
```bash
timeout 120 pipenv run python -m pytest tests/<file>.py -q > /tmp/test.log 2>&1
```
A killed run exits 124; read `/tmp/test.log`. Full suite: `tests/` (target ~180s).

**Frontend verification** (no unit-test runner in this project — the check is the TypeScript build):
```bash
cd frontend && npm run build   # runs `tsc --noEmit && vite build`
```

**Factories:** `tests/conftest.py` exports `make_puzzle()`, `make_hunt()`, `make_cog()`,
`make_ctx()` — plain functions, not fixtures. Async tests need no decorator but existing
files mark them `@pytest.mark.asyncio`; match the file you edit.

---

## File Structure

- `bot/web/data.py` — add `derive_is_minimeta()` + call it in `get_hunt_puzzles` (U0).
- `frontend/src/types.ts` — add `is_minimeta` to `Puzzle` (U0).
- `frontend/src/components/{BoardGroup,PuzzleTable}.tsx` — swap mini-panel gates to `is_minimeta` (U0).
- `frontend/src/components/PuzzleRowActions.tsx` — swap gate + fix clipped count badge (U1).
- `frontend/src/components/MetaBadge.tsx` — MINIMETA badge (U2).
- `frontend/src/components/BoardGroup.tsx` — lilac kind strip for minimeta (U2).
- `bot/core/puzzle_service.py` — `remove_minimeta()` service seam (U3).
- `bot/web/actions.py` — `POST /puzzles/<id>/remove-minimeta` route (U3).
- `bot/cogs/puzzles.py` — `!remove_minimeta` bang command (U3).
- `frontend/src/api.ts` — `useRemoveMinimeta` hook (U3).
- `frontend/src/components/DragBoard.tsx` — Remove-minimeta menu item + typed-confirm modal (U3).
- Tests: `tests/test_is_minimeta.py` (new), `tests/test_minimeta_service.py`,
  `tests/test_board_push_service.py`, `tests/test_minimeta_commands.py`.

---

## Task 1: Derive `is_minimeta` server-side (U0 backend)

**Files:**
- Modify: `bot/web/data.py` (add function near `derive_meta_kind:297`; call it in `get_hunt_puzzles` after `derive_meta_kind(puzzle_list)` ~line 293)
- Test: `tests/test_is_minimeta.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_is_minimeta.py`:

```python
"""Unit tests for derive_is_minimeta — the derived flag that identifies a minimeta
by structure (owns mini children) or the legacy puzzle_type marker, so the UI never
gates on the user-editable puzzle_type string.
"""
from bot.web.data import derive_is_minimeta


def test_has_children_is_minimeta():
    p = {"id": 1, "mini_puzzles": [{"id": 2}], "puzzle_type": ""}
    derive_is_minimeta([p])
    assert p["is_minimeta"] is True


def test_type_flag_only_is_minimeta():
    # Freshly made minimeta: type set, no minis yet.
    p = {"id": 1, "mini_puzzles": [], "puzzle_type": "Minimeta"}
    derive_is_minimeta([p])
    assert p["is_minimeta"] is True


def test_no_children_no_flag_is_not_minimeta():
    p = {"id": 1, "mini_puzzles": [], "puzzle_type": "Cryptic"}
    derive_is_minimeta([p])
    assert p["is_minimeta"] is False


def test_children_override_editable_type():
    # Type edited away from Minimeta but minis still exist — still a minimeta.
    p = {"id": 1, "mini_puzzles": [{"id": 2}], "puzzle_type": "Cryptic"}
    derive_is_minimeta([p])
    assert p["is_minimeta"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_is_minimeta.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ImportError: cannot import name 'derive_is_minimeta'`.

- [ ] **Step 3: Add the function**

In `bot/web/data.py`, immediately after the `derive_meta_kind` function (after its
`return puzzles` around line 342), add:

```python
def derive_is_minimeta(puzzles):
    """Annotate each puzzle dict in-place with ``is_minimeta`` — derived, not stored.

    A puzzle is a minimeta if it owns mini-puzzle children (the authoritative
    structural signal) OR still carries the legacy ``puzzle_type == 'Minimeta'``
    marker (covers a freshly-made minimeta with no minis yet). Mirrors the derived
    ``meta_kind`` pattern so the UI never gates on the user-editable puzzle_type.
    """
    for p in puzzles:
        p["is_minimeta"] = bool(p.get("mini_puzzles")) or p.get("puzzle_type") == "Minimeta"
    return puzzles
```

- [ ] **Step 4: Wire it into `get_hunt_puzzles`**

In `bot/web/data.py`, find (around line 292-294):

```python
    puzzle_list = list(puzzles.values())
    derive_meta_kind(puzzle_list)
    return puzzle_list
```

Change to:

```python
    puzzle_list = list(puzzles.values())
    derive_meta_kind(puzzle_list)
    derive_is_minimeta(puzzle_list)
    return puzzle_list
```

- [ ] **Step 5: Run test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_is_minimeta.py -q > /tmp/test.log 2>&1`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add bot/web/data.py tests/test_is_minimeta.py
git commit -m "feat(web): derive is_minimeta flag for dashboard puzzles"
```

---

## Task 2: Add `is_minimeta` to the frontend type + swap panel gates (U0 frontend)

**Files:**
- Modify: `frontend/src/types.ts` (Puzzle interface, near `meta_kind` ~line 68)
- Modify: `frontend/src/components/BoardGroup.tsx:294`
- Modify: `frontend/src/components/PuzzleTable.tsx:395`

- [ ] **Step 1: Add the type field**

In `frontend/src/types.ts`, in the `Puzzle` interface just after the `meta_kind` line
(line 68), add:

```typescript
  is_minimeta: boolean;
```

- [ ] **Step 2: Swap the BoardGroup mini-panel gate**

In `frontend/src/components/BoardGroup.tsx` line 294, change:

```tsx
                {p.puzzle_type === "Minimeta" && expandedMinis.has(p.id) && (
```

to:

```tsx
                {p.is_minimeta && expandedMinis.has(p.id) && (
```

- [ ] **Step 3: Swap the PuzzleTable mini-panel gate**

In `frontend/src/components/PuzzleTable.tsx` line 395, change:

```tsx
              {p.puzzle_type === "Minimeta" && expandedMinis.has(p.id) && (
```

to:

```tsx
              {p.is_minimeta && expandedMinis.has(p.id) && (
```

- [ ] **Step 4: Typecheck/build**

Run: `cd frontend && npm run build`
Expected: build succeeds, no TypeScript errors. (`is_minimeta` is now a required field
on `Puzzle`; the backend already returns it from Task 1.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/BoardGroup.tsx frontend/src/components/PuzzleTable.tsx
git commit -m "feat(web): gate mini panels on derived is_minimeta"
```

---

## Task 3: Make the mini count visible + gate on is_minimeta (U1)

**Files:**
- Modify: `frontend/src/components/PuzzleRowActions.tsx:57-70`

**Why:** the `{solved}/{total}` count `<Badge>` is nested inside a fixed-size
`<ActionIcon size="sm">` (~26px square), which clips the text. Make the `<Badge>` itself
the clickable target, and gate on `is_minimeta`.

- [ ] **Step 1: Replace the minimeta toggle block**

In `frontend/src/components/PuzzleRowActions.tsx`, replace lines 57-70:

```tsx
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
```

with:

```tsx
        {p.is_minimeta && (
          <Tooltip label={`${p.mini_puzzles.filter((m) => m.solved).length}/${p.mini_puzzles.length} minis solved`}>
            <Badge
              color="violet"
              variant="light"
              onClick={() => onToggleMinis(p.id)}
              aria-label="Toggle minis"
              style={{ cursor: "pointer" }}
            >
              {p.mini_puzzles.filter((m) => m.solved).length}/{p.mini_puzzles.length}
            </Badge>
          </Tooltip>
        )}
```

(`ActionIcon` is still imported and used by the solve/unsolve/notes controls above, so
leave the import untouched.)

- [ ] **Step 2: Typecheck/build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PuzzleRowActions.tsx
git commit -m "fix(web): show mini count on the minimeta toggle badge"
```

---

## Task 4: MINIMETA badge + lilac kind strip (U2)

**Files:**
- Modify: `frontend/src/components/MetaBadge.tsx`
- Modify: `frontend/src/components/BoardGroup.tsx:39-48`

- [ ] **Step 1: Add the MINIMETA badge**

Replace the body of `frontend/src/components/MetaBadge.tsx` (the `KINDS` map and the
`MetaBadge` function) with:

```tsx
import { Badge } from "@mantine/core";
import type { Puzzle } from "../types";

// Single label per target puzzle, by precedence Runaround > Metameta > Meta >
// Capstone. meta_kind is derived server-side (bot/web/data.py derive_meta_kind).
// minimeta is a separate derived flag (derive_is_minimeta) — minimetas are not
// is_meta, so they never carry a meta_kind.
const KINDS: Record<string, { label: string; color: string }> = {
  runaround: { label: "RUNAROUND", color: "red" },
  metameta: { label: "METAMETA", color: "violet" },
  capstone: { label: "CAPSTONE", color: "orange" },
  meta: { label: "META", color: "grape" },
  minimeta: { label: "MINIMETA", color: "grape.4" },
};

export function MetaBadge({ puzzle }: { puzzle: Puzzle }) {
  if (!puzzle.is_meta) {
    if (puzzle.is_minimeta) {
      const kind = KINDS.minimeta;
      return (
        <Badge size="xs" color={kind.color}>
          {kind.label}
        </Badge>
      );
    }
    return null;
  }
  const kind = KINDS[puzzle.meta_kind ?? "meta"] ?? KINDS.meta;
  return (
    <Badge size="xs" color={kind.color}>
      {kind.label}
    </Badge>
  );
}
```

- [ ] **Step 2: Add the lilac kind strip for minimetas**

In `frontend/src/components/BoardGroup.tsx`, replace the `KIND_STRIP_COLOR` map and
`kindStrip` function (lines 39-48):

```tsx
const KIND_STRIP_COLOR: Record<string, string> = {
  runaround: "var(--mantine-color-red-6)",
  metameta: "var(--mantine-color-violet-6)",
  capstone: "var(--mantine-color-orange-6)",
  meta: "var(--mantine-color-grape-6)",
};

function kindStrip(p: { meta_kind?: string | null }): string | undefined {
  return p.meta_kind ? KIND_STRIP_COLOR[p.meta_kind] : undefined;
}
```

with:

```tsx
const KIND_STRIP_COLOR: Record<string, string> = {
  runaround: "var(--mantine-color-red-6)",
  metameta: "var(--mantine-color-violet-6)",
  capstone: "var(--mantine-color-orange-6)",
  meta: "var(--mantine-color-grape-6)",
  minimeta: "var(--mantine-color-grape-4)",
};

function kindStrip(p: { meta_kind?: string | null; is_minimeta?: boolean }): string | undefined {
  if (p.meta_kind) return KIND_STRIP_COLOR[p.meta_kind];
  if (p.is_minimeta) return KIND_STRIP_COLOR.minimeta;
  return undefined;
}
```

(The call site `kindStrip(p)` at line 213 already passes the full puzzle, which now
carries `is_minimeta` — no call-site change needed.)

- [ ] **Step 3: Typecheck/build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MetaBadge.tsx frontend/src/components/BoardGroup.tsx
git commit -m "feat(web): minimeta badge and lilac kind strip"
```

---

## Task 5: `remove_minimeta` service seam (U3 backend)

**Files:**
- Modify: `bot/core/puzzle_service.py` (add method after `make_minimeta` ~line 1317)
- Test: `tests/test_minimeta_service.py` (new `TestRemoveMinimeta` class)
- Test: `tests/test_board_push_service.py:20-28` (add `remove_minimeta` to `EXPECTED_PUSH_METHODS`)

- [ ] **Step 1: Write the failing service tests**

Append to `tests/test_minimeta_service.py` (imports at top already include
`AsyncMock, MagicMock, patch`, `pytest`, `make_puzzle`; add `make_hunt` to the existing
`from tests.conftest import ...` line if not already present):

```python
class TestRemoveMinimeta:
    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_deletes_minis_and_clears_type(self, PuzzleDb, RollupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        m1 = make_puzzle(id=2, parent_puzzle_id=1)
        m2 = make_puzzle(id=3, parent_puzzle_id=1)
        svc.get_minis = MagicMock(return_value=[m1, m2])
        svc.delete_puzzle = AsyncMock()
        svc._gsheet_cog = MagicMock(return_value=None)
        RollupDb.get_by_attr.return_value = None

        await svc.remove_minimeta(None, make_hunt(), minimeta)

        assert svc.delete_puzzle.await_count == 2
        assert minimeta.puzzle_type is None
        PuzzleDb.commit.assert_called_with(minimeta)

    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_raises_when_not_minimeta(self, PuzzleDb, RollupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        puzzle = make_puzzle(id=1, puzzle_type="Cryptic")
        svc.get_minis = MagicMock(return_value=[])
        with pytest.raises(RuntimeError, match="not_minimeta"):
            await svc.remove_minimeta(None, make_hunt(), puzzle)

    @patch("bot.core.puzzle_service.RollupsJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_deletes_rollup_row_when_present(self, PuzzleDb, RollupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        svc.get_minis = MagicMock(return_value=[])
        svc.delete_puzzle = AsyncMock()
        svc._gsheet_cog = MagicMock(return_value=None)
        rollup = MagicMock(id=7)
        RollupDb.get_by_attr.return_value = rollup

        await svc.remove_minimeta(None, make_hunt(), minimeta)

        RollupDb.delete.assert_called_once_with(7)
```

Confirm the top-of-file import line includes `make_hunt`:
`from tests.conftest import make_hunt, make_puzzle, make_round` (add `make_hunt` if absent).

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 120 pipenv run python -m pytest tests/test_minimeta_service.py::TestRemoveMinimeta -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleService' object has no attribute 'remove_minimeta'`.

- [ ] **Step 3: Implement the service method**

In `bot/core/puzzle_service.py`, directly after the `make_minimeta` method (after line
1317, before the `@pushes_board()` `convert_to_meta` at line 1319), add:

```python
    @pushes_board()
    async def remove_minimeta(self, guild, hunt, puzzle):
        """Revert a minimeta back to an ordinary puzzle: delete every mini-puzzle
        child (thread + sheet tab + DB row), strip the rollup contribution Table from
        the minimeta's own sheet, delete the puzzle-keyed rollup row, and clear the
        Minimeta type. The inverse of make_minimeta + the minis created by create_mini.

        Guard mirrors the derived is_minimeta (bot/web/data.py): a puzzle qualifies if
        it owns minis OR still carries the Minimeta type marker; otherwise raises
        RuntimeError("not_minimeta"). Guild may be None (skips Discord thread deletion).
        Per-mini failures are isolated (logged, run continues) like delete_round.
        """
        minis = self.get_minis(puzzle.id)
        if puzzle.puzzle_type != "Minimeta" and not minis:
            raise RuntimeError("not_minimeta")

        for mini in minis:
            try:
                await self.delete_puzzle(guild, hunt, mini)
            except Exception:
                logger.exception("remove_minimeta: failed to delete mini %s", mini.id)

        # Revert the minimeta's own tab. render_minimeta_contribution promotes the tab
        # to target layout, so demote_tab_to_puzzle (the inverse) strips the Table.
        # Guard on the Table actually existing — a minimeta made but never rendered has
        # no Table, and demoting it would delete the solver grid.
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None and puzzle.google_page_id:
            has_table = any(
                bool(s.get("tables"))
                for s in gsheet_cog.sheet_list(gsheet_cog.get_spreadsheet_id())["sheets"]
                if s["properties"]["sheetId"] == int(puzzle.google_page_id)
            )
            if has_table:
                await gsheet_cog.demote_tab_to_puzzle(puzzle)

        rollup = RollupsJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
        if rollup is not None:
            RollupsJsonDb.delete(rollup.id)

        puzzle.puzzle_type = None
        PuzzleJsonDb.commit(puzzle)
```

(`logger`, `RollupsJsonDb`, and `PuzzleJsonDb` are already imported/used in this module —
see `refresh_minimeta` at line 559 and `make_minimeta` at line 1314.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_minimeta_service.py::TestRemoveMinimeta -q > /tmp/test.log 2>&1`
Expected: PASS (3 passed).

- [ ] **Step 5: Register the board-push expectation (failing)**

In `tests/test_board_push_service.py`, add `"remove_minimeta"` to `EXPECTED_PUSH_METHODS`
(the set at lines 20-28), e.g. on the line with `make_minimeta`:

```python
    "delete_round", "erase_note", "make_minimeta", "remove_minimeta", "remove_sheet",
```

- [ ] **Step 6: Run the guard test to confirm it now passes**

The `@pushes_board()` decorator was already added in Step 3, so the decorated set now
matches the expectation.
Run: `timeout 120 pipenv run python -m pytest tests/test_board_push_service.py::test_every_mutation_carries_the_push_decorator -q > /tmp/test.log 2>&1`
Expected: PASS. (If it fails with a set-difference, verify the decorator in Step 3 and the
spelling in Step 5 match exactly.)

- [ ] **Step 7: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_minimeta_service.py tests/test_board_push_service.py
git commit -m "feat(service): remove_minimeta teardown seam"
```

---

## Task 6: `remove-minimeta` web route (U3)

**Files:**
- Modify: `bot/web/actions.py` (add route after `make_minimeta_puzzle` ~line 484)

No web-route unit tests exist for this surface (make-minimeta has none), so verification
is: the service test (Task 5) plus the suite importing the module cleanly.

- [ ] **Step 1: Add the route**

In `bot/web/actions.py`, immediately after the `make_minimeta_puzzle` route (after its
`return jsonify({"ok": True})` around line 484), add:

```python
@actions_bp.route("/puzzles/<int:puzzle_id>/remove-minimeta", methods=["POST"])
@login_required
async def remove_minimeta_puzzle(puzzle_id):
    """Revert a minimeta to an ordinary puzzle — delete its minis + rollup table
    (mirrors !remove_minimeta). Organiser-gated, puzzle-locked, audited, board pushed."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    perm = await require_organiser(ctx["guild_snowflake"])
    if perm is not None:
        return perm

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = bot.get_guild(int(snowflake)) if snowflake else None

        try:
            await cog.service.remove_minimeta(guild, hunt_obj, puzzle)
        except RuntimeError:
            return jsonify({"error": "not_minimeta"}), 400
        except SheetRefreshError as exc:
            await _audit("remove_minimeta", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
            return jsonify({"ok": True, "sheet_warning": str(exc)})
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)

        await _audit("remove_minimeta", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))

    return jsonify({"ok": True})
```

(`SheetRefreshError`, `discord`, `_discord_error_response`, `asyncio`, `puzzle_lock`,
`current_app`, `_audit`, `require_organiser`, `_visible_hunt_for_puzzle` are all already
imported/used by the neighbouring `delete_puzzle` and `make_minimeta_puzzle` routes.)

- [ ] **Step 2: Verify the module imports cleanly (route registers)**

Run: `timeout 120 pipenv run python -c "import bot.web.actions" > /tmp/test.log 2>&1`
Expected: no output, exit 0 (no syntax/import error).

- [ ] **Step 3: Run the full backend suite as a regression check**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: PASS (no new failures). Read `/tmp/test.log` for the summary line.

- [ ] **Step 4: Commit**

```bash
git add bot/web/actions.py
git commit -m "feat(web): remove-minimeta endpoint"
```

---

## Task 7: `!remove_minimeta` bang command (U3)

**Files:**
- Modify: `bot/cogs/puzzles.py` (add command after `make_minimeta` ~line 1121)
- Test: `tests/test_minimeta_commands.py` (new `TestRemoveMiniMeta` class)

- [ ] **Step 1: Write the failing command test**

In `tests/test_minimeta_commands.py`, after the `TestMakeMiniMeta` class (line 29), add:

```python
class TestRemoveMiniMeta:
    @pytest.mark.asyncio
    async def test_removes_and_confirms(self):
        puzzle = make_puzzle(id=1, puzzle_type="Minimeta")
        hunt = MagicMock()
        cog = make_cog(puzzle, hunt)
        ctx = make_ctx()
        cog.service.remove_minimeta = AsyncMock()

        await Puzzles.remove_minimeta.callback(cog, ctx)

        cog.service.remove_minimeta.assert_called_once_with(ctx.guild, hunt, puzzle)
        ctx.send.assert_called_once()
        assert "minimeta" in ctx.send.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_not_minimeta_reports_error(self):
        puzzle = make_puzzle(id=1, puzzle_type="Cryptic")
        cog = make_cog(puzzle, MagicMock())
        ctx = make_ctx()
        cog.service.remove_minimeta = AsyncMock(side_effect=RuntimeError("not_minimeta"))

        await Puzzles.remove_minimeta.callback(cog, ctx)

        ctx.send.assert_called_once()
        assert "not a minimeta" in ctx.send.call_args[0][0].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 120 pipenv run python -m pytest tests/test_minimeta_commands.py::TestRemoveMiniMeta -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: type object 'Puzzles' has no attribute 'remove_minimeta'`.

- [ ] **Step 3: Implement the command**

In `bot/cogs/puzzles.py`, directly after the `make_minimeta` command (after its
`await ctx.send(...)` around line 1121), add:

```python
    @commands.command()
    async def remove_minimeta(self, ctx):
        """*Revert this minimeta back to an ordinary puzzle, deleting its mini-puzzles.*

        Usage: `!remove_minimeta`
        Example: `!remove_minimeta`
        Run in the minimeta's channel. Deletes every mini-puzzle (thread + sheet) and
        the rollup table, then clears the Minimeta type."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        try:
            await self.service.remove_minimeta(ctx.guild, self.get_hunt(ctx), puzzle)
        except RuntimeError:
            return await ctx.send(":x: This puzzle is not a minimeta.")
        await ctx.send(
            f":white_check_mark: **{puzzle.name}** is no longer a minimeta; its "
            f"mini-puzzles were removed."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_minimeta_commands.py::TestRemoveMiniMeta -q > /tmp/test.log 2>&1`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_minimeta_commands.py
git commit -m "feat(bot): !remove_minimeta command"
```

---

## Task 8: Remove-minimeta menu item + typed confirm (U3 frontend)

**Files:**
- Modify: `frontend/src/api.ts` (add hook after `useMakeMinimeta` ~line 184)
- Modify: `frontend/src/components/DragBoard.tsx` (CardProps, PuzzleCard menu, DragBoard container, modal)

- [ ] **Step 1: Add the API hook**

In `frontend/src/api.ts`, directly after the `useMakeMinimeta` function (after its closing
`}` around line 190), add:

```typescript
export function useRemoveMinimeta(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/remove-minimeta`, "POST",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}
```

- [ ] **Step 2: Import the hook + declare it in the container**

In `frontend/src/components/DragBoard.tsx`, add `useRemoveMinimeta` to the existing import
from `../api` (the line importing `useMakeMinimeta`). Then, inside the `DragBoard`
component after `const makeMinimeta = useMakeMinimeta(uid);` (line 416), add:

```tsx
  const removeMinimeta = useRemoveMinimeta(uid);
```

And after the `deleteTarget` state (line 429), add:

```tsx
  // Minimeta awaiting a remove-status confirm (null = modal closed).
  const [removeMinimetaTarget, setRemoveMinimetaTarget] = useState<Puzzle | null>(null);
```

- [ ] **Step 3: Add the mutation runner**

In `frontend/src/components/DragBoard.tsx`, after the `runDeletePuzzle` function (ends line
495), add:

```tsx
  function runRemoveMinimeta() {
    if (!removeMinimetaTarget) return;
    const name = removeMinimetaTarget.name;
    removeMinimeta.mutate(removeMinimetaTarget.id, {
      onSuccess: (data) => {
        showSheetWarning(data);
        notifications.show({ message: `"${name}" is no longer a minimeta.`, color: "green" });
        setRemoveMinimetaTarget(null);
      },
      onError: notifyError,
    });
  }
```

- [ ] **Step 4: Extend CardProps + PuzzleCard signature**

In the `CardProps` interface (contains `onMakeMinimeta` at line 64), add after it:

```tsx
  onRemoveMinimeta: (puzzle: Puzzle) => void;
```

In the `PuzzleCard` destructured params (line 74), add `onRemoveMinimeta` to the list:

```tsx
function PuzzleCard({ puzzle, dragId, columnGroupCode, columnName, metas, onRetag, onConvertToFeeder, onDeletePuzzle, onMakeMinimeta, onRemoveMinimeta, onConvertToMeta, onConvertToMetapuzzle, onConvertToCapstone, onConvertToRunaround, onConvertToGroup, isOrganiser, isPending }: CardProps) {
```

- [ ] **Step 5: Split the minimeta menu block**

In `PuzzleCard`, replace the entire block that starts at line 251
(`{!puzzle.is_meta && !puzzle.solved && (`) and ends at line 279 (`)}` closing that
block). Original:

```tsx
            {!puzzle.is_meta && !puzzle.solved && (
              <>
                <Menu.Divider />
                <Menu.Item
                  color="grape"
                  onClick={() =>
                    setPendingAction({
                      title: "Make minimeta",
                      message: `Mark "${puzzle.name}" as a minimeta?`,
                      onConfirm: () => onMakeMinimeta(puzzle.id),
                    })
                  }
                >
                  Make minimeta
                </Menu.Item>
                <Menu.Item
                  color="grape"
                  onClick={() =>
                    setPendingAction({
                      title: "Convert to meta",
                      message: `Convert "${puzzle.name}" to a meta? This creates a new meta round around it.`,
                      onConfirm: () => onConvertToMeta(puzzle.id),
                    })
                  }
                >
                  Convert to meta
                </Menu.Item>
              </>
            )}
```

with:

```tsx
            {puzzle.is_minimeta && !puzzle.solved && (
              <>
                <Menu.Divider />
                <Menu.Item color="red" onClick={() => onRemoveMinimeta(puzzle)}>
                  Remove minimeta status…
                </Menu.Item>
              </>
            )}
            {!puzzle.is_meta && !puzzle.is_minimeta && !puzzle.solved && (
              <>
                <Menu.Divider />
                <Menu.Item
                  color="grape"
                  onClick={() =>
                    setPendingAction({
                      title: "Make minimeta",
                      message: `Mark "${puzzle.name}" as a minimeta?`,
                      onConfirm: () => onMakeMinimeta(puzzle.id),
                    })
                  }
                >
                  Make minimeta
                </Menu.Item>
                <Menu.Item
                  color="grape"
                  onClick={() =>
                    setPendingAction({
                      title: "Convert to meta",
                      message: `Convert "${puzzle.name}" to a meta? This creates a new meta round around it.`,
                      onConfirm: () => onConvertToMeta(puzzle.id),
                    })
                  }
                >
                  Convert to meta
                </Menu.Item>
              </>
            )}
```

- [ ] **Step 6: Pass the handler at the PuzzleCard mount**

In the `<PuzzleCard ... />` JSX (line 631-649), after `onMakeMinimeta={onMakeMinimeta}`
(line 641), add:

```tsx
                    onRemoveMinimeta={setRemoveMinimetaTarget}
```

- [ ] **Step 7: Render the typed-confirm modal**

In `frontend/src/components/DragBoard.tsx`, after the delete `TypedConfirmModal` block
(ends line 718, before the `<ConfirmModal` at line 720), add:

```tsx
      <TypedConfirmModal
        opened={removeMinimetaTarget != null}
        onClose={() => setRemoveMinimetaTarget(null)}
        onConfirm={runRemoveMinimeta}
        title={`Remove minimeta "${removeMinimetaTarget?.name ?? ""}"`}
        confirmPhrase={removeMinimetaTarget?.name ?? ""}
        confirmLabel="Remove minimeta"
        loading={removeMinimeta.isPending}
      >
        <Text size="sm" c="red">
          ⚠️ This deletes every mini-puzzle under &ldquo;{removeMinimetaTarget?.name}&rdquo; —
          each one&rsquo;s Discord thread and Google sheet tab — and reverts this puzzle to an
          ordinary puzzle. This cannot be undone.
        </Text>
      </TypedConfirmModal>
```

- [ ] **Step 8: Typecheck/build**

Run: `cd frontend && npm run build`
Expected: build succeeds, no TypeScript errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/api.ts frontend/src/components/DragBoard.tsx
git commit -m "feat(web): remove minimeta status from arrange with typed confirm"
```

---

## Final verification

- [ ] **Backend suite green:**
  Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
  Expected: all pass (read `/tmp/test.log` summary).

- [ ] **Frontend builds:**
  Run: `cd frontend && npm run build`
  Expected: succeeds.

- [ ] **Manual smoke (optional, needs the running app):** On By Group and All Puzzles, a
  minimeta shows a legible violet `solved/total` badge (clickable → opens the mini panel),
  a lilac `MINIMETA` badge, and (By Group) a lilac left strip. In Arrange, an unsolved
  minimeta's menu offers "Remove minimeta status…", which requires typing the puzzle name,
  then deletes the minis + rollup and the puzzle reverts to ordinary.

---

## Self-review notes (spec coverage)

- U0 derivation → Task 1 (backend) + Task 2 (type/gates). ✅
- U1 visible count → Task 3. ✅
- U2 badge + strip → Task 4. ✅
- U3 service/route/bang/frontend → Tasks 5/6/7/8. ✅
- Sheet teardown reuses `demote_tab_to_puzzle` with the `has_table` guard → Task 5 Step 3. ✅
- Unsolved-only gating → Task 8 Step 5 (`!puzzle.solved`). ✅
- Board-push guard registration → Task 5 Steps 5-6. ✅
