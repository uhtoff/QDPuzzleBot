# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-06-22
Status: in progress — M5 phase 3: surface stale sheets — toast + board badge @ a51a54b

## Goal

Implement **M5** per `docs/superpowers/specs/2026-06-22-m5-rollups-design.md`:
first-class `rollups` record, persisted last_ok/last_error surfaced as a toast +
durable stale badge, inline propagation, `!rebuild` + web rebuild button, a
per-round_id rollup mutex, and dev-metadata tab pointers with google_page_id as the
trusted fallback.

## Implementation phases

1. ✅ **Migration + store + lock** — `migrations/002_rollups.sql`, `RollupData` +
   `MySQLRollupJsonDb` + `RollupsJsonDb` export, `rollup_lock`/`_ROLLUP_LOCKS` in
   `bot/core/locks.py`. Tests: `test_rollup_lock.py`, `test_rollup_data.py`.
2. ⬜ **Rollup-aware refresh_meta + dev-metadata + failure isolation** —
   `PuzzleService.refresh_meta` (lock, find-or-create rollup, persist last_ok/error,
   re-raise; tab resolve via metadata→google_page_id fallback); fan-out callers
   (solve/move/add/remove) catch-continue-collect + raise `SheetRefreshError` at end;
   gsheet `ensure_tab_metadata`/`find_tab_by_metadata`.
3. ⬜ **Edges** — `bot/web/actions.py` solve/retag return `sheet_warning`;
   `bot/web/data.py` expose `rollup_stale`/`last_error`.
4. ⬜ **`!rebuild` + `POST /api/rollups/<round_id>/rebuild`**.
5. ⬜ **Frontend** — stale badge + toast + Rebuild button.

## Key decisions (from brainstorming)

- Full M5 incl. inert source_mode/edge_type_filter columns.
- Surface: persist badge + toast. DB mutation always commits; never roll back.
- Inline propagation (no debounce). Read INSIDE the rollup lock (post-commit snapshot).
- Rollup keyed per round_id (unique). Rebuild endpoint keyed on round_id.
- Per-round_id lock lives INSIDE refresh_meta (single choke point); separate registry
  from M4 per-puzzle locks; one lock held per task → no deadlock.
- Auto-heal: next solve touching a stale group re-renders full snapshot → clears badge.
- Orphan-cleanup-on-create-failure noted for M10 (PLANS.md), out of M5 scope.

## Test / DB notes

- Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
- Service tests MOCK the store (`patch bot.core.puzzle_service.PuzzleJsonDb` etc.) — no
  DB needed there. The store round-trip was smoke-tested live.
- Test DB `rollups` table created; migrations baselined (000/001/002 applied).

## Next step

Phase 2: rewrite `PuzzleService.refresh_meta` to be rollup-aware + add gsheet metadata
helpers + failure isolation in the fan-out callers. Extend `tests/test_refresh_meta.py`.
