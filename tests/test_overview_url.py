"""_overview_url must point at the SPA's client-side hunt route.

The dashboard serves hunts at /hunts/<uid> (see bot/web/__init__.py), but the
overview link in puzzle embeds used the legacy ?uid=<uid> query form. It now
builds {website_url}/hunts/<uid>, tolerating a trailing slash on website_url.
"""
from types import SimpleNamespace

from bot.cogs.puzzles import Puzzles


def _url(website_url, uid=None, hid=0):
    cog = Puzzles.__new__(Puzzles)
    guild_data = SimpleNamespace(website_url=website_url)
    hunt = SimpleNamespace(uid=uid, id=hid)
    return cog._overview_url(hunt=hunt, guild_data=guild_data)


def test_uses_hunts_path_with_uid():
    assert _url("https://puzz.example.com", uid="ABC123") == "https://puzz.example.com/hunts/ABC123"


def test_strips_trailing_slash_on_website_url():
    assert _url("https://puzz.example.com/", uid="ABC123") == "https://puzz.example.com/hunts/ABC123"


def test_falls_back_to_numeric_id_when_no_uid():
    assert _url("https://puzz.example.com", uid="", hid=42) == "https://puzz.example.com/hunts/42"


def test_no_guild_data_does_not_raise():
    """Edge callers (ctx=None) may have no guild settings (e.g. a backfilled row
    not yet cached). _overview_url must degrade to a relative path instead of
    raising AttributeError, which would silently kill the whole welcome embed."""
    cog = Puzzles.__new__(Puzzles)
    cog.environment = {}
    hunt = SimpleNamespace(uid="ABC123", id=0)
    assert cog._overview_url(ctx=None, hunt=hunt, guild_data=None) == "/hunts/ABC123"
