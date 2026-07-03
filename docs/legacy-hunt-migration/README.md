# Legacy hunt migration runbook

How the pre-migration (baseline-schema) production database was brought forward onto
the current TagGroup / edge model. Executed **in place** against the `puzzlebot`
database on 2026-07-03.

## Situation

The legacy DB was a clean `000_base_schema` baseline: `rounds` with `meta_id` / `meta_code`,
a `tags` table of just `(round_id, puzzle_id)` with **no `edge_type`**, and no
`schema_migrations` table. It held the full old production history — 125 hunts, 479 rounds,
3840 puzzles, 2732 tags across 10 guilds.

The two data concerns going in were: (a) make sure target tags are correct, and (b) make
every membership a `feeds` edge (the legacy system had no `informs`). **Both are done
automatically by the numbered migrations** — see below — so the bespoke work was only guild
pruning + a small cosmetic cleanup.

## Key facts about the legacy shape (verified before running)

- Schema matched the baseline column-for-column, except `puzzles` lacked the `locked`
  column (a dead column no current code reads/writes).
- `puzzles.hunt_id` is the authoritative hunt link. 1368 puzzles had **no `hunt_id` and no
  tag** — pure orphan rows left behind by previously-deleted hunts (puzzles never
  cascade-delete in the baseline). They display under nothing.
- Referential integrity was clean: no orphan tags, no puzzle spanned two hunts,
  `rounds.hunt_id` never null, only 2 trivial `puzzle.hunt_id` / `round.hunt_id` mismatches.
- 352 / 362 metas were already self-tagged into their own round.

## What the migration chain does for free

- `003` adds `tags.edge_type` defaulting to `feeds-meta` for every existing row;
  `012` rewrites `feeds-meta → feeds`. → **every legacy membership becomes a `feeds` edge.**
- `011` renames `rounds → tag_group`, `meta_id → target_puzzle_id`, `tags.round_id →
  tag_group_id`. → **metas are preserved as targets.**
- `018` / `019` normalise a meta's self-edge to `target` wherever a self-membership row
  exists.
- `016` maps `type` `'Round' → Metapuzzle`, `'Metaless Round' → Group`.

What it does **not** cover (hence the cleanup script): `type = NULL` groups, metas with no
self-membership row at all, the missing `locked` column, and the orphan-puzzle junk.

## Procedure

Run from the repo root, with `config.json` present (MySQL creds).

0. **Back up first** (reversible even though the DB is a copy):
   ```bash
   mysqldump --single-transaction --routines --triggers puzzlebot > puzzlebot_backup_pre_migrate.sql
   ```

1. **Prune unwanted guilds** — `01_drop_guilds.sql`.
   Drops guilds `1` (NULL), `3` (QD Test), `7` (team meeting hunt server), `8` (QD testing
   new bot) and everything under them. Puzzles are deleted explicitly first because they have
   **no FK cascade** to hunts; the guild delete then cascades hunts → rounds → tags.
   ```bash
   mysql puzzlebot < docs/legacy-hunt-migration/01_drop_guilds.sql
   ```
   Removed 10 hunts / 37 rounds / 70 puzzles / 71 tags → 6 guilds, 115 hunts remain.

2. **Apply schema migrations** — `000 → 024` via the repo runner. `migrate.py` reads
   `config.database`, so either set that to `puzzlebot` or use a small wrapper that points
   `migrate.connect()` at it (see `run_migrate.py` in the session scratchpad). The runner is
   idempotent (`schema_migrations` bookkeeping); `000` is all `CREATE TABLE IF NOT EXISTS`,
   so it no-ops on the existing tables.
   ```bash
   python migrate.py migrate      # against the puzzlebot DB
   ```
   Pre-flight: `05` adds a UNIQUE key on `tags(round_id, puzzle_id)` — confirm there are no
   duplicate pairs first (there were none).

3. **Post-migrate cleanup** — `02_post_migrate_cleanup.sql` (runs against the *new* schema:
   `tag_group`, `target_puzzle_id`, `tags.edge_type`).
   ```bash
   mysql puzzlebot < docs/legacy-hunt-migration/02_post_migrate_cleanup.sql
   ```
   - backfills 92 NULL group `type`s (all targetless → `Group`)
   - inserts a `target` self-edge for the 9 targets that had no self-membership row
   - adds the baseline `puzzles.locked` column
   - sweeps 1368 orphan puzzles (no `hunt_id`, no tag)

## Final verified state

| | |
|---|---|
| Migrations recorded | 25 (`000`–`024`) |
| Guilds / hunts | 6 / 115 |
| TagGroups / puzzles / tags | 442 / 2402 / 2670 |
| Edge types | `feeds` 2329, `target` 341, **`informs` 0** |
| Group types | `Metapuzzle` 345, `Group` 97, NULL 0 |
| Referential integrity | zero orphan tags / hunts / dangling targets |

Per-guild hunts: QD Main 82, Uhtoff 21, QD MIT 2026 6, QD MIT 2025 3, solo hunts 2,
The Tulgey Wood 1.

## Not handled by this migration (operational)

The display-only hunts keep their old `channel_id` / `google_sheet_id` / `role_id` values —
dead links, harmless on the dashboard. The one running hunt needs its live Discord channels,
sheet, and the **manually-set hunt-category role overwrite** to be present for the dashboard's
role-gated visibility (that overwrite is set by hand by an admin, never by code).
