"""Phase 5b: PuzzleService.create_hunt — provision a hunt (Discord + sheet + DB row).

Asserts the STABLE contract only: the committed HuntData fields, the gsheet-cog calls, and the
duplicate guard. Does NOT assert the exact Discord API call sequence (implementation-defined).
Helpers are uniquely named and local to this file.
"""
import datetime
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord
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


def _create_service_with_notifier():
    bot = MagicMock()
    bot.get_cog.return_value = None
    notifier = MagicMock()
    notifier.board_changed = AsyncMock()
    return PuzzleService(bot, notifier=notifier), notifier


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
async def test_create_hunt_adds_scheme_to_bare_url():
    """A bare hostname (no https://) breaks Discord's [text](url) markdown —
    it silently shows the raw brackets instead of rendering a link — so a
    scheme must always be added at write time."""
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        await svc.create_hunt(guild, name="H", url="h.example.com/hunt", start_timestamp=0)
    hunt = _committed_hunt(hdb.commit)
    assert hunt.url == "https://h.example.com/hunt"


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
async def test_create_hunt_defaults_layout_mode_channel():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0)
    hunt = _committed_hunt(hdb.commit)
    assert hunt.layout_mode == "channel"


@pytest.mark.asyncio
async def test_create_hunt_sets_layout_mode_forum():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              layout_mode="forum")
    hunt = _committed_hunt(hdb.commit)
    assert hunt.layout_mode == "forum"


@pytest.mark.asyncio
async def test_create_hunt_rejects_bad_layout_mode():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        with pytest.raises(RuntimeError):
            await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                                  layout_mode="bogus")
    hdb.commit.assert_not_called()


@pytest.mark.asyncio
async def test_create_hunt_captures_sheet_and_role():
    svc, guild = _create_service(), _create_guild()
    role = guild.create_role.return_value
    role.members = []
    creator = _member_with_add(role)
    gsheet = MagicMock()
    gsheet.create_hunt_spreadsheet = AsyncMock(return_value="SHEET_ID")
    gsheet.create_solved_sheet = AsyncMock(return_value="ARCH_ID")
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              role_name="Team", create_solved_sheet=True, creator=creator)
    hunt = _committed_hunt(hdb.commit)
    assert hunt.google_sheet_id == "SHEET_ID"
    assert hunt.solved_sheet_id == "ARCH_ID"
    assert hunt.role_id == 333


def _guild_for_create():
    """Guild mock that records overwrites passed to create_category."""
    guild = MagicMock()
    guild.id = 999
    guild.categories = []
    guild.roles = []
    guild.default_role = MagicMock(name="everyone")
    guild.me = MagicMock(name="me")
    cat = MagicMock(); cat.id = 111
    chan = MagicMock(); chan.id = 222
    guild.create_category = AsyncMock(return_value=cat)
    guild.create_text_channel = AsyncMock(return_value=chan)
    return guild


@pytest.mark.asyncio
async def test_create_hunt_gates_categories_when_role_given():
    svc = _create_service()
    guild = _guild_for_create()
    role = MagicMock(name="role"); role.id = 333; role.members = []
    guild.create_role = AsyncMock(return_value=role)
    creator = _member_with_add(role)

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        hunt = await svc.create_hunt(
            guild, name="H", url="https://h/", start_timestamp=0,
            role_name="Team A", creator=creator,
        )

    assert hunt.role_id == 333
    _args, kwargs = guild.create_category.call_args_list[0]
    ov = kwargs["overwrites"]
    assert ov[guild.default_role].view_channel is False
    assert ov[role].view_channel is True


@pytest.mark.asyncio
async def test_create_hunt_open_when_no_role():
    svc = _create_service()
    guild = _guild_for_create()

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        hunt = await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0)

    assert hunt.role_id == 0
    _args, kwargs = guild.create_category.call_args_list[0]
    assert kwargs["overwrites"] == {}


