# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2
Branch: `feature/dashboard-live-csrf` (off `main` @ `5cc71b3`; only commit is `docs(PLANS)` `5856861`)
Started: 2026-06-23
Status: in progress — docs(PLANS): mark CSRF + live board push Phase A as done @ 6c6375e

## Goal (one sentence)

Add a CSRF token to dashboard mutating requests and a live board-invalidation push (web path) so
other admins' SPAs refetch immediately instead of waiting for the 30s poll — both entirely in
`bot/web/` + `frontend/`, touching neither the cog nor the service layer.

## Context the executing agent needs (not obvious from files)

- **Authoritative plan: `.ai/active/current-spec.md`** — phased, with near-verbatim test skeletons.
  Execute Phase 1 (tests) first, confirm they fail for the right reason, commit; then Phases 2–3.
- **Model split:** Phase 1 (test design) = **Opus**; Phases 2–3 (implementation) = **Sonnet**.
- **The board event MUST carry `guild_id` as an int Discord snowflake**, or the `ws_activity`
  filter (`bot/web/api.py:171`, `event.get("guild_id") in membership`) silently drops it.
- **`events.publish` is synchronous** (`bot/web/events.py:24`). `_notify_board` is a plain sync
  function; tests patch `bot.web.events.publish` with a plain `Mock` (NOT `AsyncMock`).
- There is **no Quart/web test-client harness and no frontend test framework** — backend tests are
  pure helpers only (mirror `tests/test_session_secret.py` / `tests/test_web_audit.py`); frontend is
  verified by `cd frontend && npm run build`.
- Env: `pipenv run python -m pytest` (bare `pytest` can't `import bot`). `bot/store` opens MySQL at
  import, so the gitignored `config.json` (test DB) must be present — it already is in this worktree.

## What NOT to touch

- `bot/cogs/puzzles.py`, `bot/cogs/puzzles_gsheet.py`, `bot/core/**` — **M3.5 owns these on
  working-1.** Staying out of them is what makes this branch conflict-free in parallel.
- `bot/web/events.py` — reuse `publish` unchanged.
- Test infrastructure / `conftest.py` — reuse existing patterns.

## Final decisions

- CSRF = **double-submit cookie** (server sets non-HttpOnly `csrf_token`; SPA echoes it as
  `X-CSRF-Token`; guard in a global `before_request` for mutating `/api/` requests). Chosen over a
  session-synchronizer token to avoid threading the token through React state into `apiSend`.
- Board push = **web-path emit only (Phase A)**. Bang-command coverage (Phase B) is deferred because
  it needs a `PuzzleService` emit that collides with M3.5.

## Rejected approaches

- Session-synchronizer CSRF token (more frontend plumbing).
- Central `PuzzleService` board emit (touches `bot/core/`; collides with M3.5) — deferred to Phase B.

## If you get stuck

- On an unexpected failure (a fatal / missing attr / bootstrap error — NOT a clean "helper doesn't
  exist yet"), diagnose with **superpowers:systematic-debugging** before changing anything.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix, write
  `.ai/active/blocker.md` (what you tried + exact output) and STOP. Do not add scaffolding to route
  around a wrong test assumption.

## Next step

All three phases implemented and committed. Branch ready for review/merge:
- ea076d5 — Phase 1 (failing tests)
- 4040a17 — Phase 2 (CSRF)
- 897bcfb — Phase 3 (board push)

## Open questions

- Non-blocking: should Phase B (bang-command board coverage) be scheduled right after M3.5 merges?
