# Open Questions

Resolve the blocking ones with the user **before** implementing the associated finding
(per the progress protocol). Move answers inline as they're decided.

## Blocking

### Q1 — #3 reconcile_table: how to prune without destroying the grid? — ANSWERED (2026-07-02)
The bug is `deleteDimension {dimension: ROWS}` deleting the full-width row. Rejected a plain
"clear the block and rewrite from the top" (b): if pruned rows are compacted, a row's
identity can be handed to a different key on the next reconcile, silently misattributing any
note a user added in a column to the right of the table (nothing is deleted, but it now sits
next to the wrong puzzle).

**Decision: refined (b)** — never call `deleteDimension`/`insertDimension` on rows at all.
- Prune: blank only the table's own columns (`key_col`..`key_col+data_col_count`) in place;
  leave the row where it is.
- Append: always write after the highest row currently in use; never reuse a blanked row.
- A row's identity is therefore permanent once assigned — anything a user adds beside the
  table stays correctly associated. The table only grows over a reconcile's lifetime; the
  user can manually sort the table's range in Sheets to reclaim blank rows (their call, not
  silent bot behaviour).

### Q2 — #2/#4: is an archive spreadsheet used in production? — ANSWERED (2026-07-02)
**Yes** — archive spreadsheets are used for bigger hunts, so #2/#4 must work correctly.
Phase 4 stays in scope at its planned position (not deprioritized).

### Q3 — #7/#8: intended timezone + editability semantics for hunt start? — ANSWERED (2026-07-02)
- **Timezone:** a UTC-offset picker (not a full IANA timezone dropdown — user confirmed
  "UTC +/- fine"). Added a `Select` of UTC-12:00..UTC+14:00 in half-hour steps next to the
  Start time field, defaulting to the browser's current offset. Conversion is manual offset
  arithmetic (`Date.UTC(...) - offsetMinutes*60000`), no new date library needed.
- **Editability:** confirmed a bug, not a design choice — url/start were already wired up
  with onChange + a Save button + `useUpdateHuntSettings`, so the intent was always
  web-editable. The "uneditable" symptom was the render-body derived-state-sync snapping
  edits back on every keystroke (see #8 fix below).

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
