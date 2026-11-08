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


# ------------------------------------------- sheet mode wording (Task 7)


def _hunt_welcome_info(hunt):
    import asyncio
    from bot.cogs.puzzles import Puzzles

    cog = object.__new__(Puzzles)
    cog._overview_url = MagicMock(return_value="https://example.com/overview")
    channel = MagicMock()
    sent = {}

    async def send(embed=None, **kw):
        sent["embed"] = embed
    channel.send = send
    asyncio.run(cog.send_initial_hunt_channel_messages(None, channel, hunt=hunt))
    field = next(f for f in sent["embed"].fields if f.name == "Hunt Information")
    return field.value or ""


def _welcome_hunt(**kw):
    from tests.conftest import make_hunt
    h = make_hunt(google_sheet_id="REF", **kw)
    h.name = "H"
    h.url = "https://hunt.example"
    h.start_timestamp = 0
    return h


def test_files_hunt_welcome_links_the_reference_sheet_and_no_solved_sheet():
    value = _hunt_welcome_info(_welcome_hunt(solved_sheet_id="", sheet_mode="files"))
    assert "• [Reference sheet](https://docs.google.com/spreadsheets/d/REF)" in value
    assert "Google Sheet" not in value
    assert "Solved Puzzles Sheet" not in value


def test_files_hunt_welcome_links_the_drive_folder():
    value = _hunt_welcome_info(_welcome_hunt(sheet_mode="files", drive_folder_id="FOLDER"))
    assert "• [Drive folder](https://drive.google.com/drive/folders/FOLDER)" in value


def test_welcome_has_no_drive_folder_line_without_a_folder():
    assert "Drive folder" not in _hunt_welcome_info(_welcome_hunt(sheet_mode="files"))
    assert "Drive folder" not in _hunt_welcome_info(_welcome_hunt(drive_folder_id="X"))


def test_tabs_hunt_welcome_is_unchanged():
    value = _hunt_welcome_info(_welcome_hunt(solved_sheet_id="ARCH"))
    assert "• [Google Sheet](https://docs.google.com/spreadsheets/d/REF)" in value
    assert "• [Solved Puzzles Sheet](https://docs.google.com/spreadsheets/d/ARCH)" in value
    assert "Reference sheet" not in value


def test_files_puzzle_links_keep_the_puzzles_file_and_add_the_reference_sheet():
    from bot.cogs.puzzles import Puzzles
    from tests.conftest import make_hunt, make_puzzle

    cog = object.__new__(Puzzles)
    cog._overview_url = MagicMock(return_value="https://example.com/overview")
    hunt = make_hunt(google_sheet_id="REF", solved_sheet_id="", sheet_mode="files")
    puzzle = make_puzzle(name="P")
    puzzle.url = "https://example.com/p"
    puzzle.google_sheet_id = "PFILE"
    puzzle.google_page_id = 5
    embed = discord.Embed()
    cog._add_puzzle_links_field(None, embed, puzzle, hunt, None)

    value = embed.fields[0].value or ""
    assert "• [Google Sheet](https://docs.google.com/spreadsheets/d/PFILE/edit#gid=5)" in value
    assert "• [Reference sheet](https://docs.google.com/spreadsheets/d/REF)" in value
    assert value.count("•") == 4
    for ln in (ln for ln in value.splitlines() if ln):
        assert ln == ln.lstrip()



def test_puzzle_links_field_includes_the_voice_channel_link():
    """A puzzle with a voice channel gets a clickable <#id> line in its pin,
    flush left like the rest; the plain fake (no int id) gets none (above)."""
    from bot.cogs.puzzles import Puzzles

    cog = object.__new__(Puzzles)
    cog.get_puzzle_sheet = MagicMock(return_value="sheet-id")
    cog._overview_url = MagicMock(return_value="https://example.com/overview")
    hunt = MagicMock(); hunt.google_sheet_id = "hunt-sheet"; hunt.sheet_mode = "tabs"
    puzzle = _fake_puzzle(); puzzle.voice_channel_id = 987
    embed = discord.Embed()
    with patch("bot.cogs.puzzles.urls.spreadsheet_url", return_value="https://sheet"), \
         patch("bot.cogs.puzzles.urls.normalize_external_url", return_value="https://puz"):
        cog._add_puzzle_links_field(None, embed, puzzle, hunt, None)
    lines = [ln for ln in (embed.fields[0].value or "").splitlines() if ln]
    assert "• Voice: <#987>" in lines
    assert lines[-1].startswith("• [Overview Website]")
