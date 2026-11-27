"""Tests for Google OAuth linking routes, /api/auth/me status, and lazy first-load sharing."""
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

from bot.utils import config
from bot.web import create_app


def _make_app(monkeypatch, *, google_enabled: bool = True):
    monkeypatch.setattr(config, "web_session_secret", "x" * 40)
    monkeypatch.setattr(config, "web_oauth_redirect_uri", "https://dash.test/api/auth/callback")
    monkeypatch.setattr(config, "web_frontend_url", "https://dash.test")
    if google_enabled:
        monkeypatch.setattr(config, "web_google_oauth_client_id", "gid-123")
        monkeypatch.setattr(config, "web_google_oauth_client_secret", "gsecret-456")
        monkeypatch.setattr(config, "web_google_oauth_redirect_uri", "https://dash.test/api/auth/google/callback")
    else:
        monkeypatch.setattr(config, "web_google_oauth_client_id", "")
        monkeypatch.setattr(config, "web_google_oauth_client_secret", "")
        monkeypatch.setattr(config, "web_google_oauth_redirect_uri", "")
    bot = MagicMock()
    bot.get_cog.return_value = None
    bot.get_guild.return_value = None
    bot.guilds = []
    return create_app(bot), bot


async def _login_session(client, user_id: int = 12345, csrf: str = "tok-csrf"):
    async with client.session_transaction() as sess:
        sess["user"] = {"id": user_id, "username": "alice", "global_name": "Alice", "avatar": None}
        sess["csrf_token"] = csrf


async def test_me_and_link_when_google_oauth_disabled(monkeypatch):
    app, _ = _make_app(monkeypatch, google_enabled=False)
    client = app.test_client()
    await _login_session(client)

    resp = await client.get("/api/auth/me")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["user"]["google_linking_enabled"] is False
    assert body["user"]["google_account"] is None

    link_resp = await client.get("/api/auth/google/link")
    assert link_resp.status_code == 503


async def test_me_includes_linked_google_account(monkeypatch):
    app, _ = _make_app(monkeypatch, google_enabled=True)
    client = app.test_client()
    await _login_session(client, user_id=555)

    with patch("bot.web.data.get_user_google_link", new=AsyncMock(return_value={
        "discord_user_id": 555,
        "google_sub": "sub-1",
        "google_email": "alice@gmail.com",
        "linked_at": "2026-11-25T10:00:00+00:00",
    })):
        resp = await client.get("/api/auth/me")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["user"]["google_linking_enabled"] is True
    assert body["user"]["google_account"] == {
        "email": "alice@gmail.com",
        "linked_at": "2026-11-25T10:00:00+00:00",
    }


