# TagGroup — schema changes (Milestones A + B storage)

**Date:** 2026-06-25
**Status:** Spec; not yet built.
**Parent design:** `docs/superpowers/specs/2026-06-24-taggroup-edges-and-commands-design.md`
(the "Schema vocabulary alignment" and "Edge model & storage" sections).

This spec covers the **database schema changes plus the full domain-vocabulary
symbol sweep** — the migrations, exact DDL, value rewrites, and the rename of every
durable `round`/`meta` identifier (classes, modules, functions, variables, JSON keys,
frontend types) to the agreed `tag_group`/`target` vocabulary. Per the decision to
"keep it all together," the symbol sweep is **in scope for Milestone A**, not deferred.
The command *surface* (new bang commands `!feeds`/`!informs`/…, Milestone C) and sheet
generation (D) remain out of scope — see the carve-out below for the handful of
command handlers C rewrites, which A deliberately leaves alone to avoid renaming code
that C deletes.

It maps the design's milestone split onto **two migrations**:

- **Migration 011 — structural renames + full symbol sweep (Milestone A).** Pure,
  behaviour-preserving. Table + column renames in the DB, **plus** the complete
  `round`/`meta` → `tag_group`/`target` rename across classes, modules, functions,
  variables, JSON keys, routes, and frontend. Leads, so later feature code is written
  against the agreed names.
- **Migration 012 — edge model & storage (Milestone B).** `edge_type` value
  rewrites, default changes, drop `kind`. Pairs with the edge/`informs` work.

> Migration numbers `011`/`012` are provisional. Per [[migration-number-collisions]],
> check the highest committed migration against any parallel worktree immediately
> before merging and renumber if needed.

---

## Current schema (verified, as of migration 010)

The relevant live DDL today:

