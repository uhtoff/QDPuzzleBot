"""T1: message deletion cascades to attachments via the FK (migration 037).

LIVE MySQL (puzzlebot_test) -- follows the live-DB convention of
tests/test_tag_edges.py. The cascade is a database guarantee, so it cannot be
mocked: the point of the test is that the DATABASE removes the attachment rows,
not that Python issued a second DELETE.
"""

from bot.store import MessageDb, mydb


def _insert_message(message_id):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO messages (id, channel_id, guild_id, author_id, author_name, "
        "kind, content, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(3))",
        (message_id, 900001, 900002, 900003, "cascade-test", 0, "hello"),
    )
    cur.close()


def _insert_attachment(attachment_id, message_id, local_path):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO message_attachments (id, message_id, filename, size, "
        "source_url, local_path) VALUES (%s,%s,%s,%s,%s,%s)",
        (attachment_id, message_id, "a.png", 1, "http://x/a.png", local_path),
    )
    cur.close()


def _attachment_count(message_id):
    cur = mydb.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM message_attachments WHERE message_id = %s", (message_id,)
    )
    n = cur.fetchone()[0]
    cur.close()
    return n


def test_delete_message_cascades_to_attachments_and_returns_paths():
    message_id = 99000000000000001
    try:
        _insert_message(message_id)
        # TWO attachments: one row cannot prove the delete removed "all" of them,
        # and a truncating implementation would pass with one.
        _insert_attachment(99000000000000011, message_id, "a/one.png")
        _insert_attachment(99000000000000012, message_id, "a/two.png")
        assert _attachment_count(message_id) == 2

        paths = MessageDb.delete_message(message_id)

        assert sorted(paths) == ["a/one.png", "a/two.png"]
        assert _attachment_count(message_id) == 0
    finally:
        cur = mydb.cursor()
        cur.execute("DELETE FROM message_attachments WHERE message_id = %s", (message_id,))
        cur.execute("DELETE FROM messages WHERE id = %s", (message_id,))
        cur.close()


def test_delete_message_leaves_other_messages_attachments_alone():
    """A cascade keyed on the wrong column would take everything with it."""
    doomed_id = 99000000000000002
    survivor_id = 99000000000000003
    try:
        _insert_message(doomed_id)
        _insert_message(survivor_id)
        _insert_attachment(99000000000000021, doomed_id, "a/doomed.png")
        _insert_attachment(99000000000000022, survivor_id, "a/survivor.png")

        MessageDb.delete_message(doomed_id)

        assert _attachment_count(doomed_id) == 0
        assert _attachment_count(survivor_id) == 1
    finally:
        cur = mydb.cursor()
        for mid in (doomed_id, survivor_id):
            cur.execute("DELETE FROM message_attachments WHERE message_id = %s", (mid,))
            cur.execute("DELETE FROM messages WHERE id = %s", (mid,))
        cur.close()
