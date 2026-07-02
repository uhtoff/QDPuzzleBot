# Implementation Plan — HIGH findings (2026-07-02 staging review)

## Finding tracker (mark off as we fix)

Single source of truth for progress. Tick the box the moment a finding is committed, and
record the commit hash next to it. `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` deferred.

- [x] **#1**  puzzles.py:2917 — archive_channel attachment/embed loss        — commit: b0225d6
- [x] **#3**  puzzles_gsheet.py:1202 — reconcile_table row-delete grid loss   — commit: 3ed8ae1
- [ ] **#5**  puzzle_service.py:1073 — convert_to_puzzle IntegrityError        — commit: ____
- [ ] **#6**  notifier.py:22 — board_changed snowflake vs row id              — commit: ____
- [ ] **#7**  AdminSettingsPanel.tsx:75 — hunt start timezone shift           — commit: ____
- [ ] **#8**  AdminSettingsPanel.tsx:88 — render-phase sync uneditable/wipe   — commit: ____
- [ ] **#2**  puzzles_gsheet.py:513 — restore dead move-back + wrong sheet    — commit: ____
- [ ] **#4**  puzzle_service.py:1012 — unsolve commit ordering (web loses)    — commit: ____
- [ ] **#62** actions.py:361 — web solve modes skip embed + pin refresh       — commit: ____

## Progress protocol (per finding)

Follow this loop for every finding so nothing slips:

1. **Discuss** — if the finding has an entry in `open-questions.md`, resolve it with the
   user before writing code. Don't guess a design where a real choice exists.
2. **Confirm anchor** — re-read the cited lines; the review is accurate against this base
   but files may have been touched since the last commit.
3. **Fix** — smallest change that resolves the finding; hit both entrypoints where the
   finding is dual-path.
4. **Verify** — run the focused test(s) for that area under `timeout`, output to a file
   (see AGENTS.md test guidance). Add/adjust a test where the review says the bug was
   untested (several note "tests mock the store, so this is never exercised").
5. **Mark off** — tick the box above, add the commit hash, bump `session-summary.md`
   Status, update `changed-files.md`.
6. **Commit** — one finding (or a coupled pair) per commit, clear message referencing the
   finding number, e.g. `fix(archive): preserve attachment/embed-only messages (#1)`.

## Phasing (impact order)

Ordering rationale lives in `session-summary.md`. Tranches, not hard gates — reorder if a
design answer changes priorities.

### Phase 1 — silent, irreversible data loss
- **#1** archive_channel attachment/embed loss
- **#3** reconcile_table grid destruction

These are the only findings where the user loses work permanently and silently. #3 needs
the most design thought (how to reconcile the table without row deletion) — see open Qs.

### Phase 2 — breaks-outright / sweeping, contained fixes
- **#5** convert_to_puzzle IntegrityError (every meta demote fails, both paths)
- **#6** board_changed snowflake (all live board pushes dropped; ~1-line fix, but confirm
  where the snowflake is available inside WebNotifier)

### Phase 3 — frontend admin settings (fix as one pair)
- **#7** + **#8** AdminSettingsPanel — do together; #7's TZ fix and #8's derived-state fix
  interact (both touch how `start_local` is derived/synced).

### Phase 4 — unsolve / archive-spreadsheet desync
- **#2** + **#4** — same code path; fix together. Archive spreadsheets ARE used (bigger
  hunts), so this must work — in scope at this position, not deferred (Q2 answered).

### Phase 5 — web solve parity
- **#62** web solve modes skip embed + pin refresh. Natural to do while in the solve path;
  watch the bang/web parity seam ([[dual-solve-delete-entrypoints]]).

## Per-finding acceptance

- **#1** — attachment-only and embed-only messages are re-sent to the archive thread
  (files + embeds) before the source channel is deleted; a message with truly nothing
  (no content, no attachments, no embeds) is skipped without error.
- **#3** — a re-key/prune of a contribution row no longer shifts cells in cols E+ of the
  target's own tab; verify against real Sheets range semantics (deleteDimension ROWS is
  full-width).
- **#5** — convert_to_puzzle (bang + web demote) clears the `target` self-edge from
  `tag_edges` so the subsequent commit's tag-sync inserts nothing against the deleted
  group; demote succeeds end-to-end.
- **#6** — board_changed events carry the guild snowflake and pass the ws_activity
  membership filter; a connected dashboard receives live board invalidation.
- **#7** — displayed hunt start time matches the stored timestamp for non-UTC users, and a
  Save that doesn't touch start time does not shift `start_timestamp`.
- **#8** — URL/start fields are editable (or intentionally read-only by design decision),
  and a background refetch mid-edit does not wipe in-progress username/password edits.
- **#2/#4** — after a web unsolve of an archived puzzle, the DB reflects archived=False and
  the live sheetId; a later re-solve/rename does not hit a dangling archive sheetId.
- **#62** — web change/partial/add post the channel embed and refresh the pinned info, so
  Discord no longer shows a stale/old solution.

## Verification (task-level)

- Focused pytest per area as fixes land (bootstrap config first, [[worktree-bootstrap-config]]).
- Full suite (parallel, compact, under timeout) at the phase boundary before considering
  the task done.
- `ruff check` before final (CI does not run it — finding #87).

## Rollback / recovery notes

- One finding per commit → revert is per-finding.
- Data-loss fixes (#1, #3) touch destructive paths (channel delete, sheet row delete):
  prefer to prove the fix on a scratch hunt/sheet before trusting it in a real hunt.
