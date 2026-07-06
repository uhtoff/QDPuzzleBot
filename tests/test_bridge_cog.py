"""Bridge cog: kind tagging, puzzle-channel gate, and worker DB ops."""
from unittest.mock import MagicMock, patch


def _make_cog():
    from bot.cogs.bridge import MessageBridge
    bot = MagicMock()
    bot.application_id = 999
    cog = MessageBridge(bot)
    cog._puzzle_channels = {100}   # only channel 100 is a tracked puzzle channel
    cog._worker = MagicMock()      # simulate the running/enabled state
    return cog


def _msg(*, mid, cid, author_id, is_bot, content, guild_id=42):
    m = MagicMock()
    m.id = mid
    m.channel = MagicMock(); m.channel.id = cid
    m.guild = MagicMock(); m.guild.id = guild_id
    m.author = MagicMock(); m.author.id = author_id; m.author.bot = is_bot
    m.author.display_name = "Ann"
    m.author.display_avatar = MagicMock(); m.author.display_avatar.url = "http://a/av.png"
    m.content = content
    m.created_at = MagicMock()
    m.created_at.replace.return_value = "2026-07-06 00:00:00"
    m.reference = None
    m.attachments = []
    m.embeds = []
    return m


def test_kind_human_bot_command():
    from bot.cogs.bridge import KIND_HUMAN, KIND_THIS_BOT, KIND_OTHER_BOT, KIND_COMMAND
    cog = _make_cog()
    assert cog._kind_for(_msg(mid=1, cid=100, author_id=1, is_bot=False, content="hi")) == KIND_HUMAN
    assert cog._kind_for(_msg(mid=1, cid=100, author_id=1, is_bot=False, content="!s ANSWER")) == KIND_COMMAND
    assert cog._kind_for(_msg(mid=1, cid=100, author_id=999, is_bot=True, content="x")) == KIND_THIS_BOT
    assert cog._kind_for(_msg(mid=1, cid=100, author_id=5, is_bot=True, content="x")) == KIND_OTHER_BOT


async def test_on_message_gate_drops_non_puzzle_channel():
    cog = _make_cog()
    # 777 is unknown -> lazy DB lookup says "not a puzzle channel"; dropped + negative-cached.
    with patch.object(cog, "_lookup_puzzle_channel", return_value=False) as look:
        await cog.on_message(_msg(mid=1, cid=777, author_id=1, is_bot=False, content="hi"))
    assert cog._queue.empty()
    look.assert_called_once_with(777)
    assert 777 in cog._non_puzzle_channels


async def test_on_message_lazily_tracks_new_puzzle_channel():
    cog = _make_cog()
    # 200 isn't in the cached set yet; the lazy lookup finds it and captures immediately.
    with patch.object(cog, "_lookup_puzzle_channel", return_value=True) as look:
        await cog.on_message(_msg(mid=9, cid=200, author_id=1, is_bot=False, content="hi"))
    assert cog._queue.get_nowait()["channel_id"] == 200
    assert 200 in cog._puzzle_channels
    # Second message in the same channel is served from cache, no extra DB hit.
    with patch.object(cog, "_lookup_puzzle_channel", return_value=True) as look2:
        await cog.on_message(_msg(mid=10, cid=200, author_id=1, is_bot=False, content="again"))
    look2.assert_not_called()
    assert look.call_count == 1


async def test_on_message_enqueues_insert_for_puzzle_channel():
    cog = _make_cog()
    await cog.on_message(_msg(mid=1, cid=100, author_id=1, is_bot=False, content="hi"))
    rec = cog._queue.get_nowait()
    assert rec["op"] == "insert" and rec["id"] == 1 and rec["channel_id"] == 100


async def test_listeners_inert_when_worker_not_running():
    # Disabled/not-started state: cog_load never ran, so _worker is None.
    cog = _make_cog()
    cog._worker = None
    assert cog._worker is None

    edit_payload = MagicMock(); edit_payload.message_id = 1; edit_payload.data = {"content": "x"}
    await cog.on_raw_message_edit(edit_payload)

    del_payload = MagicMock(); del_payload.message_id = 1
    del_payload.channel_id = 100; del_payload.guild_id = 42
    await cog.on_raw_message_delete(del_payload)

    assert cog._queue.empty()


async def test_worker_insert_writes_row():
    cog = _make_cog()
    rec = {"op": "insert", "id": 1, "channel_id": 100, "guild_id": 42, "author_id": 1,
           "author_name": "Ann", "author_avatar": None, "kind": 0, "content": "hi",
           "reference_id": None, "created_at": "2026-07-06 00:00:00", "attachments": []}
    with patch("bot.cogs.bridge.MessageDb") as db:
        await cog._handle(rec)
    db.write.assert_called_once()
    assert db.write.call_args.kwargs["id"] == 1


def test_normalize_embeds_reduces_to_subset():
    from bot.cogs.bridge import _normalize_embeds
    raw = [{
        "title": "SOLVED!", "description": "great work", "url": "http://h/p",
        "color": 0x00ff00, "type": "rich",  # 'type' dropped
        "fields": [{"name": "Follow-up", "value": "use !unsolve", "inline": True}],
        "footer": {"text": "pinned", "icon_url": "http://i"},  # icon_url dropped
    }]
    assert _normalize_embeds(raw) == [{
        "title": "SOLVED!", "description": "great work", "url": "http://h/p",
        "color": 0x00ff00,
        "fields": [{"name": "Follow-up", "value": "use !unsolve", "inline": True}],
        "footer": "pinned",
    }]


def test_normalize_embeds_empty_and_junk():
    from bot.cogs.bridge import _normalize_embeds, _embeds_json
    assert _normalize_embeds([]) == []
    assert _normalize_embeds(None) == []
    assert _embeds_json([]) is None  # nothing worth storing


async def test_on_message_captures_embed_json():
    import json
    from bot.cogs.bridge import _normalize_embeds
    cog = _make_cog()
    embed = MagicMock()
    embed.to_dict.return_value = {"title": "SOLVED!", "description": "yay"}
    msg = _msg(mid=5, cid=100, author_id=999, is_bot=True, content="")
    msg.embeds = [embed]
    await cog.on_message(msg)
    rec = cog._queue.get_nowait()
    assert json.loads(rec["embeds"]) == _normalize_embeds([{"title": "SOLVED!", "description": "yay"}])


async def test_worker_insert_passes_embeds():
    cog = _make_cog()
    rec = {"op": "insert", "id": 1, "channel_id": 100, "guild_id": 42, "author_id": 999,
           "author_name": "Bot", "author_avatar": None, "kind": 1, "content": "",
           "embeds": '[{"title": "SOLVED!"}]', "reference_id": None,
           "created_at": "2026-07-06 00:00:00", "attachments": []}
    with patch("bot.cogs.bridge.MessageDb") as db:
        await cog._handle(rec)
    assert db.write.call_args.kwargs["embeds"] == '[{"title": "SOLVED!"}]'


async def test_worker_delete_unlinks_files(tmp_path):
    cog = _make_cog()
    f = tmp_path / "g" / "c" / "1_a.png"; f.parent.mkdir(parents=True); f.write_bytes(b"x")
    with patch("bot.cogs.bridge.MessageDb") as db, \
         patch("bot.cogs.bridge.media_root", return_value=tmp_path):
        db.delete_message.return_value = ["g/c/1_a.png"]
        await cog._handle({"op": "delete", "id": 1})
    db.delete_message.assert_called_once_with(1)
    assert not f.exists()
