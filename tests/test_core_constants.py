"""M3: the role/status/priority constants live in exactly one place."""
import bot.core.constants as core
import bot.cogs.puzzles as puzzles_mod
import bot.web.auth as auth_mod
import bot.web.actions as actions_mod


def test_core_defines_canonical_values():
    assert core.ADMIN_ROLES == ('Moderator', 'mod', 'admin')
    assert core.ORGANISER_ROLE_NAMES == ('Organiser', 'Organisers', 'Organizer', 'Organizers')
    assert core.ORGANISER_ROLES == core.ADMIN_ROLES + core.ORGANISER_ROLE_NAMES
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


def test_actions_does_not_redefine_constants():
    """The web layer no longer maintains its own PUZZLE_STATUSES/PRIORITIES sets;
    validation moved to PuzzleService.update_puzzle_field which reads from core."""
    assert not hasattr(actions_mod, "PUZZLE_STATUSES"), \
        "PUZZLE_STATUSES should be removed from actions — validation is in the service"
    assert not hasattr(actions_mod, "PUZZLE_PRIORITIES"), \
        "PUZZLE_PRIORITIES should be removed from actions — validation is in the service"


from types import SimpleNamespace


def _guild_with_roles(role_names):
    return SimpleNamespace(roles=[SimpleNamespace(name=n) for n in role_names])


def test_organiser_gate_active_when_organisers_role_present():
    guild = _guild_with_roles(["everyone", "Organisers"])
    assert core.organiser_gate_active(guild) is True


def test_organiser_gate_active_for_each_accepted_spelling():
    for name in ("Organiser", "Organisers", "Organizer", "Organizers"):
        guild = _guild_with_roles(["everyone", name])
        assert core.organiser_gate_active(guild) is True, f"{name!r} should activate the gate"


def test_organiser_gate_inactive_when_no_organisers_role():
    guild = _guild_with_roles(["everyone", "Moderator"])
    assert core.organiser_gate_active(guild) is False


def test_organiser_gate_inactive_when_guild_has_no_roles():
    guild = _guild_with_roles([])
    assert core.organiser_gate_active(guild) is False
