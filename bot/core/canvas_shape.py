"""Validation for a canvas region's geometry.

Pure: no DB, no Quart, no image. Coordinates are normalised 0-1 rather than
pixels, which is what lets a region survive re-tiling, a change of image
resolution, and the whole tile-pyramid viewer without any arithmetic.
"""
import json

# Rectangles only for now. The stored value is a shape blob rather than four
# columns so that adding polygons later is a UI change, not a migration.
RECT = "rect"


class ShapeRejected(Exception):
    """A region's geometry was malformed. The message is safe to show a user."""


def _coord(value):
    """One normalised coordinate as a float in 0-1.

    bool is excluded explicitly: it is a subclass of int, so a plain numeric
    check would accept True and turn it into 1.0 — a full-extent region from a
    payload that clearly meant nothing of the kind.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShapeRejected("Coordinates must be numbers.")
    out = float(value)
    if not (0.0 <= out <= 1.0):
        raise ShapeRejected("Coordinates must be between 0 and 1.")
    return out


def parse_shape(payload) -> str:
    """Validate a shape payload and return it as JSON ready to store.

    Rectangles are normalised so points[0] is the top-left corner: a drag from
    bottom-right to top-left describes the same rectangle, and storing it as
    given would mean negative width for every consumer downstream. The two axes
    are normalised independently, so a drag that went right-and-up is handled too.
    """
    if not isinstance(payload, dict):
        raise ShapeRejected("A shape must be an object.")
    if payload.get("type") != RECT:
        raise ShapeRejected("Only rectangles are supported.")
    points = payload.get("points")
    if not isinstance(points, list) or len(points) != 2:
        raise ShapeRejected("A rectangle needs exactly two points.")
    parsed = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ShapeRejected("Each point needs an x and a y.")
        parsed.append((_coord(point[0]), _coord(point[1])))
    (x1, y1), (x2, y2) = parsed
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)
    if x1 == x2 or y1 == y2:
        raise ShapeRejected("A region needs a width and a height.")
    return json.dumps({"type": RECT, "points": [[x1, y1], [x2, y2]]})
