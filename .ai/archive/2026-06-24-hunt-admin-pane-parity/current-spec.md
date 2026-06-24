# Current Spec — Phase 5a of web↔bang parity: Hunt admin pane

Tool: Claude Code (handoff package)
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Target model per phase: **Phase 1 (tests) = Opus 4.8** · **Phases 2–7 (implementation) = Sonnet 4.6**
Started: 2026-06-24
Status: ready for handoff — Phase 1 (tests)

Source design (approved): `docs/superpowers/specs/2026-06-24-web-parity-hunt-admin-5a-design.md` — **read it first**.
Roadmap: `.ai/archive/2026-06-24-web-parity-puzzle-edits/web-command-parity.md` (this is **Phase 5a / Tier C**).
Predecessors (structural templates): solve-panel @ `1ca094c`, puzzle-edits @ `e01a427`; their handoffs
live under `.ai/archive/2026-06-24-*`. `parallel_hunt` was retired first (`bc60986`).

The numbered phases BELOW are this handoff's own TDD phases (tests first), **NOT** the parity plan's phases.

## Before you start (executing model)

- Execute only the **current** phase. Do not skip ahead.
- On any *unexpected* failure, invoke `superpowers:systematic-debugging` BEFORE changing anything.
- Before claiming a phase done, invoke `superpowers:verification-before-completion` and paste the
  acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix, write
  `.ai/active/blocker.md` (what you tried, the exact output) and STOP.
- A test failing because `update_hunt_settings` doesn't exist yet (AttributeError) is the RIGHT
  reason. A runtime fatal, a missing attribute, or an unknown payload key means a *wrong assumption
  in the test* — fix the test, don't add scaffolding.

## Goal

One new committing service method `PuzzleService.update_hunt_settings(hunt, **fields)` that owns the
per-hunt field mutations currently done inline (and **un-committed**) by `!set_start` / `!set_login`.
The bang commands rewire onto it (fixing the persistence bug); new admin-only web endpoints reuse it;
the dashboard Manage tab gets an admin pane plus a read-only hunt-info panel. Archive/unarchive and
create-archive-sheet are sibling **action** endpoints.

## In scope

- Phase 1: failing service tests for `update_hunt_settings` (`tests/test_hunt_settings_service.py`).
- Phase 2: the service method + rewire `set_start`/`set_login`.
- Phase 3: `PATCH /api/hunts/<uid>/settings` + `require_admin` + hunt-detail payload fields (`is_admin`,
  `start_timestamp`, `username`, `password`, `url_sep`, `puzzle_prefix`).
- Phase 4: action endpoints `POST /api/hunts/<uid>/archive`, `POST /api/hunts/<uid>/archive-sheet`.
- Phase 5: frontend read-only hunt-info panel (website · start · sheet links · login click-to-reveal).
- Phase 6: frontend admin panel in the Manage tab (settings form + Advanced collapsible + archive
  toggle + create-sheet button), gated on `is_admin`.
- Phase 7: verification (tsc, full pytest, manual click-through).

## Out of scope — DO NOT BUILD

- 5b hunt creation, 5c `delete_hunt`.
- Generic `!update_settings` for round/guild/puzzle scopes — only the six per-hunt keys named here.
- Any change to the Discord hunt-info embed or `start_timestamp`'s Discord rendering (we mirror INTO
  `start_time`, we do not touch the embed).
- Dropping the orphaned `parallel_hunt` DB column.

## Relevant files

- `docs/superpowers/specs/2026-06-24-web-parity-hunt-admin-5a-design.md` (read only — the design)
- `bot/core/puzzle_service.py` — add `update_hunt_settings` (already imports `HuntJsonDb`, `pytz`,
  `datetime`; existing `HuntJsonDb.commit(hunt)` calls at `:1308`,`:1365` are the commit idiom)
