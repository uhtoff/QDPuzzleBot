"""bot.web.unread: the pure half of POST /api/hunts/<uid>/unread.

Markers are Discord snowflakes (time-sortable message ids). The extension sends a
`baseline` that applies to every channel plus per-channel `read` markers; the
server folds in the user's own last message per channel and builds the list.
"""
import datetime

from bot.web import data, unread

SNOW_A = (1_700_000_000_000 - 1420070400000) << 22   # 2023-11-14T22:13:20Z
SNOW_B = SNOW_A + (60_000 << 22)                     # one minute later
T_A = datetime.datetime(2023, 11, 14, 22, 13, 20)


def test_epoch_constant_is_discords():
    assert unread.DISCORD_EPOCH_MS == 1420070400000


def test_snowflake_time_is_naive_utc():
    assert unread.snowflake_time(SNOW_A) == T_A
    assert unread.snowflake_time(SNOW_B) == datetime.datetime(2023, 11, 14, 22, 14, 20)


# ---- parse_markers ----

def test_parse_markers_accepts_digit_strings():
    assert unread.parse_markers({"baseline": "10", "read": {"5": "20", "6": "30"}}) == (10, {5: 20, 6: 30})


def test_parse_markers_read_is_optional():
    assert unread.parse_markers({"baseline": "10"}) == (10, {})


def test_parse_markers_rejects_missing_body():
    assert unread.parse_markers(None) is None


def test_parse_markers_rejects_non_dict_body():
    assert unread.parse_markers(["10"]) is None


def test_parse_markers_rejects_missing_baseline():
    assert unread.parse_markers({"read": {}}) is None


def test_parse_markers_rejects_int_baseline():
    assert unread.parse_markers({"baseline": 10}) is None


def test_parse_markers_rejects_non_digit_baseline():
    assert unread.parse_markers({"baseline": "1x"}) is None


def test_parse_markers_rejects_non_dict_read():
    assert unread.parse_markers({"baseline": "10", "read": ["5"]}) is None


def test_parse_markers_rejects_non_digit_read_key():
    assert unread.parse_markers({"baseline": "10", "read": {"a": "20"}}) is None


def test_parse_markers_rejects_int_read_value():
    assert unread.parse_markers({"baseline": "10", "read": {"5": 20}}) is None


def test_parse_markers_rejects_spaced_read_value():
    assert unread.parse_markers({"baseline": "10", "read": {"5": "2 0"}}) is None


def test_parse_markers_caps_read_entries():
    at_cap = {str(i): "1" for i in range(1, unread.MAX_READ_ENTRIES + 1)}
    over = {**at_cap, str(unread.MAX_READ_ENTRIES + 1): "1"}
    parsed = unread.parse_markers({"baseline": "1", "read": at_cap})
    assert parsed is not None and len(parsed[1]) == unread.MAX_READ_ENTRIES
    assert unread.parse_markers({"baseline": "1", "read": over}) is None


# ---- effective_markers ----

def test_effective_markers_takes_the_largest_per_channel():
    out = unread.effective_markers([1, 2, 3, 4], baseline=100,
                                   read={1: 150, 2: 50}, own_last={2: 120, 3: 90, 4: 300})
    assert out == {1: 150, 2: 120, 3: 100, 4: 300}


# ---- build_channels ----

HUNT = {"name": "Saltmarsh", "channel_id": 800}
GUILD = 777
PUZZLE_KEYS = {"channel_id", "kind", "puzzle_id", "name", "rounds", "solved", "solution",
               "newly_solved", "solved_at", "solved_by", "sheet_url", "discord_url", "unread",
               "last"}


def _bc_puz(**kw):
    """A puzzle as data.get_hunt_puzzles emits it (channel_id is a string there)."""
    base = {"id": 1, "name": "Alpha", "channel_id": "900", "rounds": [{"name": "R1"}],
            "solved": False, "solution": None, "solve_time": None, "solved_by": None,
            "sheet_url": "https://docs.google.com/spreadsheets/d/x/edit#gid=1",
            "discord_url": "https://discord.com/channels/777/900", "parent_puzzle_id": None}
    return {**base, **kw}


