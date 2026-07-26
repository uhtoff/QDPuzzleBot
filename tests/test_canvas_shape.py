"""Pure geometry validation for canvas regions. No DB, no Quart."""
import json

import pytest

from bot.core.canvas_shape import ShapeRejected, parse_shape


def test_a_plain_rect_round_trips():
    out = parse_shape({"type": "rect", "points": [[0.1, 0.2], [0.3, 0.4]]})
    assert json.loads(out) == {"type": "rect", "points": [[0.1, 0.2], [0.3, 0.4]]}


def test_a_drag_up_and_left_is_normalised():
    """A drag from bottom-right to top-left describes the same rectangle. Storing
    it as given would mean negative width for every consumer downstream."""
    out = json.loads(parse_shape({"type": "rect", "points": [[0.8, 0.9], [0.2, 0.3]]}))
    assert out["points"] == [[0.2, 0.3], [0.8, 0.9]]


def test_a_mixed_direction_drag_is_normalised_on_both_axes():
    """x and y are normalised independently — a drag right-and-up transposes only
    one of them, which a same-direction test would not catch."""
    out = json.loads(parse_shape({"type": "rect", "points": [[0.2, 0.9], [0.8, 0.3]]}))
    assert out["points"] == [[0.2, 0.3], [0.8, 0.9]]


def test_full_extent_is_allowed():
    out = json.loads(parse_shape({"type": "rect", "points": [[0.0, 0.0], [1.0, 1.0]]}))
    assert out["points"] == [[0.0, 0.0], [1.0, 1.0]]


@pytest.mark.parametrize("pts", [
    [[-0.01, 0.2], [0.3, 0.4]],
    [[0.1, 0.2], [1.01, 0.4]],
    [[0.1, -5.0], [0.3, 0.4]],
])
def test_coordinates_outside_the_image_are_rejected(pts):
    with pytest.raises(ShapeRejected):
        parse_shape({"type": "rect", "points": pts})


@pytest.mark.parametrize("pts", [
    [[0.5, 0.2], [0.5, 0.4]],       # zero width
    [[0.1, 0.5], [0.3, 0.5]],       # zero height
])
def test_a_zero_area_region_is_rejected(pts):
    """Un-clickable, and the product of a stray click rather than a drag."""
    with pytest.raises(ShapeRejected):
        parse_shape({"type": "rect", "points": pts})


@pytest.mark.parametrize("payload", [
    {"type": "circle", "points": [[0.1, 0.2], [0.3, 0.4]]},
    {"type": "rect", "points": [[0.1, 0.2]]},
    {"type": "rect", "points": [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]},
    {"type": "rect", "points": "nope"},
    {"type": "rect", "points": [[0.1, 0.2], "nope"]},
    {"type": "rect"},
    {"points": [[0.1, 0.2], [0.3, 0.4]]},
    "not a dict",
    None,
])
def test_malformed_payloads_are_rejected(payload):
    with pytest.raises(ShapeRejected):
        parse_shape(payload)


def test_non_numeric_coordinates_are_rejected():
    with pytest.raises(ShapeRejected):
        parse_shape({"type": "rect", "points": [["a", 0.2], [0.3, 0.4]]})


def test_booleans_are_not_accepted_as_coordinates():
    """bool is an int subclass, so a naive isinstance check lets True through as
    1.0 and silently produces a full-width region."""
    with pytest.raises(ShapeRejected):
        parse_shape({"type": "rect", "points": [[True, 0.2], [0.3, 0.4]]})
