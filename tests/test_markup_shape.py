"""Pure validation of a mark's geometry, text and style.

Mirrors tests/test_canvas_shape.py: no DB, no Quart, no image.
"""
import json

import pytest

from bot.core.markup_shape import MarkupRejected, parse_mark, parse_style


def test_a_pen_stroke_round_trips_its_points():
    kind, shape, text = parse_mark({"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]})
    assert kind == "pen"
    assert json.loads(shape)["points"] == [[0.1, 0.2], [0.3, 0.4]]
    assert text is None


def test_a_line_needs_exactly_two_points():
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "line", "points": [[0.1, 0.2]]})
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "line", "points": [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]})


def test_a_zero_length_stroke_is_rejected():
    """A stray click must not persist as an invisible mark."""
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "pen", "points": [[0.5, 0.5], [0.5, 0.5]]})


def test_too_many_points_are_rejected_not_truncated():
    """A silently shortened stroke is a WRONG stroke, and an uncapped TEXT blob
    is the actual hazard. Two assertions, so the cap is pinned at its edge:
    exactly MAX_POINTS is fine, one more is refused."""
    from bot.core.markup_shape import MAX_POINTS
    ok = [[i / (MAX_POINTS * 2), 0.5] for i in range(MAX_POINTS)]
    assert json.loads(parse_mark({"kind": "pen", "points": ok})[1])["points"]
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "pen", "points": ok + [[0.9, 0.9]]})


def test_coordinates_must_be_in_range_and_not_bools():
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "pen", "points": [[0.1, 0.2], [1.4, 0.4]]})
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "pen", "points": [[True, 0.2], [0.3, 0.4]]})


def test_a_text_mark_carries_its_string_and_size():
    kind, shape, text = parse_mark(
        {"kind": "text", "points": [[0.2, 0.3]], "text": "  answer  ", "size": 0.05})
    assert kind == "text"
    assert json.loads(shape) == {"points": [[0.2, 0.3]], "size": 0.05}
    assert text == "answer"


def test_empty_text_is_rejected():
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "text", "points": [[0.2, 0.3]], "text": "   ", "size": 0.05})


def test_an_unknown_kind_is_rejected():
    with pytest.raises(MarkupRejected):
        parse_mark({"kind": "polygon", "points": [[0.1, 0.2], [0.3, 0.4]]})


def test_a_style_round_trips():
    assert json.loads(parse_style(
        {"colour": "#E03131", "width": 0.004, "opacity": 0.4, "arrow": True})) == {
        "colour": "#e03131", "width": 0.004, "opacity": 0.4, "arrow": True}


def test_a_bad_colour_is_rejected():
    """The colour is echoed into an SVG attribute, so it is validated rather
    than trusted."""
    with pytest.raises(MarkupRejected):
        parse_style({"colour": "red; onload=alert(1)", "width": 0.004})
