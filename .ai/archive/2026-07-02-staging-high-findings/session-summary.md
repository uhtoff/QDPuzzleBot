# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-07-02
Status: ready for handoff — docs(ai): record dead puzzle_solved notifier path removal @ 1183eb8 (task complete, archiving)

## Goal

Fix the 9 HIGH-severity findings from `docs/reviews/2026-07-02-staging-full-review.md`,
in impact order, marking each off and discussing design questions as we go. Full spec in
`current-spec.md`; ordered plan + finding tracker in `implementation-plan.md`. After all 9
landed, the user asked to additionally fold in #28/#79/#80 (adjacent MEDIUM/LOW findings
sharing code with the HIGHs just fixed) — see "Folded in" section of
`implementation-plan.md`.

## Current status

All 9 HIGH findings landed and committed: #1, #3, #5, #6, #7, #8, #2, #4, #62. Also folded
in #28 (MEDIUM, own-sheet failure isolation in solve/change_solution/add_solution) and
#79+#80 (LOW, dedupe the solved embed formula + the change/add-solution bang command tail).
While working #79, found and removed a dead code path the review hadn't flagged as
unreachable (see Final decisions). Backend suite green (766 passed throughout — the dead
code removal deleted its own test coverage 1:1, no net count change). `ruff check .` clean
across the whole repo. All in-scope work is done; remaining items are process/wrap-up only
(see Next steps).

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

- #2 fix: `restore_puzzle_spreadsheet`'s additional-sheets move-back was gated on
  `puzzle_data.solved`, but the only production caller (`unsolve`) already clears
  `puzzle.solved = False` before calling restore — dead branch. Extra sheets never moved
  back from the archive, so the unconditional update_cell/move/revert requests for them then
  targeted the live spreadsheet's batchUpdate with an archive-only sheetId, failing the
  entire atomic batch (400 "no sheet with id"), including the main tab's own restore. Fixed
  by capturing `was_archived = puzzle_data.archived` before the main-tab move-back mutates
  it, and gating the additional-sheets move-back on `was_archived` instead of `.solved` —
  extra sheets always come back when the puzzle itself was archived (they only ever went to
  the archive as a side effect of the puzzle being archived, per `archive_puzzle_spreadsheet`
  moving every additional sheet unconditionally).
- #4 fix: `unsolve` committed the puzzle (`PuzzleJsonDb.commit`) BEFORE calling
  `restore_puzzle_spreadsheet`, which is what actually mutates `puzzle.archived`,
  `puzzle.google_page_id`, and each additional sheet's `google_page_id` (old archive tab is
  deleted as part of the move). So the commit persisted stale pre-restore values, and no
  second commit existed after — the bang path only survived by luck (`cog_after_invoke`
  commits the same ctx object again after the command returns), but the web unsolve endpoint
  has no such rescue and left the DB with `archived=1` plus a dangling archive sheetId.
  Reordered to mirror `solve()`: mutate the sheet first (`restore_puzzle_spreadsheet`), then
  commit the puzzle and every additional sheet (`SheetsJsonDb.commit`) after. Persistence now
  lives inside the service method itself, not dependent on the caller committing.
- Added `test_restore_puzzle_spreadsheet_moves_additional_sheets_back_when_archived`
  (`tests/test_sheet_ordering.py`) and
  `TestUnsolveService::test_commits_after_sheet_restore`
  (`tests/test_minimeta_service.py`); both verified red against the pre-fix code (reverted
  via `git stash`, reran, confirmed failure, restored).
