# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-06-23
Status: ready for handoff — M7 handoff: add tags UNIQUE key (005) + upsert edge writer @ 0c2f282

## Goal

Implement M7 — activate the dormant M6/M5 typed-group substrate so a roll-up sheet
aggregates different membership by a per-rollup `source_mode` (`round`/`subtree`/`explicit`),
with `tags.edge_type` becoming live. The acceptance is the **runaround**: the same answer
set producible three ways.

## Context the executing agent needs (not obvious from files)

- **`docs/PLANS.md` M7** is the roadmap entry; `.ai/active/current-spec.md` is the
  authoritative phased execution plan (read it first). The source plan
  `/home/russ/.claude/plans/can-we-plan-m7-sparkling-newell.md` is background only.
- **All schema M7 needs already exists** (migrations `002`+`003`): `source_mode`,
  `edge_type_filter`, `parent_group_id`, `tags.edge_type`. M7 is mostly *code activating
  dormant columns*. New SQL is small: `004` (data-only `source_mode` backfill) and `005`
  (dedup + `UNIQUE (round_id, puzzle_id)` on `tags`) — no new columns.
- **Two confirmed scope decisions:** (1) the new SPA aggregated-answers *view* +
  copy-to-clipboard is **M7.5, out of scope here**; the setter controls ARE in scope. (2)
  `source_mode` **supersedes** the `metameta` flag — after the 004 backfill, `refresh_meta`
  branches on `source_mode`, never on `metapuzzle.metameta`.
- **Model split:** Phase 1 (write all failing tests) on **Opus 4.8**; Phases 2-6
  (implementation) on **Sonnet 4.6**.
- Test runner is **`pipenv run python -m pytest tests/ -q`** (plain `pytest` breaks
  `import bot`); tests open MySQL at import via the gitignored `config.json`
  (`puzzlebot_test`).

## What NOT to touch

- Do not retype `PuzzleData.tags` (stays `List[int]`; add a parallel `tag_edges` map).
- Do not thread `edge_type` through the generic `commit()` tag set-difference logic or the
  solve/retag loops — use the separate `tag_puzzle_with_edge` upsert writer (relies on the
  `005` `UNIQUE KEY (round_id, puzzle_id)`).
- Do not create channel-less groups or touch the `get_by_attr(category_id)` call sites.
- Keep `add_metapuzzle_data` / `add_metametapuzzle_data` intact — existing tests mock them.
- Keep edits in `bot/cogs/puzzles.py` (the 2,700-line god-cog) focused; new logic in
  `bot/core/`.

## Final decisions

- Engine + Discord/SPA setters in M7; aggregated-answers VIEW in M7.5.
- `metameta` flag retired as a runtime branch (backfilled to `source_mode='subtree'`).
- Setters exposed in BOTH Discord (`!nest`/`!source_mode`/`!pool`) and the SPA.
- `tags` gets `UNIQUE (round_id, puzzle_id)` (migration `005`) — membership is one edge per
  pair, matching the `tag_edges` dict; done now while all rows are still `feeds-meta` so the
  required dedup is information-safe. Edge writer is a clean upsert.

## Rejected approaches

- A permanent "legacy-metameta" bridge that keeps reading `metapuzzle.metameta` at runtime
  (rejected — `source_mode` supersedes the flag; use the 004 backfill instead).
- A new migration adding columns (rejected — `002`/`003` already shipped them; `004`/`005`
  are data/index-only).
- SELECT-then-write for the edge writer (superseded — add `005` UNIQUE key + upsert instead;
  doing the dedup now is information-safe because every existing tags row is `feeds-meta`).
- `UNIQUE (round_id, puzzle_id, edge_type)` (rejected — the model is one edge per pair; that
  3-col key + edge_type-aware `commit()` diffing is only needed if a puzzle ever needs two
  edge_types into the *same* group, which the model doesn't require — deferrable).

## Next steps

1. Branch `feature/m7-flexible-aggregation` already exists (off local `main`); these handoff
   files are committed on it. Check it out and start there.
2. Execute `.ai/active/current-spec.md` Phase 1 (Opus): write the four failing test files,
   confirm they fail for the right reason, commit. Then Phases 2-6 (Sonnet) in order.

## If you get stuck

Diagnose with `superpowers:systematic-debugging` FIRST. A test failing because a
method/field doesn't exist yet is correct; an import/DB-bootstrap fatal or a wrong column
means a bad test assumption — fix the test. Two-strikes rule: if an acceptance command
fails twice without an understood fix, write `.ai/active/blocker.md` and STOP. In
particular, flag in `blocker.md` (don't improvise) if: you can't reach `puzzlebot_test`; a
seeded metameta round's `subtree` view regresses vs the old hunt-wide scan (may need a
one-time nesting backfill); or the `explicit`-pool renderer shape feels wrong.