async def test_google_link_requires_login_and_sets_pkce_state(monkeypatch):
    app, _ = _make_app(monkeypatch, google_enabled=True)
    client = app.test_client()

    unauth = await client.get("/api/auth/google/link")
    assert unauth.status_code == 401

    await _login_session(client)
    resp = await client.get("/api/auth/google/link?next=/hunts/ABC?tab=list")
    assert resp.status_code == 302
    loc = urlsplit(resp.headers["Location"])
    assert loc.netloc == "accounts.google.com"
    q = parse_qs(loc.query)
    assert q["scope"] == ["openid email"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["prompt"] == ["select_account"]
    assert q["client_id"] == ["gid-123"]
    assert q["redirect_uri"] == ["https://dash.test/api/auth/google/callback"]

    async with client.session_transaction() as sess:
        assert sess["google_oauth_state"] == q["state"][0]
        assert sess["google_oauth_verifier"]
        assert sess["google_link_next"] == "/hunts/ABC?tab=list"


async def test_google_link_rejects_open_redirect_next(monkeypatch):
    app, _ = _make_app(monkeypatch, google_enabled=True)
    client = app.test_client()
    await _login_session(client)

    resp = await client.get("/api/auth/google/link?next=//evil.example/steal")
    assert resp.status_code == 302
    async with client.session_transaction() as sess:
        assert "google_link_next" not in sess


async def test_google_callback_rejects_bad_state_and_handles_cancel(monkeypatch):
    app, _ = _make_app(monkeypatch, google_enabled=True)
    client = app.test_client()
    await _login_session(client)

    # Bad state -> 400
    async with client.session_transaction() as sess:
        sess["google_oauth_state"] = "expected"
        sess["google_oauth_verifier"] = "ver"
    bad = await client.get("/api/auth/google/callback?code=c1&state=wrong")
    assert bad.status_code == 400

    # User clicked Cancel -> redirects back with google_link=cancelled
    async with client.session_transaction() as sess:
        sess["google_oauth_state"] = "expected2"
        sess["google_oauth_verifier"] = "ver2"
        sess["google_link_next"] = "/hunts/XYZ"
    cancelled = await client.get("/api/auth/google/callback?error=access_denied&state=expected2")
    assert cancelled.status_code == 302
    assert cancelled.headers["Location"] == "https://dash.test/hunts/XYZ?google_link=cancelled"


async def test_google_callback_rejects_unverified_email(monkeypatch):
    app, _ = _make_app(monkeypatch, google_enabled=True)
    client = app.test_client()
    await _login_session(client)
    async with client.session_transaction() as sess:
        sess["google_oauth_state"] = "st1"
        sess["google_oauth_verifier"] = "v1"

    with patch("bot.web.google_auth._exchange_code", new=AsyncMock(return_value={"access_token": "tok"})), \
            patch("bot.web.google_auth._fetch_userinfo", new=AsyncMock(return_value={
                "sub": "s1", "email": "u@example.com", "email_verified": False,
            })), \
            patch("bot.web.google_auth.GoogleLinkDb") as gldb:
        resp = await client.get("/api/auth/google/callback?code=c1&state=st1")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://dash.test/?google_link=error"
    gldb.upsert.assert_not_called()


async def test_google_callback_links_replaces_old_and_schedules_preshare(monkeypatch):
    app, bot = _make_app(monkeypatch, google_enabled=True)
    client = app.test_client()
    await _login_session(client, user_id=777)
    async with client.session_transaction() as sess:
        sess["google_oauth_state"] = "st-ok"
        sess["google_oauth_verifier"] = "ver-ok"
        sess["google_link_next"] = "/hunts/H1?view=all"

    with patch("bot.web.google_auth._exchange_code", new=AsyncMock(return_value={"access_token": "secret-tok"})), \
            patch("bot.web.google_auth._fetch_userinfo", new=AsyncMock(return_value={
                "sub": "google-sub-9", "email": "Alice@Gmail.com", "email_verified": True,
            })), \
            patch("bot.web.google_auth.GoogleLinkDb") as gldb, \
            patch("bot.web.google_auth.drive_sharing.schedule_revoke_user_active_shares") as sched_revoke, \
            patch("bot.web.google_auth.drive_sharing.schedule_preshare_user_active_hunts") as sched_preshare:
        gldb.get.return_value = {"discord_user_id": 777, "google_email": "old@gmail.com"}
        resp = await client.get("/api/auth/google/callback?code=c-ok&state=st-ok")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://dash.test/hunts/H1?view=all&google_link=linked"
    gldb.upsert.assert_called_once_with(777, "google-sub-9", "alice@gmail.com")
    sched_revoke.assert_called_once_with(bot, 777, email="old@gmail.com")
    sched_preshare.assert_called_once_with(bot, 777)

    # Verify no Google token was saved into the session.
    async with client.session_transaction() as sess:
        assert "access_token" not in str(dict(sess))
        assert "secret-tok" not in str(dict(sess))


async def test_unlink_google_requires_csrf_and_schedules_revocation(monkeypatch):
    app, bot = _make_app(monkeypatch, google_enabled=True)
    client = app.test_client()
    await _login_session(client, user_id=888, csrf="csrf-123")

    no_csrf = await client.delete("/api/auth/google")
    assert no_csrf.status_code == 403

    with patch("bot.web.google_auth.GoogleLinkDb") as gldb, \
            patch("bot.web.google_auth.drive_sharing.schedule_revoke_user_active_shares") as sched_revoke:
        gldb.unlink.return_value = {"discord_user_id": 888, "google_email": "bob@gmail.com"}
        ok = await client.delete("/api/auth/google", headers={"X-CSRF-Token": "csrf-123"})

    assert ok.status_code == 200
    assert await ok.get_json() == {"ok": True, "unlinked": True}
    gldb.unlink.assert_called_once_with(888)
    sched_revoke.assert_called_once_with(bot, 888)


async def test_lazy_first_load_share_in_hunt_detail_and_by_sheet(monkeypatch):
    app, bot = _make_app(monkeypatch, google_enabled=True)
    client = app.test_client()
    await _login_session(client, user_id=321)

    hunt_row = {
        "id": 42, "uid": "H42", "name": "Test Hunt", "url": "https://h.test",
        "guild_id": 1, "role_id": 0, "roster_role_id": 0, "archived": 0,
        "google_sheet_id": "REF42", "solved_sheet_id": "", "sheet_mode": "files",
        "drive_folder_id": "FOLD42",
    }

    with patch("bot.web.api._load_visible_hunt", new=AsyncMock(return_value=(hunt_row, {"guild_snowflake": 10}))), \
            patch("bot.web.api.data.get_user_google_link", new=AsyncMock(return_value={
                "discord_user_id": 321, "google_email": "u@gmail.com",
            })), \
            patch("bot.web.api.data.get_user_shared_files", new=AsyncMock(side_effect=[
                {},                         # First load: not yet shared -> schedule
                {"FOLD42": "u@gmail.com"},  # Second load: already shared -> skip
            ])), \
            patch("bot.web.api.data.get_hunt_puzzles", new=AsyncMock(return_value=[])), \
            patch("bot.web.api.data.get_rollups_by_tag_group", new=AsyncMock(return_value={})), \
            patch("bot.web.api.data.get_all_tag_groups", new=AsyncMock(return_value=[])), \
            patch("bot.web.api.data.get_hunt_sheets", new=AsyncMock(return_value=[])), \
            patch("bot.web.api.user_can_manage", new=AsyncMock(return_value=False)), \
            patch("bot.web.api.MultiTeamDb.for_hunt", return_value=None), \
            patch("bot.web.api.drive_sharing.schedule_ensure_user_hunt_shared") as sched:
        r1 = await client.get("/api/hunts/H42")
        r2 = await client.get("/api/hunts/H42")

    assert r1.status_code == 200
    assert r2.status_code == 200
    sched.assert_called_once_with(bot, hunt_row, 321)


async def test_lazy_first_load_share_in_by_sheet(monkeypatch):
    app, bot = _make_app(monkeypatch, google_enabled=True)
    client = app.test_client()
    await _login_session(client, user_id=654)

    hunt_row = {
        "id": 99, "uid": "H99", "name": "Files Hunt", "guild_id": 1,
        "role_id": 0, "roster_role_id": 0, "archived": 0,
        "google_sheet_id": "REF99", "solved_sheet_id": "", "sheet_mode": "files",
        "drive_folder_id": "FOLD99",
    }
    puzzle_row = {
        "id": 7, "name": "Puz", "solved": 0, "channel_id": 500,
        "google_sheet_id": "PUZ_FILE", "on_solved_sheet": 0, "google_page_id": "0",
    }

    with patch("bot.web.api.data.find_puzzle_by_sheet", new=AsyncMock(return_value={
                "puzzle": puzzle_row, "hunt": hunt_row,
            })), \
            patch("bot.web.api.resolve_membership", new=AsyncMock(return_value=({10}, {10: set()}))), \
            patch("bot.web.api.data.get_guilds_map", new=AsyncMock(return_value={1: {"guild_snowflake": 10}})), \
            patch("bot.web.api.data.get_user_google_link", new=AsyncMock(return_value={
                "discord_user_id": 654, "google_email": "ext@gmail.com",
            })), \
            patch("bot.web.api.data.get_user_shared_files", new=AsyncMock(return_value={})), \
            patch("bot.web.api.drive_sharing.schedule_ensure_user_hunt_shared") as sched:
        resp = await client.get("/api/puzzles/by-sheet?spreadsheet=PUZ_FILE")

    assert resp.status_code == 200
    sched.assert_called_once_with(bot, hunt_row, 654)

