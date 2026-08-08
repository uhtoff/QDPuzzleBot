# Current Spec

## Goal

Dashboard parity for the guild-scoped settings that today exist only as bang commands.
The project principle is that every bang command has a web equivalent; `!set_admin_roles` /
`!set_organiser_roles` / `!show_auth_roles` (added for M8) and `!update_settings` /
`!show_settings` have none, and there is no guild-scoped surface in the SPA at all to hang
them on.

User's decisions (2026-08-08): **auth roles + the other guild settings**, on a **new
`/guilds/:id/settings` route**.

## In scope

- `GET` / `PATCH` API for one guild's settings, admin-gated.
- A guild settings page in the SPA, reachable from an admin-only nav entry.
- Both tiers' role pickers, showing whether each tier is pinned by id or matched by name.
- The other `guilds` columns: `discord_bot_channel`, `drive_parent_id`,
  `drive_resources_id`, `discord_use_voice_channels`.

## Out of scope

- Any change to what the tiers *mean* or which routes sit at which tier (M8/M9 settled).
- Per-hunt settings (`AdminSettingsPanel` already covers those).
- `!show_settings`' puzzle/round/hunt-scoped JSON dump — that is a debugging command, not
  a settings surface.

## Relevant files

- `bot/web/actions.py` — `patch_hunt_settings` (:953) is the model; `_SETTINGS_KEYS` (:950)
- `bot/web/api.py` — `list_guilds` (:35), the only guild-scoped route today
- `bot/web/data.py` — `get_guilds_map` (:120)
- `bot/core/role_gate.py` — `tier_role_ids`, `tier_is_pinned`
- `bot/store/puzzle_settings.py` — `GuildSettings`
- `frontend/src/api.ts` — `useAdminGuilds` (:751)
- `frontend/src/App.tsx` — routes (:28-32)
- `frontend/src/components/AppShellLayout.tsx` — nav
- `frontend/src/components/AdminSettingsPanel.tsx` — the per-hunt panel, for shape/idiom

## Required behaviour

1. Both routes are **admin-gated** (`require_admin`), matching the bang commands.
2. **Every Discord id crosses the wire as a STRING.** Role and guild snowflakes are 64-bit
   and exceed JS's safe integer range; `list_guilds` already stringifies `guild_snowflake`
   and this must not regress that.
3. `GET` returns the guild's settings, the guild's available roles (id + name), each tier's
   pinned-or-by-name state with its effective role ids, and the caller's own role ids (the
   page needs them for the lockout warning).
4. `PATCH` whitelists its keys and rejects unknown ones with 400, as `patch_hunt_settings`
   does.
5. Role ids in a `PATCH` are validated against the guild's actual roles — a role id that is
   not in the guild is a 400, not silently stored.
6. **Lockout guard**, mirroring `!set_admin_roles`: setting a non-empty admin role list the
   caller does not hold is refused. An empty list is allowed (the documented revert-to-names
   escape hatch).
7. Saving is one atomic write of the settings row, not a field at a time.
8. The nav entry only appears for a user who administers at least one guild.

## Constraints

- No new dependency, frontend or backend.
- Follow the existing Mantine idiom in `AdminSettingsPanel`; no new design system.
- `GuildSettingsDb.commit` writes every column, so the PATCH must load-modify-save rather
  than construct a fresh row (it would blank the untouched columns).

## Acceptance criteria

- `timeout 400 pipenv run python -m pytest tests/ -q` green
- `timeout 60 pipenv run ruff check` clean
- `timeout 300 pipenv run basedpyright bot/` 0 errors
- `cd frontend && npm run build` clean (`tsc --noEmit` + vite build)
- Tests prove: the gate refuses a non-admin; the lockout guard refuses; a foreign role id is
  rejected; ids survive as strings; and an unrelated column is not blanked by a partial PATCH.

## Known limits

The React half has **no component or route test coverage** — the project's 3 vitest files
are all pure-logic (`frontend/src/lib/*`). The page is type-checked and inspected, not
behaviour-tested. Stated to the user before starting.

## Phases

1. `GET /api/guilds/<id>/settings`.
2. `PATCH /api/guilds/<id>/settings`, with validation + lockout guard.
3. `frontend/src/api.ts` types + hooks.
4. The page, route and nav entry.
5. Docs: commands.md parity column, user guide, playbook.
