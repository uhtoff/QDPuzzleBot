"""Markup serialisation and routes."""
import json
from types import SimpleNamespace

from bot.web import data as web_data


def _mark(**kw):
    row = dict(id=9, media_id=7, kind="pen",
               shape=json.dumps({"points": [[0.1, 0.2], [0.3, 0.4]]}),
               style=json.dumps({"colour": "#e03131", "width": 0.004,
                                 "opacity": 1, "arrow": False}),
               text=None, created_at=None)
    row.update(kw)
    return SimpleNamespace(**row)


def test_a_mark_serialises_its_shape_and_style_as_objects():
    """Both columns are parsed back rather than passed through as strings, so
    the viewer never double-decodes — same contract as region_to_json."""
    out = web_data.mark_to_json(_mark())
    assert out["shape"]["points"] == [[0.1, 0.2], [0.3, 0.4]]
    assert out["style"]["colour"] == "#e03131"
    assert out["kind"] == "pen"
    assert out["text"] is None


def test_a_text_mark_serialises_its_text():
    out = web_data.mark_to_json(_mark(kind="text", text="ROT13"))
    assert out["text"] == "ROT13"
