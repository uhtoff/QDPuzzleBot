"""M3.5: pure channel-adapter helpers, ctx-free."""
from unittest.mock import MagicMock

import pytest

from bot.core import discord_channels   # <-- does not exist yet → right reason


def test_clean_name_lowercases_and_hyphenates():
    assert discord_channels.clean_name("  Big Red Box ") == "big-red-box"


def test_clean_name_strips_matching_quotes():
    assert discord_channels.clean_name('"Quote Me"') == "quote-me"


def test_clean_name_keeps_unmatched_leading_quote():
    # Only a matching pair of surrounding quotes is stripped (source: clean_name :267).
    assert discord_channels.clean_name('"Quote Me') == '"quote-me'


def test_solve_divider_position_returns_index_when_present():
    cat = MagicMock()
    ch = MagicMock()
    ch.name = discord_channels.SOLVE_DIVIDER
    ch.position = 7
    cat.channels = [ch]
    # Source get_solve_divider_position :927 returns the divider's .position.
    assert discord_channels.solve_divider_position(cat) == 7


def test_solve_divider_position_returns_false_when_absent():
    cat = MagicMock()
    other = MagicMock()
    other.name = "general"
    cat.channels = [other]
    # Source returns the literal False sentinel (not None) when no divider exists.
    assert discord_channels.solve_divider_position(cat) is False


def test_first_channel_position_returns_min_position():
    cat = MagicMock()
    a = MagicMock(); a.position = 5
    b = MagicMock(); b.position = 2
    c = MagicMock(); c.position = 9
    cat.channels = [a, b, c]
    # Source get_first_channel :920 returns the smallest channel.position.
    assert discord_channels.first_channel_position(cat) == 2


def test_first_channel_position_returns_false_when_empty():
    cat = MagicMock()
    cat.channels = []
    assert discord_channels.first_channel_position(cat) is False
