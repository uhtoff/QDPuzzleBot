# Current Spec

## Goal

**M8** from `docs/reviews/2026-08-07-comprehensive-review.md`: authorisation is keyed on
Discord role *names* (`ADMIN_ROLES = ('Moderator', 'mod', 'admin')`, `ORGANISER_ROLE_NAMES`)
matched by exact string against `member.roles`. Anyone holding Discord's *Manage Roles*
permission can create a role named `mod`, self-assign it, and gain full dashboard admin
(delete_hunt, set_hunt_role, patch settings, create_hunt). Such names can also pre-exist in
a guild for unrelated reasons.

Move matching to per-guild role **ids**, with the current names used only to bootstrap the
first resolution. User's decision (2026-08-08): "Per-guild role IDs, names as bootstrap".

**M9** (organiser-tier destructive bypass) — user's decision: **document only**, no code
change. The bypass is deliberate for small single-team guilds; the review doc records the
grown gated set.

## In scope

- New per-guild storage for admin / organiser role ids.
- One resolver both edges (web + bang) go through.
- Bang commands to set and show the configured role ids.
- Bootstrap: a guild with nothing configured keeps working, matching by name exactly as today.

## Out of scope

- The dashboard equivalent of the set/show commands (project principle is bang/web parity —
  flagged to the user as a follow-up, not built here).
- M9 code changes.
- Any change to *which* routes sit at which tier (that is M10's question).
- The `organiser_gate_active` bypass semantics — preserved verbatim, just keyed on ids.

## Relevant files

- `bot/core/constants.py` — `ADMIN_ROLES`, `ORGANISER_ROLE_NAMES`, `ORGANISER_ROLES`,
  `organiser_gate_active(guild)`
- `bot/web/auth.py` — `resolve_role_names`, `organiser_tier_bypassed`, `user_can_manage`
- `bot/web/actions.py` — `require_organiser`, `require_admin`
- `bot/web/api.py` — `can_manage` / `can_administer` / `organiser_gate_active` payload
- `bot/cogs/puzzles.py` — `@commands.has_any_role(*ADMIN_ROLES)` (16 sites) + the
  organiser check at `:48-59`
- `bot/store/puzzle_settings.py` (`GuildSettings`), `bot/store/mysqldb.py`
  (`MySQLGuildSettingsDb`, table `guilds`)
- `migrations/039_*.sql` (039 confirmed free across all worktrees)

## Required behaviour

1. `guilds.admin_role_ids` / `guilds.organiser_role_ids` hold comma-separated Discord role
   snowflakes. NULL/empty means **not configured**.
2. Not configured → resolve the tier by name exactly as today (bootstrap, no behaviour
   change for an existing guild).
3. Configured → match on role id only. A role *renamed* to `admin`, or a new role *named*
   `admin`, grants nothing.
4. Both edges — web routes and bang commands — go through the same resolver. No path keeps
   its own name matching.
5. `organiser_gate_active` / the organiser-tier bypass keeps today's semantics; only the
   membership test changes from name to id.
6. Setting the ids is admin-gated and takes Discord role mentions.

## Constraints

- No change to the tier each route sits at.
- Bootstrap must be transparent: an un-configured guild sees no behaviour change at all.
- Fail closed on an unresolvable guild, as `api.py:117-119` already does.

## Acceptance criteria

- `timeout 400 pipenv run python -m pytest tests/ -q` green
- `timeout 60 pipenv run ruff check` clean
- `timeout 300 pipenv run basedpyright bot/` 0 errors
- A test proves a role merely *named* `admin` grants nothing once ids are configured, and
  that an unconfigured guild still matches by name.

## Phases

1. Storage: migration 039 + `GuildSettings` fields + DAO round-trip.
2. Resolver: one seam returning the effective role ids for a tier (configured, else names).
3. Web edge switched onto the resolver.
4. Bang edge switched onto the resolver.
5. `!set_admin_roles` / `!set_organiser_roles` / `!show_auth_roles`.
6. Docs: mark M8 fixed, record the M9 decision.
