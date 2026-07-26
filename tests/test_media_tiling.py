"""MediaData / MySQLMediaDb tile-state carrying, and the tiling orchestrator."""
from bot.store.media_data import MediaData


def test_media_data_defaults_to_no_pyramid():
    m = MediaData()
    assert m.width == 0
    assert m.height == 0
    assert m.tile_status == "none"
    assert m.tile_levels == 0


def test_media_data_imports_tile_columns_from_a_row():
    m = MediaData.import_dict({
        "id": 7, "hunt_id": 3, "path": "uploads/3/x.png", "filename": "x.png",
        "content_type": "image/png", "size": 10, "origin": "upload",
        "width": 14400, "height": 14400, "tile_status": "ready", "tile_levels": 6,
    })
    assert (m.width, m.height) == (14400, 14400)
    assert m.tile_status == "ready"
    assert m.tile_levels == 6
