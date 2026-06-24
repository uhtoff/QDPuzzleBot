# Plan: Web dashboard ↔ bang command parity

Project principle: **every bang command should have a web-dashboard equivalent**
(see memory `web-dashboard-command-parity`). This plan closes the gaps found by
auditing all 74 bang commands (`bot/cogs/puzzles.py`, `bot/cogs/utility.py`,
`bot/cogs/puzzles_gsheet.py`) against the dashboard's HTTP actions
(`bot/web/actions.py`, `bot/web/api.py`) and frontend (`frontend/src/`).

## Architectural pattern

Most bang commands call a `cog.service.<method>()` on `PuzzleService`
(`bot/core/puzzle_service.py`). The web equivalent is a thin Quart endpoint in
`bot/web/actions.py` that reuses the **same** service method, wrapped with:
auth (`@login_required` + `require_organiser` where needed) → `puzzle_lock` →
service call → `_audit(...)` → best-effort `refresh_unassigned_safe` → JSON
response with optional `sheet_warning`. `retag_puzzle` (actions.py:382) is the
reference implementation.

**Caveat (drives Phase 1):** not every bang has a clean service seam. The solve
*variants* keep their mutation logic in the cog, coupled to `ctx` and the gsheet
cog:
- `_partial_impl` (puzzles.py:2209), `change_solution` (2300), `add_solution`
  (2345) mutate `puzzle.solution`/`status`/`solve_time` inline, commit, call
  `_update_all_metas` (2187), and push the puzzle's own sheet via
  `get_gsheet_cog(ctx).update_solution(puzzle)`.
- Only `solve` is already a service method (`service.solve`, puzzle_service.py:523).

So phases that wrap a clean service method are mechanical; phases that don't
require a **service-extraction refactor first**, guarded by the bang-vs-web
equivalence suite (`tests/test_solve_equivalence.py`) and `tests/test_refresh_meta.py`.

## Gap analysis (tiers)

Already on web (no action): create puzzle/meta, notes add/erase, status/priority/
type, sheets add/remove, full solve/unsolve, retag move/add/remove/clear, rebuild
rollup, source-mode, nesting, mini creation, evidence routing, rollup answers.

### Tier A — high-value daily workflow
- `!partial`, `!change_solution`, `!add_solution`, `!mark_as_complete` — solve variants.
- `!rename_puzzle` / `!rename_meta` — rename (no rename on web at all).
- `!clue`, `!clues` — log a clue (backend `add_evidence` exists, **no UI**).
- `!link` — update a puzzle's URL.
- `!convert_to_meta`, `!convert_to_feeder` — promote/demote a puzzle.
- `!make_minimeta` — convert a puzzle into a minimeta cluster.

### Tier B — organiser structural / power tools
- `!delete_puzzle`, `!delete_round`/`group` — deletion (no delete of any kind on web).
- `!reconcile` — Discord↔Sheets reconcile (dry-run / `apply` / `apply orphans`).
- `!rebuild_unassigned` — explicit button (currently only fires automatically).
- `!pool` — tag a puzzle as aggregates-into for its meta.
- `!round` (plain round, no meta) — create a category without a metapuzzle.

### Tier C — hunt lifecycle & settings (admin)  →  own admin route + pane
- `!hunt` (create), `!delete_hunt`, `!set_to_archive`, `!set_start`, `!set_login`,
  `!parallel_hunt`, `!update_settings` (channel/hunt/guild settings editor).
- **Decision:** these live in a dedicated admin route/pane, NOT scattered into the
  per-hunt board. Hunt **creation** is the exception — it lives on the all-hunts
  (HuntList) screen, and `set_start` + `set_login` are promoted to creation-time
  fields there (see Phase 5).

### Tier D — intentionally EXCLUDED (not web-appropriate)
- Discord channel ordering: `!move_to_top/bottom/solved` (board auto-orders).
- Archive-to-threads: `!archive_round/category/channel/solved_manually`.
- `!sync_meta` / `!sync_round` — Discord-only (sync a category's puzzles to its
  meta); no web equivalent intended.
- Bot runtime/info: `!start_reminders/stop_reminders`, `!uptime`, `!starttime`,
  `!info_bot`, `!invite`.
- Debug/admin: `!list_tags/add_tag/remove_tag`, `!show_settings`,
  `!show_puzzle_settings`, `!debug_puzzle_channel`, `!read`, `!info`.
