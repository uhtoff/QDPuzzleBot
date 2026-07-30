"""Media sweeper: orphan detection (live MySQL) and the sweep itself (mocked).

`get_orphaned` is a four-way LEFT JOIN plus an age floor — exactly the kind of
thing a mocked DAO cannot check, so those tests follow the live-DB convention
used by test_tag_edges.py (`from bot.store import mydb`) and insert/tear down
their own rows. `sweep` touches only the filesystem and the DAO, so it is tested
against mocks with a tmp_path media root.
"""
import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import bot.core.media_gc as media_gc
from bot.store import MediaJsonDb, mydb
from tests.conftest import live_hunt_id


# --------------------------------------------------------------------------
# Live-DB helpers
# --------------------------------------------------------------------------

OLD = datetime.datetime(2020, 1, 1, 0, 0, 0)


def _insert_media(created_at=OLD, hunt_id=None, size=100):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO media (hunt_id, path, filename, content_type, size, origin,"
        " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (hunt_id or live_hunt_id(), "uploads/gc/x.png", "x.png", "image/png",
         size, "upload", created_at),
    )
    rid = cur.lastrowid
    cur.close()
    mydb.commit()
    return rid


def _delete_media(media_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM media WHERE id = %s", (media_id,))
    cur.close()
    mydb.commit()


def _insert_puzzle(name="GCTest"):
    cur = mydb.cursor()
    cur.execute("INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
                (live_hunt_id(), name))
    pid = cur.lastrowid
    cur.close()
    mydb.commit()
    return pid


def _delete_puzzle(puzzle_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()
    mydb.commit()


def _insert_evidence(media_id, puzzle_id):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO evidence_items (hunt_id, source_puzzle_id, note, created_at,"
        " media_id) VALUES (%s, %s, %s, %s, %s)",
        (live_hunt_id(), puzzle_id, "note", OLD, media_id),
    )
    cur.close()
    mydb.commit()


def _insert_canvas(media_id):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO hunt_canvases (hunt_id, media_id, name, created_at)"
        " VALUES (%s, %s, %s, %s)",
        (live_hunt_id(), media_id, "canvas", OLD),
    )
    cid = cur.lastrowid
    cur.close()
    mydb.commit()
    return cid


def _insert_puzzle_image(media_id, puzzle_id):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzle_images (puzzle_id, media_id, position, created_at)"
        " VALUES (%s, %s, %s, %s)",
        (puzzle_id, media_id, 0, OLD),
    )
    cur.close()
    mydb.commit()


def _insert_region(canvas_id, crop_media_id=None):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO canvas_regions (canvas_id, shape, created_at, crop_media_id)"
        " VALUES (%s, %s, %s, %s)",
        (canvas_id, '{"type":"rect","points":[[0,0],[1,1]]}', OLD, crop_media_id),
    )
    cur.close()
    mydb.commit()


def _orphan_ids(hunt_id=None):
    return {m.id for m in media_gc.orphaned_media(hunt_id or live_hunt_id())}


# --------------------------------------------------------------------------
# Orphan detection (live DB)
# --------------------------------------------------------------------------

def test_old_unreferenced_media_is_orphaned():
    media_id = _insert_media()
    try:
        assert media_id in _orphan_ids()
    finally:
        _delete_media(media_id)


def test_media_within_the_grace_period_is_never_orphaned():
    """media_ingest inserts the row BEFORE the caller attaches it, so a young
    unreferenced row is indistinguishable from an upload still in flight."""
    fresh = _insert_media(created_at=datetime.datetime.utcnow())
    old = _insert_media()
    try:
        ids = _orphan_ids()
        assert old in ids                 # the pair differ only in age...
        assert fresh not in ids           # ...so the floor is what excludes it
    finally:
        _delete_media(fresh)
        _delete_media(old)


def test_evidence_reference_protects_media():
    media_id = _insert_media()
    puzzle_id = _insert_puzzle()
    try:
        _insert_evidence(media_id, puzzle_id)
        assert media_id not in _orphan_ids()
    finally:
        _delete_puzzle(puzzle_id)         # cascades the evidence row
        _delete_media(media_id)


def test_canvas_reference_protects_media():
    media_id = _insert_media()
    try:
        _insert_canvas(media_id)
        assert media_id not in _orphan_ids()
    finally:
        _delete_media(media_id)           # cascades the canvas


def test_puzzle_image_reference_protects_media():
    media_id = _insert_media()
    puzzle_id = _insert_puzzle()
    try:
        _insert_puzzle_image(media_id, puzzle_id)
        assert media_id not in _orphan_ids()
    finally:
        _delete_puzzle(puzzle_id)
        _delete_media(media_id)


def test_region_crop_reference_protects_media():
    canvas_media_id = _insert_media()
    crop_media_id = _insert_media()
    try:
        canvas_id = _insert_canvas(canvas_media_id)
        _insert_region(canvas_id, crop_media_id=crop_media_id)
        assert crop_media_id not in _orphan_ids()
    finally:
        _delete_media(canvas_media_id)    # cascades canvas -> region
        _delete_media(crop_media_id)


