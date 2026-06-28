-- Multi-edge memberships: a (puzzle, group) pair may carry several typed edges
-- (e.g. feeds + informs). Widen the uniqueness to include edge_type. Existing rows
-- are already unique under the wider key (they were unique under (tag_group_id,
-- puzzle_id) and edge_type is NOT NULL), so no dedup/backfill is required.
ALTER TABLE `tags`
  DROP KEY `tag_uq`,
  ADD UNIQUE KEY `tag_uq` (`tag_group_id`, `puzzle_id`, `edge_type`);
