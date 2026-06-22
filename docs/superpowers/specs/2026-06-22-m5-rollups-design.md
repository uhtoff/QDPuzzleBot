# M5 — `rollups` + on-solve propagation + surfaced failures

Date: 2026-06-22
Milestone: **M5** (deps: M3, M4) — from `docs/PLANS.md`.

## Goal

Make meta-sheet propagation **reliable and observable**. Today the dashboard reports
success while a Google Sheet is silently stale: the web edges swallow Sheets errors
(`web_refresh_meta` at `puzzles.py:597-605`, plus `solve`/`retag` actions in
`bot/web/actions.py`). M5 turns each aggregation target into a first-class, tracked
`rollups` record, persists the success/failure of every refresh, surfaces staleness to
the user (toast + durable badge), serializes concurrent writes to a given meta sheet,
and adds a manual `!rebuild` / dashboard rebuild affordance to recover from a failed
refresh.

"Full M5 as written": the `rollups` record carries the forward-looking `source_mode` /
`edge_type_filter` columns now (defaulting to current meta behaviour), even though they
stay inert until M6/M7 wire them up.

## Where this plugs in (current state, verified)

- Solve and retag already route through `PuzzleService` (M3): `service.solve` /
  `move_to_meta` / `add_to_meta` / `remove_from_meta` call
  `refresh_meta(hunt, meta_round)` per affected tag.
- `refresh_meta` (`bot/core/puzzle_service.py:35-51`) calls `add_metapuzzle_data` /
  `add_metametapuzzle_data`, which call the M2 `aggregate_to_tab` writer
  (`bot/cogs/puzzles_gsheet.py:599`) — clear-then-update (two API calls) on one tab.
- The aggregation target today is always a **meta round's sheet** — the meta puzzle's
  own tab, located by `meta_puzzle.google_page_id` (re-pointed on archive,
  `puzzles_gsheet.py:414`).
- The web edges deliberately swallow the Sheets `HttpError` (`web_refresh_meta`;
  `solve_puzzle` at `actions.py:277-282`; `retag` at `actions.py:357-362`).
- M4 added a per-puzzle async mutex (`bot/core/locks.py`, `_PUZZLE_LOCKS`, keyed by
  `puzzle_id`) used by the `with_puzzle_mutex` bang decorator and the web edge. It does
  **not** cover the meta sheet — a shared resource refreshed by solving *any* of its
  feeders.

## Design decisions (locked during brainstorming)

1. **Scope:** Full M5 as written — build the complete `rollups` record including inert
   `source_mode` / `edge_type_filter`.
2. **Surface failures:** persist `last_ok` / `last_error` on the rollup row (durable
   badge, visible to every viewer) **and** return a non-fatal `sheet_warning` in the
   acting request for an immediate toast. The DB mutation always commits regardless
   (consistent with the codebase's best-effort policy); "fail loudly" makes staleness
   visible, it does not roll back.
3. **Propagation timing:** inline (refresh synchronously within the action). No debounce
   coalescing — solves are human-paced and each rebuild is now 1–2 API calls (post-M2),
   and inline keeps the failure toast working. The single `refresh_meta` entry point is
   a clean seam if debounce is ever justified later.
4. **Rebuild:** `!rebuild` (hunt-wide by default; targeted in a meta channel) **plus** a
   dashboard "Rebuild" button on the stale badge.
5. **Tab pointer:** key on a `createDeveloperMetadata` id for stability across the
   archive sheet-ID reassignment — but **always** keep the meta puzzle's
   `google_page_id` as the trusted fallback. Never trust dev-metadata alone (its
   `copyTo`/`duplicateSheet` survival is documented-unknown).
6. **Rollup identity:** keyed per `round_id` (one rollup per meta round), unique
   constraint. `refresh_meta(hunt, meta_round)` does find-or-create by
   `round_id = meta_round.id`. `round_id` is the stable, load-bearing membership key;
   `meta_id` can be null/reassigned, and `tab_page_id` is the thing that changes on
   archive (so a bad lookup key).
7. **Concurrency:** add a **per-`round_id` rollup mutex** so only one writer touches a
   given TagGroup sheet at a time. Different metas (different `round_id`) refresh in
   parallel; concurrent refreshes of the *same* meta serialize. Separate lock registry
   from M4's per-puzzle locks (a `puzzle_id` and a `round_id` are ints from different
   tables and would collide in one registry). Acquire/release one meta at a time inside
   the solve/retag loop — never hold two rollup locks at once → no deadlock cycle with
   the outer per-puzzle lock.

