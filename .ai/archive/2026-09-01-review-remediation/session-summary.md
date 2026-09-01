# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-08-30
Status: ready for handoff — fix(minis): a stale meta sheet must not fail a mini solve/unsolve @ db1ac08

<!-- worktree-census: maintained by stamp-handoff-status.sh, do not hand-edit -->
## Parallel worktrees — auto-maintained, read before starting work

Another session may be mid-task in these. A worktree ahead of `main` holds work that exists
nowhere else: its own `.ai/active/` is gitignored, and nothing is shared until it wraps into
`.ai/archive/`. Check one before rebuilding anything it may already have done, and harvest it
before deleting it — including its untracked `.ai/active/` notes.

- `matrix-spike` (3 ahead of main) — last commit 2026-07-31 17:59: docs(spike): park the matrix spike with an honest resume note
- `working-3` (70 ahead of main) — last commit 2026-08-31 21:44: docs(migrations): correct the overstated risk in 046 and the review entry
<!-- /worktree-census -->

## Goal

Fix the findings of the 2026-08-30 comprehensive review. **Done.** Every finding is
closed, deferred with a recorded reason, or resolved as a non-finding.

## Current status

`staging` fast-forwarded to `working-3`, **38 commits ahead of `origin/staging`**, nothing
pushed. Gates on the merged branch: **2171 backend** (from 2062), ruff clean, basedpyright
0, frontend build clean, **52 vitest**.

**34 review findings closed**, plus two things not in the review, both found as a
side-effect of other work:

- the `bridge` source-sniffing test (found grepping after M33)
- four archived-guard omissions on admin routes (only visible after M29's extraction)
- a stale-sheet regression in `!unsolve_mini` (mine, from M27) and the identical
  pre-existing one in `!solve_mini` — found by the end-of-session risk audit

Closed this session: M14, Tranche 8 (D9), H3, M15, M19, M18, C5, M30, M21 (partial), M9,
M29 (both stages), M27, C3, C7, M10, C2, M3, M11, M12, M1, C4, M6, C1, M33, M35, C9.

Deferred with reasons recorded in commit messages, not silently dropped:
- **M21's remaining halves** — batching `delete_round`'s deletes trades per-puzzle failure
  isolation for latency (a decision, not an optimisation); the snapshot-sharing half needed
  a recount that M13 then did and acted on.
- **The `_home_category` module-function rename** — only the service-side method was
  renamed (1 call site); the module function keeps its name, being always qualified.
- **Per-guild reminder loops** and the wider `!hunt` namespace questions beyond the
  duplicate check — real design decisions, written up rather than half-built.

## THE THING TO KNOW BEFORE DEPLOYING

**Migrations 045 and 046 are applied to `puzzlebot_test` ONLY. Production has neither.**

`deploy.sh` runs `migrate.py` unconditionally at step 2, after Plesk's git pull. So a push
to whichever branch Plesk tracks applies both automatically. That is now SAFE and intended
— both must go together — but confirm which branch Plesk tracks before pushing.

045's pre-flight was run against production and did **not** come back clean: 7 rows, of
which **6 are genuinely on their hunt's solved spreadsheet** (checked against the Sheets
API, metadata only). D8's premise held on test and was false on prod. 046 corrects those
six, keyed on `(id, google_page_id)` pairs so it is a verified no-op elsewhere. Row id 6
stays at 0 — its tab really is on the main spreadsheet.

The consequence was **gated, not live**: both hunts are archived, archiving deletes their
channels, and the web unsolve route is archived-gated — so the failing operation is
unreachable until a hunt is unarchived. Corrected in the docs after overstating it first.

Post-apply check (expect exactly ids 7–12, and NOT 6):

    SELECT id, google_page_id, on_solved_sheet FROM additional_sheets
     WHERE on_solved_sheet = 1 ORDER BY id;

## Final decisions

- **D1–D9** as recorded in the review doc; **D8 corrected** — true of test, false of prod.
- **042 is applied on prod** (confirmed by `migrate.py status`), so the 2026-08-07 review's
  "not yet deployed" row was stale and is fixed. 043/044 do not exist on prod at all.
