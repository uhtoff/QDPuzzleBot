# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-06-25
Status: ready for handoff — Milestone D live-test fixes COMPLETE; #3 closed @ 18dd17b. Archived 2026-06-26.

## Goal

Fix the six issues hit while live-testing the Milestone D code-generated sheets
(see current-spec.md for the original detailed framing — note its #1 root-cause
guess, "table over a merged range", was later DISPROVEN; real cause was transient
Sheets 500s).

## Final status per issue — ALL CLOSED

- #1 meta-create 500 — DONE (e09669a). Root cause = transient Google Sheets
  write-path 500s ("Internal error encountered"), NOT geometry/addTable. Fix =
  resilience: generate_puzzle_tab creates the Table best-effort (defers on
  failure); render self-heals via _ensure_contribution_table. Retry-notice
  wording fixed (89c9a2f).
- #2 contribution header alignment (A4:D) — CODE-CORRECT; the only thing left is a
  one-time LIVE VISUAL CHECK. The D4:G4 misalignment was seen only on a stale
  cross-version tab; fresh code creates the Table with columnProperties 0-3 →
  header A4:D. Verify once by creating a meta + feeder and eyeballing the tab.
- #3 promote/convert geometry — DONE this session (18dd17b). promote_tab_to_target
  now ends in the SAME geometry as a generated target (header offset 1 / B1, Table
  at A4): insert 4 cols, unmerge+clear the frozen header, re-stamp the canonical
  header at offset 1 (shared _puzzle_tab_header_requests helper), Table at A4.
  demote_tab_to_puzzle made the exact inverse (regenerates the plain offset-0
  header instead of relying on the old +4/-4 shift symmetry). _header_shift
  simplified to a constant (TARGET_HEADER_SHIFT) — both paths are now uniform.
  flavour/progress/solution preserved both ways. TDD; full suite 451 green.
- #4 bold static labels — DONE (13272f8).
- #5 Unassigned Feeders far-left — DONE (e09669a).
- #6 web parity (meta type selector + convert) — DONE (6486621).

## This session's commits (2026-06-26)

- 7532c15 fix(sheets): name contribution table after its tab (collision fix — a
  second meta's "Contributions" table create failed; names must be unique per
  spreadsheet, so the Table is named after its tab).
- 167bd9b refactor(web): removed the confusing round/subtree/explicit source_mode
  dropdown from the meta view + its orphaned setter plumbing (useSetSourceMode
  hook + SourceMode type + PATCH /api/rollups/<id> route). The source_mode FIELD
  and PuzzleService.set_source_mode stay (bot still sets it internally).
- 18dd17b fix(sheets): #3 promote/demote canonical geometry (see above).

## Final decisions

- Generated + promoted targets share ONE geometry: header offset 1, Table at A4.
  Promote regenerates the header (not shift-preserve), demote regenerates the
  plain header. This was the user's "make them identical" call that resolved the
  old open question below.
- A native Table sharing columns with header merges above it is FINE (proven by
  scripts/diagnose_addtable.py). The earlier column-overlap hypothesis was wrong.

## Rejected approaches

- Header-shift redesign (offset 1 -> 4), committed dfea261, REVERTED ec866b6.
  Built on the disproven "Table can't share columns with a merge" hypothesis.
- For #3, the two discriminator options (detect-from-header / persist-offset) were
  made moot by the user's choice to give both paths identical geometry, so
  _header_shift needs no discriminator at all.

## Remaining forward notes (non-blocking)

- #2: do the one-time live visual check (create meta + feeder, confirm A4:D).
- Optional perf follow-up: cut per-meta Sheets request volume (e.g. the
  whole-sheet font repeatCell) to reduce exposure to transient-500 windows.
- Local diagnostics scripts/diagnose_addtable.py + diagnose_generate.py are
  gitignored local tools (create/clean up throwaway DIAG-* tabs); kept as-is.

## Open questions — RESOLVED

- #3 promote geometry: RESOLVED — both generated and promoted targets use header
  offset 1 + Table at A4; promote/demote regenerate rather than shift. Done in
  18dd17b.
