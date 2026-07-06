from unittest.mock import patch


async def test_get_channel_messages_returns_ascending_with_attachments():
    from bot.web import data
    msg_rows = [
        {"id": 2, "author_name": "Bo", "author_avatar": None, "author_id": 5, "kind": 0,
         "content": "second", "reference_id": None, "created_at": "t2", "edited_at": None},
        {"id": 1, "author_name": "Ann", "author_avatar": None, "author_id": 4, "kind": 0,
         "content": "first", "reference_id": None, "created_at": "t1", "edited_at": None},
    ]
    att_rows = [{"id": 9, "message_id": 2, "filename": "a.png", "content_type": "image/png",
                 "size": 3, "local_path": "1/2/9_a.png", "download_status": 1}]
    with patch("bot.web.data.fetch_all", side_effect=[msg_rows, att_rows]):
        out = await data.get_channel_messages(100, before=None, limit=50)
    assert [m["id"] for m in out] == ["1", "2"]       # ascending for render
    assert out[1]["attachments"][0]["id"] == "9"      # attachment attached to msg 2


async def test_get_hunt_by_channel_id_queries_join():
    from bot.web import data
    with patch("bot.web.data.fetch_one", return_value={"id": 7, "archived": 0}) as f:
        hunt = await data.get_hunt_by_channel_id(100)
    assert hunt["id"] == 7
    assert "JOIN puzzles" in f.call_args.args[0]