@pytest.mark.asyncio
async def test_create_hunt_existing_role_requires_confirm():
    svc = _create_service()
    guild = _guild_for_create()
    existing = MagicMock(name="existing"); existing.name = "Team A"
    existing.members = [MagicMock()]
    guild.roles = [existing]
    creator = MagicMock()

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        with pytest.raises(RuntimeError, match="existing_role_confirm"):
            await svc.create_hunt(
                guild, name="H", url="https://h/", start_timestamp=0,
                role_name="Team A", creator=creator,
            )
    guild.create_category.assert_not_called()


@pytest.mark.asyncio
async def test_create_hunt_falls_back_to_open_when_gating_fails():
    svc = _create_service()
    guild = _guild_for_create()
    guild.roles = []
    new_role = MagicMock(name="new"); new_role.members = []
    new_role.delete = AsyncMock()
    guild.create_role = AsyncMock(return_value=new_role)
    creator = MagicMock()
    creator.add_roles = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        hunt = await svc.create_hunt(
            guild, name="H", url="https://h/", start_timestamp=0,
            role_name="Team A", creator=creator,
        )

    assert hunt.role_id == 0
    new_role.delete.assert_awaited_once()
    _args, kwargs = guild.create_category.call_args_list[0]
    assert kwargs["overwrites"] == {}


@pytest.mark.asyncio
async def test_create_hunt_pushes_board_when_notifier_present():
    """No pre-existing hunt to bind the @pushes_board decorator's `hunt`
    parameter to (this call creates one), so the push is manual — must still
    fire with the just-committed HuntData, so connected dashboards can
    invalidate their hunt list instead of waiting on a reload (review #54)."""
    bot = MagicMock()
    bot.get_cog.return_value = None
    notifier = MagicMock()
    notifier.board_changed = AsyncMock()
    svc, guild = PuzzleService(bot, notifier=notifier), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        hunt = await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0)
    notifier.board_changed.assert_awaited_once_with(hunt)


@pytest.mark.asyncio
async def test_create_hunt_no_notifier_does_not_raise():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0)


def test_gating_overwrites_denies_everyone_allows_role_and_bot():
    svc = _create_service()
    guild = MagicMock()
    everyone = MagicMock(name="everyone")
    me = MagicMock(name="me")
    guild.default_role = everyone
    guild.me = me
    role = MagicMock(name="team")

    ov = svc._gating_overwrites(guild, role)

    assert ov[everyone].view_channel is False
    assert ov[role].view_channel is True
    assert ov[me].view_channel is True


def _member_with_add(role_after_add):
    """A fake actor whose add_roles() seeds the given role (so role.members
    becomes non-empty), mirroring Discord updating the member cache."""
    actor = MagicMock(name="actor")
    async def _add(role, reason=None):
        role.members.append(actor)
    actor.add_roles = AsyncMock(side_effect=_add)
    return actor


@pytest.mark.asyncio
async def test_resolve_gating_role_none_when_no_role_name():
    svc = _create_service()
    assert await svc._resolve_gating_role(MagicMock(), None, MagicMock(), False) is None


@pytest.mark.asyncio
async def test_resolve_gating_role_missing_creates_and_seeds_actor():
    svc = _create_service()
    guild = MagicMock()
    guild.roles = []
    new_role = MagicMock(name="new")
    new_role.members = []
    guild.create_role = AsyncMock(return_value=new_role)
    actor = _member_with_add(new_role)

    result = await svc._resolve_gating_role(guild, "Team A", actor, False)

    assert result is new_role
    guild.create_role.assert_awaited_once()
    actor.add_roles.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_gating_role_existing_needs_confirm():
    svc = _create_service()
    guild = MagicMock()
    existing = MagicMock(name="existing")
    existing.name = "Team A"
    existing.members = [MagicMock()]
    guild.roles = [existing]
    actor = MagicMock()

    with pytest.raises(RuntimeError, match="existing_role_confirm"):
        await svc._resolve_gating_role(guild, "Team A", actor, False)


