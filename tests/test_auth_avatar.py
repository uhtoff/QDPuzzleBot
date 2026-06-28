from bot.web.auth import discord_avatar_url


def test_static_avatar_is_png_with_size():
    url = discord_avatar_url(123456789012345678, "abc123")
    assert url == "https://cdn.discordapp.com/avatars/123456789012345678/abc123.png?size=64"


def test_animated_avatar_is_gif():
    url = discord_avatar_url(123456789012345678, "a_deadbeef")
    assert url == "https://cdn.discordapp.com/avatars/123456789012345678/a_deadbeef.gif?size=64"


def test_no_avatar_returns_none():
    assert discord_avatar_url(123456789012345678, None) is None
    assert discord_avatar_url(123456789012345678, "") is None
