# Current Spec — Phase 5b + 5c of web↔bang parity: hunt create & delete

Tool: Claude Code (handoff package)
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Target model per phase: **Phase 1 (tests) = Opus 4.8** · **Phases 2–8 (implementation) = Sonnet 4.6**
Started: 2026-06-24
Status: ready for handoff — Phase 1 (tests)

Source design (approved): `docs/superpowers/specs/2026-06-24-web-parity-hunt-create-delete-5bc-design.md` — **read it first**.
Roadmap: `.ai/archive/2026-06-24-web-parity-puzzle-edits/web-command-parity.md` (Tier C).
Predecessor (structural template, shipped): 5a hunt admin pane —
`docs/superpowers/specs/2026-06-24-web-parity-hunt-admin-5a-design.md`.

The numbered phases below are this handoff's own TDD phases (tests first), **NOT** the parity plan's phases.

## Before you start (executing model)

- Execute only the **current** phase. Do not skip ahead.
- On any *unexpected* failure, invoke `superpowers:systematic-debugging` BEFORE changing anything.
- Before claiming a phase done, invoke `superpowers:verification-before-completion` and paste the
  acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix, write
  `.ai/active/blocker.md` (what you tried, the exact output) and STOP.
- A test failing because `create_hunt` / `delete_hunt` doesn't exist yet (AttributeError) is the
  RIGHT reason. A runtime fatal, a missing attribute, or an unknown field means a *wrong assumption
  in the test* — fix the test, don't add scaffolding.

## Goal

Two new committing service methods — `PuzzleService.create_hunt(guild, ...)` and
`PuzzleService.delete_hunt(guild, hunt)` — that own the hunt provisioning/teardown currently done
inline (and `ctx`-coupled) by the bang `!hunt` and `!delete_hunt` commands. The bang commands rewire
onto them; new admin-only web endpoints reuse them; the dashboard gets a New-hunt form (HuntList) and
a Delete-hunt danger-zone (admin pane). `delete_hunt` also deletes the whole Google **spreadsheet
file(s)** (not just tabs) via a new gsheet-cog method, so the bang gains that behaviour too.

## In scope

- Phase 1: failing service tests for `create_hunt` + `delete_hunt`.
- Phase 2: `create_hunt` service method + rewire bang `!hunt`.
- Phase 3: `delete_hunt` service method (+ new gsheet whole-file delete) + rewire bang `!delete_hunt`.
- Phase 4: `POST /api/hunts` + admin-guild source for the picker.
- Phase 5: `DELETE /api/hunts/<uid>`.
- Phase 6: frontend New-hunt form on HuntList.
- Phase 7: frontend Delete-hunt danger-zone in `AdminSettingsPanel`.
- Phase 8: verification.

## Out of scope — DO NOT BUILD

- `create_as_archived` (dropped). Hunts are always created live; archive afterward via the 5a toggle.
- Generic `!update_settings` surface (already shipped).
- Any change to the Discord welcome embed or `start_timestamp` Discord rendering.
- Reviving the retired `parallel_hunt` feature.
- Editing hunt settings during creation beyond the named fields (use the 5a admin pane post-create).

## Relevant files

- `docs/superpowers/specs/2026-06-24-web-parity-hunt-create-delete-5bc-design.md` (read only — design)
- `bot/core/puzzle_service.py` — add `create_hunt` + `delete_hunt`. **Structural templates:**
  `create_mini` (`:1029`, a ctx-free service-create: takes `guild`, creates Discord objects, uses
  `self._gsheet_cog(hunt)` `:94`, commits, best-effort welcome) and `delete_round` (`:811`, teardown
  with per-puzzle failed-channel isolation via `self._safe_delete_channel` `:776`). Module-level
  `clean_name` is already imported and used by `create_mini`.
- `bot/cogs/puzzles.py` — current `hunt`/`create_hunt` (`:404`–`:485`) and `delete_hunt`
  (`:2654`–`:2681`) with helpers `_confirm_destructive` (`:2621`), `_delete_puzzles` (`:2565`),
  `_delete_round_category` (`:2584`), `delete_round_data` (`:2617`), `_warn_failed_channels`
  (`:2576`). Cog constants the create path uses: `GENERAL_CHANNEL_NAME`, `SOLVE_CATEGORY`,
  `SOLVE_DIVIDER`, `get_solved_puzzle_category`, `check_available_space`, `get_guild_data`.
