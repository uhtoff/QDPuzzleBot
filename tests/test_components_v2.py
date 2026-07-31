"""Limit handling for the Components V2 renderer.

The interesting failures here are silent ones: content quietly dropped when it
exceeds Discord's 4000-character / 40-component ceilings. Each test below fails
if a spill boundary is computed wrongly, not merely if the code raises.
"""
import discord

from bot.utils.components import (
    MAX_COMPONENTS,
    TEXT_TOTAL_LIMIT,
    build_sectioned_views,
)


def _text_of(view):
    """All TextDisplay content in a view, as Discord would count it."""
    out = []

    def walk(item):
        if isinstance(item, discord.ui.TextDisplay):
            out.append(str(item.content))
        for child in getattr(item, "children", []) or []:
            walk(child)

    for child in view.children:
        walk(child)
    return out


def _component_count(view):
    total = 0

    def walk(item):
        nonlocal total
        total += 1
        for child in getattr(item, "children", []) or []:
            walk(child)

    for child in view.children:
        walk(child)
    return total


def test_short_input_is_a_single_view():
    views = build_sectioned_views(
        title="Puzzles", sections=[("Round 1", "a - #a\nb - #b")]
    )
    assert len(views) == 1
    joined = "\n".join(_text_of(views[0]))
    assert "Round 1" in joined and "a - #a" in joined


def test_sections_keep_their_order_across_a_spill():
    """Order is a relationship, so it needs enough sections to actually be
    wrong - a single section could not detect a reversal or a shuffle."""
    sections = [(f"Round {i}", "x" * 900) for i in range(8)]
    views = build_sectioned_views(title="Puzzles", sections=sections)

    assert len(views) > 1, "900*8 chars must spill past the 4000 limit"
    seen = [
        line
        for v in views
        for block in _text_of(v)
        for line in block.splitlines()
        if line.startswith("### ")
    ]
    assert seen == [f"### Round {i}" for i in range(8)]


def test_no_view_exceeds_the_character_ceiling():
    sections = [(f"R{i}", "y" * 1500) for i in range(10)]
    for view in build_sectioned_views(title="T", sections=sections):
        assert sum(len(t) for t in _text_of(view)) <= TEXT_TOTAL_LIMIT


def test_no_view_exceeds_the_component_ceiling():
    """Many tiny sections blow the component budget long before the character
    budget, so this is a genuinely different boundary from the test above."""
    sections = [(f"R{i}", "z") for i in range(60)]
    views = build_sectioned_views(title="T", sections=sections)
    assert len(views) > 1
    for view in views:
        assert _component_count(view) <= MAX_COMPONENTS


def test_nothing_is_dropped_when_spilling():
    sections = [(f"R{i}", f"body-{i}") for i in range(60)]
    views = build_sectioned_views(title="T", sections=sections)
    joined = "\n".join(t for v in views for t in _text_of(v))
    for i in range(60):
        assert f"body-{i}" in joined, f"section {i} vanished during spill"


def test_oversized_section_is_marked_not_silently_cut():
    views = build_sectioned_views(title="T", sections=[("R", "q" * 9000)])
    joined = "\n".join(_text_of(views[0]))
    assert "truncated" in joined
    assert sum(len(t) for t in _text_of(views[0])) <= TEXT_TOTAL_LIMIT


def test_unboxed_puts_text_at_the_top_level_boxed_wraps_it():
    """The width choice. Unboxed TextDisplays sit directly in the view and
    render as ordinary message text; boxed nests them in a Container, which
    draws an embed-like bordered card."""
    sections = [("Alpha", "puz01\npuz02")]

    unboxed = build_sectioned_views(title="T", sections=sections)[0]
    assert not any(isinstance(c, discord.ui.Container) for c in unboxed.children)
    assert any(isinstance(c, discord.ui.TextDisplay) for c in unboxed.children)

    boxed = build_sectioned_views(title="T", sections=sections, boxed=True)[0]
    assert any(isinstance(c, discord.ui.Container) for c in boxed.children)
    assert not any(isinstance(c, discord.ui.TextDisplay) for c in boxed.children), \
        "boxed should nest text inside the Container, not alongside it"

    # Same content either way - only the wrapping differs.
    assert _text_of(unboxed) == _text_of(boxed)


def test_both_modes_respect_the_component_ceiling():
    sections = [(f"R{i}", "z") for i in range(60)]
    for boxed in (False, True):
        for view in build_sectioned_views(title="T", sections=sections, boxed=boxed):
            assert _component_count(view) <= MAX_COMPONENTS, f"boxed={boxed}"


def test_plain_messages_respect_the_2000_char_content_limit():
    """Plain content is the only rendering that uses the full window width,
    but it has the tightest per-message budget of the three."""
    from bot.utils.components import CONTENT_LIMIT, build_plain_messages

    sections = [(f"Round {i}", "\n".join(f"puz{i}-{j}" for j in range(60)))
                for i in range(6)]
    msgs = build_plain_messages(title="T", sections=sections)
    assert len(msgs) > 1, "should spill across messages"
    for m in msgs:
        assert len(m) <= CONTENT_LIMIT


def test_plain_messages_drop_nothing_and_keep_order():
    from bot.utils.components import build_plain_messages

    sections = [(f"Round {i}", "\n".join(f"puz{i}-{j}" for j in range(40)))
                for i in range(5)]
    joined = "\n".join(build_plain_messages(title="T", sections=sections))
    for i in range(5):
        for j in range(40):
            assert f"puz{i}-{j}" in joined, f"lost puz{i}-{j}"
    headings = [ln for ln in joined.splitlines() if ln.startswith("### ")]
    assert headings == [f"### Round {i}" for i in range(5)]


def test_empty_sections_render_the_note_instead_of_an_empty_message():
    assert build_sectioned_views(title="T", sections=[]) == []
    views = build_sectioned_views(title="T", sections=[], empty_note="No puzzles yet")
    assert len(views) == 1
    assert "No puzzles yet" in "\n".join(_text_of(views[0]))
