"""Every command's permission tier, probed through its actual checks.

The decorator IS the whole gate for these commands — no callback re-checks
permissions — so removing one is silent, and a test that only asserts "has some
check" can't tell organiser from admin. This resolves the tier behaviourally
instead: run every check the command carries against three members (no roles, an
Organisers role, an admin role) and see which of them get through.

EXPECTED is a **census**, not a sample: every chat command in bot/cogs/puzzles.py
and bot/cogs/utility.py is named here with the tier it enforces, the ungated ones
included as "anyone". test_the_map_is_a_census fails if the cogs and the map
disagree about which commands exist, so a command cannot land — gated or not —
without someone writing its tier down here on purpose. This mirrors the web
side's exhaustive route map in tests/test_web_route_gating.py.

Guilds here are UNCONFIGURED (no stored role ids), the bootstrap state where the
role NAMES decide — the same setup tests/test_is_organiser_check.py uses. The
guild always has an Organisers role so the Organiser tier is active; without one,
is_organiser() bypasses and every command would probe as "anyone".
"""
from unittest.mock import MagicMock, patch

import pytest
from discord.ext import commands

from bot.cogs.puzzles import Puzzles
from bot.cogs.utility import Utility
from tests.conftest import make_ctx

COGS = (Puzzles, Utility)


def _registry():
    """{command name: Command} for every chat command the cogs register.

    Read off the cog classes rather than listed by hand — that is what makes the
    census self-maintaining: a new @commands.command shows up here whether or not
    anyone remembered this file.
    """
    return {c.qualified_name: c for cog in COGS for c in cog.__cog_commands__}


COMMANDS = _registry()


@pytest.fixture(autouse=True)
def _unconfigured():
    db = MagicMock()
    db.get_cached.return_value = None
    with patch("bot.store.GuildSettingsDb", db):
        yield


_next_id = iter(range(3000, 4000))


def _role(name):
    role = MagicMock()
    role.name = name
    role.id = next(_next_id)
    return role


async def _passes(command, held):
    """True if a member holding *held* satisfies every check on *command*."""
    organisers, admin = _role("Organisers"), _role("admin")
    by_name = {"Organisers": organisers, "admin": admin}
    ctx = make_ctx()
    ctx.guild.roles = [organisers, admin]
    ctx.author.roles = [by_name[h] for h in held]
    for check in command.checks:
        try:
            result = check(ctx)
            if hasattr(result, "__await__"):
                result = await result
            if result is False:
                return False
        except commands.CheckFailure:
            return False
    return True


async def tier_of(name):
    """'anyone' | 'organiser' | 'admin' for the command called *name*."""
    command = COMMANDS[name]
    if await _passes(command, []):
        return "anyone"
    if await _passes(command, ["Organisers"]):
        return "organiser"
    if await _passes(command, ["admin"]):
        return "admin"
    return "nobody"


