# Changed Files

Written 2026-08-31 at the end of the review-remediation session. Everything below is
committed on `staging` (fast-forwarded from `working-3`) and green. Nothing is in flight.

## Git status

- `staging` @ `db1ac08`, **38 commits ahead of `origin/staging`** — unpushed.
- `main` @ `dc957bd`, 71 behind `staging`. Live is deployed from `main`.
- Working tree clean in the main checkout and in `working-3` (they are level).
- `matrix-spike` parked 3 ahead of `main` since 2026-07-31; untouched.
- **No migration-number collision**: `meta-matching` owns 043/044 (the reserved pair),
  `matrix-spike` stops at 035, so 045/046 are unambiguous. Checked, not assumed.

## Gates at `db1ac08`

| Check | Command | Result |
|---|---|---|
| Backend | `timeout 500 pipenv run python -m pytest tests/ -q` | **2171 passed** (2062 at session start) |
| Lint | `pipenv run ruff check` | clean |
| Types | `pipenv run basedpyright bot/` | 0 errors |
| Frontend build | `npm run build` (tsc + vite) | clean |
| Frontend tests | `npm test` (vitest) | **52 passed** |

The 31 pytest warnings are the pre-existing naive `datetime.utcnow()` class in
`mysqldb.py`. Not addressed; unchanged in kind, only in count as tests were added.

## Migrations

- `045_additional_sheets_location.sql` — adds `additional_sheets.on_solved_sheet`.
- `046_backfill_archived_additional_sheets.sql` — corrects 045's backfill for the six
  production rows genuinely on an archive spreadsheet.
- **Both applied to `puzzlebot_test` only. Production has NEITHER.** They apply together
  on the next deploy, in filename order, before the bot restarts.

## Verified against production (read-only)

- `migrate.py status` on the prod host: 000–042 applied; 043/044 absent entirely; no
  pending.
- 045's pre-flight query: **7 rows**, not the expected 0. Each page id checked against the
  Sheets API (metadata only, no writes): **6 on ARCHIVE, 1 on MAIN** (id 6, "Name Badges").
- No production data was modified at any point this session.

## Tests not run

- No live Google Sheets **write** has been made. The D7 scratch-spreadsheet probe created
  and then deleted one throwaway file owned by the service account; the production checks
  were metadata reads only.
- No browser verification of the frontend. M24/M25/C6 remain inspection-only — the vitest
  setup is node-only, with no jsdom/happy-dom.
- Production database never written to; `puzzlebot_test` only.

## Risk audit, 2026-08-31

Run before pushing. One regression found and fixed (`db1ac08`): `!solve_mini` and
`!unsolve_mini` caught only `ValueError`, so a `SheetRefreshError` escaped as a generic
command failure for an operation already committed. `!unsolve_mini` was introduced by M27;
`!solve_mini` was the identical pre-existing gap.

Checked and clear: C3's new DELETE is strictly narrower than the unguarded one it replaced;
C4's tile reset is gated on `tile_status == "ready"`; M6's namespace change is strictly more
permissive; the archived guards and M27 refuse more rather than mutating more; media GC is
manual-only and no scheduled task mutates data.

**M21a's `fields=` mask was the one change tests could not cover** — every test mocks
`sheet_list`. Verified directly against a real 56-tab production spreadsheet (metadata
only): the `"sheets"` guard survives and `sheetId` / `title` / `index` / `hidden` / `tables`
all arrive.

## Current risks

- **045 + 046 have not touched production.** The deploy applies them. Confirm which branch
  Plesk tracks before pushing, and run the post-apply query in `session-summary.md`.
- **`_ROLLUP_LOCKS` remains a latent cross-test hazard** — a module-global
  `defaultdict(asyncio.Lock)` outliving each test's event loop. New tests use per-test hunt
  ids; `tests/test_rollup_lock.py` contends key `5` and is safe only because nothing else
  does.
- **M24/M25/C6 still have no regression protection.**
- Two archived-guard gaps were found by mutation (`delete_puzzle`, `update_puzzle`) and
  both are now pinned. Other member-tier routes may have the same gap; the technique that
  found them is flipping one `allow_archived` keyword and seeing whether anything reds.

## Commands used against production (all read-only)

    python migrate.py status                     # on the prod host, by the user
    <045's pre-flight SELECT>                    # via phpMyAdmin, by the user
    get_sheet().get(spreadsheetId=..., fields="sheets.properties.sheetId")   # metadata only
