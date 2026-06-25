# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-06-25
Status: ready for handoff — Phase 1 (tests)

## Goal

Implement Milestone C of the TagGroup work: the command surface. Milestones A
(vocabulary rename, migration 011) and B (edge model: feeds/informs/none, drop kind,
migration 012) are complete on this branch. C adds new bang commands, the convert
family, and web API parity. **No new DB migrations.**

## Current status

Design agreed and committed at:
`docs/superpowers/specs/2026-06-25-milestone-c-commands-design.md` (fe292a2)

Implementation plan written to `.ai/active/current-spec.md`. Nothing implemented yet.
Test suite is green (392 passed, 0 failures) as of last Phase B commit `406bbf4`.

## Context the executing agent needs

**What exists after A+B:**
- `bot/core/puzzle_service.py` has `add_to_meta`, `move_to_meta`, `remove_from_meta`,
  `clear_metas` — OLD tag-based methods that C replaces (delete them).
- `bot/store/mysqldb.py` has `tag_puzzle_with_edge(puzzle_id, tag_group_id, edge_type)` —
  an idempotent upsert the new `tag_with_edge` service method wraps.
- `bot/cogs/puzzles.py` has the carve-out handlers: `add_to_meta`, `move_to_meta`,
  `remove_from_meta`, `sync_meta`, `pool`, `nest`, `source_mode`, `convert_to_meta`,
  `convert_to_feeder`, and the `round` command with its `command_map`.
- `bot/web/actions.py` has `/puzzles/<id>/meta` (mode=move/add/remove/clear),
  `/puzzles/<id>/convert-to-meta`, `/puzzles/<id>/convert-to-feeder`,
  `/hunts/<uid>/metas` (create).
- `puzzle.tag_edges` is a dict `{tag_group_id: edge_type}` populated at DB load time.
  `puzzle.tags` is a list of tag_group_id integers. Both must be kept in sync when tagging.

**Test pattern:** Mock-based service tests follow the `TestWebAddToMeta` pattern in
`tests/test_solve_equivalence.py`. Factories: `make_puzzle`, `make_round`, `make_hunt`
from `tests/conftest.py`. `make_puzzle` does NOT set `tag_edges` — set it manually:
`puz.tag_edges = {gid: "feeds"}`.

**Runner:** `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`

## What NOT to touch

- DB migrations — none for C
- `set_parent_group` and `set_source_mode` service methods — keep them
- Frontend board (`frontend/`) — no changes
- `feeders_orphaned_by_convert` service method — stays, used by `convert_to_puzzle`

## If you get stuck

1. Invoke `superpowers:systematic-debugging` before changing anything.
2. Two-strikes rule: if acceptance fails twice without a clear fix, write
   `.ai/active/blocker.md` and STOP.
