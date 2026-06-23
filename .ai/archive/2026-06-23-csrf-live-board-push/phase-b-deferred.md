# Phase B (DEFERRED) — bang-command board-push coverage via the Notifier class

**Status: BLOCKED on M3.5 (working-1).** Do NOT start until M3.5 has merged to `main` and its
`Notifier` class exists in `bot/core/`.

## Trigger condition

- M3.5 (Discord channel-adapter extraction) merged to `main`, AND
- the `Notifier` class M3.5 introduces is present in `bot/core/` (the central seam for emitting
  out-of-band events from the service layer).

## Why deferred

Phase A (this branch, `feature/dashboard-live-csrf`) emits board-invalidation events from the **web**
mutation path only (`bot/web/actions.py`), so a dashboard action by admin A reflects on admin B's
SPA. It does NOT cover **bang-command** mutations (`!s`, `!move`, `!p`, …) done purely in Discord —
those still wait for the 30s poll. Covering them needs an emit in the shared `PuzzleService`
mutation methods, which lives in `bot/core/` and would otherwise collide with M3.5's in-flight work.

## Design (route through Notifier, not a web import)

- **Do NOT** import `bot/web/events` into `bot/core/` (layering inversion). Instead, emit board
  events through M3.5's **`Notifier`** abstraction so the service stays web-agnostic; the web layer
  registers `events.publish` as a Notifier sink at app startup.
- Emit one board event per successful service mutation (solve, move/add/remove-to-meta, sheet
  add/remove, create). Reuse the **same event shape** as Phase A: `{"type":"board","uid":<hunt
  uid>,"guild_id":<int discord snowflake>}` — `guild_id` MUST be the int snowflake or the
  `ws_activity` filter (`bot/web/api.py:171`) drops it.
- The service knows `hunt`/`guild` on most mutation methods; where it only has `puzzle`/`round`,
  resolve the hunt uid + guild snowflake before notifying (or have the Notifier resolve from ids).

## Open questions (resolve at implementation)

1. **Does Phase A's web-path emit get removed once Phase B lands?** If the service emit fires for
   BOTH web and bang paths (web actions call the service), the Phase-A `_notify_board` calls in
   `actions.py` become redundant and should be deleted to avoid double events. Confirm the web
   endpoints all go through the service for their mutation before removing.
2. Notifier registration point + lifecycle (who registers `events.publish` as a sink, and when).
3. Whether Notifier also subsumes the existing activity events (out of scope — leave activity alone).

## Acceptance (when implemented)

- A bang-command solve/move/create in Discord refreshes an open dashboard within the socket window
  (no 30s wait), with no duplicate board events.
- `timeout 180 pipenv run python -m pytest tests/ -q` green; `cd frontend && npm run build` clean.
- No `bot/web/*` import inside `bot/core/*`.
