"""Phase 1 (CSRF): double-submit helpers gate mutating requests only."""
import pytest

from bot.web import _csrf_required, _csrf_ok   # pure helpers added in Phase 2


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutating_methods_require_csrf(method):
    assert _csrf_required(method) is True


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_do_not_require_csrf(method):
    assert _csrf_required(method) is False


def test_csrf_ok_when_tokens_present_and_equal():
    assert _csrf_ok("k7Qabc", "k7Qabc") is True


@pytest.mark.parametrize("header,cookie", [
    (None, "abc"), ("abc", None), (None, None), ("", ""), ("abc", "xyz"),
])
def test_csrf_rejects_missing_or_mismatched(header, cookie):
    assert _csrf_ok(header, cookie) is False


# --------------------------------------------------------------------------
# Request-level: the guard as create_app actually wires it.
#
# Everything above tests the pure helpers. Nothing exercised the
# @app.before_request that calls them, so removing the guard from create_app —
# or narrowing its path prefix — left the suite green while every mutating API
# route became reachable cross-site.
# --------------------------------------------------------------------------
from unittest.mock import MagicMock, patch   # noqa: E402


def _real_app():
    """The genuine app from create_app, with the bot and config stubbed."""
    from bot.web import create_app
    bot = MagicMock()
    bot.get_cog.return_value = None          # skip the WebNotifier sink wiring
    with patch("bot.web.config") as cfg:
        cfg.web_session_secret = "x" * 40
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        cfg.web_attachment_max_bytes = 1024
        cfg.web_frontend_dist = ""
        return create_app(bot)


@pytest.mark.asyncio
async def test_post_without_csrf_token_is_rejected_by_the_app():
    client = _real_app().test_client()
    resp = await client.post("/api/hunts", json={})
    assert resp.status_code == 403
    assert (await resp.get_json())["error"] == "csrf"


@pytest.mark.asyncio
async def test_post_with_matching_csrf_token_passes_the_guard():
    """Must get *past* the CSRF guard — the route then refuses it for its own
    reasons (no session), which is a different failure than 403 csrf. Without
    this, a guard that rejected everything unconditionally would look correct."""
    client = _real_app().test_client()
    client.set_cookie("localhost", "csrf_token", "tok123")
    resp = await client.post("/api/hunts", json={}, headers={"X-CSRF-Token": "tok123"})
    body = await resp.get_json()
    assert not (resp.status_code == 403 and (body or {}).get("error") == "csrf")


@pytest.mark.asyncio
async def test_get_is_not_csrf_gated_by_the_app():
    client = _real_app().test_client()
    resp = await client.get("/api/hunts")
    body = await resp.get_json()
    assert not (resp.status_code == 403 and (body or {}).get("error") == "csrf")


@pytest.mark.asyncio
async def test_app_issues_a_csrf_cookie_and_hardens_the_session_cookie():
    app = _real_app()
    resp = await app.test_client().get("/api/hunts")
    assert "csrf_token" in resp.headers.get("Set-Cookie", "")
    # The session cookie must stay unreadable from JS and same-site; the csrf
    # cookie is deliberately readable (the SPA echoes it back in the header).
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_SECURE"] is True   # https redirect uri
