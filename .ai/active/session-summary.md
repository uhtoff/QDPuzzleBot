# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-07-02
Status: in progress — fix(web): hunt-start timezone picker + stop wiping in-progress edits (#7, #8) @ 1740bab

## Goal

Fix the 9 HIGH-severity findings from `docs/reviews/2026-07-02-staging-full-review.md`,
in impact order, marking each off and discussing design questions as we go. Full spec in
`current-spec.md`; ordered plan + finding tracker in `implementation-plan.md`.

## Current status

Phases 1-3 complete (6 of 9 findings landed): #1, #3, #5, #6, #7, #8. Backend suite green
(751 passed) as of #6; frontend verified via `tsc --noEmit` + `vite build` (both clean) plus
a standalone Node script confirming the UTC-offset round-trip math — not independently
browser-tested (no live Quart backend/auth wired up in this worktree, and no frontend test
runner exists in the repo to add an automated regression test). Phase 4 (#2+#4,
archive-spreadsheet desync) is next — no blocking open questions.

## Final decisions

- Scope = the 9 HIGH findings only (#1,#3,#5,#6,#7,#8,#2,#4,#62). Review summary says
  "8 high" but miscounts — #62 (parity section) is also HIGH.
- Ordering by impact: data loss → breaks-outright/sweeping → frontend pair → archive-sheet
  desync → web solve parity. See `implementation-plan.md` phasing.
- One finding (or coupled pair #2+#4, #7+#8) per commit; tick the tracker + record hash +
  bump this Status line on each landing.
- Discuss design questions (`open-questions.md`) before implementing the finding they block.
- #1 fix: trigger archiving on content OR attachments OR embeds. Added
  `tests/test_archive_channel.py` (no prior test file existed for archive_channel).
- #3 fix (Q1 resolved): never `deleteDimension`/`insertDimension` rows in reconcile_table.
  Prune blanks the table's own columns in place; appends always land after the highest row
  in use, never reusing a blanked row. Rejected a plain "clear block + rewrite from top"
  because compaction can hand a row's identity to a different key, silently misattributing
  any user-added column to the right of the table. User confirmed: table only grows over
  time; a manual sort in Sheets reclaims blank rows — acceptable trade-off, no bot-side
  compaction needed. Updated `tests/test_contribution_table_requests.py`'s
  `test_reconcile_table_appends_updates_prunes` to match (was asserting `deleteDimension`).
- #5 fix: convert_to_puzzle popped the deleted round from `puzzle.tags` but left it in
  `puzzle.tag_edges` (the `'target'` self-edge), so the next `PuzzleJsonDb.commit`'s tag-sync
  tried to INSERT against the deleted tag_group → IntegrityError. Now pops the group from
  `tag_edges` too. Added `test_removes_round_from_tag_edges`; verified it fails without the
  fix (reverted the source, reran, confirmed red, restored).
- #6 fix: `hunt.guild_id` is the `guilds` row id (FK), not the Discord snowflake — the
  snowflake is `guilds.guild_id` (per `migrations/000_base_schema.sql`). WebNotifier was
  publishing the row id directly, so `board_changed` events never matched the ws_activity
  membership filter in `api.py`, silently dropping every live board push (self-heals via the
  dashboard's 30s poll). Fixed by resolving through `data.get_guilds_map()`, mirroring
  `actions.py`'s existing `_board_event` helper. The old test asserted the buggy
  pass-through behaviour; rewrote it to mock `get_guilds_map` and assert the resolved
  snowflake, plus added a case for an unknown guild row (→ `guild_id: None`, matching
  `_board_event`'s convention).
- #7/#8 fix (Q3 resolved): a UTC-offset `Select` (UTC-12:00..UTC+14:00, half-hour steps,
  default = browser offset) next to the Start-time field — user confirmed a coarse UTC
  offset is fine, no need for a full IANA timezone library/dependency. Display and parse
  both go through explicit offset arithmetic so they always agree (the bug was
  `toISOString()` for display vs. browser-implicit `new Date(str)` for parsing). Root cause
  of #8 was doing "sync form from parent" as an if-check + `setValues` in the render body
  (not an effect) — it fired on every render, including the one caused by the user's own
  keystroke, immediately reverting the edit; also wiped username/password on the 30s
  background poll. Replaced with a `useEffect` that only re-syncs when `uid` changes (i.e.
  switching hunts), not on every refetch of the same hunt. Confirmed url/start were always
  meant to be web-editable (Save button + mutation already wired up) — no real "read-only
  by design" question remained once the render-body bug was identified.

## Rejected approaches

- Fixing in file order or all-at-once — chose impact-ordered tranches instead.
- Handing off to a separate/cheaper agent — this is a collaborative fix session in-place,
  not a bounded delegate-and-forget handoff.
- #3: plain "clear the table block and rewrite current rows" (original option (b)) — rejected
  because it can misattribute user-added right-of-table columns after compaction (see above).
- #7: a full IANA-timezone picker (e.g. via a date-fns-tz-style dependency) — user explicitly
  said a UTC offset is sufficient; kept it dependency-free.
- #8: per-field "dirty tracking" against a `lastSynced` baseline (resync any prop that
  changed, but only if the user hasn't locally edited that specific field) — more complex
  than needed for the stated acceptance criteria; resync-only-on-uid-change is simpler and
  fully satisfies "a background refetch mid-edit does not wipe in-progress edits."

## Next steps

1. Phase 4: #2 (`puzzles_gsheet.py:513`, restore_puzzle_spreadsheet dead move-back + wrong
   spreadsheet batch on unsolve) + #4 (`puzzle_service.py:1012`, unsolve commit ordering) —
   same code path, fix together. Archive spreadsheets are in scope (Q2 answered yes).
2. Phase 5: #62 (`actions.py:361`, web solve modes skip embed + pin refresh).

## Open questions

See `open-questions.md`. Q1, Q2, Q3 all answered. Non-blocking: Q4–Q6 (not yet decided, but
don't block any remaining finding). Phase ordering confirmed by user.
