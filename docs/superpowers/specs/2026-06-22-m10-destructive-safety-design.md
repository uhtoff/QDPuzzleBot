# M10 — Destructive-command safety + create-orphan cleanup

> Design spec. Produced 2026-06-22. Roadmap context: `docs/PLANS.md` PART 2, milestone **M10**
> (off critical path; deps: M3, which is merged in `main`). File:line refs verified against
> `main` @ `dd163bc`.

## Goal

Make the bot's irreversible operations safe to run: gate destructive deletes behind a
confirmation, stop half-completed deletes from crashing, and stop failed creates from leaving
orphaned DB rows + Google sheets. Stopping the roadmap after M5/M10 should leave "a strictly
better version of today's bot" — same mental model, fewer footguns.

## Decisions (locked with maintainer 2026-06-22)

- **Full bundle in one plan:** confirmation UI + delete hardening + create-orphan cleanup together.
- **Confirm-before only:** a confirmation gate before the irreversible action. No post-hoc undo —
  a deleted Google sheet + Discord channel cannot be truly restored, so "undo" is out of scope.
- **Discord + dashboard parity:** destructive deletes are bang-command-only today (no web delete
  endpoints exist), so parity here means the **create-orphan cleanup** covers both the bang create
  path and the web create endpoints.

## In scope

Three workstreams (A, B, C below).

## Out of scope

- True undo / soft-delete / grace-window restore of deleted sheets or channels.
- Persistent `discord.ui` views that survive a bot restart.
- A DB↔guild reconciler / sweeper (deferred at M3/M4; not revived here).
- Web *delete* endpoints (none exist; not adding them).

## Relevant files

- `bot/cogs/puzzles.py`
  - `delete_puzzle` (2214), `delete_puzzle_data` (2232)
  - `delete_round` (2241), `delete_round_data` (2271)
  - `delete_hunt` (2277)
  - bang `_puzzle` create path (`create_puzzle` 461 → `create_puzzle_channel` called at 454)
  - `web_create_puzzle` (721), `web_create_round` (787, rollback `except` 836-855)
- `bot/web/actions.py` — `create_puzzle` (456), `create_meta` (492) endpoints (call the `web_create_*` helpers)
- `bot/core/constants.py` — `ADMIN_ROLES` (confirmation gate reuses the existing role gate)
- New: `bot/cogs/ui.py` — reusable `ConfirmView`

## Required behaviour

### A. Confirmation gate (`discord.ui`)

- New reusable `ConfirmView(discord.ui.View)`:
  - **Confirm** + **Cancel** buttons.
  - ~30s `timeout`.
  - Author-only `interaction_check`: only the invoking user may click; anyone else gets an
    ephemeral "this confirmation isn't for you".
  - On **Cancel** or **timeout**: abort, edit the prompt message to indicate nothing was deleted,
    disable the buttons.
  - On **Confirm**: disable buttons, then run the existing delete logic.
- Applied to `delete_puzzle` (2214), `delete_round` (2241), `delete_hunt` (2277).
- The prompt states precisely what will be destroyed: target name, count of channels/puzzles
  affected, and an explicit "Google sheets will be permanently deleted (not recoverable)".
- **Minimal new-surface footprint:** no persistent views — the View timeout handles an abandoned
  prompt. We take on interaction-token + timeout handling only, not restart-persistence machinery.
- Location: `bot/cogs/ui.py` (keeps the 2,700-line cog from growing; reusable for future
  `discord.ui` work in M7/M9).

### B. Delete-path hardening

- **None-guards:** `delete_round:2255` and `delete_hunt:2287` call
  `discord.utils.get(self.get_guild(ctx)..., id=...).delete(...)`. `discord.utils.get` returns
  `None` when the channel is already gone → `AttributeError`. Guard both: skip a missing channel.
