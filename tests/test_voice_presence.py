"""In-memory voice presence: counts of humans per voice channel."""
from unittest.mock import MagicMock, patch

import pytest

from bot.core import voice_presence


def _chan(cid, members=(), guild_id=7):
    ch = MagicMock()
    ch.id = cid
    ch.guild.id = guild_id
    ch.members = list(members)
    return ch


def _member(bot=False):
    m = MagicMock()
    m.bot = bot
    return m


@pytest.fixture(autouse=True)
def _reset():
    voice_presence.snapshot([])
    yield
    voice_presence.snapshot([])


def test_snapshot_counts_humans_only():
    a = _chan(1, [_member(), _member(), _member(bot=True)])
    b = _chan(2, [_member(bot=True)])
    guild = MagicMock()
    guild.voice_channels = [a, b]
    voice_presence.snapshot([guild])
    assert voice_presence.count(1) == 2
    assert voice_presence.count(2) == 0
    assert voice_presence.count(99) == 0


def test_snapshot_resets_previous_counts():
    guild = MagicMock()
    guild.voice_channels = [_chan(1, [_member()])]
    voice_presence.snapshot([guild])
    guild.voice_channels = [_chan(2, [_member()])]
    voice_presence.snapshot([guild])
    assert voice_presence.count(1) == 0
    assert voice_presence.count(2) == 1


def test_move_between_channels_changes_both():
    a, b = _chan(1), _chan(2)
    voice_presence.on_state_change(_member(), None, a)
    assert voice_presence.count(1) == 1
    changed = voice_presence.on_state_change(_member(), a, b)
    assert sorted(changed) == [1, 2]
    assert voice_presence.count(1) == 0
    assert voice_presence.count(2) == 1


def test_join_and_leave_report_one_id():
    a = _chan(1)
    assert voice_presence.on_state_change(_member(), None, a) == [1]
    assert voice_presence.on_state_change(_member(), a, None) == [1]


def test_count_never_goes_negative():
    a = _chan(1)
    voice_presence.on_state_change(_member(), a, None)
    assert voice_presence.count(1) == 0


def test_mute_toggle_changes_nothing():
    a = _chan(1)
    voice_presence.on_state_change(_member(), None, a)
    assert voice_presence.on_state_change(_member(), a, a) == []
    assert voice_presence.count(1) == 1


def test_bots_ignored():
    a = _chan(1)
    assert voice_presence.on_state_change(_member(bot=True), None, a) == []
    assert voice_presence.count(1) == 0


def test_event_for_puzzle_and_hunt_and_unknown():
    a, b, c = _chan(1, guild_id=7), _chan(2, guild_id=8), _chan(3)
    for ch in (a, b, c):
        voice_presence.on_state_change(_member(), None, ch)
    puzzle = MagicMock(channel_id=555)
    hunt = MagicMock(channel_id=666, uid="huntuid")
    with patch("bot.core.voice_presence.PuzzleJsonDb") as P, \
         patch("bot.core.voice_presence.HuntJsonDb") as H:
        P.by_voice_channel.side_effect = lambda i: puzzle if i == 1 else None
        H.by_voice_channel.side_effect = lambda i: hunt if i == 2 else None
        assert voice_presence.event_for(1) == {
            "type": "voice", "guild_id": 7, "channel_id": "555", "voice_count": 1}
        assert voice_presence.event_for(2) == {
            "type": "voice", "guild_id": 8, "channel_id": "666",
            "hunt_uid": "huntuid", "voice_count": 1}
        assert voice_presence.event_for(3) is None
