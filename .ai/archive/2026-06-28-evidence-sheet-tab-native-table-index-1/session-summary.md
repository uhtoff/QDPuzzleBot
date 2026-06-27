# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-06-27
Status: ready for handoff — all 3 phases committed @ 72b8e3b; suite green (619); adversarial review clean (0/7 findings confirmed); not merged; needs live verify on test site

## Goal

Evidence sheet tab should render as a native Table and sit at index 1 (after Unassigned
Feeders), matching the other roll-ups. See `current-spec.md` (authoritative).

## Current status

Diagnosis complete and verified (4-agent workflow + direct code reads). Root cause:
`refresh_evidence` is a bespoke path that never adopted Milestone-D native Tables or the
zone-ordering pin — it writes via `aggregate_to_tab` (flat list) and creates the tab with
no index (appended at end). Fix = mirror the `refresh_unassigned`/`ensure_unassigned_table`
precedent. Scope confirmed: gap in shipped work, NOT the deferred M9b feature.

## Final decisions

- Add hidden leading `id` key column (evidence item id) so `reconcile_table` can diff
  (Source Puzzle name is not unique). data_col_count = 5.
- New `ensure_evidence_table` pins the tab to **index 1** and does a one-time full-tab
  clear (migrates the old A5 flat render cleanly).
- New `hunt.evidence_table_id` column via migration `017`.

## Rejected approaches

- Position-only quick fix (leave as list) — user chose the full fix.
- Reusing `render_contribution` — it's target/CONTRIBUTION_HEADER-specific.
- Keeping `aggregate_to_tab` for the data write + a separate table wrapper — `reconcile_table`
  is the established shared updater; reuse it.

## Next steps (TDD, commit per phase)

1. HuntData `evidence_table_id` field + migration `017_evidence_table.sql`.
2. `ensure_evidence_table` cog method (RED tests first in test_contribution_table_requests.py).
3. Rewrite `refresh_evidence` (rewrite TestRefreshEvidence to new contract first).
4. Run focused tests + ruff; commit.

## Open questions

- REFERENCE tab position: not pinned by code (only in RESERVED_TAB_TITLES). Pinning Evidence
  to index 1 should yield `Unassigned Feeders | Evidence | REFERENCE`. Verify on live sheet.
