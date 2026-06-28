# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1  (branch: feature/multi-edge-membership)
Started: 2026-06-28
Status: COMPLETE — all 10 phases implemented @ 4de50d6

## Goal

Make `(puzzle, tag_group)` memberships multi-edge so a puzzle can both `feeds` and `informs` the
same target, and make the evidence-board link auto-add an `informs` edge that never evicts `feeds`.

## Current status

Design + plan are committed on `main` (specs `243bdb4`, plan `9637e94`) and present in this
worktree. **Phase 1 (tests) is done** — five test files written/converted and committed @ c442e69;
each fails for the right reason (missing `add_edge`/`has_edge`/`link_informs_for_evidence`,
set-vs-string assertions, `untag_from` lacking an `edge` kwarg) with no collection errors. Next:
Phase 2 (Sonnet 4.6) onward. Execute `.ai/active/current-spec.md` phase by phase — **it is
authoritative**. Phases 2–10 apply the exact code in
`docs/superpowers/plans/2026-06-28-multi-edge-membership.md`.

## Context the files don't make obvious

- **Two unrelated `target_puzzle_id` columns exist.** Only `tag_group.target_puzzle_id` is the meta
  target — and it is OUT OF SCOPE here. `evidence_items.target_puzzle_id` / `rollups.target_puzzle_id`
  are separate; do not touch either.
- **The whole point of this change** is that a single membership row can no longer hold one edge;
  `edge_type` moves into the unique key so a pair can have several rows. In memory that becomes
  `tag_edges: Dict[int, set]`. Every `p.tag_edges.get(gid) == "x"` reader must become
  `p.has_edge(gid, "x")`; every `p.tag_edges[gid] = "x"` writer must become `p.add_edge(gid, "x")`.
- **The suite is fully mocked** (no live DB/Discord). The migration is NOT needed for pytest to pass
  — only for the manual smoke. DAOs are patched at the *service* import site
  (`patch("bot.core.puzzle_service.PuzzleJsonDb")`), not `bot.store`.
- **Board-API shape change ripples** beyond the frontend: `bot/web/data.py` `derive_meta_kind`
  (`:290`), `:416`, `:444` all read `rounds[].edge_type`, as does `DragBoard.tsx:87`. Phase 6/7 fix
  them. `tests/test_meta_kind.py` builds round dicts by hand with `edge_type` — its fixtures change
  in Phase 6.
- **Phase 1 failure shapes are intentional**: missing `add_edge`/`has_edge`/`link_informs_for_evidence`
  (AttributeError) and set-vs-string assertion failures are the *right* reasons. A `KeyError` on a
  column or an import/collection fatal is a WRONG test assumption — fix the test.

## What NOT to touch

- `tag_group.target_puzzle_id` / the target model; `evidence_items` / `rollups` target columns.
- Any file not listed in current-spec.md "Relevant files".
- `bulk_re_edge` semantics (stays *replace*, not additive).
- Test infrastructure / `conftest.py` factories beyond the `make_round_ref` change named in plan Task 6.

## If you get stuck

1. Diagnose with `superpowers:systematic-debugging` before changing anything.
2. Two-strikes rule: if an acceptance command fails twice without a clear, understood fix, write
   `.ai/active/blocker.md` (command, output, hypothesis, what you tried) and STOP — do not add
   scaffolding or invent harness to route around it.

## Next steps

1. Phase 1: write the five test files/edits in current-spec.md, confirm each fails for the right
   reason, commit.
2. Phases 2–10: implement per plan Tasks, one phase per commit, running each phase's acceptance.
