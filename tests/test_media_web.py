"""Plan A: GET /api/media/<id> and POST /api/hunts/<uid>/media — auth, hunt
visibility, traversal guard, and (for the upload route) the content-sniffing
and size-cap boundary."""
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from werkzeug.datastructures import FileStorage


def _hunt(archived=False):
    return {"id": 10, "uid": "U1", "archived": archived, "guild_id": 1}


def _file(filename, data, content_type):
    """Quart 0.20's test client `files=` kwarg takes `FileStorage` objects,
    not the `(filename, bytes, content_type)` tuples Flask's test client
    accepts (confirmed against `quart.testing.utils.make_test_body_with_headers`,
    which reads `.filename`/`.headers`/`.read()` off each value)."""
    return FileStorage(stream=BytesIO(data), filename=filename, content_type=content_type)


@pytest.fixture
def web(tmp_path):
    from quart import Quart
    from bot.web.api import api_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"
    app.register_blueprint(api_bp)

    (tmp_path / "uploads" / "10").mkdir(parents=True)
    (tmp_path / "uploads" / "10" / "a_crop.png").write_bytes(b"\x89PNG\r\n\x1a\nbytes")

    mdb = MagicMock()
    mdb.get_by_id.return_value = SimpleNamespace(
        id=7, hunt_id=10, path="uploads/10/a_crop.png", filename="crop.png",
        content_type="image/png", size=13, origin="upload", created_at=None)

    hdb = MagicMock()
    hdb.get_by_attr.return_value = SimpleNamespace(id=10, uid="U1")

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api._load_visible_hunt", AsyncMock(return_value=(_hunt(), {}))),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.core.message_media.media_root", return_value=tmp_path),
        # `bot/core/media_store.py` does `from bot.core.message_media import
        # media_root` at module load, binding its OWN name in its own
        # namespace (see docs/agents/python.md — patch where it's looked up).
        # The GET route above re-imports media_root locally on every call, so
        # patching the source module is enough for it, but save_image_bytes
        # (used by the POST route below) reads the name bound at import time,
        # so it needs patching at its own lookup site too.
        patch("bot.core.media_store.media_root", return_value=tmp_path),
    ]
    started = [p.start() for p in patches]
    yield app, mdb, started
    for p in patches:
        p.stop()


@pytest.mark.asyncio
async def test_serves_the_bytes(web):
    app, _, _ = web
    client = app.test_client()
    resp = await client.get("/api/media/7")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("image/png")
    assert await resp.get_data() == b"\x89PNG\r\n\x1a\nbytes"


@pytest.mark.asyncio
async def test_missing_media_is_404(web):
    app, mdb, _ = web
    mdb.get_by_id.return_value = None
    resp = await app.test_client().get("/api/media/7")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_path_escaping_the_media_root_is_404(web):
    """Defence in depth: a stored path that resolves outside the root is refused
    even though save_image_bytes should never produce one.

    Uses an *absolute* escape path rather than a "../../" relative one: pathlib's
    `root / "/etc/passwd"` discards `root` entirely per the `/` operator's rule for
    absolute right-hand operands, landing on the real, existing /etc/passwd. That
    makes this test load-bearing — without the resolve()-against-root guard the
    route would happily serve a real file outside media_root(), so this is a
    mutation the test can actually catch. (A relative "../../etc/passwd" payload
    resolves to a nonexistent path under pytest's tmp_path sandbox, so the
    is_file() check alone would already 404 it regardless of whether the guard
    exists — that shape was tried first and rejected as untestable.)
    """
    app, mdb, _ = web
    mdb.get_by_id.return_value = SimpleNamespace(
        id=7, hunt_id=10, path="/etc/passwd", filename="passwd",
        content_type="image/png", size=1, origin="upload", created_at=None)
    resp = await app.test_client().get("/api/media/7")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_hunt_not_visible_is_forbidden(web):
    app, _, _ = web
    with patch("bot.web.api._load_visible_hunt", AsyncMock(return_value=(None, 403))):
        resp = await app.test_client().get("/api/media/7")
    assert resp.status_code in (403, 404)


