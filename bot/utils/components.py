"""Components V2 rendering helpers.

Embeds render in a narrow fixed-width card and cap each field at 1024
characters, which is why call sites grew artificial splits like "part 2" field
names. Components V2 (discord.py 2.6+, message flag IS_COMPONENTS_V2) renders
full width and takes markdown directly, so those splits stop being necessary.

The trade is that CV2 is *tighter* on volume, not looser:

    embeds  6000 chars per embed, up to 10 embeds per message
    CV2     4000 chars TOTAL across every TextDisplay in the message,
            and 40 components including nested ones

So this module keeps the same shape as ``build_note_embeds``: it returns a
*list* of views and the caller sends each one, spilling to a new message when
the next section will not fit. Long output is not silently truncated.
"""
from typing import List, Optional, Sequence, Tuple

import discord

# Discord's documented Components V2 ceilings.
TEXT_TOTAL_LIMIT = 4000
MAX_COMPONENTS = 40
# Leave room for the container, the title, and the trailing separator, and a
# little slack so a boundary case cannot produce a 4001-character payload.
_COMPONENT_OVERHEAD = 3
_CHAR_SLACK = 64

Section = Tuple[str, str]   # (heading, body)


def _clip(text: str, limit: int) -> str:
    """Hard-clip with an explicit marker. A section longer than a whole message
    cannot be rendered, and silently dropping the tail would be worse than
    saying so."""
    if len(text) <= limit:
        return text
    marker = "\n… (truncated)"
    return text[: max(0, limit - len(marker))] + marker


def build_sectioned_views(
    *,
    title: Optional[str],
    sections: Sequence[Section],
    accent_colour: Optional[discord.Colour] = None,
    empty_note: Optional[str] = None,
) -> List[discord.ui.LayoutView]:
    """Render ``sections`` as one or more full-width Components V2 views.

    Each section becomes a single TextDisplay of ``### heading`` + body, so a
    long list flows naturally instead of being chopped into 1024-character
    fields with invented "part N" labels.
    """
    if not sections:
        if empty_note is None:
            return []
        sections = [("", empty_note)]

    title_md = f"## {title}\n" if title else ""
    budget = TEXT_TOTAL_LIMIT - _CHAR_SLACK

    views: List[discord.ui.LayoutView] = []
    batch: List[str] = []
    used = len(title_md)

    def flush():
        nonlocal batch, used
        if not batch:
            return
        container = discord.ui.Container(accent_colour=accent_colour)
        if title_md:
            container.add_item(discord.ui.TextDisplay(title_md.rstrip("\n")))
        for i, block in enumerate(batch):
            if i or title_md:
                container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(block))
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        views.append(view)
        batch, used = [], len(title_md)

    for heading, body in sections:
        block = f"### {heading}\n{body}".strip() if heading else body.strip()
        block = _clip(block, budget - len(title_md))

        # +2 components per section (separator + text); flush when either the
        # character budget or the component budget would be exceeded.
        over_chars = used + len(block) > budget
        over_components = _COMPONENT_OVERHEAD + (len(batch) + 1) * 2 > MAX_COMPONENTS
        if batch and (over_chars or over_components):
            flush()

        batch.append(block)
        used += len(block)

    flush()
    return views


async def send_views(ctx, views: Sequence[discord.ui.LayoutView]) -> None:
    """Send each view as its own message.

    A Components V2 message carries no ``content`` and no ``embeds`` - the flag
    is exclusive - so there is nothing to merge and each view is one send.
    """
    for view in views:
        await ctx.send(view=view)
