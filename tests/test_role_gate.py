"""M8 phase 2: the shared tier resolver.

Authorisation matched Discord role *names*, so a member with Manage Roles could
create a role called `admin`, self-assign it, and hold the dashboard's admin
tier. `bot.core.role_gate` is the one seam both the web routes and the bang
commands ask: given a guild and a tier, which role IDS grant it — the configured
ids where a guild has opted in, the ids of the name-matched roles where it has
not.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bot.core import role_gate
from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLE_NAMES


def _role(id, name):
    return SimpleNamespace(id=id, name=name)


def _guild(*roles, id=99):
    return SimpleNamespace(id=id, roles=list(roles))


def _settings(admin_role_ids="", organiser_role_ids=""):
    return SimpleNamespace(admin_role_ids=admin_role_ids,
                           organiser_role_ids=organiser_role_ids)


def _with_settings(settings):
    """Patch the settings lookup at role_gate's own import site."""
    db = MagicMock()
    db.get_cached.return_value = settings
    return patch("bot.store.GuildSettingsDb", db)


# ---------------------------------------------------------------- bootstrap

def test_unconfigured_guild_falls_back_to_the_role_names():
    guild = _guild(_role(101, "admin"), _role(102, "Moderator"), _role(103, "Solvers"))
    with _with_settings(_settings()):
        assert role_gate.tier_role_ids(guild, ADMIN_ROLES) == frozenset({101, 102})


def test_missing_settings_row_is_not_an_error():
    """A guild with no `guilds` row at all is the same 'not configured' state as
    one with NULL columns — it must bootstrap, not raise or fail open."""
    guild = _guild(_role(101, "admin"))
    with _with_settings(None):
        assert role_gate.tier_role_ids(guild, ADMIN_ROLES) == frozenset({101})


# ---------------------------------------------------------------- configured

def test_configured_guild_uses_the_stored_ids():
    guild = _guild(_role(101, "admin"), _role(700, "Hunt Runners"))
    with _with_settings(_settings(admin_role_ids="700")):
        assert role_gate.tier_role_ids(guild, ADMIN_ROLES) == frozenset({700})


def test_a_role_merely_named_admin_grants_nothing_once_configured():
    """The whole point of M8: minting a role called `admin` must not confer the
    tier in a guild that has opted in to ids."""
    impostor = _role(999, "admin")
    guild = _guild(_role(700, "Hunt Runners"), impostor)
    with _with_settings(_settings(admin_role_ids="700")):
        ids = role_gate.tier_role_ids(guild, ADMIN_ROLES)
        assert 999 not in ids
        assert not role_gate.member_holds_tier({999}, guild, ADMIN_ROLES)
        assert role_gate.member_holds_tier({700}, guild, ADMIN_ROLES)


def test_the_two_tiers_are_stored_and_read_separately():
    """A transposed pair would hand the admin tier to organisers and vice versa,
    so each tier must read its OWN column."""
    guild = _guild(_role(700, "Runners"), _role(800, "Helpers"))
    with _with_settings(_settings(admin_role_ids="700", organiser_role_ids="800")):
        assert role_gate.tier_role_ids(guild, ADMIN_ROLES) == frozenset({700})
        assert role_gate.tier_role_ids(guild, ORGANISER_ROLE_NAMES) == frozenset({800})


def test_organiser_tier_includes_the_admin_ids():
    """ORGANISER_ROLES = ADMIN_ROLES + ORGANISER_ROLE_NAMES today: an admin is
    always also an organiser, and that must survive the move to ids."""
    from bot.core.constants import ORGANISER_ROLES

    guild = _guild(_role(700, "Runners"), _role(800, "Helpers"))
    with _with_settings(_settings(admin_role_ids="700", organiser_role_ids="800")):
        assert role_gate.tier_role_ids(guild, ORGANISER_ROLES) == frozenset({700, 800})


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("stored,expected", [
    ("700", {700}),
    ("700,800", {700, 800}),
    (" 700 , 800 ", {700, 800}),
    ("700,,800", {700, 800}),
    ("700,notanid,800", {700, 800}),   # junk is skipped, the rest still gate
    ("", None),                        # empty  -> not configured
    (None, None),                      # NULL   -> not configured
])
def test_stored_id_parsing(stored, expected):
    guild = _guild(_role(101, "admin"))
    with _with_settings(_settings(admin_role_ids=stored)):
        ids = role_gate.tier_role_ids(guild, ADMIN_ROLES)
    # `None` expected means bootstrap: the name-matched role id, not the stored set.
    assert ids == frozenset(expected if expected is not None else {101})


def test_a_guild_configured_with_only_junk_does_not_fail_open():
    """If every stored id is unparseable the guild is still CONFIGURED — falling
    back to names there would re-open the exact hole M8 closes."""
    guild = _guild(_role(101, "admin"))
    with _with_settings(_settings(admin_role_ids="notanid")):
        assert role_gate.tier_role_ids(guild, ADMIN_ROLES) == frozenset()


# ---------------------------------------------------------------- gate state

def test_organiser_gate_active_follows_the_configured_ids():
    """`organiser_gate_active` decides whether the organiser tier is bypassed for
    everyone. Once ids are configured it must read them, not the names — else a
    guild that configured ids but has no Organiser-NAMED role reads as un-gated
    and opens the organiser tier to every member."""
    guild = _guild(_role(800, "Hunt Helpers"))
    with _with_settings(_settings(organiser_role_ids="800")):
        assert role_gate.organiser_gate_active(guild) is True


def test_organiser_gate_inactive_when_neither_ids_nor_names_exist():
    guild = _guild(_role(101, "Solvers"))
    with _with_settings(_settings()):
        assert role_gate.organiser_gate_active(guild) is False


def test_organiser_gate_active_by_name_when_unconfigured():
    guild = _guild(_role(101, "Organiser"))
    with _with_settings(_settings()):
        assert role_gate.organiser_gate_active(guild) is True
