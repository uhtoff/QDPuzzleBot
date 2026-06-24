# Web ↔ bang parity — Phase 2: Puzzle edits (design)

Date: 2026-06-24
Status: design approved — ready to turn into an implementation plan
Roadmap: `.ai/plans/web-command-parity.md` (this is **Phase 2** of that plan)
Predecessor: Phase 1 (solve panel) — merged to main @ `1ca094c`

## Goal

Give the web dashboard equivalents of the Tier-A puzzle-edit bang commands so an
organiser/participant can do them without Discord:

- `!link` — set a puzzle's URL.
- `!rename_puzzle` / `!rename_meta` — rename a puzzle (cascades to channel + sheet tab).
- `!clue` — log a single clue against a puzzle.
- `!make_minimeta` — mark a puzzle as a minimeta cluster.
- `!convert_to_meta` — promote a puzzle to a metapuzzle (creates its round).

## In scope

The five commands above, each as: a ctx-free `PuzzleService` method (extract where one
doesn't exist; reuse where it does) → a thin Quart action endpoint → a dashboard control.

## Out of scope (do NOT do here)

- **`convert_to_feeder`** — destructive (deletes the meta's round, orphans feeders per
  the `capstone-orphans-feeders` rule). Deferred to **Phase 3** and handed to **worktree-2**
  as its own destructive-parity task. Do not implement it here.
- Batch `!clues` (multiple quoted strings) — single-clue MVP only.
- Phases 3–5 of the parity plan (deletion, reconcile, hunt lifecycle/settings).
- Any change to `PuzzleService.solve`/`unsolve` or the Phase-1 solve-mode endpoint.

## Architecture — thin controller (Approach A)

Three layers; every command flows the same way (mirrors `retag_puzzle` and the Phase-1
solve-modes endpoint):

1. **Service (`bot/core/puzzle_service.py`, ctx-free):** owns all mutation + cascade
   (DB commit → gsheet push/restructure → Discord channel rename → `notifier.board_changed`).
   Returns/raises like `solve`: raise `SheetRefreshError` on a non-fatal sheet failure so
   the endpoint can surface a `sheet_warning` while keeping the DB change.
2. **Endpoint (`bot/web/actions.py`, thin — no business logic):** resolve the visible hunt
   + auth → `puzzle_lock(puzzle_id)` → one `cog.service.*` call → `_audit(...)` →
   best-effort `refresh_unassigned_safe` → JSON `{ ok, sheet_warning? }`.
3. **Frontend (`frontend/src/`):** one mutation hook per action in `api.ts`; invalidate
   `["hunt", uid]` on success.

Rejected alternatives:
- **B — fold rename/link into the generic `PATCH /api/puzzles/<id>`:** hides heavy
  side-effects (channel rename, sheet restructure) behind a "field update", muddies the
  audit log, diverges from how solve/retag are modelled.
- **C — one mega `action`-discriminated endpoint:** becomes a god-endpoint with per-action
  auth/validation branching; violates small-focused-units.

## Per-command plan

| Command | Service method | Seam | Endpoint | Cascades | Auth |
|---|---|---|---|---|---|
| link | `set_puzzle_url(guild, hunt, puzzle, url)` | EXTRACT | `POST /api/puzzles/<id>/link` | gsheet cell | mirror bang |
| rename | `rename_puzzle(guild, hunt, puzzle, name)` | EXTRACT | `POST /api/puzzles/<id>/rename` | `safe_rename` channel + gsheet tab + meta refresh | mirror bang |
| clue | `log_clues` + `refresh_evidence` | EXISTS | `POST /api/puzzles/<id>/clues` | evidence sheet | mirror bang |
| make_minimeta | `make_minimeta(puzzle)` | EXISTS | `POST /api/puzzles/<id>/make-minimeta` | none (flag flip) | organiser |
| convert_to_meta | `convert_to_meta(guild, hunt, puzzle)` | EXTRACT | `POST /api/puzzles/<id>/convert-to-meta` | creates round + gsheet restructure + channel type | organiser |

Permissions: **mirror each bang command's decorator** — verify at implementation, do not
guess. Structural ops (`make_minimeta`, `convert_to_meta`) are expected to need
`require_organiser`; field edits likely allow any hunt member. Confirm against the cog.

### Reference implementations to copy
- Endpoint wrapper: `retag_puzzle` (`bot/web/actions.py`) and `solve_puzzle` (same file,
  Phase-1 mode dispatch).
- Service contract: `PuzzleService.solve` (isolation + `SheetRefreshError` + board push).
- Existing seams to reuse verbatim: `log_clues`, `refresh_evidence`, `make_minimeta`.

## Frontend

- **Drawer — field edits:**
  - rename: inline-edit on the drawer title.
  - link: editable URL field — renders as a link, click to edit (`TypeCell`-style popover).
  - clue: a "Log clue" input + submit, rendering into the existing `EvidencePanel`.
- **Kebab / overflow menu — structural actions** on the puzzle card/row:
  - "Make minimeta" and "Convert to meta", each behind a confirm dialog.
  - `convert_to_meta`'s confirm states it creates a meta round.
- **`api.ts` hooks:** `useSetPuzzleLink`, `useRenamePuzzle`, `useLogClue`, `useMakeMinimeta`,
  `useConvertToMeta`.

## Testing

- Extend `tests/test_solve_equivalence.py` with service-level **equivalence** tests for the
  three newly-extracted methods (`set_puzzle_url`, `rename_puzzle`, `convert_to_meta`) using
  the existing `_run` harness — assert the same DB-state transitions the bang path produces.
- `log_clues` / `make_minimeta` already have seams; add a light service test only where not
  already covered.
- `cd frontend && tsc --noEmit` clean.
- Manual dashboard click-through of all five controls.
- Tests-first per phase (same discipline as the Phase-1 handoff).

## Risks & sequencing

- **rename is the riskiest extraction.** `safe_rename` couples to the cog's deferred-rename
  state machine + Discord's 2-renames/10-min limit. The extracted method must **reuse that
  exact path**, not reimplement it, and the web must tolerate a deferred rename gracefully.
- **convert_to_meta creates a round** (`_round_impl`): extraction must preserve channel-type
  update + sheet restructure; guarded by the equivalence test.
- **Suggested commit order (simplest cascade → most complex):**
  link → clue → make_minimeta → convert_to_meta → rename.

## Acceptance criteria

- Each of the five commands works from the dashboard and produces DB state identical to its
  bang equivalent (equivalence tests prove the service seam).
- Full pytest suite green; `tsc --noEmit` clean.
- No change to Phase-1 solve behaviour; `convert_to_feeder` untouched (lives in worktree-2).