@pytest.mark.asyncio
async def test_resolve_gating_role_existing_confirmed_adds_actor():
    svc = _create_service()
    guild = MagicMock()
    existing = MagicMock(name="existing")
    existing.name = "Team A"
    existing.members = [MagicMock()]  # already has a member
    guild.roles = [existing]
    actor = _member_with_add(existing)

    result = await svc._resolve_gating_role(guild, "Team A", actor, True)

    assert result is existing
    actor.add_roles.assert_awaited_once()  # actor not yet in it -> added


@pytest.mark.asyncio
async def test_resolve_gating_role_new_role_add_fails_deletes_and_raises():
    svc = _create_service()
    guild = MagicMock()
    guild.roles = []
    new_role = MagicMock(name="new")
    new_role.members = []
    new_role.delete = AsyncMock()
    guild.create_role = AsyncMock(return_value=new_role)
    actor = MagicMock()
    actor.add_roles = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))

    with pytest.raises(RuntimeError, match="role_not_applied"):
        await svc._resolve_gating_role(guild, "Team A", actor, False)
    new_role.delete.assert_awaited_once()  # cleaned up our own role


@pytest.mark.asyncio
async def test_resolve_gating_role_existing_populated_add_fails_still_gates():
    svc = _create_service()
    guild = MagicMock()
    existing = MagicMock(name="existing")
    existing.name = "Team A"
    existing.members = [MagicMock()]  # other members present
    existing.delete = AsyncMock()
    guild.roles = [existing]
    actor = MagicMock()
    actor.add_roles = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))

    result = await svc._resolve_gating_role(guild, "Team A", actor, True)

    assert result is existing            # still visible to its members
    existing.delete.assert_not_called()  # never delete a pre-existing role


@pytest.mark.asyncio
async def test_resolve_gating_role_no_actor_raises():
    svc = _create_service()
    with pytest.raises(RuntimeError, match="role_not_applied"):
        await svc._resolve_gating_role(MagicMock(), "Team A", None, False)


@pytest.mark.asyncio
async def test_apply_gating_overwrites_edits_main_and_round_categories():
    svc = _create_service()
    guild = MagicMock()
    guild.default_role = MagicMock(); guild.me = MagicMock()
    main = MagicMock(name="main"); main.edit = AsyncMock()
    round_cat = MagicMock(name="round"); round_cat.edit = AsyncMock()
    guild.get_channel = MagicMock(return_value=main)
    hunt = MagicMock(); hunt.category_id = 111; hunt.id = 5; hunt.name = "H"
    role = MagicMock()
    ov = svc._gating_overwrites(guild, role)

    with patch("bot.core.puzzle_service.SOLVE_CATEGORY", False), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tg, \
         patch("bot.core.puzzle_service.category_layout") as cl:
        tg.get_all.return_value = [MagicMock()]
        cl.primary_category.return_value = round_cat
        failed = await svc._apply_gating_overwrites(guild, hunt, ov)

    assert failed == []
    main.edit.assert_awaited_once_with(overwrites=ov)
    round_cat.edit.assert_awaited_once_with(overwrites=ov)


@pytest.mark.asyncio
async def test_apply_gating_overwrites_collects_failures():
    svc = _create_service()
    guild = MagicMock()
    guild.default_role = MagicMock(); guild.me = MagicMock()
    main = MagicMock(name="main"); main.id = 111; main.name = "H"
    main.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))
    guild.get_channel = MagicMock(return_value=main)
    hunt = MagicMock(); hunt.category_id = 111; hunt.id = 5; hunt.name = "H"
    ov = svc._gating_overwrites(guild, MagicMock())

    with patch("bot.core.puzzle_service.SOLVE_CATEGORY", False), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tg:
        tg.get_all.return_value = []
        failed = await svc._apply_gating_overwrites(guild, hunt, ov)

    assert failed == ["H"]