# (command, tier) for EVERY command in both cogs — see the module docstring.
# Keep it alphabetical within each tier; the comments record why a tier is where
# it is, for the ones that were argued over.
EXPECTED = [
    # --- Admins/mods ---
    ("archive_category", "admin"),
    ("archive_channel", "admin"),
    ("archive_hunt", "admin"),
    ("archive_hunt_to_hub", "admin"),
    ("archive_round", "admin"),
    ("archive_solved_manually", "admin"),
    ("debug_puzzle_channel", "admin"),
    ("delete_hunt", "admin"),
    ("hunt", "admin"),
    ("invite_player", "admin"),
    ("post_role_panel", "admin"),
    ("mod_invite", "admin"),
    ("send_invites", "admin"),
    ("set_admin_roles", "admin"),
    ("set_hunt_role", "admin"),
    ("set_login", "admin"),
    ("set_organiser_roles", "admin"),
    ("set_start", "admin"),
    ("set_template_link", "admin"),
    ("show_auth_roles", "admin"),
    ("show_puzzle_settings", "admin"),
    ("show_settings", "admin"),
    ("show_template_link", "admin"),
    ("update_settings", "admin"),      # D3 dropped Manage Channels, tier unchanged
    ("use_solved_sheet", "admin"),

    # --- Organisers/admins ---
    ("absorb_category", "organiser"),  # D1: was admin
    ("backfill_glyphs", "organiser"),  # D3
    ("consolidate_to", "organiser"),   # D3 dropped Manage Channels, tier unchanged
    ("convert_to_capstone", "organiser"),
    ("convert_to_group", "organiser"),
    ("convert_to_meta", "organiser"),
    ("convert_to_metapuzzle", "organiser"),
    ("convert_to_puzzle", "organiser"),
    ("convert_to_runaround", "organiser"),
    ("delete_mini", "organiser"),
    ("delete_puzzle", "organiser"),
    ("delete_round", "organiser"),
    ("extract_target_to_category", "organiser"),
    ("list_tags", "organiser"),
    ("media_stats", "organiser"),
    ("media_sweep", "organiser"),
    ("move_to_bottom", "organiser"),
    ("move_to_solved", "organiser"),
    ("move_to_top", "organiser"),
    ("rebuild", "organiser"),
    ("rebuild_unassigned", "organiser"),
    ("reconcile", "organiser"),
    ("remove_minimeta", "organiser"),
    ("remove_sheet", "organiser"),     # D6: was anyone; it deletes the Google tab
    ("rename_group", "organiser"),
    ("rename_puzzle", "organiser"),
    ("round", "organiser"),            # D2: was anyone; creating a tag group
    ("start_reminders", "organiser"),
    ("stop_reminders", "organiser"),
    ("sync", "organiser"),

    # --- Anyone: ungated on purpose, and pinned here so that dropping a gate
    # onto one of these, or off one of the tiers above, has to be written down.
    ("add_mini", "anyone"),
    ("add_sheet", "anyone"),           # Create area; only removal is gated
    ("add_solution", "anyone"),
    ("canvas", "anyone"),
    ("change_solution", "anyone"),
    ("clue", "anyone"),
    ("clues", "anyone"),
    ("erase_note", "anyone"),          # Annotate area
    ("feeds", "anyone"),
    ("feeds_only", "anyone"),
    ("groups_only_with", "anyone"),
    ("groups_with", "anyone"),
    ("info", "anyone"),
    ("info_bot", "anyone"),            # Utility cog
    ("informs", "anyone"),
    ("informs_only", "anyone"),
    ("invite", "anyone"),              # Utility cog
    ("link", "anyone"),
    ("list_additional_sheets", "anyone"),
    ("list_minis", "anyone"),
    ("list_puzzles", "anyone"),
    ("make_minimeta", "anyone"),
    ("mark_as_complete", "anyone"),
    ("note", "anyone"),
    ("partial", "anyone"),
    ("priority", "anyone"),
    ("puzzle", "anyone"),              # creating a puzzle is a solver action
    ("remove_from", "anyone"),         # Tag area: tidying up without an organiser
    ("setup_server", "anyone"),        # Bang-only; hub-admin check is runtime
    ("solve", "anyone"),
    ("solve_mini", "anyone"),
    ("starttime", "anyone"),           # Utility cog
    ("status", "anyone"),
    ("type", "anyone"),
    ("unsolve", "anyone"),
    ("unsolve_mini", "anyone"),
    ("uptime", "anyone"),              # Utility cog
]


def test_the_map_is_a_census():
    """EXPECTED names exactly the commands the cogs register — no more, no less.

    This is what stops the map decaying back into a sample. A command added with
    no gate at all still fails here until its tier is recorded, so "ungated" has
    to be a decision someone wrote down rather than the default nobody notices.
    """
    mapped = [name for name, _ in EXPECTED]
    assert len(mapped) == len(set(mapped)), "duplicate entries in EXPECTED"
    assert sorted(mapped) == sorted(COMMANDS), (
        f"unmapped commands: {sorted(set(COMMANDS) - set(mapped))}; "
        f"mapped but no such command: {sorted(set(mapped) - set(COMMANDS))}"
    )


@pytest.mark.parametrize("name,expected", EXPECTED)
async def test_command_tier(name, expected):
    assert await tier_of(name) == expected


async def test_admin_commands_are_not_satisfied_by_an_organiser_role():
    """The tiers nest one way only: ORGANISER_ROLES contains ADMIN_ROLES, so an
    admin clears an organiser gate — but an Organisers role must NOT clear an
    admin gate, or the admin tier means nothing."""
    assert await _passes(COMMANDS["delete_hunt"], ["admin"]) is True
    assert await _passes(COMMANDS["delete_hunt"], ["Organisers"]) is False


async def test_manage_channels_is_no_longer_required_anywhere():
    """D3: the Discord permission was a second, undocumented bar on top of the
    tier. A member with the right role but no Manage Channels must get through."""
    for name in ("consolidate_to", "extract_target_to_category", "backfill_glyphs"):
        assert await _passes(COMMANDS[name], ["Organisers"]) is True, name
    for name in ("show_settings", "show_puzzle_settings", "update_settings"):
        assert await _passes(COMMANDS[name], ["admin"]) is True, name
