-- 019_target_edge.sql
-- A target puzzle is a member of its own group but neither feeds nor informs it.
-- Migration 018 normalised its self-edge to 'none', but 'none' is also the edge of
-- a genuine pooled member, so in explicit/pooled rollups the target collided with
-- real pooled feeders and could only be told apart by an id comparison. Give the
-- target a self-describing 'target' edge instead. Only the row where the puzzle IS
-- the group's target is touched; the puzzle's feeder edges to OTHER groups (and any
-- deliberate self-feed loop with a 'feeds'/'informs' edge) are left untouched.
UPDATE `tags`
  JOIN `tag_group` ON `tag_group`.`id` = `tags`.`tag_group_id`
  SET `tags`.`edge_type` = 'target'
  WHERE `tag_group`.`target_puzzle_id` = `tags`.`puzzle_id`
    AND `tags`.`edge_type` = 'none';
