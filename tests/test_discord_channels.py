"""M3.5: pure channel-adapter helpers, ctx-free."""
from unittest.mock import MagicMock


from bot.core import discord_channels   # <-- does not exist yet → right reason


def test_clean_name_lowercases_and_hyphenates():
    assert discord_channels.clean_name("  Big Red Box ") == "big-red-box"


def test_clean_name_strips_quotes_as_ascii_punctuation():
    # Quotes are ASCII punctuation, so Discord strips them wherever they appear.
    assert discord_channels.clean_name('"Quote Me"') == "quote-me"
    assert discord_channels.clean_name('"Quote Me') == "quote-me"


def test_clean_name_strips_ascii_punctuation_but_keeps_hyphen_and_underscore():
    assert discord_channels.clean_name("Monster Mash!") == "monster-mash"
    assert discord_channels.clean_name("The Emperor's New Clothes") == "the-emperors-new-clothes"
    assert discord_channels.clean_name("foo_bar-baz") == "foo_bar-baz"
    assert discord_channels.clean_name("a.b~c,d") == "abcd"


def test_clean_name_keeps_non_ascii_emoji_dashes_and_pound():
    # Non-ASCII passes through: emoji, em/en dashes, £, styled fonts.
    assert discord_channels.clean_name("👻 Monster Mash") == "👻-monster-mash"
    assert discord_channels.clean_name("Round A — solved") == "round-a-—-solved"
    assert discord_channels.clean_name("bit – piece") == "bit-–-piece"
    assert discord_channels.clean_name("£5 Note") == "£5-note"


def test_clean_name_collapses_and_trims_hyphens():
    assert discord_channels.clean_name("Round --  Two") == "round-two"
    assert discord_channels.clean_name("!leading and trailing!") == "leading-and-trailing"


def test_clean_name_caps_at_100_chars_without_trailing_hyphen():
    result = discord_channels.clean_name("word " * 40)
    assert len(result) <= 100
    assert not result.endswith("-")


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