Revisit only on explicit request.

## Phases (each independently shippable)

Every phase ends with: a service-level test (mirror `test_refresh_meta.py`),
extend `test_solve_equivalence.py` where a service seam changed, `tsc --noEmit`,
and a manual dashboard click-through. Commit per logical unit.

### Phase 1 — Solve panel completeness  (Tier A solving)  ← FIRST
Goal: dashboard can do everything the solve family does, not just full solve/unsolve.
1. **Refactor**: extract cog solve-variant logic into `PuzzleService`, mirroring
   `service.solve`'s isolation/return contract:
   - `add_partial(guild, hunt, puzzle, solution)` ← `_partial_impl`
     (status "Partially Solved", append `/`, commit, refresh_metas).
   - `change_solution(guild, hunt, puzzle, solution)` ← `change_solution` body
     (set solution+solve_time, commit, refresh_metas, gsheet `update_solution`).
   - `add_solution(guild, hunt, puzzle, solution)` ← `add_solution` body
     (append `/`, solve_time, commit, refresh_metas, gsheet `update_solution`).
   - Rewire the bang commands to call these (keep their embeds/channel messaging).
   - `mark_as_complete` needs no new method — it is `solve(..., "✅")`.
2. **Backend**: extend the existing solve route family in actions.py. Either add
   modes to `POST /api/puzzles/<id>/solve` (e.g. `mode: full|partial|change|add`)
   or sibling routes. Reuse the new service methods + audit + board push.
3. **Frontend**: in the solve UI (PuzzleDrawer / MiniPuzzlePanel) add partial /
   change-solution / add-solution / mark-complete actions alongside solve.
4. **Verify**: equivalence test asserts bang and web produce identical DB state for
   each variant.

### Phase 2 — Puzzle edits  (Tier A rest)
- `rename` (puzzle+meta): needs a `service.rename` seam (check current cog impl) →
  `PATCH /api/puzzles/<id>` extended or new route → inline-edit in PuzzleDrawer.
- `link`: extend `update_puzzle` PATCH with a `link`/`url` field → PuzzleDrawer field.
- `clue` log UI: `add_evidence` already exists — add a "Log clue" control to
  EvidencePanel (batch = `!clues`).
- `convert_to_meta` / `convert_to_feeder`: wrap service convert methods → card menu
  action. Honour the capstone rule (memory `capstone-orphans-feeders`): converting
  a meta orphans its feeders.
- `make_minimeta`: wrap → card action.

### Phase 3 — Deletion & structural  (Tier B)
- `delete_puzzle`, `delete_round/group`: destructive → require organiser + explicit
  typed confirm; reuse the bot's delete service paths (channel + sheet teardown).
- `rebuild_unassigned` (button), `pool`, plain `round` create.
- (`sync_meta` dropped — Discord-only, see Tier D.)

### Phase 4 — Reconcile  (Tier B, isolated — riskiest)
- `!reconcile` with dry-run / apply / apply-orphans. Deletes Discord channels, so it
  gets its own phase and a careful preview→confirm UI (show the diff before apply).

### Phase 5 — Hunt lifecycle & settings  (Tier C)  — own admin route + pane
Everything except hunt creation lives behind a dedicated admin route/pane (gated
to admins), not scattered into the per-hunt board.

- **Hunt creation on the all-hunts (HuntList) screen:** a "New hunt" form with
  - **link** to the hunt — mandatory,
  - **start time** — optional (promotes `!set_start` to creation-time),
  - **login details** — optional (promotes `!set_login` to creation-time).
  These remain editable afterwards from the admin pane.
- **Admin pane:** `delete_hunt`, `set_to_archive` (archive toggle), edit
  start/login post-creation, `parallel_hunt`.
- **Settings editor (`update_settings`):** reviewed setting-by-setting when we
  reach this phase — not all are web-appropriate, and some may instead be promoted
  to creation-time options. Do NOT bulk-expose the whole settings surface; decide
  per key. Treat this as a sub-spec written at Phase 5 start.

## Verification & rollback
- Service refactors are guarded by `test_solve_equivalence.py` — run it after every
  Phase 1 extraction step.
- Each web endpoint added is covered by a service-level unit test; the endpoint
  wrapper itself is low-risk (auth + reuse), matching existing untested wrappers.
- Rollback: phases are independent commits; revert a phase without touching others.
