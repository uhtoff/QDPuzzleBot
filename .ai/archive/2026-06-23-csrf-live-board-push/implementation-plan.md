# Implementation Plan — CSRF + live board push

Authoritative spec: `current-spec.md` (this is the ordered execution view).

## Phase 1 — failing tests (Opus or careful Sonnet)
- `tests/test_csrf.py`: import `_csrf_required`, `_csrf_ok` from `bot.web`; assert
  POST/PATCH/DELETE→required, GET/HEAD→not; matching tokens→ok, missing/mismatch→not.
- `tests/test_board_events.py`: import `_board_event`, `_notify_board` from `bot.web.actions`;
  assert event dict shape (`type=="board"`, `guild_id` is `int`, `uid` present); patch
  `bot.web.events.publish` (AsyncMock not needed — `publish` is sync) and assert `_notify_board`
  calls it once and swallows a raised exception.
- Both must FAIL with ImportError/AttributeError (symbols absent), files collect cleanly.
- Commit.

## Phase 2 — CSRF
- `bot/web/__init__.py`: pure helpers + `after_request` (set cookie) + `before_request` (guard).
- `frontend/src/api.ts`: send `X-CSRF-Token` in `apiSend` + `logout()`; `friendlyError` `csrf` case.
- Verify: CSRF tests pass; `pytest tests/ -q` green; `npm run build` clean. Commit.

## Phase 3 — Live board push (web path / Phase A)
- `bot/web/actions.py`: `_board_event` + `_notify_board`; call beside each `_audit`.
- `frontend`: `useActivityFeed` `onBoard` callback; `HuntBoard` invalidates `["hunt", uid]`;
  `types.ts` `ActivityEvent.uid`.
- Verify: board-event tests pass; full suite green; `npm run build` clean. Commit.

## Verification (whole task)
- `timeout 180 pipenv run python -m pytest tests/ -q` green.
- `cd frontend && npm run build` clean.
- Manual: second browser refreshes board on first admin's mutation; mutating request without header → 403.

## Deferred (NOT in this branch)
- **Board push Phase B** — central `PuzzleService` emit for bang-command coverage. Do after M3.5
  merges; one emit point in the service. Coordinate with working-1.

## Rollback / recovery
- Per-commit revert; no DB migration involved.