- `bot/cogs/puzzles_gsheet.py` — `create_hunt_spreadsheet` (`:513`), `create_hunt_archive_spreadsheet`
  (`:526`), `delete_puzzle_spreadsheet` (`:415`, tab-level only). Drive file creation uses
  `get_drive().files().copy/create`; the whole-file delete (`get_drive().files().delete(fileId=...)`)
  does NOT exist yet — Phase 3 adds it.
- `bot/store/mysqldb.py` — `HuntJsonDb.check_duplicates(name)` (`:82`), `generate_uid('uid')` (`:90`),
  `delete(id)` (`:74`); `PuzzleJsonDb.get_all_from_hunt`, `RoundJsonDb.get_all(hunt_id)`.
- `bot/store/hunt_data.py` — `HuntData` fields (read only; confirm names before asserting):
  `name, id, uid, category_id, channel_id, guild_id, google_sheet_id, archive_google_sheet_id, url,
  url_sep, puzzle_prefix, archived, role_id, start_timestamp, username, password, start_time`.
- `bot/web/actions.py` — add endpoints. Seams: `require_admin` (added in 5a, forwards to
  `require_organiser` with `ADMIN_ROLES`), `_audit` (`:51`, takes `detail=`), `_visible_hunt_by_uid`
  (`:110`), `_discord_error_response`, and the `delete_round` endpoint (the DELETE template).
  `current_app.bot.get_guild(snowflake)` resolves the guild; `current_app.bot.get_cog("Puzzles")`
  gives `cog.service`.
- `bot/web/data.py` / `bot/web/api.py` — guild data + `get_guilds_map`; add the admin-guild source.
- `bot/web/auth.py` — `user_can_manage(user_id, guild, roles=...)` (`:157`), `ADMIN_ROLES`,
  `resolve_membership`.
- `frontend/src/` — HuntList page (find it: the all-hunts screen), `api.ts` (mutation pattern),
  `components/TypedConfirmModal.tsx` (typed-confirm gate), `components/AdminSettingsPanel.tsx`
  (host the Delete danger-zone), `types.ts`.
- `tests/test_create_hunt_service.py`, `tests/test_delete_hunt_service.py` — NEW (Phase 1).
- `tests/test_solve_equivalence.py`, `tests/test_create_service.py` (read only — patterns).

## Constraints

- Do not touch files outside the Relevant files list. Do not refactor unrelated code.
- `create_hunt` / `delete_hunt` are NEW methods — no constructor/signature changes, so no
  `PuzzleService(` call-site sweep. The bang rewires change only the *bodies* of `create_hunt`
  (cog) / `delete_hunt` (cog) to delegate to `self.service`.
- `start_timestamp` writes mirror into `start_time` (the 5a idiom: `start_time =
  datetime.datetime.fromtimestamp(ts, tz=pytz.UTC)`). Do not touch the Discord embed.
- Admin endpoints gate on `ADMIN_ROLES` via `require_admin`. The `DELETE /api/hunts/<uid>` endpoint
  must **NOT** inherit the `if hunt["archived"]: 403` guard — an archived hunt must stay deletable.
- Keep bang behaviour identical apart from the shared-seam change: `delete_hunt` now deletes the
  whole spreadsheet file(s). Keep the cog's `ConfirmView` prompt + welcome embed in the cog.

## Test constraints (read — these are the failure modes)

- Reuse exact helpers/seams; invent none. Helpers are **uniquely named and local to each test
  file** — the parallel gate shards by file; a cross-file top-level helper becomes an "undefined
  function" fatal. Do not import helpers from a sibling test file.
- Confirm every attribute exists before asserting on it — `HuntData` fields are in
  `bot/store/hunt_data.py`. Do not guess names.
- **Assert the stable contract, not implementation-defined Discord call sequences.** The committed
  `HuntData` fields, the gsheet-cog calls, the duplicate guard, and the teardown DB deletes are
  stable. Do NOT assert the exact `guild.create_category(...)` / `create_text_channel(...)` call
  order — that is decided in Phase 2 and would over-constrain a method that doesn't exist yet.
  Build `guild` as a flexible `MagicMock` whose async methods return mocks exposing `.id`.
- NEVER write a loop that resolves state "until" a condition. If you think you need one, STOP and
  write `blocker.md`.
- NEVER modify test infrastructure or stub randomness unless a numbered step says to.
- Patch `bot.core.puzzle_service.HuntJsonDb` (and `PuzzleJsonDb`/`RoundJsonDb` where the method
  uses them) to capture `.commit` / `.delete` and to control `check_duplicates`.
- Wrap every test run in `timeout`, write output to a file, single statement (no `; echo` tail).

