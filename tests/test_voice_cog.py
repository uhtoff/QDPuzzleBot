"""Voice cog: !voice, !voice_cleanup, !voice_per_puzzle, the presence listener."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from discord.ext import commands

from bot.cogs.voice import Voice, _hunt_for
from bot.core.voice import VoiceError
from tests.conftest import make_ctx


def _cog():
    return Voice(MagicMock())


def _puzzle(name="Cat"):
    p = MagicMock()
    p.name = name
    p.hunt_id = 3
    return p


def _hunt(on=0):
    h = MagicMock()
    h.id = 3
    h.name = "GPH"
    h.voice_per_puzzle = on
    return h


@pytest.fixture
def stores():
    with patch("bot.cogs.voice.PuzzleJsonDb") as P, \
         patch("bot.cogs.voice.HuntJsonDb") as H, \
         patch("bot.cogs.voice.TagGroupJsonDb") as T:
        P.get_by_attr.return_value = None
        H.get_by_attr.return_value = None
        T.get_by_attr.return_value = None
        yield P, H, T


def _sent(ctx):
    return ctx.send.await_args.args[0]


async def test_voice_success_mentions_channel(stores):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle("Cat")
    H.get_by_attr.return_value = _hunt()
    chan = MagicMock()
    chan.mention = "<#9>"
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.create_puzzle_voice",
               new=AsyncMock(return_value=(chan, True))) as create:
        await Voice.voice.callback(_cog(), ctx)
    assert _sent(ctx) == ":loud_sound: Voice for **Cat**: <#9>"
    assert create.await_args.args[0] is ctx.guild


MOVE_WARNING = (":warning: I don't have **Move Members**, so people can't be moved out "
                "when this channel closes — they'll be disconnected instead.")


async def test_voice_warns_when_bot_lacks_move_members(stores):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle("Cat")
    hunt = _hunt()
    H.get_by_attr.return_value = hunt
    chan = MagicMock()
    chan.mention = "<#9>"
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.create_puzzle_voice",
               new=AsyncMock(return_value=(chan, True))), \
         patch("bot.cogs.voice.voice.can_move_members", return_value=False) as cmm:
        await Voice.voice.callback(_cog(), ctx)
    assert _sent(ctx) == ":loud_sound: Voice for **Cat**: <#9>\n" + MOVE_WARNING
    cmm.assert_called_once_with(ctx.guild, hunt)


async def test_voice_no_warning_with_move_members(stores):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle("Cat")
    H.get_by_attr.return_value = _hunt()
    chan = MagicMock()
    chan.mention = "<#9>"
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.create_puzzle_voice",
               new=AsyncMock(return_value=(chan, True))), \
         patch("bot.cogs.voice.voice.can_move_members", return_value=True):
        await Voice.voice.callback(_cog(), ctx)
    assert _sent(ctx) == ":loud_sound: Voice for **Cat**: <#9>"


async def test_voice_existing_channel_same_text(stores):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle("Cat")
    H.get_by_attr.return_value = _hunt()
    chan = MagicMock()
    chan.mention = "<#9>"
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.create_puzzle_voice",
               new=AsyncMock(return_value=(chan, False))):
        await Voice.voice.callback(_cog(), ctx)
    assert _sent(ctx) == ":loud_sound: Voice for **Cat**: <#9>"


async def test_voice_not_a_puzzle_channel(stores):
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.create_puzzle_voice", new=AsyncMock()) as create:
        await Voice.voice.callback(_cog(), ctx)
    assert _sent(ctx) == ":x: Run this in a puzzle's channel."
    create.assert_not_awaited()


@pytest.mark.parametrize("code,text", [
    ("solved", ":x: This puzzle is solved — no new voice channel."),
    ("archived", ":x: This hunt is archived."),
    ("no_hunt_category", ":x: I can't find this hunt's category to copy permissions "
                         "from, so I won't make a voice channel."),
])
async def test_voice_refusals(stores, code, text):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle()
    H.get_by_attr.return_value = _hunt()
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.create_puzzle_voice",
               new=AsyncMock(side_effect=VoiceError(code))):
        await Voice.voice.callback(_cog(), ctx)
    assert _sent(ctx) == text


async def test_voice_cleanup_reports_removed(stores):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle()
    H.get_by_attr.return_value = _hunt()
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.cleanup_hunt",
               new=AsyncMock(return_value=["Cat", "Dog"])):
        await Voice.voice_cleanup.callback(_cog(), ctx)
    sent = [c.args[0] for c in ctx.send.await_args_list]
    assert sent == [":broom: Clearing puzzle voice channels…",
                    ":white_check_mark: Removed 2 voice channel(s): Cat, Dog"]


async def test_voice_cleanup_nothing_to_remove(stores):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle()
    H.get_by_attr.return_value = _hunt()
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.cleanup_hunt", new=AsyncMock(return_value=[])):
        await Voice.voice_cleanup.callback(_cog(), ctx)
    assert [c.args[0] for c in ctx.send.await_args_list][-1] == \
        "No puzzle voice channels to remove."


async def test_voice_cleanup_resolves_hunt_from_hunt_channel(stores):
    _, H, _ = stores
    hunt = _hunt()
    H.get_by_attr.side_effect = lambda **kw: hunt if "channel_id" in kw else None
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.cleanup_hunt",
               new=AsyncMock(return_value=[])) as cleanup:
        await Voice.voice_cleanup.callback(_cog(), ctx)
    assert cleanup.await_args.args[1] is hunt


async def test_voice_cleanup_unknown_channel(stores):
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.cleanup_hunt", new=AsyncMock()) as cleanup:
        await Voice.voice_cleanup.callback(_cog(), ctx)
    cleanup.assert_not_awaited()
    assert _sent(ctx).startswith(":x:")


@pytest.mark.parametrize("arg,flag,text", [
    ("on", True, "Every new puzzle in **GPH** now gets a voice channel."),
    ("off", False, "New puzzles in **GPH** no longer get a voice channel automatically "
                   "(existing ones stay; `!voice_cleanup` removes them)."),
])
async def test_voice_per_puzzle_sets(stores, arg, flag, text):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle()
    H.get_by_attr.return_value = _hunt()
    ctx = make_ctx()
    await Voice.voice_per_puzzle.callback(_cog(), ctx, arg)
    H.set_voice_per_puzzle.assert_called_once_with(3, flag)
    assert _sent(ctx) == text


@pytest.mark.parametrize("on,state", [(1, "on"), (0, "off")])
async def test_voice_per_puzzle_reports_without_arg(stores, on, state):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle()
    H.get_by_attr.return_value = _hunt(on)
    ctx = make_ctx()
    await Voice.voice_per_puzzle.callback(_cog(), ctx)
    H.set_voice_per_puzzle.assert_not_called()
    assert f"**{state}**" in _sent(ctx)


async def test_voice_per_puzzle_rejects_junk(stores):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle()
    H.get_by_attr.return_value = _hunt()
    ctx = make_ctx()
    await Voice.voice_per_puzzle.callback(_cog(), ctx, "maybe")
    H.set_voice_per_puzzle.assert_not_called()


def _vs(before_id, after_id):
    b, a = MagicMock(), MagicMock()
    b.channel = None if before_id is None else MagicMock(id=before_id)
    a.channel = None if after_id is None else MagicMock(id=after_id)
    return b, a


async def test_listener_publishes_one_event_per_changed_id():
    before, after = _vs(1, 2)
    with patch("bot.cogs.voice.voice_presence.on_state_change", return_value=[1, 2]), \
         patch("bot.cogs.voice.voice_presence.event_for",
               side_effect=lambda i: {"type": "voice", "id": i}), \
         patch("bot.cogs.voice.events.publish") as publish:
        await _cog().on_voice_state_update(MagicMock(), before, after)
    assert [c.args[0]["id"] for c in publish.call_args_list] == [1, 2]


async def test_listener_nothing_for_mute_toggle():
    before, after = _vs(1, 1)
    with patch("bot.cogs.voice.voice_presence.on_state_change", return_value=[]) as osc, \
         patch("bot.cogs.voice.voice_presence.event_for") as ef, \
         patch("bot.cogs.voice.events.publish") as publish:
        await _cog().on_voice_state_update(MagicMock(), before, after)
    assert osc.call_args.args[1:] == (before.channel, after.channel)
    ef.assert_not_called()
    publish.assert_not_called()


async def test_listener_skips_unowned_channels_and_never_raises():
    before, after = _vs(None, 5)
    with patch("bot.cogs.voice.voice_presence.on_state_change", return_value=[5]), \
         patch("bot.cogs.voice.voice_presence.event_for", return_value=None), \
         patch("bot.cogs.voice.events.publish") as publish:
        await _cog().on_voice_state_update(MagicMock(), before, after)
    publish.assert_not_called()
    with patch("bot.cogs.voice.voice_presence.on_state_change", side_effect=RuntimeError):
        await _cog().on_voice_state_update(MagicMock(), before, after)  # must not raise


async def test_on_ready_snapshots_guilds():
    cog = _cog()
    with patch("bot.cogs.voice.voice_presence.snapshot") as snap:
        await cog.on_ready()
    snap.assert_called_once_with(cog.bot.guilds)


def test_admin_commands_are_gated_and_voice_is_open():
    def has_check(cmd):
        return bool(cmd.checks)
    assert has_check(Voice.voice_cleanup)
    assert has_check(Voice.voice_per_puzzle)
    assert not has_check(Voice.voice)
    assert Voice.voice.aliases == ["vc"]


@pytest.mark.parametrize("cmd", [Voice.voice_cleanup, Voice.voice_per_puzzle])
async def test_admin_check_refuses_non_admin(cmd):
    ctx = make_ctx()
    ctx.author.roles = []
    with patch("bot.cogs.puzzles._holds", return_value=False):
        with pytest.raises(commands.MissingAnyRole):
            await cmd.checks[0](ctx)


async def test_hunt_for_resolves_via_group_category(stores):
    _, H, T = stores
    hunt = _hunt()
    group = MagicMock(hunt_id=3)
    T.get_by_attr.side_effect = lambda **kw: group if kw == {"category_id": 77} else None
    H.get_by_attr.side_effect = lambda **kw: hunt if kw == {"id": 3} else None
    channel = MagicMock()
    channel.category.id = 77
    assert _hunt_for(channel) is hunt
    channel.category.id = 78
    assert _hunt_for(channel) is None


async def test_voice_unknown_error_code_fallback(stores):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle()
    H.get_by_attr.return_value = _hunt()
    ctx = make_ctx()
    with patch("bot.cogs.voice.voice.create_puzzle_voice",
               new=AsyncMock(side_effect=VoiceError("not_a_puzzle"))):
        await Voice.voice.callback(_cog(), ctx)
    assert _sent(ctx) == ":x: Couldn't make a voice channel."


async def test_sweep_swallows_errors():
    cog = _cog()
    with patch("bot.cogs.voice.voice.sweep_solved",
               new=AsyncMock(side_effect=RuntimeError)):
        await cog.sweep.coro(cog)  # must not raise


def _pushing_cog():
    """A Voice cog whose bot has a Puzzles cog with an awaitable board notifier."""
    bot = MagicMock()
    puzzles = MagicMock()
    puzzles.service.notifier.board_changed = AsyncMock()
    bot.get_cog.side_effect = lambda name: puzzles if name == "Puzzles" else None
    return Voice(bot), puzzles.service.notifier.board_changed


@pytest.mark.parametrize("created", [True, False])
async def test_voice_pushes_the_board_only_when_it_created_a_channel(stores, created):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle("Cat")
    hunt = _hunt()
    H.get_by_attr.return_value = hunt
    cog, board_changed = _pushing_cog()
    with patch("bot.cogs.voice.voice.create_puzzle_voice",
               new=AsyncMock(return_value=(MagicMock(mention="<#9>"), created))):
        await Voice.voice.callback(cog, make_ctx())
    if created:
        board_changed.assert_awaited_once_with(hunt)
    else:
        board_changed.assert_not_awaited()


@pytest.mark.parametrize("names", [["Cat"], []])
async def test_voice_cleanup_pushes_the_board_when_it_removed_something(stores, names):
    P, H, _ = stores
    P.get_by_attr.return_value = _puzzle()
    hunt = _hunt()
    H.get_by_attr.return_value = hunt
    cog, board_changed = _pushing_cog()
    with patch("bot.cogs.voice.voice.cleanup_hunt", new=AsyncMock(return_value=names)):
        await Voice.voice_cleanup.callback(cog, make_ctx())
    if names:
        board_changed.assert_awaited_once_with(hunt)
    else:
        board_changed.assert_not_awaited()
