"""MessageDb DAO: write/edit/delete + attachment rows, all against a mocked cursor."""
from unittest.mock import MagicMock


def _make_db():
    from bot.store.mysqldb import MySQLMessageDb
    mydb = MagicMock()
    cursor = MagicMock()
    mydb.cursor.return_value = cursor
    # delete_message's local_path SELECT goes through dict_cursor(); share the
    # same mock so tests can set fetchall() on one object regardless of which
    # accessor the implementation uses.
    mydb.dict_cursor.return_value = cursor
    return MySQLMessageDb(mydb=mydb), cursor


def test_write_inserts_with_upsert_and_commits():
    db, cursor = _make_db()
    db.write(id=10, channel_id=20, guild_id=30, author_id=40, author_name="Ann",
             author_avatar=None, kind=0, content="hi", reference_id=None,
             created_at="2026-07-06 00:00:00.000")
    sql = cursor.execute.call_args.args[0]
    assert "INSERT INTO messages" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert cursor.execute.call_args.args[1][0] == 10
    db.mydb.commit.assert_called_once()


def test_write_persists_embeds_json():
    db, cursor = _make_db()
    db.write(id=10, channel_id=20, guild_id=30, author_id=40, author_name="Bot",
             author_avatar=None, kind=1, content="", reference_id=None,
             created_at="2026-07-06 00:00:00.000", embeds='[{"title": "SOLVED!"}]')
    sql, params = cursor.execute.call_args.args
    assert "embeds" in sql
    assert '[{"title": "SOLVED!"}]' in params


def test_mark_edited_updates_content_and_edited_at():
    db, cursor = _make_db()
    db.mark_edited(10, "edited", "2026-07-06 00:01:00.000")
    sql, params = cursor.execute.call_args.args
    assert "UPDATE messages SET" in sql and "edited_at" in sql
    assert "embeds" not in sql           # content-only edit leaves embeds untouched
    assert params == ("edited", "2026-07-06 00:01:00.000", 10)


def test_mark_edited_rewrites_embeds_when_provided():
    db, cursor = _make_db()
    db.mark_edited(10, "edited", "2026-07-06 00:01:00.000", '[{"title": "X"}]')
    sql, params = cursor.execute.call_args.args
    assert "embeds" in sql
    assert params == ("edited", '[{"title": "X"}]', "2026-07-06 00:01:00.000", 10)


def test_channel_has_messages():
    db, cursor = _make_db()
    cursor.fetchone.return_value = {"1": 1}
    assert db.channel_has_messages(100) is True
    cursor.fetchone.return_value = None
    assert db.channel_has_messages(100) is False


def test_delete_message_returns_local_paths_and_deletes_only_messages_row():
    db, cursor = _make_db()
    cursor.fetchall.return_value = [{"local_path": "g/c/1_a.png"}, {"local_path": None}]
    paths = db.delete_message(10)
    assert paths == ["g/c/1_a.png"]  # None filtered out
    executed = " ".join(c.args[0] for c in cursor.execute.call_args_list)
    assert "DELETE FROM messages WHERE id" in executed
    # Migration 037's FK (ON DELETE CASCADE) is what removes the attachment
    # rows now; Python must not issue a second DELETE for them.
    assert "DELETE FROM message_attachments" not in executed


def test_add_attachment_inserts_pending():
    db, cursor = _make_db()
    db.add_attachment(id=1, message_id=10, filename="a.png", content_type="image/png",
                      size=123, source_url="https://cdn/x")
    sql, params = cursor.execute.call_args.args
    assert "INSERT INTO message_attachments" in sql
    assert params == (1, 10, "a.png", "image/png", 123, "https://cdn/x", 0)


def test_set_attachment_local_marks_done():
    db, cursor = _make_db()
    db.set_attachment_local(1, "g/c/1_a.png", 1)
    sql, params = cursor.execute.call_args.args
    assert "UPDATE message_attachments SET" in sql
    assert params == ("g/c/1_a.png", 1, 1)