- `bot/cogs/puzzles.py` — rewire `set_login` (`:1522`) and `set_start` (`:1541`) onto the service
- `bot/web/actions.py` — new endpoints; `require_organiser` is at `:611` (takes `roles=`); `ADMIN_ROLES`
  must be imported (only `ORGANISER_ROLES` is, `:18`); `retag_puzzle` is the reference endpoint shape
- `bot/web/auth.py` — `user_can_manage(user_id, guild, roles=ORGANISER_ROLES)` at `:157`; `ADMIN_ROLES`
  already imported (`:17`)
- `bot/web/data.py` — `hunt_to_json` at `:140`, hunt-detail SELECTs at `:103`/`:111` (read only for
  reference; you will extend the detail fetch + serializer used by the hunt-detail endpoint)
- `frontend/src/components/ReconcilePanel.tsx` (read only — the current Manage-tab content),
  `frontend/src/pages/HuntBoard.tsx` (hosts the Manage tab), `frontend/src/types.ts`,
  `frontend/src/api.ts` (read only for the fetch-wrapper pattern)
- `tests/test_hunt_settings_service.py` — NEW (Phase 1)
- `tests/test_create_service.py`, `tests/test_solve_equivalence.py` (read only — test patterns)

## Constraints

- Do not touch files outside the Relevant files list. Do not refactor unrelated code.
- `update_hunt_settings` is a NEW method — no constructor/signature changes, so no `new PuzzleService(`
  call-site sweep needed. The bang rewire only changes the *bodies* of `set_login`/`set_start`.
- Keep bang behaviour identical apart from the now-correct commit: `set_login` keeps its
  `username:password` colon parsing and its `ctx.send` messages; `set_start` keeps its int arg and
  message. They simply delegate the mutate+commit to the service.
- Admin endpoints gate on `ADMIN_ROLES` and **must NOT** inherit the `if hunt["archived"]: 403`
  guard the puzzle-edit endpoints use — otherwise an archived hunt can never be un-archived.
- `username`/`password` go in the hunt-detail payload (so the team can see the shared login) but only
  for users who already pass the hunt-detail visibility gate.

## Test constraints (read — these are the failure modes)

- Reuse exact helpers/seams; invent none. Build `HuntData()` and mutate fields (see skeleton). Patch
  `bot.core.puzzle_service.HuntJsonDb` to capture `.commit`.
- Helpers are **uniquely named and local to this file** — the parallel gate shards by file; a
  cross-file top-level helper becomes an "undefined function" fatal. Do not import helpers from a
  sibling test file.
- NEVER write a loop that resolves state "until" a condition. If you think you need one, STOP and
  write `blocker.md`.
- NEVER modify test infrastructure or stub randomness unless a numbered step says to.
- Confirm every attribute exists before asserting on it — the `HuntData` fields are in
  `bot/store/hunt_data.py` (`url`, `start_timestamp`, `start_time`, `username`, `password`,
  `url_sep`, `puzzle_prefix`). Do not guess names.
- Wrap every test run in `timeout`, write output to a file, single statement (no `; echo` tail).

## Phase 1 — write the failing tests (Opus 4.8)

Create `tests/test_hunt_settings_service.py`. Near-verbatim skeleton (fill in, keep the shape):