```sql
-- rounds  (000_base_schema.sql + 003_typed_groups.sql)
CREATE TABLE `rounds` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `hunt_id` int(11) DEFAULT NULL,
  `meta_id` int(11) DEFAULT NULL,              -- the target puzzle (NO FK constraint)
  `meta_code` char(6) DEFAULT '0',
  `category_id` bigint(20) unsigned DEFAULT NULL,
  `name` varchar(150) DEFAULT NULL,
  `type` varchar(25) DEFAULT NULL,             -- the declared axis (kept)
  `start_time` datetime DEFAULT NULL,
  `solve_time` datetime DEFAULT NULL,
  `archive_time` datetime DEFAULT NULL,
  `kind` varchar(20) DEFAULT NULL,             -- meta/metameta/metaless (to drop)
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `parent_group_id` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `HuntID` (`hunt_id`),
  CONSTRAINT `HuntID` FOREIGN KEY (`hunt_id`) REFERENCES `hunts` (`id`) ...,
  CONSTRAINT `ParentGroupID` FOREIGN KEY (`parent_group_id`) REFERENCES `rounds` (`id`) ...
);

-- tags  (000 + 003 + 005)
CREATE TABLE `tags` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `round_id` int(11) NOT NULL,
  `puzzle_id` int(11) NOT NULL,
  `edge_type` varchar(32) NOT NULL DEFAULT 'feeds-meta',
  PRIMARY KEY (`id`),
  UNIQUE KEY `tag_uq` (`round_id`, `puzzle_id`),
  KEY `PuzzleID` (`puzzle_id`),
  CONSTRAINT `PuzzleID` FOREIGN KEY (`puzzle_id`) REFERENCES `puzzles` (`id`) ...,
  CONSTRAINT `RoundID`   FOREIGN KEY (`round_id`)  REFERENCES `rounds` (`id`) ...
);

-- rollups  (002 + 008)
CREATE TABLE `rollups` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `hunt_id` int(11) NOT NULL,
  `round_id` int(11) NULL,
  `target_puzzle_id` int(11) DEFAULT NULL,
  `tab_page_id` bigint DEFAULT NULL,
  `metadata_id` bigint DEFAULT NULL,
  `source_mode` varchar(32) NOT NULL DEFAULT 'round',
  `edge_type_filter` varchar(32) DEFAULT 'feeds-meta',   -- NB: also carries old value
  `last_ok` tinyint(1) NOT NULL DEFAULT 1,
  `last_error` text DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_rollups_round` (`round_id`),
  UNIQUE KEY `uq_rollups_target_puzzle` (`target_puzzle_id`),
  KEY `idx_rollups_hunt` (`hunt_id`),
  CONSTRAINT `RollupHuntID`        FOREIGN KEY (`hunt_id`) REFERENCES `hunts` (`id`) ...,
  CONSTRAINT `RollupRoundID`       FOREIGN KEY (`round_id`) REFERENCES `rounds` (`id`) ...,
  CONSTRAINT `RollupTargetPuzzleID` FOREIGN KEY (`target_puzzle_id`) REFERENCES `puzzles` (`id`) ...
);
```

`evidence_items` (009) and `puzzles` need **no schema change** — `evidence_items` is
already keyed `(source_puzzle_id, target_puzzle_id, note)` with no uniqueness on the
pair, which is exactly the "many payload rows per (source, target), one `informs`
edge" the design requires. `informs` is materialised as a `tags.edge_type` value, not
a new table.

### Deltas the design doc missed / under-specified

1. **`rollups.edge_type_filter`** also defaults to `'feeds-meta'` and stores it in
   existing rows. It is **not** in the design's rename table but must be rewritten to
   `'feeds'` (value + column default) alongside `tags.edge_type`, or the
   feeds-filtered subtree harvest silently matches nothing.
2. **`rounds.meta_id` has no FK constraint** — its rename is a plain `CHANGE COLUMN`,
   no FK to drop/recreate.
3. **`rounds.status` and `rounds.type` are retained.** `type` is the declared axis
   (gains `Capstone`/`Runaround`/`Group` values at the code layer, no DDL). `status`
   is untouched by this work.

---

## Migration 011 — structural renames (Milestone A)

Pure renames, no row rewrites, no behaviour change. InnoDB on MySQL 8.0 renames
columns that participate in FKs and indexes online; the FK targets and the `tag_uq`
unique key follow the rename automatically.

```sql
-- 011_taggroup_rename.sql

-- 1. The umbrella table.  FKs that REFERENCE rounds (tags.RoundID,
--    rollups.RollupRoundID, the parent_group_id self-FK) auto-repoint to tag_group.
RENAME TABLE `rounds` TO `tag_group`;

-- 2. Target puzzle + group code (no FK on meta_id).
ALTER TABLE `tag_group`
  CHANGE COLUMN `meta_id`   `target_puzzle_id` int(11)  DEFAULT NULL,
  CHANGE COLUMN `meta_code` `group_code`       char(6)  DEFAULT '0';

-- 3. FK columns follow the table rename.  CHANGE COLUMN preserves the index
--    (tag_uq) and the FK; only the column name changes.
ALTER TABLE `tags`
  CHANGE COLUMN `round_id` `tag_group_id` int(11) NOT NULL;

ALTER TABLE `rollups`
  CHANGE COLUMN `round_id` `tag_group_id` int(11) NULL;
```

**FK constraint names** (`RoundID`, `RollupRoundID`, `ParentGroupID`) are left as-is —
renaming a constraint is cosmetic, costs an extra drop/recreate, and the names are not
part of the model vocabulary. Note this explicitly so a later reader doesn't think it
was missed. (Optional, deferrable: rename to `TagGroupID` / `RollupTagGroupID` if a
later cleanup wants the constraint names to read true.)

`parent_group_id` is **unchanged** — it already names "group" correctly; its self-FK
auto-repoints to `tag_group` via the `RENAME TABLE`.

**Verification (011):** schema-only. Confirm `SHOW CREATE TABLE tag_group/tags/rollups`
shows the new names, `tag_uq` still spans `(tag_group_id, puzzle_id)`, and all three FKs
resolve. Run the existing suite green against renamed code (see Code impact below) — this
migration ships **with** its code sweep as one unit, since the rename is breaking for old
code (dev-only, no deploy hazard).

---

## Migration 012 — edge values + drop kind (Milestone B)

Behavioural phase. Rewrite the `edge_type` vocabulary, retarget the column defaults,
and drop the now-derived `kind` column. `'informs'` needs **no DDL** — it is a new
value in an existing `varchar`.

```sql
-- 012_edge_model.sql

