# Current Spec — Web parity: the DESTRUCTIVE surface (Phases 3 + 4)

Tool: (to be set by the executing session)
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2
Started: 2026-06-24
Status: ready to pick up

This is the **full destructive surface** of the web-command-parity plan, handed to
worktree-2 — parity **Phase 3 (deletion & structural)** + **Phase 4 (reconcile)**, plus the
destructive convert carved out of Phase 2. The NON-destructive Phase 2 edits (link, rename,
clue, make_minimeta, convert_to_meta) are being done in worktree-1.

Shared architecture (thin controller; service → endpoint → frontend): worktree-1's
`docs/superpowers/specs/2026-06-24-web-parity-puzzle-edits-design.md` (on main once merged).
Roadmap: `.ai/plans/web-command-parity.md` Phases 3 & 4.

## ⚠️ Before you start

- **Sync this worktree to local `main` first.** working-2 (@ `8478aac`) is behind main
  (now `1ca094c` after the Phase-1 + arrange merge) and lacks the Phase-1 solve-mode
  endpoint/service baseline these tasks copy. Reset/ff onto local `main` per the
  `worktree-base-stale-origin` rule, then branch. Do NOT build on the stale base.
- **Coordinate with worktree-1** on the puzzle kebab/overflow menu and `api.ts` — both
  worktrees add structural/destructive actions there; keep the eventual merge clean.
- These are the only genuinely destructive web ops in the whole parity effort. Every one
  needs an explicit **preview/typed-confirm** before applying.

## What is ALREADY built (do not rebuild — verify, then wire)

- **Delete bang paths are hardened** (ConfirmView gate, `_safe_delete_channel`, None-guards,
  per-item exception wrapping). Service has `delete_puzzle_data` (DB+sheet) and
  `delete_round_data` (~puzzle_service.py:2672/2713); `_safe_delete_channel` is reusable.
  The *orchestration* (`_delete_puzzles`, `_delete_round_category`) currently lives as
  **cog-level helpers** — that is what needs lifting into the service for web reuse.
- **Reconcile is FULLY extracted in the service:** `reconcile()` (puzzle_service.py:142),
  `delete_orphans()` (:206), `_reconcile_channel`/`_reconcile_tabs`,
  `_collect_orphan_channels`/`_collect_orphan_tabs`. The bang command + ConfirmView orphan
  confirmation already work (working-2 already enriched the orphan list — `_format_orphan_confirmation`).
  **There is NO web endpoint** — that is the gap.
- `ConfirmView` (bot/cogs/ui.py) is Discord-only; the **web** needs its own typed-confirm
  modal (no reusable web confirm exists beyond per-component dialogs).

## Goal

Close every destructive web gap, each as: ctx-free service method (extract/lift where the
logic is still cog-level; reuse where a seam exists) → thin Quart endpoint (`require_organiser`,
`puzzle_lock` where puzzle-scoped, `_audit`, board push) → dashboard control behind an
explicit preview/typed-confirm.

## In scope — four destructive areas

### 1. `convert_to_feeder` (the destructive convert)
- Bang: `bot/cogs/puzzles.py` `convert_to_feeder` (~L1461): finds the round where
  `meta_id == puzzle.id`, **deletes it** (`RoundJsonDb.delete`), sets `metapuzzle=False`,
  drops the round id from `puzzle.tags`, `gsheet_cog.convert_puzzle_spreadsheet(puzzle)`
  (reverts restructure), channel type → `Puzzle`.
- **Destructive consequence (`capstone-orphans-feeders`):** deleting that round **orphans
  every feeder tagged to it**. The web confirm MUST state the orphan count first.
- Work: extract `PuzzleService.convert_to_feeder(guild, hunt, puzzle)` (mirror `solve`'s
  isolation/`SheetRefreshError` contract); rewire the bang; expose an orphan-count;
  `POST /api/puzzles/<id>/convert-to-feeder`; kebab action + typed confirm.

