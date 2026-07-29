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


def test_max_content_length_admits_the_configured_cap():
    """MAX_CONTENT_LENGTH is Quart's own body guard and runs BEFORE any route.
    If it is below web_attachment_max_bytes, the configured cap is unreachable
    and the caller gets a bare 413 instead of our JSON error."""
    from bot.web import _max_body_bytes
    assert _max_body_bytes(26214400) > 26214400      # headroom for multipart overhead
    assert _max_body_bytes(1024) >= 1024
    # Pin the exact headroom (1 MiB), not just "some": a mutation that adds a
    # trivial few bytes of slack would still satisfy the two asserts above,
    # but would not actually cover multipart boundary/header overhead.
    assert _max_body_bytes(26214400) == 26214400 + 1024 * 1024


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
            mdb.create.return_value = SimpleNamespace(id=7, hunt_id=10,
                                                      tile_status="none")
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 201
        body = await resp.get_json()
        assert body["id"] == 7
        assert body["url"] == "/api/media/7"

    @pytest.mark.asyncio
    async def test_a_screenshot_is_stored_untiled(self, web):
        """Below the direct-decode ceiling a pyramid would be hundreds of files
        serving no purpose, so no tiling task is scheduled at all."""
        app, _, _ = web
        with patch("bot.store.MediaJsonDb") as mdb, \
             patch("bot.core.image_tiler.probe_dimensions",
                   return_value=(1920, 1080)), \
             patch("bot.core.media_tiling.tile_media") as tile:
            mdb.create.return_value = SimpleNamespace(id=7, hunt_id=10,
                                                      tile_status="none")
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 201
        body = await resp.get_json()
        assert (body["width"], body["height"]) == (1920, 1080)
        assert body["tile_status"] == "none"
        assert mdb.create.call_args.kwargs["tile_status"] == "none"
        tile.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_huge_sheet_is_recorded_pending_and_tiled(self, web):
        """The row goes in as pending and the response returns immediately —
        tiling the motivating image takes about eight seconds, which must not
        happen inside the request."""
        app, _, _ = web
        with patch("bot.store.MediaJsonDb") as mdb, \
             patch("bot.core.image_tiler.probe_dimensions",
                   return_value=(14400, 14400)), \
             patch("bot.core.media_tiling.tile_media") as tile:
            mdb.create.return_value = SimpleNamespace(id=7, hunt_id=10,
                                                      tile_status="pending")
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": _file("big.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 201
        body = await resp.get_json()
        assert (body["width"], body["height"]) == (14400, 14400)
        assert body["tile_status"] == "pending"
        assert mdb.create.call_args.kwargs["tile_status"] == "pending"
        tile.assert_called_once()

    @pytest.mark.asyncio
    async def test_an_unprobeable_image_is_still_stored(self, web):
        """save_image_bytes already sniffed the magic bytes, so a probe failure
        means a valid allowed type libvips cannot open. Losing the upload over
        that would be worse than storing it without a pyramid."""
        app, _, _ = web
        with patch("bot.store.MediaJsonDb") as mdb, \
             patch("bot.core.image_tiler.probe_dimensions",
                   side_effect=RuntimeError("vips said no")):
            mdb.create.return_value = SimpleNamespace(id=7, hunt_id=10,
                                                      tile_status="none")
            resp = await app.test_client().post(
                "/api/hunts/U1/media",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 201
        body = await resp.get_json()
        assert (body["width"], body["height"]) == (0, 0)

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


class TestMediaTiles:
    """B1: GET /api/media/<id>/tiles/<z>/<y>/<x> — authorised exactly as the
    sibling GET is, plus the blank-tile fallback that dzsave's omission of
    uniform tiles makes necessary."""

    @staticmethod
    def _pyramid(tmp_path, mdb):
        """Build a real pyramid and point the media row at it.

        A real one, not a hand-made directory: the route and the tiler have to
        agree on the z/y/x layout, and a fake tree would let them drift apart
        while both sets of tests stayed green.
        """
        import pyvips
        from bot.core import image_tiler

        src = tmp_path / "big.png"
        im = pyvips.Image.xyz(1024, 1024)
        ((im[0] + im[1]) % 255).cast("uchar").write_to_file(str(src))
        dest = tmp_path / image_tiler.tiles_relpath(10, 7)
        _, _, levels = image_tiler.build_pyramid(src, dest)
        mdb.get_by_id.return_value = SimpleNamespace(
            id=7, hunt_id=10, path="uploads/10/a_crop.png", filename="crop.png",
            content_type="image/png", size=13, origin="upload", created_at=None,
            width=1024, height=1024, tile_status="ready", tile_levels=levels)
        return levels

    @pytest.mark.asyncio
    async def test_serves_a_tile(self, web, tmp_path):
        app, mdb, _ = web
        self._pyramid(tmp_path, mdb)
        resp = await app.test_client().get("/api/media/7/tiles/0/0/0")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == "image/webp"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert "max-age" in resp.headers["Cache-Control"]

    @pytest.mark.asyncio
    async def test_serves_a_native_level_tile_by_row_then_column(self, web, tmp_path):
        """Asserts the route reads z/y/x in libvips' google order. A 1024px
        image has a 2x2 native level, so this only distinguishes order if the
        pyramid is asymmetric -- which it is not at 2x2, so the value of this
        test is that it proves the deepest level is addressable at all."""
        app, mdb, _ = web
        levels = self._pyramid(tmp_path, mdb)
        resp = await app.test_client().get(f"/api/media/7/tiles/{levels - 1}/1/1")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == "image/webp"

    @pytest.mark.asyncio
    async def test_missing_tile_serves_a_white_tile(self, web, tmp_path):
        """dzsave omits any tile of a uniform colour, and a mostly-white puzzle
        sheet has many. 404ing would pepper the viewer with broken images over
        blank paper.

        Decodes the actual pixel rather than stopping at status/content-type:
        dzsave's OWN blank.png is also a 200 image/png, but its pixels are a
        degenerate near-black placeholder — checking only the envelope would
        not have caught that regression."""
        import pyvips
        app, mdb, _ = web
        levels = self._pyramid(tmp_path, mdb)
        resp = await app.test_client().get(f"/api/media/7/tiles/{levels - 1}/99/99")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == "image/png"
        img = pyvips.Image.new_from_buffer(await resp.get_data(), "")
        assert img(0, 0)[0] > 200

    @pytest.mark.asyncio
    async def test_a_level_past_the_pyramid_is_404(self, web, tmp_path):
        app, mdb, _ = web
        levels = self._pyramid(tmp_path, mdb)
        resp = await app.test_client().get(f"/api/media/7/tiles/{levels}/0/0")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_an_untiled_row_has_no_tiles(self, web, tmp_path):
        """tile_status gates the route: a screenshot has no pyramid, and serving
        a stale one from a reused media id would be worse than a 404."""
        app, mdb, _ = web
        self._pyramid(tmp_path, mdb)
        mdb.get_by_id.return_value.tile_status = "none"
        resp = await app.test_client().get("/api/media/7/tiles/0/0/0")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unauthenticated_request_is_401(self, web, tmp_path):
        """Every other test in this class is handed a session by the fixture, so
        this is the only one that would notice @login_required being dropped."""
        app, mdb, _ = web
        self._pyramid(tmp_path, mdb)
        with patch("bot.web.auth.session", {}):
            resp = await app.test_client().get("/api/media/7/tiles/0/0/0")
        assert resp.status_code == 401


class TestEvidenceOcrPreview:
    """POST /api/hunts/<uid>/evidence/ocr-preview -- extracts text from an
    about-to-be-logged clue image, without saving it anywhere. Reuses
    upload_media's own sniffing/size validation, on the same permission bar
    (any member who can see an unarchived hunt)."""

    @pytest.mark.asyncio
    async def test_extracts_text_from_the_upload(self, web):
        app, _, _ = web
        with patch("bot.core.ocr.extract_text", return_value="HYDROGEN") as extract:
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 200
        assert (await resp.get_json())["text"] == "HYDROGEN"
        extract.assert_called_once_with(b"\x89PNG\r\n\x1a\npayload")

    @pytest.mark.asyncio
    async def test_rejects_an_svg(self, web):
        """Same sniffing as the real upload route -- the declared content type
        is not trusted, only the magic bytes are."""
        app, _, _ = web
        resp = await app.test_client().post(
            "/api/hunts/U1/evidence/ocr-preview",
            files={"file": _file("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'>",
                                  "image/svg+xml")},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_a_body_over_the_configured_limit(self, web):
        """Pins the size cap AT THIS LAYER, same as
        TestMediaUpload::test_rejects_a_body_over_the_configured_limit --
        without this, deleting the cap check in this route (or never wiring
        max_upload_bytes() into it) would leave every other test in this
        class green.
        """
        app, _, _ = web
        raw = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 100)
        with patch("bot.core.media_store.max_upload_bytes", return_value=10):
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("big.png", raw, "image/png")},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_type_outside_allowlist_is_refused(self, web):
        """The route must apply the ALLOWLIST, not just "did it sniff as
        something". Today it checks only `sniff_image_type(...) is None`, so a
        type dropped from ALLOWED_IMAGE_TYPES still reaches the OCR engine —
        the drift that comes of re-implementing upload_media's validation."""
        app, _, _ = web
        with patch("bot.core.media_store.ALLOWED_IMAGE_TYPES", ("image/gif",)), \
             patch("bot.core.ocr.extract_text", return_value="HYDROGEN"):
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 400
        body = await resp.get_json()
        assert "Not a supported image" in body["error"]
        assert "HYDROGEN" not in str(body)

    @pytest.mark.asyncio
    async def test_extraction_failure_is_a_500_not_a_crash(self, web):
        app, _, _ = web
        with patch("bot.core.ocr.extract_text", side_effect=RuntimeError("boom")):
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 500
        assert (await resp.get_json())["error"] == "ocr_failed"

    @pytest.mark.asyncio
    async def test_nothing_is_persisted(self, web):
        app, mdb, _ = web
        with patch("bot.core.ocr.extract_text", return_value="text"):
            await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        mdb.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_an_archived_hunt(self, web):
        app, _, _ = web
        with patch("bot.web.api._load_visible_hunt",
                   AsyncMock(return_value=(_hunt(archived=True), {}))):
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\np", "image/png")},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_file_is_400(self, web):
        app, _, _ = web
        resp = await app.test_client().post("/api/hunts/U1/evidence/ocr-preview")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_unauthenticated_request_is_401(self, web):
        app, _, _ = web
        with patch("bot.web.auth.session", {}):
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\np", "image/png")},
            )
        assert resp.status_code == 401
