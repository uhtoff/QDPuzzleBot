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

# The marker prefixed ahead of the kind glyph for a category's OWN target (i.e.
# its owning group owns a real Discord category — see
# category_layout.primary_category). A nested channel-home target (shares a
# category rather than owning one) keeps just its kind glyph, no marker.
PRIMARY_MARKER = "⭐"


def glyph_for_kind(kind):
    """The glyph for a structural kind, or "" for kinds/None that carry none."""
    return GLYPHS.get(kind, "")


def glyphed_name(kind, clean_name):
    """A channel name with *kind*'s glyph and a '-' separator prefixed (or bare
    *clean_name* when the kind carries no glyph)."""
    glyph = glyph_for_kind(kind)
    return f"{glyph}-{clean_name}" if glyph else clean_name


def marked_name(kind, clean_name, is_primary):
    """glyphed_name plus the ⭐ primary marker when *is_primary* (a category's
    own target). Feeders (kind None) get no marker and no glyph."""
    name = glyphed_name(kind, clean_name)
    if kind is not None and is_primary:
        return PRIMARY_MARKER + name
    return name


