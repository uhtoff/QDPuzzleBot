"""The puzzle_images service: ordering, attach/detach, and the region sync.

The DAO is patched at the service's own import site per docs/agents/python.md —
puzzle_images.py does `from bot.store import ...` at module load, binding its
own name.
"""
from unittest.mock import MagicMock, patch

import pytest

from bot.core import puzzle_images
from bot.store.puzzle_image_data import PuzzleImageData


def _image(image_id=1, puzzle_id=5, media_id=10, source_region_id=None, position=0):
    p = PuzzleImageData()
    p.id, p.puzzle_id, p.media_id = image_id, puzzle_id, media_id
    p.source_region_id, p.position = source_region_id, position
    return p


@pytest.fixture
def db():
    images = MagicMock()
    with patch.object(puzzle_images, "PuzzleImageJsonDb", images):
        yield images


def test_list_images_returns_the_daos_order_untouched(db):
    ordered = [_image(1, position=0), _image(2, position=1), _image(3, position=2)]
    db.get_by_puzzle.return_value = ordered
    assert puzzle_images.list_images(5) is ordered
    db.get_by_puzzle.assert_called_once_with(5)


def test_attach_upload_appends_after_existing_images(db):
    db.get_by_puzzle.return_value = []
    db.create.return_value = _image(1, puzzle_id=5, media_id=10, position=0)
    puzzle_images.attach_upload(puzzle_id=5, media_id=10)
    first_kwargs = db.create.call_args.kwargs
    assert first_kwargs["puzzle_id"] == 5
    assert first_kwargs["media_id"] == 10
    assert first_kwargs["source_region_id"] is None
    assert first_kwargs["position"] == 0

    # Second attach: one existing image already in place.
    db.get_by_puzzle.return_value = [_image(1, puzzle_id=5, media_id=10, position=0)]
    db.create.return_value = _image(2, puzzle_id=5, media_id=11, position=1)
    puzzle_images.attach_upload(puzzle_id=5, media_id=11)
    second_kwargs = db.create.call_args.kwargs
    assert second_kwargs["position"] == 1
    assert second_kwargs["position"] > first_kwargs["position"]


def test_reorder_sets_positions_for_exactly_the_given_ids_in_order(db):
    db.get_by_puzzle.return_value = [
        _image(1, puzzle_id=5, position=0),
        _image(2, puzzle_id=5, position=1),
        _image(3, puzzle_id=5, position=2),
    ]
    puzzle_images.reorder(puzzle_id=5, ordered_ids=[3, 1, 2])
    db.set_positions.assert_called_once_with([3, 1, 2])


def test_reorder_rejects_an_id_belonging_to_another_puzzle(db):
    db.get_by_puzzle.return_value = [
        _image(1, puzzle_id=5, position=0),
        _image(2, puzzle_id=5, position=1),
    ]
    with pytest.raises(puzzle_images.ImageNotFound):
        puzzle_images.reorder(puzzle_id=5, ordered_ids=[1, 2, 99])
    db.set_positions.assert_not_called()


def test_detach_deletes_an_image_belonging_to_the_puzzle(db):
    db.get_by_id.return_value = _image(1, puzzle_id=5)
    puzzle_images.detach(image_id=1, puzzle_id=5)
    db.delete.assert_called_once_with(1)


def test_detach_refuses_an_image_from_another_puzzle(db):
    db.get_by_id.return_value = _image(1, puzzle_id=99)
    with pytest.raises(puzzle_images.ImageNotFound):
        puzzle_images.detach(image_id=1, puzzle_id=5)
    db.delete.assert_not_called()


def test_detach_refuses_a_missing_image(db):
    db.get_by_id.return_value = None
    with pytest.raises(puzzle_images.ImageNotFound):
        puzzle_images.detach(image_id=1, puzzle_id=5)
    db.delete.assert_not_called()


def test_sync_region_image_creates_a_row_for_a_new_locates_link(db):
    db.get_by_region.return_value = None
    puzzle_images.sync_region_image(
        region_id=7, puzzle_id=5, role="locates", media_id=10)
    db.create.assert_called_once_with(
        puzzle_id=5, media_id=10, source_region_id=7, position=0)
    db.delete_by_region.assert_not_called()


def test_sync_region_image_leaves_an_unchanged_locates_link_alone(db):
    """Idempotent: re-syncing an already-linked region must not touch its
    existing row, or it would lose its position in the strip."""
    db.get_by_region.return_value = _image(
        1, puzzle_id=5, media_id=10, source_region_id=7, position=3)
    puzzle_images.sync_region_image(
        region_id=7, puzzle_id=5, role="locates", media_id=10)
    db.create.assert_not_called()
    db.delete_by_region.assert_not_called()


def test_sync_region_image_deletes_the_link_for_a_non_locates_role(db):
    db.get_by_region.return_value = _image(1, puzzle_id=5, source_region_id=7)
    puzzle_images.sync_region_image(
        region_id=7, puzzle_id=5, role="informs", media_id=10)
    db.delete_by_region.assert_called_once_with(7)
    db.create.assert_not_called()


def test_sync_region_image_deletes_the_link_when_there_is_no_puzzle(db):
    puzzle_images.sync_region_image(
        region_id=7, puzzle_id=None, role="locates", media_id=10)
    db.delete_by_region.assert_called_once_with(7)
    db.create.assert_not_called()


def test_sync_region_image_never_raises_on_a_missing_crop(db):
    db.get_by_region.return_value = None
    # media_id is None: the crop hasn't been produced yet (or failed).
    puzzle_images.sync_region_image(
        region_id=7, puzzle_id=5, role="locates", media_id=None)
    db.create.assert_not_called()
    db.delete_by_region.assert_not_called()
