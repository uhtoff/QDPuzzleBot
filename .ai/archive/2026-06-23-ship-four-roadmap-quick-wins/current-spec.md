# Current Spec — Four parallel-safe quick wins (alongside M7)

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2 (work here). It currently holds the
  merged M10 branch (`worktree-working-2`); base the new work cleanly on main first (see below).
Target model per phase: **Phase 1 = Opus** (test design); **Phase 2–5 = Sonnet** (implementation)
Started: 2026-06-23
Status: ready for handoff — Phase 1 (tests)

Source plan (background): `/home/russ/.claude/plans/tranquil-herding-quiche.md`.

---

## Before you start (executing model)

- **Execute only the current phase.** Do not look ahead and pre-build later phases.
- **Worktree setup first:** you are in `working-2`, which is on the merged M10 branch. Start a fresh
  branch off main's tip so the quick-wins history is clean and clearly post-M10:
  `git checkout -b quickwins-parallel main` (or `git reset --hard main` if you intend to reuse the
  `worktree-working-2` branch). `origin/main` is intentionally stale on this dev box; local `main` is
  the truth (has M0–M6 + M10). The worktree env is already set up (pipenv venv + `config.json`); if a
  command can't import deps, `pipenv sync --dev`. Confirm
  `timeout 120 pipenv run python -m pytest tests/test_core_constants.py -q` runs (env sane).
- On any **unexpected** failure (a fatal, a missing column/attr, an import error — NOT a clean
  "this function doesn't exist yet"), invoke **superpowers:systematic-debugging** before changing
  anything.
- Before claiming a phase done, invoke **superpowers:verification-before-completion** and paste the
  acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix, write
  `.ai/active/blocker.md` (what you tried, the exact output) and STOP.
- Commit at each phase boundary. Each phase is an independent, revertible unit.

## Goal

Ship four independent, conflict-free roadmap quick-wins that do NOT touch `bot/cogs/puzzles.py`, the
Google Sheets cog, or `bot/core/` (so they cannot collide with M7, in-flight on
`feature/m7-flexible-aggregation`):
- **A** — sheet-count badge on the dashboard sheets icon (frontend).
- **B** — stop leaking raw exception text in web error responses + reject a weak `session_secret` (backend).
- **C** — audit log of which human performed each web mutation (backend + one migration).
- **D** — responsive solve/retag in the dashboard (no full-board blanking) (frontend).

## In scope

Exactly the files in "Relevant files". Each candidate = its own commit.

## Out of scope

- `bot/cogs/puzzles.py`, `bot/cogs/puzzles_gsheet.py`, `bot/core/**` — DO NOT EDIT (M7 owns them).
- Full optimistic cross-column membership rewrite for retag (Candidate D) — explicitly excluded as
  fragile; do the localized-pending approach in Phase 5.
- The "aggregated answers view" roadmap item — that is M7's, not here.
- Any Quart test-client harness — see Test constraints; do NOT build one.

## Relevant files

Read-only (context):
- `/home/russ/projects/puzzbot/docs/PLANS.md` (roadmap; M10 entry shows the destructive-safety patterns)
- `bot/web/auth.py` — `current_user()` (`:107`) returns `{id, username, global_name, avatar}` or None
- `bot/web/data.py` — `fetch_all`/`fetch_one` (`:52-57`) run via `asyncio.to_thread(_run, ...)`
- `frontend/src/types.ts` — `Puzzle.sheets: AdditionalSheet[]`, board shape (`HuntDetail.board: BoardGroup[]`)
- `frontend/src/components/PuzzleTable.tsx:217` — the notes `<Indicator>` pattern to mirror
- `tests/test_refresh_meta.py`, `tests/test_solve_equivalence.py` — existing mock/patch patterns to copy
- `tests/conftest.py` — shared fixtures (`make_puzzle`, `make_round`)

Write:
- `migrations/004_web_audit_log.sql` (new)
- `bot/web/data.py` (add `execute` helper)
- `bot/web/actions.py` (`_audit` helper + calls; sanitize error responses)
- `bot/web/__init__.py` (session_secret validation; add pure `_valid_session_secret`)
- `frontend/src/api.ts` (`useSolvePuzzle` optimistic; `friendlyError` discord_error)
- `frontend/src/components/DragBoard.tsx` (per-card pending; drop full-board overlay)
- `frontend/src/components/PuzzleLinks.tsx` (sheet-count `<Indicator>`)
- New tests: `tests/test_web_audit.py`, `tests/test_session_secret.py`

