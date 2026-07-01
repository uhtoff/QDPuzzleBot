# Mini-meta UI updates — Design

Date: 2026-07-01
Status: approved-pending-review

Three independent updates to how mini-metas are surfaced and managed in the web
dashboard, plus a backend teardown seam and matching bang command.

## Goal

1. Make the attached-mini count actually visible on the lilac/violet badge used to
   open a minimeta's mini UI (By Group + All Puzzles pages).
2. Give minimetas a `MINIMETA` kind badge and a left kind-strip, matching the other
   meta kinds.
3. In Arrange, replace the "Make minimeta" option on an existing minimeta with
   "Remove minimeta status", gated behind a typed-name confirmation, which tears down
   the mini puzzles and their sheets and reverts the puzzle to a normal puzzle.

## Update 1 — Make the mini count visible

**Symptom:** the count "isn't showing". **Root cause (to verify during
implementation):** in `frontend/src/components/PuzzleRowActions.tsx` (~lines 57-70) the
violet `{solved}/{total}` `<Badge>` is nested inside a fixed-size
`<ActionIcon size="sm">`. That ActionIcon is a ~26px square sized for a single icon, so
the badge text (`2/5`) is clipped and effectively invisible. The data layer already
populates `mini_puzzles` for every board/all-puzzles row (`bot/web/data.py:262-277`), so
the count exists — it is only a render problem.

**Fix:** make the `<Badge>` itself the clickable element:
- Drop the wrapping `<ActionIcon>`.
- Put `onClick={() => onToggleMinis(p.id)}`, `style={{ cursor: "pointer" }}`, and the
  `aria-label="Toggle minis"` on the `<Badge>` (or a plain wrapping element that does not
  constrain size).
- Keep the existing `<Tooltip label={"x/y minis solved"}>`.
- Keep color `violet` (the "lilac" the user refers to) and `variant="light"`.

This file is shared by By Group (`BoardGroup.tsx`) and All Puzzles (`PuzzleTable.tsx`), so
both pages are fixed by the one change.

**Acceptance:** the badge legibly shows `solved/total` (e.g. `2/5`) and clicking it still
toggles the `MiniPuzzlePanel`.

## Update 2 — MINIMETA badge + kind strip

Minimetas are `puzzle_type === "Minimeta"`; they are **not** `is_meta` and their
`meta_kind` is `null`, so both the badge and the strip skip them today.

**Colour:** lilac = a lighter grape, visually related to META (grape) but distinct.
Concrete value to use consistently:
- Badge: Mantine `color="grape.4"` (light grape) — or a shared constant if cleaner.
- Kind strip: `var(--mantine-color-grape-4)`.

**Badge (`frontend/src/components/MetaBadge.tsx`):**
- Current guard `if (!puzzle.is_meta) return null;` must not swallow minimetas.
- Add handling: when `!is_meta && puzzle.puzzle_type === "Minimeta"`, render a
  `MINIMETA` badge in lilac. Add a `minimeta` entry to `KINDS`
  (`{ label: "MINIMETA", color: "grape.4" }`) and select it via `puzzle_type` rather than
  `meta_kind` for this case.
- Appears everywhere `MetaBadge` is used: `BoardGroup`, `PuzzleTable`, `DragBoard`.

**Kind strip (`frontend/src/components/BoardGroup.tsx`, `kindStrip()` ~lines 39-48):**
- `kindStrip()` keys off `meta_kind` (null for minimeta). Extend it to accept the puzzle
  and, when `puzzle_type === "Minimeta"` (and no `meta_kind`), return the lilac strip
  colour.
- The strip currently only exists on By Group (`BoardGroup.tsx:212-213`). All Puzzles has
  no strip today — leave that as-is (out of scope) unless requested.

**Acceptance:** a minimeta row shows a lilac `MINIMETA` badge (all three views) and a
lilac left strip on By Group.

## Update 3 — Remove minimeta status (web + bang)

Currently `make_minimeta` only sets `puzzle.puzzle_type = "Minimeta"`
(`bot/core/puzzle_service.py:1314-1317`). Minis are separate child puzzles created by
`create_mini` (`:1430-1484`): each is `puzzle_type='Mini'`, `parent_puzzle_id=minimeta.id`,
with its own Google tab (`create_puzzle_spreadsheet`) and a Discord thread in the
minimeta's channel. The minimeta also carries a puzzle-keyed rollup: a contribution table
rendered onto its own sheet (`refresh_minimeta` → `render_minimeta_contribution`) plus a
`RollupData` row keyed `target_puzzle_id=minimeta.id`.

