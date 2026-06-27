-- Collapse group types (code-review finding #1):
--   Round          -> Metapuzzle  (Round is now a create-time alias only)
--   Metaless Round -> Group       (retired entirely)
-- Spec: docs/superpowers/specs/2026-06-27-group-type-consolidation-design.md
UPDATE tag_group SET type = 'Metapuzzle' WHERE type = 'Round';
UPDATE tag_group SET type = 'Group'      WHERE type = 'Metaless Round';
