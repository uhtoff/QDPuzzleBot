"""MediaData / MySQLMediaDb tile-state carrying, and the tiling orchestrator."""
import threading
from unittest.mock import MagicMock, patch

from bot.core import media_tiling
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


def _media(media_id=42, hunt_id=3, path="uploads/3/abc_src.png"):
    m = MediaData()
    m.id, m.hunt_id, m.path = media_id, hunt_id, path
    m.tile_status = "pending"
    return m


async def test_tile_media_marks_the_row_ready_with_its_level_count(tmp_path):
    db = MagicMock()
    with patch.object(media_tiling, "media_root", return_value=tmp_path), \
         patch.object(media_tiling, "build_pyramid",
                      return_value=(14400, 14400, 6)) as build:
        await media_tiling.tile_media(_media(), db)
    build.assert_called_once()
    src, dest = build.call_args[0]
    assert src == tmp_path / "uploads/3/abc_src.png"
    # Keyed by media id, and under tiles/ rather than uploads/.
    assert dest == tmp_path / "tiles/3/42"
    db.update_tiles.assert_called_once_with(42, "ready", 6)


async def test_tile_media_marks_the_row_failed_and_does_not_raise(tmp_path):
    """A bad upload must not take the request or the bot down with it."""
    db = MagicMock()
    with patch.object(media_tiling, "media_root", return_value=tmp_path), \
         patch.object(media_tiling, "build_pyramid",
                      side_effect=RuntimeError("libvips said no")):
        await media_tiling.tile_media(_media(), db)
    db.update_tiles.assert_called_once_with(42, "failed", 0)


async def test_tiling_runs_at_most_the_cap_concurrently(tmp_path):
    """3 jobs, cap 2: the third pyramid must not start while two hold slots.

    Each pyramid build peaks around 360 MB, so an unbounded fan-out (a batch
    upload, or a retile of several canvases) is what takes the box down.
    """
    import asyncio
    started, release = [], threading.Event()

    def slow_build(src, dest):
        started.append(1)
        release.wait(timeout=10)
        return (100, 100, 1)

    db = MagicMock()
    with patch.object(media_tiling, "media_root", return_value=tmp_path), \
         patch.object(media_tiling, "build_pyramid", side_effect=slow_build):
        tasks = [asyncio.create_task(media_tiling.tile_media(_media(media_id=i), db))
                 for i in (1, 2, 3)]
        await asyncio.sleep(0.3)      # single bounded wait for the executor to spin up
        try:
            assert media_tiling.MAX_CONCURRENT_TILING == 2
            assert len(started) == 2
        finally:
            release.set()
            await asyncio.gather(*tasks)
    assert len(started) == 3
    assert db.update_tiles.call_count == 3


async def test_tile_media_runs_the_tiler_off_the_event_loop(tmp_path):
    """Eight seconds of CPU on the loop would stall the gateway heartbeat, so
    the executor hand-off is asserted rather than trusted."""
    db = MagicMock()
    seen = {}

    def record(src, dest):
        seen["thread"] = threading.current_thread().name
        return (10, 10, 1)

    main = threading.current_thread().name
    with patch.object(media_tiling, "media_root", return_value=tmp_path), \
         patch.object(media_tiling, "build_pyramid", side_effect=record):
        await media_tiling.tile_media(_media(), db)
    assert seen["thread"] != main
