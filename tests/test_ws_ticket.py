"""Single-use ticket auth for the extension's activity websocket.

Chrome does not send the SameSite=Lax session cookie on a websocket opened from
an MV3 service worker, so the extension fetches a ticket over HTTP instead.
"""
import pytest
from unittest.mock import MagicMock, patch

EXT = "chrome-extension://abc"


@pytest.fixture(autouse=True)
def _secret_and_clean_seen():
    from bot.web import ws_ticket
    with patch("bot.web.ws_ticket.config") as cfg:
        cfg.web_session_secret = "s" * 40
        ws_ticket._seen.clear()
        yield
        ws_ticket._seen.clear()


def _real_app():
    from bot.web import create_app
    bot = MagicMock()
    bot.get_cog.return_value = None
    with patch("bot.web.config") as cfg:
        cfg.web_session_secret = "x" * 40
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        cfg.web_attachment_max_bytes = 1024
        cfg.web_frontend_dist = ""
        return create_app(bot)


def test_round_trip_returns_the_user_id():
    from bot.web import ws_ticket
    assert ws_ticket.redeem(ws_ticket.issue(42)) == "42"


def test_tickets_are_distinct_per_issue():
    from bot.web import ws_ticket
    assert ws_ticket.issue(1) != ws_ticket.issue(1)


def test_tampered_ticket_rejected():
    from bot.web import ws_ticket
    t = ws_ticket.issue(1)
    flipped = t[:-2] + ("AA" if not t.endswith("AA") else "BB")
    assert ws_ticket.redeem(flipped) is None
    assert ws_ticket.redeem("garbage") is None
    assert ws_ticket.redeem("") is None


def test_ticket_signed_with_another_secret_rejected():
    from bot.web import ws_ticket
    t = ws_ticket.issue(1)
    with patch("bot.web.ws_ticket.config") as cfg:
        cfg.web_session_secret = "o" * 40
        assert ws_ticket.redeem(t) is None


def test_expired_ticket_rejected():
    from bot.web import ws_ticket
    t = ws_ticket.issue(1)
    with patch("itsdangerous.timed.time.time", return_value=__import__("time").time() + 61):
        assert ws_ticket.redeem(t) is None


def test_ticket_is_single_use():
    from bot.web import ws_ticket
    t = ws_ticket.issue(7)
    assert ws_ticket.redeem(t) == "7"
    assert ws_ticket.redeem(t) is None


@pytest.mark.asyncio
async def test_ticket_route_requires_login():
    client = _real_app().test_client()
    resp = await client.get("/api/extension/ws-ticket")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ticket_route_issues_a_redeemable_ticket():
    from bot.web import ws_ticket
    with patch("bot.web.api.session", {"user": {"id": 99}}), \
         patch("bot.web.auth.session", {"user": {"id": 99}}):
        client = _real_app().test_client()
        resp = await client.get("/api/extension/ws-ticket")
    assert resp.status_code == 200
    assert ws_ticket.redeem((await resp.get_json())["ticket"]) == "99"


async def _open(origin, ticket, session_user=None):
    """Open the activity websocket; return the user id resolve_membership got
    (None if the handshake never got past auth)."""
    from bot.web import api
    url = "/api/ws/activity" + (f"?ticket={ticket}" if ticket is not None else "")
    with patch("bot.web.api.session", {"user": session_user} if session_user else {}), \
         patch("bot.web.api.config") as cfg, \
         patch.object(api, "resolve_membership") as resolve:
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        cfg.web_extension_origins = [EXT]
        resolve.return_value = ({}, {})
        client = _real_app().test_client()
        async with client.websocket(url, headers={"Origin": origin}) as ws:
            try:
                await ws.receive_json()
            except Exception:
                pass
    return resolve.call_args.args[0] if resolve.called else None


@pytest.mark.asyncio
async def test_ws_listed_extension_origin_with_ticket_and_no_session_proceeds():
    from bot.web import ws_ticket
    assert await _open(EXT, ws_ticket.issue(1234)) == 1234