- **A migration whose constraint fails loudly needs no pre-flight ledger** — a recorded
  apply IS the evidence (042). One that only adds a defaulted column does (045). This is
  the most reusable thing the session produced.
- **All four questionable archived exemptions are now guarded**; three remain exempt
  deliberately (`post_hunt_archive`, `get_puzzle_images`, `delete_hunt`).
- The review's own prescribed fixes were wrong **three times** in the Sheets tranche, each
  time naming a sibling to copy without checking the sibling did the job (M16, M14, M18).
  Treat "mirror X" in that document as a lead, not an instruction.

## Rejected approaches

- **Batching `delete_round`'s tab deletes** — see above; loses failure isolation.
- **Cascading M1 through `delete_mini`** — it re-renders the doomed parent's rollup once
  per mini; children are deleted directly instead.
- **Locking inside `service.create_hunt_solved_sheet`** — `hunt_sheet_lock` is held at the
  EDGE by convention; both callers wrap it.
- **A TTL cache on `sheet_list` (M13)** — every staleness bug fixed today lived on that
  read.
- **`migrate.py baseline` to skip 045** — records it applied without the column existing.

## Pre-deploy risk audit (2026-08-31)

Run at Russ's request — "any other DB or live-data risks?". It found one real
regression, now fixed (`db1ac08`). Everything below was checked, not assumed.

| Area | Verdict |
|---|---|
| C3's new `DELETE` | **Safer than before** — replaced an unguarded `delete(id)` with the same delete plus four `NOT EXISTS` guards |
| M21a's `fields=` mask | **Was inspection-only** (all tests mock `sheet_list`). Now verified against a real 56-tab production spreadsheet: guard survives, all four read fields present |
| M27 making `unsolve` raise | **Found the regression.** Three callers; web + `!unsolve` catch it, `unsolve_mini_by_number`'s bang edge did not. Fixed with its pre-existing `!solve_mini` twin |
| M1's cascade | Deletes more rows, as decided. Narrow-branch edge case (a tagged mini in both lists) degrades safely — the relocation loop already guards `if channel is None: continue` |
| C4's `update_tiles` | Write, gated on `tile_status == "ready"`; untiled rows untouched |
| M6's namespace change | Strictly MORE permissive — no existing row becomes invalid |
| Archived guards / M6 scoping / M27 | Refuse more, mutate less. No data risk |
| Live Sheets & Drive | One scratch spreadsheet created and deleted. Production: **metadata reads only, zero writes** |
| Scheduled work | Media GC is manual (two organiser-gated routes); the only `tasks.loop` posts reminders. Nothing destructive runs unattended |

**Still unverified, and only checkable on prod:** M1's cascade keys on
`parent_puzzle_id`. If any production puzzle carries that column for something other
than a mini, deleting its parent now sweeps it. Confirm the count looks like minis:

    SELECT COUNT(*) AS minis, COUNT(DISTINCT parent_puzzle_id) AS parents
      FROM puzzles WHERE parent_puzzle_id IS NOT NULL;

## Next steps

1. Confirm which branch Plesk's Git integration tracks.
2. Run the `parent_puzzle_id` sanity check above.
3. `git push origin staging` (38 commits).
4. For live: fast-forward `main` from `staging` and push — **this triggers the deploy and
   applies 045 + 046**, in that order, before the bot restarts.
5. Run the post-apply verification query above.
6. Archive this folder (`/wrap`) once deployed.

## Open questions

None blocking. Optional follow-ups, all recorded in commits:
- `tests/test_bridge_archive_survives.py` is fixed, but the same negative-source-assertion
  pattern may exist elsewhere — one `grep -rn "inspect.getsource" tests/` finds them.
- M21's two deferred halves.
- Whether `update_puzzle`-style route tests are missing for other member-tier routes; two
  such gaps were found by mutation this session (`delete_puzzle`, `update_puzzle`).
