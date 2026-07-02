# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-07-02
Status: in progress — fix(web): publish the guild snowflake from board_changed, not the row id (#6) @ 0da35ea

## Goal

Fix the 9 HIGH-severity findings from `docs/reviews/2026-07-02-staging-full-review.md`,
in impact order, marking each off and discussing design questions as we go. Full spec in
`current-spec.md`; ordered plan + finding tracker in `implementation-plan.md`.

## Current status

Phase 1 and Phase 2 complete (4 of 9 findings landed): #1, #3, #5, #6. Full suite green
(751 passed) after #6. Phase 3 (#7+#8, AdminSettingsPanel.tsx) is next and needs Q3
(timezone + editability semantics) discussed with the user first.

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

## Rejected approaches

- Fixing in file order or all-at-once — chose impact-ordered tranches instead.
- Handing off to a separate/cheaper agent — this is a collaborative fix session in-place,
  not a bounded delegate-and-forget handoff.
- #3: plain "clear the table block and rewrite current rows" (original option (b)) — rejected
  because it can misattribute user-added right-of-table columns after compaction (see above).

## Next steps

1. Discuss Q3 (#7/#8: local-vs-UTC display, and whether URL/start should be web-editable)
   with the user, then implement #7+#8 together (Phase 3) — they share how `start_local` is
   derived/synced.
2. Phase 4 (#2+#4, archive-spreadsheet desync) and Phase 5 (#62, web solve parity) follow
   per `implementation-plan.md`.

## Open questions

See `open-questions.md`. Q1 and Q2 both answered. Still blocking its phase: Q3 (#7/#8
timezone + editability). Non-blocking: Q4–Q6. Phase ordering confirmed by user.