def _bc_build(puzzles, *, hunt=HUNT, markers=None, solve_markers=None, counts=None, lasts=None):
    if markers is None:
        ids = ([hunt["channel_id"]] if hunt["channel_id"] else []) + \
              [int(p["channel_id"]) for p in puzzles if p["channel_id"]]
        markers = {c: SNOW_A for c in ids}
    return unread.build_channels(hunt, puzzles, GUILD, markers, counts or {}, lasts or {},
                                 solve_markers=markers if solve_markers is None else solve_markers)


def _bc_by_channel(out):
    return {e["channel_id"]: e for e in out}


def test_build_channels_puts_general_first_then_puzzles_in_input_order():
    out = _bc_build([_bc_puz(id=2, name="Bravo", channel_id="902"),
                     _bc_puz(id=1, name="Alpha", channel_id="901")])
    assert [e["channel_id"] for e in out] == ["800", "902", "901"]
    general = out[0]
    assert general["kind"] == "general"
    assert general["name"] == "Saltmarsh (general)"
    assert general["puzzle_id"] is None
    assert general["rounds"] == []
    assert general["solved"] is False
    assert general["sheet_url"] is None
    assert general["discord_url"] == "https://discord.com/channels/777/800"
    assert [(e["kind"], e["puzzle_id"], e["name"]) for e in out[1:]] == [
        ("puzzle", 2, "Bravo"), ("puzzle", 1, "Alpha")]


def test_build_channels_without_a_general_channel():
    out = _bc_build([_bc_puz()], hunt={"name": "Saltmarsh", "channel_id": None})
    assert [e["kind"] for e in out] == ["puzzle"]


def test_build_channels_skips_channelless_and_mini_puzzles():
    out = _bc_build([_bc_puz(id=1, channel_id=None),
                     _bc_puz(id=2, channel_id="902", parent_puzzle_id=5),
                     _bc_puz(id=3, channel_id="903")],
                    markers={800: SNOW_A, 902: SNOW_A, 903: SNOW_A})
    assert [e["channel_id"] for e in out] == ["800", "903"]


def test_build_channels_maps_each_count_and_last_to_its_own_channel():
    t1 = datetime.datetime(2026, 11, 24, 14, 10)
    t2 = datetime.datetime(2026, 11, 24, 14, 20)
    counts = {900: {"unread": 3, "last_id": 55}, 901: {"unread": 1, "last_id": 66}}
    lasts = {55: {"id": 55, "author_name": "Mina", "content": "grid refs", "created_at": t1},
             66: {"id": 66, "author_name": "Jon", "content": "stuck", "created_at": t2}}
    out = _bc_by_channel(_bc_build([_bc_puz(id=1, channel_id="900"), _bc_puz(id=2, channel_id="901"),
                                    _bc_puz(id=3, channel_id="902")], counts=counts, lasts=lasts))
    assert out["900"]["unread"] == 3
    assert out["900"]["last"] == {"id": "55", "author_name": "Mina", "content": "grid refs",
                                  "created_at": data._iso(t1)}
    assert out["901"]["unread"] == 1
    assert out["901"]["last"] == {"id": "66", "author_name": "Jon", "content": "stuck",
                                  "created_at": data._iso(t2)}
    assert out["902"]["unread"] == 0
    assert out["902"]["last"] is None
    assert out["800"]["unread"] == 0


def test_build_channels_null_content_becomes_empty_string():
    counts = {900: {"unread": 1, "last_id": 55}}
    lasts = {55: {"id": 55, "author_name": "Mina", "content": None,
                  "created_at": datetime.datetime(2026, 11, 24, 14, 10)}}
    out = _bc_by_channel(_bc_build([_bc_puz()], counts=counts, lasts=lasts))
    assert out["900"]["last"]["content"] == ""


def test_build_channels_round_names_in_order():
    out = _bc_by_channel(_bc_build([_bc_puz(rounds=[{"name": "R1"}, {"name": "R2"}])]))
    assert out["900"]["rounds"] == ["R1", "R2"]


