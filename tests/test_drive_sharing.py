"""Unit tests for bot/core/drive_sharing.py (mocked Drive cog and DAOs)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from bot.core import drive_sharing
from bot.core.puzzle_service import PuzzleService
from tests.conftest import make_hunt


def _http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    resp.reason = "err"
    return HttpError(resp, b"{}")


def _make_bot(gsheet_cog=None, guilds=None):
    bot = MagicMock()
    bot.get_cog.side_effect = lambda name: gsheet_cog if name == "GoogleSheets" else None
    guild_map = {g.id: g for g in (guilds or [])}
    bot.get_guild.side_effect = lambda gid: guild_map.get(gid)
    return bot


def _make_member(user_id: int, *, role_ids=(), is_bot=False, guild=None):
    m = MagicMock()
    m.id = user_id
    m.bot = is_bot
    m.roles = [SimpleNamespace(id=rid) for rid in role_ids]
    m.guild = guild
    return m


@pytest.fixture(autouse=True)
def _reset_cooldowns():
    drive_sharing._failed_cooldowns.clear()
    yield
    drive_sharing._failed_cooldowns.clear()


# --- hunt_share_targets ------------------------------------------------------

def test_hunt_share_targets_files_mode_prefers_folder():
    h = make_hunt(google_sheet_id="REF1", solved_sheet_id="")
    h.sheet_mode = "files"
    h.drive_folder_id = "FOLD1"
    assert drive_sharing.hunt_share_targets(h) == ["FOLD1"]


def test_hunt_share_targets_files_mode_fallback_without_folder():
    h = make_hunt(google_sheet_id="REF1")
    h.sheet_mode = "files"
    h.drive_folder_id = ""
    assert drive_sharing.hunt_share_targets(h) == ["REF1"]


def test_hunt_share_targets_tabs_mode_includes_solved_when_present():
    h = make_hunt(google_sheet_id="LIVE1", solved_sheet_id="SOLVED1")
    h.sheet_mode = "tabs"
    assert drive_sharing.hunt_share_targets(h) == ["LIVE1", "SOLVED1"]

    h2 = make_hunt(google_sheet_id="LIVE1", solved_sheet_id="")
    h2.sheet_mode = "tabs"
    assert drive_sharing.hunt_share_targets(h2) == ["LIVE1"]


def test_hunt_share_targets_archived_hunt_returns_empty():
    h = make_hunt(google_sheet_id="LIVE1", solved_sheet_id="SOLVED1", archived=True)
    assert drive_sharing.hunt_share_targets(h) == []
    assert drive_sharing.hunt_share_targets({
        "archived": 1, "sheet_mode": "files", "drive_folder_id": "F1",
    }) == []


def test_hunt_share_targets_accepts_web_dict():
    assert drive_sharing.hunt_share_targets({
        "id": 5, "archived": 0, "sheet_mode": "files",
        "drive_folder_id": "F5", "google_sheet_id": "S5",
    }) == ["F5"]


# --- ensure_user_hunt_shared -------------------------------------------------

async def test_ensure_user_hunt_shared_noops_for_unlinked_user():
    gsheet = MagicMock()
    gsheet.share_user_writer = AsyncMock()
    bot = _make_bot(gsheet)
    hunt = make_hunt(id=10, google_sheet_id="S10")

    with patch("bot.core.drive_sharing.GoogleLinkDb") as gldb, \
            patch("bot.core.drive_sharing.DriveShareDb") as dsdb:
        gldb.get.return_value = None
        await drive_sharing.ensure_user_hunt_shared(bot, hunt, 111)

    gsheet.share_user_writer.assert_not_awaited()
    dsdb.upsert.assert_not_called()


async def test_ensure_user_hunt_shared_shares_once_then_cache_hits():
    gsheet = MagicMock()
    gsheet.share_user_writer = AsyncMock(return_value="PERM1")
    bot = _make_bot(gsheet)
    hunt = make_hunt(id=10, google_sheet_id="S10")
    hunt.sheet_mode = "files"
    hunt.drive_folder_id = "FOLD10"

    stored = {}

    def fake_get(uid, fid):
        return stored.get((uid, fid))

    def fake_upsert(hid, uid, fid, email, perm_id):
        stored[(uid, fid)] = {
            "hunt_id": hid, "discord_user_id": uid, "drive_file_id": fid,
            "google_email": email, "permission_id": perm_id,
        }

    with patch("bot.core.drive_sharing.GoogleLinkDb") as gldb, \
            patch("bot.core.drive_sharing.DriveShareDb") as dsdb:
        gldb.get.return_value = {"discord_user_id": 111, "google_email": "alice@example.com"}
        dsdb.get.side_effect = fake_get
        dsdb.upsert.side_effect = fake_upsert

        await drive_sharing.ensure_user_hunt_shared(bot, hunt, 111)
        await drive_sharing.ensure_user_hunt_shared(bot, hunt, 111)

    gsheet.share_user_writer.assert_awaited_once_with("FOLD10", "alice@example.com")
    assert stored[(111, "FOLD10")]["permission_id"] == "PERM1"


async def test_ensure_user_hunt_shared_replaces_stale_email():
    gsheet = MagicMock()
    gsheet.share_user_writer = AsyncMock(return_value="PERM_NEW")
    gsheet.unshare_permission = AsyncMock()
    bot = _make_bot(gsheet)
    hunt = make_hunt(id=10, google_sheet_id="S10", solved_sheet_id="")

    with patch("bot.core.drive_sharing.DriveShareDb") as dsdb:
        dsdb.get.return_value = {
            "hunt_id": 10, "discord_user_id": 111, "drive_file_id": "S10",
            "google_email": "old@example.com", "permission_id": "PERM_OLD",
        }
        dsdb.has_other_user_share.return_value = False

        await drive_sharing.ensure_user_hunt_shared(bot, hunt, 111, email="new@example.com")

    dsdb.delete_for_user_file.assert_called_once_with(111, "S10", email="old@example.com")
    gsheet.unshare_permission.assert_awaited_once_with("S10", "PERM_OLD")
    gsheet.share_user_writer.assert_awaited_once_with("S10", "new@example.com")
    dsdb.upsert.assert_called_once_with(10, 111, "S10", "new@example.com", "PERM_NEW")


async def test_ensure_user_hunt_shared_cools_down_after_http_error():
    gsheet = MagicMock()
    gsheet.share_user_writer = AsyncMock(side_effect=_http_error(400))
    bot = _make_bot(gsheet)
    hunt = make_hunt(id=10, google_sheet_id="S10", solved_sheet_id="")

    with patch("bot.core.drive_sharing.DriveShareDb") as dsdb:
        dsdb.get.return_value = None
        await drive_sharing.ensure_user_hunt_shared(bot, hunt, 111, email="bad@example.com")
        # Second call inside cooldown window skips Drive completely.
        await drive_sharing.ensure_user_hunt_shared(bot, hunt, 111, email="bad@example.com")
        assert gsheet.share_user_writer.await_count == 1

        # Clearing user cooldowns (as re-link does) allows retrying.
        drive_sharing.clear_user_cooldowns(111)
        await drive_sharing.ensure_user_hunt_shared(bot, hunt, 111, email="bad@example.com")
        assert gsheet.share_user_writer.await_count == 2


# --- preshare_hunt -----------------------------------------------------------

async def test_preshare_hunt_respects_role_and_skips_bots():
    gsheet = MagicMock()
    gsheet.share_user_writer = AsyncMock(side_effect=["P1", "P2"])
    guild = MagicMock()
    guild.id = 999
    guild.chunked = True
    m_linked = _make_member(1, role_ids=(50,), guild=guild)
    m_bot = _make_member(2, role_ids=(50,), is_bot=True, guild=guild)
    m_unlinked = _make_member(3, role_ids=(50,), guild=guild)
    role = MagicMock()
    role.id = 50
    role.members = [m_linked, m_bot, m_unlinked]
    guild.get_role.return_value = role
    bot = _make_bot(gsheet, guilds=[guild])

    hunt = make_hunt(id=7, google_sheet_id="S7", solved_sheet_id="", role_id=50)
    hunt.sheet_mode = "files"
    hunt.drive_folder_id = "F7"

    with patch("bot.core.drive_sharing.GoogleLinkDb") as gldb, \
            patch("bot.core.drive_sharing.DriveShareDb") as dsdb:
        gldb.get_many.return_value = {1: "alice@example.com"}
        dsdb.get.return_value = None
        await drive_sharing.preshare_hunt(bot, hunt, guild=guild)

    # Bot member 2 must never be queried; only 1 and 3 are eligible.
    gldb.get_many.assert_called_once_with([1, 3])
    gsheet.share_user_writer.assert_awaited_once_with("F7", "alice@example.com")


async def test_preshare_hunt_open_hunt_uses_all_non_bot_members():
    gsheet = MagicMock()
    gsheet.share_user_writer = AsyncMock(return_value="P1")
    guild = MagicMock()
    guild.id = 999
    guild.chunked = True
    guild.members = [_make_member(10, guild=guild), _make_member(11, is_bot=True, guild=guild)]
    bot = _make_bot(gsheet, guilds=[guild])
    hunt = make_hunt(id=8, google_sheet_id="S8", solved_sheet_id="", role_id=0)

    with patch("bot.core.drive_sharing.GoogleLinkDb") as gldb, \
            patch("bot.core.drive_sharing.DriveShareDb") as dsdb:
        gldb.get_many.return_value = {10: "bob@example.com"}
        dsdb.get.return_value = None
        await drive_sharing.preshare_hunt(bot, hunt, guild=guild)

    gldb.get_many.assert_called_once_with([10])
    gsheet.share_user_writer.assert_awaited_once_with("S8", "bob@example.com")


# --- revoke_user_active_shares ----------------------------------------------

async def test_revoke_user_active_shares_respects_other_discord_account():
    gsheet = MagicMock()
    gsheet.unshare_permission = AsyncMock()
    bot = _make_bot(gsheet)

    rows = [
        {"hunt_id": 1, "discord_user_id": 100, "drive_file_id": "F1",
         "google_email": "shared@example.com", "permission_id": "PERM1"},
        {"hunt_id": 2, "discord_user_id": 100, "drive_file_id": "F2",
         "google_email": "shared@example.com", "permission_id": "PERM2"},
    ]

    with patch("bot.core.drive_sharing.DriveShareDb") as dsdb:
        dsdb.list_active_for_user.return_value = rows
        dsdb.get.side_effect = rows
        # F1 still shared by an alt Discord account; F2 is not.
        dsdb.has_other_user_share.side_effect = [True, False]

        await drive_sharing.revoke_user_active_shares(bot, 100)

    assert dsdb.delete_for_user_file.call_count == 2
    gsheet.unshare_permission.assert_awaited_once_with("F2", "PERM2")


# --- Member event hooks -----------------------------------------------------

async def test_on_member_roles_changed_grants_and_revokes():
    guild = MagicMock()
    guild.id = 777
    before = _make_member(42, role_ids=(20,), guild=guild)
    after = _make_member(42, role_ids=(10,), guild=guild)
    bot = _make_bot()

    h_added = make_hunt(id=1, role_id=10, google_sheet_id="S1", solved_sheet_id="")
    h_removed = make_hunt(id=2, role_id=20, google_sheet_id="S2", solved_sheet_id="")

    with patch("bot.core.drive_sharing.HuntJsonDb") as hdb, \
            patch("bot.core.drive_sharing.GoogleLinkDb") as gldb, \
            patch("bot.core.drive_sharing.ensure_user_hunt_shared", new=AsyncMock()) as ensure_mock, \
            patch("bot.core.drive_sharing.revoke_user_active_shares", new=AsyncMock()) as revoke_mock:
        hdb.get_active_for_guild.return_value = [h_added, h_removed]
        gldb.get.return_value = {"discord_user_id": 42, "google_email": "a@example.com"}

        await drive_sharing.on_member_roles_changed(bot, before, after, {10}, {20})

    ensure_mock.assert_awaited_once_with(bot, h_added, 42, email="a@example.com")
    revoke_mock.assert_awaited_once_with(bot, 42, hunt_id=2)


# --- PuzzleService hook sanity ----------------------------------------------

async def test_create_hunt_solved_sheet_schedules_preshare():
    gsheet = MagicMock()
    gsheet.create_solved_sheet = AsyncMock(return_value="SOLVED_NEW")
    bot = _make_bot(gsheet)
    service = PuzzleService(bot)
    hunt = make_hunt(id=5, google_sheet_id="LIVE5", solved_sheet_id="")

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
            patch("bot.core.puzzle_service.drive_sharing.schedule_preshare_hunt") as sched:
        sid = await service.create_hunt_solved_sheet(hunt)

    assert sid == "SOLVED_NEW"
    hdb.commit.assert_called_once_with(hunt)
    sched.assert_called_once_with(bot, hunt)
