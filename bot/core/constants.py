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


# organiser_gate_active lives in bot.core.role_gate: since M8 it must read the
# guild's CONFIGURED role ids, and this module stays free of the DB layer.


# The statuses a user may set (via !status, the slash command, or the dashboard
# picker). Stored values are canonically LOWERCASE; Title Case is produced at the
# display edges (slash Choice names, embed fields, dashboard badges) so that one
# spelling reaches the database however the status was set.
STATUSES = ["unstarted", "in progress", "stuck", "needs extraction",
            "needs submission", "solved", "backsolved"]

# Two further statuses are written by the service and are NOT user-settable, so
# they stay out of STATUSES and update_puzzle_field keeps rejecting them: the
# solve/unsolve/partial flows own them. They are still part of the stored
# vocabulary, which is what STATUSES alone failed to say.
UNSOLVED_STATUS = "unsolved"          # PuzzleService.unsolve
PARTIAL_STATUS = "partially solved"   # PuzzleService.add_partial

# Everything that can legitimately appear in puzzles.status.
ALL_STATUS_VALUES = tuple(STATUSES) + (UNSOLVED_STATUS, PARTIAL_STATUS)


PRIORITIES = ["low", "medium", "high", "very high"]
SOURCE_MODES = ("round", "subtree", "explicit")


def format_status(status):
    """Title-case a stored status for display ("in progress" -> "In Progress").

    Stored values are lowercase; every user-facing surface (embed fields, the
    !list rendering, dashboard badges) title-cases at the edge. Legacy rows may
    still hold Title Case, which this leaves unchanged.
    """
    return status.title() if status else status


def format_priority(priority):
    """Title-case a stored priority for display. Same contract as format_status."""
    return priority.title() if priority else priority