- **Don't abort halfway:** `delete_hunt`'s puzzle loop (2285-2288) is **not** wrapped in
  try/except (the round loop at 2290-2300 is). One missing channel aborts the whole hunt-delete.
  Wrap each iteration so the delete continues.
- **Bulk-delete pacing:** the loops at 2253-2255 (`delete_round`) and 2285-2288 (`delete_hunt`)
  fire `.delete()` back-to-back. A mid-loop `discord.RateLimited` (a hard error via
  `max_ratelimit_timeout=60`, see `memory/bot-channel-rate-limits`) leaves a half-deleted hunt.
  Catch `RateLimited`/`HTTPException` per item, continue, and **report what could not be deleted**
  so a re-run finishes the job. (Delete is not in the ~24h channel-*create* bucket, so the cheap
  fix is catch-and-continue + report, not pre-emptive sleeps that slow the common case.)
- **`delete_puzzle` error handling:** replace the bare `except:` (2228) with specific handling
  (`discord.HTTPException`/`RateLimited`) that reports which step failed (DB row already deleted in
  `delete_puzzle_data` vs the subsequent channel delete).

### C. Create-orphan cleanup (dashboard parity + bang)

Ordering today is sheet-create → DB-commit → channel-create, so a channel failure *after* the
first two strands a DB row + Google tab.

- **`web_create_puzzle` (721):** no rollback. If `get_or_create_channel` (764) fails after the
  sheet (757) + `PuzzleJsonDb.commit` (759), the row + Google tab orphan. Wrap the post-commit work;
  on failure delete the Google sheet + DB row, then re-raise.
- **`web_create_round` rollback (836-855):** already deletes the round row + category, but **strands
  the nested meta puzzle's DB row + Google sheet** when a step after `web_create_puzzle` returns
  (828-834) fails. Capture the returned puzzle handle and delete its row + sheet in the `except`.
  (With the `web_create_puzzle` fix above, a failure *inside* `web_create_puzzle` cleans itself; the
  remaining gap is failure *after* it succeeds.)
- **Bang `_puzzle` (452-454):** `create_puzzle` (461) commits sheet + row, then
  `create_puzzle_channel` runs at 454. A channel failure strands the row + sheet. Add equivalent
  cleanup at the bang call site (or in a shared helper used by both paths).

## Constraints

- Migration → deploy → code rule does not apply (no schema changes in M10).
- Do not touch unrelated code in the god-cog; keep the diff focused on the delete/create paths.
- Reuse existing seams: `ADMIN_ROLES` for the gate, `discord.RateLimited`/`HTTPException` handling
  patterns already present in `bot/web/actions.py` (`_discord_error_response`), and the sheets-cog
  delete (`delete_puzzle_spreadsheet`) for orphan-sheet cleanup.
- discord.py is pinned 2.7.1 — full `discord.ui` is available; zero dep bumps.

## Acceptance criteria

- A bang `!delete_puzzle` / `!delete_round` / `!delete_hunt` shows a confirmation with Confirm/Cancel;
  Cancel or timeout deletes nothing; only the invoker can act on it.
- `delete_round` / `delete_hunt` complete (and report any skips) when a puzzle channel is already
  missing, instead of crashing.
- A simulated mid-loop `RateLimited` during `delete_hunt` does not crash; the command reports which
  channels remain.
- A simulated channel-create failure on `web_create_puzzle`, `web_create_round`, and bang `!p`
  leaves **no** orphaned DB row and **no** orphaned Google sheet.
- Existing tests still pass; new unit tests cover the None-guard, the rollback paths, and the
  `ConfirmView` confirm/cancel/timeout/author-check behaviour where unit-testable.

## Open implementation choices (resolved as defaults; flag to change)

1. `ConfirmView` lives in new `bot/cogs/ui.py` (vs inline in `puzzles.py`). → **new module**.
2. Pacing = catch-and-continue + report remainder (vs pre-emptive sleeps). → **catch-and-continue**.
