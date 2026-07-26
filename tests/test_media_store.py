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
