"""Canonical authorization-role / puzzle-status / priority constants.

Single source of truth shared by the Discord cog (bot/cogs/puzzles.py) and the
web layer (bot/web/auth.py, bot/web/actions.py). Previously each side defined its
own copy, which had already drifted in type (list vs set). STATUSES / PRIORITIES
are ordered lists (UI enumeration needs order); the web side wraps them in set()
locally for membership checks.
"""

ADMIN_ROLES = ('Moderator', 'mod', 'admin')
ORGANISER_ROLES = ADMIN_ROLES + ('Organisers',)

STATUSES = ["unstarted", "in progress", "stuck", "needs extraction",
            "needs submission", "solved", "backsolved"]
PRIORITIES = ["low", "medium", "high", "very high"]