@pytest.mark.asyncio
async def test_ws_unlisted_origin_with_valid_ticket_is_closed():
    from bot.web import ws_ticket
    # Dashboard origin passes the origin gate but is not an extension origin.
    assert await _open("https://example.test", ws_ticket.issue(1)) is None


@pytest.mark.asyncio
async def test_ws_listed_origin_with_bad_ticket_is_closed():
    assert await _open(EXT, "garbage") is None
    assert await _open(EXT, None) is None


@pytest.mark.asyncio
async def test_ws_reused_ticket_is_closed():
    from bot.web import ws_ticket
    t = ws_ticket.issue(5)
    assert await _open(EXT, t) == 5
    assert await _open(EXT, t) is None


@pytest.mark.asyncio
async def test_ws_session_path_unchanged_and_ignores_ticket():
    assert await _open("https://example.test", None, session_user={"id": 8}) == 8


def test_reuse_rejected_even_after_pruning_runs():
    """Nonce expiry shares the signature's wall clock: a jump in the monotonic
    clock (the old expiry basis) must not free a nonce whose ticket is still valid."""
    import time
    from bot.web import ws_ticket
    t = ws_ticket.issue(3)
    assert ws_ticket.redeem(t) == "3"
    with patch("time.monotonic", return_value=time.monotonic() + 10_000):
        assert ws_ticket.redeem(t) is None


@pytest.mark.asyncio
async def test_ws_ticket_socket_drops_events_the_user_cannot_see():
    from bot.web import api, events, ws_ticket
    ticket = ws_ticket.issue(11)

    async def visible(ev, user_id, guild_id):
        return ev["channel_id"] == "ok"

    with patch("bot.web.api.session", {}), \
         patch("bot.web.api.config") as cfg, \
         patch.object(api, "resolve_membership") as resolve, \
         patch.object(api, "_activity_event_visible", side_effect=visible):
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        cfg.web_extension_origins = [EXT]
        resolve.return_value = ({42}, {})
        client = _real_app().test_client()
        async with client.websocket(
            f"/api/ws/activity?ticket={ticket}", headers={"Origin": EXT}
        ) as ws:
            assert (await ws.receive_json())["type"] == "ready"
            base = {"type": "message", "guild_id": 42}
            events.publish({**base, "channel_id": "hidden"})
            events.publish({**base, "channel_id": "ok"})
            got = await ws.receive_json()
    assert got["channel_id"] == "ok"


@pytest.mark.asyncio
async def test_ws_session_wins_and_does_not_consume_the_ticket():
    from bot.web import ws_ticket
    t = ws_ticket.issue(5)
    assert await _open(EXT, t, session_user={"id": 8}) == 8
    assert ws_ticket.redeem(t) == "5"


MOZ = "moz-extension://0123abcd-4567-89ef-0123-456789abcdef"


@pytest.mark.asyncio
async def test_ws_firefox_origin_with_ticket_proceeds_without_listing():
    # Firefox gives each install a random moz-extension UUID, so it is never listed.
    from bot.web import ws_ticket
    assert await _open(MOZ, ws_ticket.issue(77)) == 77


@pytest.mark.asyncio
async def test_ws_firefox_origin_without_ticket_is_closed():
    assert await _open(MOZ, None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [
    "moz-extension://abc",
    MOZ + "/x",
    "moz-extension://0123ABCD-4567-89EF-0123-456789ABCDEF",
    MOZ + ".evil.test",
    "https://" + MOZ[len("moz-extension://"):],
])
async def test_ws_malformed_firefox_origin_is_closed(origin):
    from bot.web import ws_ticket
    assert await _open(origin, ws_ticket.issue(3)) is None


def test_is_extension_origin_unit():
    from bot.web import api
    with patch("bot.web.api.config") as cfg:
        cfg.web_extension_origins = [EXT]
        assert api._is_extension_origin(EXT)
        assert api._is_extension_origin(MOZ)
        assert not api._is_extension_origin(None)
        assert not api._is_extension_origin("chrome-extension://other")
