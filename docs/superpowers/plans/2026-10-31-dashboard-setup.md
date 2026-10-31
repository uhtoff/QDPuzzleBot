# Slice 5c — dashboard setup and archive-to-hub from the dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a team server be set up from a dashboard page (hub and hub-role dropdowns, hunt fields, optional Send invites) running the same code as `!setup_server`, link it from the template greeting, and make the dashboard archive team hunts to the hub.

**Architecture:** Two shared seams first — `server_setup.perform_setup(...)` (the whole `!setup_server` flow) and a shared archive-to-hub runner — so bang and web call one implementation. Then the setup API (`GET`/`POST /api/guilds/<id>/setup`), the archive-to-hub web route, the React setup page and archive dialog, and the greeting link.

**Tech Stack:** Python 3.12/3.13, Quart, discord.py 2.7, MariaDB; React + TypeScript + Mantine + react-query, vitest.

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-setup-design.md` (read "Settled after the builder check").

## Global Constraints

- Tests: `timeout 180 /home/russ/projects/puzzbot/.venv/bin/python -m pytest tests/ -q > <file> 2>&1`; `.venv/bin/ruff check .`; `cd frontend && timeout 120 npm test && timeout 180 npm run build`.
- Worktree `/home/russ/projects/puzzbot/.claude/worktrees/dashboard-setup`, branch `dashboard-setup`; config files and `frontend/node_modules` already present. Never push.
- No migration expected.
- **One implementation:** `!setup_server` and `POST /api/guilds/<id>/setup` both call `perform_setup`; `!archive_hunt_to_hub` and `POST /api/hunts/<uid>/archive-to-hub` both call the shared archive runner. User-visible messages from the command are preserved.
- Setup routes' gate: the logged-in user is the team server's **owner** (`guild.owner_id`) **or** holds its Admin tier — not `require_admin`. Hub-admin checks use `hunt_archive.resolve_hub` / `hub_member` + `role_gate.member_holds_tier`; its `HubUnavailable` messages pass through as `{"error": "hub_unavailable", "message": ...}` (403).
- Mutating routes call `_audit` and are listed in `tests/test_web_route_gating.py`; new SPA routes are added to `bot/web/__init__.py` `_CLIENT_ROUTES` (guarded by `tests/test_spa_client_routes.py`).
- Ids leave the API as strings.
- Send invites at setup is **off by default** and runs only after a setup that completed without error.
- Archive-to-hub needs `confirm_name` equal to the hunt name exactly; it runs as a background task and returns 202; progress/summary go to the hub (admin channel if usable, else the hub archive channel) — never only to the team server, whose channels the archive deletes.

## Review Focus

1. A viewer who owns the team server but holds no tier there can use the setup page; a non-owner non-admin cannot (Task 3, `test_owner_without_mod_can_set_up`, `test_stranger_refused`).
2. A setup POST that stops partway returns the error and steps; a second POST finishes it, as re-running the command does (Task 3, `test_retry_finishes_a_partial_setup`).
3. `send_invites: true` with a setup that errored sends nothing (Task 3, `test_send_invites_skipped_when_setup_errored`).
4. Two archive-to-hub POSTs for the same hunt → the second gets 409 `archive_running` (Task 2, `test_second_archive_while_running_is_refused`).
5. The in-place archive route refuses a running team hunt but still unarchives (Task 2, `test_in_place_archive_refuses_team_hunt`, `test_unarchive_unchanged`).

---

### Task 1: Shared setup seam

**Files:**
- Modify: `bot/core/server_setup.py` (split `preflight`; add `perform_setup` and `SetupOutcome`), `bot/cogs/puzzles.py` (`setup_server` becomes a thin caller)
- Test: `tests/test_server_setup_perform.py`; existing `tests/test_setup_server_command.py`, `tests/test_server_setup_*.py` keep passing (re-point patch targets only where the code moved; state per changed test what moved)

**Interfaces:**
- Produces:
  - `server_setup.template_problems(guild, settings) -> list[str]` — the hub-free checks from `preflight` (Administrator, @everyone view, one `mod`, bot above `mod`, #lobby/#bot-commands present) plus "already set up on <date>" when `settings.setup_complete_time` is set; same wording as today's `SetupRefused` messages. `preflight` calls it first and raises `SetupRefused(problems[0])` if non-empty (behaviour unchanged).
  - `@dataclass SetupOutcome: lines: list[str]; ok: bool; hunt: HuntData | None; error: str` — `lines` is exactly what `!setup_server` sends today (steps, the ✅/❌ lines, the mod-invite line, the "Next:" line).
  - `async server_setup.perform_setup(bot, service, guild, invoker, hub_id, roster_role_id, hunt_name, hunt_url, *, start_timestamp=0, username="", password="", create_solved_sheet=False) -> SetupOutcome` — everything `setup_server` does after argument parsing: settings-row check, run-once check, owner-or-admin check (invoker is a `discord.Member` of `guild`), `resolve_hub`, `preflight`, the "⏳ Setting up…" line is NOT included (the command sends it itself), `run_setup` (extended to pass the new hunt fields to `service.create_hunt`), the hunt welcome via `DiscordNotifier`, `mod_invite.post`. Refusals become `SetupOutcome(ok=False, error=<the command's text without ":x: ">, lines=[":x: <text>"])`.

- [ ] **Step 1: Failing tests** — `test_template_problems_lists_each_problem` (parametrize: no Administrator; @everyone sees channels; two `mod` roles; bot below `mod`; missing #lobby; already set up); `test_template_problems_empty_for_a_good_clone`; `test_perform_setup_passes_hunt_fields_to_create_hunt` (start_timestamp/username/password/create_solved_sheet reach `service.create_hunt`); `test_perform_setup_refuses_a_non_owner_non_admin` (same text as the command); `test_setup_server_command_sends_perform_setup_lines` (command sends "⏳ …" then the outcome's lines joined).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — move code, don't rewrite it; `run_setup` gains the four keyword args with today's defaults.
- [ ] **Step 4: Full suite PASS** (existing setup tests unchanged in what they assert).
- [ ] **Step 5: Commit** `refactor(setup): one perform_setup for !setup_server and the dashboard`.

### Task 2: Shared archive runner and the web archive-to-hub

**Files:**
- Create: `bot/core/hub_archive_run.py` (or a function in `hunt_archive.py` — builder's call; name it in the report)
- Modify: `bot/cogs/puzzles.py` (`archive_hunt_to_hub` calls the runner after confirming), `bot/web/actions.py` (new route; `post_hunt_archive` refuses team hunts), `tests/test_web_route_gating.py`
- Test: `tests/test_hub_archive_run.py`, `tests/test_archive_to_hub_web.py`; existing `tests/test_archive_hunt_to_hub.py`, `tests/test_lock_ordering.py` keep passing

**Interfaces:**
- Produces:
  - `async run_archive_to_hub(bot, service, guild, hunt, invoker, hub, hub_row, *, status_channel) -> HuntArchiveReport` — takes `puzzles_lock(member puzzle ids)`, posts "⏳ Archiving…" in `status_channel` and edits it with progress, calls `service.archive_hunt_to_hub`, builds the summary exactly as the command does (`_archive_summary` + the left/failures lines — move `_archive_summary` if needed), posts it to the hub archive channel and (if different) `status_channel`; never raises past a logged error.
  - `RUNNING_HUB_ARCHIVES: set[int]` (hunt ids) maintained by the web path.
  - `POST /api/hunts/<uid>/archive-to-hub` body `{"confirm_name": str}` → checks in order: Admin tier on the hunt's server (`require_admin`), hunt not archived, not forum, has `roster_role_id`, roster role findable (else `409 {"error": "hub_role_missing"}`), `confirm_name == hunt.name` (else `400 confirm_mismatch`), not already running (`409 archive_running`), `resolve_hub(bot, roster.guild.id, guild, invoker)` with invoker = the team server member for the logged-in user (`HubUnavailable` → 403 `hub_unavailable` + message). Then picks `status_channel` = `hub_channel.find(hub)[0]` or None (the runner then uses the hub archive channel), starts `asyncio.create_task(...)` that removes the id from `RUNNING_HUB_ARCHIVES` in `finally`, audits, returns `202 {"started": true, "progress_channel": <name or null>}`.
  - `POST /api/hunts/<uid>/archive` with archived true on a hunt with `roster_role_id` and not archived → `409 {"error": "use_hub_archive"}`; unarchive unchanged.

- [ ] **Step 1: Failing tests** — runner: `test_runner_locks_posts_progress_and_summary`, `test_runner_falls_back_to_the_hub_archive_channel_for_status`; web: `test_archive_to_hub_starts_and_returns_202`, `test_confirm_mismatch_refused`, `test_non_team_hunt_refused`, `test_hub_unavailable_message_passes_through`, `test_second_archive_while_running_is_refused`, `test_in_place_archive_refuses_team_hunt`, `test_unarchive_unchanged`; command: existing tests still pass and one new `test_command_uses_the_shared_runner`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** (move the command's locked block into the runner unchanged; the command passes `status_channel=ctx.channel`).
- [ ] **Step 4: Full suite PASS.**
- [ ] **Step 5: Commit** `feat(archive): archive team hunts to the hub from the dashboard`.

### Task 3: Setup API

**Files:**
- Modify: `bot/web/actions.py` (two routes + a `_team_setup_ctx` gate helper), `tests/test_web_route_gating.py`
- Test: `tests/test_setup_web.py`

**Interfaces:**
- Consumes: Task 1 `template_problems`, `perform_setup`; `team_invites.send_invites`, `team_invites.report_text`; `hunt_archive.hub_member`; `role_gate.member_holds_tier`, `role_gate.tier_role_ids`; `HuntJsonDb.live_team_hunt_for_guild`, `HuntJsonDb.get_by_attr`, `GuildSettingsDb.get`.
- Produces:
  - `GET /api/guilds/<guild_id>/setup` → `{"template_problems": [str], "already_set_up": iso|null, "existing": {"hub_id", "roster_role_id", "hunt_name", "hunt_url"}|null, "hubs": [{"id", "name", "roles": [{"id", "name"}]}]}`. Hubs per spec "Settled" item 3; roles exclude `@everyone`, managed roles and the hub's Admin-tier roles, sorted by position descending. `existing` from this server's one hunt if present (hub_id from its roster role's guild when findable, else null).
  - `POST /api/guilds/<guild_id>/setup` body `{hub_id, roster_role_id, hunt_name, hunt_url, start_timestamp?, username?, password?, create_solved_sheet?, send_invites?}` (ids as strings or ints; validate types like `create_hunt` does; unknown keys → 400 `bad_keys`) → `perform_setup(...)`; if `outcome.ok` and `send_invites` → `report_text(await send_invites(bot, guild, outcome.hunt))`. Returns `200 {"ok", "lines", "error", "hunt_uid", "invites"}` (a refusal or stopped setup is still 200 with `ok: false` — it is a normal outcome the page shows; gate failures are 403).
  - Gate: logged-in user id == `guild.owner_id`, or the member holds the Admin tier on this guild; not a member → 403 `not_a_member`.

- [ ] **Step 1: Failing tests** — `test_owner_without_mod_can_set_up`; `test_stranger_refused`; `test_get_lists_only_hubs_where_viewer_is_admin` (three other guilds: admin hub ✓, non-admin hub ✗, a team server ✗); `test_get_roles_exclude_everyone_managed_and_admin_roles`; `test_get_reports_template_problems`; `test_get_prefills_existing`; `test_post_runs_perform_setup_with_fields`; `test_retry_finishes_a_partial_setup`; `test_send_invites_after_success`; `test_send_invites_skipped_when_setup_errored`; `test_send_invites_off_by_default`; `test_bad_keys_refused`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite PASS.**
- [ ] **Step 5: Commit** `feat(setup): dashboard setup API for team servers`.

### Task 4: Setup page and archive dialog

**Files:**
- Create: `frontend/src/pages/ServerSetup.tsx`, `frontend/src/lib/setupForm.ts` (+ `.test.ts`)
- Modify: `frontend/src/App.tsx` (route `/guilds/:guildId/setup`), `bot/web/__init__.py` (`_CLIENT_ROUTES` += `guilds/\d+/setup/?`), `frontend/src/api.ts`, `frontend/src/types.ts`, `frontend/src/components/AdminSettingsPanel.tsx` (team-hunt archive dialog), `frontend/src/pages/HuntList.tsx` only if extracting shared time helpers from its create-hunt form

**Interfaces:**
- Consumes: Task 3 JSON; Task 2 routes.
- Produces: `setupForm.buildPayload(form) -> SetupPayload` (trims strings, omits empty optionals, converts start time + offset with the same helper HuntList's create form uses, `send_invites` false unless set) and `setupForm.canSubmit(form, status) -> boolean` (false while `template_problems` is non-empty, or hub/role/name/url missing).

- [ ] **Step 1: Failing vitest** for `buildPayload` (omits blanks; start time converted; send_invites defaults false) and `canSubmit` (each blocking condition).
- [ ] **Step 2: `npm test` FAIL.**
- [ ] **Step 3: Implement** the page per the spec's Form and Result sections (status box first; Hub → Hub role selects; name; website; Advanced: start time + timezone, shared username/password, solved-puzzle sheet switch; "Send invites when setup finishes" switch, off, description "Leave off to look the server over first; run !send_invites later"; result lines, error in red with "Fix that and press Set up again", success with invites report and links to the hunt board and Team servers page). Archive dialog: for a hunt with `roster_role_id` (expose it in the hunt detail JSON if not already — check `hunt_to_json`), the Archive button opens a Modal explaining it (moves history to the hub, the bot leaves the server, not reversible) with a TextInput for the hunt name; on 202 show "Archiving started — progress and the result will appear in #<progress_channel> on the hub." (or "…in the hub's archive channel" when null); errors shown with their message.
- [ ] **Step 4: `npm test` + `npm run build` PASS; full pytest PASS** (SPA route guard).
- [ ] **Step 5: Commit** `feat(setup): dashboard setup page and team-hunt archive dialog`.

### Task 5: Greeting link and docs

**Files:**
- Modify: `bot/cogs/settings.py` (`TEMPLATE_SETUP_MESSAGE` becomes a function of the guild row id; when `config.web_frontend_url` is set it leads with "**Set me up on the dashboard:** <url>/guilds/<row id>/setup" and keeps the command form below as "Or, here in #bot-commands, run: …"), `tests/test_template_greeting.py` (extend: link present when configured, absent when not; existing step-order test still passes), `docs/commands.md`, `docs/new-server-playbook.md` (Team servers: the dashboard setup path first, command as alternative; archive from the dashboard), spec Status → implemented.

- [ ] **Step 1: Failing tests** for the greeting link.
- [ ] **Step 2: Implement + docs.**
- [ ] **Step 3: Full suite PASS.**
- [ ] **Step 4: Commit** `feat(setup): the template greeting links to dashboard setup; docs`.

## Manual acceptance (after merge + staging push)

1. Clone the template, invite the test bot → the greeting in #bot-commands links to the setup page.
2. Open it as the group account (owner, no `mod`) → status box; before dragging the bot role, Set up is disabled with the drag instruction.
3. Pick the hub and role, fill name/website/start time/login, solved sheet on, Send invites off → Set up → steps shown; hunt board has the start time and login; solved sheet exists.
4. `!send_invites` still works; on a second fresh server, run the page with Send invites on → invites DM'd, report on the page.
5. Dashboard → that hunt's panel → Archive → dialog needs the name → progress and summary in `#bot-set-up` on the hub; the hunt shows as Archived on Team servers.
6. `!setup_server` on a third clone still works end to end.
