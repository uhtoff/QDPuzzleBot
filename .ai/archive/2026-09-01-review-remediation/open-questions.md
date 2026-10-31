# Open Questions

## Blocking

**None — all five questions were decided on 2026-08-31.** Recorded below as D5–D9 so the
reasoning survives; the evidence behind each is in `sheets-tranche-research.md`.

- **D5 (was Q1) — M14: use the existing pattern, and decide by ownership, not by name.**
  The realistic collision is a *puzzle* named "Evidence" taking the title first: puzzle tabs
  uniquify through `get_unique_tab_name` (`puzzles_gsheet.py:1370`), but the hunt-level
  `create_blank_tab` passes a literal title, so it 400s and wedges. A puzzle named
  "Evidence" *after* the tab exists is harmless — it becomes "Evidence (2)".
  So: if the existing tab belongs to a puzzle, create under a unique name via
  `get_unique_tab_name`; if it belongs to no puzzle it is a bot leftover and may be
  adopted (and cleared). **Do not adopt blind by name** — `refresh_unassigned`'s current
  fallback does, and would adopt a puzzle's tab and write rollup rows into a live solving
  grid. Fix that sibling the same way.
- **D6 (was Q2) — M16/M17: strict name match.** No live spreadsheet carries a legacy-era
  contribution table. No allowlist; record the legacy names in a comment as the one-line
  remedy if a rollup ever goes blank.
- **D7 (was Q3) — M18/C5: a live scratch-spreadsheet exercise is acceptable** to settle the
  four unverified Sheets API assumptions before shipping.
- **D8 (was Q4) — Tranche 2: backfill everything to "main".** **No hunt has used an archive
  spreadsheet since the new bot**, so no `additional_sheets` row is archived today. That
  makes "main" correct, not merely safe. Migration `045` should still carry a pre-flight
  query in its header that would reveal a contradicting row (house standard — see 040).
- **D9 (was Q5) — Tranche 8: split** into `failed_group_ids` + `failed_puzzle_ids`.
  The deciding argument is user-facing, not cosmetic: the message says "rebuild to retry",
  but `!rebuild` refreshes rollups and cannot retry the failed solved-sheet archive move
  that the puzzle-id case represents. Splitting lets the message give the right remedy per
  kind.

## Non-blocking

- **M19 should ship with H3, not separately.** They are the same loop
  (`puzzles_gsheet.py:599-604`), and H3 is a High still open in Tranche 2. Fixing M19
  alone leaves the more common breakage in place.
- **M21 must go last.** It rewrites the same lines M17/M18 touch (`sheet_list`'s response
  shape, the `_ensure_contribution_table` → `reconcile_table` handoff), so doing it early
  guarantees rework. It is also the only one of the nine that is pure optimisation — no
  state is corrupted by it today.
- **The whole Sheets test surface is request-shape only.** Every feasible test asserts
  captured request bodies and call ordering; none verifies what Google actually does. Worth
  deciding whether that is acceptable coverage for this subsystem or whether one
  live-API smoke test earns its place.
- **Phase 9 triage** — the ~15 findings not yet in a tranche should be slotted or
  explicitly deferred, or they will rot the way the 2026-08-07 "still open" list did.
- **Frontend has no DOM test environment.** M24/M25/C6 shipped inspection-only. Adding
  jsdom/happy-dom would make that class of bug testable; it is a dependency decision.
