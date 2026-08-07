"""validate_image_bytes + ingest_image_bytes (Phase 1 red: neither exists yet)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bot.core.media_store import MediaRejected

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class TestValidateImageBytes:
    def test_oversize_is_rejected(self):
        from bot.core.media_store import validate_image_bytes
        with patch("bot.core.media_store.max_upload_bytes", return_value=10):
            with pytest.raises(MediaRejected):
                validate_image_bytes(b"x" * 11)

    def test_unknown_bytes_are_rejected(self):
        from bot.core.media_store import validate_image_bytes
        with pytest.raises(MediaRejected):
            validate_image_bytes(b"definitely not an image")

    def test_type_outside_allowlist_is_rejected(self):
        # sniff says PNG, but the allowlist no longer contains it — the check
        # ocr-preview currently skips. Patch works because validate reads the
        # module global at call time.
        from bot.core.media_store import validate_image_bytes
        with patch("bot.core.media_store.ALLOWED_IMAGE_TYPES", ("image/gif",)):
            with pytest.raises(MediaRejected):
                validate_image_bytes(PNG)

    def test_valid_png_returns_its_type(self):
        from bot.core.media_store import validate_image_bytes
        assert validate_image_bytes(PNG) == "image/png"


class TestIngestImageBytes:
    def _db(self):
        db = MagicMock()
        db.create.side_effect = lambda **kw: SimpleNamespace(id=1, **kw)
        return db

    def _patches(self, tmp_path, dims, tiled):
        return [
            patch("bot.core.media_store.media_root", return_value=tmp_path),
            patch("bot.core.media_ingest.media_root", return_value=tmp_path),
            patch("bot.core.media_ingest.probe_dimensions", return_value=dims),
            patch("bot.core.media_ingest.needs_tiling", return_value=tiled),
        ]

    def test_small_image_gets_dimensions_and_no_pyramid(self, tmp_path):
        from bot.core.media_ingest import ingest_image_bytes
        db = self._db()
        ps = self._patches(tmp_path, dims=(120, 80), tiled=False)
        try:
            for p in ps: p.start()
            ingest_image_bytes(hunt_id=10, filename="a.png", raw=PNG,
                               origin="discord", db=db)
        finally:
            for p in ps: p.stop()
        kw = db.create.call_args.kwargs
        assert (kw["width"], kw["height"]) == (120, 80)   # distinct: catches transposition
        assert kw["tile_status"] == "none"
        assert kw["origin"] == "discord"

    def test_large_image_is_marked_pending(self, tmp_path):
        from bot.core.media_ingest import ingest_image_bytes
        db = self._db()
        ps = self._patches(tmp_path, dims=(14400, 9000), tiled=True)
        try:
            for p in ps: p.start()
            ingest_image_bytes(hunt_id=10, filename="a.png", raw=PNG,
                               origin="upload", db=db)
        finally:
            for p in ps: p.stop()
        kw = db.create.call_args.kwargs
        assert (kw["width"], kw["height"]) == (14400, 9000)
        assert kw["tile_status"] == "pending"

    def test_unprobeable_image_keeps_zero_dims_untiled(self, tmp_path):
        # A valid image of an allowed type that libvips still cannot open is
        # kept rather than lost; it simply gets no dimensions and no pyramid.
        from bot.core.media_ingest import ingest_image_bytes
        db = self._db()
        ps = self._patches(tmp_path, dims=(120, 80), tiled=False)
        ps[2] = patch("bot.core.media_ingest.probe_dimensions",
                      side_effect=RuntimeError("boom"))
        try:
            for p in ps: p.start()
            ingest_image_bytes(hunt_id=10, filename="a.png", raw=PNG,
                               origin="discord", db=db)
        finally:
            for p in ps: p.stop()
        db.create.assert_called_once()
        kw = db.create.call_args.kwargs
        assert (kw["width"], kw["height"]) == (0, 0)
        assert kw["tile_status"] == "none"

    def test_pixel_bomb_is_rejected_and_leaves_nothing_behind(self, tmp_path):
        """A small file can decode to an enormous raster (a flat 40000x40000 PNG
        compresses to ~1 MB), so the byte-size cap does not bound the memory a
        pyramid build will use. Reject on area, and leave no file or row."""
        from bot.core.media_ingest import ingest_image_bytes
        db = self._db()
        ps = self._patches(tmp_path, dims=(40000, 40000), tiled=True)
        try:
            for p in ps: p.start()
            with pytest.raises(MediaRejected):
                ingest_image_bytes(hunt_id=10, filename="bomb.png", raw=PNG,
                                   origin="upload", db=db)
        finally:
            for p in ps: p.stop()
        db.create.assert_not_called()
        assert list(tmp_path.rglob("*.png")) == []   # the saved file is cleaned up

    def test_large_but_legitimate_sheet_is_still_accepted(self, tmp_path):
        """The cap must sit above the real working case: the tiler is built for
        sheets around 207 MP, so one of those must still ingest."""
        from bot.core.media_ingest import ingest_image_bytes
        db = self._db()
        ps = self._patches(tmp_path, dims=(14400, 14400), tiled=True)
        try:
            for p in ps: p.start()
            ingest_image_bytes(hunt_id=10, filename="sheet.png", raw=PNG,
                               origin="upload", db=db)
        finally:
            for p in ps: p.stop()
        db.create.assert_called_once()

    def test_rejection_propagates_and_creates_no_row(self, tmp_path):
        from bot.core.media_ingest import ingest_image_bytes
        db = self._db()
        ps = self._patches(tmp_path, dims=(120, 80), tiled=False)
        try:
            for p in ps: p.start()
            with pytest.raises(MediaRejected):
                ingest_image_bytes(hunt_id=10, filename="a.png", raw=b"junk",
                                   origin="discord", db=db)
        finally:
            for p in ps: p.stop()
        db.create.assert_not_called()


class TestTrustedBytesSkipTheUploadCap:
    """The byte cap bounds what a *user* can push at us. A canvas region crop is
    bytes this server generated: a large region re-encodes to lossless PNG well
    past the cap, and rejecting it silently produced a region with no image."""

    def test_oversize_trusted_bytes_are_accepted(self):
        from bot.core.media_store import validate_image_bytes
        with patch("bot.core.media_store.max_upload_bytes", return_value=10):
            assert validate_image_bytes(PNG, enforce_size_limit=False) == "image/png"

    def test_trusted_bytes_still_must_be_an_allowed_image(self):
        """Skipping the size cap must not skip the type allowlist."""
        from bot.core.media_store import validate_image_bytes
        with pytest.raises(MediaRejected):
            validate_image_bytes(b"not an image at all", enforce_size_limit=False)

    def test_untrusted_bytes_still_hit_the_cap(self):
        from bot.core.media_store import validate_image_bytes
        with patch("bot.core.media_store.max_upload_bytes", return_value=10):
            with pytest.raises(MediaRejected):
                validate_image_bytes(PNG)

    def test_trusted_ingest_still_enforces_the_pixel_ceiling(self, tmp_path):
        """Trust covers size, not decodability — a crop must not smuggle a
        pixel bomb past the ceiling."""
        from bot.core.media_ingest import ingest_image_bytes
        db = MagicMock()
        ps = [
            patch("bot.core.media_store.media_root", return_value=tmp_path),
            patch("bot.core.media_ingest.media_root", return_value=tmp_path),
            patch("bot.core.media_ingest.probe_dimensions", return_value=(40000, 40000)),
            patch("bot.core.media_ingest.needs_tiling", return_value=True),
        ]
        try:
            for p in ps: p.start()
            with pytest.raises(MediaRejected):
                ingest_image_bytes(hunt_id=10, filename="c.png", raw=PNG,
                                   origin="canvas_crop", db=db,
                                   enforce_size_limit=False)
        finally:
            for p in ps: p.stop()
        db.create.assert_not_called()
