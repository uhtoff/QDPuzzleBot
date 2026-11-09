"""header_style / header_layout columns and the set_header_style setter (LIVE puzzlebot_test)."""
import pytest

from bot.store import HuntJsonDb, PuzzleJsonDb, mydb
from bot.store.puzzle_data import PuzzleData

GUILD_SNOW = 990000000000000072


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def _hunt(guild, name):
    return _exec("INSERT INTO hunts (name, guild_id) VALUES (%s,%s)", (name, guild))


def _puzzle(hunt, name):
    return _exec("INSERT INTO puzzles (name, hunt_id) VALUES (%s,%s)", (name, hunt))


def _cleanup(guild):
    _exec("DELETE FROM puzzles WHERE hunt_id IN (SELECT id FROM hunts WHERE guild_id = %s)", (guild,))
    _exec("DELETE FROM hunts WHERE guild_id = %s", (guild,))
    _exec("DELETE FROM guilds WHERE id = %s", (guild,))


def _guild():
    return _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)", (GUILD_SNOW, "header store"))


def test_a_new_hunt_row_defaults_to_compact():
    guild = _guild()
    try:
        hid = _hunt(guild, "hs1")
        assert HuntJsonDb.get_by_attr(id=hid).header_style == "compact"
    finally:
        _cleanup(guild)


def test_set_header_style_round_trips_and_rejects_unknown():
    guild = _guild()
    try:
        hid = _hunt(guild, "hs2")
        HuntJsonDb.set_header_style(hid, "full")
        assert HuntJsonDb.get_by_attr(id=hid).header_style == "full"
        with pytest.raises(ValueError):
            HuntJsonDb.set_header_style(hid, "Compact")
        assert HuntJsonDb.get_by_attr(id=hid).header_style == "full"
    finally:
        _cleanup(guild)


def test_a_stale_hunt_commit_never_reverts_header_style():
    guild = _guild()
    try:
        hid = _hunt(guild, "hs3")
        stale = HuntJsonDb.get_by_attr(id=hid)
        assert stale.header_style == "compact"
        HuntJsonDb.set_header_style(hid, "full")
        HuntJsonDb.commit(stale)
        assert HuntJsonDb.get_by_attr(id=hid).header_style == "full"
    finally:
        _cleanup(guild)


def test_a_puzzle_row_defaults_full_and_commit_stores_compact():
    guild = _guild()
    try:
        hid = _hunt(guild, "hs4")
        pid = _puzzle(hid, "raw")
        assert PuzzleJsonDb.get_by_attr(id=pid).header_layout == "full"
        p = PuzzleData(name="made", hunt_id=hid)
        p.header_layout = "compact"
        PuzzleJsonDb.commit(p)
        assert PuzzleJsonDb.get_by_attr(id=p.id).header_layout == "compact"
    finally:
        _cleanup(guild)
