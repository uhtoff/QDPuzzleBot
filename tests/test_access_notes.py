"""Short-lived notes the bot leaves for itself so the role-change listener can
tell its own changes from hand edits."""
from unittest.mock import patch

from bot.core import access_notes


def setup_function(_):
    access_notes._notes.clear()


def test_take_returns_the_note_once():
    access_notes.expect(1, 2, 3, "button")
    assert access_notes.take(1, 2, 3) == "button"
    assert access_notes.take(1, 2, 3) is None


def test_notes_are_per_guild_user_and_key():
    access_notes.expect(1, 2, 3, "button")
    assert access_notes.take(1, 2, 4) is None
    assert access_notes.take(1, 9, 3) is None
    assert access_notes.take(9, 2, 3) is None
    assert access_notes.take(1, 2, 3) == "button"


def test_a_note_expires_after_a_minute():
    with patch.object(access_notes, "_now", return_value=100.0):
        access_notes.expect(1, 2, 3, "button")
    with patch.object(access_notes, "_now", return_value=161.0):
        assert access_notes.take(1, 2, 3) is None


def test_a_note_just_inside_the_minute_is_taken():
    with patch.object(access_notes, "_now", return_value=100.0):
        access_notes.expect(1, 2, 3, "button")
    with patch.object(access_notes, "_now", return_value=159.0):
        assert access_notes.take(1, 2, 3) == "button"


def test_expect_prunes_expired_notes():
    with patch.object(access_notes, "_now", return_value=100.0):
        access_notes.expect(1, 2, 3, "button")
    with patch.object(access_notes, "_now", return_value=200.0):
        access_notes.expect(1, 2, 4, "self")
    assert (1, 2, 3) not in access_notes._notes
    assert (1, 2, 4) in access_notes._notes


def test_kick_key():
    access_notes.expect(1, 2, "kick", "hub_role_lost")
    assert access_notes.take(1, 2, 3) is None
    assert access_notes.take(1, 2, "kick") == "hub_role_lost"
