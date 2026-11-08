"""Task 6: PuzzleService voice hooks (create_hunt, create_puzzle, delete_puzzle).

All three are best-effort: a failing voice call never fails the operation.
Patched at ``bot.core.puzzle_service.voice`` (the name the service looks up).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle, make_round
from tests.test_create_hunt_service import _create_guild, _create_service


def _voice_mock(**kw):
    v = MagicMock()
    v.ensure_hunt_voice = AsyncMock(**kw)
    v.create_puzzle_voice = AsyncMock(**kw)
    v.delete_puzzle_voice_now = AsyncMock(**kw)
    return v


async def _make_hunt(guild, *, toggle=False, voice=None, **create_kw):
    svc = _create_service()
    voice = voice or _voice_mock()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.GuildSettingsDb") as gdb, \
         patch("bot.core.puzzle_service.voice", voice):
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc"
        gdb.get.return_value = MagicMock(discord_use_voice_channels=toggle)
        hunt = await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                                     **create_kw)
    return hunt, hdb, voice


@pytest.mark.asyncio
async def test_create_hunt_calls_ensure_hunt_voice_with_committed_hunt():
    hunt, _hdb, voice = await _make_hunt(_create_guild())
    voice.ensure_hunt_voice.assert_awaited_once()
    args = voice.ensure_hunt_voice.await_args.args
    assert args[1] is hunt


@pytest.mark.asyncio
async def test_create_hunt_survives_ensure_hunt_voice_raising():
    hunt, hdb, _ = await _make_hunt(
        _create_guild(), voice=_voice_mock(side_effect=RuntimeError("boom")))
    assert isinstance(hunt, HuntData)
    hdb.commit.assert_called()


@pytest.mark.asyncio
async def test_create_hunt_guild_toggle_on_sets_voice_per_puzzle():
    hunt, hdb, voice = await _make_hunt(_create_guild(), toggle=True)
    hdb.set_voice_per_puzzle.assert_called_once_with(hunt.id, True)
    assert hunt.voice_per_puzzle  # in-memory flag also set
    # the setter ran before ensure (hunt row committed first)
    voice.ensure_hunt_voice.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_hunt_guild_toggle_off_leaves_voice_per_puzzle():
    hunt, hdb, _ = await _make_hunt(_create_guild(), toggle=False)
    hdb.set_voice_per_puzzle.assert_not_called()
    assert not hunt.voice_per_puzzle


@pytest.mark.asyncio
async def test_create_hunt_survives_set_voice_per_puzzle_raising():
    svc = _create_service()
    voice = _voice_mock()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.GuildSettingsDb") as gdb, \
         patch("bot.core.puzzle_service.voice", voice):
        hdb.check_duplicates.return_value = False
        hdb.set_voice_per_puzzle.side_effect = RuntimeError("db")
        gdb.get.return_value = MagicMock(discord_use_voice_channels=True)
        hunt = await svc.create_hunt(_create_guild(), name="H", url="https://h/",
                                     start_timestamp=0)
    assert isinstance(hunt, HuntData)


# --- create_puzzle ------------------------------------------------------

def _puzzle_hunt(per_puzzle):
    h = HuntData()
    h.id = 10
    h.url = None
    h.voice_per_puzzle = per_puzzle
    return h


async def _create_puzzle(per_puzzle, voice, is_meta=False, n_channels=0):
    bot = MagicMock()
    bot.get_cog.return_value = None
    svc = PuzzleService(bot)
    guild = MagicMock()
    guild.channels = [object()] * n_channels
    guild.categories = [MagicMock(id=999)]
    text = MagicMock(id=555, mention="#p", spec_set=None)
    text.edit = AsyncMock()
    round_data = make_round(id=5, category_id=999)
    hunt = _puzzle_hunt(per_puzzle)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=None)), \
         patch.object(svc, "_maybe_relieve_overflow", AsyncMock()), \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(text, True))), \
         patch("bot.core.puzzle_service.voice", voice):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        puzzle = await svc.create_puzzle(guild, hunt, round_data, "Newpuz", is_meta=is_meta)
    return puzzle, guild, hunt


@pytest.mark.asyncio
async def test_create_puzzle_with_flag_creates_voice():
    voice = _voice_mock()
    puzzle, guild, hunt = await _create_puzzle(1, voice)  # DB reads back int 1
    voice.create_puzzle_voice.assert_awaited_once_with(guild, hunt, puzzle)


@pytest.mark.asyncio
async def test_create_puzzle_meta_with_flag_creates_voice():
    voice = _voice_mock()
    puzzle, guild, hunt = await _create_puzzle(1, voice, is_meta=True)
    voice.create_puzzle_voice.assert_awaited_once_with(guild, hunt, puzzle)


@pytest.mark.asyncio
async def test_create_puzzle_without_flag_skips_voice():
    voice = _voice_mock()
    await _create_puzzle(0, voice)
    voice.create_puzzle_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_puzzle_skips_auto_voice_near_the_guild_channel_limit(caplog):
    from bot.core.discord_channels import MAX_CHANNELS_PER_GUILD
    voice = _voice_mock()
    with caplog.at_level("WARNING", logger="bot.core.puzzle_service"):
        puzzle, _g, _h = await _create_puzzle(1, voice, n_channels=MAX_CHANNELS_PER_GUILD - 25)
    assert puzzle.id == 1
    voice.create_puzzle_voice.assert_not_awaited()
    assert any("voice" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


@pytest.mark.asyncio
async def test_create_puzzle_auto_voice_just_below_the_headroom():
    from bot.core.discord_channels import MAX_CHANNELS_PER_GUILD
    voice = _voice_mock()
    await _create_puzzle(1, voice, n_channels=MAX_CHANNELS_PER_GUILD - 26)
    voice.create_puzzle_voice.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_puzzle_survives_voice_failure():
    voice = _voice_mock(side_effect=RuntimeError("no room"))
    puzzle, _g, _h = await _create_puzzle(1, voice)
    assert puzzle.id == 1
    voice.create_puzzle_voice.assert_awaited_once()


# --- delete_puzzle ------------------------------------------------------

async def _delete(guild, voice):
    bot = MagicMock()
    bot.get_cog.return_value = None
    svc = PuzzleService(bot)
    puzzle = make_puzzle(id=7, name="P", channel_id=555)
    puzzle.tags = []
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.voice", voice), \
         patch("bot.core.puzzle_service.category_layout.maybe_consolidate_category",
               MagicMock()), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=True)):
        pdb.get_children.return_value = []
        await svc.delete_puzzle(guild, _puzzle_hunt(0), puzzle)
    return puzzle


@pytest.mark.asyncio
async def test_delete_puzzle_deletes_voice_now():
    voice = _voice_mock()
    guild = MagicMock()
    puzzle = await _delete(guild, voice)
    voice.delete_puzzle_voice_now.assert_awaited_once_with(guild, puzzle)


@pytest.mark.asyncio
async def test_delete_puzzle_survives_voice_failure():
    voice = _voice_mock(side_effect=RuntimeError("x"))
    await _delete(MagicMock(), voice)
    voice.delete_puzzle_voice_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_puzzle_guild_none_skips_voice():
    voice = _voice_mock()
    await _delete(None, voice)
    voice.delete_puzzle_voice_now.assert_not_awaited()


async def _rename(voice, guild):
    svc = _create_service()
    svc.desired_channel_name = MagicMock(return_value="new-name")
    svc._gsheet_cog = MagicMock(return_value=None)
    svc.refresh_targets = AsyncMock(return_value=[])
    puzzle = make_puzzle(id=7, name="Old", channel_id=0)
    with patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.voice", voice):
        await svc.rename_puzzle(guild, MagicMock(), puzzle, "New")
    return puzzle


@pytest.mark.asyncio
async def test_rename_puzzle_renames_its_voice_channel():
    v = _voice_mock()
    v.rename_puzzle_voice = AsyncMock()
    guild = MagicMock()
    puzzle = await _rename(v, guild)
    v.rename_puzzle_voice.assert_awaited_once_with(guild, puzzle, "New")


@pytest.mark.asyncio
async def test_rename_puzzle_survives_voice_rename_failure():
    v = _voice_mock()
    v.rename_puzzle_voice = AsyncMock(side_effect=RuntimeError("boom"))
    puzzle = await _rename(v, MagicMock())
    assert puzzle.name == "New"


@pytest.mark.asyncio
async def test_rename_puzzle_without_guild_skips_voice():
    v = _voice_mock()
    v.rename_puzzle_voice = AsyncMock()
    await _rename(v, None)
    v.rename_puzzle_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_hunt_explicit_voice_choice_beats_the_guild_default():
    hunt, hdb, _ = await _make_hunt(_create_guild(), toggle=False, voice_per_puzzle=True)
    hdb.set_voice_per_puzzle.assert_called_once_with(hunt.id, True)
    assert hunt.voice_per_puzzle
    hunt2, hdb2, _ = await _make_hunt(_create_guild(), toggle=True, voice_per_puzzle=False)
    hdb2.set_voice_per_puzzle.assert_not_called()
    assert not hunt2.voice_per_puzzle
