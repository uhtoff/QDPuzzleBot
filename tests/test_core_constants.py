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


def test_service_written_statuses_are_named_but_not_settable():
    """unsolve() and add_partial() write statuses no user may set.

    They used to be bare literals ("Unsolved"/"Partially Solved") absent from
    STATUSES, so the service wrote values its own validator would reject. They
    are named constants now, but must stay OUT of STATUSES so update_puzzle_field
    keeps refusing them -- those states are owned by the solve/unsolve flows.
    """
    assert core.UNSOLVED_STATUS == "unsolved"
    assert core.PARTIAL_STATUS == "partially solved"
    assert core.UNSOLVED_STATUS not in core.STATUSES
    assert core.PARTIAL_STATUS not in core.STATUSES
    assert core.ALL_STATUS_VALUES == tuple(core.STATUSES) + ("unsolved", "partially solved")


def test_stored_statuses_are_canonically_lowercase():
    """Storage is lowercase; Title Case is a display concern only."""
    for value in core.ALL_STATUS_VALUES:
        assert value == value.lower(), f"{value!r} is not canonical lowercase"


def test_format_status_titlecases_for_display():
    assert core.format_status("in progress") == "In Progress"
    assert core.format_status("needs submission") == "Needs Submission"
    assert core.format_status("partially solved") == "Partially Solved"
    # Legacy Title Case rows predate the backfill and must survive unchanged.
    assert core.format_status("Stuck") == "Stuck"
    assert core.format_status(None) is None
    assert core.format_status("") == ""


def test_status_slash_choices_match_the_canonical_list():
    """The regression this tranche closed: "needs submission" was in STATUSES but
    absent from the slash choices, so it was settable by bang command and not by
    slash. The choices are derived from STATUSES now; this asserts the derivation
    holds, in order, and that the stored VALUE is lowercase while the user-facing
    NAME is Title Case.
    """
    (param,) = [p for p in puzzles_mod.Puzzles.status.app_command.parameters
                if p.name == "status"]
    assert [c.value for c in param.choices] == core.STATUSES
    assert [c.name for c in param.choices] == [core.format_status(s) for s in core.STATUSES]


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


# The organiser_gate_active tests moved to tests/test_role_gate.py with the
# function itself (M8): the gate now reads configured role ids, not role names.
