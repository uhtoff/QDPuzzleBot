"""Tests for bot.utils.urls helpers."""
from bot.utils import urls


def test_puzzle_url_slug_ascii_name():
    assert urls.puzzle_url_slug("The Big Puzzle") == "the-big-puzzle"


def test_puzzle_url_slug_drops_punctuation():
    assert urls.puzzle_url_slug("Number 42: Go!") == "number-42-go"


def test_puzzle_url_slug_transliterates_non_ascii():
    # Discord's clean_channel_name would pass these through unchanged, leaking
    # accents/emoji/em-dashes into the URL. slugify transliterates to ASCII.
    assert urls.puzzle_url_slug("Café ☕") == "cafe"
    assert urls.puzzle_url_slug("naïve façade") == "naive-facade"
    assert urls.puzzle_url_slug("Puzzle — Two") == "puzzle-two"


def test_puzzle_url_slug_honours_separator():
    assert urls.puzzle_url_slug("The Big Puzzle", sep="_") == "the_big_puzzle"


def test_puzzle_url_slug_empty_when_no_ascii_content():
    assert urls.puzzle_url_slug("☕🎉") == ""