-- 1. Rewrite stored edge values (four -> two + none; reveals/part-of unused live).
UPDATE `tags`    SET `edge_type` = 'feeds' WHERE `edge_type` = 'feeds-meta';
UPDATE `tags`    SET `edge_type` = 'none'  WHERE `edge_type` = 'aggregates-into';
UPDATE `rollups` SET `edge_type_filter` = 'feeds'
                 WHERE `edge_type_filter` = 'feeds-meta';

-- 2. Retarget the column defaults so new rows speak the new vocabulary.
ALTER TABLE `tags`
  ALTER COLUMN `edge_type` SET DEFAULT 'feeds';
ALTER TABLE `rollups`
  ALTER COLUMN `edge_type_filter` SET DEFAULT 'feeds';

-- 3. Drop kind — now derived (has-target = target_puzzle_id IS NOT NULL;
--    fed-vs-informed from incoming edges; metameta from structure).
ALTER TABLE `tag_group`
  DROP COLUMN `kind`;
```

Notes:
- The `UPDATE`s are idempotent (rerunning matches nothing) and must run **before** the
  default change so no in-flight insert lands an old value between steps. Since this is
  dev-only and lands as a unit, ordering within the one migration is sufficient.
- No `reveals` / `part-of` rows exist live (confirmed by the codebase sweep), so no
  rewrite is needed for them — they were design-only. `part-of` is simply never
  introduced; clusters stay `puzzles.parent_puzzle_id` (M8).
- Column stays a plain `varchar(32)` — **no `ENUM` / `CHECK`**. The design treats
  meta/capstone as derived and edges as open-ended; an enum would re-stamp a closed
  taxonomy the design just dissolved. Allowed values (`feeds` / `informs` / `none`) are
  enforced in code, not the column.

**Verification (012):** `SELECT DISTINCT edge_type FROM tags` ⊆ {feeds, informs, none};
`SELECT DISTINCT edge_type_filter FROM rollups` ⊆ {feeds}; `kind` column gone; derived
helpers (has-target / fed-vs-informed / metameta) return the same answers the old `kind`
gave on a representative fixture.

---

## Resulting target DDL (after 011 + 012)

```sql
CREATE TABLE `tag_group` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `hunt_id` int(11) DEFAULT NULL,
  `target_puzzle_id` int(11) DEFAULT NULL,   -- was meta_id; NULL for a base Group
  `group_code` char(6) DEFAULT '0',          -- was meta_code; the "groupcode"
  `category_id` bigint(20) unsigned DEFAULT NULL,
  `name` varchar(150) DEFAULT NULL,
  `type` varchar(25) DEFAULT NULL,           -- declared axis: Round/Metapuzzle/Capstone/Runaround/Group
  `start_time` datetime DEFAULT NULL,
  `solve_time` datetime DEFAULT NULL,
  `archive_time` datetime DEFAULT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `parent_group_id` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `HuntID` (`hunt_id`),
  CONSTRAINT `HuntID` FOREIGN KEY (`hunt_id`) REFERENCES `hunts` (`id`) ...,
  CONSTRAINT `ParentGroupID` FOREIGN KEY (`parent_group_id`) REFERENCES `tag_group` (`id`) ...
);

