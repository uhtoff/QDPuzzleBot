# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-06-25
Status: in progress — live-test fixes #1/#4/#5/#6 + retry-notice wording landed; #2 needs live check, #3 open @ 89c9a2f

## Goal

Fix the six issues the user hit live-testing the Milestone D code-generated sheets:
1. 500 on creating a meta (web + bang).
2. Contribution table header text in the wrong place (D4:G4 instead of A4:D4).
3. convert_to_meta clears header rows 2-3, data from row 2 down.
4. Bold the static header labels.
5. Unassigned Feeders tab not in the far-left position.
6. Web dashboard missing create-type + convert options.

## Current status per issue

- #1 DONE (e09669a). ROOT CAUSE = **transient Google Sheets write-path 500s**
  ("Internal error encountered"), NOT geometry/addTable. Proven with scripts/diagnose_*.py:
  the identical batchUpdate (even one trivial repeatCell) flips between all-fail and
  all-pass minutes apart. Log shows 0× 429/quota, 35× 500. Fix = resilience:
  generate_puzzle_tab creates the contribution Table best-effort (logs + defers on
  failure, no longer aborts meta creation); render_contribution self-heals via
  _ensure_contribution_table (creates if missing; skips the write if it still can't, so
  data never clobbers row 0). VERIFIED live: generate_puzzle_tab now returns during a
  flicker instead of raising.
- Retry-notice wording (89c9a2f): _notify_retry said "(rate-limited)" for every retry;
  reworded to "temporary error" since the errors are 500s, not 429s. (This is the
  "Discord claims we're rate-limited by Sheets" the user saw — it was the bot's own text.)
- #2 LIKELY-FIXED, NEEDS LIVE CHECK. The D4:G4 misalignment was seen ONLY on the OLD
  "Second Meta" tab (stale cross-code-version state). Fresh code creates the Table with
  columnProperties at indices 0-3 → header lands A4:D4. Recent metas had NO table (the #1
  500). Verify once a table actually creates (sheet was flickering during the session).
- #3 OPEN. convert_to_meta (promote_tab_to_target) still adds its Table at row 0, inside
  the frozen header → clears rows 2-3. A row-4 fix was attempted in dfea261 but REVERTED
  (bundled with a wrong geometry change). See Open questions.
- #4 DONE (13272f8). _label_bold_requests emits textFormat.bold for the captured labels.
- #5 DONE (e09669a). ensure_unassigned_table pins the tab to index 0.
- #6 DONE (6486621). Frontend parity: meta type selector + capstone/runaround/group
  convert hooks/menu items. Backend routes already existed. tsc clean.

## Final decisions

- Generated + working metas use offset 1 (labels B/I, merges C-R, Table A4:D). Confirmed
  by inspecting the working "Second Meta" tab. Correct — DO NOT shift the header.
- A native Table sharing columns with header merges above it is FINE (proven by
  diagnose_addtable.py). The earlier column-overlap hypothesis was wrong.

## Rejected approaches

- **Header-shift redesign (offset 1 -> 4), committed dfea261, REVERTED ec866b6.** Built on
  the disproven hypothesis that a Table can't share columns with a merge. Real cause of #1
  is transient Sheets 500s.

## Next steps

- Live-verify #2: create a meta (now succeeds), add a feeder, confirm the contribution
  Table header is A4:D4 with data below.
- Decide + implement #3 (see Open questions).
- Optional: diagnostics scripts/diagnose_addtable.py + diagnose_generate.py are untracked;
  keep or delete. They also create/delete throwaway DIAG-* tabs (cleaned up this session).
- Possible follow-up: cut per-meta Sheets request volume (e.g. the whole-sheet font
  repeatCell) to reduce exposure to the transient-500 windows.

## Open questions

- #3 promote geometry: convert_to_meta inserts CONTRIBUTION_WIDTH(4) cols to preserve the
  solver grid, so it uses header offset 4 (≠ generated offset 1) and keeps its Table at
  row 0. Moving it to row 4 (the fix) makes generated and promoted both have the Table at
  row 4, so _header_shift can no longer tell them apart by table-row. Options: (a)
  distinguish by reading the header-label column; (b) persist the offset; (c) make promote
  also offset 1 and not preserve solver columns. Needs a call before implementing.
