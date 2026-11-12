"""hunts.canvas_enabled and the set_canvas_enabled setter (LIVE puzzlebot_test)."""
from bot.store import HuntJsonDb, mydb

GUILD_SNOW = 990000000000000073


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def _guild():
    return _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)", (GUILD_SNOW, "canvas store"))


def _hunt(guild, name):
    return _exec("INSERT INTO hunts (name, guild_id) VALUES (%s,%s)", (name, guild))


def _cleanup(guild):
    _exec("DELETE FROM hunts WHERE guild_id = %s", (guild,))
    _exec("DELETE FROM guilds WHERE id = %s", (guild,))


def test_a_new_hunt_row_has_canvas_off():
    guild = _guild()
    try:
        hid = _hunt(guild, "cv1")
        assert bool(HuntJsonDb.get_by_attr(id=hid).canvas_enabled) is False
    finally:
        _cleanup(guild)


def test_set_canvas_enabled_round_trips_both_ways():
    guild = _guild()
    try:
        hid = _hunt(guild, "cv2")
        HuntJsonDb.set_canvas_enabled(hid, True)
        assert bool(HuntJsonDb.get_by_attr(id=hid).canvas_enabled) is True
        HuntJsonDb.set_canvas_enabled(hid, False)
        assert bool(HuntJsonDb.get_by_attr(id=hid).canvas_enabled) is False
    finally:
        _cleanup(guild)


def test_a_stale_hunt_commit_never_reverts_canvas_enabled():
    guild = _guild()
    try:
        hid = _hunt(guild, "cv3")
        stale = HuntJsonDb.get_by_attr(id=hid)
        HuntJsonDb.set_canvas_enabled(hid, True)
        HuntJsonDb.commit(stale)
        assert bool(HuntJsonDb.get_by_attr(id=hid).canvas_enabled) is True
    finally:
        _cleanup(guild)


def _backfill_sql():
    """The UPDATE statement migration 061 ran, verbatim from the file."""
    from pathlib import Path
    text = Path(__file__).resolve().parent.parent.joinpath("migrations", "061_canvas_enabled.sql").read_text()
    sql = text[text.index("UPDATE `hunts`"):]
    return sql[:sql.index(";")]


def test_the_migration_backfill_turns_on_only_hunts_with_a_canvas():
    guild = _guild()
    try:
        with_canvas = _hunt(guild, "cv4")
        without = _hunt(guild, "cv5")
        media = _exec("INSERT INTO media (hunt_id, path, created_at) VALUES (%s, 'x.png', NOW())", (with_canvas,))
        _exec("INSERT INTO hunt_canvases (hunt_id, media_id, created_at) VALUES (%s, %s, NOW())",
              (with_canvas, media))
        _exec(_backfill_sql())  # idempotent: re-running it only re-sets hunts that have canvases
        assert bool(HuntJsonDb.get_by_attr(id=with_canvas).canvas_enabled) is True
        assert bool(HuntJsonDb.get_by_attr(id=without).canvas_enabled) is False
    finally:
        _exec("DELETE FROM hunt_canvases WHERE hunt_id IN (SELECT id FROM hunts WHERE guild_id = %s)", (guild,))
        _exec("DELETE FROM media WHERE hunt_id IN (SELECT id FROM hunts WHERE guild_id = %s)", (guild,))
        _cleanup(guild)