CREATE TABLE `tags` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `tag_group_id` int(11) NOT NULL,           -- was round_id
  `puzzle_id` int(11) NOT NULL,
  `edge_type` varchar(32) NOT NULL DEFAULT 'feeds',   -- feeds | informs | none
  PRIMARY KEY (`id`),
  UNIQUE KEY `tag_uq` (`tag_group_id`, `puzzle_id`),  -- one edge per (member, group)
  KEY `PuzzleID` (`puzzle_id`),
  CONSTRAINT `PuzzleID` FOREIGN KEY (`puzzle_id`) REFERENCES `puzzles` (`id`) ...,
  CONSTRAINT `RoundID`  FOREIGN KEY (`tag_group_id`) REFERENCES `tag_group` (`id`) ...
);

CREATE TABLE `rollups` (
  ...
  `tag_group_id` int(11) NULL,               -- was round_id
  `target_puzzle_id` int(11) DEFAULT NULL,
  `source_mode` varchar(32) NOT NULL DEFAULT 'round',
  `edge_type_filter` varchar(32) DEFAULT 'feeds',
  ...
);
```

---

## Code impact (ships with the migrations)

Renames are breaking for old code, so each migration lands **with** its code sweep as
one unit. Listed by what each migration touches; this is the data-layer footprint, not
the command surface (C).

### With 011 — the full symbol sweep (in scope)

> **Note on "HuntRound".** The design doc's open item names "the `HuntRound` class",
> but no such class exists. The real durable symbol is the **`RoundData` dataclass**
> (`bot/store/round_data.py`) and the **`hunt_round` local variable** (a `RoundData`
> instance, the "current group" in context — **123 occurrences**). The sweep renames
> those.

**Storage layer — dataclasses, modules, DB classes** (`bot/store/`):

| from | to |
|---|---|
| module `round_data.py` | `tag_group_data.py` |
| class `RoundData` | `TagGroupData` |
| `RoundData.meta_id` | `.target_puzzle_id` |
| `RoundData.meta_code` (`str`; fix the stale `int` comment) | `.group_code` |
| `RoundData.kind` | *removed in 012* (derived) |
| exception `MissingRoundError` | `MissingTagGroupError` |
| abstract `_RoundJsonDb` | `_TagGroupJsonDb` |
| class `MySQLRoundJsonDb` (`mysqldb.py`) | `MySQLTagGroupJsonDb` |
| `MySQLRoundJsonDb.check_duplicate_meta_code` | `.check_duplicate_group_code` |
| `MySQLRoundJsonDb.delete(round_id)` | `.delete(tag_group_id)` |
| `MySQLPuzzleJsonDb.get_all_from_round(round_id)` | `.get_all_from_tag_group(tag_group_id)` |
| `tag_puzzle_with_edge(..., round_id, ...)` param | `..., tag_group_id, ...` |
| `RollupData.round_id` (`rollup_data.py`) | `.tag_group_id` |
| `MySQLRollupJsonDb` `round_id` reads/writes | `tag_group_id` |
| `PuzzleData.tag_edges: Dict[int,str]` key | now `tag_group_id`-keyed (no type change) |
| local var `round_data` (in `import_dict`) | `tag_group_data` |

**Core service** (`bot/core/puzzle_service.py`) — rename internal helpers and the
`hunt_round` variable throughout. **Bold = handler names the new commands call**:

| from | to |
|---|---|
| local/param `hunt_round`, `meta_round`, `child_round`, `root_round` | `tag_group`, `target_group`, `child_group`, `root_group` |
| **`create_round(...)`** | **`create_tag_group(...)`** |
| **`refresh_meta(...)` / `refresh_metas(...)`** | **`refresh_target(...)` / `refresh_targets(...)`** |
| `_find_or_create_rollup(hunt_round)` | `_find_or_create_rollup(tag_group)` (param only) |
| `select_rollup_members(hunt_round, rollup)` | `select_rollup_members(tag_group, rollup)` (param only) |
| `collect_subtree_rounds(root_round)` | `collect_subtree_groups(root_group)` |
| `collect_subtree_puzzles(root_round)` | `collect_subtree_puzzles(root_group)` (param only) |
| `set_parent_group(child_round, parent_id)` | `set_parent_group(child_group, parent_id)` (param only) |

> **`minimeta` / `metapuzzle` / `metameta` are NOT renamed here.** They name a puzzle's
> *role* (`puzzles.metapuzzle`, `.metameta`, `.parent_puzzle_id`), which the design
> keeps — "meta" stays a valid word for *a fed target*. Only the **group=round** and
> **meta_id/meta_code=target** vocabulary moves. So `make_minimeta`, `refresh_minimeta`,
> `get_minis`, `add_metapuzzle_data`, `add_metametapuzzle_data` keep their names.

**Context helpers** (`bot/cogs/puzzles.py`):

| from | to |
|---|---|
| `get_hunt_round(ctx)` | `get_tag_group(ctx)` |
| `set_hunt_round(ctx, hunt_round)` | `set_tag_group(ctx, tag_group)` |
| `meta_code_autocomplete(...)` | `group_code_autocomplete(...)` |

**Web layer** (`bot/web/`) — handler/data-function names and **JSON keys**:

| from | to |
|---|---|
| `get_hunt_for_round(round_id)` (`data.py`) | `get_hunt_for_tag_group(tag_group_id)` |
| `get_rollups_by_round(hunt_id)` | `get_rollups_by_tag_group(hunt_id)` |
| `build_board(..., rollups_by_round)` | `build_board(..., rollups_by_tag_group)` |
| `ensure(round_id, name, meta_code)` | `ensure(tag_group_id, name, group_code)` |
| route param `<int:round_id>` (rollups/rounds routes) | `<int:tag_group_id>` |
| JSON key `"round_id"` | `"tag_group_id"` |
| JSON key `"meta_code"` | `"group_code"` |
| JSON key `"is_meta_of"` | `"is_target_of"` |
| JSON keys `"meta_solution"`/`"meta_solved"` | `"target_solution"`/`"target_solved"` |
| error code `"bad_meta_code"` | `"bad_group_code"` |

> **URL *paths* are a judgement call.** Renaming `/rounds/<id>` → `/tag_groups/<id>`
> and `/hunts/<uid>/metas` → `/.../targets` is consistent but touches the frontend's
> fetch URLs too. Since this whole body of work is dev-only and ships as a unit (no
> live clients), **rename the paths as well** for full consistency. The route
> *handler* function names (`create_meta`, `retag_meta`, `delete_round`,
> `set_source_mode`, `rebuild_rollup`) follow.

**Frontend** (`bot/frontend/src/`) — types, api, components must track the renamed
JSON keys and any renamed paths:

| from | to |
|---|---|
| `interface PuzzleRound` (`types.ts`) | `PuzzleTagGroup` |
| `interface BoardGroup` field `round_id` | `tag_group_id` |
| `meta_code` / `is_meta_of` / `meta_solution` / `meta_solved` fields | `group_code` / `is_target_of` / `target_solution` / `target_solved` |
| `api.ts` payload keys `meta_code`, `parent_round_id` | `group_code`, `parent_tag_group_id` |
| error cases `"round_exists"`, `"bad_meta_code"` | match renamed server codes |
| component refs (`CreateControls`, `BoardGroup`, `DragBoard`) | follow the type/key renames |

> `source_mode` and its values (`round`/`subtree`/`explicit`) are **unchanged** — the
> value `'round'` there means the aggregation strategy, not the entity, and the design
> does not rename it.

**Tests** — ~38 files reference the vocabulary; class names like `TestWebAddToMeta`,
files like `test_refresh_meta.py`, `test_round_status.py` follow the symbol renames
mechanically. Test *file* renames (`test_refresh_meta.py` → `test_refresh_target.py`)
are optional polish; renaming the asserted symbols is mandatory (tests won't import
otherwise).

#### Carve-out — left for Milestone C (do **not** rename in A)

These are command handlers/command names that **C rewrites or deletes outright**.
Renaming them in A would be throwaway work (rename now, replace in C):

- cog command methods `add_to_meta` / `move_to_meta` / `remove_from_meta` → become
  `!feeds` / `!feeds_only` / `!remove_from` in C.
- `convert_to_feeder` → `convert_to_puzzle` (C); `convert_to_meta` re-specced (C).
- `sync_meta` → edge-aware `!sync` (C); `pool` → **dropped** (C); `nest` /
  `source_mode` → **folded away** (C).
- the `round` command (`!r`/`!mp`/`!rnm`) and its `command_map` → C grows it
  (`!capstone`/`!runaround`/`!group`).

A still **renames the data these handlers touch** (columns, dataclass fields, service
helpers they call) — it just doesn't rename the soon-to-be-replaced handler funcs and
`@command` names. The web action handlers behind those commands (`create_meta`,
`retag_meta`, `convert_to_meta`, `convert_to_feeder`) get C parity work too, so A
renames only their *data references*, leaving the route/handler renames to C.

DAL/SQL/code files swept in A (reference counts from the scan):
`bot/core/puzzle_service.py` (~327), `bot/cogs/puzzles.py` (~309),
`bot/web/actions.py` (~130), `bot/web/data.py` (~53), `bot/cogs/puzzles_gsheet.py` (~44),
`bot/store/mysqldb.py` (~43), `bot/core/notifier.py` (~18), `bot/store/round_data.py` (15),
`bot/web/api.py` (13), `bot/store/rollup_data.py` (10), `bot/core/locks.py` (9),
`bot/store/puzzle_data.py` (7), `bot/core/errors.py` (7), `frontend/src/{types,api}.ts`,
`frontend/src/components/*.tsx`, ~38 test files.

> **Execution.** This is a large mechanical diff — keep it pure-rename, no behaviour
> change, reviewable as a vocabulary sweep. Land it as its own commit(s) ahead of 012,
> exactly as the design's "rename-first" discipline requires. Recommend a scripted /
> LSP-assisted rename per symbol (not blind find-replace — `round`/`meta` are common
> substrings; `source_mode`, `metapuzzle`, `parent_puzzle_id`, the word "round" in
> source_mode values must survive).

### With 012 (edge model)

| change | locations |
|---|---|
| `'feeds-meta'` string literal → `'feeds'` | `bot/store/rollup_data.py:33`, `bot/store/mysqldb.py:378,438,441`, `bot/core/puzzle_service.py:438,441`, tests |
| `'aggregates-into'` → `'none'` | `bot/core/puzzle_service.py:434`, `bot/cogs/puzzles.py:1181,1195,1202`, tests |
| `RollupData.edge_type_filter` default `"feeds-meta"` → `"feeds"` | `bot/store/rollup_data.py` |
| remove `RoundData.kind` field; replace reads with derived helpers | `round_data.py` + every `kind` reader |
| derive has-target / fed-vs-informed / metameta | new helpers in `puzzle_service.py` |
| subtree harvest gains `feeds` filter (`edge_type_filter`) | `puzzle_service.py` (`collect_subtree_puzzles` / `select_rollup_members`) |

Recommend centralising the three edge values as constants (e.g. `EDGE_FEEDS`,
`EDGE_INFORMS`, `EDGE_NONE`) so literals don't re-scatter.

---

## Out of scope

Command vocabulary (Milestone C — the new `!feeds`/`!informs`/`!groups_with`/`!capstone`/
`!runaround`/`!group`/convert family and the command handlers/routes behind them, including
the carve-out handlers A deliberately skips), sheet/native-table generation (Milestone D),
the `default_edge` / `default_source_mode` derivation **map** (a code-side tuple, not a
column — no schema change), and any `evidence_items` change (none needed).

FK constraint-name renaming (`RoundID`/`RollupRoundID`) and test-*file* renames are
optional polish, not required for correctness. The `minimeta`/`metapuzzle`/`metameta`
puzzle-role vocabulary and `source_mode` values are intentionally **not** renamed (see
notes above) — "meta" remains a valid word for a fed target; only `round`→`tag_group`
and `meta_id`/`meta_code`→`target` move.
