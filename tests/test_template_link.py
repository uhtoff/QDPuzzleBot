"""Slice 5b: template-link validator and the two commands."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.core import template_link
from tests.conftest import make_cog, make_ctx


@pytest.mark.parametrize("text", [
    "https://discord.new/abc123",
    "discord.new/abc123",
    "https://discord.com/template/abc123",
    "http://www.discord.com/template/abc123",
    "  discord.new/abc123 \n",
])
def test_accepts_the_three_forms(text):
    assert template_link.normalise(text) == "https://discord.new/abc123"


@pytest.mark.parametrize("text", [
    "https://evil.example/discord.new/abc",
    "discord.new/abc?x=1",
    "discord.new/abc/extra",
    "discord.new/",
    "discord.gg/abc",
    "",
])
def test_rejects_lookalikes(text):
    assert template_link.normalise(text) is None


def test_browser_url_and_code():
    assert template_link.code("https://discord.new/abc123") == "abc123"
    assert template_link.browser_url("https://discord.new/abc123") == "https://discord.com/template/abc123"


def _sent(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


def _cog():
    cog = make_cog()
    cog.check_is_bot_channel = AsyncMock(return_value=True)
    return cog


async def _set(link=None):
    cog, ctx = _cog(), make_ctx()
    settings = MagicMock(template_link="https://discord.new/old")
    with patch("bot.cogs.puzzles.GuildSettingsDb") as gdb:
        gdb.get.return_value = settings
        if link is None:
            await Puzzles.set_template_link.callback(cog, ctx)
        else:
            await Puzzles.set_template_link.callback(cog, ctx, link)
    return ctx, settings, gdb


async def test_set_template_link_stores_canonical_and_replies_with_it():
    ctx, settings, gdb = await _set("https://discord.com/template/abc123")
    assert settings.template_link == "https://discord.new/abc123"
    gdb.commit.assert_called_once_with(settings)
    assert "https://discord.new/abc123" in _sent(ctx)


async def test_set_template_link_refuses_a_bad_link():
    ctx, settings, gdb = await _set("https://evil.example/discord.new/abc")
    gdb.commit.assert_not_called()
    assert settings.template_link == "https://discord.new/old"
    assert "discord.com/template/<code>" in _sent(ctx)


async def test_set_template_link_with_no_argument_clears_it():
    ctx, settings, gdb = await _set()
    assert settings.template_link == ""
    gdb.commit.assert_called_once_with(settings)
    assert "cleared" in _sent(ctx)


async def _show(link, found):
    cog, ctx = make_cog(), make_ctx()
    with patch("bot.cogs.puzzles.GuildSettingsDb") as gdb, \
         patch("bot.cogs.puzzles.hub_channel.find", return_value=found) as find, \
         patch("bot.cogs.puzzles.hub_channel.post", new=AsyncMock(return_value=True)) as post:
        gdb.get.return_value = MagicMock(template_link=link)
        await Puzzles.show_template_link.callback(cog, ctx)
    return ctx, find, post


async def test_show_template_link_posts_link_and_fallback_in_the_admin_channel():
    channel = MagicMock()
    channel.name = "admin-room"
    ctx, _, post = await _show("https://discord.new/abc123", (channel, ""))
    post.assert_awaited_once()
    text = post.await_args.args[1]
    assert "https://discord.new/abc123" in text
    assert "https://discord.com/template/abc123" in text
    assert "#admin-room" in _sent(ctx)
    assert "discord.new" not in _sent(ctx)


async def test_show_template_link_refused_channel_posts_nothing():
    ctx, _, post = await _show("https://discord.new/abc123",
                               (None, "#admin is visible to everyone on Hub"))
    post.assert_not_awaited()
    assert "visible to everyone" in _sent(ctx)
    assert "discord.new" not in _sent(ctx)


async def test_show_template_link_when_none_is_set():
    ctx, _, post = await _show("", (MagicMock(), ""))
    post.assert_not_awaited()
    assert "No template link is set" in _sent(ctx)
    assert "!set_template_link" in _sent(ctx)
