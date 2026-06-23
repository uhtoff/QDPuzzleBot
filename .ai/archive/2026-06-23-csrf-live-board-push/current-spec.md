# Current Spec — CSRF protection + live board push (dashboard)

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2 (work here)
Branch: `feature/dashboard-live-csrf` (fresh off `main` @ `5cc71b3`; `docs(PLANS)` commit `5856861` on top)
Target model per phase: **Phase 1 = Opus** (test design); **Phases 2–3 = Sonnet** (implementation)
Started: 2026-06-23
Status: ready for handoff — Phase 1 (tests)

Two independent, low-contention dashboard quick-wins, packaged together because both live in
`bot/web/` + `frontend/` and neither touches the contention magnet (`bot/cogs/puzzles.py`) or the
service layer — so they run cleanly in parallel with **M3.5 (working-1)** and the M8/M11 fan-out.

---

## Before you start (executing model)

- **Execute only the current phase.** Do not look ahead and pre-build later phases.
- The worktree env is already set up (pipenv venv + gitignored `config.json`). Confirm env sanity:
  `timeout 120 pipenv run python -m pytest tests/test_core_constants.py -q`. If a command can't
  `import bot`, you forgot `pipenv run python -m pytest` (bare `pytest` can't import the package);
  if deps are missing, `pipenv sync --dev`.
- On any **unexpected** failure (a fatal, a missing attr, an import error of something that SHOULD
  exist, a Quart bootstrap error — NOT a clean "this helper doesn't exist yet"), invoke
  **superpowers:systematic-debugging** before changing anything.
- Before claiming a phase done, invoke **superpowers:verification-before-completion** and paste the
  acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix, write
  `.ai/active/blocker.md` (what you tried, the exact output) and STOP.
- Commit at each phase boundary. Each phase is an independent, revertible unit.

## Goal

1. **CSRF** — add a CSRF token to dashboard mutating requests (defense-in-depth on top of the
   existing `SameSite=Lax` + `@login_required`). Design: **double-submit cookie**.
2. **Live board push (Phase A — web path)** — on each web mutation, publish a board-invalidation
   event over the existing `/api/ws/activity` socket so OTHER viewers' SPAs refetch the hunt
   immediately instead of waiting for the 30s poll.

## In scope (exactly these files)

Write:
- `bot/web/__init__.py` — pure CSRF helpers + app `before_request`/`after_request` hooks.
- `bot/web/actions.py` — `_board_event` + `_notify_board` helpers; a call beside each `_audit`.
- `frontend/src/api.ts` — send `X-CSRF-Token`; `friendlyError` `csrf` case.
- `frontend/src/hooks/useActivityFeed.ts` — optional `onBoard` callback.
- `frontend/src/pages/HuntBoard.tsx` — pass `onBoard` that invalidates `["hunt", uid]`.
- `frontend/src/types.ts` — add `uid?: string` to `ActivityEvent`.
- New tests: `tests/test_csrf.py`, `tests/test_board_events.py`.

## Out of scope

- `bot/cogs/puzzles.py`, `bot/cogs/puzzles_gsheet.py`, `bot/core/**` — DO NOT EDIT (M3.5 owns
  `puzzle_service.py` + the cog create paths on working-1).
- **Board push Phase B (bang-command coverage):** emitting board events from `PuzzleService` so a
  pure-Discord `!s`/`!move`/`!p` reflects on the dashboard. Needs a central service emit (touches
  `bot/core/`) → **deferred and gated on M3.5**, which is introducing a `Notifier` class that Phase B
  routes through (NOT a `bot/web/events` import into core). Full spec: `.ai/active/phase-b-deferred.md`.
  Do not build it here.
- No Quart/web test-client harness — test only the pure helpers named in Phase 1. Do NOT spin up the
  app, a request context, or a fake Quart client.
- No new migration (neither feature touches the schema).
- Do not refactor unrelated code; do not touch `bot/web/events.py` (reuse `publish` as-is).

## Relevant files (verified 2026-06-23)

Read-only (context):
- `bot/web/__init__.py:67-90` — `create_app`; `app.secret_key` + `SESSION_COOKIE_*` set here; this
  is where the `before_request`/`after_request` hooks and the `_valid_session_secret` pure helper
  (`:18`) already live — mirror that helper's module-level style.
- `bot/web/auth.py` — `auth_bp` `/api/auth` (`:21`); login `:30` (GET), callback `:45` (GET, sets
  `session["user"]` `:61`), logout `:70` (**POST**), `/me` `:76`, `login_required` `:111`. (The OAuth
  routes are GET, so the CSRF guard never blocks them; only logout is a mutating auth route.)
- `bot/web/api.py:141-177` — `ws_activity`. **Filter is `event.get("guild_id") in membership`
  (`:171`)**, where `membership` is keyed by the **Discord guild snowflake as an int**. ⇒ a board
  event MUST carry `"guild_id": <int snowflake>` or it is silently dropped.
- `bot/web/events.py` — `publish(event)` (`:24`, **sync**, `put_nowait`, drops on full queue),
  `subscribe`/`unsubscribe`. Import-safe (no Quart). Reuse `publish` unchanged.
- `bot/cogs/activity.py:60` — the existing `events.publish({"type":"activity","channel_id":...,
  "guild_id": message.guild.id, ...})`. Copy its conventions: `guild_id` is the int snowflake.
- `bot/web/actions.py` — every mutation endpoint calls `await _audit(...)` on its success path with
  `ctx["hunt"]["uid"]` and (where Discord is involved) `ctx["guild_snowflake"]` in scope. `_audit`
  (`:50`) is the pattern to mirror for `_notify_board`. Existing `_audit` call sites: `add_note`
  (`:131`), `erase_note` (`:153`), `update_puzzle` (`:196`), `add_sheet` (`:236`), `remove_sheet`
  (`:273`), `solve_puzzle` (`:326`), `retag_puzzle` (`:~411`), `rebuild_rollup`, `create_puzzle`,
  `create_meta`. `ctx["guild_snowflake"]` is read at `:199/232/269/309/360`.
- `frontend/src/api.ts:12-29` — `apiSend` is the SINGLE client choke-point for POST/PATCH/DELETE.
  `:82` `logout()` is a separate bare `fetch` (also needs the header). `friendlyError` `:31`.
  `useHunt` `:105` (`queryKey:["hunt", uid]`, `refetchInterval: 30_000`).
- `frontend/src/hooks/useActivityFeed.ts` — `onmessage` (`:26`) handles only `type==="activity"`.
  **Only call site: `frontend/src/pages/HuntBoard.tsx:29`** (`const live = useActivityFeed();`).
- `frontend/src/types.ts:94-100` — `ActivityEvent { type; channel_id?; guild_id?; at?; author? }`.
- `frontend/src/pages/HuntBoard.tsx:26-29` — has `uid` from `useParams()`; does NOT yet import
  `useQueryClient`.

## Constraints

- Do NOT touch files outside the In-scope list. Do NOT refactor unrelated code.
- Backend test runner (single statement, wrapped in `timeout`, output to a file): for the two new
  files `timeout 120 pipenv run python -m pytest tests/test_csrf.py tests/test_board_events.py -q`;
  full suite `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` (a killed run
  exits 124 — read the log). Always `pipenv run python -m pytest`.
- Frontend checks: `cd frontend && npm run build` (runs `tsc --noEmit && vite build`).
- **Signature change — grep call sites.** Phase 3 adds an optional `onBoard` param to
  `useActivityFeed`. It is optional so existing calls still type-check, but still
  `grep -rn "useActivityFeed(" frontend/src` and confirm the only site (`HuntBoard.tsx:29`) is the
  one you update. No backend signatures change (all new symbols are additive module-level helpers).

## Test constraints (READ — these are the rules, do not skip)

- **Reuse exact helpers/seams; invent none.** Patch `bot.web.events.publish` with `unittest.mock`
  exactly as `tests/test_web_audit.py` patches `bot.web.data.execute`. `events.publish` is **sync**,
  so patch with a plain `Mock` (NOT `AsyncMock`) and call `_notify_board` WITHOUT `await`.
- **There is NO Quart/web test-client harness, and you must NOT build one.** Test only the
  harness-free pure units below. Do not spin up the app or a request context.
- **NEVER write a loop that resolves state "until" a condition.** If tempted, STOP and write `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says to.
- **Confirm every attribute/key exists before use.** The helpers under test are defined by Phase 2/3
  below — assert exactly the keys named here (`type`, `uid`, `guild_id`); invent no extra keys.
- **Each test file defines/imports every helper it calls** — no cross-file top-level helpers. Give
  each file uniquely-named local helpers.
- **Assert the actual value, not just structure** — for `_csrf_required`/`_csrf_ok` assert the exact
  bool per input; for `_board_event` assert `guild_id` is an `int` (not a str) and equals the parsed
  snowflake; for `_notify_board` assert `publish` was called once with the action payload.
- **Wrap every test run in `timeout`, output to a file, single statement** (no `; echo` tail).

---

## Phase 1 — write ALL failing tests (Opus). No production code.

Create both files, commit, and confirm each FAILS for the right reason (the helper does not exist
yet — `ImportError`/`AttributeError`), AND that each file is collectible by the real runner (no
bootstrap fatal unrelated to the missing symbol).

### `tests/test_csrf.py`

```python
"""Phase 1 (CSRF): double-submit helpers gate mutating requests only."""
import pytest

from bot.web import _csrf_required, _csrf_ok   # pure helpers added in Phase 2


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutating_methods_require_csrf(method):
    assert _csrf_required(method) is True


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_do_not_require_csrf(method):
    assert _csrf_required(method) is False


def test_csrf_ok_when_tokens_present_and_equal():
    assert _csrf_ok("k7Qabc", "k7Qabc") is True


@pytest.mark.parametrize("header,cookie", [
    (None, "abc"), ("abc", None), (None, None), ("", ""), ("abc", "xyz"),
])
def test_csrf_rejects_missing_or_mismatched(header, cookie):
    assert _csrf_ok(header, cookie) is False
```

### `tests/test_board_events.py`

```python
"""Phase 1 (board push): event builder + best-effort notifier over events.publish."""
from unittest.mock import patch

import pytest


def test_board_event_shape_and_int_guild():
    from bot.web import actions  # _board_event added in Phase 3
    ev = actions._board_event("HUNTX", "123456789")
    assert ev["type"] == "board"
    assert ev["uid"] == "HUNTX"
    assert ev["guild_id"] == 123456789
    assert isinstance(ev["guild_id"], int)


def test_board_event_none_guild_is_none_not_crash():
    from bot.web import actions
    ev = actions._board_event("HUNTX", None)
    assert ev["guild_id"] is None
    assert ev["uid"] == "HUNTX"


def test_notify_board_publishes_once():
    from bot.web import actions  # _notify_board added in Phase 3
    with patch("bot.web.events.publish") as pub:   # publish is SYNC — plain Mock
        actions._notify_board("HUNTX", "123456789")
    pub.assert_called_once()
    sent = pub.call_args.args[0]
    assert sent["type"] == "board" and sent["uid"] == "HUNTX" and sent["guild_id"] == 123456789


def test_notify_board_never_raises_when_publish_fails():
    from bot.web import actions
    with patch("bot.web.events.publish", side_effect=RuntimeError("boom")):
        # best-effort: a publish failure must NOT propagate
        actions._notify_board("HUNTX", "123456789")
```

**Phase 1 acceptance (run, paste output):**
- `timeout 120 pipenv run python -m pytest tests/test_csrf.py -q > /tmp/p1a.log 2>&1`
  → FAILS at collection with `ImportError: cannot import name '_csrf_required' from 'bot.web'`.
- `timeout 120 pipenv run python -m pytest tests/test_board_events.py -q > /tmp/p1b.log 2>&1`
  → FAILS with `AttributeError: module 'bot.web.actions' has no attribute '_board_event'`
  (the `_notify_board` tests fail the same way once `_board_event` exists; both are Phase-3 symbols).
- Neither file errors at *collection* with a bootstrap/import fatal unrelated to the missing symbol.
- Commit: `git add tests/test_csrf.py tests/test_board_events.py && git commit -m "live-csrf: failing tests for CSRF helpers + board-event helpers"`

> Frontend changes (api.ts, useActivityFeed, HuntBoard, types) have NO unit tests (no frontend test
> framework). They are verified in Phases 2–3 by `npm run build` + the manual checklist. Do not
> author frontend tests.

---

## Phase 2 — CSRF double-submit token (Sonnet)

1. `bot/web/__init__.py`: add two module-level pure helpers (near `_valid_session_secret`):
   ```python
   import secrets

   _CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

   def _csrf_required(method):
       """True for state-changing HTTP methods (request.method is always upper-case)."""
       return method in _CSRF_METHODS

   def _csrf_ok(header_token, cookie_token):
       """Double-submit check: both present and equal (constant-time)."""
       if not header_token or not cookie_token:
           return False
       return secrets.compare_digest(header_token, cookie_token)
   ```
2. In `create_app` (after the blueprints are registered), add:
   - `@app.after_request` — if `request.cookies.get("csrf_token")` is absent, set a fresh cookie:
     `resp.set_cookie("csrf_token", secrets.token_urlsafe(32), samesite="Lax",
     secure=config.web_oauth_redirect_uri.startswith("https://"), httponly=False)`. (Non-HttpOnly so
     the SPA can read it. `import request` from quart at the top of `create_app`'s quart imports.)
   - `@app.before_request` — guard mutating API requests:
     ```python
     if request.path.startswith("/api/") and _csrf_required(request.method):
         if not _csrf_ok(request.headers.get("X-CSRF-Token"), request.cookies.get("csrf_token")):
             from quart import jsonify
             return jsonify({"error": "csrf"}), 403
     ```
3. `frontend/src/api.ts`:
   - Add a tiny reader: `function csrfToken() { return document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)?.[1] ?? ""; }`
   - In `apiSend`, merge `"X-CSRF-Token": csrfToken()` into the headers (keep `Content-Type` when a
     body is present).
   - In `logout()`, add `headers: { "X-CSRF-Token": csrfToken() }`.
   - `friendlyError`: add `case "csrf": return "Your session expired — please refresh the page.";`

**Acceptance (paste output):**
- `timeout 120 pipenv run python -m pytest tests/test_csrf.py -q` → PASS.
- `timeout 180 pipenv run python -m pytest tests/ -q` → green.
- `cd frontend && npm run build` → clean.
- Commit: `git commit -am "feat(web): CSRF double-submit token on dashboard mutations"`

## Phase 3 — Live board push (Phase A, web path) (Sonnet)

1. `bot/web/actions.py`: add (near `_audit`):
   ```python
   from . import events   # add to the existing `from . import data` import group

   def _board_event(uid, guild_snowflake):
       """Lightweight board-invalidation event; guild_id must be the int snowflake to pass the
       ws_activity membership filter (api.py:171). None snowflake → None (event harmlessly dropped)."""
       return {"type": "board", "uid": uid,
               "guild_id": int(guild_snowflake) if guild_snowflake else None}

   def _notify_board(uid, guild_snowflake):
       """Best-effort board push — never raises."""
       try:
           events.publish(_board_event(uid, guild_snowflake))
       except Exception:
           logger.exception("board push failed")
   ```
   Then call `_notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))` on the success path of
   each mutation endpoint, right beside its existing `await _audit(...)` line. For endpoints that
   already read a `snowflake` local (add_sheet/remove_sheet/solve/retag), pass that. `_notify_board`
   is sync — call it WITHOUT `await`.
2. `frontend/src/types.ts`: add `uid?: string;` to `ActivityEvent`.
3. `frontend/src/hooks/useActivityFeed.ts`:
   - Change the signature to `export function useActivityFeed(onBoard?: (uid: string) => void): Record<string, LiveActivity> {`.
   - In `onmessage`, after the existing `activity` branch, add:
     `if (event.type === "board" && event.uid) { onBoard?.(event.uid); }`.
   - Keep `onBoard` current via a ref so the long-lived socket always calls the latest closure:
     `const cb = useRef(onBoard); cb.current = onBoard;` and call `cb.current?.(event.uid)`.
     (Do NOT add `onBoard` to the `useEffect` deps — that would reconnect the socket on every render.)
4. `frontend/src/pages/HuntBoard.tsx`:
   - `import { useQueryClient } from "@tanstack/react-query";` and `const qc = useQueryClient();`.
   - Change `const live = useActivityFeed();` to
     `const live = useActivityFeed((u) => { if (u === uid) qc.invalidateQueries({ queryKey: ["hunt", uid] }); });`

**Acceptance (paste output):**
- `timeout 120 pipenv run python -m pytest tests/test_board_events.py -q` → PASS (4 tests).
- `timeout 180 pipenv run python -m pytest tests/ -q` → green.
- `grep -rn "useActivityFeed(" frontend/src` → only `HuntBoard.tsx` (updated).
- `cd frontend && npm run build` → clean.
- Manual: a second browser viewing the same hunt updates within the socket window after the first
  admin mutates (no 30s wait); a mutating request with no `X-CSRF-Token` header is rejected 403.
- Commit: `git commit -am "feat(web): live board-invalidation push on dashboard mutations"`

## Verification (whole handoff)

- `timeout 180 pipenv run python -m pytest tests/ -q` green; `cd frontend && npm run build` clean.
- Three commits (tests, CSRF, board-push), each independently revertible.

## Rollback / recovery notes

- Each feature is one commit; revert independently. Phase 1 tests can stay.
- No migration, no schema change — nothing to roll back DB-side.

## Rejected approaches

- **Synchronizer CSRF token in session exposed via `/me`**: marginally tighter but needs threading
  the token through React state into the bare `apiSend` function. Double-submit reads the cookie
  directly — less frontend plumbing for an internal role-gated site.
- **Central `PuzzleService` emit for board push (covers bang commands too)**: correct end-state, but
  touches `bot/core/` → collides with M3.5 on working-1. Deferred to Phase B post-M3.5.
