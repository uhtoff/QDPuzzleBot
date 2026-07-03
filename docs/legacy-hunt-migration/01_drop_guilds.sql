-- 01_drop_guilds.sql
-- Prune unwanted guilds from the legacy DB BEFORE running the schema migrations.
-- Runs against the pre-migration baseline schema (tables: guilds, hunts, rounds,
-- puzzles, tags).
--
-- Guilds dropped: 1 (NULL), 3 (QD Test), 7 (team meeting hunt server),
--                 8 (QD testing new bot).
-- Kept:          2 (Uhtoff), 4 (QD Main), 5 (QD MIT 2025), 6 (QD MIT 2026),
--                 9 (solo hunts), 10 (The Tulgey Wood).
--
-- Order matters: `puzzles` has NO foreign-key cascade to `hunts` in the baseline
-- schema, so its rows must be deleted explicitly first. Deleting the guilds then
-- cascades hunts -> rounds -> tags (tags.RoundID / PuzzleID are ON DELETE CASCADE).

-- 1. Puzzles belonging to hunts under the dropped guilds (their tags cascade via
--    the PuzzleID FK).
DELETE FROM `puzzles`
WHERE `hunt_id` IN (
  SELECT `id` FROM `hunts` WHERE `guild_id` IN (1, 3, 7, 8)
);

-- 2. The guild rows themselves -> cascades hunts -> rounds -> remaining tags.
DELETE FROM `guilds` WHERE `id` IN (1, 3, 7, 8);

-- Observed effect: -10 hunts, -37 rounds, -70 puzzles, -71 tags.
-- Leaves 6 guilds / 115 hunts, zero orphan tags.
