-- 018_target_edge_none.sql
-- A target puzzle is a member of its own group but neither feeds nor informs it,
-- so its self-edge is 'none'. Historically targets inherited the 'feeds' column
-- default, which made them appear as feeders of themselves and tripped the
-- metameta classifier. Normalise every existing target's self-edge to 'none'.
-- Only the row where the puzzle IS the group's target is touched; the puzzle's
-- feeder edges to OTHER groups are left untouched.
UPDATE `tags`
  JOIN `tag_group` ON `tag_group`.`id` = `tags`.`tag_group_id`
  SET `tags`.`edge_type` = 'none'
  WHERE `tag_group`.`target_puzzle_id` = `tags`.`puzzle_id`;
