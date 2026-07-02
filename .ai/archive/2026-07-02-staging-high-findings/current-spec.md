# Current Spec

## Goal

Fix the HIGH-severity findings from the 2026-07-02 full staging review
(`docs/reviews/2026-07-02-staging-full-review.md`, in the main checkout at
`/home/russ/projects/puzzbot/docs/reviews/`). Work through them in impact order,
marking each finding off as it lands and pausing to discuss design questions before
implementing anything with a real design choice.

## In scope

The **9 HIGH-tagged findings** (the review summary says 8; it miscounts — #62 in the
parity section is also HIGH):

- #1  `bot/cogs/puzzles.py:2917` — archive_channel drops attachment/embed-only messages, then deletes the channel (irreversible data loss)
- #3  `bot/cogs/puzzles_gsheet.py:1202` — reconcile_table deleteDimension over whole ROWS destroys the solver grid on re-key/untag/capstone/evidence-delete
- #5  `bot/core/puzzle_service.py:1073` — convert_to_puzzle leaves the `target` self-edge → IntegrityError; every meta demote (bang + web) fails
- #6  `bot/web/notifier.py:22` — board_changed publishes guilds row id, not the Discord snowflake; every board push is dropped (self-heals via 30s poll)
- #7  `frontend/src/components/AdminSettingsPanel.tsx:75` — hunt start time round-trips through mismatched timezones; every Save shifts start by the UTC offset
- #8  `frontend/src/components/AdminSettingsPanel.tsx:88` — render-phase sync makes URL/start uneditable and wipes in-progress edits
- #2  `bot/cogs/puzzles_gsheet.py:513` — restore_puzzle_spreadsheet dead move-back + wrong-spreadsheet batch on unsolve (archive-spreadsheet only)
- #4  `bot/core/puzzle_service.py:1012` — unsolve commits before restore, never after; web unsolve loses restore state (archive-spreadsheet only)
- #62 `bot/web/actions.py:361` — web solve modes partial/change/add skip the channel embed + pin refresh; Discord shows the OLD solution

## Out of scope (this task)

- All MEDIUM and LOW findings (49 + 33). Some are cheap to fix alongside a related HIGH
  (e.g. #28 solve() post-commit-raise shares #4's shape; #74 the feeds/informs command
  family shares #17's lying-ack). Fold those in **only** when touching the same code and
  after noting it — do not scope-creep.
- The known-intentional quirks excluded from the review by construction (stale forum
  layout, manual hunt-role seam, tag/edge wording, vestigial pool wording).

## Relevant files

- `bot/cogs/puzzles.py` — archive family (#1), solve/announce helpers
- `bot/cogs/puzzles_gsheet.py` — reconcile_table (#3), restore/archive spreadsheet (#2)
- `bot/core/puzzle_service.py` — convert_to_puzzle (#5), unsolve (#4)
- `bot/web/notifier.py` — board_changed (#6)
- `bot/web/actions.py` — web solve modes (#62), web unsolve (#4)
- `frontend/src/components/AdminSettingsPanel.tsx` — settings panel (#7, #8)

## Required behaviour

Per-finding acceptance is in `implementation-plan.md`. The overriding rules:

- No regression to the bang path while fixing the web path (or vice versa) — several of
  these have dual entrypoints (see [[dual-solve-delete-entrypoints]]).
- Data-loss fixes (#1, #3) must be verified against the actual Sheets/Discord API
  semantics, not just unit tests that mock the store.

## Constraints

- Base is `staging`; this worktree (`working-1`) sits exactly on it (tip `bed73c9`), so
  every line anchor in the review is accurate as-is. Re-confirm the anchor before each edit
  anyway (small, cheap).
- Tests can't run until the gitignored `config.json` + `google_secrets.json` are copied
  in from the main checkout (see [[worktree-bootstrap-config]]) — do this before the first
  fix, not now.
- Keep diffs narrow and reviewable; one finding (or one tightly-coupled pair) per commit.

## Acceptance criteria

- Each of the 9 highs is either fixed (box ticked, focused test run, committed) or
  explicitly deferred with a recorded reason.
- Every design question in `open-questions.md` that blocks a finding is resolved with the
  user before that finding is implemented.
- `changed-files.md` reflects reality at each stage boundary.
