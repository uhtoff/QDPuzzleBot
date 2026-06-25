# Milestone B2 — Derive has-target; drop `metapuzzle` + `metameta`

**Date:** 2026-06-25
**Status:** Design agreed; ready for implementation planning.
**Parent design:** `docs/superpowers/specs/2026-06-24-taggroup-edges-and-commands-design.md`
**Sequencing:** **after Milestone C, before Milestone D.** C is left as-specced (it still
sets/reads `metapuzzle`); B2 removes the denormalised flags afterward, so D is written
against the derived signal. (Named "B2" because it is the same *derive-a-denormalised-cache*
move B made for the `kind` column — surfaced late, hence out of A/B order.)

## Why

`puzzles.metapuzzle` and `puzzles.metameta` are denormalised role-flag columns — caches of
facts derivable from the tag_group/edge model, structurally identical to the `kind` column
B dropped. They were never a deliberate keep; they merely fell outside A's rename sweep. An
audit (below) shows neither is load-bearing in a way that requires a stored column.

## Audit findings (done — do not re-litigate, but re-verify before deleting)

### `metapuzzle` — means **has-target**, never "fed"
- **Definition:** `metapuzzle == 1` ⟺ `EXISTS(tag_group WHERE target_puzzle_id = puzzle.id)`.
  Maintained in lockstep with `target_puzzle_id` across `create_puzzle(is_meta=True)`
  (`puzzle_service.py:1358`), `convert_to_meta` (`:1157`), `convert_to_type` promote
  (`:658`), and cleared on `convert_to_puzzle` demote (`:903`).
- **All ~15 `is_metapuzzle()` readers use the has-target meaning** — not "is a target fed by
  feeders". The fed-vs-informed distinction lives entirely in the **edge layer**
  (`select_rollup_members` filters `tag_edges == "feeds"`; rollup membership never gates on
  `metapuzzle`). So deriving the flag is behaviour-preserving, including for Capstone /
  Runaround targets (which are also `metapuzzle == 1`).
- **Readers:** `notifier.py:119,146,157`; `puzzle_service.py:594,1142`;
  `web/actions.py:523,828,893,930`; `puzzles.py:1303,1334`(+`1036` raw `metapuzzle`);
  `gsheet.py:227,234,362,498`; raw SQL `web/data.py:165,177` (incl. `ORDER BY metapuzzle
  DESC`) and `mysqldb.py:357` (`AND p.metapuzzle = 0`); frontend `is_meta`
  (`web/data.py:196`).

### `metameta` — **dead**
- **Never written** anywhere in current code (only the dataclass default `= 0`,
  `puzzle_data.py:37`). Therefore always `0`.
- **Its one reader is dead code:** `puzzles.py:513` `get_by_attr(metameta=1)` (the
  `update_metameta` command, `puzzles.py:506-515`) always returns `None`.
- `web/data.py:197` always emits `is_metameta: false`; frontend `is_metameta`
  (`frontend/src/types.ts:65`) is declared but **never rendered** in any component.
- The *real* metameta concept already has a structural derived helper from B:
  `is_metameta(tag_group, all_tag_groups, puzzles_in_group)` (`puzzle_service.py:71`).

> Re-verify both audits at plan time (grep for new writers/readers introduced by C) before
> deleting anything.

## Scope

**In:**
- Add a `has_target(puzzle)` accessor (the single has-target seam) and route all current
  `is_metapuzzle()` callers through it.
- Replace the two raw-SQL readers with a derived test (JOIN/`EXISTS` on
  `tag_group.target_puzzle_id`); `ORDER BY metapuzzle DESC` → `ORDER BY (tag_group target
  exists) DESC`.
- Migration **013**: drop `puzzles.metapuzzle` and `puzzles.metameta`.
- Remove the dead `metameta` surface: the `update_metameta` command + helper
  (`puzzles.py:506-515`), the `is_metameta` JSON key (`web/data.py:197`) and the frontend
  `is_metameta` field — **or** keep `is_meta`/`is_metameta` JSON keys backed by the derived
  values if the frontend still wants the badge (decide at plan time; `is_meta` drives a
  visible META badge, `is_metameta` drives nothing).
- Update `PuzzleData` (drop both fields), `import_dict`, the generic `commit` field mapping,
  and the ~10 test files referencing the flags.

**Out:**
- Anything in D (sheet generation). D only *consumes* has-target via the accessor.
- Reworking the structural `is_metameta()` helper (already correct from B).
- C's command behaviour (B2 runs after C).

## `has_target` accessor — where it lives

- **Given a `PuzzleData`** (most readers): `has_target(puzzle)` ≡
  `RoundJsonDb.get_by_attr(target_puzzle_id=puzzle.id) is not None` — the exact pattern
  `convert_to_type` already uses (`puzzle_service.py:643`). Callers that already hold the
  group skip the lookup.
- **In raw board/feeder SQL:** a `LEFT JOIN tag_group tg ON tg.target_puzzle_id =
  puzzles.id` with `tg.id IS NOT NULL` as the boolean (the board query already joins
  `tag_group`, so this is nearly free there).
- **JSON:** `is_meta` continues to be emitted, now from the derived value, so the frontend
  META badge is unaffected.

## Constraints

- **Behaviour-preserving** for `metapuzzle` (audit-backed); **dead-code removal** for
  `metameta`. No new behaviour.
- A puzzle is the target of **at most one** tag_group, so the derivation is a clean 1:0..1
  lookup/join (confirm no schema allows otherwise at plan time).
- Watch the generic `commit()` in `mysqldb.py` — dropping a dataclass field must drop it from
  the column mapping or the by-name write breaks.

## Testing

- Reuse existing fixtures. Tests that asserted `metapuzzle`/`metameta` now assert through
  `has_target` (or the derived JSON `is_meta`).
- A focused test that `has_target` agrees with the old flag for: a fed meta, a capstone
  target, a runaround target, a bare puzzle, and a converted Group.
- Board ordering test: targets still sort first under the derived `ORDER BY`.
- Acceptance: full suite green (`timeout 180 pipenv run python -m pytest tests/ -q`),
  `ruff check` exit 0.

## Verification (whole milestone)

- `SHOW COLUMNS FROM puzzles` — no `metapuzzle`, no `metameta`.
- `grep -rn "\.metapuzzle\b\|\.metameta\b\|is_metapuzzle\|get_by_attr(metameta" bot/` — only
  the `has_target` accessor / derived helpers remain (no raw column reads).
- Frontend builds clean; META badge still renders for targets.

## Rollback

- Revert the milestone's commits.
- Reverse migration 013: re-add the columns and backfill `metapuzzle` from
  `EXISTS(tag_group …)` (and `metameta` to 0).