### 2. `delete_puzzle`
- Bang: `bot/cogs/puzzles.py` (~L2583): `delete_puzzle_data` (DB row + `delete_puzzle_spreadsheet`)
  + delete the Discord channel + `update_metapuzzle` refresh.
- Work: lift a ctx-free `PuzzleService.delete_puzzle(guild, hunt, puzzle)` that reuses
  `delete_puzzle_data` + `_safe_delete_channel` + meta refresh; rewire the bang onto it.
  `DELETE /api/puzzles/<id>` (`require_organiser`, `puzzle_lock`, audit, board push).
  Frontend: delete action in the kebab menu behind a **typed-confirm** (type the puzzle
  name) — warn that the channel + sheet tab are permanently deleted.

### 3. `delete_round` / `delete_group`
- Bang: `bot/cogs/puzzles.py` (~L2645): `_delete_puzzles` (loop: each puzzle's DB+sheet+channel,
  per-item try/except) → `delete_round_data` → `_delete_round_category`.
- Work: lift the orchestration into a ctx-free `PuzzleService.delete_round(guild, hunt, round)`
  reusing the existing helpers; rewire the bang. `DELETE /api/rounds/<id>` (organiser).
  Frontend: delete action on the round/category header behind a typed-confirm that **lists
  how many puzzles + channels will be destroyed** (preview → confirm).
- NOTE: this is the highest-blast-radius op (N channels + N tabs + a category). Lean on the
  existing per-item failure isolation; surface partial-failure results in the response.

### 4. `reconcile` (Phase 4 — most isolated/riskiest)
- Service is done (`reconcile()`, `delete_orphans()`); only the web layer is missing.
- Work: web endpoints exposing the three modes —
  - `POST /api/hunts/<uid>/reconcile?mode=report` → the dry-run diff (read-only),
  - `mode=apply` → non-destructive fixes,
  - `mode=apply-orphans` → **deletes orphan Discord channels/tabs**, gated behind an
    explicit typed-confirm that lists the orphans (reuse `_collect_orphan_*` output).
- Frontend: a **preview→confirm pane** (admin-gated) that shows the report diff, lets the
  organiser apply non-destructive fixes, and requires a typed-confirm to delete orphans.
- **Decision note:** the reconcile design spec (`2026-06-23-reconcile-design.md`) said
  "Discord-only for now". This task **deliberately supersedes** that — parity Phase 4 brings
  reconcile to the web. Call this out; do not treat the old "Discord-only" line as binding.

## Out of scope

- Non-destructive Phase 3 siblings (`rebuild_unassigned` button, `pool`, plain-`round`
  create) — those are NOT destructive; leave them to a later non-destructive pass unless
  trivial to include.
- Create-orphan cleanup hardening (M10 workstream C, bang `!p` rollback) — separate
  reliability concern, not web parity.
- Phase 5 (hunt lifecycle/settings) — its own future plan.

## Constraints

- Additive service methods only; no signature changes to existing methods.
- Behaviour parity: each rewired bang must produce identical DB/Discord/sheet teardown.
- Reuse `tests/test_solve_equivalence.py`'s `_run` harness for service equivalence tests;
  invent no new harness. Reuse the existing delete hardening + per-item isolation — do not
  reimplement teardown.
- Every destructive web action requires an explicit preview and/or typed-confirm; no
  one-click destructive endpoint.

## Acceptance criteria

- All four areas work from the dashboard; DB/Discord/sheet teardown identical to the bang
  paths (equivalence tests prove the service seams).
- Every destructive action is gated by a preview/typed-confirm naming exactly what is
  destroyed (orphan count, puzzle/channel counts, orphan list).
- Full pytest suite green; `tsc --noEmit` clean; manual click-through of each destructive op.

## Suggested order (lowest blast radius → highest)

convert_to_feeder → delete_puzzle → delete_round/group → reconcile.
Each: tests-first (failing equivalence test) → extract/lift + rewire bang → endpoint →
frontend confirm UX → suite green.