@pytest.mark.asyncio
async def test_unauthenticated_request_is_401(web):
    """The @login_required decorator, not just hunt visibility, must gate this
    route: with no session at all the request must never reach the DB/media
    lookup. Every other test in this file grants a session in the fixture, so
    none of them would notice @login_required being dropped from the route --
    this is the one that would."""
    app, _, _ = web
    with patch("bot.web.auth.session", {}):
        resp = await app.test_client().get("/api/media/7")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sets_nosniff(web):
    """Sniffing validates only a prefix, so a stored file can carry an appended
    payload. nosniff stops a browser MIME-sniffing that body into an executable
    context if the URL is ever navigated to directly."""
    app, _, _ = web
    resp = await app.test_client().get("/api/media/7")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_serves_the_stored_content_type_verbatim(web):
    """Never re-derived from the file extension or from anything the client
    sent — the sniffed type recorded at upload is the only source of truth."""
    app, mdb, _ = web
    mdb.get_by_id.return_value = SimpleNamespace(
        id=7, hunt_id=10, path="uploads/10/a_crop.png", filename="crop.png",
        content_type="image/gif", size=13, origin="upload", created_at=None)
    resp = await app.test_client().get("/api/media/7")
    assert resp.headers["Content-Type"].startswith("image/gif")


class TestMediaUpload:
    """POST /api/hunts/<uid>/media — content sniffing, size cap, and the
    archived-hunt read-only guard, on top of the auth cover pinned above for
    the sibling GET route (login_required, hunt visibility)."""

    @pytest.mark.asyncio
    async def test_uploads_a_png(self, web):
        app, _, _ = web
        with patch("bot.store.MediaJsonDb") as mdb:
            mdb.create.return_value = SimpleNamespace(id=7, hunt_id=10)
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 201
        body = await resp.get_json()
        assert body["id"] == 7
        assert body["url"] == "/api/media/7"

    @pytest.mark.asyncio
    async def test_rejects_an_svg(self, web):
        """The multipart part DECLARES "image/svg+xml" -- if the route ever
        trusted that instead of sniffing the bytes, this would sail through.
        It doesn't: save_image_bytes sniffs the body and SVG has no allowed
        magic-byte signature, so it is refused regardless of what the client
        claims."""
        app, _, _ = web
        resp = await app.test_client().post(
            "/api/hunts/U1/media",
            files={"file": _file("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'>",
                                  "image/svg+xml")},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_an_archived_hunt(self, web):
        app, _, _ = web
        with patch("bot.web.api._load_visible_hunt",
                   AsyncMock(return_value=(_hunt(archived=True), {}))):
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\np", "image/png")},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_file_is_400(self, web):
        app, _, _ = web
        resp = await app.test_client().post("/api/hunts/U1/media")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_unauthenticated_request_is_401(self, web):
        """The plan's own Task 14 sketch had no test for this at all. Every
        other test in this class is handed a valid session by the fixture, so
        none of them would notice @login_required being dropped from the
        route -- this is the one that would. (This is exactly the gap the
        sibling GET route shipped with in Task 13, before a test was added.)
        """
        app, _, _ = web
        with patch("bot.web.auth.session", {}):
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\np", "image/png")},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_rejects_a_body_over_the_configured_limit(self, web):
        """Pins the size cap AT THIS LAYER, not only in
        test_media_store.py::test_size_limit_boundary. Without this, deleting
        the cap check inside save_image_bytes (or never wiring
        max_upload_bytes() into the route at all) would leave every test in
        this file green.
        """
        app, _, _ = web
        raw = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 100)
        with patch("bot.core.media_store.max_upload_bytes", return_value=10):
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": _file("big.png", raw, "image/png")},
            )
        assert resp.status_code == 400
