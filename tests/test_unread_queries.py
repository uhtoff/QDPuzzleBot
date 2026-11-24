"""The three unread-summary queries in bot.web.data, with fetch_all mocked."""
from unittest.mock import AsyncMock, patch

from bot.web import data


async def test_own_last_message_ids_maps_each_channel():
    rows = [{"channel_id": 900, "last_id": 7}, {"channel_id": 901, "last_id": 9}]
    with patch("bot.web.data.fetch_all", new=AsyncMock(return_value=rows)) as fa:
        out = await data.get_own_last_message_ids([900, 901], 42)
    assert out == {900: 7, 901: 9}
    sql, params = fa.call_args.args
    assert "author_id" in sql and "GROUP BY channel_id" in sql
    assert "AS last_id" in sql
    assert 42 in params and 900 in params and 901 in params


async def test_own_last_message_ids_empty_runs_no_query():
    with patch("bot.web.data.fetch_all", new=AsyncMock(return_value=[])) as fa:
        assert await data.get_own_last_message_ids([], 42) == {}
    fa.assert_not_awaited()


async def test_unread_counts_one_query_with_channel_marker_pairs():
    rows = [{"channel_id": 900, "unread": 2, "last_id": 60},
            {"channel_id": 901, "unread": 5, "last_id": 90}]
    with patch("bot.web.data.fetch_all", new=AsyncMock(return_value=rows)) as fa:
        out = await data.get_unread_counts({900: 50, 901: 70})
    fa.assert_awaited_once()
    sql, params = fa.call_args.args
    assert tuple(params) == (900, 50, 901, 70)
    assert "kind = 0" in sql and "GROUP BY channel_id" in sql
    assert out == {900: {"unread": 2, "last_id": 60}, 901: {"unread": 5, "last_id": 90}}


async def test_unread_counts_empty_runs_no_query():
    with patch("bot.web.data.fetch_all", new=AsyncMock(return_value=[])) as fa:
        assert await data.get_unread_counts({}) == {}
    fa.assert_not_awaited()


async def test_messages_by_ids_keys_by_id():
    rows = [{"id": 55, "author_name": "Mina", "content": "a", "created_at": None},
            {"id": 66, "author_name": "Jon", "content": "b", "created_at": None}]
    with patch("bot.web.data.fetch_all", new=AsyncMock(return_value=rows)) as fa:
        out = await data.get_messages_by_ids([55, 66])
    assert out == {55: rows[0], 66: rows[1]}
    sql, params = fa.call_args.args
    assert tuple(params) == (55, 66)
    assert "FROM messages" in sql


async def test_messages_by_ids_empty_runs_no_query():
    with patch("bot.web.data.fetch_all", new=AsyncMock(return_value=[])) as fa:
        assert await data.get_messages_by_ids([]) == {}
    fa.assert_not_awaited()
