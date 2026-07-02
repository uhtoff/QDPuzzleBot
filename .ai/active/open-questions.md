# Open Questions

Resolve the blocking ones with the user **before** implementing the associated finding
(per the progress protocol). Move answers inline as they're decided.

## Blocking

### Q1 — #3 reconcile_table: how to prune without destroying the grid?
The bug is `deleteDimension {dimension: ROWS}` deleting the full-width row. Options:
- (a) Replace prune with a **bounded clear** of only the table's own columns
  (key_col..end_col) via `updateCells`/`deleteRange` with column bounds, leaving E+ intact —
  but then stale rows accumulate / the table grows. Need a compaction story.
- (b) Rewrite the whole table region (bounded to its columns) each reconcile — clear the
  block, re-write current rows — never deleting sheet rows.
- (c) Move the contribution table somewhere it can own full rows (bigger change).
**Decision needed:** which approach. (b) looks least invasive and matches "table owns only
its columns" intent, but confirm the table has a known max extent to clear.

### Q2 — #2/#4: is an archive spreadsheet used in production? — ANSWERED (2026-07-02)
**Yes** — archive spreadsheets are used for bigger hunts, so #2/#4 must work correctly.
Phase 4 stays in scope at its planned position (not deprioritized).

### Q3 — #7/#8: intended timezone + editability semantics for hunt start?
- Should the datetime-local input show/accept the admin's **local** time (most intuitive)
  or UTC? Whichever we pick, display and parse must agree (the current bug is that they
  disagree).
- #8 currently makes URL/start **uneditable** from the web. Is that intended (URL/start are
  Discord-only) or should the web be able to edit them? This decides whether we fix the
  sync to allow editing or deliberately render them read-only.
**Decision needed:** local-vs-UTC, and editable-vs-read-only for URL/start.

## Non-blocking

### Q4 — #6 urgency vs the 30s poll
The dashboard self-heals within 30s via `useHunt` refetch, so #6 is "live push silently
never fires" rather than data loss. Keep it in Phase 2 (tiny fix), or defer? Current plan:
keep — it's cheap and conceptually central.

### Q5 — fold in adjacent MEDIUM/LOWs?
Several mediums share code with a high:
- #28 solve() post-commit-raise ↔ #4 unsolve ordering (same @pushes_board / commit-order shape)
- #17 / #74 lying-ack in feeds_only/remove_from/informs_only ↔ solve/parity work
- #47 update_cell dead `get_puzzle_data` fallback ↔ #2 gsheet work
Fix opportunistically when already in the file, noting it in the commit — but don't let it
expand the diff. **Preference?** (default: opportunistic, off by default.)

### Q6 — mark-off location
Progress is tracked in `implementation-plan.md`'s checklist. Do we also want to annotate
the source review doc (in the main checkout) with a "FIXED in <hash>" note per finding, or
is the plan checklist enough? (default: plan checklist is the tracker; annotate review doc
only at the very end.)
