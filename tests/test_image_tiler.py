"""The pure tiler: a file on disk in, a pyramid and dimensions out.

No DAOs, no Quart, no mocks — pyvips generates the fixtures too, so these tests
exercise the real encoder rather than a stand-in for it.
"""
import pytest

from bot.core import image_tiler


def _write_image(path, width, height):
    """Diagonal stripes covering every tile.

    Content in all four corners matters: dzsave omits any tile of a uniform
    colour, so an image that is blank anywhere produces a pyramid with holes and
    the "is this tile present" assertions stop meaning what they say.

    The modulo is load-bearing. `(x + y).cast("uchar")` *clips* rather than
    wrapping, which saturates everything outside the top-left triangle to a flat
    255 — and dzsave then correctly omits three of the four native tiles.
    """
    import pyvips
    im = pyvips.Image.xyz(width, height)
    ((im[0] + im[1]) % 255).cast("uchar").write_to_file(str(path))


def test_probe_dimensions_reads_the_header(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 300, 200)
    # 300 != 200 so a transposed return value cannot pass.
    assert image_tiler.probe_dimensions(src) == (300, 200)


def test_needs_tiling_is_false_for_a_screenshot():
    assert image_tiler.needs_tiling(1920, 1080) is False


def test_needs_tiling_is_true_for_the_motivating_image():
    assert image_tiler.needs_tiling(14400, 14400) is True


def test_needs_tiling_boundary_is_the_pixel_count_not_a_side():
    # A long thin panorama under the ceiling stays untiled; the rule is area.
    assert image_tiler.needs_tiling(20000, 100) is False


def test_tiles_relpath_is_keyed_by_media_id():
    assert image_tiler.tiles_relpath(hunt_id=3, media_id=42) == "tiles/3/42"


def test_tile_relpath_uses_google_z_y_x_order():
    # Google layout is z/y/x -- row before column. Getting this backwards
    # transposes the image about its diagonal, so it is asserted with y != x.
    assert image_tiler.tile_relpath(3, 42, z=5, y=9, x=2) == "tiles/3/42/5/9/2.webp"


def test_crop_bytes_returns_png_bytes_and_the_crop_dimensions(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 200, 100)
    png, w, h = image_tiler.crop_bytes(src, (0.0, 0.0, 0.5, 0.5))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert (w, h) == (100, 50)


def test_crop_bytes_selects_the_requested_region_not_a_fixed_one(tmp_path):
    """Two crops of different corners of a gradient must decode to different
    pixels -- proves the rect actually selects where it says, rather than e.g.
    always returning the whole image or the same corner regardless of rect."""
    import pyvips
    src = tmp_path / "src.png"
    _write_image(src, 200, 200)
    top_left, _, _ = image_tiler.crop_bytes(src, (0.0, 0.0, 0.1, 0.1))
    bottom_right, _, _ = image_tiler.crop_bytes(src, (0.9, 0.9, 1.0, 1.0))
    top_left_avg = pyvips.Image.new_from_buffer(top_left, "").avg()
    bottom_right_avg = pyvips.Image.new_from_buffer(bottom_right, "").avg()
    assert top_left_avg != bottom_right_avg


def test_crop_bytes_at_the_far_edge_stays_in_bounds(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 100, 100)
    _, w, h = image_tiler.crop_bytes(src, (0.5, 0.5, 1.0, 1.0))
    assert (w, h) == (50, 50)


def test_crop_bytes_clamps_a_sub_pixel_rect_to_one_pixel(tmp_path):
    """canvas_shape guarantees an ordered, non-zero-area rect in 0-1, but
    rounding one that thin on a small image can still hit 0 -- which libvips'
    crop rejects outright rather than clamping itself."""
    src = tmp_path / "src.png"
    _write_image(src, 100, 100)
    _, w, h = image_tiler.crop_bytes(src, (0.001, 0.001, 0.002, 0.002))
    assert (w, h) == (1, 1)


def test_build_pyramid_returns_dimensions_and_level_count(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 1024, 1024)
    dest = tmp_path / "tiles"
    width, height, levels = image_tiler.build_pyramid(src, dest)
    assert (width, height) == (1024, 1024)
    # 1024 at tile_size 512 is 2x2 tiles; depth=onetile keeps halving until one
    # tile covers the image, so: level 0 (1 tile) and level 1 (2x2).
    assert levels == 2


def test_build_pyramid_writes_tiles_at_both_ends_of_the_pyramid(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 1024, 1024)
    dest = tmp_path / "tiles"
    _, _, levels = image_tiler.build_pyramid(src, dest)
    deepest = levels - 1
    assert (dest / str(deepest) / "1" / "1.webp").is_file()
    assert (dest / "0" / "0" / "0.webp").is_file()


def test_build_pyramid_writes_a_blank_tile_for_the_route_to_fall_back_on(tmp_path):
    src = tmp_path / "src.png"
    _write_image(src, 1024, 1024)
    dest = tmp_path / "tiles"
    image_tiler.build_pyramid(src, dest)
    assert (dest / image_tiler.BLANK_TILE_NAME).is_file()


def test_build_pyramid_replaces_a_stale_pyramid(tmp_path):
    """Rebuilding must not leave tiles from a previous, larger image behind."""
    src = tmp_path / "src.png"
    _write_image(src, 1024, 1024)
    dest = tmp_path / "tiles"
    image_tiler.build_pyramid(src, dest)
    stale = dest / "1" / "1" / "1.webp"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"stale")
    _write_image(src, 300, 300)
    image_tiler.build_pyramid(src, dest)
    assert not stale.is_file()


def test_build_pyramid_raises_on_a_corrupt_image_and_leaves_no_pyramid(tmp_path):
    src = tmp_path / "bad.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage" * 10)
    dest = tmp_path / "tiles"
    with pytest.raises(Exception):
        image_tiler.build_pyramid(src, dest)
    assert not dest.exists()
