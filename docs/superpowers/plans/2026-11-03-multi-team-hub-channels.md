# Multi-team Hub Channels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Team servers set up for the same hunt can be grouped into a multi-team hunt on the hub, which gets a no-spoiler channel (all its teams + hub mods) and a finishers channel (completed teams only), condensed into an open archive when the last team archives.

**Architecture:** Two new tables (`multi_team_hunts`, `multi_team_members`) behind one store class. One ctx-free core module `bot/core/multi_team.py` owns every Discord side effect (find-or-create channels by topic marker, overwrites, 🏁 line, condensing). Setup, the mark-complete commands/routes and the archive-to-hub runner call into it; nothing else touches the hub channels.

**Tech Stack:** Python 3.12, discord.py, Quart, MySQL/MariaDB (CI MySQL 8.0), React + Mantine + TanStack Query, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-11-03-multi-team-hub-channels-design.md`

## Global Constraints

- Work on branch `staging`. Never push; the user approves each push.
- Migration is `migrations/053_multi_team_hunts.sql`; plain MySQL 8 syntax (no `ADD COLUMN IF NOT EXISTS`, no MariaDB-only features). `hunts.id` is `int(11)`; FK columns must match exactly.
- User-facing text never says "shared". The setup question is exactly **"Are we fielding more than one team?"**; lists show the hunt's own name plus a team count, e.g. `Mystery Hunt 2027 (3 teams)`.
- Every bot post uses `allowed_mentions=discord.AllowedMentions.none()` — nothing pings.
- 🏁 line: `🏁 **{team}** has finished!` (team = the hub role's name).
- Archive channel name: `{created_at:%Y-%m} {name} - all teams`; threads `No spoilers` and `Finishers`.
- Live channel topic markers: `puzzbot-multi-team:{id}:no-spoilers` and `puzzbot-multi-team:{id}:finishers`; archive channel marker `puzzbot-multi-team-archive:{id}`.
- Overwrites (both live channels): `@everyone` → `view_channel=False`. No-spoiler: each team's hub role (`hunts.roster_role_id`) and each hub Admin-tier role (`role_gate.tier_role_ids(hub, ADMIN_ROLES)`) → `view_channel=True, send_messages=True, read_message_history=True`. Finishers: only completed teams' hub roles, same three. Channels are created **uncategorised** (no category sync can ever leak finishers).
- Tests: `timeout 600 .venv/bin/python -m pytest <target> -q > <file> 2>&1`; lint `.venv/bin/ruff check bot tests`; types `~/.local/bin/basedpyright --pythonpath .venv/bin/python bot/`; frontend `cd frontend && npx tsc --noEmit && npx vitest run`.
- Dual entrypoints: every action has a bang command and a dashboard route calling the same core function.
- Commit trailers per the session's attribution lines.

## Review Focus

1. Finishers must never be visible to an unfinished team: re-running `ensure_channels` on an existing finishers channel must not add any non-completed team (test asserts overwrites exactly).
2. Two setups racing to create the same new multi-team hunt must not create two records or four channels (module-level `asyncio.Lock`; test with `asyncio.gather`).
3. A failure inside the multi-team step must never fail or roll back the team's own archive-to-hub (test makes `multi_team` raise and asserts `run_archive_to_hub`'s outcome is unchanged). In setup it stops the run like any other step, so a re-run finishes it.
4. Mark complete on an archived hunt, an unlinked hunt, or a hunt with no roster role is refused with a clear message, never a silent no-op that records state.
5. A channel deleted by hand is recreated by the next `ensure_channels`, with the correct overwrites for the CURRENT members and completions (not just the role that triggered it).

---

### Task 1: Schema and store

**Files:**
- Create: `migrations/053_multi_team_hunts.sql`
- Modify: `bot/store/mysqldb.py` (new `MySQLMultiTeamDb`), `bot/store/__init__.py` (export `MultiTeamDb`)
- Test: `tests/test_multi_team_live_db.py` (LIVE `puzzlebot_test`, raw inserts torn down in `finally`, as `tests/test_access_log_live_db.py`)

**Interfaces — Produces:**
- Tables:
  - `multi_team_hunts`: `id INT AUTO_INCREMENT PK`, `hub_guild_id BIGINT(20) UNSIGNED NOT NULL` (Discord snowflake), `name VARCHAR(255) NOT NULL`, `no_spoiler_channel_id BIGINT(20) UNSIGNED NULL`, `finishers_channel_id BIGINT(20) UNSIGNED NULL`, `created_at DATETIME NOT NULL`, `archived_time DATETIME NULL`, `KEY (hub_guild_id, archived_time)`.
  - `multi_team_members`: `hunt_id INT(11) PK` (FK → `hunts.id` ON DELETE CASCADE), `multi_team_id INT NOT NULL` (FK → `multi_team_hunts.id` ON DELETE CASCADE), `completed_time DATETIME NULL`.
- `MultiTeamDb` methods (rows are plain dicts; datetimes naive UTC as other tables):
  - `create(hub_guild_id: int, name: str) -> int`
  - `get(mt_id: int) -> dict | None`
  - `running_for_hub(hub_guild_id: int) -> list[dict]` — `archived_time IS NULL`, each with an added `team_count` (members whose hunt is not archived), ordered by name.
  - `running_by_name(hub_guild_id: int, name: str) -> dict | None` — case-insensitive, running only.
  - `set_channels(mt_id, no_spoiler_channel_id, finishers_channel_id) -> None`
  - `mark_archived(mt_id) -> None`
  - `link(hunt_id: int, mt_id: int) -> None` — INSERT … ON DUPLICATE KEY UPDATE is NOT wanted: re-linking to a different multi-team hunt raises `ValueError`; same id is a no-op.
  - `for_hunt(hunt_id: int) -> dict | None` — the member row joined with its multi-team row (`multi_team_id`, `completed_time`, `name`, `hub_guild_id`, channel ids, `archived_time`).
  - `members(mt_id: int) -> list[dict]` — `hunt_id`, `completed_time`, `roster_role_id`, `archived` (from `hunts`).
  - `set_completed(hunt_id: int, completed: bool) -> bool` — returns True only when the state changed (sets `UTC_TIMESTAMP()` / NULL).

- [ ] **Step 1:** Write `test_multi_team_storage_against_the_real_schema` covering, with TWO hunts and TWO multi-team hunts on one hub (so mapping/ordering mistakes show): create; `running_for_hub` order and `team_count` (archive one hunt → count drops); `running_by_name` case-insensitivity and that an archived multi-team hunt is not returned; `link` idempotent for the same id and `ValueError` for a different id; `for_hunt` fields; `set_completed` returns True, then False on repeat, True on undo; deleting a hunt cascades its member row.
- [ ] **Step 2:** Run it — Expected: FAIL (`ImportError: MultiTeamDb`).
- [ ] **Step 3:** Write the migration (header comment in the 049 style: purpose, PRE-FLIGHT none — new tables only) and the store class.
- [ ] **Step 4:** Apply: `.venv/bin/python migrate.py` (test DB, sanctioned). Run the test — Expected: PASS.
- [ ] **Step 5:** Commit `feat(multi-team): tables and store`.

### Task 2: Core — channels, create, join

**Files:**
- Create: `bot/core/multi_team.py`
- Modify: `bot/core/hunt_archive.py` — extract `ensure_tagged_channel(hub, marker, name)` from `ensure_hub_archive_channel` (which then calls it unchanged in behaviour).
- Test: `tests/test_multi_team_core.py` (fake hub: `MagicMock` guild whose `text_channels`, `get_channel`, `get_role`, `default_role`, `create_text_channel` are set explicitly; `role_gate.tier_role_ids` patched)

**Interfaces — Consumes:** `MultiTeamDb` (Task 1). **Produces:**
- `class MultiTeamRefused(Exception)` — message shown to the user as-is.
- `def no_spoiler_marker(mt_id) -> str`, `def finishers_marker(mt_id) -> str`, `def archive_marker(mt_id) -> str` (values in Global Constraints).
- `async def ensure_channels(hub, mt_id) -> tuple[TextChannel, TextChannel]` — find each by marker (else create uncategorised, named `{slug}-no-spoilers` / `{slug}-finishers` where slug is `discord_channels` naming of the multi-team name), then **set the full overwrite set from the DB** (members' roster roles + admin roles on no-spoiler; completed members' roster roles on finishers; `@everyone` deny on both), store ids with `set_channels`. Idempotent.
- `async def find_or_create(hub, hunt_name) -> tuple[int, bool]` — under module `_LOCK`: `running_by_name(hub.id, hunt_name)` or `create(hub.id, hunt_name)`; returns `(mt_id, created)`. The multi-team hunt's name IS the hunt name; there is no second name anywhere. Linking is done by the caller (Task 4) once the hunt exists, then `ensure_channels` applies the new team's role — order in setup is find_or_create → link → ensure.

- [ ] **Step 1:** Failing tests: (a) first `ensure_channels` creates both with exactly the overwrites above for 2 member roles + 1 admin role, finishers has only the `@everyone` deny; (b) second call finds them by marker and creates nothing; (c) a member completed → finishers gains exactly that role and nobody else; (d) a hand-deleted finishers channel is recreated with current overwrites; (e) two concurrent `find_or_create(hub, "Big Hunt")` → one `MultiTeamDb.create`, both return the same id, one with `created=True`; (f) `find_or_create(hub, "big hunt")` after `"Big Hunt"` exists returns it (case-insensitive).
- [ ] **Step 2:** Run — FAIL (module missing). **Step 3:** Implement. **Step 4:** PASS; also run `tests/ -k hub_archive` to show the extraction changed nothing. **Step 5:** Commit `feat(multi-team): hub channels with explicit overwrites`.

### Task 3: Core — mark complete / not complete

**Files:** Modify `bot/core/multi_team.py`; Test `tests/test_multi_team_complete.py`

**Interfaces — Produces:**
- `@dataclass class CompleteOutcome: changed: bool; message: str`
- `async def set_complete(bot, hunt, complete: bool) -> CompleteOutcome` — hub = `role_panel.find_roster_role(bot, hunt.roster_role_id).guild`. Refuses (`MultiTeamRefused`) when: hunt not linked ("**X** isn't part of a multi-team hunt"), no roster role, roster role not found, hunt archived (`complete=False` only; archiving already completed it), or multi-team hunt archived. On a change: `set_completed`, `ensure_channels`, and when `complete` post the 🏁 line in finishers. No post on undo or on a no-change call. Messages: `":white_check_mark: **{team}** marked complete — they can now see #{finishers}."`, `":white_check_mark: **{team}** marked not complete."`, `"**{team}** is already complete."` / `"… isn't marked complete."`.

- [ ] **Step 1:** Failing tests: complete posts the 🏁 line once with no-mentions and adds the overwrite; repeat is `changed=False` with no post; undo removes the overwrite with no post; re-complete posts again; each refusal case raises with its message and writes nothing (`set_completed` not called).
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat(multi-team): mark complete`.

