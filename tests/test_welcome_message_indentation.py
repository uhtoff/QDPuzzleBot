"""Welcome-message bodies must not leak their source indentation.

Message bodies in the cogs are written as triple-quoted literals indented to
match the surrounding code. Python keeps that indentation in the *value*, so
Discord rendered the bullets pushed up to 32 columns right. ``flush_left``
strips it at the point the value is used.

The AST guard below is the useful part: it fails for any *future* bullet
literal that is added without being flush-left'd, not just the ones fixed here.
"""
import ast
import pathlib
import re
from unittest.mock import MagicMock, patch

import discord

from bot.utils.text import flush_left

COG = pathlib.Path("bot/cogs/puzzles.py")
# A bullet sitting after a newline with leading whitespace - i.e. an indented
# continuation line inside a multi-line literal.
INDENTED_BULLET = re.compile(r"\n[ \t]+•")


# --------------------------------------------------------------- unit


def test_flush_left_strips_every_line():
    text = "    • one\n                    • two\n        • three"
    assert flush_left(text) == "• one\n• two\n• three"


def test_flush_left_keeps_blank_lines_as_paragraph_breaks():
    assert flush_left("a\n\n    b") == "a\n\nb"


def test_flush_left_leaves_clean_text_untouched():
    clean = "• one\n• two"
    assert flush_left(clean) == clean


def test_flush_left_handles_empty():
    assert flush_left("") == ""


# --------------------------------------------------- the actual worst case


def _fake_puzzle():
    p = MagicMock()
    p.name = "Some Puzzle"
    p.url = "https://example.com/puzzle"
    p.google_page_id = "gid123"
    return p


def test_puzzle_links_field_has_no_leading_whitespace():
    """This field was the worst offender: 4 spaces on the first bullet and 32
    on the rest."""
    from bot.cogs.puzzles import Puzzles

    cog = object.__new__(Puzzles)
    cog.get_puzzle_sheet = MagicMock(return_value="sheet-id")
    cog._overview_url = MagicMock(return_value="https://example.com/overview")

    hunt = MagicMock(); hunt.google_sheet_id = "hunt-sheet"
    embed = discord.Embed()

    with patch("bot.cogs.puzzles.urls.spreadsheet_url", return_value="https://sheet"), \
         patch("bot.cogs.puzzles.urls.normalize_external_url", return_value="https://puz"):
        cog._add_puzzle_links_field(None, embed, _fake_puzzle(), hunt, None)

    assert embed.fields, "field should have been added"
    value = embed.fields[0].value or ""
    lines = [ln for ln in value.splitlines() if ln]
    assert lines, "field should not be empty"
    for ln in lines:
        assert ln == ln.lstrip(), f"leading whitespace survived: {ln!r}"
    # All three links still present - stripping must not have eaten content.
    assert value.count("•") == 3


# ----------------------------------------------------- regression guard


def _flush_left_wrapped_nodes(tree):
    """Every node sitting inside a flush_left(...) call."""
    inside = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "flush_left"):
            for child in ast.walk(node):
                inside.add(id(child))
    return inside


def _literal_text(node):
    """Approximate the rendered text of a Constant or f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    return None


def test_inline_field_values_with_indented_bullets_are_flush_left_wrapped():
    """Fails when a literal with indented bullets is passed *directly* as a
    field ``value=``/``description=`` without flush_left.

    Scoped to inline literals on purpose. A string built up in a variable
    (``puzzle_links += ...``) is flushed once at the point it is used, which no
    static check can see; those paths are covered by the runtime test above
    instead. This guard catches the realistic regression: someone adding a new
    indented bullet block straight into an add_field call.
    """
    tree = ast.parse(COG.read_text())
    wrapped = _flush_left_wrapped_nodes(tree)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg not in ("value", "description"):
                continue
            if id(kw.value) in wrapped:
                continue
            text = _literal_text(kw.value)
            if text and INDENTED_BULLET.search(text):
                offenders.append(getattr(kw.value, "lineno", "?"))

    assert not offenders, (
        "inline field values with indented bullets not wrapped in flush_left, "
        f"at lines: {offenders}"
    )
