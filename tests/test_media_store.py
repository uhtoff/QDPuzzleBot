"""Plan A: media storage — dataclass, validation, and on-disk save."""
import datetime
from unittest.mock import patch

import pytest


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

    def test_riff_shorter_than_twelve_bytes(self):
        """The WEBP check slices rather than indexes, so a truncated RIFF
        header must return None rather than raising."""
        from bot.core.media_store import sniff_image_type
        assert sniff_image_type(b"RIFF") is None
        assert sniff_image_type(b"RIFF\x00\x00\x00\x00") is None


class TestUploadRelpath:
    def test_shape_and_uniqueness(self):
        from bot.core.media_store import upload_relpath
        a = upload_relpath(10, "crop.png")
        b = upload_relpath(10, "crop.png")
        assert a.startswith("uploads/10/")
        assert a.endswith("_crop.png")
        assert a != b            # uuid prefix prevents collisions

    def test_hostile_filename_cannot_add_path_segments(self):
        from bot.core.media_store import upload_relpath
        p = upload_relpath(10, "../../etc/passwd")
        assert p.count("/") == 2          # only uploads/ and the hunt id
        assert ".." not in p


class TestSaveImageBytes:
    """save_image_bytes validates, then writes under the media root."""

    def test_rejects_bytes_that_are_not_an_allowed_image(self, tmp_path):
        from bot.core.media_store import save_image_bytes, MediaRejected
        with patch("bot.core.media_store.media_root", return_value=tmp_path):
            with pytest.raises(MediaRejected) as exc:
                save_image_bytes(hunt_id=10, filename="x.svg",
                                 raw=b"<svg xmlns='http://www.w3.org/2000/svg'>")
        assert "image" in str(exc.value).lower()

    def test_rejects_oversize(self, tmp_path):
        from bot.core.media_store import save_image_bytes, MediaRejected
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000
        with patch("bot.core.media_store.media_root", return_value=tmp_path), \
             patch("bot.core.media_store.max_upload_bytes", return_value=10):
            with pytest.raises(MediaRejected) as exc:
                save_image_bytes(hunt_id=10, filename="big.png", raw=raw)
        assert "large" in str(exc.value).lower()

    def test_writes_file_and_returns_metadata(self, tmp_path):
        from bot.core.media_store import save_image_bytes
        raw = b"\x89PNG\r\n\x1a\n" + b"payload"
        with patch("bot.core.media_store.media_root", return_value=tmp_path), \
             patch("bot.core.media_store.max_upload_bytes", return_value=1_000_000):
            saved = save_image_bytes(hunt_id=10, filename="crop.png", raw=raw)
        assert saved["content_type"] == "image/png"
        assert saved["size"] == len(raw)
        assert saved["filename"] == "crop.png"
        assert saved["path"].startswith("uploads/10/")
        assert (tmp_path / saved["path"]).read_bytes() == raw

    def test_filename_cannot_escape_the_media_root(self, tmp_path):
        """A traversal filename must not write outside the root."""
        from bot.core.media_store import save_image_bytes
        raw = b"\x89PNG\r\n\x1a\n"
        with patch("bot.core.media_store.media_root", return_value=tmp_path), \
             patch("bot.core.media_store.max_upload_bytes", return_value=1_000_000):
            saved = save_image_bytes(hunt_id=10, filename="../../etc/passwd", raw=raw)
        written = (tmp_path / saved["path"]).resolve()
        assert tmp_path.resolve() in written.parents

    def test_content_type_is_sniffed_not_taken_from_the_filename(self, tmp_path):
        """JPEG bytes behind a `.png` filename are still stored as image/jpeg."""
        from bot.core.media_store import save_image_bytes
        raw = b"\xff\xd8\xff\xe0" + b"\x00" * 8
        with patch("bot.core.media_store.media_root", return_value=tmp_path), \
             patch("bot.core.media_store.max_upload_bytes", return_value=1_000_000):
            saved = save_image_bytes(hunt_id=10, filename="a.png", raw=raw)
        assert saved["content_type"] == "image/jpeg"
