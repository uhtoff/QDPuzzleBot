-- Feeder matrix: where a minimeta's feeder tab lives.
--
-- A minimeta with a non-empty feeder pool gets its own "Feeders — <name>" tab, a
-- matrix of feeders against claim targets. puzzles.google_page_id already names the
-- puzzle's OWN tab and rollups.tab_page_id is spent on the mini-list rollup, so the
-- matrix tab needs its own column.
--
-- NULL means "no matrix tab yet" — the overwhelming majority of puzzles, forever.
--
-- Down: ALTER TABLE puzzles DROP COLUMN feeder_tab_page_id;

ALTER TABLE `puzzles` ADD COLUMN `feeder_tab_page_id` int(11) DEFAULT NULL;
