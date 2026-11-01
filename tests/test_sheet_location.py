from types import SimpleNamespace

from bot.core.sheet_location import (
    SheetLocation,
    additional_sheet_location,
    hunt_sheet_location,
    puzzle_location,
    spreadsheet_for,
)


def _hunt(live="LIVE", solved="ARCHIVE"):
    return SimpleNamespace(google_sheet_id=live, solved_sheet_id=solved)


def _puzzle(on_solved=False, page="11", own=""):
    return SimpleNamespace(on_solved_sheet=on_solved, google_page_id=page, google_sheet_id=own)


def _extra(on_solved=False, page="22", own=""):
    return SimpleNamespace(on_solved_sheet=on_solved, google_page_id=page, google_sheet_id=own)


def test_live_puzzle_is_on_the_live_sheet():
    loc = puzzle_location(_hunt(), _puzzle(on_solved=False, page="11"))
    assert loc == SheetLocation("LIVE", "11")


def test_solved_puzzle_is_on_the_solved_sheet():
    loc = puzzle_location(_hunt(), _puzzle(on_solved=True, page="12"))
    assert loc == SheetLocation("ARCHIVE", "12")


def test_solved_flag_with_empty_solved_sheet_falls_back_to_live():
    loc = puzzle_location(_hunt(solved=""), _puzzle(on_solved=True))
    assert loc.spreadsheet_id == "LIVE"
    assert "/d//" not in (loc.url or "")


def test_extra_uses_its_own_flag_not_the_puzzles():
    hunt = _hunt()
    live_puzzle = _puzzle(on_solved=False)
    solved_puzzle = _puzzle(on_solved=True)
    # extra archived while its puzzle is live, and the reverse
    assert additional_sheet_location(hunt, _extra(on_solved=True, page="1")) == SheetLocation("ARCHIVE", "1")
    assert additional_sheet_location(hunt, _extra(on_solved=False, page="2")) == SheetLocation("LIVE", "2")
    assert puzzle_location(hunt, live_puzzle).spreadsheet_id == "LIVE"
    assert puzzle_location(hunt, solved_puzzle).spreadsheet_id == "ARCHIVE"


def test_extra_on_solved_sheet_with_empty_solved_id_falls_back_to_live():
    loc = additional_sheet_location(_hunt(solved=""), _extra(on_solved=True))
    assert loc.spreadsheet_id == "LIVE"


def test_hunt_sheet_is_always_on_the_hunt_sheet():
    assert hunt_sheet_location(_hunt(), "33") == SheetLocation("LIVE", "33")


def test_url_is_full_url_with_gid():
    loc = SheetLocation("abc", 7)
    assert loc.url == "https://docs.google.com/spreadsheets/d/abc/edit#gid=7"


def test_gid_zero_is_a_real_page():
    assert SheetLocation("abc", 0).url == "https://docs.google.com/spreadsheets/d/abc/edit#gid=0"


def test_url_none_without_page_or_spreadsheet():
    assert SheetLocation("abc", None).url is None
    assert SheetLocation("abc", "").url is None
    assert SheetLocation("", "5").url is None


def test_legacy_full_url_sheet_id_passes_through_unchanged():
    legacy = "https://docs.google.com/spreadsheets/d/old/edit#gid=99"
    assert SheetLocation(legacy, "5").url == legacy


# --- Task 3: send_state and the mini welcome follow a solved puzzle's tab ---

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_hunt, make_puzzle


def _send_state_embed(puzzle, hunt):
    from bot.cogs.puzzles import Puzzles

    cog = object.__new__(Puzzles)
    cog.get_hunt = MagicMock(return_value=hunt)
    cog.info = AsyncMock()
    ctx = MagicMock()
    ctx.send = AsyncMock()
    asyncio.run(cog.send_state(ctx, MagicMock(), puzzle))
    return ctx.send.call_args.kwargs["embed"]


def _drive_value(embed):
    return next(f.value for f in embed.fields if f.name == "Google Drive")