## Components

### 1. `rollups` table + store

New migration `migrations/002_rollups.sql` (additive; ships **before** code — the
generic `commit()` writes every dataclass field by name and crashes on a missing
column). Mirrors the existing `RoundData` / `MySQLRoundJsonDb` pattern.

```sql
CREATE TABLE IF NOT EXISTS `rollups` (
  `id`               int(11)         NOT NULL AUTO_INCREMENT,
  `hunt_id`          int(11)         NOT NULL,
  `round_id`         int(11)         NOT NULL,
  `tab_page_id`      bigint          DEFAULT NULL,   -- cached destination tab page id
  `metadata_id`      bigint          DEFAULT NULL,   -- createDeveloperMetadata id (stable across archive)
  `source_mode`      varchar(32)     NOT NULL DEFAULT 'round',        -- inert until M7
  `edge_type_filter` varchar(32)     DEFAULT 'feeds-meta',           -- inert until M7
  `last_ok`          tinyint(1)      NOT NULL DEFAULT 1,
  `last_error`       text            DEFAULT NULL,
  `updated_at`       datetime        DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_rollups_round` (`round_id`),
  KEY `idx_rollups_hunt` (`hunt_id`),
  CONSTRAINT `RollupHuntID`  FOREIGN KEY (`hunt_id`)  REFERENCES `hunts`  (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `RollupRoundID` FOREIGN KEY (`round_id`) REFERENCES `rounds` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Store layer:
- `bot/store/rollup_data.py` — `RollupData` dataclass (`@dataclass_json`, `import_dict`
  with the `_time`/`updated_at` tz handling) + an interface stub `_RollupJsonDb`,
  mirroring `round_data.py`.
- `bot/store/mysqldb.py` — `MySQLRollupJsonDb(_MySQLBaseDb)` with `TABLE_NAME='rollups'`,
  `get_by_attr(round_id=…)`, `get_all_from_hunt(hunt_id)`.
- `bot/store/__init__.py` — export `RollupsJsonDb`.

### 2. Rollup-aware `refresh_meta` (persistence in the service)

`PuzzleService.refresh_meta(hunt, meta_round)` becomes:

1. Guard as today (None / no `meta_id` → return).
2. **Acquire the per-`round_id` rollup lock** for `meta_round.id`, *waiting* if held
   (see §6). The lock wraps everything below — crucially the **DB read** — so each
   holder renders post-commit state (see §6 for why this is load-bearing).
3. Find-or-create the `rollups` row for `round_id = meta_round.id`
   (`source_mode='round'`, `edge_type_filter='feeds-meta'` on create).
4. Read the round/meta puzzles (`get_all_from_round` / `get_all_from_hunt`) **inside the
   lock**, resolve the target tab page id (see §3), and render via the existing
   `add_metapuzzle_data` / `add_metametapuzzle_data` path.
5. **On success:** `last_ok=1`, `last_error=NULL`, `updated_at=now`, persist the
   `tab_page_id` / `metadata_id`, commit the rollup row.
6. **On failure** (`HttpError` / `RuntimeError` from the Sheets path): `last_ok=0`,
   `last_error=str(exc)[:1000]`, `updated_at=now`, commit the rollup row, then
   **re-raise** so the edge can toast.

Persistence happens before the re-raise, so every caller gets the durable badge for
free; callers that want a toast (solve/retag) catch the raise, and
`web_create_puzzle`'s existing swallowing wrapper keeps swallowing — the badge still
lands.

#### 2a. Failure isolation in the fan-out callers

A single mutation often refreshes **several** metas: `solve` loops over `puzzle.tags`;
`move_to_meta` / `remove_from_meta` refresh the new meta plus every old tag. With
`refresh_meta` re-raising, a naive loop would abort on the first failing meta — so a
feeder of TagGroups 1 **and** 5 that fails on 1 would skip 5 *and* skip the archive
step. That is wrong: one stale sheet must not poison the others.

So the fan-out service methods (`solve`, `move_to_meta`, `add_to_meta`,
`remove_from_meta`) **catch per-meta, accumulate the failed `round_id`s, and continue** —
each failure is already persisted (badge) by `refresh_meta` before it raised. Side
effects that must happen regardless (archive + final `commit` in `solve`) run after the
loop. If any meta failed, the method raises a single
`SheetRefreshError(failed_round_ids)` **at the very end** (after the DB/archive work is
done) so the edge can toast "N sheet(s) stale — rebuild to retry" without losing the
archive or the other refreshes. The empty-solution guard still returns `False` *before*
any of this.

`SheetRefreshError` is a new lightweight exception in `bot/core`. Edges catch it
(alongside `HttpError`) and map it to the `sheet_warning` toast; the DB mutation is
already fully committed. Bang-command edges log it (as today) — the Discord user already
sees the SOLVED embed; the durable badge carries the staleness.

**Auto-heal:** because every refresh is a full DB snapshot (not a delta), the *next*
solve/retag that touches a stale group regenerates its whole sheet from current DB state
— picking up every previously-committed-but-unwritten answer — so the badge clears with
no special handling. `!rebuild` / the web button exist only for groups that receive no
further mutation after a failure.

### 3. `createDeveloperMetadata` helpers (gsheet cog)

Add to `bot/cogs/puzzles_gsheet.py`:
- `ensure_tab_metadata(spreadsheet_id, page_id, key) -> metadata_id` — idempotently
  attach searchable developer metadata to a tab (returns existing id if present). M2
  confirmed `createDeveloperMetadata` + `searchDeveloperMetadata` are in the bundled
  static discovery doc (offline-safe).
- `find_tab_by_metadata(spreadsheet_id, metadata_id) -> page_id | None`.

`refresh_meta` resolves the target tab as: **try `metadata_id` →** on None/miss **fall
back to the meta puzzle's `google_page_id`** (already re-pointed on archive). On a
successful render via the fallback, (re-)stamp the metadata id so the rollup self-heals.
This is the substrate M7/M8 need for standalone aggregate tabs; for M5 the fallback is
the reliable path and dev-metadata is belt-and-suspenders.

### 4. Surfaced failures at the edges

- `bot/web/actions.py` `solve_puzzle` / `retag`: keep committing the DB mutation, but on
  a caught Sheets failure return `{ok: true, sheet_warning: "<short message>"}` instead
  of a silent `logger.exception`. (Still log server-side.)
- `bot/web/data.py`: include per-meta `rollup_stale` (`last_ok == 0`) + `last_error` in
  the board payload so the SPA can render a "sheet stale" badge on the meta.
- Frontend: a stale badge on the meta (mirrors the existing notes `Indicator` pattern),
  a toast from `sheet_warning`, and a **Rebuild** button on the badge wired to §5.

### 5. `!rebuild` command + web rebuild endpoint

- `!rebuild` (`bot/cogs/puzzles.py`): no-args → rebuild every rollup in the current hunt
  (iterate `RollupsJsonDb.get_all_from_hunt`, call the service refresh per round,
  aggregate per-target ok/fail into one summary message). In a meta channel → rebuild
  just that meta. Routes through the same `refresh_meta`, so `last_ok`/`last_error` get
  updated and the lock is honoured.
- `POST /api/rollups/<round_id>/rebuild` (organiser-gated): keyed on `round_id` (a
  rollup is per round). Resolve the round → its hunt → apply the same visibility +
  organiser auth as the retag endpoint; retry that one rollup; return `ok` or the error
  string for a toast; the SPA invalidates the board (`["hunt", uid]`).

### 6. Per-`round_id` rollup mutex

Extend `bot/core/locks.py` with a **separate** registry (`_ROLLUP_LOCKS`) and a
`rollup_lock(round_id)` helper, alongside the existing `_PUZZLE_LOCKS` /
`puzzle_lock(puzzle_id)`. `refresh_meta` wraps its **read + render + persist** in
`async with rollup_lock(meta_round.id)`. Because solve/retag refresh metas one at a time
in a loop, at most one rollup lock is held at any instant (plus the outer per-puzzle
lock on a *different* key space) → no deadlock.

**Pending rebuild vs concurrent action** — both a rebuild (`!rebuild` / web button) and
an on-solve/retag refresh go through `refresh_meta`, so they contend on the *same*
`rollup_lock(round_id)`. Three properties make this safe:

1. **Wait, don't drop.** The rollup lock is acquired *waiting* (unlike M4's fail-fast
   `with_puzzle_mutex(wait=False)`): a refresh is an internal, idempotent full-snapshot
   write, so a contended action should queue (~1–2s for one round) and then run, not be
   rejected. (Rebuild itself is not user-command-rejecting either.)
