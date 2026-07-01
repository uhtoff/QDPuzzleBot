"""Unit tests for derive_is_minimeta — the derived flag that identifies a minimeta
by structure (owns mini children) or the legacy puzzle_type marker, so the UI never
gates on the user-editable puzzle_type string.
"""
from bot.web.data import derive_is_minimeta


def test_has_children_is_minimeta():
    p = {"id": 1, "mini_puzzles": [{"id": 2}], "puzzle_type": ""}
    derive_is_minimeta([p])
    assert p["is_minimeta"] is True


def test_type_flag_only_is_minimeta():
    # Freshly made minimeta: type set, no minis yet.
    p = {"id": 1, "mini_puzzles": [], "puzzle_type": "Minimeta"}
    derive_is_minimeta([p])
    assert p["is_minimeta"] is True


def test_no_children_no_flag_is_not_minimeta():
    p = {"id": 1, "mini_puzzles": [], "puzzle_type": "Cryptic"}
    derive_is_minimeta([p])
    assert p["is_minimeta"] is False


def test_children_override_editable_type():
    # Type edited away from Minimeta but minis still exist — still a minimeta.
    p = {"id": 1, "mini_puzzles": [{"id": 2}], "puzzle_type": "Cryptic"}
    derive_is_minimeta([p])
    assert p["is_minimeta"] is True
