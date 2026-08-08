"""Dashboard parity for the guild-scoped settings.

`!set_admin_roles` / `!set_organiser_roles` / `!show_auth_roles` and
`!update_settings` had no web equivalent, and no guild-scoped surface existed to
hang one on. These cover the two routes behind the new settings page.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_web_app, start_patches, stop_patches

GUILD_ROW_ID = 1
SNOWFLAKE = 123
RUNNERS_ID = 700
HELPERS_ID = 800
IMPOSTOR_ID = 999
EVERYONE_ID = 1000
FOREIGN_ID = 555_555_555_555_555_555


def _roles():
    return [
        SimpleNamespace(id=EVERYONE_ID, name="everyone"),
        SimpleNamespace(id=RUNNERS_ID, name="Hunt Runners"),
        SimpleNamespace(id=HELPERS_ID, name="Hunt Helpers"),
        SimpleNamespace(id=IMPOSTOR_ID, name="admin"),
    ]


@pytest.fixture
def web():
    guild = SimpleNamespace(id=SNOWFLAKE, name="Team A", roles=_roles())
    app = make_web_app("actions", cog=MagicMock(), get_guild=guild)

    settings = SimpleNamespace(
        id=3, guild_id=SNOWFLAKE, guild_name="Team A", website_url="",
        discord_bot_channel="bot-commands", discord_bot_emoji="",
        discord_use_voice_channels=False,
        drive_parent_id="DRIVE1", drive_resources_id="",
        admin_role_ids=str(RUNNERS_ID), organiser_role_ids=str(HELPERS_ID),
    )
    db = MagicMock()
    db.get.return_value = settings
    db.get_cached.return_value = settings

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions.web_data.get_guilds_map", AsyncMock(return_value={
            GUILD_ROW_ID: {"guild_snowflake": SNOWFLAKE, "guild_name": "Team A",
                           "website_url": ""},
        })),
        patch("bot.web.actions.resolve_member_role_ids",
              AsyncMock(return_value={EVERYONE_ID, RUNNERS_ID})),
        patch("bot.store.GuildSettingsDb", db),
        patch("bot.web.actions.GuildSettingsDb", db),
        patch("bot.web.actions._audit", AsyncMock()),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), db=db, settings=settings,
                              guild=guild)
    finally:
        stop_patches(patches)


# ------------------------------------------------------------------ GET

async def test_get_returns_settings_and_roles(web):
    resp = await web.client.get(f"/api/guilds/{GUILD_ROW_ID}/settings")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["settings"]["discord_bot_channel"] == "bot-commands"
    assert body["settings"]["drive_parent_id"] == "DRIVE1"
    assert {r["name"] for r in body["roles"]} == {
        "everyone", "Hunt Runners", "Hunt Helpers", "admin"}


async def test_every_discord_id_crosses_the_wire_as_a_string(web):
    """Role and guild snowflakes are 64-bit and exceed JS's safe integer range;
    a number here silently loses precision in the browser."""
    resp = await web.client.get(f"/api/guilds/{GUILD_ROW_ID}/settings")
    body = await resp.get_json()
    assert all(isinstance(r["id"], str) for r in body["roles"])
    assert isinstance(body["guild"]["guild_snowflake"], str)
    assert all(isinstance(i, str) for i in body["settings"]["admin_role_ids"])
    assert all(isinstance(i, str) for i in body["tiers"]["admin"]["role_ids"])
    assert all(isinstance(i, str) for i in body["your_role_ids"])


async def test_get_reports_each_tier_pinned_state_separately(web):
    web.settings.organiser_role_ids = ""      # pinned admin, unpinned organiser
    resp = await web.client.get(f"/api/guilds/{GUILD_ROW_ID}/settings")
    body = await resp.get_json()
    assert body["tiers"]["admin"]["pinned"] is True
    assert body["tiers"]["organiser"]["pinned"] is False
    # An unpinned tier resolves by NAME, so its effective ids come from the role
    # names — the organiser tier has no Organiser-named role here, so it is empty
    # (that emptiness is what the page warns about: the tier is open to everyone).
    assert body["tiers"]["organiser"]["role_ids"] == []
    assert body["tiers"]["admin"]["role_ids"] == [str(RUNNERS_ID)]


async def test_get_refuses_a_non_admin(web):
    with patch("bot.web.actions.require_admin",
               AsyncMock(return_value=({"error": "insufficient_role"}, 403))):
        resp = await web.client.get(f"/api/guilds/{GUILD_ROW_ID}/settings")
    assert resp.status_code == 403


async def test_get_404s_an_unknown_guild(web):
    resp = await web.client.get("/api/guilds/99/settings")
    assert resp.status_code == 404


# ------------------------------------------------------------------ PATCH

async def _patch(web, body):
    return await web.client.patch(f"/api/guilds/{GUILD_ROW_ID}/settings", json=body)


async def test_patch_stores_role_ids(web):
    resp = await _patch(web, {"admin_role_ids": [str(RUNNERS_ID), str(HELPERS_ID)]})
    assert resp.status_code == 200
    assert set(web.settings.admin_role_ids.split(",")) == {
        str(RUNNERS_ID), str(HELPERS_ID)}
    web.db.commit.assert_called_once_with(web.settings)


async def test_patch_stores_the_other_settings(web):
    resp = await _patch(web, {"discord_bot_channel": "puzzle-bot",
                              "discord_use_voice_channels": True})
    assert resp.status_code == 200
    assert web.settings.discord_bot_channel == "puzzle-bot"
    assert web.settings.discord_use_voice_channels is True


async def test_partial_patch_does_not_blank_untouched_columns(web):
    """GuildSettingsDb.commit writes EVERY column, so the route must
    load-modify-save; building a fresh row would wipe the rest of the guild's
    settings on any partial update."""
    await _patch(web, {"discord_bot_channel": "puzzle-bot"})
    assert web.settings.drive_parent_id == "DRIVE1"
    assert web.settings.admin_role_ids == str(RUNNERS_ID)


async def test_patch_rejects_unknown_keys(web):
    resp = await _patch(web, {"guild_name": "Renamed"})
    assert resp.status_code == 400
    web.db.commit.assert_not_called()


async def test_patch_rejects_a_role_not_in_this_guild(web):
    """A role id from another server must not be storable — it would gate the
    tier on something nobody here can hold."""
    resp = await _patch(web, {"admin_role_ids": [str(FOREIGN_ID)]})
    assert resp.status_code == 400
    web.db.commit.assert_not_called()


async def test_patch_refuses_to_lock_the_caller_out(web):
    """Mirrors !set_admin_roles: storing admin ids the caller does not hold leaves
    nobody able to undo it. The caller holds RUNNERS, not HELPERS."""
    resp = await _patch(web, {"admin_role_ids": [str(HELPERS_ID)]})
    assert resp.status_code == 400
    body = await resp.get_json()
    assert body["error"] == "would_lock_you_out"
    web.db.commit.assert_not_called()


async def test_patch_allows_clearing_back_to_name_matching(web):
    """The empty list is the documented escape hatch, not a lockout."""
    resp = await _patch(web, {"admin_role_ids": []})
    assert resp.status_code == 200
    assert web.settings.admin_role_ids == ""


async def test_patch_organiser_roles_skips_the_lockout_guard(web):
    """The organiser tier cannot lock an admin out — an admin is an organiser."""
    resp = await _patch(web, {"organiser_role_ids": [str(HELPERS_ID)]})
    assert resp.status_code == 200
    assert web.settings.organiser_role_ids == str(HELPERS_ID)


async def test_patch_refuses_a_non_admin(web):
    with patch("bot.web.actions.require_admin",
               AsyncMock(return_value=({"error": "insufficient_role"}, 403))):
        resp = await _patch(web, {"discord_bot_channel": "x"})
    assert resp.status_code == 403
    web.db.commit.assert_not_called()
