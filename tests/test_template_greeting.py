"""A server cloned from the team-server template gets a join greeting aimed at
!setup_server, posted in #bot-commands (owner + mod only) — not the hub's
manual-setup greeting, which would land in #lobby where every player sees it."""
from unittest.mock import MagicMock, patch

from bot.cogs.settings import Settings
from bot.core.server_setup import looks_like_template


def _role(name, position=1):
    r = MagicMock()
    r.name = name
    r.position = position
    return r


def _channel(name, writable=True):
    c = MagicMock()
    c.name = name
    c.sent = []

    async def send(text, **_kw):
        c.sent.append(text)

    c.send = send
    c.permissions_for.return_value.send_messages = writable
    return c


def _template_guild():
    g = MagicMock()
    g.id = 5
    g.name = "Team Rocket"
    g.default_role = _role("@everyone", 0)
    g.default_role.permissions.view_channel = False
    g.roles = [g.default_role, _role("mod", 1)]
    g.text_channels = [_channel("lobby"), _channel("bot-commands")]
    g.system_channel = None
    return g


def _lobby(g):
    return g.text_channels[0]


def _bot_commands(g):
    return g.text_channels[1]


# --- looks_like_template ------------------------------------------------------

def test_a_template_clone_looks_like_a_template():
    assert looks_like_template(_template_guild())


def test_everyone_seeing_channels_is_not_a_template():
    g = _template_guild()
    g.default_role.permissions.view_channel = True
    assert not looks_like_template(g)


def test_no_mod_role_is_not_a_template():
    g = _template_guild()
    g.roles = [g.default_role]
    assert not looks_like_template(g)


def test_two_mod_roles_is_not_a_template():
    g = _template_guild()
    g.roles.append(_role("mod", 2))
    assert not looks_like_template(g)


def test_missing_lobby_is_not_a_template():
    g = _template_guild()
    g.text_channels = [_bot_commands(g)]
    assert not looks_like_template(g)


def test_missing_bot_commands_is_not_a_template():
    g = _template_guild()
    g.text_channels = [_lobby(g)]
    assert not looks_like_template(g)


# --- the greeting on join -----------------------------------------------------

async def _join(guild):
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = None
        await Settings(MagicMock()).on_guild_join(guild)


async def test_template_join_greets_in_bot_commands_not_lobby():
    g = _template_guild()
    await _join(g)
    assert _lobby(g).sent == []
    assert len(_bot_commands(g).sent) == 1
    text = _bot_commands(g).sent[0]
    assert "!setup_server <hub server id> <hub role id> <hunt url> <hunt name>" in text
    assert "above `mod`" in text


async def test_template_greeting_drops_the_manual_setup_steps():
    """!setup_server pins admin and gates the hunt itself, so the hub greeting's
    two manual steps would be wrong advice here."""
    g = _template_guild()
    await _join(g)
    text = _bot_commands(g).sent[0]
    assert "!set_admin_roles" not in text
    assert "overwrite" not in text.lower()


async def test_ordinary_server_still_gets_the_manual_setup_greeting():
    g = _template_guild()
    g.default_role.permissions.view_channel = True     # not locked down
    await _join(g)
    assert len(_lobby(g).sent) == 1                     # first writable channel
    assert "!set_admin_roles" in _lobby(g).sent[0]
    assert _bot_commands(g).sent == []


async def test_template_greeting_walks_through_the_whole_setup():
    g = _template_guild()
    await _join(g)
    text = _bot_commands(g).sent[0]
    steps = ["above `mod`", "!setup_server <hub server id>", "**Join**",
             "!send_invites <hunt name>", "ownership"]
    for step in steps:
        assert step in text, step
    positions = [text.index(s) for s in steps]
    assert positions == sorted(positions), "steps are out of order"
