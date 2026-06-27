"""Forum configuration helpers for forum-mode hunts.

Sets tags, emoji, and guidelines on forum creation; syncs them after welcome changes.
Computes desired post tags by kind/solved status and applies them selectively.
"""

import discord

# Available forum tags (ordered by precedence for structural tagging)
FORUM_TAGS = ["solved", "meta", "capstone", "metameta", "runaround", "minimeta"]
DEFAULT_REACTION = "\U0001f44d"  # thumbs-up emoji


def forum_create_kwargs():
    """Return kwargs dict to splat into guild.create_forum(...) for a new round/forum."""
    return {
        "available_tags": [discord.ForumTag(name=t) for t in FORUM_TAGS],
        "default_reaction_emoji": DEFAULT_REACTION,
    }


def render_welcome_plaintext(kind, group, puzzle, hunt):
    """Render a plain-text welcome (<=4096 chars) suitable for forum topic/guidelines.

    kind: one of "group", "meta", "round", "capstone"
    """
    name = getattr(group, "name", "?") or "?"
    code = getattr(group, "group_code", "") or ""

    if kind == "group":
        lines = [
            f"Welcome to the round channel for {name}!",
            "",
            "General discussion of the round.",
            "",
            "Commands:",
            "- `!p <puzzle>` — add a puzzle",
            "- `!list` — list all puzzles in this group",
            "- `!info` — re-post this message",
        ]
    elif kind == "meta":
        lines = [
            f"Welcome to the meta channel for {name}!",
            "",
            "General discussion and working on the meta. Note: the first two columns of the sheet are reserved for puzzle titles and answers.",
            "",
            "Commands:",
            "- `!p <puzzle>` — add a puzzle",
            "- `!list` — list all puzzles in this meta",
            "- `!info` — re-post this message",
        ]
    else:
        # capstone, round
        lines = [
            f"Welcome to the channel for {name}!",
            "",
            "General discussion of the round.",
            "",
            "Commands:",
            "- `!p <puzzle>` — add a puzzle",
            "- `!list` — list all puzzles in this round",
            "- `!info` — re-post this message",
        ]

    if code:
        lines.append(f"Group code: {code}")

    return "\n".join(lines)


async def sync_forum_guidelines(guild, hunt, group, puzzle):
    """Update forum topic to match rendered welcome text (idempotent)."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        channel_id = getattr(group, "category_id", None)
        if not channel_id:
            return
        forum = guild.get_channel(channel_id)
        if forum is None:
            return
        # Determine kind from group.type for render
        t = getattr(group, "type", "")
        if t == "Group":
            kind = "group"
        elif t in ("Round", "Metapuzzle"):
            kind = "meta"
        elif t == "Capstone":
            kind = "capstone"
        else:
            kind = "round"
        rendered = render_welcome_plaintext(kind, group, puzzle, hunt)
        if getattr(forum, "topic", None) != rendered:
            await forum.edit(topic=rendered)
    except Exception:
        logger.exception("sync_forum_guidelines failed")


def desired_tags_for(group, puzzle, *, metameta: bool = False, minimeta: bool = False):
    """Compute the set of forum tag names a post should carry.

    Precedence (structural): runaround > metameta > meta > minimeta > capstone.
    + always add "solved" when solved.
    Feeders (no group or puzzle not is_target) get only "solved" if applicable.

    Args:
        group: TagGroupData or None (for unassigned feeders)
        puzzle: PuzzleData
        metameta: pre-computed flag — whether this group is a metameta
        minimeta: pre-computed flag — whether this puzzle has mini-feeds

    Returns:
        set of tag name strings
    """
    tags = set()
    gtype = getattr(group, "type", "") if group is not None else ""
    has_target = bool(
        group is not None and getattr(group, "target_puzzle_id", 0)
        and getattr(puzzle, "is_target", False)
    )

    # Structural tag by precedence: runaround > metameta > meta > minimeta > capstone.
    # "meta" is restricted to the meta-bearing types so a Capstone (which also has a
    # target puzzle) falls through to its own tag rather than being tagged "meta".
    if gtype == "Runaround":
        tags.add("runaround")
    elif metameta:
        tags.add("metameta")
    elif gtype in ("Round", "Metapuzzle") and has_target:
        tags.add("meta")
    elif minimeta:
        tags.add("minimeta")
    elif gtype == "Capstone":
        tags.add("capstone")

    if getattr(puzzle, "solved", False):
        tags.add("solved")

    return tags


def _resolve_tags(available, names):
    """Map a set of tag names to the actual ForumTag objects from available_tags."""
    by_name = {t.name: t for t in available}
    return [t for n, t in by_name.items() if n in names]


async def apply_forum_tags(thread, forum, desired):
    """Apply (or clear) post tags on a thread to match the desired set.

    Args:
        thread: discord.Thread with .applied_tags and .edit()
        forum: discord.ForumChannel with .available_tags
        desired: set of tag name strings
    """
    current_names = {t.name for t in getattr(thread, "applied_tags", [])}
    if current_names == desired:
        return
    new_tags = _resolve_tags(getattr(forum, "available_tags", []), desired)
    await thread.edit(applied_tags=new_tags)