def test_orphans_are_scoped_to_the_hunt():
    """Blast radius is one hunt, like the other maintenance commands: a sweep
    must never report (and so never delete) another hunt's media."""
    cur = mydb.cursor()
    cur.execute("INSERT INTO hunts (name) VALUES (%s)", ("gc-other-hunt",))
    other_hunt = cur.lastrowid
    cur.close()
    mydb.commit()
    mine = _insert_media()
    theirs = _insert_media(hunt_id=other_hunt)
    try:
        ids = _orphan_ids()
        assert mine in ids
        assert theirs not in ids
    finally:
        _delete_media(mine)
        cur = mydb.cursor()
        cur.execute("DELETE FROM hunts WHERE id = %s", (other_hunt,))
        cur.close()
        mydb.commit()


def test_dao_delete_removes_the_row():
    media_id = _insert_media()
    MediaJsonDb.delete(media_id)
    assert MediaJsonDb.get_by_id(media_id) is None


# --------------------------------------------------------------------------
# sweep (mocked DAO + tmp_path media root)
# --------------------------------------------------------------------------

def _media(media_id, path, tile_status="none", size=10, hunt_id=10):
    return SimpleNamespace(id=media_id, hunt_id=hunt_id, path=path, size=size,
                           tile_status=tile_status)


@pytest.fixture
def swept(tmp_path):
    """(db, root, run) — run(rows) sweeps `rows` against a tmp media root."""
    db = MagicMock()

    def run(rows):
        db.get_orphaned.return_value = rows
        with patch.object(media_gc, "MediaJsonDb", db), \
             patch.object(media_gc, "media_root", return_value=tmp_path):
            return media_gc.sweep(10)

    return db, tmp_path, run


def test_sweep_deletes_row_file_and_tiles(swept):
    db, root, run = swept
    original = root / "uploads/10/a.png"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"bytes")
    tiles = root / "tiles/10/7"
    tiles.mkdir(parents=True)
    (tiles / "0_0.jpg").write_bytes(b"tile")

    stats = run([_media(7, "uploads/10/a.png", tile_status="ready", size=1024)])

    assert not original.exists()
    assert not tiles.exists()
    db.delete.assert_called_once_with(7)
    assert (stats.count, stats.bytes_freed) == (1, 1024)


def test_sweep_leaves_the_tile_root_of_other_media_alone(swept):
    """Two pyramids under one hunt: sweeping media 7 must not take out media 8's
    tiles. A single-row test would pass even if the code rmtree'd tiles/<hunt>."""
    db, root, run = swept
    mine = root / "tiles/10/7"
    mine.mkdir(parents=True)
    theirs = root / "tiles/10/8"
    theirs.mkdir(parents=True)

    run([_media(7, "uploads/10/a.png", tile_status="ready")])

    assert not mine.exists()
    assert theirs.exists()


def test_sweep_tolerates_missing_files(swept):
    """Already gone on disk (a half-finished delete, a manual cleanup) must not
    stop the row itself from being removed — otherwise it is re-reported forever."""
    db, root, run = swept
    stats = run([_media(7, "uploads/10/gone.png", tile_status="ready")])

    db.delete.assert_called_once_with(7)
    assert stats.count == 1


def test_sweep_counts_the_row_even_when_the_file_cannot_be_removed(swept):
    db, root, run = swept
    with patch.object(media_gc.shutil, "rmtree", side_effect=OSError("busy")):
        stats = run([_media(7, "uploads/10/a.png", tile_status="ready", size=5)])

    db.delete.assert_called_once_with(7)
    assert (stats.count, stats.bytes_freed) == (1, 5)


def test_sweep_of_nothing_reports_nothing(swept):
    db, root, run = swept
    stats = run([])

    db.delete.assert_not_called()
    assert (stats.count, stats.bytes_freed) == (0, 0)


def test_sweep_sums_every_row(swept):
    """Two rows, different sizes: a single-row test cannot tell a sum from a
    last-write-wins assignment."""
    db, root, run = swept
    stats = run([_media(7, "uploads/10/a.png", size=100),
                 _media(8, "uploads/10/b.png", size=25)])

    assert (stats.count, stats.bytes_freed) == (2, 125)
    assert [c.args[0] for c in db.delete.call_args_list] == [7, 8]


def test_sweep_skips_the_tile_dir_for_untiled_media(swept):
    """tile_status 'none' means no pyramid was ever built; rmtree'ing a path that
    was never created is pointless work on every swept upload."""
    db, root, run = swept
    with patch.object(media_gc.shutil, "rmtree") as rmtree:
        run([_media(7, "uploads/10/a.png", tile_status="none")])

    rmtree.assert_not_called()


def test_sweep_uses_the_grace_period_cutoff(swept):
    db, root, run = swept
    run([])

    hunt_id, cutoff = db.get_orphaned.call_args.args
    assert hunt_id == 10
    age = datetime.datetime.utcnow() - cutoff
    assert media_gc.GRACE_PERIOD <= age < media_gc.GRACE_PERIOD + datetime.timedelta(minutes=1)


@pytest.mark.parametrize("num_bytes,expected", [
    (0, "0 B"), (512, "512 B"), (2048, "2.0 KB"),
    (35861299, "34.2 MB"), (3 * 1024 ** 3, "3.0 GB"),
])
def test_format_size(num_bytes, expected):
    assert media_gc.format_size(num_bytes) == expected
