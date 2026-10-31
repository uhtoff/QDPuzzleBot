# Slice 5b — Team servers page and the hub's template link — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give hub admins a read-only dashboard page of every running and archived team hunt on their hub (who's on the team server now, who's invited but hasn't arrived, full access history), and store the hub's guild-template link with `!set_template_link` / `!show_template_link` and a Server settings field.

**Architecture:** Backend: a template-link validator in `bot/core/`, a `guilds.template_link` column, two bang commands in `bot/cogs/puzzles.py`, two admin-gated read routes in `bot/web/actions.py` built on a small pure module `bot/web/team_access.py` that assembles the payloads. Frontend: a new page `frontend/src/pages/TeamServers.tsx` with hooks in `api.ts`, pure formatting helpers in `frontend/src/lib/accessHistory.ts` (vitest), a link from Server settings, and a template-link field there.

**Tech Stack:** Python 3.12/3.13, Quart, discord.py 2.7, MariaDB; React + TypeScript + Mantine + react-query, vitest.

**Spec:** `docs/superpowers/specs/2026-10-31-team-servers-page-and-template-link-design.md`

## Global Constraints

- Tests: `timeout 180 /home/russ/projects/puzzbot/.venv/bin/python -m pytest tests/ -q > <file> 2>&1`; lint `.venv/bin/ruff check .`; frontend `cd frontend && timeout 120 npm test` and `timeout 180 npm run build` (tsc + vite).
- Worktree needs `config.json`, `google_secrets.json` copied in, and `frontend/node_modules` (run `npm ci` in the worktree's `frontend/`).
- **A push deploys.** Never push from a task.
- Migration `050_template_link.sql` (check 050 is free before merge).
- All Discord ids leave the API as **strings**.
- No invite URL or code (`hunt_invites.code`, `hunts.mod_invite_code`) in any API response.
- Both new routes are gated exactly like `GET /api/guilds/<id>/settings` (`_guild_settings_ctx`: Admin tier on that hub).
- A hunt belongs to hub H iff `H.get_role(hunt.roster_role_id)` is not None. A detail request for a hunt not on this hub → 404 `{"error": "hunt_not_found"}`.
- `!show_template_link` posts **only** in the hub admin channel via `hub_channel.post`; its reply never contains the link.
- Plain words for `via` (exact): `button` → "by the Join/Leave button", `arrival` → "on arrival", `hub_role_lost` → "lost the hub role", `hub_admin_lost` → "lost hub admin", `hand` → "by hand", `self` → "left on their own".
- Accepted template-link forms: `https://discord.new/<code>`, `discord.new/<code>`, `https://discord.com/template/<code>` (also `http://`, optional `www.` on discord.com); `<code>` is `[A-Za-z0-9]+`. Stored canonically as `https://discord.new/<code>`. Browser fallback shown as `https://discord.com/template/<code>`.

## Review Focus

1. A detail URL for another hub's hunt (right uid, wrong `guild_id`) → 404, no data (Task 3, `test_detail_refuses_a_hunt_from_another_hub`).
2. The bot has left a running hunt's team server → detail still returns invites and history, with `on_server: null` and `bot_on_server: false` (Task 3, `test_detail_when_the_bot_left_the_team_server`).
3. A template link with trailing junk or a different host (`https://evil.example/discord.new/abc`, `discord.new/abc?x=1`) is refused (Task 1, `test_rejects_lookalikes`).
4. History rows whose actor isn't visible to the bot → shown by id, no error (Task 3, `test_history_actor_not_on_hub_falls_back_to_id`).
5. A hub with no team hunts → page renders an empty state, list route returns `[]` (Task 3 `test_list_empty`; Task 4 empty-state text).

---

### Task 1: Template link — storage, validator, commands

**Files:**
- Create: `migrations/050_template_link.sql`, `bot/core/template_link.py`
- Modify: `bot/store/puzzle_settings.py` (field `template_link: str = ""`), `bot/store/mysqldb.py` (`MySQLGuildSettingsDb.commit` writes `template_link` in both the UPDATE and INSERT column lists), `bot/cogs/puzzles.py` (two commands, next to `!show_settings`), `tests/test_command_gating.py` (census `("set_template_link", "admin")`, `("show_template_link", "admin")`)
- Test: `tests/test_template_link.py`, `tests/test_template_link_live_db.py`

**Interfaces:**
- Produces:
  - `template_link.normalise(text: str) -> str | None` — canonical `https://discord.new/<code>` or None.
  - `template_link.code(link: str) -> str` — the code from a canonical link.
  - `template_link.browser_url(link: str) -> str` — `https://discord.com/template/<code>`.
  - `template_link.ACCEPTED_FORMS: str` — human-readable list for refusal messages.
  - `GuildSettings.template_link` persisted by `GuildSettingsDb.commit`.

- [ ] **Step 1: Migration** — header in the 048/049 style (PRE-FLIGHT: none, nullable additive column); `ALTER TABLE guilds ADD COLUMN template_link VARCHAR(255) NULL DEFAULT NULL`. Apply: `.venv/bin/python migrate.py`.
- [ ] **Step 2: Failing tests.**
  - `test_accepts_the_three_forms` (parametrize the forms from Global Constraints, incl. surrounding whitespace) → `https://discord.new/abc123`.
  - `test_rejects_lookalikes` — `https://evil.example/discord.new/abc`, `discord.new/abc?x=1`, `discord.new/`, `discord.gg/abc`, `""` → None.
  - `test_browser_url_and_code`.
  - `test_set_template_link_stores_canonical_and_replies_with_it`; `test_set_template_link_refuses_a_bad_link` (nothing committed; reply lists accepted forms); `test_set_template_link_with_no_argument_clears_it`.
  - `test_show_template_link_posts_link_and_fallback_in_the_admin_channel` — patch `bot.cogs.puzzles.hub_channel.find`/`post` (or the module the cog imports); post text has both URLs; the `ctx.send` reply contains `#<channel>` and **not** `discord.new`.
  - `test_show_template_link_refused_channel_posts_nothing` — `find` → `(None, "…visible to everyone…")` → no post; reply contains the reason.
  - `test_show_template_link_when_none_is_set` — reply says none set and how to set it; no post.
  - Live DB (`tests/test_template_link_live_db.py`, style of `tests/test_access_log_live_db.py`): commit a settings row with `template_link`, `get` returns it; commit with `""` round-trips.
- [ ] **Step 3: Run, expect FAIL.**
- [ ] **Step 4: Implement.** `!set_template_link [link]` — `@commands.guild_only()`, `@is_admin()`, bot-channel check; with a link: normalise or refuse; store via `GuildSettingsDb.get(ctx.guild.id)` → set → `commit`, and invalidate `cached_settings` the way `!update_settings` does; reply ":white_check_mark: Template link set: <canonical>" or ":white_check_mark: Template link cleared." `!show_template_link` — `@commands.guild_only()`, `@is_admin()` (no bot-channel check): `find(ctx.guild)`; refused → reply with reason; else `post(ctx.guild, f":hammer_and_wrench: Team-server template: {link}\nIf the Discord app ignores it, open {browser_url} in a browser.")` → reply ":white_check_mark: Posted in #<name>."
- [ ] **Step 5: Run, expect PASS;** full suite PASS.
- [ ] **Step 6: Commit** `feat(template-link): store the hub's team-server template link`.

### Task 2: Template link on Server settings (web)

**Files:**
- Modify: `bot/web/actions.py` (`_GUILD_SETTINGS_KEYS` gains `template_link`; PATCH validates it with `template_link.normalise`, `""` clears, a bad value → 400 `{"error": "bad_value", "field": "template_link"}`), `frontend/src/types.ts` (settings types), `frontend/src/pages/GuildSettings.tsx` (a "Team-server template link" `TextInput` with the accepted forms as description; shows the server's 400 as a field error)
- Test: `tests/test_guild_settings_web.py` (extend)

**Interfaces:**
- Consumes: Task 1 `template_link.normalise`.

- [ ] **Step 1: Failing tests** — `test_get_includes_template_link`; `test_patch_template_link_stores_canonical` (body `{"template_link": "discord.new/abc"}` → settings committed with `https://discord.new/abc`); `test_patch_template_link_rejects_bad` (400, nothing committed); `test_patch_template_link_empty_clears`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement backend + frontend field.**
- [ ] **Step 4: Run pytest file, expect PASS; `npm run build` PASS;** full suite PASS.
- [ ] **Step 5: Commit** `feat(template-link): edit it on Server settings`.

### Task 3: Team servers API

**Files:**
- Create: `bot/web/team_access.py`
- Modify: `bot/store/mysqldb.py` (`MySQLHuntJsonDb.team_hunts()` → `[(hunt, guild_snowflake)]` for every hunt with a non-zero `roster_role_id`, archived or not, `ORDER BY archived ASC, id DESC`; `MySQLHuntInviteDb.for_hunt(hunt_id) -> list[dict]` rows `user_id, created_at, joined_at` — **no code column selected**), `bot/web/actions.py` (two routes)
- Test: `tests/test_team_access.py` (pure assembly), `tests/test_team_servers_web.py` (routes, style of `tests/test_guild_settings_web.py`), `tests/test_team_access_live_db.py` (the two store methods)

**Interfaces:**
- Consumes: `HuntAccessLogDb.for_hunt(hunt_id)` (slice 5; rows oldest first), `role_panel.admin_roles(guild)`.
- Produces (JSON, ids as strings):
  - `GET /api/guilds/<guild_id>/team-hunts` → `{"hunts": [{"uid", "name", "team": <hub role name>, "archived": bool, "team_server": <guild name> | null, "on_server_count": int | null, "invited_waiting_count": int}]}`
  - `GET /api/guilds/<guild_id>/team-hunts/<uid>` → `{"hunt": {same fields}, "bot_on_server": bool, "team_role_missing": bool, "on_server": [{"user_id", "name", "team_role": bool, "mod": bool}] | null, "invited_waiting": [{"user_id", "name", "invited_at"}], "history": [{"at", "user_id", "name", "action", "via", "actor_id", "actor_name"}]}` — `history` newest first; `on_server` null for archived hunts or when the bot isn't on the team server; times ISO-8601 UTC with `Z`.
  - `team_access.hub_team_hunts(hub, rows) -> list[tuple[hunt, int]]` — filters `HuntJsonDb.team_hunts()` rows to this hub.
  - `team_access.members_now(team_guild, hunt) -> list[dict]` — non-bot members, sorted `mod` first, then team role, then neither, then by name.
  - `team_access.waiting(invite_rows, here_ids) -> list[dict]` — latest invite per user with `joined_at` null and not in `here_ids`.
  - `team_access.history(log_rows, name_of) -> list[dict]` — newest first; `actor_name = name_of(actor_id)` or the id string.

- [ ] **Step 1: Failing tests.**
  - Pure: `test_members_now_sorts_mod_then_team_then_neither_and_skips_bots`; `test_members_now_team_role_missing` (all `team_role` false); `test_waiting_excludes_arrived_and_present_and_dedupes`; `test_history_newest_first_with_actor_names`; `test_history_actor_not_on_hub_falls_back_to_id`; `test_hub_team_hunts_keeps_only_this_hubs`.
  - Routes: `test_list_running_and_archived` (running first; archived has `team_server` null, `on_server_count` null); `test_list_empty`; `test_list_requires_admin` (patched `require_admin` returning a 403 → 403); `test_detail_running`; `test_detail_archived_has_no_on_server`; `test_detail_refuses_a_hunt_from_another_hub`; `test_detail_when_the_bot_left_the_team_server`; `test_no_invite_codes_in_any_response` (seed codes `SECRETCODE` in invite rows and `mod_invite_code`; assert absent from both JSON bodies).
  - Live DB: `team_hunts` returns live + archived roster hunts and not a hub-hosted hunt; `HuntInviteDb.for_hunt` returns rows without a `code` key.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Routes resolve the hub via `_guild_settings_ctx`, find the hunt by `HuntJsonDb.get_by_attr(uid=uid)`, check membership with `hub.get_role`, look up the team guild with `bot.get_guild(snowflake)` for running hunts. `name_of` resolves an id via the hub's member cache then the team guild's, else returns the id string.
- [ ] **Step 4: Run, expect PASS;** full suite PASS.
- [ ] **Step 5: Commit** `feat(team-servers): admin API for a hub's team hunts and their access`.

### Task 4: Team servers page

**Files:**
- Create: `frontend/src/pages/TeamServers.tsx`, `frontend/src/lib/accessHistory.ts`, `frontend/src/lib/accessHistory.test.ts`
- Modify: `frontend/src/App.tsx` (route `/guilds/:guildId/teams`), `frontend/src/api.ts` (`useTeamHunts(guildId)`, `useTeamHunt(guildId, uid)`), `frontend/src/types.ts`, `frontend/src/pages/GuildSettings.tsx` (a "Team servers" link/button to `/guilds/<id>/teams`)

**Interfaces:**
- Consumes: Task 3 JSON.
- Produces: `accessHistory.viaLabel(via: string, actorName: string | null): string` (exact words from Global Constraints; `hand` with an actor → "by hand (by <actor>)", without → "by hand"); `accessHistory.actionLabel(action: string): string` — `grant` "got the team role", `revoke` "lost the team role", `elevate` "got mod", `demote` "lost mod", `arrive` "arrived", `depart` "left".

- [ ] **Step 1: Failing vitest tests:** every `via` value maps to its exact words; `hand` with and without actor; every `action`; an unknown value falls back to the raw string.
- [ ] **Step 2: `npm test`, expect FAIL.**
- [ ] **Step 3: Implement** the helpers and the page: Mantine, following `GuildSettings.tsx` (loading/error states, server picker if that page has one). A list of hunts (name, team, Running/Archived badge, counts) each expanding (Accordion) to three sections: "On the server now" (table: name, Team role ✓, Mod ✓; or "I'm not on that server" / omitted for archived; "This hunt's team role no longer exists" when `team_role_missing`), "Invited, not arrived" (name, invited at), "History" (time in local time, "<name> <actionLabel> <viaLabel>"). Empty state: "No team servers use this hub yet." Detail fetched on expand.
- [ ] **Step 4: `npm test` PASS; `npm run build` PASS.**
- [ ] **Step 5: Commit** `feat(team-servers): Team servers page on the dashboard`.

### Task 5: Docs

**Files:**
- Modify: `docs/commands.md` (`!set_template_link`, `!show_template_link`), `docs/new-server-playbook.md` (Team servers: store the template link once with `!set_template_link`, fetch it with `!show_template_link`; the Team servers page), `docs/superpowers/specs/2026-10-31-team-servers-page-and-template-link-design.md` (Status → implemented, this plan), `docs/user-guide.md` only if it documents admin pages (check first).

- [ ] **Step 1: Edit.** **Step 2:** `grep -n "template_link" docs/commands.md docs/new-server-playbook.md` shows both. **Step 3: Commit** `docs: slice 5b — Team servers page and template link`.

## Manual acceptance (after merge + a staging push, which deploys and migrates the test instance)

1. On the test hub: `!set_template_link <your template link>` → canonical link echoed; `!show_template_link` → link + fallback in `#bot-set-up`.
2. Server settings shows the link; edit it there; `!show_template_link` shows the new one.
3. Server settings → Team servers: the test team hunt is listed as Running with counts; expand → you, the throwaways, `mod` flags; the history from the earlier tests.
4. A throwaway with no role in `#lobby` appears under "neither".
5. As a non-admin account, `/guilds/<hub>/teams` is refused.
