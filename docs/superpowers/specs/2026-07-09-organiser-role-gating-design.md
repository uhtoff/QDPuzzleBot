# Organiser role gating bypass — design

## Problem

Organiser-tier permission checks (`ORGANISER_ROLES` in `bot/core/constants.py`) are
gated by role *name* — a guild must have a role literally called `Organisers` (or one
of the admin names) for anyone to run Organiser-level commands. Small guilds that never
set up an `Organisers` role end up with those commands unusable by anyone, including
regular users who should reasonably have access on a server with no formal org
structure.

## Goal

If a guild has no role named `Organisers`, Organiser-tier gating is bypassed and those
commands/actions become available to all members. Admin-tier gating (`Moderator` /
`mod` / `admin`) is unaffected — it always requires one of those roles to exist and be
held.

## Non-goals

- No bypass for the Admin tier, ever.
- No stored per-guild config/flag. The check is computed live from the guild's role
  list each time (see "Performance" below for why this is cheap).
- No change to what counts as "having" the Organisers role once it exists — a guild
  with an `Organisers` role but zero members assigned to it stays strictly gated (not
  in scope: treating an empty role as a missing one).

## Design

### 1. Shared predicate

A single helper function, used by both the Discord cog and the web layer:

```python
def organiser_gate_active(guild) -> bool:
    """False if the guild has no 'Organisers' role — Organiser-tier checks pass for everyone."""
    return any(r.name == 'Organisers' for r in guild.roles)
```

Location: `bot/core/constants.py`, alongside `ORGANISER_ROLES`/`ADMIN_ROLES` (or a new
`bot/core/checks.py` if that reads more naturally next to the cog's custom check —
implementer's call, no functional difference).

When `organiser_gate_active(guild)` is `False`, Organiser-tier checks pass
unconditionally. When `True`, fall through to the existing role-membership check
against `ORGANISER_ROLES`.

### 2. Discord cog (`bot/cogs/puzzles.py`)

Replace `@commands.has_any_role(*ORGANISER_ROLES)` with a new custom check,
`@is_organiser()`, defined via discord.py's `commands.check`:

```python
def is_organiser():
    async def predicate(ctx):
        if not organiser_gate_active(ctx.guild):
            return True
        if any(r.name in ORGANISER_ROLES for r in ctx.author.roles):
            return True
        raise commands.MissingAnyRole(list(ORGANISER_ROLES))
    return commands.check(predicate)
```

Apply `@is_organiser()` in place of `@commands.has_any_role(*ORGANISER_ROLES)` on all
Organiser-gated commands in `puzzles.py` (currently ~13 call sites, per the earlier
survey — exact count to be confirmed at implementation time as decorators are swapped).
Admin-gated commands (`@commands.has_any_role(*ADMIN_ROLES)`) are untouched.

### 3. Web layer (`bot/web/auth.py`, `bot/web/actions.py`)

`require_organiser(guild_snowflake, roles=ORGANISER_ROLES)` and
`user_can_manage(user_id, guild_snowflake, roles=ORGANISER_ROLES)` gain a pre-check:
if `'Organisers' in roles` (true only for the Organiser-tier call — `ADMIN_ROLES`
never contains this name, so `require_admin` is unaffected by construction) and the
guild has no such role, return "allowed" immediately without resolving the calling
user's roles at all.

`require_admin` (which calls through with `roles=ADMIN_ROLES`) is unaffected.

### 4. "View as regular" reflects the real bypass state

In `frontend/src/context/ViewAsContext.tsx`, `detail.hunt.can_manage` is the
Organiser-tier flag and `detail.hunt.is_admin` is the Admin-tier flag (there is no
separate `is_organiser` field). `applyViewAs` currently strips both unconditionally
when previewing as `"regular"`. Once the bypass ships, unconditionally stripping
`can_manage` would misrepresent guilds where the gate is inactive — a real regular
user on such a guild actually has Organiser powers, so hiding `can_manage` in the
preview would be inaccurate. `is_admin` stripping is correct as-is and stays
unconditional: the bypass never touches the Admin tier, so a real regular user never
has admin powers regardless of gate state.

- Backend: the handler in `bot/web/actions.py` that builds hunt-detail responses adds
  one boolean field, `hunt.organiser_gate_active`, computed via the same
  `organiser_gate_active(guild)` helper as parts 1–3. No new endpoint, no schema
  change, no migration — it rides along on the existing hunt-detail payload.
- Frontend: `applyViewAs` in `ViewAsContext.tsx` changes only the `"regular"` branch's
  handling of `can_manage`: strip it (force `false`) when
  `detail.hunt.organiser_gate_active` is `true` (existing behaviour), leave it as
  fetched when the gate is inactive (matching what a real regular user would actually
  see). `is_admin` stays forced `false` for `"regular"` in both cases, unchanged from
  today.
- `viewAs === "organiser"` is unaffected — that tier is never bypassed.

## Performance

Both the cog and web-side role lookups read discord.py's in-memory gateway cache
(`ctx.guild.roles`, `bot.get_guild(...)`, cached `member.roles`) — no live Discord HTTP
call in steady state. The added `organiser_gate_active` check is a linear scan over an
already-cached, small (guild role count) list. Negligible cost.

## Testing

- Cog: unit test `is_organiser()`'s predicate directly — guild with no `Organisers`
  role (any author) passes; guild with the role present requires the author to hold an
  `ORGANISER_ROLES` name; Admin-gated commands are unaffected by any of this.
- Web: unit tests for `require_organiser`/`user_can_manage` covering: guild missing
  `Organisers` role → allowed regardless of caller's roles; guild has the role →
  existing behaviour; `require_admin` unaffected in both cases.
- Frontend: test `applyViewAs` for `viewAs === "regular"` with
  `organiser_gate_active: true` (`can_manage` stripped, existing behaviour preserved)
  and `organiser_gate_active: false` (`can_manage` left as fetched); `is_admin` stays
  forced `false` in both cases.

## Files touched (expected)

- `bot/core/constants.py` (or new `bot/core/checks.py`) — `organiser_gate_active`
  helper, `is_organiser()` check.
- `bot/cogs/puzzles.py` — swap decorator on Organiser-gated commands.
- `bot/web/auth.py`, `bot/web/actions.py` — bypass pre-check in
  `require_organiser`/`user_can_manage`; add `organiser_gate_active` to hunt-detail
  response.
- `frontend/src/context/ViewAsContext.tsx` — conditional strip in `applyViewAs`.
