"""Voice-channel columns and the narrow setters/sweep queries (LIVE puzzlebot_test)."""
import datetime

from bot.store import HuntJsonDb, PuzzleJsonDb, mydb

GUILD_SNOW = 990000000000000071


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def _hunt(guild, name, archived=0):
    return _exec("INSERT INTO hunts (name, guild_id, archived) VALUES (%s,%s,%s)",
                 (name, guild, archived))


def _puzzle(hunt, name, *, voice=None, solved=0, solve_time=None):
    return _exec(
        "INSERT INTO puzzles (name, hunt_id, voice_channel_id, solved, solve_time) "
        "VALUES (%s,%s,%s,%s,%s)", (name, hunt, voice, solved, solve_time))


def _cleanup(guild):
    _exec("DELETE FROM puzzles WHERE hunt_id IN (SELECT id FROM hunts WHERE guild_id = %s)", (guild,))
    _exec("DELETE FROM hunts WHERE guild_id = %s", (guild,))
    _exec("DELETE FROM guilds WHERE id = %s", (guild,))


def _guild():
    return _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)", (GUILD_SNOW, "voice store"))


def test_set_voice_channel_round_trips_and_clears():
    guild = _guild()
    try:
        pid = _puzzle(_hunt(guild, "v1"), "p")
        PuzzleJsonDb.set_voice_channel(pid, 123)
        assert PuzzleJsonDb.get_by_attr(voice_channel_id=123).id == pid
        PuzzleJsonDb.set_voice_channel(pid, None)
        assert not PuzzleJsonDb.get_by_attr(id=pid).voice_channel_id
    finally:
        _cleanup(guild)


def test_hunt_voice_fields_round_trip():
    guild = _guild()
    try:
        hid = _hunt(guild, "v2")
        fresh = HuntJsonDb.get_by_attr(id=hid)
        assert not fresh.voice_channel_id and not fresh.voice_per_puzzle
        HuntJsonDb.set_voice_channel(hid, 456)
        HuntJsonDb.set_voice_per_puzzle(hid, True)
        got = HuntJsonDb.get_by_attr(voice_channel_id=456)
        assert got.id == hid and got.voice_channel_id == 456 and got.voice_per_puzzle
        HuntJsonDb.set_voice_channel(hid, None)
        HuntJsonDb.set_voice_per_puzzle(hid, False)
        got = HuntJsonDb.get_by_attr(id=hid)
        assert not got.voice_channel_id and not got.voice_per_puzzle
    finally:
        _cleanup(guild)


def test_sweep_query_ignores_unsolved_and_recent():
    guild = _guild()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    ago = lambda m: now - datetime.timedelta(minutes=m)  # noqa: E731
    try:
        live = _hunt(guild, "v3")
        old = _hunt(guild, "v3 archived", archived=1)
        due = _puzzle(live, "due", voice=11, solved=1, solve_time=ago(20))
        _puzzle(live, "recent", voice=12, solved=1, solve_time=ago(5))
        _puzzle(live, "unsolved", voice=13, solved=0, solve_time=None)
        _puzzle(live, "unsolved stale", voice=15, solved=0, solve_time=ago(20))
        _puzzle(live, "no voice", voice=None, solved=1, solve_time=ago(20))
        _puzzle(old, "archived", voice=14, solved=1, solve_time=ago(20))
        got = PuzzleJsonDb.voice_due(ago(15))
        mine = [p.id for p in got if p.hunt_id in (live, old)]
        assert mine == [due]
    finally:
        _cleanup(guild)


def test_with_voice_lists_only_this_hunts_voice_puzzles():
    guild = _guild()
    try:
        a = _hunt(guild, "v4a")
        b = _hunt(guild, "v4b")
        a1 = _puzzle(a, "a1", voice=21)
        a2 = _puzzle(a, "a2", voice=22)
        _puzzle(a, "a3 none", voice=None)
        _puzzle(a, "a4 zero", voice=0)
        b1 = _puzzle(b, "b1", voice=23)
        assert sorted(p.id for p in PuzzleJsonDb.with_voice(a)) == sorted([a1, a2])
        assert [p.id for p in PuzzleJsonDb.with_voice(b)] == [b1]
    finally:
        _cleanup(guild)


def test_a_stale_full_commit_never_clears_stored_voice_ids():
    """The narrow setters are the ONLY writers: a puzzle/hunt object loaded before
    its voice channel was made, then saved whole, must not wipe the id (else a
    solved puzzle's voice channel would never be cleaned up)."""
    guild = _guild()
    try:
        hid = _hunt(guild, "voice stale")
        pid = _puzzle(hid, "stale puzzle")
        stale_p = PuzzleJsonDb.get_by_attr(id=pid)
        stale_h = HuntJsonDb.get_by_attr(id=hid)
        PuzzleJsonDb.set_voice_channel(pid, 111)
        HuntJsonDb.set_voice_channel(hid, 222)
        HuntJsonDb.set_voice_per_puzzle(hid, True)
        PuzzleJsonDb.commit(stale_p)
        HuntJsonDb.commit(stale_h)
        assert PuzzleJsonDb.get_by_attr(id=pid).voice_channel_id == 111
        h = HuntJsonDb.get_by_attr(id=hid)
        assert h.voice_channel_id == 222 and h.voice_per_puzzle
    finally:
        _cleanup(guild)


def test_by_voice_channel_lookups_find_and_return_none():
    guild = _guild()
    try:
        hid = _hunt(guild, "v-lookup")
        pid = _puzzle(hid, "p", voice=4242)
        HuntJsonDb.set_voice_channel(hid, 4343)
        assert PuzzleJsonDb.by_voice_channel(4242).id == pid
        assert HuntJsonDb.by_voice_channel(4343).id == hid
        assert PuzzleJsonDb.by_voice_channel(4343) is None
        assert HuntJsonDb.by_voice_channel(4242) is None
    finally:
        _cleanup(guild)


def test_narrow_voice_channel_id_getters():
    guild = _guild()
    try:
        hid = _hunt(guild, "v-getters")
        pid = _puzzle(hid, "p", voice=5151)
        bare = _puzzle(hid, "bare")
        assert PuzzleJsonDb.voice_channel_id(pid) == 5151
        assert not PuzzleJsonDb.voice_channel_id(bare)
        assert not PuzzleJsonDb.voice_channel_id(-1)
        assert not HuntJsonDb.voice_channel_id(hid)
        HuntJsonDb.set_voice_channel(hid, 5252)
        assert HuntJsonDb.voice_channel_id(hid) == 5252
        assert not HuntJsonDb.voice_channel_id(-1)
    finally:
        _cleanup(guild)
