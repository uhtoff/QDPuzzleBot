"""Images field in the pinned info message: pure formatter + whole-message budget."""
from unittest.mock import MagicMock, patch

import discord

from bot.cogs.puzzles import Puzzles
from bot.core.pin_images import PinImage, images_field_value

BASE = "https://dash.example"
HUNT = "abc123"


def _val(images, budget=1024, base=BASE):
    return images_field_value(images, base, HUNT, 7, budget)


def test_order_and_numbering():
    imgs = [
        PinImage(11, None, False),
        PinImage(12, None, False),
        PinImage(21, "a note", True),
        PinImage(22, None, True),
    ]
    lines = _val(imgs).split("\n")
    assert len(lines) == 4
    assert all(ln.startswith("• [") for ln in lines)
    assert "Image 1" in lines[0] and "image=11" in lines[0]
    assert "Image 2" in lines[1] and "image=12" in lines[1]
    assert "Evidence: a note" in lines[2] and "image=21" in lines[2]
    assert "Evidence 2" in lines[3] and "image=22" in lines[3]
    for ln in lines:
        assert f"{BASE}/hunts/{HUNT}?puzzle=7&image=" in ln


def test_note_collapsed_escaped_truncated():
    note = "look ] at (this)\nnow *bold* _x_ ~y~ `z` |p| \\ " + "w" * 80
    value = _val([PinImage(5, note, True)])
    assert "\n" not in value
    label = value[value.index("[") + 1:value.index("](")]
    for ch in "[]()*_~`|":
        # every occurrence in the label is preceded by a backslash
        for i, c in enumerate(label):
            if c == ch:
                assert label[i - 1] == "\\"
    assert label.endswith("…")
    assert len(label) <= len("Evidence: ") + 40 + 1 + 20  # escapes add backslashes


def test_overflow_adds_more_line():
    imgs = [PinImage(i, None, False) for i in range(1, 61)]
    value = _val(imgs, budget=1024)
    assert len(value) <= 1024
    lines = value.split("\n")
    shown = len(lines) - 1
    assert 0 < shown < 60
    assert lines[-1].startswith(f"• [+{60 - shown} more on the dashboard](")
    assert lines[-1].endswith(f"/hunts/{HUNT}?puzzle=7)")


def test_none_cases():
    imgs = [PinImage(1, None, False)]
    assert _val(imgs, budget=30) is None
    assert _val(imgs, budget=5) is None
    assert _val(imgs, base="") is None
    assert _val([]) is None


def test_only_more_line_when_no_image_line_fits():
    imgs = [PinImage(i, None, False) for i in range(1, 4)]
    line_len = len(_val(imgs[:1]))
    more_len = len(f"• [+3 more on the dashboard]({BASE}/hunts/{HUNT}?puzzle=7)")
    assert more_len < line_len + 1 + more_len  # sanity
    value = _val(imgs, budget=more_len + 2)
    assert value == f"• [+3 more on the dashboard]({BASE}/hunts/{HUNT}?puzzle=7)"


def _cog():
    cog = Puzzles.__new__(Puzzles)
    cog.service = MagicMock()
    return cog


def test_whole_message_stays_within_6000():
    cog = _cog()
    puzzle = MagicMock()
    puzzle.id = 7
    puzzle.additional_sheets = []
    hunt = MagicMock()
    hunt.uid = HUNT
    guild_data = MagicMock()
    guild_data.website_url = BASE
    cog.service.get_minis.return_value = [object()]
    minis = discord.Embed(description="m" * 4000)
    minis.add_field(name="x", value="y" * 1000)
    minis.add_field(name="z", value="y" * 480)
    main = discord.Embed(description="d")
    imgs = [PinImage(i, "note %d" % i, i % 2 == 0) for i in range(1, 31)]
    with patch.object(cog, "_build_mini_list_embed", return_value=minis), \
            patch("bot.cogs.puzzles.pin_images_for", return_value=imgs):
        embeds = cog._build_embed_list(embed=main, puzzle=puzzle, hunt=hunt, guild_data=guild_data)
    assert sum(len(e) for e in embeds) <= 6000
    assert any(f.name == "Images" for e in embeds for f in e.fields)


def test_no_images_embed_when_no_base():
    cog = _cog()
    puzzle = MagicMock()
    puzzle.id = 7
    puzzle.additional_sheets = []
    hunt = MagicMock()
    hunt.uid = HUNT
    guild_data = MagicMock()
    guild_data.website_url = ""
    cog.service.get_minis.return_value = []
    with patch("bot.cogs.puzzles.pin_images_for", return_value=[PinImage(1, None, False)]):
        embeds = cog._build_embed_list(embed=discord.Embed(description="d"), puzzle=puzzle, hunt=hunt, guild_data=guild_data)
    assert len(embeds) == 1
