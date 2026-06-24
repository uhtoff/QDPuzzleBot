"""Phase 5b: PuzzleService.create_hunt — provision a hunt (Discord + sheet + DB row).

Asserts the STABLE contract only: the committed HuntData fields, the gsheet-cog calls, and the
duplicate guard. Does NOT assert the exact Discord API call sequence (implementation-defined).
Helpers are uniquely named and local to this file.
"""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData


def _create_guild():
    """Flexible guild double: async Discord creators return objects exposing .id."""
    guild = MagicMock()
    guild.id = 999
    cat = MagicMock(); cat.id = 111
    chan = MagicMock(); chan.id = 222
    role = MagicMock(); role.id = 333
    guild.categories = []
    guild.roles = []
    guild.create_category = AsyncMock(return_value=cat)
    guild.create_text_channel = AsyncMock(return_value=chan)
    guild.create_role = AsyncMock(return_value=role)
    return guild


def _create_service():
    bot = MagicMock()
    bot.get_cog.return_value = None          # no gsheet cog unless a test injects one
    return PuzzleService(bot)


def _committed_hunt(commit_mock):
    """The HuntData passed to HuntJsonDb.commit (last call)."""
    assert commit_mock.called
    return commit_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_create_hunt_commits_core_fields():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0)
    hunt = _committed_hunt(hdb.commit)
    assert isinstance(hunt, HuntData)
    assert hunt.name == "H"
    assert hunt.url == "https://h/"
    assert hunt.uid == "abc123"


@pytest.mark.asyncio
async def test_create_hunt_mirrors_start_time():
    svc, guild = _create_service(), _create_guild()
    ts = 1_700_000_000
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=ts)
    hunt = _committed_hunt(hdb.commit)
    assert hunt.start_timestamp == ts
    assert hunt.start_time == datetime.datetime.fromtimestamp(ts, tz=pytz.UTC)


@pytest.mark.asyncio
async def test_create_hunt_duplicate_name_does_not_commit():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = True
        # A duplicate name must be signalled WITHOUT committing. The service convention
        # (create_round → RuntimeError("round_exists")) is to raise RuntimeError.
        with pytest.raises(RuntimeError):
            await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0)
    hdb.commit.assert_not_called()


@pytest.mark.asyncio
async def test_create_hunt_captures_sheet_and_role():
    svc, guild = _create_service(), _create_guild()
    gsheet = MagicMock()
    gsheet.create_hunt_spreadsheet = AsyncMock(return_value="SHEET_ID")
    gsheet.create_hunt_archive_spreadsheet = AsyncMock(return_value="ARCH_ID")
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              role_name="Team", create_archive_sheet=True)
    hunt = _committed_hunt(hdb.commit)
    assert hunt.google_sheet_id == "SHEET_ID"
    assert hunt.archive_google_sheet_id == "ARCH_ID"
    assert hunt.role_id == 333
