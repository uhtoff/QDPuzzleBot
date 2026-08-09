# Plan — UNIQUE on `tag_group.group_code`

Closes the last blocked finding from the 2026-08-07 review. Scoped 2026-08-09
against a **production dump of `tag_group`** (384 rows, supplied by the user),
not against the review's description — which turned out to be incomplete.

## What the production data changed

The review said the fix was "move the `'0'` sentinel to NULL". That is necessary
but **not sufficient**, and the sentinel is not the only problem.

| Finding | Evidence |
|---|---|
| 343 real 6-char codes, **no duplicates** | the race has never bitten in prod |
| 7 rows hold the `'0'` sentinel | as the review said |
| ~34 rows hold legacy numeric codes `'1'`–`'25'` | **not in the review** |
| those numerics are duplicated: `'1'`×4, `'2'`×4, `'3'`×2, `'4'`×2, `'5'`×2 | NULLing only `'0'` still fails the index |
| 23 real codes contain punctuation (`RO1-CU`, `TR#1LO`, `(AOFCR`) | pre-date `generate_uid`; still valid, leave alone |

Counts are from a local parse of the dump (362/384 rows parsed cleanly) — treat
as approximate. **Get authoritative counts from the query in step 0.**

## The mechanism the review missed

Every group creation is two-phase (`puzzle_service.py:1025`, `:2032`, `:2876`):

    TagGroupJsonDb.commit(new_round)                    # INSERT — group_code takes the default
    new_round.group_code = generate_uid("group_code")   # mint
    TagGroupJsonDb.commit(new_round)                    # UPDATE

("Initial commit so the row has an id (rollback safety)" — `:2875`.)

So `'0'` is not only historic: **every new group passes through it today**. Add
UNIQUE while `'0'` is still the column default and the next group creation fails
at its first commit. Backfilling old rows alone would not have worked.

`check_duplicates` is a plain SELECT (`mysqldb.py:177`) and does not need the row
to exist, so minting *before* the first insert is feasible.

## Design (chosen: "always a real code", with a NULL safety net)

1. Historic non-6-char rows get **minted codes**, so all 384 become addressable —
   rather than being made permanently codeless.
2. The column default becomes **NULL, not `'0'`**. NULLs are exempt from UNIQUE,
   so the default is a harmless safety valve if a path ever forgets to mint.
3. Minting moves **before the first insert**, so in practice no row is ever
   written without a code.

Rejected: *NULL the sentinel and stop there* (leaves 34 duplicated numerics, and
leaves every creation inserting a colliding placeholder). Rejected: *delete the
historic rows* — they are `status='active'`, 37/40 are Metapuzzles with a
`target_puzzle_id`, all 40 have a `category_id`, and
`tag_group_category.group_id` is `ON DELETE CASCADE` (`022_delete_cascades.sql:52`).
Deletion is destructive, unnecessary for the index, and a separate question.

## Why this is lower-risk than it looks

- **Nothing compares against `'0'`.** The only reference in the whole codebase is
  the dataclass default (`tag_group_data.py:40`). Every consumer filters on
  `len(...) == 6` instead (`tag_group_data.py:104`, `puzzles.py:386`, `:458`),
  and those sites already `isinstance(..., str)`-guard, so they tolerate None.
- **The retry machinery already exists.** `MySQLTagGroupJsonDb.GENERATED_UNIQUE_FIELDS
  = ('group_code',)` is already set (`mysqldb.py:241`); it just has no index to
  fire against. Migration 040 built the re-mint-and-retry for `hunts.uid`.
- **Only 3 construction sites** (`puzzle_service.py:1019`, `:2026`, `:2870`).
- **The 5 live-DB test helpers need no change.** They insert without a
  `group_code` (`test_is_target_derivation.py:30`, `test_bulk_edge_atomicity.py:18`,
  `test_tag_edges.py:21`, `test_get_unassigned.py:42`,
  `test_missing_row_update_logs.py:117`); with the default NULL they keep passing.
  This is what broke eight live-DB tests when 040 tried the index with `'0'`.

## The minting scheme

Migrations are plain `.sql` (`migrate.py:27`), so it must be SQL-expressible.

    CONCAT('G', LPAD(id, 5, '0'))    -- 'G00303'

Unique because `id` is; deterministic, so the migration is re-runnable; 6 chars;
uses only `[A-Z0-9]`, matching `UID_CHARS`. **Verified collision-free**: no
existing code matches `G#####`, and max id is 766 (fits 5 digits to 99999).

## Phases

**0. Pre-flight (blocking).** Run against production and record the output:

    SELECT COUNT(*) AS groups_total,
           SUM(CHAR_LENGTH(group_code) <> 6 OR group_code IS NULL) AS needs_recoding,
           SUM(group_code = '0')                                   AS sentinel_rows
      FROM tag_group;

    -- must return no rows: real codes that already collide
    SELECT group_code, COUNT(*) n FROM tag_group
     WHERE group_code IS NOT NULL AND CHAR_LENGTH(group_code) = 6
     GROUP BY group_code HAVING n > 1;

    -- must return no rows: the minting scheme colliding with a real code
    SELECT id, group_code FROM tag_group WHERE group_code REGEXP '^G[0-9]{5}$';

If the second query returns rows, STOP: two groups share a code users type, and
which one keeps it is a human decision, not a migration's.

**1. Migration 042** — re-code, re-default, index. One file, in this order:

    UPDATE tag_group SET group_code = CONCAT('G', LPAD(id,5,'0'))
     WHERE group_code IS NULL OR CHAR_LENGTH(group_code) <> 6;
    ALTER TABLE tag_group ALTER COLUMN group_code SET DEFAULT NULL;
    ALTER TABLE tag_group ADD UNIQUE INDEX uq_tag_group_group_code (group_code);

**2. Model** — `tag_group_data.py:40` default `"0"` → `None`, type `str | None`.
Let basedpyright enumerate the read sites that then need guarding; that list is
the real remaining scope and is discoverable rather than estimated.

**3. Creation order** — at the 3 construction sites, mint before the first
commit, preserving the rollback-safety intent of the current split.

**4. Verification** — full pytest (the 5 live-DB helpers are the ones to watch),
ruff, basedpyright, then a mutation check: re-inserting a duplicate code must now
raise and be re-minted by the `GENERATED_UNIQUE_FIELDS` retry rather than silently
succeed. That is the behaviour the whole change exists to buy.

## Not done, and why

- **Local index rehearsal was blocked** (dev-DB write denied by the sandbox). The
  test-cost figure above is from static analysis of the insert helpers, not from a
  run. Rehearsing on `puzzlebot_test` before touching production is worth doing —
  it needs a permission grant or the user running it.
- `scripts/diagnose_atomicity.py:212` also inserts a bare `tag_group` row. It is a
  diagnostic script, not production flow, but it will collide on a second run once
  the default is NULL only if it sets a code — worth a look during phase 3.