## Phase 1 — write all the failing tests (Opus 4.8)

Create both files. Near-verbatim skeletons (fill in, keep the shape). `create_hunt`/`delete_hunt`
are `async` — use `pytest.mark.asyncio` exactly as `tests/test_solve_equivalence.py` does (copy its
async marker/import style; do not invent a new one).

### `tests/test_create_hunt_service.py`

```python
"""Phase 5b: PuzzleService.create_hunt — provision a hunt (Discord + sheet + DB row).

Asserts the STABLE contract only: the committed HuntData fields, the gsheet-cog calls, and the
duplicate guard. Does NOT assert the exact Discord API call sequence (implementation-defined).
Helpers are uniquely named and local to this file.
"""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytz

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData


def _create_guild():
    """Flexible guild double: async Discord creators return objects exposing .id."""
    guild = MagicMock()
    guild.id = 999
    cat = MagicMock(); cat.id = 111
    chan = MagicMock(); chan.id = 222
    role = MagicMock(); role.id = 333
    guild.categories = []
    guild.roles = []
    guild.create_category = AsyncMock(return_value=cat)
    guild.create_text_channel = AsyncMock(return_value=chan)
    guild.create_role = AsyncMock(return_value=role)
    return guild


def _create_service():
    bot = MagicMock()
    bot.get_cog.return_value = None          # no gsheet cog unless a test injects one
    return PuzzleService(bot)


def _committed_hunt(commit_mock):
    """The HuntData passed to HuntJsonDb.commit (last call)."""
    assert commit_mock.called
    return commit_mock.call_args[0][0]


def test_create_hunt_commits_core_fields():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        # NOTE: the executing agent fills in the real keyword args create_hunt accepts.
        # await the call; capture the committed HuntData via hdb.commit.
        # svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=...)
        ...
    hunt = _committed_hunt(hdb.commit)
    assert isinstance(hunt, HuntData)
    assert hunt.name == "H"
    assert hunt.url == "https://h/"
    assert hunt.uid == "abc123"


def test_create_hunt_mirrors_start_time():
    svc, guild = _create_service(), _create_guild()
    ts = 1_700_000_000
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        # svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=ts)
        ...
    hunt = _committed_hunt(hdb.commit)
    assert hunt.start_timestamp == ts
    assert hunt.start_time == datetime.datetime.fromtimestamp(ts, tz=pytz.UTC)


def test_create_hunt_duplicate_name_does_not_commit():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = True
        # The method must signal a duplicate WITHOUT committing. Pin the exact behaviour
        # the agent chooses (raise a named error OR return a falsy/sentinel) once implemented.
        ...
    hdb.commit.assert_not_called()


def test_create_hunt_captures_sheet_and_role():
    svc, guild = _create_service(), _create_guild()
    gsheet = MagicMock()
    gsheet.create_hunt_spreadsheet = AsyncMock(return_value="SHEET_ID")
    gsheet.create_hunt_archive_spreadsheet = AsyncMock(return_value="ARCH_ID")
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        # svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
        #                 role_name="Team", create_archive_sheet=True)
        ...
    hunt = _committed_hunt(hdb.commit)
    assert hunt.google_sheet_id == "SHEET_ID"
    assert hunt.archive_google_sheet_id == "ARCH_ID"
    assert hunt.role_id == 333
```

### `tests/test_delete_hunt_service.py`

```python
"""Phase 5c: PuzzleService.delete_hunt — tear down a hunt (puzzles, rounds, sheets, DB row).

Asserts the stable teardown contract: spreadsheet FILE delete(s), the hunt DB delete, and
failed-channel isolation. Helpers are uniquely named and local to this file.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData


def _delete_guild():
    guild = MagicMock()
    guild.categories = []
    return guild


def _delete_service():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def _delete_hunt_obj():
    h = HuntData()
    h.id = 7
    h.name = "H"
    h.category_id = 50
    h.channel_id = 60
    h.google_sheet_id = "SHEET_ID"
    h.archive_google_sheet_id = "ARCH_ID"
    return h


def test_delete_hunt_deletes_db_row_and_spreadsheet_files():
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock()
    # The NEW whole-file delete method (added in Phase 3). Pin its real name once implemented.
    gsheet.delete_hunt_spreadsheet = AsyncMock()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = []
        # await svc.delete_hunt(guild, hunt)
        ...
    hdb.delete.assert_called_once_with(7)
    # Both the working and archive spreadsheet files are deleted (assert by file id).
    called_ids = [c.args[0] for c in gsheet.delete_hunt_spreadsheet.call_args_list]
    assert "SHEET_ID" in called_ids
    assert "ARCH_ID" in called_ids


def test_delete_hunt_returns_failed_channels():
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock(); gsheet.delete_hunt_spreadsheet = AsyncMock()
    one = MagicMock(); one.name = "p1"; one.channel_id = 1
    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=False)):
        pdb.get_all_from_hunt.return_value = [one]
        rdb.get_all.return_value = []
        # failed = await svc.delete_hunt(guild, hunt)
        ...
    # A puzzle whose channel could not be deleted surfaces in the returned list.
    # assert "p1" in failed
```