def test_build_channels_newly_solved_compares_solve_time_to_the_marker():
    minute = datetime.timedelta(minutes=1)
    after = data._iso(T_A + minute)
    before = data._iso(T_A - minute)
    out = _bc_by_channel(_bc_build([
        _bc_puz(id=1, channel_id="901", solved=True, solution="AFTER", solve_time=after),
        _bc_puz(id=2, channel_id="902", solved=True, solution="BEFORE", solve_time=before),
        _bc_puz(id=3, channel_id="903", solved=False, solve_time=None),
        _bc_puz(id=4, channel_id="904", solved=True, solution="NOTIME", solve_time=None),
    ]))
    assert out["901"]["newly_solved"] is True
    assert out["902"]["newly_solved"] is False
    assert out["903"]["newly_solved"] is False
    assert out["904"]["newly_solved"] is False
    assert (out["901"]["solved"], out["901"]["solution"]) == (True, "AFTER")


def test_build_channels_newly_solved_uses_each_channels_own_marker():
    """Same solve time, different markers: only the channel read before it is new."""
    solve = data._iso(T_A + datetime.timedelta(seconds=30))
    out = _bc_by_channel(_bc_build(
        [_bc_puz(id=1, channel_id="901", solved=True, solution="X", solve_time=solve),
         _bc_puz(id=2, channel_id="902", solved=True, solution="Y", solve_time=solve)],
        markers={800: SNOW_A, 901: SNOW_A, 902: SNOW_B}))
    assert out["901"]["newly_solved"] is True
    assert out["902"]["newly_solved"] is False


def test_build_channels_newly_solved_ignores_the_users_own_messages():
    """Every solve shows, even one the user typed with !s: the user's own message
    (folded into `markers`, which drive the counts) must not hide it. The solve is
    judged against `solve_markers` (baseline + read markers only)."""
    solve = data._iso(T_A + datetime.timedelta(seconds=30))
    out = _bc_by_channel(_bc_build(
        [_bc_puz(id=1, channel_id="901", solved=True, solution="X", solve_time=solve),
         _bc_puz(id=2, channel_id="902", solved=True, solution="Y", solve_time=solve)],
        markers={800: SNOW_A, 901: SNOW_B, 902: SNOW_B},       # own message after the solve
        solve_markers={800: SNOW_A, 901: SNOW_A, 902: SNOW_B}))  # 902 really was read after it
    assert out["901"]["newly_solved"] is True
    assert out["902"]["newly_solved"] is False


def test_build_channels_solved_at_is_the_solve_time_of_solved_puzzles_only():
    solve = data._iso(T_A)
    out = _bc_by_channel(_bc_build([
        _bc_puz(id=1, channel_id="901", solved=True, solution="X", solve_time=solve),
        _bc_puz(id=2, channel_id="902", solved=False, solve_time=None),
    ]))
    assert out["901"]["solved_at"] == solve
    assert out["902"]["solved_at"] is None
    assert out["800"]["solved_at"] is None


def test_build_channels_solved_by_names_the_solver_of_solved_puzzles_only():
    out = _bc_by_channel(_bc_build([
        _bc_puz(id=1, channel_id="901", solved=True, solution="MINE",
                solve_time=data._iso(T_A), solved_by="Uhtoff"),
        _bc_puz(id=2, channel_id="902", solved=True, solution="OLD",
                solve_time=data._iso(T_A), solved_by=None),        # solved before 063
        _bc_puz(id=3, channel_id="903", solved=False, solved_by="Stale"),
    ]))
    assert out["901"]["solved_by"] == "Uhtoff"
    assert out["902"]["solved_by"] is None
    assert out["903"]["solved_by"] is None
    assert out["800"]["solved_by"] is None


def test_build_channels_entry_has_exactly_the_pinned_keys():
    out = _bc_build([_bc_puz()])
    assert set(out[0]) == PUZZLE_KEYS
    assert set(out[1]) == PUZZLE_KEYS
