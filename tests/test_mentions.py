"""Capture-time mention naming: bot.core.mentions and its bridge wiring."""
import json
from unittest.mock import MagicMock

from bot.core.mentions import resolve_mentions

USERS = {1: "Ann", 2: "Bob"}
ROLES = {3: "Solvers", 4: "Admins"}


def test_each_mention_gets_its_own_name():
    text = "<@1> and <@!2>, ping <@&3> not <@&4>"
    assert resolve_mentions(text, USERS.get, ROLES.get) == "@Ann and @Bob, ping @Solvers not @Admins"


def test_unknown_mentions_and_channels_stay_raw():
    text = "<@9> <@&9> in <#1> <:wave:1>"
    assert resolve_mentions(text, USERS.get, ROLES.get) == text


def test_non_string_names_are_not_trusted():
    # A cache miss can hand back a non-name; the raw markup is the safe fallback.
    assert resolve_mentions("<@1>", lambda _id: MagicMock(), ROLES.get) == "<@1>"


def _guild(members, roles):
    g = MagicMock()
    g.get_member.side_effect = lambda i: MagicMock(display_name=members[i]) if i in members else None
    g.get_role.side_effect = lambda i: MagicMock() if i not in roles else _named(roles[i])
    return g


def _named(name):
    m = MagicMock()
    m.name = name
    return m


async def test_on_message_stores_names_from_mentions_and_cache():
    from tests.test_bridge_cog import _make_cog, _msg
    cog = _make_cog()
    m = _msg(mid=1, cid=100, author_id=7, is_bot=False, content="<@1> ask <@2> or <@&3>")
    ann = MagicMock(id=1, display_name="Ann (nick)")
    m.mentions = [ann]                       # the message's own mention wins
    m.role_mentions = []
    m.guild = _guild({1: "stale", 2: "Bob"}, {3: "Solvers"})
    m.guild.id = 42
    m.embeds = [MagicMock(to_dict=MagicMock(return_value={
        "description": "solved by <@2>", "fields": [{"name": "<@&3>", "value": "<@1>"}],
        "footer": {"text": "<@2>"}}))]
    await cog.on_message(m)
    rec = cog._queue.get_nowait()
    assert rec["content"] == "@Ann (nick) ask @Bob or @Solvers"
    assert json.loads(rec["embeds"]) == [{
        "description": "solved by @Bob",
        "fields": [{"name": "@Solvers", "value": "@Ann (nick)", "inline": False}],
        "footer": "@Bob"}]


async def test_edit_names_from_payload_then_cache():
    from tests.test_bridge_cog import _make_cog
    cog = _make_cog()
    cog.bot.get_guild.return_value = _guild({2: "Bob"}, {3: "Solvers"})
    payload = MagicMock(message_id=1, channel_id=100, guild_id=42)
    payload.data = {
        "content": "<@1> <@5> <@6> <@2> <@&3>",
        "mentions": [
            {"id": "1", "username": "ann", "global_name": "Ann G", "member": {"nick": "Annie"}},
            {"id": "5", "username": "cat", "global_name": "Cat G"},
            {"id": "6", "username": "dan"},
        ],
        "embeds": [{"description": "<@2>"}],
    }
    await cog.on_raw_message_edit(payload)
    rec = cog._queue.get_nowait()
    cog.bot.get_guild.assert_called_with(42)
    assert rec["content"] == "@Annie @Cat G @dan @Bob @Solvers"
    assert json.loads(rec["embeds"]) == [{"description": "@Bob"}]