@pytest.mark.asyncio
async def test_set_hunt_role_gates_open_hunt():
    svc, notifier = _create_service_with_notifier()
    guild = MagicMock()
    guild.default_role = MagicMock(); guild.me = MagicMock()
    guild.roles = []
    role = MagicMock(); role.id = 777; role.members = []
    guild.create_role = AsyncMock(return_value=role)
    main = MagicMock(); main.edit = AsyncMock()
    guild.get_channel = MagicMock(return_value=main)
    requester = _member_with_add(role)
    hunt = MagicMock(); hunt.role_id = 0; hunt.category_id = 111; hunt.id = 5; hunt.name = "H"

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.SOLVE_CATEGORY", False), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tg:
        tg.get_all.return_value = []
        failed = await svc.set_hunt_role(
            guild, hunt, role_name="Team A", requester=requester,
            confirm_existing_role=True,
        )

    assert hunt.role_id == 777
    hdb.commit.assert_called_once_with(hunt)
    main.edit.assert_awaited_once()
    notifier.board_changed.assert_awaited_once_with(hunt)
    assert failed == []


@pytest.mark.asyncio
async def test_set_hunt_role_rejects_already_gated():
    svc, _ = _create_service_with_notifier()
    hunt = MagicMock(); hunt.role_id = 42
    with pytest.raises(RuntimeError, match="already_gated"):
        await svc.set_hunt_role(
            MagicMock(), hunt, role_name="Team A", requester=MagicMock(),
        )


@pytest.mark.asyncio
async def test_set_hunt_role_existing_role_needs_confirm():
    svc, _ = _create_service_with_notifier()
    guild = MagicMock()
    existing = MagicMock(); existing.name = "Team A"; existing.members = [MagicMock()]
    guild.roles = [existing]
    hunt = MagicMock(); hunt.role_id = 0
    with pytest.raises(RuntimeError, match="existing_role_confirm"):
        await svc.set_hunt_role(
            guild, hunt, role_name="Team A", requester=MagicMock(),
        )


def _file_cog():
    gsheet = MagicMock()
    gsheet.create_hunt_spreadsheet = AsyncMock(return_value="SHEET_ID")
    gsheet.create_solved_sheet = AsyncMock(return_value="ARCH_ID")
    gsheet.create_folder = AsyncMock(side_effect=["FOLDER", "SOLVEDF"])
    gsheet.create_reference_spreadsheet = AsyncMock(return_value="REF")
    gsheet.share_anyone_reader = AsyncMock()
    return gsheet


@pytest.mark.asyncio
async def test_create_hunt_files_mode_makes_folders_then_reference():
    svc, guild = _create_service(), _create_guild()
    gsheet = _file_cog()
    order = MagicMock()
    order.attach_mock(gsheet.create_folder, "folder")
    order.attach_mock(gsheet.create_reference_spreadsheet, "ref")
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.GuildSettingsDb") as gdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        gdb.get.return_value = MagicMock(drive_parent_id="PARENT")
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              sheet_mode="files")
    hunt = _committed_hunt(hdb.commit)
    assert [c[0] for c in order.mock_calls] == ["folder", "folder", "ref"]
    assert order.mock_calls[0].args == ("H", "PARENT")
    assert order.mock_calls[1].args == ("Solved", "FOLDER")
    assert order.mock_calls[2].args == ("H", "FOLDER")
    assert (hunt.sheet_mode, hunt.drive_folder_id, hunt.solved_folder_id,
            hunt.google_sheet_id, hunt.solved_sheet_id) == ("files", "FOLDER", "SOLVEDF", "REF", "")
    gsheet.create_hunt_spreadsheet.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_hunt_files_mode_without_parent_uses_drive_root():
    svc, guild = _create_service(), _create_guild()
    gsheet = _file_cog()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.GuildSettingsDb") as gdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        gdb.get.return_value = None
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              sheet_mode="files")
    assert gsheet.create_folder.await_args_list[0].args == ("H", None)


def _drive_http_error(status):
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = status
    resp.reason = "x"
    return HttpError(resp, b"{}")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_create_hunt_files_mode_falls_back_to_root_when_parent_unusable(status, caplog):
    svc, guild = _create_service(), _create_guild()
    gsheet = _file_cog()
    gsheet.create_folder = AsyncMock(
        side_effect=[_drive_http_error(status), "FOLDER", "SOLVEDF"])
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.GuildSettingsDb") as gdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         caplog.at_level("WARNING"):
        hdb.check_duplicates.return_value = False
        gdb.get.return_value = MagicMock(drive_parent_id="PARENT")
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              sheet_mode="files")
    assert [c.args for c in gsheet.create_folder.await_args_list] == [
        ("H", "PARENT"), ("H", None), ("Solved", "FOLDER")]
    assert _committed_hunt(hdb.commit).drive_folder_id == "FOLDER"
    assert "PARENT" in caplog.text


