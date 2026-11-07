from bot.core.target_glyph import GLYPHS, glyph_for_kind, glyphed_name  # noqa: F401


def test_glyph_for_each_kind():
    assert glyph_for_kind("runaround") == "🏃"
    assert glyph_for_kind("metameta") == "🧩🧩"
    assert glyph_for_kind("meta") == "🧩"
    assert glyph_for_kind("capstone") == "👑"


def test_glyph_for_unglyphed_kinds_is_empty():
    assert glyph_for_kind("minimeta") == ""
    assert glyph_for_kind(None) == ""
    assert glyph_for_kind("feeder") == ""


def test_glyphed_name_prefixes():
    assert glyphed_name("meta", "spooky-meta") == "🧩-spooky-meta"
    assert glyphed_name("capstone", "final") == "👑-final"
    assert glyphed_name(None, "just-a-feeder") == "just-a-feeder"


def test_marked_name_primary_gets_star():
    from bot.core.target_glyph import marked_name
    assert marked_name("meta", "round-meta", True) == "⭐🧩-round-meta"
    assert marked_name("meta", "sub", False) == "🧩-sub"
    assert marked_name(None, "feeder", True) == "feeder"   # feeders never marked