2. **Read inside the lock ⇒ no lost update.** The action commits its puzzle mutation to
   the DB *before* calling `refresh_meta`; because the lock wraps the DB read, when the
   action acquires the lock it re-reads current state and renders post-commit truth. If
   the in-flight rebuild had written a momentarily-stale snapshot (read before the
   action's commit), the action overwrites it with the correct one. Final state is always
   correct; at worst the sheet is transiently stale for the lock-hold window.
3. **Hunt-wide `!rebuild` holds one round's lock at a time.** It loops round-by-round, so
   an action on round R waits only while R itself is being rebuilt, not for the whole
   sweep.

## Testing

Pure/unit tests (mock the GoogleSheets cog), following the M1 seam:
- `refresh_meta` persists `last_ok=1`, clears `last_error`, sets `updated_at` on success.
- `refresh_meta` persists `last_ok=0` + `last_error` **and re-raises** on a Sheets
  failure.
- find-or-create is idempotent: two refreshes of the same round → one rollup row.
- Tab resolution falls back to `google_page_id` when `metadata_id` is absent / missing.
- `!rebuild` hunt-wide iterates the hunt's rollups and aggregates per-target results.
- `bot/web/data.py` board payload exposes `rollup_stale` / `last_error` from the rollup.
- **Failure isolation:** a `solve` whose feeder touches two metas, one of which fails to
  refresh, still refreshes the other and still archives, badges only the failed one, and
  raises `SheetRefreshError([failed_round_id])` at the end.
- **Auto-heal:** after a refresh of round R fails (badge set), a later `refresh_meta(R)`
  that succeeds clears `last_ok`/`last_error` and the rendered snapshot includes the
  earlier already-committed answer.
- (If practical) concurrent `refresh_meta` on the same `round_id` serialize on the lock.

## Constraints / non-goals

- **Migration → deploy → code.** `002_rollups.sql` runs before the new code.
- Additive and backward-compatible: inert `source_mode`/`edge_type_filter` defaults;
  old code paths untouched.
- No debounce/background propagation this milestone (seam left for later).
- `source_mode` / `edge_type_filter` are **not** exercised — they sit at their defaults
  until M6/M7.
- Do not refactor the M2 `aggregate_to_tab` renderers beyond what tab-resolution needs.
- Leave the unrelated dirty `Pipfile.lock` out of any M5 commit.

## Acceptance criteria

- A meta-sheet refresh failure (solve or retag, Discord or dashboard) leaves a durable
  `last_ok=0` + `last_error` on the rollup, shows a stale badge on the meta in the SPA,
  and (for the acting web request) returns a `sheet_warning` toast — while the DB
  mutation is still committed.
- A subsequent successful refresh (`!rebuild`, the web Rebuild button, or the next solve
  that touches the group) clears the badge and writes a full snapshot including the
  earlier already-committed-but-unwritten answers — no manual rebuild required when the
  group keeps getting solves.
- A multi-meta solve isolates failures: a failure on one meta neither aborts the other
  metas nor skips the archive.
- Two concurrent refreshes of the *same* meta serialize; refreshes of different metas do
  not block each other.
- Migration `002_rollups.sql` stands the table up; the bot reads/writes it via
  `RollupsJsonDb`.
- All new tests pass under the existing pytest seam.
