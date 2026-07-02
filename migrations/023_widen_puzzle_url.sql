-- 023_widen_puzzle_url.sql
-- Finding #61 (docs/reviews/2026-07-02-staging-full-review.md): puzzles.url is
-- varchar(150) while hunts.url is varchar(250), and create_puzzle composes the
-- puzzle URL as hunt.url + "/" + prefix + "/" + slug with no length check, so a
-- hunt URL near its own column limit plus a normal puzzle name can overflow
-- puzzles.url -- aborting puzzle creation (under strict sql_mode) after the
-- Google sheet was already created, or silently truncating the stored link.
-- Widen to varchar(512) (matching hunts.website_url) so the composed URL fits
-- with generous headroom for any hunt.url + prefix + slug combination.

ALTER TABLE `puzzles`
  MODIFY COLUMN `url` varchar(512) DEFAULT NULL;
