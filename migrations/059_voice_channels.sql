-- 059_voice_channels.sql
-- Voice channels per hunt and per puzzle. `voice_channel_id` is the hunt's own
-- shared voice channel (where a closing puzzle channel's occupants are moved);
-- `voice_per_puzzle` is the admin toggle for giving each puzzle its own voice
-- channel. (puzzles.voice_channel_id already exists in 000_base_schema.sql.)
-- Both voice_channel_id columns are indexed: every voice join/leave in a guild
-- looks its channel up by them (bot/core/voice_presence.py).
--
-- PRE-FLIGHT: none needed. Additive columns with defaults, and plain indexes.

ALTER TABLE `hunts`
  ADD COLUMN `voice_channel_id` BIGINT UNSIGNED NULL DEFAULT NULL,
  ADD COLUMN `voice_per_puzzle` TINYINT(1) NOT NULL DEFAULT 0,
  ADD KEY `idx_hunts_voice_channel_id` (`voice_channel_id`);

ALTER TABLE `puzzles`
  ADD KEY `idx_puzzles_voice_channel_id` (`voice_channel_id`);
