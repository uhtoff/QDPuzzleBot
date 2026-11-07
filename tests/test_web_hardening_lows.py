"""Web-hardening Lows from the 2026-08-07 comprehensive review.

Covers, at request level where the defect lives:
  * the CSRF token must be bound to the session (a tossed cookie must not pass)
  * the activity WebSocket must check Origin
  * client-supplied ids must 400, not 500
  * only the API's deliberate error slugs may be echoed to clients
  * the negative-membership cache must be bounded
"""
import asyncio

import pytest
from unittest.mock import MagicMock, patch


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


# --------------------------------------------------------------------------
# CSRF: double-submit alone trusts any cookie the browser sends. A sibling host
# (evil.example.test) can set a csrf_token cookie for the parent domain AND
# knows its value, so it can satisfy header == cookie cross-site. Binding the
# token to the signed session closes that: the attacker cannot forge a session.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_tossed_csrf_cookie_does_not_pass_the_guard():
    """Header and cookie agree, but the token was never issued to this session."""
    client = _real_app().test_client()
    client.set_cookie("localhost", "csrf_token", "tossed-by-a-sibling-host")
    resp = await client.post("/api/hunts", json={},
                             headers={"X-CSRF-Token": "tossed-by-a-sibling-host"})
    assert resp.status_code == 403
    assert (await resp.get_json())["error"] == "csrf"



# --------------------------------------------------------------------------
# WebSocket Origin. Quart's CSRF-style before_request guard does not run for
# websockets, and browsers do not apply same-origin policy to the WS handshake:
# any site can open wss://dashboard/api/ws/activity with the victim's cookies
# and read every activity event they can see. Origin is the only handshake
# signal that identifies the calling site, so the route must check it itself.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_websocket_rejects_a_foreign_origin():
    from bot.web import api
    with patch("bot.web.api.session", {"user": {"id": 1}}), \
         patch("bot.web.api.config") as cfg, \
         patch.object(api, "resolve_membership") as resolve:
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        resolve.return_value = ({}, {})
        client = _real_app().test_client()
        async with client.websocket(
            "/api/ws/activity", headers={"Origin": "https://evil.test"}
        ) as ws:
            with pytest.raises(Exception):
                await ws.receive()
    # Rejected at the handshake: a logged-in session is present, so without an
    # Origin check the connection would proceed and resolve membership.
    resolve.assert_not_called()


@pytest.mark.asyncio
async def test_activity_websocket_accepts_its_own_origin():
    """Counterpart: the dashboard's own origin must get past the Origin check,
    or a check that closed every connection would look correct above."""
    from bot.web import api
    with patch("bot.web.api.session", {"user": {"id": 1}}), \
         patch("bot.web.api.config") as cfg, \
         patch.object(api, "resolve_membership") as resolve:
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        resolve.return_value = ({}, {})          # no guilds -> closed after the check
        client = _real_app().test_client()
        async with client.websocket(
            "/api/ws/activity", headers={"Origin": "https://example.test"}
        ) as ws:
            assert (await ws.receive_json())["type"] == "ready"
    resolve.assert_called()


async def _ws_with_origin(origin, **cfg_attrs):
    """Open the activity websocket with a given Origin; return whether the
    handshake got past the Origin check (membership was resolved)."""
    from bot.web import api
    with patch("bot.web.api.session", {"user": {"id": 1}}), \
         patch("bot.web.api.config") as cfg, \
         patch.object(api, "resolve_membership") as resolve:
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        for k, v in cfg_attrs.items():
            setattr(cfg, k, v)
        resolve.return_value = ({}, {})
        client = _real_app().test_client()
        async with client.websocket(
            "/api/ws/activity", headers={"Origin": origin}
        ) as ws:
            try:
                await ws.receive_json()
            except Exception:
                pass
    return resolve.called


@pytest.mark.asyncio
async def test_ws_origin_accepts_listed_extension():
    assert await _ws_with_origin(
        "chrome-extension://abc",
        web_extension_origins=["chrome-extension://abc"],
    )


@pytest.mark.asyncio
async def test_ws_origin_rejects_unlisted_extension():
    assert not await _ws_with_origin(
        "chrome-extension://evil",
        web_extension_origins=["chrome-extension://abc"],
    )
    # No list configured at all: nothing extra is admitted.
    assert not await _ws_with_origin(
        "chrome-extension://abc", web_extension_origins=[]
    )


