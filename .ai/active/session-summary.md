# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-06-25
Status: in progress — fixes landed on main; awaiting live verification @ 6318d3c

## Goal

Fix the Milestone D code-generated-sheets breakage the user hit live: meta
creation 500 / "Google Sheets had an error", missing web-hunt welcome, the
Unassigned Feeders table not populating, decorative table headers, lost header
formatting. See `.ai/active/current-spec.md` (authoritative) for full detail.

## Current status — six defects, all addressed (commits on main)

1. e15ec9e  Web hunt create posts + pins the welcome message (parity with !newhunt).
2. cac4aa9  Rename "Unassigned Puzzles" -> "Unassigned Feeders" (tab title, Table
            name, bang strings; old title kept in RESERVED_TAB_TITLES).
3. 92ae7d8  Reconciler rework: numeric Length cells (no batch rejection), single
            diff (service passes full desired; reconcile_table is the only diff),
            Table resize so rows belong to it, adaptive header-row/key-col, keys
            matched as strings. Retired read_unassigned_keys /
            apply_unassigned_changes / _unassigned_len_cell.
4. a46f44b  Contribution table at row 4; target header shifted 1 col so col A is
            the hidden key; fixes the merge-overlap crash. _header_shift reads the
            Table's real position so generated (row4/shift1) AND promoted
            (row0/shift4) targets write correctly.
5. fff488b  Tab formatting for all tabs: JetBrains Mono, frozen+grey 3 header rows,
            wide label column; solution cell green on solve / grey on unsolve.
6. 6318d3c  Resize preserves the Table's column span (keep Unassigned Notes col).

Tests: 441 passed; ruff clean.

## Final decisions

- Target meta tab layout (user-confirmed): A1-3 empty (hidden key col), B holds
  the labels (Title:/Flavor Text:/Progress:), values to the right, contribution
  Table at row 4 cols A-D (key col A hidden).
- Row 4 everywhere EXCEPT the Unassigned Feeders + Evidence tabs.
- Solution cell bright green only when solved.
- promote/demote kept on their existing 4-col-shift geometry (table at row 0) to
  avoid a blind rewrite of the grid-preserving path; _header_shift detects the
  offset so writes stay correct. See Rejected approaches.

## Rejected approaches

- Rewriting promote_tab_to_target/demote to the 1-col-shift + row-4 geometry:
  in-place solver-grid preservation is incompatible with a row-4 table that grows
  (table cols A-D would overwrite the grid). The old 4-col-shift keeps table and
  grid in disjoint columns. Left as-is; only the detection in _header_shift bridges
  the two layouts. CANNOT be live-verified here.

## Next steps (live verification — needs a real Google Sheet)

- Create a hunt on web -> welcome message pinned in the hunt channel.
- Create a meta on web AND via !mp/!r -> succeeds (no 500 / Sheets error), tab is
  JetBrains Mono, grey frozen 3-row header, labels in B, contribution Table at row
  4 with col A hidden.
- Add feeders -> they appear AS Table rows on the meta tab; the Unassigned Feeders
  table populates and the header is a real Table (sortable), Notes column intact.
- Solve a puzzle -> solution cell turns green; unsolve -> back to grey.
- Convert an existing feeder to a meta (!convert_to_meta) -> promote path still
  works (table at row 0, grid preserved); solving it places solution correctly.

## Open questions

- Do existing (already-created) hunts need their Unassigned tab renamed in place?
  Current change only affects newly created tabs (lookup is by page id).
- Should promote/demote be unified to the row-4 layout later (cosmetic
  consistency with fresh metas)? Deferred.
