from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def test_safe_filename_strips_path_separators():
    from bot.core.message_media import safe_filename
    assert safe_filename("../../etc/passwd") == "etc_passwd"
    assert safe_filename("a b.png") == "a b.png"
    assert safe_filename("") == "file"


def test_attachment_relpath_layout():
    from bot.core.message_media import attachment_relpath
    assert attachment_relpath(1, 2, 3, "a.png") == "1/2/3_a.png"


async def test_download_attachment_writes_bytes_and_returns_true(tmp_path):
    from bot.core.message_media import download_attachment
    resp = MagicMock()
    resp.headers = {"Content-Length": "3"}
    resp.content.iter_chunked = lambda n: _aiter([b"abc"])
    session = MagicMock()
    session.get.return_value.__aenter__ = AsyncMock(return_value=resp)
    session.get.return_value.__aexit__ = AsyncMock(return_value=False)
    dest = tmp_path / "x" / "y.png"
    ok = await download_attachment(session, "https://cdn/x", dest, max_bytes=10)
    assert ok is True and dest.read_bytes() == b"abc"


async def test_download_attachment_rejects_oversize(tmp_path):
    from bot.core.message_media import download_attachment
    resp = MagicMock()
    resp.headers = {"Content-Length": "999"}
    session = MagicMock()
    session.get.return_value.__aenter__ = AsyncMock(return_value=resp)
    session.get.return_value.__aexit__ = AsyncMock(return_value=False)
    dest = tmp_path / "x" / "y.png"
    ok = await download_attachment(session, "https://cdn/x", dest, max_bytes=10)
    assert ok is False and not dest.exists()


async def _aiter(items):
    for i in items:
        yield i