Commit both test files alone (no production code).

**Phase 1 acceptance:**

```
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py tests/test_delete_hunt_service.py -q > /tmp/p1.log 2>&1
```

Exit non-zero with the assertions failing because `create_hunt` / `delete_hunt` don't exist yet
(AttributeError once the `...` placeholders are replaced with the real calls). The files must
**collect cleanly** under the same runner the final gate uses (no import/bootstrap fatal). If a test
errors on anything other than the missing method, fix the test.

## Phase 2 — `create_hunt` service method + bang rewire (Sonnet 4.6)

1. In `bot/core/puzzle_service.py`, add `async def create_hunt(self, guild, *, name, url,
   start_timestamp, role_name=None, username=None, password=None, create_archive_sheet=False)`,
   modelled on `create_mini` (`:1029`). Steps: `HuntJsonDb.check_duplicates(name)` → signal
   duplicate (raise a named error or return a sentinel — match what the Phase 1 test pins); reuse-or-
   create the team role (`discord.utils.get(guild.roles, name=...)` else `guild.create_role`);
   create the hunt category + welcome text channel + solved category-or-divider; create the Google
   Sheet via `self._gsheet_cog(...)` (`create_hunt_spreadsheet`, and `create_hunt_archive_spreadsheet`
   when `create_archive_sheet`); build `HuntData(name, category_id, channel_id, guild_id, url, uid=
   HuntJsonDb.generate_uid('uid'))`; set `start_timestamp` AND mirror `start_time`; set
   `username`/`password`/`role_id`/`google_sheet_id`/`archive_google_sheet_id` when present;
   `HuntJsonDb.commit(new_hunt)`; return it. Read the cog constants the current create path uses
   (`SOLVE_CATEGORY`, `SOLVE_DIVIDER`, `GENERAL_CHANNEL_NAME`, `get_solved_puzzle_category`,
   `puzzles.py:444-456`) and reproduce that branch; lift module-level helpers (`clean_name`) rather
   than cog methods where one exists.
2. Rewire the cog `create_hunt` (`puzzles.py:426`): keep `check_available_space` and the welcome
   embed (`send_initial_hunt_channel_messages` + pin) in the cog; replace the provisioning/commit
   body with `new_hunt = await self.service.create_hunt(ctx.guild, name=hunt_name, url=hunt_url,
   role_name=role)`. The `!hunt` parse + duplicate message (`:415-422`) stay.

**Phase 2 acceptance:**

```
timeout 180 pipenv run python -m pytest tests/test_create_hunt_service.py tests/test_solve_equivalence.py tests/test_create_service.py -q > /tmp/p2.log 2>&1
```

Phase 1 create tests pass; the create/equivalence suites stay green.

## Phase 3 — `delete_hunt` service method + whole-file delete + bang rewire (Sonnet 4.6)

1. In `bot/cogs/puzzles_gsheet.py`, add `async def delete_hunt_spreadsheet(self, file_id)` that
   deletes the whole Drive file: `await execute_with_retry(get_drive().files().delete(fileId=file_id),
   on_retry=self._notify_retry)`. Tolerate an already-gone file (catch/log, never raise out).
2. In `bot/core/puzzle_service.py`, add `async def delete_hunt(self, guild, hunt) -> list[str]`,
   modelled on `delete_round` (`:811`): delete each puzzle from `PuzzleJsonDb.get_all_from_hunt(hunt.id)`
   (sheet tab + `PuzzleJsonDb.delete` + `_safe_delete_channel`, isolating failures into the returned
   list); delete each round from `RoundJsonDb.get_all(hunt.id)` (category + divider + `RoundJsonDb.delete`);
   delete the hunt's spreadsheet file(s) via the new method for `hunt.google_sheet_id` and, if set,
   `hunt.archive_google_sheet_id`; delete the hunt welcome channel + category; `HuntJsonDb.delete(hunt.id)`;
   return the failed-channel list.
