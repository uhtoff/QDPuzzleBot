"""Markup on a stored image.

Purely visual: a mark is never linked to a puzzle, never spawns an evidence
row, and is never cropped or OCR'd. That is what keeps this module free of the
region/evidence coupling canvas.py owns — there is nothing here to keep in step
with anything else.

Lock-free by the bot/core convention: callers lock.
"""
import logging

from bot.core.markup_shape import parse_mark, parse_style
from bot.store import MediaMarkupJsonDb

logger = logging.getLogger(__name__)


class MarkNotFound(Exception):
    """No such mark on that image. The message is safe to show a user."""


def list_marks(media_id: int) -> list:
    """Every mark on one image, oldest first — SVG paint order is document
    order, so a later mark must draw over an earlier one."""
    return MediaMarkupJsonDb.get_by_media(media_id)


def create_mark(media_id: int, mark, style):
    """Validate and store one mark."""
    kind, shape, text = parse_mark(mark)
    return MediaMarkupJsonDb.create(
        media_id=media_id, kind=kind, shape=shape,
        style=parse_style(style), text=text)


def _owned(mark_id: int, media_id: int):
    """The mark, if it belongs to that image.

    Checked here and not only in the route: a mark id is client-supplied, and
    this is the one place both ids are in scope.
    """
    existing = MediaMarkupJsonDb.get_by_id(mark_id)
    if existing is None or existing.media_id != media_id:
        raise MarkNotFound("That mark no longer exists.")
    return existing


def update_mark(mark_id: int, media_id: int, style, text=None):
    """Restyle a mark, and retext it when `text` is given.

    Geometry is never updated: a redrawn stroke is a new mark. A None `text`
    means "unchanged" rather than "empty" — reading it as empty would wipe a
    text box every time someone changed its colour.
    """
    existing = _owned(mark_id, media_id)
    MediaMarkupJsonDb.update(
        mark_id, style=parse_style(style),
        text=existing.text if text is None else text.strip()[:500])
    return MediaMarkupJsonDb.get_by_id(mark_id)


def delete_mark(mark_id: int, media_id: int) -> None:
    _owned(mark_id, media_id)
    MediaMarkupJsonDb.delete(mark_id)
