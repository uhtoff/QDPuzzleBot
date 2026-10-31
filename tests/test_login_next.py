"""/api/auth/login?next=: return to the page after OAuth, same-site paths only."""
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from bot.web.auth import safe_next_path


@pytest.mark.parametrize("value", ["/", "/guilds/5/setup", "/hunts/ab?x=1#y"])
def test_same_site_paths_are_allowed(value):
    assert safe_next_path(value) == value


@pytest.mark.parametrize("value", [
    None, "", "guilds/5", "//evil.example", "///evil.example", "https://evil.example/",
    "javascript:alert(1)", "/\\evil.example", "/ok\\path", "/a\nb", "/a\r\nSet-Cookie: x", "\\\\evil",
])
def test_everything_else_is_rejected(value):
    assert safe_next_path(value) is None


def _client(frontend=""):
    from bot.web import create_app
    bot = MagicMock()
    bot.get_cog.return_value = None
    with patch("bot.web.config") as cfg:
        cfg.web_session_secret = "x" * 40
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        cfg.web_attachment_max_bytes = 1024
        cfg.web_frontend_dist = ""
        app = create_app(bot)
    site = patch.multiple("bot.web.auth.config", create=True, web_oauth_client_id="cid",
                          web_oauth_client_secret="s", web_frontend_url=frontend,
                          web_oauth_redirect_uri="https://example.test/cb")
    return app.test_client(), site


async def _login_then_callback(client, next_value):
    url = "/api/auth/login" + (f"?next={next_value}" if next_value is not None else "")
    resp = await client.get(url)
    assert resp.status_code == 302
    state = parse_qs(urlparse(resp.headers["Location"]).query)["state"][0]
    with patch("bot.web.auth._exchange_code", return_value={"access_token": "t"}), \
         patch("bot.web.auth._fetch_user", return_value={"id": "7", "username": "u"}):
        return await client.get(f"/api/auth/callback?code=c&state={state}")


async def test_callback_returns_to_an_allowed_next_path():
    client, site = _client()
    with site:
        resp = await _login_then_callback(client, "/guilds/5/setup")
    assert resp.status_code == 302 and resp.headers["Location"] == "/guilds/5/setup"


async def test_callback_prefixes_the_configured_frontend_url():
    client, site = _client("https://dash.example/")
    with site:
        resp = await _login_then_callback(client, "/guilds/5/setup")
    assert resp.headers["Location"] == "https://dash.example/guilds/5/setup"


@pytest.mark.parametrize("bad", ["//evil.example", "https://evil.example/", "%5Cevil.example",
                                 "/%5Cevil.example", "javascript:alert(1)"])
async def test_callback_ignores_a_rejected_next(bad):
    client, site = _client()
    with site:
        resp = await _login_then_callback(client, bad)
    assert resp.status_code == 302 and resp.headers["Location"] == "/"


async def test_no_next_goes_home():
    client, site = _client()
    with site:
        resp = await _login_then_callback(client, None)
    assert resp.headers["Location"] == "/"


async def test_a_stale_next_does_not_leak_into_a_later_login():
    client, site = _client()
    with site:
        await client.get("/api/auth/login?next=/guilds/5/setup")
        resp = await _login_then_callback(client, None)
    assert resp.headers["Location"] == "/"
