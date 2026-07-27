"""The markup service: validate, then read/write through the DAO.

The DAO is patched at the service's own import site per docs/agents/python.md —
markup.py does `from bot.store import ...` at module load, binding its own name.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from bot.core import markup
from bot.core.markup_shape import MarkupRejected
from bot.store.markup_data import MediaMarkupData

PEN = {"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]}
STYLE = {"colour": "#e03131", "width": 0.004}


def _mark(mark_id=9, media_id=7, kind="pen"):
    m = MediaMarkupData()
    m.id, m.media_id, m.kind = mark_id, media_id, kind
    m.shape = json.dumps({"points": [[0.1, 0.2], [0.3, 0.4]]})
    m.style = json.dumps({"colour": "#e03131", "width": 0.004, "opacity": 1, "arrow": False})
    return m


@pytest.fixture
def db():
    marks = MagicMock()
    marks.create.return_value = _mark()
    marks.get_by_id.return_value = _mark()
    with patch.object(markup, "MediaMarkupJsonDb", marks):
        yield marks


def test_creating_a_mark_stores_validated_geometry_and_style(db):
    markup.create_mark(media_id=7, mark=PEN, style=STYLE)
    kwargs = db.create.call_args.kwargs
    assert kwargs["media_id"] == 7
    assert kwargs["kind"] == "pen"
    assert json.loads(kwargs["shape"])["points"] == [[0.1, 0.2], [0.3, 0.4]]
    # Defaults are filled in by the validator, not left to the DB.
    assert json.loads(kwargs["style"])["opacity"] == 1.0
    assert kwargs["text"] is None


def test_a_malformed_mark_never_reaches_the_db(db):
    with pytest.raises(MarkupRejected):
        markup.create_mark(media_id=7, mark={"kind": "pen", "points": [[9, 9]]},
                           style=STYLE)
    db.create.assert_not_called()


def test_a_text_mark_stores_its_text_separately(db):
    markup.create_mark(
        media_id=7,
        mark={"kind": "text", "points": [[0.2, 0.3]], "text": "ROT13", "size": 0.05},
        style=STYLE)
    kwargs = db.create.call_args.kwargs
    assert kwargs["kind"] == "text"
    assert kwargs["text"] == "ROT13"


def test_updating_a_mark_from_another_image_is_refused(db):
    """The route checks the parent too, but the service must not depend on it:
    a mark id is client-supplied and this is the only place both ids meet."""
    db.get_by_id.return_value = _mark(media_id=99)
    with pytest.raises(markup.MarkNotFound):
        markup.update_mark(mark_id=9, media_id=7, style=STYLE, text=None)
    db.update.assert_not_called()


def test_updating_a_text_mark_keeps_its_text_when_none_is_given(db):
    """An absent text means 'style only'. Reading it as None would silently
    empty a text box on a colour change."""
    existing = _mark(kind="text")
    existing.text = "ROT13"
    db.get_by_id.return_value = existing
    markup.update_mark(mark_id=9, media_id=7, style=STYLE, text=None)
    assert db.update.call_args.kwargs["text"] == "ROT13"


def test_deleting_a_mark_from_another_image_is_refused(db):
    db.get_by_id.return_value = _mark(media_id=99)
    with pytest.raises(markup.MarkNotFound):
        markup.delete_mark(mark_id=9, media_id=7)
    db.delete.assert_not_called()
