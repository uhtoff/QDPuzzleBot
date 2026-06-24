# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2
Started: 2026-06-24
Status: in progress — feat(parity): convert_to_feeder web parity — frontend @ 954699b

## Goal

Web-dashboard parity for the **full destructive surface**: parity Phase 3 (deletion &
structural) + Phase 4 (reconcile), plus the destructive `convert_to_feeder` carved out of
Phase 2. Four areas: convert_to_feeder, delete_puzzle, delete_round/group, reconcile.

Authoritative spec: `.ai/active/current-spec.md` (this folder).
Shared architecture (thin controller): worktree-1's
`docs/superpowers/specs/2026-06-24-web-parity-puzzle-edits-design.md` (on main once merged).

## Context the executing agent needs (not obvious from files)

- **Sync this worktree to local `main` first** — working-2 is behind main and lacks the
  Phase-1 solve-mode baseline these tasks copy (`worktree-base-stale-origin` rule).
- **Most teardown is ALREADY built — wire the web layer, don't rebuild.** Delete bang paths
  are hardened (ConfirmView, `_safe_delete_channel`, None-guards, per-item isolation);
  reconcile is FULLY extracted in the service (`reconcile()`, `delete_orphans()`). The gap
  is: no web delete endpoints, no web reconcile endpoint, and delete *orchestration*
  (`_delete_puzzles`, `_delete_round_category`) still lives as cog helpers to lift into the
  service for reuse.
- **convert_to_feeder is the orphaning op** (`capstone-orphans-feeders`): deleting the
  meta's round orphans its feeders — the web confirm MUST state the orphan count.
- **reconcile on web supersedes** the old "Discord-only for now" line in
  `2026-06-23-reconcile-design.md` — parity Phase 4 deliberately brings it to the web.
- Reference impls: `retag_puzzle` / `solve_puzzle` (`bot/web/actions.py`), `PuzzleService.solve`
  (contract to mirror), `tests/test_solve_equivalence.py` `_run` harness (copy, don't invent).
- **Coordinate with worktree-1** on the shared puzzle kebab menu + `api.ts` to keep merges clean.

## Progress (Claude Code, 2026-06-24)

- Synced working-2 to local main (`1ca094c`).
- **Area 1 convert_to_feeder — DONE.** Backend `eb7dfa6`, frontend `954699b`.
  - `PuzzleService.convert_to_feeder(guild,hunt,puzzle)` + read-only
    `feeders_orphaned_by_convert(puzzle)` (puzzle_service.py, after `unsolve`).
  - Bang `!convert_to_feeder` rewired onto the service.
  - `GET /api/puzzles/<id>/convert-to-feeder/preview` + `POST .../convert-to-feeder`
    (actions.py, before `rebuild_rollup`).
  - Reusable `TypedConfirmModal` (type-the-name gate) — built for reuse by areas 2–4.
  - Board kebab "Convert to feeder…" (metas only) → preview → typed confirm.
  - 8 new equivalence tests; `test_solve_equivalence.py` + `test_refresh_meta.py` green; tsc clean.
  - NOT done: manual dashboard click-through (needs running app).

## Next steps

Areas remaining, lowest blast radius → highest: delete_puzzle → delete_round/group →
reconcile. Each tests-first: failing equivalence test → extract/lift + rewire bang →
endpoint → frontend confirm UX (reuse `TypedConfirmModal`).
- Area 2/3 need the cog-level delete orchestration (`_delete_puzzles`,
  `_delete_round_category`) LIFTED into the service before the web can reuse it.
- Area 4 reconcile: service already extracted; only web endpoints + admin pane missing.

## Open questions

- convert_to_feeder orphan count: service return value vs a cheap read helper.
- Web typed-confirm pattern: no reusable web confirm component exists yet — design one
  shared modal (type-the-name) for all four destructive ops, or per-action dialogs?
- reconcile preview pane placement: admin route/pane vs per-hunt screen.