@pytest.mark.asyncio
async def test_create_hunt_files_mode_propagates_a_500_from_the_parent_folder():
    svc, guild = _create_service(), _create_guild()
    gsheet = _file_cog()
    gsheet.create_folder = AsyncMock(side_effect=_drive_http_error(500))
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.GuildSettingsDb") as gdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        gdb.get.return_value = MagicMock(drive_parent_id="PARENT")
        with pytest.raises(Exception):
            await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                                  sheet_mode="files")
    assert gsheet.create_folder.await_count == 1
    hdb.commit.assert_not_called()


@pytest.mark.asyncio
async def test_create_hunt_tabs_mode_makes_exactly_todays_calls():
    svc, guild = _create_service(), _create_guild()
    gsheet = _file_cog()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              create_solved_sheet=True)
    assert gsheet.mock_calls == [call.create_hunt_spreadsheet("H"),
                                 call.create_solved_sheet("H")]
    hunt = _committed_hunt(hdb.commit)
    assert (hunt.sheet_mode, hunt.drive_folder_id, hunt.solved_folder_id) == ("tabs", "", "")


@pytest.mark.asyncio
async def test_create_hunt_files_mode_refuses_solved_sheet():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        with pytest.raises(RuntimeError, match="files_mode_no_solved_sheet"):
            await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                                  sheet_mode="files", create_solved_sheet=True)
    guild.create_category.assert_not_awaited()
    hdb.commit.assert_not_called()


@pytest.mark.asyncio
async def test_create_hunt_rejects_bad_sheet_mode():
    svc, guild = _create_service(), _create_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        with pytest.raises(RuntimeError, match="bad_sheet_mode"):
            await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                                  sheet_mode="bogus")
    hdb.commit.assert_not_called()


@pytest.mark.asyncio
async def test_create_hunt_files_mode_failure_deletes_created_folders():
    svc, guild = _create_service(), _create_guild()
    gsheet = _file_cog()
    gsheet.create_reference_spreadsheet = AsyncMock(side_effect=RuntimeError("copy failed"))
    gsheet.delete_file = AsyncMock()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.GuildSettingsDb") as gdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        gdb.get.return_value = None
        with pytest.raises(RuntimeError, match="copy failed"):
            await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                                  sheet_mode="files")
    hdb.commit.assert_not_called()
    assert sorted(c.args[0] for c in gsheet.delete_file.await_args_list) == ["FOLDER", "SOLVEDF"]


@pytest.mark.asyncio
async def test_create_hunt_files_mode_shares_the_hunt_folder_view_only():
    """The dashboard links to the folder, so anyone with the link may view it
    (Solved inherits); puzzle files keep their own anyone-writer sharing."""
    svc, guild = _create_service(), _create_guild()
    gsheet = _file_cog()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.GuildSettingsDb") as gdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        gdb.get.return_value = None
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              sheet_mode="files")
    gsheet.share_anyone_reader.assert_awaited_once_with("FOLDER")


@pytest.mark.asyncio
async def test_create_hunt_files_mode_survives_a_failed_folder_share(caplog):
    svc, guild = _create_service(), _create_guild()
    gsheet = _file_cog()
    gsheet.share_anyone_reader = AsyncMock(side_effect=_drive_http_error(403))
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.GuildSettingsDb") as gdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         caplog.at_level("WARNING"):
        hdb.check_duplicates.return_value = False
        gdb.get.return_value = None
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              sheet_mode="files")
    assert _committed_hunt(hdb.commit).drive_folder_id == "FOLDER"
    gsheet.delete_file.assert_not_called()
    assert "FOLDER" in caplog.text
