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

## Update 0 — Derived `is_minimeta` field (foundation for U1–U3)

`puzzle_type` is a free-text, user-editable field (`PuzzleDrawer.tsx:174`,
`PuzzleTable.tsx:374` both PATCH it), so gating minimeta UI on `puzzle_type === "Minimeta"`
is fragile: edit the type off a puzzle that still owns minis and the badge/panel/remove
affordance all vanish while the orphaned mini children remain. Mirror the existing
`meta_kind` pattern (`bot/web/data.py:297`, derived from structure, never stored) with a
derived boolean.

**Derivation (hybrid):** `is_minimeta = bool(mini_puzzles) or puzzle_type == "Minimeta"`.
The structural signal (owns mini children) is authoritative wherever it matters — when minis
exist, the editable type string is irrelevant. The `puzzle_type` flag only covers the
freshly-made **empty** minimeta (0 minis), which has nothing to orphan, so its fragility is
harmless.

- **Backend:** annotate each puzzle dict with `is_minimeta` in `derive_meta_kind` (it
  already has `mini_puzzles` and `puzzle_type` in scope), or a sibling one-liner pass in
  `get_hunt_puzzles`.
- **Frontend type:** add `is_minimeta: boolean` to `Puzzle` in `frontend/src/types.ts`.
- **Replace all three existing `puzzle_type === "Minimeta"` gates** with `is_minimeta`:
  `PuzzleRowActions.tsx:57` (U1 toggle badge), `BoardGroup.tsx:294` and
  `PuzzleTable.tsx:395` (mini-panel render rows).
- **Service guard** (U3): `remove_minimeta` mirrors the hybrid check —
  `puzzle.puzzle_type == "Minimeta" or self.get_minis(puzzle.id)` — rather than the type
  string alone.

U1–U3 below all key off this derived `is_minimeta`, not `puzzle_type`.

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

Minimetas are identified by the derived `is_minimeta` flag (Update 0); they are **not**
`is_meta` and their `meta_kind` is `null`, so both the badge and the strip skip them today.

**Colour:** lilac = a lighter grape, visually related to META (grape) but distinct.
Concrete value to use consistently:
- Badge: Mantine `color="grape.4"` (light grape) — or a shared constant if cleaner.
- Kind strip: `var(--mantine-color-grape-4)`.

**Badge (`frontend/src/components/MetaBadge.tsx`):**
- Current guard `if (!puzzle.is_meta) return null;` must not swallow minimetas.
- Add handling: when `!is_meta && puzzle.is_minimeta`, render a `MINIMETA` badge in lilac.
  Add a `minimeta` entry to `KINDS` (`{ label: "MINIMETA", color: "grape.4" }`) and select
  it via the derived `is_minimeta` flag for this case.
- Appears everywhere `MetaBadge` is used: `BoardGroup`, `PuzzleTable`, `DragBoard`.

**Kind strip (`frontend/src/components/BoardGroup.tsx`, `kindStrip()` ~lines 39-48):**
- `kindStrip()` keys off `meta_kind` (null for minimeta). Extend it to accept the puzzle
  and, when `is_minimeta` (and no `meta_kind`), return the lilac strip colour.
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

New `@pushes_board()` method in `puzzle_service.py`. Models on the existing
`convert_to_puzzle` (`:1047-1074`) demote seam:
1. Guard (hybrid, per Update 0): `puzzle.puzzle_type == "Minimeta" or self.get_minis(puzzle.id)`
   else raise (e.g. `RuntimeError("not_minimeta")`).
2. For each mini in `self.get_minis(puzzle.id)`: delete it (tab + thread + DB row). Reuse
   the existing `delete_puzzle(guild, hunt, mini)` seam — minis are untagged and not
   targets, so its target-refresh loop is a no-op. Isolate per-mini failures like
   `delete_round` does.
3. Revert the minimeta's own sheet: a minimeta tab is promoted to the *same* target layout
   as a real meta — `render_minimeta_contribution` lazily calls
   `promote_tab_to_target(minimeta)` (`puzzles_gsheet.py:1450-1451`). So reuse
   `gsheet_cog.demote_tab_to_puzzle(minimeta)` (`:646`, the inverse of
   `promote_tab_to_target`) — the exact seam `convert_to_puzzle` uses — to delete the
   contribution Table + its columns and regenerate the plain puzzle header. **Guard:** only
   call `demote_tab_to_puzzle` when the tab actually has the contribution Table (mirror the
   `has_table` scan in `render_minimeta_contribution`); a minimeta made but never rendered
   has no Table, and demoting it would delete the solver grid. Also delete the puzzle-keyed
   rollup bookkeeping row `RollupsJsonDb.get_by_attr(target_puzzle_id=puzzle.id)` if present.
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
  `puzzle.is_minimeta && !puzzle.solved`, show **"Remove minimeta status"** (red) that opens
  `TypedConfirmModal` requiring the puzzle name, with a warning it deletes the mini puzzles
  and their sheets. Otherwise, when `!puzzle.is_minimeta && !is_meta && !solved`, keep the
  existing "Make minimeta" + "Convert to meta" items.

**Acceptance:** removing a minimeta deletes all its minis (threads + tabs + rows) and the
rollup table, reverts `puzzle_type` to normal, updates the board live, and is reachable
from both the web (typed confirm) and `!remove_minimeta`.

## Out of scope

- Adding a kind strip to All Puzzles (`PuzzleTable`) — it has none today.
- Changing the `solved/total` format or the mini panel itself.
- Any change to how minis roll up while a minimeta exists.

## Relevant files

- `bot/web/data.py` (`derive_meta_kind` — add `is_minimeta`), `frontend/src/types.ts` (U0)
- `frontend/src/components/PuzzleRowActions.tsx` (U1)
- `frontend/src/components/MetaBadge.tsx`, `BoardGroup.tsx` (U2)
- `frontend/src/components/DragBoard.tsx`, `frontend/src/api.ts` (U3 frontend)
- `bot/core/puzzle_service.py`, `bot/web/actions.py`, `bot/cogs/puzzles.py` (U3 backend)
- `bot/cogs/puzzles_gsheet.py` (`demote_tab_to_puzzle` — reused for rollup-table teardown, no new seam needed)
- Tests: `tests/test_minimeta_service.py`, `tests/test_board_push_service.py`,
  `tests/test_minimeta_commands.py`

## Testing

- Frontend: manual/visual check that the count renders and the MINIMETA badge+strip show.
- Backend: unit test `remove_minimeta` deletes minis + rollup and resets `puzzle_type`;
  board-push guard test includes `remove_minimeta`; bang-command test mirrors
  `test_minimeta_commands.py`.
