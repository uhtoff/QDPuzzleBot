"""!canvas — links to the Canvas view. Drawing itself is web-only."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_cog, make_hunt


def _ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    return ctx


def _canvas(id=3, name="Extravaganza"):
    return SimpleNamespace(id=id, name=name)


@pytest.fixture
def cog():
    c = make_cog()
    # The Canvas view is on: !canvas only lists canvases then (off is covered in
    # test_canvas_toggle.py). Migration 061 made the switch; HuntData defaults off.
    c.get_hunt = MagicMock(return_value=make_hunt(id=10, canvas_enabled=True))
    return c


async def test_lists_the_hunts_canvases_with_a_link(cog):
    ctx = _ctx()
    with patch("bot.cogs.puzzles.CanvasJsonDb") as cdb, \
         patch("bot.cogs.puzzles.config") as cfg:
        cfg.web_frontend_url = "https://puzz.example/"
        cdb.get_by_hunt.return_value = [_canvas(), _canvas(4, "Second sheet")]
        await cog.canvas(cog, ctx)
    msg = ctx.send.await_args[0][0]
    # Both names, not just the first: a message built from canvases[0] would
    # still look right with one canvas.
    assert "Extravaganza" in msg
    assert "Second sheet" in msg
    assert "2 canvases" in msg
    # Trailing slash in the configured URL must not double up.
    assert "https://puzz.example/hunts/" in msg
    assert "example//hunts" not in msg


async def test_singular_wording_for_one_canvas(cog):
    ctx = _ctx()
    with patch("bot.cogs.puzzles.CanvasJsonDb") as cdb, \
         patch("bot.cogs.puzzles.config") as cfg:
        cfg.web_frontend_url = "https://puzz.example"
        cdb.get_by_hunt.return_value = [_canvas()]
        await cog.canvas(cog, ctx)
    msg = ctx.send.await_args[0][0]
    assert "1 canvas " in msg
    assert "canvases" not in msg


async def test_says_so_when_there_are_none(cog):
    ctx = _ctx()
    with patch("bot.cogs.puzzles.CanvasJsonDb") as cdb, \
         patch("bot.cogs.puzzles.config") as cfg:
        cfg.web_frontend_url = "https://puzz.example"
        cdb.get_by_hunt.return_value = []
        await cog.canvas(cog, ctx)
    msg = ctx.send.await_args[0][0]
    assert "No canvases yet" in msg
    # Still links, so the reader knows where to go and upload one.
    assert "https://puzz.example/hunts/" in msg


async def test_outside_a_hunt_it_refuses(cog):
    ctx = _ctx()
    cog.get_hunt = MagicMock(return_value=None)
    await cog.canvas(cog, ctx)
    assert "isn't part of a hunt" in ctx.send.await_args[0][0]


async def test_an_unconfigured_dashboard_url_says_so(cog):
    """Otherwise it posts a link to "/hunts/UID", which goes nowhere."""
    ctx = _ctx()
    with patch("bot.cogs.puzzles.config") as cfg:
        cfg.web_frontend_url = ""
        await cog.canvas(cog, ctx)
    assert "isn't configured" in ctx.send.await_args[0][0]
