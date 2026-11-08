-- 059_voice_channels.sql
-- Voice channels per hunt and per puzzle. `voice_channel_id` is the hunt's own
-- shared voice channel (where a closing puzzle channel's occupants are moved);
-- `voice_per_puzzle` is the admin toggle for giving each puzzle its own voice
-- channel. (puzzles.voice_channel_id already exists in 000_base_schema.sql.)
--
-- PRE-FLIGHT: none needed. Additive columns with defaults.

ALTER TABLE `hunts`
  ADD COLUMN `voice_channel_id` BIGINT UNSIGNED NULL DEFAULT NULL,
  ADD COLUMN `voice_per_puzzle` TINYINT(1) NOT NULL DEFAULT 0;
