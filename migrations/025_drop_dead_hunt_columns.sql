-- Drop two long-dead columns from `hunts`:
--   * `num_rounds`    — never read or written by any code; `000_base_schema.sql`
--                       already documents it as vestigial. Round counts are
--                       derived from `tag_group` rows at query time.
--   * `parallel_hunt` — a flag no code path ever set or read.
--
-- Neither is a `HuntData` dataclass field, so the generic `commit()` never names
-- them and no read references them. Unlike 021 this needs NO code-deploy ordering:
-- current code is already oblivious to both columns, before and after the drop.
ALTER TABLE `hunts`
  DROP COLUMN `num_rounds`,
  DROP COLUMN `parallel_hunt`;
