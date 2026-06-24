# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-06-24
Status: in progress — docs: web/bang command-parity plan + Phase 1 (solve panel) spec @ 7a706a2

## Goal

Close web-dashboard ↔ bang-command parity. Full plan in
`.ai/plans/web-command-parity.md`; currently executing **Phase 1 — Solve panel
completeness** (partial / change_solution / add_solution / mark_as_complete),
spec in `.ai/active/current-spec.md`.

## Current status

- Audited all 74 bang commands vs the dashboard's HTTP actions. Tiered gap
  analysis + 5-phase plan committed to `.ai/plans/web-command-parity.md`.
- Phase 1 spec written. No Phase 1 code yet.
- Earlier unrelated task (arrange-tab retag grey-out + drag-to-unassign) is DONE
  and committed (e8bc800, 33f8d70, 72f38cf) — separate from this work.

## Final decisions

- Start with Phase 1 (solve panel); user chose "full plan doc first".
- Phase 1 requires a service-extraction refactor (solve variants live in the cog,
  not the service), guarded by `tests/test_solve_equivalence.py`.
- Tier D commands (Discord channel ordering, archive-to-threads, bot runtime,
  debug) are excluded as not web-appropriate unless explicitly requested.
- `!sync_meta`/`!sync_round` are Discord-only → moved to Tier D (excluded).
- Tier C (hunt lifecycle/settings) gets its OWN admin route + pane. Hunt creation
  lives on the all-hunts screen with link (mandatory), start time (optional),
  login (optional) — promoting set_start/set_login to creation-time fields.
- `!update_settings` is reviewed setting-by-setting at Phase 5; some keys may be
  excluded or promoted to creation. Not bulk-exposed.

## Rejected approaches

- Implementing solve variants directly in the web endpoint (would duplicate cog
  logic and drift from bang) — rejected in favour of shared service methods.

## Next steps

1. Extract `add_partial` / `change_solution` / `add_solution` into `PuzzleService`,
   rewire bang commands, run `test_solve_equivalence.py`.
2. Add web `solve` mode(s) + audit + board push.
3. Frontend solve-variant controls in PuzzleDrawer (+ MiniPuzzlePanel).
4. Service test + extended equivalence test + tsc + manual click-through.

## Open questions

- Endpoint shape: `mode` param on `/solve` vs sibling routes (spec leans `mode`).
- Exact PuzzleDrawer UI layout for the four variants (confirm during build).