# --------------------------------------------------------------------------
# The negative-membership cache is keyed on (guild_id, user_id) and nothing ever
# removed an entry: expired ones were read straight past but left in the dict.
# It is written on every failed fetch_member, so it grows without bound for the
# lifetime of the process — a slow leak on a long-running bot.
# --------------------------------------------------------------------------

def test_not_member_cache_is_bounded():
    from bot.web import auth
    original = dict(auth._not_member_cache)
    auth._not_member_cache.clear()
    try:
        for user_id in range(auth._NOT_MEMBER_CACHE_MAX * 2):
            auth._remember_not_member((1, user_id))
        assert len(auth._not_member_cache) <= auth._NOT_MEMBER_CACHE_MAX
    finally:
        auth._not_member_cache.clear()
        auth._not_member_cache.update(original)


def test_not_member_cache_keeps_the_newest_entries():
    """Bounding by clearing everything would satisfy the size cap while throwing
    away the cache's whole purpose on every overflow; the most recent misses —
    the ones a polling dashboard is about to ask for again — must survive."""
    from bot.web import auth
    original = dict(auth._not_member_cache)
    auth._not_member_cache.clear()
    try:
        for user_id in range(auth._NOT_MEMBER_CACHE_MAX + 10):
            auth._remember_not_member((1, user_id))
        newest = auth._NOT_MEMBER_CACHE_MAX + 9
        assert (1, newest) in auth._not_member_cache
        assert (1, 0) not in auth._not_member_cache
        # Overflowing by 10 must cost about 10 entries, not the whole cache:
        # emptying it on overflow also satisfies "newest kept, oldest gone", so
        # without this the size assertion is the only thing telling them apart.
        assert len(auth._not_member_cache) > auth._NOT_MEMBER_CACHE_MAX - 100
    finally:
        auth._not_member_cache.clear()
        auth._not_member_cache.update(original)


# --------------------------------------------------------------------------
# `except RuntimeError as exc: return {"error": str(exc)}` is how the routes
# surface the service layer's deliberate slugs ("already_meta",
# "no_category_space") — that vocabulary is the API contract and the SPA keys on
# it. But RuntimeError is a *builtin*: an unintended one raised by a library
# deep in the call stack lands in the same handler and echoes its internal text
# verbatim to the client. Echo the vocabulary, not whatever text turns up.
# --------------------------------------------------------------------------

def test_deliberate_error_slugs_are_passed_through():
    from bot.web.actions import _error_slug
    for slug in ("already_meta", "no_category_space", "sheet_not_found", "guild_full"):
        assert _error_slug(RuntimeError(slug)) == slug


def test_an_internal_message_is_not_echoed_to_the_client():
    from bot.web.actions import _error_slug
    leaky = RuntimeError(
        "(1054, \"Unknown column 'foo' in 'field list'\") at /srv/puzzbot/bot/store/mysqldb.py"
    )
    assert _error_slug(leaky) == "internal_error"


@pytest.mark.asyncio
async def test_activity_websocket_pings_when_idle_and_refreshes_on_its_own_cadence():
    """An idle socket gets {"type":"ping"} every ping interval (keeps the
    extension service worker alive); membership is re-resolved only once the
    (separate, longer) refresh interval has elapsed."""
    from bot.web import api
    with patch("bot.web.api.session", {"user": {"id": 1}}), \
         patch("bot.web.api.config") as cfg, \
         patch.object(api, "_WS_PING_SECONDS", 0.02), \
         patch.object(api, "_WS_AUTH_REFRESH_SECONDS", 0.11), \
         patch.object(api, "resolve_membership") as resolve:
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        resolve.return_value = ({1: object()}, {})
        client = _real_app().test_client()
        async with client.websocket(
            "/api/ws/activity", headers={"Origin": "https://example.test"}
        ) as ws:
            assert (await asyncio.wait_for(ws.receive_json(), 2))["type"] == "ready"
            assert resolve.call_count == 1
            for _ in range(3):
                assert await asyncio.wait_for(ws.receive_json(), 2) == {"type": "ping"}
            assert resolve.call_count == 1      # several pings, no refresh yet
            for _ in range(8):
                assert await asyncio.wait_for(ws.receive_json(), 2) == {"type": "ping"}
            assert resolve.call_count >= 2      # refresh happened after its interval
