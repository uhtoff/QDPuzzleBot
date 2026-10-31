"""preflight: everything !setup_server needs is checked before anything is made,
and each refusal names the one-step human fix."""
from unittest.mock import MagicMock, patch

import pytest

from bot.core import server_setup
from bot.core.constants import ADMIN_ROLES
from bot.core.server_setup import SetupRefused, preflight

TEAM, HUB, MOD, ROSTER, BOT_ROLE = 2000, 1000, 50, 70, 90


def _role(role_id, name, position):
    r = MagicMock()
    r.id = role_id
    r.name = name
    r.position = position
    r.managed = False
    r.is_default.return_value = False
    return r


def _channel(name):
    c = MagicMock()
    c.name = name
    return c


def _guild():
    g = MagicMock()
    g.id = TEAM
    g.me.guild_permissions.administrator = True
    g.me.top_role = _role(BOT_ROLE, "PuzzBot", 10)
    g.default_role = _role(TEAM, "@everyone", 0)
    g.default_role.permissions.view_channel = False
    g.roles = [g.default_role, _role(MOD, "mod", 5), g.me.top_role]
    g.text_channels = [_channel("lobby"), _channel("bot-commands")]
    g.categories = []
    return g


def _hub(roster=None):
    hub = MagicMock()
    hub.id = HUB
    roster = roster or _role(ROSTER, "Team Rocket", 3)
    hub.get_role.side_effect = lambda rid: roster if rid == ROSTER else None
    return hub


def _settings():
    s = MagicMock()
    s.id = 7
    s.setup_complete_time = None   # preflight now also reports 'already set up'
    return s


def _run(guild=None, hub=None, roster_id=ROSTER, existing=None, count=0, name="Big Hunt"):
    with patch("bot.core.server_setup.HuntJsonDb") as hdb:
        hdb.get_by_name_for_guild.return_value = existing
        hdb.count_for_guild.return_value = count
        return preflight(guild or _guild(), _settings(), hub or _hub(), roster_id, name)


def test_template_mod_role_clears_the_admin_gate_by_name():
    assert server_setup.TEMPLATE_MOD_ROLE in ADMIN_ROLES


def test_a_fresh_template_server_passes_and_resolves_its_targets():
    guild = _guild()
    targets = _run(guild=guild)
    assert targets.mod_role.id == MOD
    assert targets.lobby is guild.text_channels[0]
    assert targets.bot_channel is guild.text_channels[1]
    assert targets.roster_role.id == ROSTER
    assert targets.hunt is None


def test_refuses_without_administrator():
    guild = _guild()
    guild.me.guild_permissions.administrator = False
    with pytest.raises(SetupRefused, match="Administrator"):
        _run(guild=guild)


def test_refuses_a_server_where_everyone_can_see_channels():
    guild = _guild()
    guild.default_role.permissions.view_channel = True
    with pytest.raises(SetupRefused, match="template"):
        _run(guild=guild)


@pytest.mark.parametrize("roles", [[], ["mod", "mod"]])
def test_refuses_unless_exactly_one_mod_role(roles):
    guild = _guild()
    guild.roles = [guild.default_role, guild.me.top_role] + [
        _role(MOD + i, n, 5) for i, n in enumerate(roles)]
    with pytest.raises(SetupRefused, match="mod"):
        _run(guild=guild)


@pytest.mark.parametrize("mod_position", [10, 11])
def test_refuses_when_mod_is_not_below_the_bot(mod_position):
    guild = _guild()
    guild.roles[1].position = mod_position          # equal is not below
    with pytest.raises(SetupRefused, match="drag"):
        _run(guild=guild)


@pytest.mark.parametrize("missing", ["lobby", "bot-commands"])
def test_refuses_a_missing_template_channel(missing):
    guild = _guild()
    guild.text_channels = [c for c in guild.text_channels if c.name != missing]
    with pytest.raises(SetupRefused, match=missing):
        _run(guild=guild)


def test_refuses_a_role_id_not_on_the_hub():
    with pytest.raises(SetupRefused, match="role"):
        _run(roster_id=ROSTER + 1)


def test_refuses_a_managed_hub_role():
    roster = _role(ROSTER, "Some Bot", 3)
    roster.managed = True
    with pytest.raises(SetupRefused, match="role"):
        _run(hub=_hub(roster))


def test_refuses_the_hubs_everyone_role():
    roster = _role(ROSTER, "@everyone", 0)
    roster.is_default.return_value = True
    with pytest.raises(SetupRefused, match="role"):
        _run(hub=_hub(roster))


def test_refuses_a_hub_role_named_like_a_permission_tier():
    with pytest.raises(SetupRefused, match="tier"):
        _run(hub=_hub(_role(ROSTER, "mod", 3)))


def test_refuses_a_server_that_already_has_another_hunt():
    with pytest.raises(SetupRefused, match="another hunt"):
        _run(existing=None, count=1)


def test_refuses_an_existing_hunt_plus_another():
    hunt = MagicMock()
    hunt.roster_role_id = 0
    with pytest.raises(SetupRefused, match="another hunt"):
        _run(existing=hunt, count=2)


def test_a_rerun_picks_up_the_hunt_a_partial_run_created():
    hunt = MagicMock()
    hunt.name = "Big Hunt"
    hunt.roster_role_id = ROSTER          # already tied to this run's hub role
    targets = _run(existing=hunt, count=1)
    assert targets.hunt is hunt


def test_refuses_a_rerun_naming_a_different_hub_role():
    hunt = MagicMock()
    hunt.name = "Big Hunt"
    hunt.roster_role_id = ROSTER + 1
    with pytest.raises(SetupRefused, match="already tied"):
        _run(existing=hunt, count=1)


def test_a_rerun_with_no_roster_role_recorded_yet_passes():
    hunt = MagicMock()
    hunt.name = "Big Hunt"
    hunt.roster_role_id = 0
    targets = _run(existing=hunt, count=1)
    assert targets.hunt is hunt


def test_refuses_a_leftover_category_with_no_hunt_saved():
    guild = _guild()
    leftover = MagicMock()
    leftover.name = "big-hunt"                      # clean_category_name("Big Hunt")
    guild.categories = [leftover]
    with patch("bot.core.server_setup.clean_category_name", return_value="big-hunt"):
        with pytest.raises(SetupRefused, match="delete"):
            _run(guild=guild)


def test_refuses_a_team_role_left_above_the_bot():
    guild = _guild()
    guild.roles.append(_role(71, "Team Rocket", 12))
    with pytest.raises(SetupRefused, match="drag"):
        _run(guild=guild)
