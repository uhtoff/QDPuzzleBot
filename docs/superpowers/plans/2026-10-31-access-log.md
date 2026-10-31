# Slice 5 — access log, hub admin channel, mod invites — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every change to a team server's team role and `mod` in `hunt_access_log`, give hub mods a reusable way onto every team server, warn at startup about broken team servers — all surfaced through a quiet hub admin channel.

**Architecture:** Four small new modules under `bot/core/` — `hub_channel` (find and safely post to the hub's command channel), `access_notes` (the bot's in-memory "I'm about to change this" notes), `access_log` (turn member events into rows and quiet-channel lines), `mod_invite` (the reusable invite) — plus `startup_check`. Existing role-changing paths (`role_panel`, `team_invites`) gain one `access_notes.expect(...)` call before each change. The `TeamServers` cog routes the events.

**Tech Stack:** Python 3.12/3.13, discord.py 2.7, MariaDB via `bot/store/mysqldb.py`, pytest + pytest-asyncio (auto mode), unittest.mock.

**Spec:** `docs/superpowers/specs/2026-10-31-access-log-and-hub-admin-channel-design.md` (parent: `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`).

## Global Constraints

- Tests: `timeout 180 /home/russ/projects/puzzbot/.venv/bin/python -m pytest tests/ -q > <file> 2>&1` (pipenv is broken in worktrees; the `.venv` is 3.13). Lint: `.venv/bin/ruff check .`.
- A worktree needs `config.json` and `google_secrets.json` copied from the main checkout before tests run.
- **A push deploys** (`deploy.sh`: `pipenv sync` → `migrate.py` → restart). Never push from a task; the controller pushes after the final review, with the user's go-ahead.
- Migration → deploy → code. The new migration is `049_hunt_access_log.sql` (check no other worktree has taken 049 before merging).
- Listener code never lets an exception escape into discord.py (pattern: `bot/cogs/team_servers.py`).
- No team-server invite link is ever posted anywhere but the hub admin channel (after its safety check) or a DM.
- `hunt_access_log.action` values exactly: `grant`, `revoke`, `elevate`, `demote`, `arrive`, `depart`. `via` values exactly: `button`, `arrival`, `hub_role_lost`, `hub_admin_lost`, `hand`, `self`.
- Quiet list — posted to the hub admin channel: rows with `via` in (`hand`, `hub_role_lost`, `hub_admin_lost`); every `elevate`/`demote` regardless of `via` **except** `via='button'`; bot failures (couldn't DM a late joiner, couldn't remove someone); mod invites; startup warnings. Never posted: `via` in (`button`, `arrival`, `self`) for grant/revoke/arrive/depart.
- Message style follows slice 4 (`:warning:`, `:door:`, `:information_source:`, names in **bold**, the team server named by its guild name).

## Review Focus

