"""find_puzzle_by_sheet against the real schema (LIVE puzzlebot_test)."""
import asyncio

from bot.store import mydb
from bot.web import data

GUILD_SNOW = 990000000000000061


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def _puzzle(hunt, name, page, *, sheet="", parent=None, solved_sheet=0):
    return _exec(
        "INSERT INTO puzzles (name, hunt_id, google_sheet_id, google_page_id, parent_puzzle_id, "
        "on_solved_sheet) VALUES (%s,%s,%s,%s,%s,%s)",
        (name, hunt, sheet, page, parent, solved_sheet))


def _find(sheet, gid):
    return asyncio.run(data.find_puzzle_by_sheet(sheet, gid))


def _id(found):
    return None if found is None else found["puzzle"]["id"]


def test_find_puzzle_by_sheet_against_the_real_schema():
    guild = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)", (GUILD_SNOW, "t2 lookup"))
    try:
        tabs = _exec("INSERT INTO hunts (name, guild_id, archived, google_sheet_id, solved_sheet_id) "
                     "VALUES (%s,%s,0,%s,%s)", ("t2 tabs", guild, "T2LIVE", "T2SOLVED"))
        files = _exec("INSERT INTO hunts (name, guild_id, archived, google_sheet_id, solved_sheet_id) "
                      "VALUES (%s,%s,0,%s,%s)", ("t2 files", guild, "T2FLIVE", ""))
        on_live = _puzzle(tabs, "on live", "500")
        on_solved = _puzzle(tabs, "on solved", "500", solved_sheet=1)
        meta = _puzzle(tabs, "minimeta", "600")
        _puzzle(tabs, "mini", "601", parent=meta)
        own_file = _puzzle(files, "own file", "0", sheet="T2OWNFILE")
        _puzzle(files, "own mini", "0", sheet="T2MINIFILE", parent=own_file)

        # Same gid on two spreadsheets: each matches only via its own location.
        assert _id(_find("T2LIVE", 500)) == on_live
        assert _id(_find("T2SOLVED", 500)) == on_solved
        # Wrong gid / no gid in tabs mode / unknown sheet -> nothing.
        assert _find("T2LIVE", 999) is None
        assert _find("T2LIVE", None) is None
        assert _find("T2NOPE", 500) is None
        # A mini's tab resolves to its parent minimeta.
        assert _id(_find("T2LIVE", 601)) == meta
        assert _id(_find("T2LIVE", 600)) == meta
        # Files mode: sheet id alone, gid ignored; a mini's own file -> its parent.
        assert _id(_find("T2OWNFILE", None)) == own_file
        assert _id(_find("T2OWNFILE", 12345)) == own_file
        assert _id(_find("T2MINIFILE", None)) == own_file
        # The hunt row comes back with the match.
        assert _find("T2LIVE", 500)["hunt"]["id"] == tabs
    finally:
        _exec("DELETE FROM puzzles WHERE hunt_id IN (SELECT id FROM hunts WHERE guild_id = %s)", (guild,))
        _exec("DELETE FROM hunts WHERE guild_id = %s", (guild,))
        _exec("DELETE FROM guilds WHERE id = %s", (guild,))
