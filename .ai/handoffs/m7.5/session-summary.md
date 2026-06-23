# Session Summary — M7.5 handoff

Tool: Claude Code
Worktree: a NEW worktree branched off M7 (NOT working-1 — that holds the in-progress M7 handoff)
Started: 2026-06-23
Status: ready for handoff — Phase 1 (tests)

## Goal

Add the read counterpart to M7 flexible aggregation: an inline per-meta dashboard view of a
rollup's aggregated answers with one-click copy-to-clipboard, reusing M7's engine selection logic
so the view matches exactly what is written to the Google Sheet.

## Context the executing agent needs (not obvious from files)

- **This depends on M7**, which is still in progress on `feature/m7-flexible-aggregation`. Do not
  start until M7 has landed Phase 4 (`_collect_rollup_puzzles`) and Phase 6 (`source_mode` in the
  `/api/hunts/<uid>` board payload). Branch M7.5 from M7's final state, in a fresh worktree.
- **The pivotal design move:** M7's `_collect_rollup_puzzles` *renders as it selects* (it awaits the
  Google-Sheet cog). A read endpoint must not re-render the sheet, so Phase 2 extracts the pure
  selection half (`select_rollup_members`) that both the render path and the read endpoint share.
  This is the single source of truth that keeps the view consistent with the sheet.
- **Confirmed product decisions** (from the planning conversation): surface is *inline per-meta*
  (not a separate top-level view); data comes from a *server endpoint reusing the engine* (not
  client-side re-derivation); each row shows *name + answer*, and copy yields `name\tanswer` one
  per line.
- Runner: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` (pipenv; tests
  open MySQL `puzzlebot_test` at import via gitignored `config.json`). Frontend gate:
  `cd frontend && npx tsc --noEmit`.
- Model split: Phase 1 (test design) on **Opus 4.8**; Phases 2–3 on **Sonnet 4.6**.

## What NOT to touch

- The Google-Sheet renderers (`add_metapuzzle_data` / `add_metametapuzzle_data`) and M7's
  source_mode *routing behaviour* — only extract the selection half so it can be reused.
- The writers — `source_mode`/nest/pool setters are M7 Phases 5–6, not M7.5.
- `.ai/active/` in `working-1` — that is M7's live handoff; do not overwrite it. (This package
  lives under `.ai/handoffs/m7.5/` on purpose.)
- The read path must perform zero writes: no `commit`, no `refresh_meta`, no rollup-row creation.

## If you get stuck

Diagnose with `superpowers:systematic-debugging` FIRST. If `_collect_rollup_puzzles` /
`refresh_meta` / the board `source_mode` field don't match this spec's assumptions, M7 may not be
finished — reconcile against the real code; if the seam is genuinely absent, write
`.ai/active/blocker.md` (what failed, exact output, your diagnosis) and STOP rather than rebuilding
M7's engine. Same for the web-test convention: if there is no route-test precedent in the suite,
test the assembly helper directly and keep the route thin — don't invent a Quart test-client
harness; write `blocker.md` if unsure.

## Files

Authoritative spec: `.ai/handoffs/m7.5/current-spec.md` (phased; Phase 1 = all failing tests,
Phases 2–3 = implementation). Background plan: `/home/russ/.claude/plans/can-we-plan-7-5-parallel-scroll.md`.
