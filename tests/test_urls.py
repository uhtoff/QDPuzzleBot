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


def test_normalize_external_url_adds_https_when_no_scheme():
    assert urls.normalize_external_url("example.com/hunt") == "https://example.com/hunt"


def test_normalize_external_url_leaves_https_alone():
    assert urls.normalize_external_url("https://example.com/hunt") == "https://example.com/hunt"


def test_normalize_external_url_leaves_http_alone():
    assert urls.normalize_external_url("http://example.com/hunt") == "http://example.com/hunt"


def test_normalize_external_url_leaves_other_schemes_alone():
    assert urls.normalize_external_url("ftp://example.com/hunt") == "ftp://example.com/hunt"


def test_normalize_external_url_strips_surrounding_whitespace():
    assert urls.normalize_external_url("  example.com  ") == "https://example.com"


def test_normalize_external_url_empty_stays_empty():
    assert urls.normalize_external_url("") == ""
