"""Which Discord role ids grant an authorisation tier, per guild (M8).

Authorisation used to be keyed on role *names*: ADMIN_ROLES = ('Moderator',
'mod', 'admin') matched by exact string against `member.roles`. Anyone holding
Discord's *Manage Roles* permission could create a role called `mod`, self-assign
it and hold the dashboard's admin tier; the names can also pre-exist in a guild
for entirely unrelated reasons.

Both edges — the web routes (bot/web/auth.py, bot/web/actions.py) and the bang
commands (bot/cogs/puzzles.py) — now ask this module instead, so neither keeps a
name comparison of its own.

A guild stores its role ids on `guilds.admin_role_ids` / `organiser_role_ids`
(migration 039). Empty/NULL means NOT CONFIGURED and the tier resolves by name,
exactly as before — the bootstrap state every guild starts in, so nothing changes
until someone runs !set_admin_roles. Once ids are stored they are the ONLY thing
that grants the tier: a guild that configured ids and stored nothing but junk
gates everyone out rather than falling back to names, because falling back there
would re-open the very hole this closes.

The DB import is function-local: bot.core.constants and the cog import this at
module scope, and the DB layer must not be pulled in at import time.
"""

from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLE_NAMES


def _stored_ids(guild_id, column):
    """Parsed ids from one settings column, or None if the guild has not
    configured that tier. `frozenset()` and None are different answers:
    empty-but-configured gates everyone out, unconfigured bootstraps by name."""
    from bot.store import GuildSettingsDb

    settings = GuildSettingsDb.get_cached(guild_id)
    raw = getattr(settings, column, None) if settings is not None else None
    if not raw:
        return None
    ids = set()
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            # A malformed entry gates nothing; the well-formed ones still do.
            continue
    return frozenset(ids)


def _names_to_ids(guild, names):
    """Bootstrap: the ids of the guild's roles whose names match *names*."""
    wanted = set(names)
    return frozenset(r.id for r in getattr(guild, "roles", ()) if r.name in wanted)


def _tier_ids_for_column(guild, names, column):
    stored = _stored_ids(guild.id, column)
    return stored if stored is not None else _names_to_ids(guild, names)


def tier_role_ids(guild, names):
    """The role ids granting the tier identified by *names* in *guild*.

    *names* is one of the constants tuples — ADMIN_ROLES, ORGANISER_ROLE_NAMES,
    or ORGANISER_ROLES (the union). Passing the union resolves BOTH columns and
    unions them, preserving today's rule that an admin is always an organiser
    too.
    """
    wanted = set(names)
    ids = set()
    if wanted & set(ADMIN_ROLES):
        ids |= _tier_ids_for_column(guild, ADMIN_ROLES, "admin_role_ids")
    if wanted & set(ORGANISER_ROLE_NAMES):
        ids |= _tier_ids_for_column(guild, ORGANISER_ROLE_NAMES, "organiser_role_ids")
    return frozenset(ids)


def member_holds_tier(member_role_ids, guild, names):
    """True if any of *member_role_ids* grants the tier in *guild*."""
    return bool(set(member_role_ids) & tier_role_ids(guild, names))


def organiser_gate_active(guild) -> bool:
    """False if the guild has nothing standing for the Organiser tier — when
    False, Organiser-tier checks pass for every member (a deliberate convenience
    for small single-team guilds; see M9 in the 2026-08-07 review, left as-is by
    decision). Never affects the Admin tier, which is always strictly gated.

    Reads the configured ids where a guild has opted in. Keying this on names
    after the tier itself moved to ids would let a guild that configured an
    organiser role with a non-matching name read as un-gated, opening the tier
    to everyone.
    """
    return bool(_tier_ids_for_column(guild, ORGANISER_ROLE_NAMES, "organiser_role_ids"))
