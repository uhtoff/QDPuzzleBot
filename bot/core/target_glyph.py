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
    """A channel name with *kind*'s glyph prefixed (or bare *clean_name*)."""
    return glyph_for_kind(kind) + clean_name


def strip_glyph(channel_name):
    """*channel_name* with a leading known glyph removed (or unchanged)."""
    for glyph in _STRIP_ORDER:
        if channel_name.startswith(glyph):
            return channel_name[len(glyph):]
    return channel_name
