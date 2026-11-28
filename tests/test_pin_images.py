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
    more_len = len(f"• [+3 more on the dashboard]({BASE}/hunts/{HUNT}?puzzle=7)")
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
    puzzle.parent_puzzle_id = None
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
    puzzle.parent_puzzle_id = None
    puzzle.additional_sheets = []
    hunt = MagicMock()
    hunt.uid = HUNT
    guild_data = MagicMock()
    guild_data.website_url = ""
    cog.service.get_minis.return_value = []
    with patch("bot.cogs.puzzles.pin_images_for", return_value=[PinImage(1, None, False)]) as pif:
        embeds = cog._build_embed_list(embed=discord.Embed(description="d"), puzzle=puzzle, hunt=hunt, guild_data=guild_data)
    assert len(embeds) == 1
    pif.assert_not_called()  # no DB reads when there is nothing to link to


def _embeds_for(puzzle_kwargs, pin_images):
    cog = _cog()
    puzzle = MagicMock()
    puzzle.id = 7
    puzzle.parent_puzzle_id = None
    puzzle.additional_sheets = []
    for k, v in puzzle_kwargs.items():
        setattr(puzzle, k, v)
    hunt = MagicMock()
    hunt.uid = HUNT
    guild_data = MagicMock()
    guild_data.website_url = BASE
    cog.service.get_minis.return_value = []
    with patch("bot.cogs.puzzles.pin_images_for", **pin_images):
        return cog._build_embed_list(embed=discord.Embed(description="d"), puzzle=puzzle,
                                     hunt=hunt, guild_data=guild_data)


def _images_value(embeds):
    return next(f.value for e in embeds for f in e.fields if f.name == "Images")


def test_top_level_puzzle_links_carry_the_puzzle():
    embeds = _embeds_for({}, {"return_value": [PinImage(5, None, False)]})
    assert _images_value(embeds) == f"• [Image 1]({BASE}/hunts/{HUNT}?puzzle=7&image=5)"


def test_mini_links_use_the_image_only_form():
    # Minis are not in the board's puzzle list: ?puzzle=<mini> would show
    # "That puzzle no longer exists".
    embeds = _embeds_for({"parent_puzzle_id": 3},
                         {"return_value": [PinImage(5, None, False), PinImage(6, None, True)]})
    assert _images_value(embeds) == (f"• [Image 1]({BASE}/hunts/{HUNT}?image=5)\n"
                                     f"• [Evidence 1]({BASE}/hunts/{HUNT}?image=6)")


def test_mini_more_line_points_at_the_board():
    imgs = [PinImage(i, None, False) for i in range(1, 4)]
    more = f"• [+3 more on the dashboard]({BASE}/hunts/{HUNT})"
    assert images_field_value(imgs, BASE, HUNT, None, len(more) + 2) == more


def test_a_failing_image_lookup_never_stops_the_pin():
    embeds = _embeds_for({}, {"side_effect": RuntimeError("db down")})
    assert len(embeds) == 1
    assert embeds[0].description == "d"


# --- captions label a puzzle's own pictures ---

def test_pin_images_for_labels_own_images_by_caption_and_evidence_by_note():
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from bot.core.pin_images import pin_images_for
    imgs = MagicMock(); imgs.get_by_puzzle.return_value = [
        SimpleNamespace(media_id=1), SimpleNamespace(media_id=2)]
    ev = MagicMock(); ev.get_image_evidence_for_puzzle.return_value = [
        SimpleNamespace(media_id=3, note="clue note")]
    media = MagicMock(); media.captions.return_value = {2: "**Grid** key"}
    with patch("bot.store.PuzzleImageJsonDb", imgs), patch("bot.store.EvidenceJsonDb", ev), \
         patch("bot.store.MediaJsonDb", media):
        out = pin_images_for(9)
    assert [(p.media_id, p.note, p.is_evidence) for p in out] == [
        (1, None, False), (2, "Grid key", False), (3, "clue note", True)]
    assert sorted(media.captions.call_args.args[0]) == [1, 2]


def test_caption_label_is_plain_text():
    from bot.core.pin_images import caption_label
    assert caption_label("**Grid** __key__ ~~old~~ ||spoiler|| `code`") == "Grid key old spoiler code"
    assert caption_label("  ") is None
    assert caption_label(None) is None
