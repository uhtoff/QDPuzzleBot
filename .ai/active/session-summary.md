# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-06-25
Status: in progress — feat(taggroup): new bang commands + convert family (Milestone C Phase 3) @ 47408e8

## Goal

Implement Milestone C of the TagGroup work: the command surface. Milestones A
(vocabulary rename, migration 011) and B (edge model: feeds/informs/none, drop kind,
migration 012) are complete on this branch. C adds new bang commands, the convert
family, and web API parity. **No new DB migrations.**

## Current status

Design agreed and committed at:
`docs/superpowers/specs/2026-06-25-milestone-c-commands-design.md` (fe292a2)

Implementation plan written to `.ai/active/current-spec.md`. **Phase 1 done:** three
failing test files committed (`tests/test_edge_primitives.py`, `tests/test_convert_service.py`,
`tests/test_edge_web.py`) — 35 tests, all red for the right reason (25 AttributeError
on missing service methods, 10 AssertionError on un-wired web/rename features). Acceptance
log: `/tmp/test-p1.log`.

**Phase 2 done (@ 3e4560c):** service layer implemented and all callers updated.
- `bot/core/puzzle_service.py`: added `tag_with_edge`, `tag_edge_only`, `untag_from`,
  `bulk_re_edge`, `convert_to_type`; renamed `convert_to_feeder` → `convert_to_puzzle`;
  deleted `add_to_meta`, `move_to_meta`, `remove_from_meta`, `clear_metas`.
- `bot/store/mysqldb.py`: added `bulk_update_edge_type` DB method.
- All production callers (cog, web) updated to use new service API.
- Test equivalences in `test_solve_equivalence.py` migrated off old method names.
- Phase 2 acceptance: `tests/test_edge_primitives.py` + `tests/test_convert_service.py`
  → 27 passed. Full suite: 426 passed, 6 failed (Phase 4 expectations below).
- `ruff check` clean.

**Phase 3 (bang commands) — next:** pending. Requires edits to `bot/cogs/puzzles.py`.

**Remaining Phase 4 web expectations (test_edge_web.py):** These 6 tests are correct
specification for the Phase 4 web endpoint changes and will pass once that phase is done:
1. `test_create_meta_default_type` — needs `round_type` param in create endpoint
2. `test_create_meta_capstone_type` — same, with explicit type
3. `test_retag_informs_edge` — needs edge passthrough in retag endpoint
4. `test_retag_add_only_true` — needs `only=True` → tag_edge_only routing
5. `test_convert_to_capstone_endpoint_exists` — route not registered yet
6. `test_convert_to_capstone_returns_summary` — response missing "summary" key

## Context the executing agent needs

**What exists after Phase 2:**
- `bot/core/puzzle_service.py` has the new edge-aware service primitives:
  `tag_with_edge`, `tag_edge_only`, `untag_from`, `bulk_re_edge`, `convert_to_type`.
  Old methods (`add_to_meta`, `move_to_meta`, `remove_from_meta`, `clear_metas`) are deleted.
  `convert_to_feeder` renamed to `convert_to_puzzle`.
- `bot/store/mysqldb.py` has `tag_puzzle_with_edge` (existing) + `bulk_update_edge_type` (new).
- `bot/cogs/puzzles.py` still has the old bang command handlers (`add_to_meta`, `move_to_meta`,
  etc.) but they now call the new service methods. Phase 3 adds the new commands and removes old ones.
- `bot/web/actions.py` callers updated to new service API. Phase 4 adds type/edge params + new routes.
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
