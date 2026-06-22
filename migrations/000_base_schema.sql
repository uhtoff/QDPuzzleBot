-- Base schema for QDPuzzleBot (MySQL/MariaDB, InnoDB, utf8mb4).
--
-- This is the checked-in CREATE TABLE DDL for every core table the bot reads
-- and writes. Before this file existed there was no tracked DDL at all, so a
-- fresh clone could not stand up a database. The migration runner
-- (../migrate.py) applies this first, then the numbered migrations on top.
--
-- It is transcribed verbatim from the live schema (column types, defaults,
-- foreign keys and all) so a fresh database is byte-for-byte what production
-- runs -- EXCEPT it represents the PRE-DASHBOARD state on purpose: the
-- `archived` column on `hunts` and the `channel_activity` table are added by
-- 001_dashboard.sql, so that additive migration applies cleanly on top.
--
-- Notes on reality vs the dataclasses (kept faithful to the live DB):
--   * `hunts.guild_id` is an INT FK to `guilds.id` (the row id), NOT a Discord
--     snowflake. The snowflake lives in `guilds.guild_id`.
--   * `hunts.num_rounds` and `puzzles.locked` exist in the live schema but are
--     not in any dataclass and are not written by current code (legacy columns,
--     kept for fidelity; both have safe defaults).
--   * `rounds.meta_code` is CHAR(6) holding a 6-char string, though RoundData
--     annotates it `int` (a known latent bug; fixed in M6).
--   * `rounds.meta_id` is the `puzzles.id` of the round's meta puzzle.
--   * Table creation order respects the FK graph: guilds <- hunts <- rounds,
--     puzzles <- tags. Keep these columns in lockstep with the dataclasses in
--     bot/store/*.py -- the generic commit() writes every field by name, so a
--     missing column crashes inserts. Follow migration -> deploy -> code.

CREATE TABLE IF NOT EXISTS `guilds` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `guild_id` bigint(20) unsigned DEFAULT NULL,
  `guild_name` varchar(150) DEFAULT NULL,
  `website_url` varchar(512) DEFAULT NULL,
  `discord_bot_channel` varchar(20) DEFAULT 'bot-set-up',
  `discord_bot_emoji` varchar(250) DEFAULT NULL,
  `discord_use_voice_channels` tinyint(1) DEFAULT NULL,
  `drive_parent_id` varchar(50) DEFAULT NULL,
  `drive_resources_id` varchar(50) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `guilds_id` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `hunts` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `uid` varchar(12) DEFAULT NULL,
  `category_id` bigint(20) unsigned DEFAULT NULL,
  `guild_id` int(11) DEFAULT NULL,
  `channel_id` bigint(20) unsigned DEFAULT NULL,
  `url_sep` char(1) DEFAULT NULL,
  `name` varchar(150) DEFAULT NULL,
  `num_rounds` smallint(5) unsigned DEFAULT 0,
  `url` varchar(250) DEFAULT NULL,
  `puzzle_prefix` varchar(50) DEFAULT 'puzzle',
  `google_sheet_id` varchar(50) DEFAULT NULL,
  `archive_google_sheet_id` varchar(50) DEFAULT NULL,
  `parallel_hunt` tinyint(1) DEFAULT NULL,
  `role_id` bigint(20) unsigned DEFAULT NULL,
  `username` varchar(150) DEFAULT NULL,
  `password` varchar(150) DEFAULT NULL,
  `start_timestamp` int(11) DEFAULT NULL,
  `start_time` datetime DEFAULT NULL,
  `solve_time` datetime DEFAULT NULL,
  `archive_time` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `GuildID` (`guild_id`),
  CONSTRAINT `GuildID` FOREIGN KEY (`guild_id`) REFERENCES `guilds` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `rounds` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `hunt_id` int(11) DEFAULT NULL,
  `meta_id` int(11) DEFAULT NULL,
  `meta_code` char(6) DEFAULT '0',
  `category_id` bigint(20) unsigned DEFAULT NULL,
  `name` varchar(150) DEFAULT NULL,
  `type` varchar(25) DEFAULT NULL,
  `start_time` datetime DEFAULT NULL,
  `solve_time` datetime DEFAULT NULL,
  `archive_time` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `HuntID` (`hunt_id`),
  CONSTRAINT `HuntID` FOREIGN KEY (`hunt_id`) REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `puzzles` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(150) DEFAULT NULL,
  `url` varchar(150) DEFAULT NULL,
  `channel_name` varchar(100) DEFAULT NULL,
  `hunt_id` int(11) DEFAULT NULL,
  `channel_id` bigint(20) unsigned DEFAULT NULL,
  `channel_mention` varchar(150) DEFAULT NULL,
  `voice_channel_id` bigint(20) unsigned DEFAULT NULL,
  `google_page_id` varchar(150) DEFAULT NULL,
  `metapuzzle` tinyint(1) DEFAULT 0,
  `metameta` tinyint(1) NOT NULL DEFAULT 0,
  `status` varchar(150) DEFAULT NULL,
  `locked` tinyint(1) NOT NULL DEFAULT 0,
  `solved` tinyint(1) DEFAULT 0,
  `archived` tinyint(1) DEFAULT 0,
  `solution` text DEFAULT NULL,
  `priority` varchar(50) DEFAULT NULL,
  `puzzle_type` varchar(50) DEFAULT NULL,
  `notes` longtext DEFAULT NULL,
  `start_time` datetime DEFAULT NULL,
  `solve_time` datetime DEFAULT NULL,
  `archive_time` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `tags` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `round_id` int(11) NOT NULL,
  `puzzle_id` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `tag_index` (`round_id`,`puzzle_id`),
  KEY `PuzzleID` (`puzzle_id`),
  CONSTRAINT `PuzzleID` FOREIGN KEY (`puzzle_id`) REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `RoundID` FOREIGN KEY (`round_id`) REFERENCES `rounds` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `additional_sheets` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `puzzle_id` int(11) DEFAULT NULL,
  `puzzle` tinyint(1) NOT NULL DEFAULT 0,
  `solved` tinyint(1) NOT NULL DEFAULT 0,
  `puzzle_name` varchar(250) DEFAULT NULL,
  `solution` varchar(250) DEFAULT NULL,
  `google_page_id` varchar(150) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