**Decisions:**
- Delete mini tabs **and** the rollup table on the minimeta's sheet (full revert).
- Offer removal only when the minimeta is **unsolved** (matches current "Make minimeta"
  gating `!is_meta && !solved`).
- Web + Discord bang command (parity principle).
- Web confirmation is a typed-name confirmation (`TypedConfirmModal`).

### Backend service — `remove_minimeta(guild, hunt, puzzle)`

New `@pushes_board()` method in `puzzle_service.py`:
1. Guard: `puzzle.puzzle_type == "Minimeta"` else raise (e.g. `RuntimeError("not_minimeta")`).
2. For each mini in `self.get_minis(puzzle.id)`: delete it (tab + thread + DB row). Reuse
   the existing `delete_puzzle(guild, hunt, mini)` seam — minis are untagged and not
   targets, so its target-refresh loop is a no-op. Isolate per-mini failures like
   `delete_round` does.
3. Remove the rollup from the minimeta's own sheet: clear/remove the contribution table on
   the minimeta's page and delete the `RollupData` row for `target_puzzle_id=puzzle.id`. If
   no direct "clear table" gsheet seam exists, add a small helper (or render with empty
   members) — implementation-plan detail.
4. `puzzle.puzzle_type = None`; `PuzzleJsonDb.commit(puzzle)`.

### Web route

`POST /api/puzzles/<int:puzzle_id>/remove-minimeta` in `bot/web/actions.py`, mirroring
`make_minimeta_puzzle` (`:454-481`): resolve puzzle/ctx, call
`cog.service.remove_minimeta(...)`, `_audit("remove_minimeta", …)`. Add `remove_minimeta`
to the board-push guard set (see `tests/test_board_push_service.py:24`).

### Bang command

`!remove_minimeta` in `bot/cogs/puzzles.py` (near `make_minimeta` `:1111`): run in the
minimeta's channel, resolve the puzzle, confirm, call `service.remove_minimeta`.

### Frontend

- `frontend/src/api.ts`: add `useRemoveMinimeta(uid)` mirroring `useMakeMinimeta`
  (`:184-188`) → `POST /api/puzzles/${id}/remove-minimeta`.
- `frontend/src/components/DragBoard.tsx` (menu ~lines 251-279): when
  `puzzle.puzzle_type === "Minimeta" && !puzzle.solved`, show **"Remove minimeta status"**
  (red) that opens `TypedConfirmModal` requiring the puzzle name, with a warning it deletes
  the mini puzzles and their sheets. Otherwise (non-minimeta, `!is_meta && !solved`) keep
  the existing "Make minimeta" + "Convert to meta" items.

**Acceptance:** removing a minimeta deletes all its minis (threads + tabs + rows) and the
rollup table, reverts `puzzle_type` to normal, updates the board live, and is reachable
from both the web (typed confirm) and `!remove_minimeta`.

## Out of scope

- Adding a kind strip to All Puzzles (`PuzzleTable`) — it has none today.
- Changing the `solved/total` format or the mini panel itself.
- Any change to how minis roll up while a minimeta exists.

## Relevant files

- `frontend/src/components/PuzzleRowActions.tsx` (U1)
- `frontend/src/components/MetaBadge.tsx`, `BoardGroup.tsx` (U2)
- `frontend/src/components/DragBoard.tsx`, `frontend/src/api.ts` (U3 frontend)
- `bot/core/puzzle_service.py`, `bot/web/actions.py`, `bot/cogs/puzzles.py` (U3 backend)
- `bot/cogs/puzzles_gsheet.py` (rollup-table teardown, if a new seam is needed)
- Tests: `tests/test_minimeta_service.py`, `tests/test_board_push_service.py`,
  `tests/test_minimeta_commands.py`

## Testing

- Frontend: manual/visual check that the count renders and the MINIMETA badge+strip show.
- Backend: unit test `remove_minimeta` deletes minis + rollup and resets `puzzle_type`;
  board-push guard test includes `remove_minimeta`; bang-command test mirrors
  `test_minimeta_commands.py`.