- #62 fix: `bot/web/actions.py`'s `solve_puzzle` route only posted a Discord embed +
  refreshed the pinned info for `mode == "full"`; the `partial`/`change`/`add` modes ran the
  service mutation (DB + sheet correctly updated) but left the puzzle channel silent and the
  pin showing the OLD solution — a user-visible wrong answer, and a violation of the
  bang/web parity principle ([[web-dashboard-command-parity]]). Added
  `Puzzles.web_post_solution_update_embed(guild, hunt, puzzle, mode)` mirroring the three
  bang embeds (`Partially SOLVED!` / `PUZZLE SOLUTION UPDATED!` / `EXTRA PUZZLE SOLUTION
  ADDED!`) and calling `web_refresh_info`; wired into the route's `else` branch alongside the
  existing full-solve path. Deliberately does NOT reposition the channel (unlike
  `web_post_solved_embed`/`web_post_unsolved`) since these three modes don't change solved
  state, and does NOT call `_refresh_parent_minimeta_pin` — only `solve()`/`unsolve()` touch
  minimeta rollup state (no bang equivalent exists for partial/change/add either). Added
  `tests/test_web_solution_update_message.py` (4 tests: one per mode + a mini-thread case),
  following the existing `test_web_unsolve_message.py` pattern of unit-testing the cog method
  directly rather than the Quart route (no existing route-level tests for this endpoint to
  extend, and that's the established depth for this code path).

- #28 fix: `solve()`'s `archive_puzzle_spreadsheet` call and `change_solution`/
  `add_solution`'s `update_solution` call were the only Sheets writes in these three
  methods NOT wrapped in the failure-isolation pattern already used for meta refreshes
  (try/except + append to `failed` + keep going) — an uncaught Sheets error propagated past
  the second commit, the forum-tag update, and `@pushes_board`'s push, even though the DB
  mutation was already committed. Wrapped each in the same pattern, appending `puzzle.id` to
  the existing `failed` list (reusing `SheetRefreshError`'s existing contract rather than
  inventing a second exception type — its `failed_round_ids` field is only ever used to
  build a display string, not looked up structurally, so a puzzle id alongside round ids is
  functionally safe even though the field name is now slightly imprecise). Added
  `tests/test_sheet_failure_isolation.py` (3 tests); verified red via `git stash` (import
  works pre-fix — the failure showed as the exception propagating and `board_changed` never
  being awaited).
- #79/#80 fix: extracted `bot.core.notifier.build_solved_embed(emoji, puzzle, follow_up)` as
  the one shared "PUZZLE SOLVED!" title/description formula (was copied in `_solve_impl`,
  `web_post_solved_embed`, and notifier.py's cog-unavailable fallback); each caller still
  supplies its own Follow-up text (bang vs. dashboard copy is deliberately different, per the
  review's own note). Merged `change_solution`/`add_solution`'s near-identical ~30-line bang
  command tails into one `_change_or_add_solution_impl(ctx, answer, mode=...)`, and added the
  Follow-up field `add_solution`'s embed had been missing (the divergence #80 flagged as a
  sign the copies were already drifting). Added `tests/test_change_add_solution_impl.py`
  (6 tests); verified red via `git stash` (import error — `build_solved_embed` didn't exist).
- Dead-code follow-up (user asked to clean it up after the #79/#80 summary flagged it):
  `DiscordNotifier.puzzle_solved` / `Notifier.puzzle_solved` / `WebNotifier.puzzle_solved` /
  `CompositeNotifier.puzzle_solved` and the `_post_solved_embed` cog-unavailable fallback were
  never called from anywhere outside `notifier.py` (grepped `.puzzle_solved(` across the whole
  repo) — the live SOLVED-embed push is `Puzzles.web_post_solved_embed`, called directly by
  `bot/web/actions.py` and the bang `!solve` command, never through the `Notifier` interface;
  the live push path is `board_changed` (Phase B). The 2026-07-02 review flagged this exact
  fallback's duplication (#79) and a fire-and-forget bug in it (#88, unaddressed — moot now
  the code is gone) but never identified the whole path as unreachable — that was found
  independently via grep, not in the review. Removed the method from the interface and all
  four implementations, plus `_post_solved_embed` itself (`build_solved_embed` stays — still
  used by the two live callers). Updated `tests/test_notifier.py`, `test_web_notifier.py`,
  `test_board_push_service.py` to drop the now-removed method's assertions/fixture lines; no
  new tests needed (removing dead code needs no new coverage). Verified: 766 passed
  throughout (removing the dead path's own coverage was a wash).

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

All 9 HIGH findings, the folded-in #28/#79/#80, and the dead `puzzle_solved` cleanup are
implemented, tested, and committed. Full suite (766 passed) and `ruff check .` (repo-wide)
both green. Remaining before closing the task:
1. Decide Q6 (annotate the source review doc with FIXED-in-hash notes, or leave the plan
   checklist as the sole tracker).
3. Ask the user whether to archive `.ai/active/` under
   `.ai/archive/2026-07-02-staging-high-findings/` now that the task is done.

## Open questions

See `open-questions.md`. Q1, Q2, Q3 all answered. Non-blocking: Q4–Q6 (not yet decided, but
don't block any remaining finding). Phase ordering confirmed by user.
