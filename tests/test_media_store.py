"""Plan A: media storage — dataclass, validation, and on-disk save."""
import datetime


class TestMediaData:
    def test_default_fields(self):
        from bot.store.media_data import MediaData
        m = MediaData()
        assert m.id == 0
        assert m.hunt_id == 0
        assert m.path == ""
        assert m.filename == ""
        assert m.content_type == ""
        assert m.size == 0
        assert m.origin == "upload"
        assert m.created_at is None

    def test_import_dict_reads_all_fields(self):
        from bot.store.media_data import MediaData
        row = {
            "id": 3,
            "hunt_id": 10,
            "path": "uploads/10/abc_crop.png",
            "filename": "crop.png",
            "content_type": "image/png",
            "size": 2048,
            "origin": "upload",
            "created_at": datetime.datetime(2026, 7, 26, 9, 0, 0),
        }
        m = MediaData.import_dict(row)
        assert m.id == 3
        assert m.hunt_id == 10
        assert m.path == "uploads/10/abc_crop.png"
        assert m.filename == "crop.png"
        assert m.content_type == "image/png"
        assert m.size == 2048
        assert m.origin == "upload"

    def test_import_dict_reattaches_utc_tzinfo(self):
        """created_at is stored naive UTC like every other timestamp column;
        import_dict must reattach tzinfo or comparisons and serialisation drift
        from the rest of the app (same pattern as EvidenceData)."""
        from bot.store.media_data import MediaData
        m = MediaData.import_dict({
            "id": 1, "hunt_id": 10, "path": "p", "filename": "f",
            "content_type": "image/png", "size": 1, "origin": "upload",
            "created_at": datetime.datetime(2026, 7, 26, 9, 0, 0),
        })
        assert m.created_at.tzinfo == datetime.timezone.utc
        assert m.created_at.isoformat() == "2026-07-26T09:00:00+00:00"

    def test_import_dict_handles_null_created_at(self):
        from bot.store.media_data import MediaData
        m = MediaData.import_dict({
            "id": 1, "hunt_id": 10, "path": "p", "filename": "f",
            "content_type": "image/png", "size": 1, "origin": "upload",
            "created_at": None,
        })
        assert m.created_at is None


class TestSniffImageType:
    """The declared Content-Type is not trusted; magic bytes decide."""

    def test_png(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) == "image/png"

    def test_jpeg(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "image/jpeg"

    def test_gif87a_and_gif89a(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"GIF87a" + b"\x00" * 20) == "image/gif"
        assert sniff_image_type(b"GIF89a" + b"\x00" * 20) == "image/gif"

    def test_webp(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"

    def test_riff_that_is_not_webp_is_rejected(self):
        """A RIFF container holding WAVE audio is not an image."""
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None

    def test_svg_is_rejected(self):
        """SVG is a scriptable document. Serving one from our own origin is
        stored XSS, so it is not an allowed image type however it is declared."""
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"<svg xmlns='http://www.w3.org/2000/svg'>") is None

    def test_png_bytes_declared_as_something_else_still_sniff_as_png(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"\x89PNG\r\n\x1a\n") == "image/png"

    def test_empty_and_short_input(self):
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"") is None
        assert sniff_image_type(b"\x89PN") is None
