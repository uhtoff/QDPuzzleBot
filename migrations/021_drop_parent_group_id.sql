-- Remove the dormant `parent_group_id` group-in-group NESTING column from
-- tag_group. Its only mechanism (set_parent_group + the /tag_groups/<id>/nest
-- endpoint) was removed when the runaround subtree became a transitive 'feeds'
-- walk (collect_subtree_puzzles); no runtime code reads or writes it.
--
-- ORDERING (the reverse of the additive migration->deploy->code rule): deploy the
-- code that drops the `parent_group_id` dataclass field FIRST, then run this
-- migration. The generic commit() writes every dataclass field by name, so the
-- field must be gone before the column is — otherwise an UPDATE names a missing
-- column. New code running against the still-present column is safe (its INSERT
-- omits the column, which falls back to DEFAULT NULL; reads ignore it).
--
-- Drop the foreign key first (it pins the column); dropping the column then
-- removes its single-column index (named `ParentGroupID`) automatically.
ALTER TABLE `tag_group`
  DROP FOREIGN KEY `ParentGroupID`,
  DROP COLUMN `parent_group_id`;
