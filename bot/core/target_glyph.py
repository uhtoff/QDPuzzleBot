"""Emoji glyphs prefixed to a target puzzle's Discord channel name so targets
are identifiable in the sidebar. Pure string logic — no Discord/DB.

Glyph by structural kind (see the design spec's identification section):
runaround 🏃, metameta 🧩🧩, meta 🧩, capstone 👑. Feeders, minimetas, and
solved status get no glyph. The glyph is a Discord-channel-name-only prefix;
the stored puzzle.name never carries it.
"""

GLYPHS = {
    "runaround": "🏃",
    "metameta": "🧩🧩",
    "meta": "🧩",
    "capstone": "👑",
}

# Longest first so a metameta ("🧩🧩") isn't half-stripped as a meta ("🧩").
_STRIP_ORDER = sorted(GLYPHS.values(), key=len, reverse=True)


def glyph_for_kind(kind):
    """The glyph for a structural kind, or "" for kinds/None that carry none."""
    return GLYPHS.get(kind, "")


def glyphed_name(kind, clean_name):
    """A channel name with *kind*'s glyph and a '-' separator prefixed (or bare
    *clean_name* when the kind carries no glyph)."""
    glyph = glyph_for_kind(kind)
    return f"{glyph}-{clean_name}" if glyph else clean_name


def strip_glyph(channel_name):
    """*channel_name* with a leading known glyph (and its '-' separator, if any)
    removed; unchanged if there is no leading glyph."""
    for glyph in _STRIP_ORDER:
        if channel_name.startswith(glyph):
            rest = channel_name[len(glyph):]
            return rest[1:] if rest.startswith("-") else rest
    return channel_name
