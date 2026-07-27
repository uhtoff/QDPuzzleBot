"""Validation for one mark drawn on an image.

Pure: no DB, no Quart, no image. Coordinates are normalised 0-1, the same
choice canvas_shape makes and for the same reason — a mark survives any change
of resolution, and the SVG overlay can use the stored numbers directly.

Deliberately NOT folded into canvas_shape: that module's rectangle rules are
load-bearing for cropping (rect_bounds feeds crop_bytes), and growing branches
there to accommodate ink would put the crop path one refactor away from a bug.
Duplicating a six-line _coord is the cheaper mistake.
"""
import json
import re

PEN = "pen"
LINE = "line"
TEXT = "text"
KINDS = (PEN, LINE, TEXT)

#: A stroke longer than this is refused outright rather than truncated: a
#: silently shortened stroke is a wrong stroke, and the frontend already thins
#: points to ~0.001 apart, so a legitimate drag lands in the low hundreds.
MAX_POINTS = 2000
MAX_TEXT = 500

_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


class MarkupRejected(Exception):
    """A mark was malformed. The message is safe to show a user."""


def _coord(value):
    """One normalised coordinate as a float in 0-1.

    bool is excluded explicitly: it is a subclass of int, so a plain numeric
    check would accept True and place a mark at the image's far corner.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarkupRejected("Coordinates must be numbers.")
    out = float(value)
    if not (0.0 <= out <= 1.0):
        raise MarkupRejected("Coordinates must be between 0 and 1.")
    return out


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarkupRejected(f"{name} must be a number.")
    out = float(value)
    if not (low <= out <= high):
        raise MarkupRejected(f"{name} is out of range.")
    return out


def _points(raw, least, most):
    if not isinstance(raw, list) or not (least <= len(raw) <= most):
        raise MarkupRejected("That mark has the wrong number of points.")
    out = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise MarkupRejected("Each point needs an x and a y.")
        out.append([_coord(point[0]), _coord(point[1])])
    return out


def parse_mark(payload) -> tuple[str, str, str | None]:
    """Validate a mark and return (kind, shape JSON ready to store, text).

    Text travels back separately because it lives in its own column, where it
    can be searched and edited without rewriting the geometry.
    """
    if not isinstance(payload, dict):
        raise MarkupRejected("A mark must be an object.")
    kind = payload.get("kind")
    if kind not in KINDS:
        raise MarkupRejected(f"Kind must be one of: {', '.join(KINDS)}.")
    if kind == TEXT:
        points = _points(payload.get("points"), 1, 1)
        size = _number(payload.get("size"), "Text size", 0.001, 1.0)
        text = (payload.get("text") or "").strip()[:MAX_TEXT]
        if not text:
            raise MarkupRejected("A text box needs some text.")
        return kind, json.dumps({"points": points, "size": size}), text
    least, most = (2, 2) if kind == LINE else (2, MAX_POINTS)
    points = _points(payload.get("points"), least, most)
    # A stray click produces a run of identical points; it would store as an
    # invisible mark that still has to be found and erased.
    if all(p == points[0] for p in points):
        raise MarkupRejected("That mark has no length.")
    return kind, json.dumps({"points": points}), None


def parse_style(payload) -> str:
    """Validate a mark's style and return it as JSON ready to store.

    The colour is echoed straight into an SVG attribute, so it is matched
    against a strict six-digit hex pattern rather than trusted.
    """
    if not isinstance(payload, dict):
        raise MarkupRejected("A style must be an object.")
    colour = payload.get("colour")
    if not isinstance(colour, str) or not _COLOUR.match(colour):
        raise MarkupRejected("Colour must be a hex value like #e03131.")
    return json.dumps({
        "colour": colour.lower(),
        "width": _number(payload.get("width", 0.004), "Width", 0.0005, 0.2),
        "opacity": _number(payload.get("opacity", 1), "Opacity", 0.05, 1.0),
        "arrow": bool(payload.get("arrow", False)),
    })