1. The hub's `discord_bot_channel` is empty, misspelt, or the channel was deleted → nothing posted, a warning logged, no exception (Task 2, `test_unset_or_missing_channel_posts_nothing`).
2. The bot can't read the audit log for a hand change (`discord.Forbidden`) → the row is still written with `actor_id=None` (Task 3, `test_hand_change_with_unreadable_audit_log_still_writes_row`).
3. A role change on a guild that is not a live team server (the hub itself, an archived hunt's server) → no rows, no posts (Task 3, `test_update_on_a_non_team_guild_is_ignored`).
4. One update carrying both the team role and `mod` (Join by a hub admin) → two rows, both `via='button'`, nothing posted (Task 4, `test_hub_admin_join_writes_grant_and_elevate_quietly`).
5. The stored mod invite was deleted by hand in Discord → `!mod_invite` makes a fresh one and stores its code (Task 5, `test_a_vanished_invite_is_replaced_and_stored`).

---

### Task 1: Migration and storage

**Files:**
- Create: `migrations/049_hunt_access_log.sql`
- Modify: `bot/store/mysqldb.py` (new `MySQLHuntAccessLogDb`; two methods on `MySQLHuntJsonDb`)
- Modify: `bot/store/__init__.py` (export `HuntAccessLogDb`, constructed like `HuntInviteDb` at line ~148)
- Test: `tests/test_access_log_live_db.py`

**Interfaces:**
- Produces:
  - `HuntAccessLogDb.record(hunt_id: int, user_id: int, username: str, action: str, via: str, actor_id: int | None = None, detail: str | None = None) -> None`
  - `HuntAccessLogDb.for_hunt(hunt_id: int) -> list[dict]` — rows oldest first (for tests now, slice 5b later).
  - `HuntJsonDb.set_mod_invite_code(hunt_id: int, code: str | None) -> None` and `HuntJsonDb.mod_invite_code(hunt_id: int) -> str | None` — own SQL; `HuntData` gets **no** field (so `commit()` never clears it), exactly like `invites_sent_time`.

- [ ] **Step 1: Write the migration.** Header comment in the style of `048_team_invites.sql` (purpose, PRE-FLIGHT: none needed — new table, nullable additive column). Table `hunt_access_log`: `id` BIGINT AUTO_INCREMENT PK; `created_at` DATETIME NOT NULL; `hunt_id` INT(11) NOT NULL (**no FK** — the record outlives a deleted hunt; index it); `user_id` BIGINT(20) UNSIGNED NOT NULL; `username` VARCHAR(255) NULL; `action` VARCHAR(16) NOT NULL; `via` VARCHAR(16) NOT NULL; `actor_id` BIGINT(20) UNSIGNED NULL; `detail` VARCHAR(255) NULL; index `(hunt_id, created_at)`. Then `ALTER TABLE hunts ADD COLUMN mod_invite_code VARCHAR(32) NULL DEFAULT NULL`.
- [ ] **Step 2: Apply it locally:** `.venv/bin/python migrate.py` → `Applying 049_hunt_access_log.sql ... done.` (this is the shared `puzzlebot_test`; additive, so the running test bot is unaffected).
- [ ] **Step 3: Write the failing live-DB test** `test_access_log_and_mod_invite_storage_against_the_real_schema`, following `tests/test_team_invites_live_db.py` (raw inserts of a guild + hunt, `finally` teardown that also deletes the log rows by `hunt_id`):
  - two `record` calls (`grant`/`arrival`, then `elevate`/`hand` with `actor_id=777`) → `for_hunt` returns both, oldest first, with those `action`/`via`/`actor_id` values;
  - deleting the hunt leaves its log rows in place (assert count before cleaning them up);
  - `mod_invite_code` is `None`, then `"abc"` after `set_mod_invite_code(hunt, "abc")`, then `None` after `set_mod_invite_code(hunt, None)`;
  - `HuntJsonDb.commit(HuntJsonDb.get_by_attr(id=hunt))` after setting `"abc"` leaves it `"abc"`.
- [ ] **Step 4: Run it, expect FAIL** (`AttributeError` / no such method).
- [ ] **Step 5: Implement** the store methods (timestamps as UTC-naive `now`, as `MySQLHuntInviteDb.record` does).
- [ ] **Step 6: Run the test file, expect PASS;** run the full suite, expect all pass.
- [ ] **Step 7: Commit** `feat(access-log): hunt_access_log table and mod invite code storage`.

### Task 2: Hub admin channel and the bot's change notes

**Files:**
- Create: `bot/core/hub_channel.py`, `bot/core/access_notes.py`
- Test: `tests/test_hub_channel.py`, `tests/test_access_notes.py`

**Interfaces:**
- Produces:
  - `hub_channel.find(hub: discord.Guild) -> tuple[discord.TextChannel | None, str]` — the channel named by `GuildSettingsDb.get(hub.id).discord_bot_channel`, or `None` and the reason (no settings / setting empty / no such channel / `"#<name> is visible to everyone on <hub>"`). Visible means `channel.permissions_for(hub.default_role).view_channel` is true.
  - `async hub_channel.post(hub: discord.Guild | None, text: str) -> bool` — sends if `find` gives a channel; otherwise logs a warning with the reason and returns False. `hub=None` → False. `discord.HTTPException` on send → logged, False. Never raises.
  - `access_notes.expect(guild_id: int, user_id: int, key: int | str, via: str) -> None` — `key` is a role id, or the string `"kick"`.
  - `access_notes.take(guild_id: int, user_id: int, key: int | str) -> str | None` — returns and removes the `via`; `None` if absent or older than `TTL_SECONDS = 60`.
  - Module-level clock seam `access_notes._now = time.monotonic` so tests can move time.

- [ ] **Step 1: Write failing tests.**
  - `test_posts_in_the_hubs_command_channel` — settings name `bot-set-up`, channel hidden from `@everyone` → `send` awaited with the text, returns True.
  - `test_unset_or_missing_channel_posts_nothing` — parametrize: no settings row, empty setting, setting naming a channel that doesn't exist → no send, returns False, no exception.
  - `test_a_channel_everyone_can_see_is_refused` — `view_channel` True for default role → no send; `find` reason contains `"visible to everyone"`.
  - `test_a_failed_send_returns_false` — send raises `discord.Forbidden` → False.
  - `test_take_returns_the_note_once`, `test_notes_are_per_guild_user_and_key` (a note for role 1 isn't taken by role 2, or another user, or another guild), `test_a_note_expires_after_a_minute` (patch `_now`: 61 s later → `None`), `test_kick_key` (`"kick"` works as a key).
- [ ] **Step 2: Run, expect FAIL** (modules missing).
- [ ] **Step 3: Implement both modules.** Notes: a dict `{(guild_id, user_id, key): (via, stamped_at)}`; prune expired entries on each `expect`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(access-log): hub admin channel lookup and bot change notes`.

### Task 3: The access log listener

**Files:**
- Create: `bot/core/access_log.py`
- Modify: `bot/cogs/team_servers.py` (route `on_member_update`, `on_member_join`, `on_member_remove`)
- Test: `tests/test_access_log.py`

**Interfaces:**
- Consumes: Task 1 `HuntAccessLogDb.record`; Task 2 `hub_channel.post`, `access_notes.take`; existing `HuntJsonDb.live_team_hunt_for_guild(guild.id)`, `role_panel.admin_roles(guild)`, `role_panel.find_roster_role(bot, id)`.
- Produces:
  - `async access_log.on_roles_changed(bot, before: discord.Member, after: discord.Member) -> None`
  - `async access_log.on_arrived(bot, member) -> None` — `arrive` row, `via='arrival'`; never posted.
  - `async access_log.on_departed(bot, member) -> None` — `depart` row, plus `revoke`/`demote` rows for the team role / `mod` the member still held.
  - `access_log.ACTIONS`, `access_log.VIAS` — the exact tuples from Global Constraints.

Rules (from the spec): do nothing unless `live_team_hunt_for_guild(guild.id)` returns a hunt. For each of the hunt's team role (`hunt.role_id`) and the guild's admin roles that was added or removed, `via = access_notes.take(guild.id, member.id, role.id)`; if `None`, it is `'hand'` and the actor is looked up in `guild.audit_logs(action=discord.AuditLogAction.member_role_update, limit=10)` for an entry whose `target.id` is the member and whose change includes that role, created in the last 30 s; one retry after `asyncio.sleep(2)` if not found; `discord.Forbidden`/`HTTPException` → `actor_id=None`. For departures, `via = take(..., "kick")`; if `None`, look for a recent `AuditLogAction.kick` of that member → `'hand'` with actor, else `'self'`. Hub for posting: `find_roster_role(bot, hunt.roster_role_id).guild` (None → don't post). Post per the quiet list; a line names the person, the role, the team server and, for `hand`, the actor (`<@id>` mention is fine inside the admin channel).

- [ ] **Step 1: Write failing tests** (a `World` fixture in the style of `tests/test_team_invites_leaving.py`; patch `bot.core.access_log.HuntAccessLogDb`, `...HuntJsonDb`, `...hub_channel.post`, and `bot.core.role_gate.tier_role_ids`; audit-log iteration via an async-generator fake):
  - `test_bot_noted_grant_is_logged_quietly` — note `(g, u, TEAM_ROLE) → 'arrival'`, team role added → one `record(..., 'grant', 'arrival', None, ...)`, `post` not awaited, `audit_logs` never called.
  - `test_hand_grant_of_mod_is_logged_with_actor_and_posted` — no note, `mod` added, audit entry by user 777 → `record(..., 'elevate', 'hand', 777, ...)`, `post` awaited once with text containing the member's name and `mod`.
  - `test_hand_change_with_unreadable_audit_log_still_writes_row` — `audit_logs` raises `discord.Forbidden` → row with `actor_id=None`, still posted.
  - `test_missing_audit_entry_is_retried_once` — first iteration empty, second has the entry → actor filled; patch `asyncio.sleep` in `bot.core.access_log`.
  - `test_update_on_a_non_team_guild_is_ignored` — `live_team_hunt_for_guild` returns None → no `record`, no `post`.
  - `test_unrelated_role_changes_are_ignored` — some other role added → nothing.
  - `test_hub_role_lost_removal_is_posted` — note `'kick'` → `'hub_role_lost'`; `on_departed` for a member holding team role + `mod` → rows `depart`, `revoke`, `demote` all `via='hub_role_lost'`; `post` awaited once.
  - `test_leaving_by_themselves_is_quiet` — no note, no kick entry → `depart` via `'self'`, no post.
  - `test_kick_by_hand_is_posted_with_actor` — no note, kick entry by 777 → `depart` via `'hand'`, actor 777, posted.
  - `test_arrival_is_logged_quietly` — `on_arrived` → `arrive`/`arrival` row, no post.
  - `test_the_cog_routes_member_events_and_swallows_errors` — `TeamServers.on_member_update/on_member_join/on_member_remove` each call the `access_log` function (patched to raise) and don't raise; `on_member_update` still calls the slice 4 handlers.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement `access_log.py` and the cog routing.** The cog's `on_member_update` calls `access_log.on_roles_changed` whenever any role changed, in its own try/except, alongside the existing added/removed calls; add `on_member_remove`; `on_member_join` calls `access_log.on_arrived` after `team_invites.on_join` (separate try/except).
- [ ] **Step 4: Run the file, expect PASS;** full suite PASS.
- [ ] **Step 5: Commit** `feat(access-log): record team role and mod changes from member events`.

### Task 4: The bot announces its own changes

**Files:**
- Modify: `bot/core/role_panel.py` (`join`, `leave`), `bot/core/team_invites.py` (`on_join`, `_drop_mod`, `_remove_from_team`, `_tell_mods`, `on_hub_roles_added` failure lines)
- Test: `tests/test_access_log_notes_wired.py`

**Interfaces:**
- Consumes: Task 2 `access_notes.expect`, `hub_channel.post`; Task 3 `access_log.on_roles_changed` (for the end-to-end tests).
- Produces: no new public names. `_tell_mods(guild, text, hub=None)` — when `hub` is given, the same text also goes to `hub_channel.post(hub, ...)`.

Notes to add, immediately before the Discord call: `join` → each role in `to_add`, `'button'`; `leave` → each in `to_remove`, `'button'`; `on_join` → team role, `'arrival'`; `_drop_mod` → each mod role, `'hub_admin_lost'`; `_remove_from_team` → `"kick"` before `kick`, or each stripped role before the owner's `remove_roles`, `'hub_role_lost'`. Failure lines that also go to the hub (pass `hub=roster.guild` / `member.guild`): late-joiner couldn't-DM and couldn't-create-invite in `on_hub_roles_added`; couldn't-remove and owner-can't-be-removed in `_remove_from_team`; couldn't-take-mod in `_drop_mod`. The success lines in `_remove_from_team` / `_drop_mod` stay team-server-only — the hub gets those from Task 3's listener.

- [ ] **Step 1: Write failing tests** — each drives the real function with mocks, captures the note made, then feeds a matching before/after member pair to `access_log.on_roles_changed` (or `on_departed`) and asserts the row and that nothing/something was posted:
  - `test_hub_admin_join_writes_grant_and_elevate_quietly` — `role_panel.join` for a hub admin → rows `grant/button` and `elevate/button`, `post` not awaited.
  - `test_leave_is_quiet` — `leave` → `revoke/button` (+ `demote/button`), no post.
  - `test_arrival_grant_is_quiet` — `team_invites.on_join` → `grant/arrival`, no post.
  - `test_drop_mod_is_logged_as_hub_admin_lost_and_posted` — `_drop_mod` → `demote/hub_admin_lost`, posted once by the listener.
  - `test_kick_note_makes_departure_hub_role_lost` — `_remove_from_team` → `depart/hub_role_lost`.
  - `test_failures_also_reach_the_hub` — late joiner with closed DMs → `hub_channel.post` awaited with the couldn't-DM text, and the team server's `#bot-commands` still gets it.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Add the notes and the `hub=` passes.** Existing slice 3/4 tests must still pass unchanged.
- [ ] **Step 4: Run new file + `tests/test_role_panel*.py tests/test_team_invites*.py`, expect PASS;** full suite PASS.
- [ ] **Step 5: Commit** `feat(access-log): bot role changes are noted, failures reach the hub`.

### Task 5: The mod invite

**Files:**
- Create: `bot/core/mod_invite.py`
- Modify: `bot/cogs/puzzles.py` (`setup_server` completion; new `!mod_invite`), `bot/core/puzzle_service.py` (`archive_hunt_to_hub`, before `guild.leave()`), `tests/test_command_gating.py` (census `("mod_invite", "admin")`)
- Test: `tests/test_mod_invite.py`

**Interfaces:**
- Consumes: Task 1 `HuntJsonDb.mod_invite_code` / `set_mod_invite_code`; Task 2 `hub_channel.post`, `hub_channel.find`; existing `team_invites.InviteUnavailable`, `server_setup.LOBBY_CHANNEL`, `HuntJsonDb.live_team_hunts()`.
- Produces:
  - `async mod_invite.ensure(guild, hunt) -> discord.Invite` — the stored invite if `guild.invites()` still has that code, else a new `#lobby` invite (`max_uses=0, max_age=0, unique=True, reason=f"mod invite: {hunt.name}"`), stored with `set_mod_invite_code`. No `#lobby`, or create fails → `InviteUnavailable` (same wording as `team_invites.make_invite`).
  - `async mod_invite.post(bot, guild, hunt) -> str` — checks `hub_channel.find(hub)` first (no invite made if the channel is refused), then `ensure`, then posts `":key: Mod way into **{guild.name}** ({team role name}) for **{hunt.name}**: {url} — click **Join** in #lobby when you arrive."`. Returns a short reply for the caller (posted / refused with reason).
  - `async mod_invite.delete(guild, hunt) -> None` — deletes the stored invite if present, clears the code; never raises.
  - `!mod_invite <hunt name>` — Admin tier (`@is_admin()`), guild-only, bot-channel check; run on the **hub**: candidates are `live_team_hunts()` whose `ctx.guild.get_role(hunt.roster_role_id)` is not None and whose name matches case-insensitively. None → `:x: No running team hunt named **X** on this hub.`; two or more → refuse naming each team server; one → `mod_invite.post` with `bot.get_guild(snowflake)` (bot not there → say so).

- [ ] **Step 1: Write failing tests:**
  - `test_ensure_makes_a_reusable_lobby_invite_and_stores_it` — kwargs `max_uses=0, max_age=0`; `set_mod_invite_code(hunt.id, code)` called.
  - `test_ensure_reuses_a_live_stored_invite` — stored code present in `guild.invites()` → no `create_invite`.
  - `test_a_vanished_invite_is_replaced_and_stored` — stored code absent → new invite, new code stored.
  - `test_no_lobby_raises_invite_unavailable`.
  - `test_post_refuses_a_visible_channel_without_making_an_invite` — `find` returns `(None, reason)` → no `create_invite`, reply contains the reason.
  - `test_post_puts_the_link_in_the_admin_channel` — text contains the URL, the hunt name and `Join`.
  - `test_delete_removes_and_clears` and `test_delete_with_nothing_stored_is_a_no_op`.
  - `test_setup_server_completion_posts_the_mod_invite` — patch `mod_invite.post`; a completed run awaits it once; an errored run doesn't.
  - `test_mod_invite_command_finds_the_hunt_on_this_hub_only` — one hunt whose roster role is on `ctx.guild`, one on another hub with the same name → posts for the first only.
  - `test_mod_invite_command_refuses_an_ambiguous_name`.
  - `test_archive_to_hub_deletes_the_mod_invite_before_leaving` — in the existing `archive_hunt_to_hub` service tests' style: `mod_invite.delete` awaited before `guild.leave`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `mod_invite.py`, the `setup_server` hook (after the success lines are built; a failure to post adds a line to the reply but never fails setup), the command, the archive call, the census entry. Add the step to `!setup_server`'s completion text: "A mod invite for hub admins is in the hub's #<channel>." only when posted.
- [ ] **Step 4: Run, expect PASS;** full suite PASS (incl. `tests/test_command_gating.py`).
- [ ] **Step 5: Commit** `feat(access-log): reusable mod invite in the hub admin channel`.

### Task 6: Startup warning

**Files:**
- Create: `bot/core/startup_check.py`
- Modify: `bot/cogs/team_servers.py` (`on_ready` → run once per process)
- Test: `tests/test_startup_check.py`

**Interfaces:**
- Consumes: `HuntJsonDb.live_team_hunts()`, `role_panel.find_roster_role`, `role_panel.admin_roles`, Task 2 `hub_channel.post`.
- Produces: `startup_check.problems(bot) -> dict[discord.Guild | None, list[str]]` (hub → problem lines; `None` collects hunts whose hub can't be found) and `async startup_check.run(bot) -> None` (one post per hub; the `None` bucket only logged).

Problems per live team hunt: `bot.get_guild(snowflake)` is None → "I'm no longer on **{hunt.name}**'s team server"; `guild.me.guild_permissions.administrator` false → "I've lost Administrator on **{guild.name}**"; `guild.me.top_role` not above every admin role → "My role is below `mod` on **{guild.name}** — drag it above".

- [ ] **Step 1: Write failing tests:** `test_all_well_posts_nothing`; `test_a_missing_team_server_is_reported_to_its_hub`; `test_lost_administrator_and_low_role_are_both_listed_in_one_message` (one `post`, both lines); `test_problems_on_two_hubs_post_twice`; `test_an_unfindable_hub_is_only_logged` (no `post`); `test_the_cog_runs_the_check_once` (two `on_ready` calls → one `run`; errors swallowed).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement;** the cog keeps a `_checked` flag.
- [ ] **Step 4: Run, expect PASS;** full suite PASS.
- [ ] **Step 5: Commit** `feat(access-log): warn at startup about broken team servers`.

### Task 7: Docs

**Files:**
- Modify: `docs/commands.md` (`!mod_invite`), `docs/new-server-playbook.md` (Team servers: the hub command channel must be admin-only; the mod invite; where warnings go), parent spec (dated *Changed* note under *Access audit trail* pointing to the slice 5 design; acceptance 4 now met by `hunt_access_log` + quiet channel, mirror superseded), slice 5 design `Status:` → implemented with this plan's name.

- [ ] **Step 1: Edit the four docs.**
- [ ] **Step 2: `grep -n "mod_invite" docs/commands.md docs/new-server-playbook.md`** shows both.
- [ ] **Step 3: Commit** `docs: slice 5 — access log, hub admin channel, mod invite`.

## Manual acceptance (after merge and a staging push, which deploys and migrates the test instance)

1. On the test hub, confirm `#bot-set-up` is hidden from `@everyone`.
2. Fresh template server → `!setup_server` → a `:key:` mod invite appears in `#bot-set-up`.
3. From a hub-admin account not on the team server, follow it, click **Join** → team role + `mod`; nothing posted in `#bot-set-up`.
4. `!send_invites`; a throwaway arrives → nothing posted; `SELECT action, via FROM hunt_access_log WHERE hunt_id=…` shows `arrive`/`arrival` and `grant`/`arrival`.
5. Give a throwaway `mod` by hand on the team server → a line in `#bot-set-up` naming you as the giver; `elevate`/`hand` row with your id.
6. Take the hub role off a throwaway → removed; line in `#bot-set-up`; `depart`/`revoke` rows `hub_role_lost`.
7. `!mod_invite <hunt name>` on the hub → the same link re-posted.
8. Push/restart with nothing wrong → no warning. (Optional: remove the bot's Administrator on the team server, restart → one warning.)
9. `!archive_hunt_to_hub` on a throwaway hunt → the mod invite is gone from the team server's invite list.