```python
"""Phase 5a: PuzzleService.update_hunt_settings — whitelisted per-hunt field edits.

Service-level tests for the new committing settings method that the web admin pane and the
rewired !set_start / !set_login bang commands both flow through. Helpers are uniquely named
and local to this file (the parallel gate shards by file).
"""
import datetime
from unittest.mock import MagicMock, patch

import pytz

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData


def _settings_hunt():
    h = HuntData()
    h.id = 10
    h.url = "https://old.example/"
    h.start_timestamp = 0
    h.start_time = None
    h.username = ""
    h.password = ""
    h.url_sep = "-"
    h.puzzle_prefix = "puzzle"
    h.name = "H"
    return h


def _settings_service():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def test_update_url_commits_new_value():
    svc, hunt = _settings_service(), _settings_hunt()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, url="https://new.example/")
    assert hunt.url == "https://new.example/"
    hdb.commit.assert_called_once_with(hunt)


def test_update_start_timestamp_mirrors_start_time():
    svc, hunt = _settings_service(), _settings_hunt()
    ts = 1_700_000_000
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, start_timestamp=ts)
    assert hunt.start_timestamp == ts
    assert hunt.start_time == datetime.datetime.fromtimestamp(ts, tz=pytz.UTC)
    hdb.commit.assert_called_once_with(hunt)


def test_update_login_sets_both_fields():
    svc, hunt = _settings_service(), _settings_hunt()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, username="team", password="secret")
    assert (hunt.username, hunt.password) == ("team", "secret")
    hdb.commit.assert_called_once_with(hunt)


def test_update_url_building_keys():
    svc, hunt = _settings_service(), _settings_hunt()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, url_sep="_", puzzle_prefix="puzzles")
    assert (hunt.url_sep, hunt.puzzle_prefix) == ("_", "puzzles")
    hdb.commit.assert_called_once_with(hunt)


def test_unprovided_fields_left_untouched():
    svc, hunt = _settings_service(), _settings_hunt()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, url="https://new.example/")
    assert hunt.username == "" and hunt.password == ""
    assert hunt.start_timestamp == 0 and hunt.start_time is None
    assert hunt.url_sep == "-" and hunt.puzzle_prefix == "puzzle"
```

Commit the test file alone (no production code).

**Phase 1 acceptance:**

```
timeout 120 pipenv run python -m pytest tests/test_hunt_settings_service.py -q > /tmp/p1.log 2>&1
```

Exit non-zero with every test failing on `AttributeError: ... 'PuzzleService' ... 'update_hunt_settings'`
(the method doesn't exist yet) — the RIGHT reason. The file must **collect cleanly** (no import/bootstrap
fatal). If a test errors on anything other than the missing method, fix the test.

## Phase 2 — service method + bang rewire (Sonnet 4.6)

1. In `bot/core/puzzle_service.py`, add to `PuzzleService` (sync method; sentinel for "not provided"):
   ```python
   _UNSET = object()

   def update_hunt_settings(self, hunt, *, url=_UNSET, start_timestamp=_UNSET,
                            username=_UNSET, password=_UNSET, url_sep=_UNSET,
                            puzzle_prefix=_UNSET):
       """Apply whitelisted per-hunt settings and commit. Only provided keys change."""
       if url is not _UNSET:
           hunt.url = url
       if start_timestamp is not _UNSET:
           hunt.start_timestamp = start_timestamp
           hunt.start_time = datetime.datetime.fromtimestamp(start_timestamp, tz=pytz.UTC)
       if username is not _UNSET:
           hunt.username = username
       if password is not _UNSET:
           hunt.password = password
       if url_sep is not _UNSET:
           hunt.url_sep = url_sep
       if puzzle_prefix is not _UNSET:
           hunt.puzzle_prefix = puzzle_prefix
       HuntJsonDb.commit(hunt)
       return hunt
   ```
   (`_UNSET` may live at module scope if cleaner — match surrounding style.)
2. Rewire `set_login` (`bot/cogs/puzzles.py:1522`): keep the colon parse + the `ctx.send` message;
   replace the two inline assignments (`self.get_hunt(ctx).username = ...` / `.password = ...`) with
   `self.service.update_hunt_settings(self.get_hunt(ctx), username=username, password=password)`.
   Confirm the cog exposes the service as `self.service` (grep `self.service.` in the cog).
3. Rewire `set_start` (`:1541`): replace `self.get_hunt(ctx).start_timestamp = timestamp` with
   `self.service.update_hunt_settings(self.get_hunt(ctx), start_timestamp=timestamp)`; keep the message.

**Phase 2 acceptance:**

```
timeout 180 pipenv run python -m pytest tests/test_hunt_settings_service.py tests/test_create_service.py tests/test_solve_equivalence.py -q > /tmp/p2.log 2>&1
```