## Constraints

- Do NOT touch files outside the list. Do NOT refactor unrelated code.
- Backend test runner (single statement, wrapped in timeout, output to file):
  `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` (a killed run exits 124 —
  read the log). For one file: `timeout 120 pipenv run python -m pytest tests/test_web_audit.py -q`.
  Always `pipenv run python -m pytest` (bare `pytest` can't `import bot`).
- Frontend checks: `cd frontend && npm run build` (runs `tsc --noEmit && vite build`).
- Migration files: plain `.sql`, **full-line `--` comments only** (per `migrate.py`).
- If any phase changes a function signature, grep every call site and update hand-built ones.

## Test constraints (READ — these are the rules, do not skip)

- **Reuse exact helpers/seams; invent none.** Patch `bot.web.data.execute` and `bot.web.auth.current_user`
  with `unittest.mock` exactly as `tests/test_refresh_meta.py` patches `bot.core.puzzle_service.*`.
- **There is NO Quart/web test-client harness, and you must NOT build one.** Test only the
  harness-free pure units named in Phase 1 (`_audit` with mocked deps; `_valid_session_secret`).
  Do NOT spin up the app, a test request context, or a fake Quart client.
- **NEVER write a loop that resolves state "until" a condition.** If tempted, STOP and write `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says to.
- **Confirm every column / payload key exists before use** — for the audit insert, the columns are
  defined by YOUR migration in Phase 3, but in Phase 1 assert against the exact column list named
  below; do not invent extra columns.
- **Each test file defines/imports every helper it calls** — no cross-file top-level helpers. Give
  each file uniquely-named local helpers.
- **Assert the actual value, not just structure** — for `_valid_session_secret` assert the exact
  bool per input; for `_audit` assert `execute` was awaited with the action string and user id.
- **Wrap every test run in `timeout`, output to a file, single statement** (no `; echo` tail).

---

## Phase 1 — write ALL failing tests (Opus). No production code.

Create both test files, commit them, and confirm each FAILS for the right reason (the function under
test does not exist yet — `ImportError`/`AttributeError`), AND that the file is collectible by the
real runner (no bootstrap fatal).

### `tests/test_session_secret.py`

```python
"""Phase 1 (B): session_secret strength validation must reject weak/placeholder secrets."""
import pytest

from bot.web import _valid_session_secret   # pure helper to be added in Phase 2


@pytest.mark.parametrize("secret", ["", "short", "changeme", "insecure", "x" * 31])
def test_rejects_weak_or_placeholder(secret):
    assert _valid_session_secret(secret) is False


def test_accepts_strong_secret():
    assert _valid_session_secret("k7Q" + "a" * 40) is True
```

### `tests/test_web_audit.py`

```python
"""Phase 1 (C): _audit records the acting user + action via the data.execute write helper."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_audit_writes_row_with_user_and_action():
    from bot.web import actions  # _audit added in Phase 3
    user = {"id": 4242, "username": "alice", "global_name": "Alice", "avatar": None}
    with patch("bot.web.actions.current_user", return_value=user), \
         patch("bot.web.data.execute", new=AsyncMock()) as ex:
        await actions._audit("solve", puzzle_id=7, hunt_uid="HUNTX", detail="ANSWER")
    ex.assert_awaited_once()
    sql = ex.await_args.args[0]
    params = ex.await_args.args[1]
    assert "web_audit_log" in sql
    assert 4242 in params and "solve" in params and 7 in params and "HUNTX" in params


@pytest.mark.asyncio
async def test_audit_never_raises_when_write_fails():
    from bot.web import actions
    user = {"id": 1, "username": "bob", "global_name": None, "avatar": None}
    with patch("bot.web.actions.current_user", return_value=user), \
         patch("bot.web.data.execute", new=AsyncMock(side_effect=RuntimeError("db down"))):
        # best-effort: a failed audit write must NOT propagate
        await actions._audit("retag", puzzle_id=1)
```

> NOTE on `bot.web.actions.current_user`: `actions.py` already imports `current_user` from
> `bot.web.auth` (used at `:54/:68/:82/:115`). Patch it at `bot.web.actions.current_user` (the name
> bound in that module). Confirm the import line before running.

**Phase 1 acceptance (run each, paste output):**
- `timeout 120 pipenv run python -m pytest tests/test_session_secret.py -q > /tmp/p1a.log 2>&1`
  → FAILS with `ImportError: cannot import name '_valid_session_secret'` (right reason).
- `timeout 120 pipenv run python -m pytest tests/test_web_audit.py -q > /tmp/p1b.log 2>&1`
  → FAILS with `AttributeError: module 'bot.web.actions' has no attribute '_audit'` (right reason).
- Neither file errors at *collection* with a bootstrap/import fatal unrelated to the missing symbol.
- Commit: `git add tests/test_session_secret.py tests/test_web_audit.py && git commit -m "M-quickwins: failing tests for session-secret validation + web audit helper"`

> Frontend candidates A and D have NO unit tests (no frontend test framework). They are verified in
> Phases 4–5 by `npm run build` + a manual checklist. Do not author frontend tests.

---

## Phase 2 — Candidate B: sanitize web error responses + reject weak session_secret (Sonnet)

1. `bot/web/__init__.py`: add a module-level pure helper
   ```python
   def _valid_session_secret(secret):
       """A usable Quart session secret: present, >= 32 chars, not a known placeholder."""
       if not secret or len(secret) < 32:
           return False
       return secret.lower() not in {"changeme", "insecure", "secret", "password"}
   ```
   Then in `create_app` where the empty check is today (~`:91`,
   `if not config.web_session_secret: ... return None`), change the guard to
   `if not _valid_session_secret(config.web_session_secret):` with a log message naming the reason.
2. `bot/web/actions.py` `_discord_error_response` (`:41-45`): remove the raw `"detail": str(exc)`.
   Keep `{"error": "discord_error"}`, status 502, and `logger.exception("web discord error")` the
   real exception server-side. (Leave the `{"error": str(exc)}` sites at `:221/:257/:484/:513` —
   those are intentional RuntimeError **codes** mapped by the frontend `friendlyError` switch.)
   Also sanitize the generic `:435` `{"ok": False, "error": str(exc)}` → a fixed `"internal_error"`
   code + `logger.exception`.
3. `frontend/src/api.ts` `friendlyError` `discord_error` case (`:64-65`): drop the `d.detail`
   dependency → return a fixed `"Discord error — please try again."`.

**Acceptance (paste output):**
- `timeout 120 pipenv run python -m pytest tests/test_session_secret.py -q` → PASS.
- `cd frontend && npm run build` → clean.
- `grep -n 'str(exc)' bot/web/actions.py` → only the intentional RuntimeError-code sites remain
  (`:221/:257/:484/:513`); none in `_discord_error_response` or `:435`.
- Commit: `git commit -am "quickwin(B): stop leaking raw exception text; reject weak session_secret"`

## Phase 3 — Candidate C: audit log for web actions (Sonnet)

1. `migrations/004_web_audit_log.sql` (full-line `--` comments only):
   ```sql
   -- Audit log of dashboard (web) mutations: which human did what.
   CREATE TABLE IF NOT EXISTS web_audit_log (
     id          BIGINT AUTO_INCREMENT PRIMARY KEY,
     created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
     user_id     BIGINT NOT NULL,
     username    VARCHAR(255),
     action      VARCHAR(64) NOT NULL,
     puzzle_id   INT NULL,
     hunt_uid    VARCHAR(64) NULL,
     detail      VARCHAR(255) NULL
   );
   ```
   ⚠️ **Migration number:** if M7 has already taken `004_*.sql`, rename this to the next free `NNN`
   (migrations apply in filename order; an unapplied file renames freely). Note it in the commit.
2. `bot/web/data.py`: add
   ```python
   async def execute(sql, params=()):
       """Run a writing statement on the dedicated web connection and commit."""
       return await asyncio.to_thread(_run_write, sql, params)
   ```
   and a `_run_write` mirroring `_run` (`:` nearby) but calling `conn.commit()` after `cur.execute`.
   (Open `_run` first and match its connection handling exactly.)
3. `bot/web/actions.py`: add
   ```python
   from bot.web import data
   async def _audit(action, *, puzzle_id=None, hunt_uid=None, detail=None):
       try:
           u = current_user() or {}
           await data.execute(
               "INSERT INTO web_audit_log (user_id, username, action, puzzle_id, hunt_uid, detail) "
               "VALUES (%s,%s,%s,%s,%s,%s)",
               (u.get("id"), u.get("username"), action, puzzle_id, hunt_uid, detail),
           )
       except Exception:
           logger.exception("web audit log write failed")
   ```
   Then `await _audit(...)` on the success path of each mutation endpoint: `add_note`, `erase_note`,
   `update_puzzle`, `add_sheet`, `remove_sheet`, `solve_puzzle`, the meta/retag endpoint,
   `rebuild_rollup`, `create_puzzle`, `create_meta`. Use a short action string per endpoint
   (`"note"`, `"solve"`, `"retag"`, `"create_puzzle"`, …). Pass `puzzle_id`/`hunt_uid` where the
   handler already has them; never block the user action on an audit failure (the try/except handles it).

**Acceptance (paste output):**
- `timeout 120 pipenv run python -m pytest tests/test_web_audit.py -q` → PASS (2 tests).
- `timeout 180 pipenv run python -m pytest tests/ -q` → green.
- `python migrate.py` applies `004_web_audit_log.sql` without error (against the test DB).
- Commit: `git commit -am "quickwin(C): web_audit_log + _audit on every dashboard mutation"`

## Phase 4 — Candidate A: sheet-count badge (Sonnet, frontend, no unit tests)

1. `frontend/src/components/PuzzleLinks.tsx`: import `Indicator` from `@mantine/core`; wrap the
   spreadsheet `ActionIcon` in
   `<Indicator label={puzzle.sheets.length} size={16} disabled={puzzle.sheets.length === 0} color="green"> ... </Indicator>`
   (mirror `PuzzleTable.tsx:217`'s notes Indicator). Keep the existing `Tooltip`/link.

**Acceptance:**
- `cd frontend && npm run build` → clean (tsc + vite).
- Manual: a puzzle with N additional sheets shows the count on the spreadsheet icon; zero shows none.
- Commit: `git commit -am "quickwin(A): sheet-count badge on the sheets icon"`

## Phase 5 — Candidate D: responsive solve/retag (Sonnet, frontend, no unit tests)

1. `frontend/src/api.ts` `useSolvePuzzle`: add `onMutate`/`onError`/`onSettled` (react-query):
   - `onMutate({puzzleId, solution})`: `await qc.cancelQueries({queryKey:["hunt",uid]})`; snapshot
     `const previous = qc.getQueryData(["hunt",uid])`; `qc.setQueryData(["hunt",uid], old => …)`
     mapping over `old.board[].puzzles` and, for puzzles with `id===puzzleId`, set
     `solved:true, solution, status:"Solved"`. Return `{previous}`.
   - `onError(_e,_v,ctx)`: `qc.setQueryData(["hunt",uid], ctx.previous)`.
   - `onSettled`: `qc.invalidateQueries({queryKey:["hunt",uid]})` (keep — authoritative reconcile,
     fixes feeder counts). Keep the existing `onSuccess` sheet_warning handling (move it into the
     mutate call's onSuccess in DragBoard, or keep here).
2. `frontend/src/components/DragBoard.tsx`:
   - REMOVE the full-board `<LoadingOverlay visible={retag.isPending} … />` (`:242`).
   - Track the in-flight retag target (e.g. `retag.isPending ? retag.variables?.puzzleId : null`)
     and pass it down so the matching `PuzzleCard` renders a small pending state (dim + a tiny
     `Loader` from `@mantine/core`, and disable its drag). Keep the existing error `Text` (`:277`)
     and `sheet_warning` notification (`:202`).

**Acceptance:**
- `cd frontend && npm run build` → clean.
- Manual checklist: (1) solving a puzzle turns its card green/shows the solution instantly; on a
  forced error it reverts. (2) A retag leaves the rest of the board visible and interactive; only the
  moved card shows a pending state; the board reconciles when the write completes.
- Commit: `git commit -am "quickwin(D): optimistic solve + per-card retag pending (no board blanking)"`

## Verification (whole handoff)

- `timeout 180 pipenv run python -m pytest tests/ -q` green; `cd frontend && npm run build` clean;
  `python migrate.py` applies cleanly.
- Five commits (one per phase: tests, B, C, A, D), each independently revertible.

## Rollback / recovery notes

- Each candidate is isolated; revert its single commit to back it out (Phase 1 tests can stay).
- The only cross-cutting risk is the audit-log migration number vs M7 — renumber at merge if needed.
