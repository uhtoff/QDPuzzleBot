-- 046_backfill_additional_sheet_locations.sql
-- Correct 045's backfill for the rows production actually had on the archive.
--
-- WHY THIS EXISTS.
--
-- 045 added additional_sheets.on_solved_sheet and backfilled every row to 0
-- ("on the main spreadsheet"). Its header records the reasoning: decision D8
-- held that no hunt had used a solved/archive spreadsheet since the current
-- bot, so no extra sheet could be on one. That is TRUE of puzzlebot_test --
-- its six candidate rows were checked against the live Sheets API and every
-- one was dangling, pointing at a tab that no longer existed on either
-- spreadsheet.
--
-- It is FALSE of production. 045's own pre-flight query returned seven rows
-- there, and checking each page id against the Sheets API (metadata only) on
-- 2026-08-31 found SIX genuinely living on their hunt's solved spreadsheet:
--
--   id  page id     tab                      puzzle                    hunt
--    6  750042390   Name Badges              Physical Items            MITMH 2026    -> MAIN
--    7  759535652   Long Grid                strand type game          MITMH 2026    -> ARCHIVE
--    8  1043724513  No Clues                 Can you really call...    MITMH 2026    -> ARCHIVE
--    9  1467996436  Meta                     Can you really call...    MITMH 2026    -> ARCHIVE
--   10  88396963    The Palace and Its Cats  Doubles Games             MITMH 2026    -> ARCHIVE
--   11  797174413   Sunshine East of Eden    Doubles Games             MITMH 2026    -> ARCHIVE
--   12  944991075   Hope                     Puzzles Within...         FlatDurian    -> ARCHIVE
--
-- Left at 0 deliberately: id 6, whose tab really is still on the main
-- spreadsheet despite its puzzle being solved -- the extras were never moved.
-- 0 is correct for it, and forcing it to 1 would create the mirror-image bug.
--
-- WHAT GOES WRONG WITHOUT THIS. A row flagged 0 while its tab lives on the
-- archive is exactly the state H3 describes: restore_puzzle_from_solved_sheet
-- leaves the sheet where it is and then addresses its archive-side page id to
-- the MAIN spreadsheet. Sheets answers 400, which is not retryable, and
-- unsolve has no try/except around it -- so unsolving any of those four
-- puzzles half-restores it. 045 would have introduced, on live hunts, the
-- precise failure it was written to prevent.
--
-- WHY IT IS A SEPARATE FILE. 045 has already been applied to puzzlebot_test,
-- and an applied migration is not edited -- even though this runner tracks by
-- filename and would not notice. Test needs no correction (all its candidates
-- were dangling), and this file matching nothing there is the expected result.
--
-- WHY (id, google_page_id) AND NOT id ALONE. Row ids are per-environment: id 7
-- names a different sheet on puzzlebot_test than on production. Pairing each
-- id with the page id observed on production makes this a no-op anywhere the
-- data does not match, rather than silently flagging six arbitrary rows as
-- archived. It is also re-runnable: applying it twice sets the same rows to
-- the same value.
--
-- VERIFY AFTER APPLYING (expect exactly the six ids below, and id 6 absent):
--
--   SELECT id, google_page_id, on_solved_sheet FROM additional_sheets
--    WHERE on_solved_sheet = 1 ORDER BY id;

UPDATE `additional_sheets`
   SET `on_solved_sheet` = 1
 WHERE (`id`, `google_page_id`) IN (
       (7,  '759535652'),
       (8,  '1043724513'),
       (9,  '1467996436'),
       (10, '88396963'),
       (11, '797174413'),
       (12, '944991075')
 );