Phase 1 tests now pass; the create/equivalence suites stay green.

## Phase 3 — settings endpoint + payload (Sonnet 4.6)

1. In `bot/web/actions.py`: import `ADMIN_ROLES` (alongside `ORGANISER_ROLES` at `:18`). Add a thin
   `require_admin(guild_snowflake)` that calls `require_organiser(guild_snowflake, roles=ADMIN_ROLES)`.
2. Add `PATCH /api/hunts/<uid>/settings`, modelled on `retag_puzzle`: `@login_required` → resolve hunt
   → `require_admin` → parse JSON → **whitelist-check keys** against
   `{url, start_timestamp, username, password, url_sep, puzzle_prefix}` (400 on any other key) →
   `cog.service.update_hunt_settings(hunt, **provided)` → `_audit("hunt_settings", hunt_uid=...)` →
   return the refreshed hunt detail. Do NOT apply the archived-lockout.
3. In `bot/web/data.py`: extend the hunt-**detail** fetch + serializer to include `start_timestamp`,
   `username`, `password`, `url_sep`, `puzzle_prefix`, and a computed `is_admin`
   (`await user_can_manage(user_id, guild, roles=ADMIN_ROLES)`). Confirm the column names against
   `bot/store/hunt_data.py` before adding them to the SELECT.

**Phase 3 acceptance:** `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/p3.log 2>&1` stays
green (endpoint wrappers are untested by project convention — verify nothing regressed). Manually
confirm via `grep` that the whitelist set and `require_admin` are wired.

## Phase 4 — action endpoints (Sonnet 4.6)

1. `POST /api/hunts/<uid>/archive` — body `{"archived": bool}`; `require_admin`; set `hunt.archived`,
   `HuntJsonDb.commit(hunt)`, `_audit("hunt_archive", ...)`. No archived-lockout.
2. `POST /api/hunts/<uid>/archive-sheet` — `require_admin`; 409 if `hunt.archive_google_sheet_id`
   already set; else call the gsheet cog's `create_hunt_archive_spreadsheet(hunt.name)` (see
   `puzzles.py:502`), store the id, commit, `_audit("hunt_archive_sheet", ...)`; clear error if the
   gsheet cog is absent.

**Phase 4 acceptance:** `timeout 180 pipenv run python -m pytest tests/ -q` stays green; grep-confirm
the two routes + the 409 guard.

## Phase 5 — frontend hunt-info panel (Sonnet 4.6)

Read `ReconcilePanel.tsx`, `HuntBoard.tsx`, `types.ts`, `api.ts` first. Extend `HuntDetail.hunt`
(types.ts) with `start_timestamp`, `username`, `password`, `url_sep`, `puzzle_prefix`, `is_admin`.
Add a read-only hunt-info panel (website · start time · Google Sheet link · archive sheet link if any ·
username · password with **click-to-reveal**), visible to any user who can see the hunt.

**Phase 5 acceptance:** `cd frontend && timeout 120 npx tsc --noEmit > /tmp/p5.log 2>&1` clean.

## Phase 6 — frontend admin panel (Sonnet 4.6)

In the Manage tab, render an admin panel **only when `hunt.is_admin`** (keep `ReconcilePanel` as-is —
it stays organiser-gated): settings form (url, start datetime picker, username, password) + an
**Advanced** collapsible (url_sep, puzzle_prefix) + an **Archive/Unarchive** toggle (plain confirm
dialog) + a **Create archive sheet** button (→ link once `archive_google_sheet_id` is set). Wire the
three endpoints via `api.ts`.

**Phase 6 acceptance:** `cd frontend && timeout 120 npx tsc --noEmit` clean.

## Phase 7 — verification (Sonnet 4.6)

1. `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/p7.log 2>&1` — full suite green.
2. `cd frontend && timeout 120 npx tsc --noEmit` — clean.
3. Manual dashboard click-through: edit url / start / login; reveal password; toggle archive then
   unarchive; create the archive sheet (button → link). Confirm each persists across reload.
