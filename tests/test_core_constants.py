"""M3: the role/status/priority constants live in exactly one place."""
import bot.core.constants as core
import bot.cogs.puzzles as puzzles_mod
import bot.web.auth as auth_mod
import bot.web.actions as actions_mod


def test_core_defines_canonical_values():
    assert core.ADMIN_ROLES == ('Moderator', 'mod', 'admin')
    assert core.ORGANISER_ROLES == core.ADMIN_ROLES + ('Organisers',)
    assert core.STATUSES == ["unstarted", "in progress", "stuck",
                             "needs extraction", "needs submission",
                             "solved", "backsolved"]
    assert core.PRIORITIES == ["low", "medium", "high", "very high"]


def test_puzzles_cog_uses_core_constants():
    assert puzzles_mod.ADMIN_ROLES is core.ADMIN_ROLES
    assert puzzles_mod.ORGANISER_ROLES is core.ORGANISER_ROLES
    assert puzzles_mod.Puzzles.STATUSES is core.STATUSES
    assert puzzles_mod.Puzzles.PRIORITIES is core.PRIORITIES


def test_auth_uses_core_roles():
    assert auth_mod.ADMIN_ROLES is core.ADMIN_ROLES
    assert auth_mod.ORGANISER_ROLES is core.ORGANISER_ROLES


def test_actions_statuses_match_core():
    assert actions_mod.PUZZLE_STATUSES == set(core.STATUSES)
    assert actions_mod.PUZZLE_PRIORITIES == set(core.PRIORITIES)