3. Rewire the cog `delete_hunt` (`puzzles.py:2654`): keep `_confirm_destructive` and
   `_warn_failed_channels` in the cog; replace the teardown body with
   `failed = await self.service.delete_hunt(ctx.guild, self.get_hunt(ctx))`, then warn + clear cache.

**Phase 3 acceptance:**

```
timeout 180 pipenv run python -m pytest tests/test_delete_hunt_service.py tests/ -q > /tmp/p3.log 2>&1
```

Phase 1 delete tests pass; the full suite stays green.

## Phase 4 — `POST /api/hunts` + admin-guild source (Sonnet 4.6)

1. In `bot/web/actions.py`, add `POST /api/hunts`: `@login_required` → read `guild_id` from the JSON
   body → resolve `guild_snowflake` → `require_admin(guild_snowflake)`. Validate **mandatory** keys
   `{guild_id, name, url, start_timestamp}` (400 on missing); whitelist-check the optional keys
   `{username, password, role_name, create_archive_sheet}` (400 on any other key). Resolve the guild
   via `current_app.bot.get_guild(snowflake)` (503 if bot/guild unavailable). Call
   `cog.service.create_hunt(guild, **fields)`; map a duplicate to 409, a `discord` error to
   `_discord_error_response`. `_audit("create_hunt", hunt_uid=new.uid)`. Return the new hunt uid.
2. Add the admin-guild source for the picker: extend the guilds payload (`bot/web/data.py` /
   `bot/web/api.py`) with a per-guild `is_admin` flag (`user_can_manage(user_id, guild,
   roles=ADMIN_ROLES)`), or a small `GET /api/guilds?admin=1`. The frontend lists only admin guilds.

**Phase 4 acceptance:** `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/p4.log 2>&1` stays
green (endpoint wrappers untested by convention). Grep-confirm the mandatory/optional key checks,
`require_admin`, and the admin-guild source are wired.

## Phase 5 — `DELETE /api/hunts/<uid>` (Sonnet 4.6)

In `bot/web/actions.py`, add `DELETE /api/hunts/<uid>`, modelled on the `delete_round` endpoint:
`_visible_hunt_by_uid(uid)` → `require_admin(ctx["guild_snowflake"])` → resolve the `HuntData` via
`HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])` → `failed = await cog.service.delete_hunt(guild, hunt)`
→ `_audit("delete_hunt", hunt_uid=uid, detail=...)` → `{ "ok": True, "failed_channels": failed }`.
**No archived-lockout.** Map `discord` errors via `_discord_error_response`.

**Phase 5 acceptance:** `timeout 180 pipenv run python -m pytest tests/ -q` stays green; grep-confirm
the route, `require_admin`, and the absence of an `archived` 403 guard.

## Phase 6 — frontend New-hunt form (Sonnet 4.6)

Read the HuntList page, `api.ts`, `types.ts` first. Add a `useCreateHunt` mutation (`POST /api/hunts`,
invalidate `["hunts"]`). Add a **"New hunt"** button on HuntList, hidden when the user is admin in
zero guilds. Modal form: guild dropdown (admin guilds only), name, url (required), start datetime
(required), an **Advanced** collapsible (username, password, team-role name), a **create-archive-sheet**
checkbox. On success, navigate to the new hunt's board.

**Phase 6 acceptance:** `cd frontend && timeout 120 npx tsc --noEmit > /tmp/p6.log 2>&1` clean.

## Phase 7 — frontend Delete-hunt danger-zone (Sonnet 4.6)

In `AdminSettingsPanel` (admin-gated, reachable even when archived), add a **Delete hunt** danger-zone
using `TypedConfirmModal` with `confirmPhrase = hunt.name` and an impact summary (N puzzles, all
rounds, Discord channels, **Google spreadsheets — permanently deleted, irreversible**). Add a
`useDeleteHunt` mutation (`DELETE /api/hunts/<uid>`, invalidate `["hunts"]`). On success, navigate
back to HuntList.

**Phase 7 acceptance:** `cd frontend && timeout 120 npx tsc --noEmit` clean.

## Phase 8 — verification (Sonnet 4.6)

1. `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/p8.log 2>&1` — full suite green.
2. `cd frontend && timeout 120 npx tsc --noEmit` — clean.
3. `pipenv run ruff check` on changed backend files — clean.
4. Manual click-through: create a hunt from the web (confirm Discord category/channels + sheet
   provisioned, lands on the board), then delete it (confirm channels, categories, and spreadsheet
   files all removed). Confirm each persists across reload.
