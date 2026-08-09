-- 042_unique_group_code.sql
-- The half of the 2026-08-07 review finding that migration 040 could not do.
--
-- group_code is the 6-character handle people type to address a group. It is
-- minted by generate_uid, which SELECTs for a clash and then INSERTs -- a
-- check-then-insert with no constraint underneath, so two concurrent creates
-- can both pass the check and both insert the same code. get_by_attr then
-- returns whichever row it finds first and the other group is unreachable by
-- code. 040 closed exactly this hole on hunts.uid; group_code was held back.
--
-- WHY IT WAS HELD BACK, AND WHY THE STATED FIX WAS NOT ENOUGH.
--
-- 040's note said this column needed its '0' sentinel moved to NULL first.
-- That was true but incomplete. A production dump (2026-08-09, 384 rows) shows
-- the column holds THREE kinds of value, not two:
--
--   * 343 real 6-character codes  -- and NO duplicates among them, so the race
--                                    has never actually bitten in production
--   *   7 rows holding '0'        -- the "no code yet" sentinel 040 described
--   *  34 rows holding '1'..'25'  -- legacy numeric codes, NOT in the review,
--                                    and duplicated: '1'x4, '2'x4, '3'x2,
--                                    '4'x2, '5'x2
--
-- So NULLing only '0' would still have failed on the numerics. Confirmed
-- against production before writing this: 384 total, 41 needing re-coding
-- (7 + 34), max id 766, no duplicate real codes, no existing code shaped like
-- the minted ones below.
--
-- AND THE SENTINEL WAS NOT MERELY HISTORIC. Every group creation is two-phase
-- (puzzle_service.py:1025, :2032, :2876): the row is INSERTed first so it has
-- an id, and the code is minted and UPDATEd in second. So a row transiently
-- held the column default on EVERY create. Adding a UNIQUE index while that
-- default was '0' would have broken the next group creation in production --
-- and it is what broke eight live-DB tests when 040 tried it, since five test
-- helpers insert a tag_group row without a group_code.
--
-- WHAT THIS DOES.
--
-- 1. Re-codes the 41 codeless rows rather than NULLing them, so every existing
--    group becomes addressable by code instead of permanently unaddressable.
--    CONCAT('G', LPAD(id,5,'0')) is unique because id is, deterministic (so
--    this file is re-runnable), 6 characters, and uses only the [A-Z0-9] of
--    generate_uid's UID_CHARS. Verified collision-free against the real codes.
--    Note the 23 real codes containing punctuation ('RO1-CU', 'TR#1LO') --
--    they pre-date generate_uid, are still valid handles, and are left alone.
--
-- 2. Changes the column default from '0' to NULL. NULLs are exempt from
--    UNIQUE, so the default becomes a harmless safety valve rather than a
--    collision: a path that forgets to mint yields an unaddressable group,
--    exactly as today, instead of a failed insert. This is also what keeps the
--    five live-DB test helpers passing unchanged.
--
-- 3. Adds the index. The application half needs no new work: this table's
--    MySQLTagGroupJsonDb already declares GENERATED_UNIQUE_FIELDS =
--    ('group_code',) (mysqldb.py:241), so commit() re-mints and retries on a
--    duplicate-key error, as it does for hunts.uid.
--
-- ORDER MATTERS: the UPDATE is recoverable, the two ALTERs auto-commit. If the
-- index ever fails, the re-coding and the new default persist and are correct
-- on their own -- fix forward, then re-run.
--
-- Re-run the pre-flight before applying to any other environment:
--
--   SELECT group_code, COUNT(*) n FROM tag_group
--    WHERE group_code IS NOT NULL AND CHAR_LENGTH(group_code) = 6
--    GROUP BY group_code HAVING n > 1;
--
-- Rows there mean two groups share a handle people type; decide which keeps it
-- before applying, rather than letting a migration pick.

UPDATE `tag_group`
   SET `group_code` = CONCAT('G', LPAD(`id`, 5, '0'))
 WHERE `group_code` IS NULL
    OR CHAR_LENGTH(`group_code`) <> 6;

ALTER TABLE `tag_group`
  MODIFY COLUMN `group_code` char(6) DEFAULT NULL;

ALTER TABLE `tag_group`
  ADD UNIQUE INDEX `uq_tag_group_group_code` (`group_code`);