def test_send_state_link_follows_solved_puzzle_to_solved_sheet():
    hunt = make_hunt(id=10)
    hunt.google_sheet_id = "LIVE1"
    hunt.solved_sheet_id = "ARCH1"
    puzzle = make_puzzle(id=1)
    puzzle.on_solved_sheet = True
    puzzle.google_page_id = "77"
    value = _drive_value(_send_state_embed(puzzle, hunt))
    assert "ARCH1" in value and "gid=77" in value
    assert "LIVE1" not in value


def test_send_state_link_for_live_puzzle_is_unchanged():
    hunt = make_hunt(id=10)
    hunt.google_sheet_id = "LIVE1"
    hunt.solved_sheet_id = "ARCH1"
    puzzle = make_puzzle(id=1)
    puzzle.on_solved_sheet = False
    puzzle.google_page_id = "78"
    value = _drive_value(_send_state_embed(puzzle, hunt))
    assert "LIVE1" in value and "gid=78" in value
    assert "ARCH1" not in value


def test_send_state_without_sheet_shows_question_mark():
    hunt = make_hunt(id=10)
    hunt.google_sheet_id = ""
    puzzle = make_puzzle(id=1)
    puzzle.google_page_id = "78"
    assert _drive_value(_send_state_embed(puzzle, hunt)) == "?"


def _mini_welcome(on_solved):
    import discord
    from bot.core.puzzle_service import PuzzleService

    minimeta = make_puzzle(id=3, name="Big Meta", hunt_id=10, channel_id=555, tags=[1])
    hunt = make_hunt(id=10)
    hunt.google_sheet_id = "LIVE1"
    hunt.solved_sheet_id = "ARCH1"
    thread = MagicMock(spec=discord.Thread)
    thread.id = 9003
    thread.name = "tiny"
    thread.send = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)

    async def fake_create_thread(**kwargs):
        return thread
    channel.create_thread = fake_create_thread
    guild = MagicMock()
    guild.get_channel.return_value = channel

    async def fake_create_sheet(p, **kwargs):
        p.google_page_id = "PAGE7"
        p.on_solved_sheet = on_solved
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = fake_create_sheet
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    bot = MagicMock()
    bot.get_cog.return_value = gsheet

    with patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rounds:
        rounds.get_by_attr.return_value = MagicMock(id=1)
        asyncio.run(PuzzleService(bot).create_mini(guild, hunt, minimeta, "Tiny"))
    return thread.send.call_args[0][0]


def test_mini_welcome_link_follows_solved_flag():
    solved = _mini_welcome(True)
    assert "ARCH1" in solved and "gid=PAGE7" in solved and "LIVE1" not in solved
    live = _mini_welcome(False)
    assert "LIVE1" in live and "gid=PAGE7" in live and "ARCH1" not in live


def _obj(own="", on_solved=False):
    return SimpleNamespace(google_sheet_id=own, on_solved_sheet=on_solved)


def test_rule_own_id_wins_over_a_set_solved_flag():
    assert spreadsheet_for(_obj(own="OWN", on_solved=True), "LIVE", "ARCHIVE") == "OWN"


def test_rule_own_id_wins_with_no_flag():
    assert spreadsheet_for(_obj(own="OWN"), "LIVE", "ARCHIVE") == "OWN"


def test_rule_solved_flag_and_solved_id_means_solved():
    assert spreadsheet_for(_obj(on_solved=True), "LIVE", "ARCHIVE") == "ARCHIVE"


def test_rule_solved_flag_without_solved_id_means_live():
    assert spreadsheet_for(_obj(on_solved=True), "LIVE", "") == "LIVE"


def test_rule_plain_object_is_live_even_when_a_solved_id_exists():
    assert spreadsheet_for(_obj(), "LIVE", "ARCHIVE") == "LIVE"


def test_puzzle_location_with_own_id_ignores_hunt_spreadsheets():
    loc = puzzle_location(_hunt(), _puzzle(on_solved=True, page="0", own="FILE"))
    assert loc == SheetLocation("FILE", "0")
    assert loc.url == "https://docs.google.com/spreadsheets/d/FILE/edit#gid=0"


def test_additional_sheet_location_with_own_id_ignores_hunt_spreadsheets():
    loc = additional_sheet_location(_hunt(), _extra(on_solved=True, page="5", own="XFILE"))
    assert loc == SheetLocation("XFILE", "5")
