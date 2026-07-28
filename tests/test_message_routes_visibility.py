from unittest.mock import AsyncMock, MagicMock, patch


async def test_channel_messages_visible_uses_hunt_gate():
    from bot.web.api import _channel_messages_visible
    with patch("bot.web.api.data.get_hunt_by_channel_id", new=AsyncMock(return_value={"uid": "H"})), \
         patch("bot.web.api.data.get_guilds_map", new=AsyncMock(return_value={})), \
         patch("bot.web.api.hunt_visible", return_value=True) as hv:
        ok = await _channel_messages_visible(100, membership={42}, roles_by_guild={})
    assert ok is True and hv.called


async def test_channel_messages_hidden_when_no_hunt():
    from bot.web.api import _channel_messages_visible
    with patch("bot.web.api.data.get_hunt_by_channel_id", new=AsyncMock(return_value=None)):
        ok = await _channel_messages_visible(100, membership={42}, roles_by_guild={})
    assert ok is False


async def _call_attachment_bytes(content_type, filename, tmp_path):
    from bot.web import api
    att = {"id": 9, "filename": filename, "content_type": content_type,
           "local_path": "c/9_f", "download_status": 1, "channel_id": 100}
    (tmp_path / "c").mkdir(); (tmp_path / "c" / "9_f").write_bytes(b"x")
    with patch("bot.web.api.current_user", return_value={"id": 1}), \
         patch("bot.web.api.data.get_attachment_with_channel", new=AsyncMock(return_value=att)), \
         patch("bot.web.api.resolve_membership", new=AsyncMock(return_value=({42}, {}))), \
         patch("bot.web.api._channel_messages_visible", new=AsyncMock(return_value=True)), \
         patch("bot.core.message_media.media_root", return_value=tmp_path), \
         patch("bot.web.api.send_file", new=AsyncMock(return_value="RESP")) as sf:
        # Call the undecorated view (bypasses @login_required's request-context lookup).
        await api.attachment_bytes.__wrapped__(9)
    return sf.call_args.kwargs


async def test_attachment_non_image_forces_download_with_filename(tmp_path):
    kwargs = await _call_attachment_bytes("application/pdf", "report.pdf", tmp_path)
    assert kwargs["as_attachment"] is True
    assert kwargs["attachment_filename"] == "report.pdf"


async def test_attachment_image_served_inline(tmp_path):
    kwargs = await _call_attachment_bytes("image/png", "pic.png", tmp_path)
    assert kwargs["as_attachment"] is False


async def test_attachment_bytes_sets_nosniff(tmp_path):
    """User-supplied bytes served under our own origin: without nosniff a file
    uploaded as image/png but sniffed as HTML runs as script on the dashboard.
    The newer media routes set it; this one is the hole.

    Same arrangement as _call_attachment_bytes, but the response object is kept
    rather than send_file's call kwargs, and it carries real headers.
    """
    from bot.web import api
    att = {"id": 9, "filename": "pic.png", "content_type": "image/png",
           "local_path": "c/9_f", "download_status": 1, "channel_id": 100}
    (tmp_path / "c").mkdir(); (tmp_path / "c" / "9_f").write_bytes(b"x")
    with patch("bot.web.api.current_user", return_value={"id": 1}), \
         patch("bot.web.api.data.get_attachment_with_channel", new=AsyncMock(return_value=att)), \
         patch("bot.web.api.resolve_membership", new=AsyncMock(return_value=({42}, {}))), \
         patch("bot.web.api._channel_messages_visible", new=AsyncMock(return_value=True)), \
         patch("bot.core.message_media.media_root", return_value=tmp_path), \
         patch("bot.web.api.send_file",
               new=AsyncMock(return_value=MagicMock(headers={}))):
        resp = await api.attachment_bytes.__wrapped__(9)
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