### Task 4: Setup — the question, both entrypoints

**Files:**
- Modify: `bot/core/server_setup.py` (`perform_setup`/`_perform_setup`/`run_setup` gain `multi_team=None`), `bot/cogs/puzzles.py` (`setup_server` tail parsing + docstring), `bot/web/actions.py` (`get_guild_setup`, `post_guild_setup`, `_SETUP_OPTIONAL`)
- Test: `tests/test_multi_team_setup.py`, extend `tests/test_setup_server*.py` / web setup tests where they pin `_SETUP_OPTIONAL` or the GET shape

**Interfaces — Produces:**
- `def parse_setup_tail(text: str) -> tuple[str, str, bool]` in `server_setup.py` — splits trailing comma clauses in any order: `files` → sheet_mode; `multi-team` → `multi_team=True`. Returns `(hunt_name, sheet_mode, multi_team: bool)`. Hunt names containing commas are unaffected unless a clause matches exactly (case-insensitive).
- `multi_team: bool = False` param. A re-run on a hunt already linked ignores it and reuses the link (step line "Already part of multi-team hunt **X**.").
- In `run_setup`, after the roster step and before the welcome: create-or-join, `MultiTeamDb.link(hunt.id, mt_id)`, `ensure_channels`, step line `Joined multi-team hunt **{name}** — now {n} teams.` / `Started multi-team hunt **{name}** on the hub (#… and #…).` (the wording makes an accidental second hunt from a typo obvious) A failure in this step is an ordinary setup failure: it lands in `report.error`, the run-once lock stays unwritten, and re-running setup finishes it (the spec's "partial run finishes the link on re-run"). Link before ensuring channels, so a re-run sees the link and only re-ensures.
- GET `/guilds/<id>/setup`: each `hubs[]` entry gains `"multi_team_hunts": [{"id", "name", "team_count"}]`; `existing` gains `"multi_team": {"id", "name"} | null`.
- POST body optional key `"multi_team"`: a bool; anything else → 400 `bad_value`/`multi_team`.

- [ ] **Step 1:** Failing tests: `parse_setup_tail` table (plain name; `, files`; `, multi-team`; both clauses either order; a name with an inner comma); setup with multi_team joins a running one of the same name (case-insensitive) and reports the team count; with none running it creates one; a multi-team failure leaves `ok=False`, no lock, and a re-run completes the link and channels; re-run reuses an existing link; POST validation 400s; GET shape.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat(multi-team): choose at setup (command and dashboard)`.

### Task 5: Mark complete — commands, routes, payloads

**Files:** Modify `bot/cogs/puzzles.py`, `bot/web/actions.py`, `bot/web/api.py`; Test `tests/test_multi_team_entrypoints.py`

**Interfaces — Produces:**
- Bang (team server, main Hunt channel, `@is_admin()` — the team server's `mod` tier): `!hunt_complete` and `!hunt_not_complete` → `multi_team.set_complete`, reply with the outcome message or `:x: <refusal>`.
- `POST /api/hunts/<uid>/complete` body `{"complete": bool}` — `require_admin` on the hunt's guild (team-server mods). `POST /api/guilds/<hub_id>/team-hunts/<uid>/complete` same body — `_guild_settings_ctx` (hub admins). Both: 200 `{ok, changed, message}`; refusal 409 `{error: "refused", message}`; audit `hunt_complete`.
- Payloads: hunt payload (`api.py`) gains `"multi_team": {"name", "completed": bool} | null`; `_team_hunt_out` gains `"multi_team": {"id", "name", "completed": bool, "archived": bool} | null`.

- [ ] Steps: failing tests for both routes (auth refused for non-admin; body validation; refusal → 409 with message; success calls `set_complete` with the right bool) and the two commands (wrong channel refused; message relayed) → implement → PASS → commit `feat(multi-team): mark complete from Discord and the dashboard`.

### Task 6: End of hunt — auto-archive and Archive now

**Files:** Modify `bot/core/multi_team.py`, `bot/core/hub_archive_run.py`, `bot/cogs/puzzles.py`, `bot/web/actions.py`; Test `tests/test_multi_team_archive.py`

**Interfaces — Produces:**
- `async def archive_multi_team(bot, hub, mt_id) -> str` — dest = `hunt_archive.ensure_tagged_channel(hub, archive_marker(mt_id), name)`; webhook via `hunt_archive.ensure_webhook`; each live channel present (by marker) → `archive_and_delete_channel(..., thread_name="No spoilers"|"Finishers")`; a missing one is skipped; on no exception `mark_archived`. Returns a summary line; raises nothing (errors logged and returned as a `:x:` line).
- `async def on_team_archived(bot, hunt) -> None` — called by `run_archive_to_hub` **after** a report with no failures: if linked, `set_complete(True)` (swallowing `MultiTeamRefused`), then if every member's hunt is archived (or none remain), `archive_multi_team` and post its summary with `hub_channel.post(hub, …)`. Wrapped so nothing propagates into `run_archive_to_hub`.
- Archive now: bang `!archive_multi_team <name>` on the hub (bot channel, `@is_admin()`), and `POST /api/guilds/<hub_id>/multi-team/<mt_id>/archive` (`_guild_settings_ctx`). Archive now runs whether or not member hunts are still live (it is also the retry and the "last team deleted" path); the dashboard asks for the multi-team hunt's name to confirm first.

- [ ] Steps: failing tests — last team archived → both channels condensed into one archive channel with two threads and `mark_archived`; not-last team → only marked complete, no archive; a failure in `on_team_archived` leaves `run_archive_to_hub`'s return value and posts unchanged; a hand-deleted live channel is skipped; Archive now route/command auth + call → implement → PASS → commit `feat(multi-team): condense the hub channels when the hunt is over`.

### Task 7: Dashboard UI

**Files:** Modify `frontend/src/lib/setupForm.ts` (+ test), `frontend/src/pages/ServerSetup.tsx`, `frontend/src/pages/TeamServers.tsx`, `frontend/src/components/AdminSettingsPanel.tsx`, `frontend/src/api.ts`, `frontend/src/types.ts`

**Interfaces — Consumes:** payloads/routes from Tasks 4–6.
- `SetupForm` gains `multiTeam: boolean`. `buildPayload` adds `multi_team: true` only when set; `canSubmit` unchanged (hunt name already required).
- Setup page: a Yes/No `SegmentedControl` labelled "Are we fielding more than one team?"; Yes shows the chosen hub's running multi-team hunts as `Name (N teams)` buttons/select that FILL the hunt name field, plus a line "or a new one called <hunt name>" reflecting what will happen with the name as typed (a case-insensitive match shows "joins <Name> (N teams)").
- Team servers page: rows grouped under their multi-team hunt's name; per running team a Mark complete / Mark not complete button; per multi-team hunt an "Archive now" button behind a type-the-name confirm (as the existing archive confirm).
- Hunt Admin settings: for a hunt with `multi_team`, a "Finished the hunt" switch calling `/api/hunts/<uid>/complete`.

- [ ] Steps: failing vitest cases for `buildPayload` (multi_team present only when Yes) and a pure helper `multiTeamMatch(huntName, running) -> {id,name,team_count} | null` (case-insensitive, trimmed) → implement → `npx tsc --noEmit && npx vitest run` PASS → commit `feat(dashboard): multi-team setup, mark complete, archive now`.

### Task 8: Docs and full verification

**Files:** `docs/commands.md` (new commands, setup tail clauses), `docs/user-guide.md` (Team servers section), `docs/new-server-playbook.md` (multi-team steps), `docs/PLANS.md` (link the spec).

- [ ] Run full suite, ruff, basedpyright, frontend checks; all green. Commit `docs: multi-team hunts`.
- [ ] Manual acceptance list from the spec, on the test bot after the user approves a push.
