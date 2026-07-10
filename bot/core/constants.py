"""Canonical authorization-role / puzzle-status / priority constants.

Single source of truth shared by the Discord cog (bot/cogs/puzzles.py) and the
web layer (bot/web/auth.py, bot/web/actions.py). Previously each side defined its
own copy, which had already drifted in type (list vs set). STATUSES / PRIORITIES
are ordered lists (UI enumeration needs order); the web side wraps them in set()
locally for membership checks.
"""

ADMIN_ROLES = ('Moderator', 'mod', 'admin')
ORGANISER_ROLE_NAMES = ('Organiser', 'Organisers', 'Organizer', 'Organizers')
ORGANISER_ROLES = ADMIN_ROLES + ORGANISER_ROLE_NAMES


def organiser_gate_active(guild) -> bool:
    """False if the guild has none of ORGANISER_ROLE_NAMES — when False,
    Organiser-tier permission checks pass for everyone. Never affects the
    Admin tier (Moderator/mod/admin), which is always strictly gated."""
    return any(r.name in ORGANISER_ROLE_NAMES for r in guild.roles)


STATUSES = ["unstarted", "in progress", "stuck", "needs extraction",
            "needs submission", "solved", "backsolved"]
PRIORITIES = ["low", "medium", "high", "very high"]
SOURCE_MODES = ("round", "subtree", "explicit")
