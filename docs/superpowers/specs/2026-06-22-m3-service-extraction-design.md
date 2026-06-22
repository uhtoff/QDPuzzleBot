# M3 — `bot/core/` service extraction (design)

> Date: 2026-06-22. Milestone **M3** from `docs/PLANS.md` (deps: M1, M2 — both delivered).
> Goal: kill the `web_*` duplication by promoting the ctx-free twins into a single canonical
> service layer that both the bang-commands and the web actions call. **No behaviour change.**

## Goal

Today every dashboard write-action has a hand-synced ctx-free `web_*` twin sitting beside its
ctx-bound bang-command `_impl` in `bot/cogs/puzzles.py`. The twins have already diverged
(`web_solve_puzzle` posts a different embed and *swallows* sheet errors where `_solve_impl`
raises). M3 collapses each pair into **one core method + two thin edges**, deletes the twins, and
centralises the duplicated role/status constants. This is "the seam everything hangs off" — once
logic lives in `bot/core/`, the later fan-out (M7/M8/M9/M11) adds there instead of in the 2,700-line
god-cog.

## In scope

- `bot/core/` package: `constants.py` + `puzzle_service.py` (`PuzzleService`).
- Centralise `ADMIN_ROLES`, `ORGANISER_ROLES`, `STATUSES`, `PRIORITIES` into `bot/core/constants.py`;
  rewire `bot/cogs/puzzles.py`, `bot/web/auth.py`, `bot/web/actions.py` to import from there.
- Migrate operations into `PuzzleService` one path at a time, deleting each `web_*` twin and folding
  the bang `_impl` onto the service. Order: scaffold → retag → solve → (create_puzzle, create_round,
  add_sheet/remove_sheet, refresh_meta/refresh_info).
- Service owns `spreadsheet_id` selection (sets it from `hunt.google_sheet_id` like the twins do).

## Out of scope (deferred, per PLANS.md)

- Per-puzzle mutex on the web path → **M4**. Core does not lock; bang keeps `@with_puzzle_mutex` at
  the edge, web stays unlocked until M4.
- Surfacing web sheet-refresh failures to the dashboard → **M5**. M3 only *preserves* today's
  behaviour (web swallows, bang raises).
- De-ctx-ing the `environment[ctx.message.id]` spine / fake-ctx hack / dashboard-as-primary →
  deferred per the architecture verdict.

## Relevant files

- `bot/cogs/puzzles.py` — `web_*` twins (606–985), the `_impl` methods, constants (26–27, 80–81),
  `cog_before_invoke` (159–210), `_PUZZLE_LOCKS` + `@with_puzzle_mutex` (23, 29–64).
- `bot/web/actions.py` — HTTP endpoints calling the twins; `PUZZLE_STATUSES`/`PUZZLE_PRIORITIES`
  (29–31, as **sets**).
- `bot/web/auth.py` — `ADMIN_ROLES`/`ORGANISER_ROLES` (22–24).
- `tests/` — M1 bang-vs-web equivalence suite (the guard rail).

## Design decisions (locked with maintainer)

1. **Incremental strangler.** Scaffold + one path per commit; equivalence tests green between each;
   each commit independently revertable; stopping after any path leaves a working bot.
2. **Core raises; edges decide.** `PuzzleService` methods do data + Google Sheets + *intrinsic*
   Discord mutations (channel move-to-solved, channel/category creation, `safe_rename`) and **raise**
   on failure. Each edge keeps its current policy:
   - bang `_impl`: let errors propagate, post its `ctx` embed/confirmation.
   - web action: keep its best-effort `try/except` (moved *out* of the twin to the web edge), post to
     the puzzle channel.
   Pure **presentation** (embed text, post location, "tidied away" confirmation) stays at the edges;
   core is the common subset. This preserves both surfaces' behaviour exactly and sets up M5 cleanly.

## Architecture

### Package layout

```
bot/core/
  __init__.py
  constants.py        # ADMIN_ROLES, ORGANISER_ROLES, STATUSES, PRIORITIES (ordered lists)
  puzzle_service.py   # class PuzzleService(bot) — canonical ctx-free operations
```

- `constants.py` is the single source. `STATUSES`/`PRIORITIES` stay **ordered lists** (UI enumeration
  needs order); the web side does `set(STATUSES)` locally for membership checks. Kills the
  list-vs-set drift.
- `PuzzleService` is constructed with the `bot` reference (it needs `bot.get_cog("GoogleSheets")` and
  guild lookups — what the twins already use). The `Puzzles` cog sets `self.service =
  PuzzleService(self.bot)` in `__init__`; web actions reach it via `bot.get_cog("Puzzles").service`.

### The seam (per operation)

```
PuzzleService.<op>(guild, hunt, puzzle, ...)   # data + sheets + intrinsic Discord mutations; RAISES
   ├── _<op>_impl(ctx, ...)   → parse/validate from ctx, call service, post ctx embed, errors propagate
   └── web action /<op>       → call service, keep best-effort try/except, post to puzzle channel
```

The bang `_impl` extracts `guild`/`hunt`/`puzzle` from its ctx/environment and calls the service —
the service resolves the gsheet cog itself (like the twins do today) rather than via
`get_gsheet_cog(ctx)`, so the gsheet-cog `spreadsheet_id` setup that `cog_before_invoke` does is
preserved inside the service (set from `hunt.google_sheet_id`).

## Migration order (each = its own commit)

1. **Scaffold** — create `bot/core/` package + `constants.py`; rewire all three files to import the
   constants; add an (initially empty) `PuzzleService(bot)` on the cog. **Zero behaviour change.**
2. **Retag** — `move_to_meta` / `add_to_meta` / `remove_from_meta` → `PuzzleService`; both edges call
   it; delete the 3 `web_*` retag twins. (Lowest risk first.)
3. **Solve** — the high-divergence path; core raises, edges keep their policy; delete
   `web_solve_puzzle`, fold `_solve_impl`.
4. **Remaining paths** (subsequent commits): `create_puzzle`, `create_round`,
   `add_sheet`/`remove_sheet`, `refresh_meta`/`refresh_info`.

## Behaviour preservation & testing

- M1's bang-vs-web equivalence tests are the guard rail; run the focused suite after each path under
  `timeout`, output to a file.
- Intentional divergences (embed text, error tolerance) are preserved by living at the edge — core is
  the common subset, so the tests for both surfaces stay green.
- Work in a dedicated git worktree (large change; isolation per git discipline).

## Acceptance criteria

- `bot/core/` exists with `constants.py` + `PuzzleService`; the four constants are defined once and
  imported by `puzzles.py`, `web/auth.py`, `web/actions.py` (no duplicate literals remain).
- For each migrated path: exactly one core method; the `web_*` twin is deleted; the bang `_impl` is a
  thin caller; both edges' observable behaviour is unchanged.
- M1 equivalence + existing pure tests pass after every commit.
- `py_compile` clean; `tsc --noEmit` unaffected (no frontend change in M3).

## Risks / notes

- `bot/cogs/puzzles.py` is a contention magnet — land M3 before the M7/M8/M9/M11 fan-out (this is
  the point of doing it now).
- Solve path is where the real divergence lives; reconcile by keeping presentation/tolerance at the
  edges, not by changing either surface.
- Deploy order does not matter for M3 (no schema change), unlike the column-adding milestones.
