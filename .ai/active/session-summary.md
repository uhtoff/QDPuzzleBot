# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-08-12
Status: in progress — test(feeders): pin mini-thread claim targeting and the missing-parent guard @ d280b93

## Goal

Build the **feeder pool** for meta matching puzzles: a Minimeta owns a list of given
feeder strings; its Minis (and the Minimeta itself) claim items out of it; claims confirm
automatically when a mini is solved.

Spec: `docs/superpowers/specs/2026-08-12-feeder-pool-design.md` (authoritative).
Plan 1: `docs/superpowers/plans/2026-08-12-feeder-pool-core.md` (executed, complete).

Three plans total. **Plan 1 = core (done). Plan 2 = sheet surfaces (next). Plan 3 = web
routes + dashboard panel.**

## Current status

Plan 1 complete on `staging`, 19 commits, `97d2d75..d280b93`.

- Suite **1981 passed**, `ruff check` clean, `basedpyright bot/` 0 errors, working tree clean.
- `bot/core/feeder_pool.py` is import-free (design requirement — verified by grep).

Working end-to-end from Discord, no Sheets involvement: `!add_feeders`, `!feeders`,
`!claim_feeder`, `!unclaim_feeder`, `!edit_feeder`, `!remove_feeder` (organiser), and
confirm-on-solve/demote-on-unsolve via the shared `service.solve()`/`unsolve()` seam.

## Final decisions

- **Claims + confirmed**, not one home per feeder. Overlap is legal and informative.
- **Confirmation happens automatically at mini solve**; unsolve demotes. Solving is the proof.
- **Confirmation never deletes a rival claim** — a losing claim is information, and deleting
  it would make unsolve lossy.
- **Provided rows** (feeders the main meta uses directly) render on the CLAIM, not on
  confirmation, because the confirming event for a main-meta claim is solving the main meta.
  A confirmed claim elsewhere supersedes them.
- **Feeder text stored verbatim**, but deduped case- and accent-insensitively to match the
  `utf8mb4_uca1400_ai_ci` collation on `feeder_pool_items.text`.
- **Key on `parent_puzzle_id`, never `puzzle_type`** — the latter is user-editable via `!type`.
- **One shared staleness badge** on the Minimeta; mini claim blocks go unbadged and self-heal
  on the next sync. (`rollups` has `UNIQUE(target_puzzle_id)`, so a puzzle gets one record.)
- Gating: building ungated, `!remove_feeder` organiser.

## Rejected approaches

- Making `feeder_pool_items.text` case-sensitive so `MARZIPAN`/`Marzipan` are two feeders —
  they are the same answer typed twice.
- Adding a `kind` column to `rollups` so every render gets its own staleness badge — alters an
  existing table's UNIQUE on live data to distinguish failures a user responds to identically.
- Deleting rival claims on confirmation — makes unsolve lossy and destroys useful signal.
- Pools on TagGroups (round-level metas). Minimeta-owned only; generalising stays open.

## Next steps

1. Write **Plan 2 — sheet surfaces** to `docs/superpowers/plans/`. Covers: the `Feeder Pool`
   table on the parent tab, the `Feeder Claims` block on each mini tab (with the solver-owned
   checkbox column), `!sync_feeders`, provided rows on the contribution table, and the shared
   staleness badge.
2. Execute it the same way (subagent per task, two-stage review, mutation checks).
3. Then Plan 3 (web routes + dashboard panel), per the project's command-parity principle.

`provided_item_ids` and `diff_ticks` in `bot/core/feeder_pool.py` are already written and
tested but **not yet called by anything** — Plan 2 consumes them. That is intentional.

## Process lessons (carry into Plan 2)

- **Per-task review missed a cross-task defect.** Every Plan 1 task passed its own review;
  a whole-diff adversarial pass then found a crash (Python dedup written in Task 3 vs the
  collation observed in Task 1). Run a whole-diff review at the end of Plan 2 too.
- **Three plan specifications were wrong because they were written from memory**, and only
  reading the real code caught them: `_confirm_destructive`'s signature (would have told
  users their Google sheets were being deleted), an invented embed helper when
  `split_lines_for_embed` exists, and two mutation checks whose fixtures made them
  unfalsifiable. **Plan 2's Sheets code has no test coverage to catch equivalents** — read
  `bot/cogs/puzzles_gsheet.py` properly before specifying against it.
- Mocked tests are blind to collation by construction. Anything about DB constraint semantics
  needs a live-DB test (`tests/test_feeder_pool_cascade.py` conventions).

## Open questions

- None blocking. Two decisions taken without the user (both flagged, both reversible):
  case-insensitive dedup, and `!remove_minimeta` tearing down the pool.
