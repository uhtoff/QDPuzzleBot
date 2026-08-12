"""Every command's permission tier, probed through its actual checks.

The decorator IS the whole gate for these commands — no callback re-checks
permissions — so removing one is silent, and a test that only asserts "has some
check" can't tell organiser from admin. This resolves the tier behaviourally
instead: run every check the command carries against three members (no roles, an
Organisers role, an admin role) and see which of them get through.

Guilds here are UNCONFIGURED (no stored role ids), the bootstrap state where the
role NAMES decide — the same setup tests/test_is_organiser_check.py uses. The
guild always has an Organisers role so the Organiser tier is active; without one,
is_organiser() bypasses and every command would probe as "anyone".
"""
from unittest.mock import MagicMock, patch

import pytest
from discord.ext import commands

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_ctx


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
    command = getattr(Puzzles, name)
    if await _passes(command, []):
        return "anyone"
    if await _passes(command, ["Organisers"]):
        return "organiser"
    if await _passes(command, ["admin"]):
        return "admin"
    return "nobody"


# (command, tier). Not exhaustive — the commands this batch moved, plus anchors
# either side of each move so a blanket change to one tier can't pass.
EXPECTED = [
    # --- moved by the 2026-08-11 gating batch ---
    ("round", "organiser"),            # D2: was anyone; creating a tag group
    ("absorb_category", "organiser"),  # D1: was admin
    ("remove_sheet", "organiser"),     # D6: was anyone; it deletes the Google tab
    # --- deliberately left open, recorded rulings ---
    ("puzzle", "anyone"),              # creating a puzzle is a solver action
    ("add_sheet", "anyone"),           # Create area; only removal is gated
    ("erase_note", "anyone"),          # Annotate area
    ("remove_from", "anyone"),         # Tag area: tidying up without an organiser
    ("feeds_only", "anyone"),
    ("solve", "anyone"),
    # --- feeder pool (Task 10): building is Create-area/ungated, destroying is organiser ---
    ("add_feeders", "anyone"),
    ("feeders", "anyone"),
    ("claim_feeder", "anyone"),
    ("unclaim_feeder", "anyone"),
    ("edit_feeder", "anyone"),
    ("remove_feeder", "organiser"),
    ("sync_claims", "anyone"),  # Task 6 (Plan 2): a refresh, not a mutation
    # --- anchors: unchanged by the batch ---
    ("convert_to_meta", "organiser"),
    ("delete_round", "organiser"),
    ("consolidate_to", "organiser"),   # D3 dropped Manage Channels, tier unchanged
    ("backfill_glyphs", "organiser"),  # D3
    ("reconcile", "organiser"),
    ("delete_hunt", "admin"),
    ("set_organiser_roles", "admin"),
    ("update_settings", "admin"),      # D3 dropped Manage Channels, tier unchanged
    ("archive_hunt", "admin"),
]


@pytest.mark.parametrize("name,expected", EXPECTED)
async def test_command_tier(name, expected):
    assert await tier_of(name) == expected


async def test_admin_commands_are_not_satisfied_by_an_organiser_role():
    """The tiers nest one way only: ORGANISER_ROLES contains ADMIN_ROLES, so an
    admin clears an organiser gate — but an Organisers role must NOT clear an
    admin gate, or the admin tier means nothing."""
    assert await _passes(getattr(Puzzles, "delete_hunt"), ["admin"]) is True
    assert await _passes(getattr(Puzzles, "delete_hunt"), ["Organisers"]) is False


async def test_manage_channels_is_no_longer_required_anywhere():
    """D3: the Discord permission was a second, undocumented bar on top of the
    tier. A member with the right role but no Manage Channels must get through."""
    for name in ("consolidate_to", "extract_target_to_category", "backfill_glyphs"):
        assert await _passes(getattr(Puzzles, name), ["Organisers"]) is True, name
    for name in ("show_settings", "show_puzzle_settings", "update_settings"):
        assert await _passes(getattr(Puzzles, name), ["admin"]) is True, name
