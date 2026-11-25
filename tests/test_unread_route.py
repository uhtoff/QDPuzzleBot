"""POST /api/hunts/<uid>/unread: the chat extension's unread summary."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from tests.conftest import make_web_app, start_patches, stop_patches

URL = "/api/hunts/H1/unread"
HUNT = {"id": 10, "uid": "H1", "name": "Saltmarsh", "channel_id": 800, "archived": 0, "guild_id": 1}


def _ur_puz(pid, channel):
    return {"id": pid, "name": f"P{pid}", "channel_id": channel, "rounds": [],
            "solved": False, "solution": None, "solve_time": None, "sheet_url": None,
            "discord_url": None, "parent_puzzle_id": None}


@asynccontextmanager
async def _ur_client(*, load=(HUNT, {"guild_snowflake": 777})):
    app = make_web_app("api")
    mocks = {
        "load": AsyncMock(return_value=load),
        "puzzles": AsyncMock(return_value=[_ur_puz(1, "900"), _ur_puz(2, "901")]),
        "own": AsyncMock(return_value={901: 500}),
        "counts": AsyncMock(return_value={900: {"unread": 2, "last_id": 600},
                                          901: {"unread": 4, "last_id": 700}}),
        "lasts": AsyncMock(return_value={}),
    }
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api._load_visible_hunt", mocks["load"]),
        patch("bot.web.api.data.get_hunt_puzzles", mocks["puzzles"]),
        patch("bot.web.api.data.get_own_last_message_ids", mocks["own"]),
        patch("bot.web.api.data.get_unread_counts", mocks["counts"]),
        patch("bot.web.api.data.get_messages_by_ids", mocks["lasts"]),
    ]
    start_patches(patches)
    try:
        c = app.test_client()
        c.m = mocks
        yield c
    finally:
        stop_patches(patches)


async def test_unread_folds_markers_and_returns_channels_in_order():
    async with _ur_client() as c:
        resp = await c.post(URL, json={"baseline": "100", "read": {"900": "300"}})
        assert resp.status_code == 200
        body = await resp.get_json()
    c.m["load"].assert_awaited_once_with("H1")
    assert c.m["puzzles"].call_args.args == (HUNT, 777)
    assert c.m["own"].call_args.args == ([800, 900, 901], 1, 100)  # bounded by the baseline
    # general -> baseline; 900 -> its read marker; 901 -> the user's own later message
    c.m["counts"].assert_awaited_once_with({800: 100, 900: 300, 901: 500})
    assert sorted(c.m["lasts"].call_args.args[0]) == [600, 700]
    chans = body["channels"]
    assert [(e["channel_id"], e["kind"], e["unread"]) for e in chans] == [
        ("800", "general", 0), ("900", "puzzle", 2), ("901", "puzzle", 4)]


async def test_unread_missing_hunt_is_404():
    async with _ur_client(load=(None, 404)) as c:
        resp = await c.post(URL, json={"baseline": "100"})
        assert resp.status_code == 404
        assert await resp.get_json() == {"error": "not_found_or_forbidden"}
    c.m["puzzles"].assert_not_awaited()


async def test_unread_forbidden_hunt_is_also_404():
    async with _ur_client(load=(None, 403)) as c:
        resp = await c.post(URL, json={"baseline": "100"})
        assert resp.status_code == 404
        assert await resp.get_json() == {"error": "not_found_or_forbidden"}
    c.m["puzzles"].assert_not_awaited()


async def test_unread_bad_markers_is_400():
    async with _ur_client() as c:
        resp = await c.post(URL, json={"baseline": 5})
        assert resp.status_code == 400
        assert await resp.get_json() == {"error": "bad_markers"}
    c.m["counts"].assert_not_awaited()


async def test_unread_get_is_not_allowed():
    async with _ur_client() as c:
        resp = await c.get(URL)
        assert resp.status_code == 405


async def test_unread_a_solve_after_the_users_own_message_hides_nothing():
    """The own-message rule drives the counts, but not newly_solved: a puzzle the
    user solved with !s (an own message at the solve) still shows as solved."""
    snow_a = (1_700_000_000_000 - 1420070400000) << 22      # 2023-11-14T22:13:20Z
    snow_b = snow_a + (60_000 << 22)                         # a minute later
    solved = {**_ur_puz(2, "901"), "solved": True, "solution": "FOUND",
              "solve_time": "2023-11-14T22:13:50+00:00"}
    async with _ur_client() as c:
        c.m["puzzles"].return_value = [_ur_puz(1, "900"), solved]
        c.m["own"].return_value = {901: snow_b}
        resp = await c.post(URL, json={"baseline": str(snow_a), "read": {}})
        body = await resp.get_json()
    c.m["counts"].assert_awaited_once_with({800: snow_a, 900: snow_a, 901: snow_b})
    entry = {e["channel_id"]: e for e in body["channels"]}["901"]
    assert entry["newly_solved"] is True
    assert entry["solved_at"] == "2023-11-14T22:13:50+00:00"


async def test_unread_returns_the_users_own_id_as_a_string():
    """The extension skips the user's own live messages by author id. A Discord id
    outgrows a JS number (2**53), so it must arrive as a string."""
    big = 123456789012345678
    async with _ur_client() as c:
        with patch("bot.web.auth.session", {"user": {"id": big, "username": "t"}}):
            resp = await c.post(URL, json={"baseline": "100", "read": {}})
            body = await resp.get_json()
    assert body["self_id"] == "123456789012345678"
