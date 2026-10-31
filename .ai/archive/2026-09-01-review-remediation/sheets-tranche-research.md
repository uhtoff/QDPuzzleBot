# Tranche 4 (Sheets tab lifecycle) — research findings

Confirmed against `working-3` @ `80e2875` (level with `staging`) on 2026-08-31. All nine
findings re-checked in current source; line numbers below are current. **None is fixed
yet** except M20 and M22 (the safe subset, taken separately).

## The review's framing does not survive

The plan said these nine "share one fix shape — verify-before-mutate,
tolerate-already-done". They do not. They are **three clusters plus three singletons**, and
there is no generic wrapper worth building: a "tolerate-already-done" decorator would have
to know each operation's idempotency probe (addSheet-duplicate, addTable-duplicate,
insertDimension-already-inserted, deleteDimension-already-deleted), and that probe *is* the
whole content of each fix.

**Cluster A — "is this Table ours?" (M16, M17, C5, and the guard half of M18).**
One real seam: `bot/core/sheet_tables.py`. Fixing `select_table`'s fallback collapses
**4 call sites** (`puzzles_gsheet.py:1186, 1501, 1531`, plus a new one replacing the
inline guard at `puzzle_service.py:2053`). Highest-leverage change in the tranche.
Does **not** cover M18's column-insert idempotency, nor `convert_to_puzzle`'s missing
guard.

**Cluster B — "probe geometry before a shifting mutation" (M18, C5).**
The pattern already exists inline and undocumented at `_ensure_contribution_table`
(`puzzles_gsheet.py:1509-1515`): read A1, compare against
`sheet_layout.PUZZLE_TAB_CELLS[0]["value"]`. Extracting it as a named helper collapses 3
uses — promote's pre-insert check, demote's pre-delete check, and the existing one.

**Cluster C — serialise the shared-tab refresh (M22).** Not a new seam: `rollup_lock` is
already generic-keyed and already used with a tuple key. Two `async with` lines.

**Singletons:** M14 (lazy-create fallback), M19 (step reorder), M20 (a deletion),
M21 (call-count optimisation).

## Per-finding corrections to the review

- **M16's stated fix does not work.** The review says to guard on `select_table(...)`.
  But `select_table` does not short-circuit on a name miss — it falls through to
  `len(tables) == 1`. The M16 scenario is *one* solver table, so it still matches and
  demote still runs. **M16 and M17 are one fix**, requiring a strict name match.
  Also: the existing test `tests/test_minimeta_service.py:621-640` uses a lone unnamed
  table, which passes under both the old and the proposed guard — the new test must use
  two tables, or one named `Table5`, to be falsifiable at all.

- **M17's legacy population is named, not unnamed** (see Q2 in `open-questions.md` for
  the three naming eras). The docstring's "lone *unnamed* table" is half the story, and
  narrowing on that basis would blank legacy rollups.

- **C5 is M16 seen from the other end.** Whether a demote retry is destructive depends on
  whether a solver table is present — which is exactly M16's guard. Fixing M16 with a
  strict name match closes C5's main reachable path. C5's retry path is real and
  *documented as a virtue*: `remove_minimeta`'s docstring (`puzzle_service.py:2034`) notes
  the operation "stays retryable" because `puzzle_type` is cleared only after the demote.

- **M14 has a second wedge in the same block.** `ensure_evidence_table` swallows an
  `addTable` failure and returns `page_id` as a sentinel (`puzzles_gsheet.py:1038,
  1074-1076`), which is then committed to `hunt.evidence_table_id` — so the
  `if ... is None` retry guard never fires again and the native Table is never retried.
  Degrades rather than wedges (rows still write via `reconcile_table`'s no-table
  fallback), but it is the same sentinel-poisons-the-guard shape.
  `ensure_unassigned_table` carries the identical sentinel.

- **M16's damage also lands via `convert_to_puzzle`** (`puzzle_service.py:1485-1487`),
  which calls `demote_tab_to_puzzle` with **no guard at all**. Reachable when a previous
  `convert_to_meta` → `promote_tab_to_group` failed, leaving `is_target=True` on an
  unpromoted tab.

- **M19 belongs with H3.** Both live in the same archive loop
  (`puzzles_gsheet.py:599-604`). Fixing M19 alone leaves the more common breakage.
  Also relevant: `_reconcile_tabs` (`puzzle_service.py:499-523`) only repairs
  `puzzle.google_page_id` — additional sheets are never checked — and
  `_collect_orphan_tabs` (`604-636`) scans only the main spreadsheet, so a stranded
  "Copy of X" on the archive is never even reported.

- **M21 is pure optimisation**, no state corruption. Confirmed sites: per-puzzle
  batchUpdate in `delete_round` (`puzzle_service.py:1706-1712`), sequential
  `refresh_target` in `!rebuild` (`puzzles.py:924-931`), duplicate full `sheet_list` per
  render (`puzzles_gsheet.py:1495` then `1173`), three write batches per render
  (`1276`, `1283`, `1320`), a full `sheet_list` per additional sheet in
  `move_sheet_to_end` (`362-368`), and `sheet_list` requesting full metadata with no
  `fields=` filter (`104-108`).

- **M22's key must be per tab, not per hunt.** Unassigned and Evidence are different
  tabs and do not race with each other; a single per-hunt key would over-serialise.

## Ordering and risk

1. **M20** — deletion only, zero corruption risk, no test breakage. *(done)*
2. **M22** — two `async with` lines on an existing seam. *(done)*
3. **M14** — 6 lines plus the Q1 decision on `_values_clear`.
4. **M17 + M16 together** — needs Q2. Risk is regression (blank rollups), not corruption.
5. **M18 + C5 together** — needs Q3. Highest corruption risk in the set.
6. **M19** — ship with H3.
7. **M21 last** — rewrites the same lines M17/M18 touch.

## Testability

Eight of nine are expressible in the existing harness, via one of two established
patterns: cog-level request-shape capture (`tests/test_contribution_table_requests.py:21-55`
— construct `GoogleSheets.__new__`, patch `batch_update`/`_values_get`/`sheet_list`, assert
on captured request bodies; inject failure with a `side_effect`, as
`tests/test_sheet_failure_isolation.py:38` does) or service-level DAO patching
(`tests/test_evidence_service.py:197-255`).

Assertions that would actually be falsifiable:

| Finding | Assertion |
|---|---|
| M14 | create raises → `get_page_id_by_name` awaited, page id committed; re-raise when lookup returns None |
| M16 | one table named `Table5` → `demote_tab_to_puzzle.assert_not_awaited()` (**must not reuse the lone-unnamed fixture**) |
| M17 | lone `Table1` → no writes sent; keeper test that a lone `"Contributions"` still writes |
| M18 | fail `addTable`, run promote **twice**, assert exactly one `insertDimension` |
| M19 | fail the rename, assert the copied sheetId still reached the caller and `deleteSheet` came after the rename |
| M21 | count `sheet_list` awaits per render (1, not 2) and batches per reconcile (1, not 3) |
| C5 | fail header regen, run demote twice, assert exactly one `deleteDimension` |

**The gap to state plainly:** every one of these asserts request shapes and call ordering.
None verifies what Google actually does. Four of the nine rest on unverified API
assumptions (see Q3) — those are inspection-only, not gate-verified, however green the
suite looks.
