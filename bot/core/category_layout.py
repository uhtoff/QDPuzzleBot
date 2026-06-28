"""Multi-category layout for a tag_group (round) that outgrows one Discord
category (50-channel cap). Owns placement, allocation, the single→split
transition, empty-overflow cleanup, and teardown.

A group with no `tag_group_category` rows is in the legacy single-category
layout (one category + the ———solved——— divider); callers keep today's
divider behaviour for it. Once a group splits, every category it owns has a
row recording its role ('unsolved'/'solved') and seq.
"""
import asyncio
import logging

import discord

from bot.core.discord_channels import DELETE_REASON, MAX_CATEGORIES, SOLVE_DIVIDER, clean_name, solve_divider_position
from bot.store.tag_group_category_data import ROLE_SOLVED, ROLE_UNSOLVED, TagGroupCategoryData

logger = logging.getLogger(__name__)

# Allocate/split when a category WOULD cross this, leaving headroom below the
# hard cap (MAX_CHANNELS_PER_CATEGORY = 50) so a create never fails mid-flight.
CATEGORY_SOFT_LIMIT = 45

# Serialises all category-structure mutations (create category, move channel
# between categories, delete category) this module performs.
layout_lock = asyncio.Lock()


# ----------------------------------------------------------------- naming
def solved_category_name(base_name):
    return f"{base_name} — solved"


def overflow_category_name(base_name, role, seq):
    name = solved_category_name(base_name) if role == ROLE_SOLVED else base_name
    if seq >= 1:
        name = f"{name} ({seq + 1})"
    return name


# ----------------------------------------------------------------- helpers
def puzzle_count(category):
    """Channels in a category, excluding the solved divider."""
    return sum(1 for ch in category.channels if ch.name != SOLVE_DIVIDER)


def _category_by_id(guild, category_id):
    return discord.utils.get(guild.categories, id=category_id) if category_id else None


def _home_category(guild, tag_group):
    """The category a group's new puzzles belong in. Normally ``category_id`` IS a
    category; for an in-place-converted / channel-mode group whose home is a
    channel (not a category), fall back to that channel's parent category."""
    primary = _category_by_id(guild, tag_group.category_id)
    if primary is not None:
        return primary
    home = guild.get_channel(tag_group.category_id) if tag_group.category_id else None
    return getattr(home, "category", None)


def primary_category(guild, tag_group):
    """Resolve *tag_group*'s primary Discord **category**, or ``None``.

    ``tag_group.category_id`` is the group's *home container* id, but the kind of
    Discord object it points at varies by how the group was created (see
    :class:`~bot.store.tag_group_data.TagGroupData.category_id`): a real category
    for a normal channel-layout round/meta, but a forum channel, a group's text
    channel, or an in-place-converted puzzle channel otherwise.

    This resolves the *primary category* sense: only groups whose home actually
    *is* a category resolve to one; the other kinds (whose home is some other
    channel) return ``None``. Use this when you need a category to place/move
    channels into; read ``tag_group.category_id`` directly for the raw home id.
    """
    return _category_by_id(guild, tag_group.category_id)


def base_name(guild, tag_group):
    """The group's primary (unsolved seq=0) category name; fall back to the
    cleaned group name if the category has gone missing."""
    primary = _category_by_id(guild, tag_group.category_id)
    return primary.name if primary is not None else clean_name(tag_group.name)


# ----------------------------------------------------------------- state
def group_categories(tag_group, dao):
    return dao.get_for_group(tag_group.id)


def is_split(tag_group, dao):
    return len(group_categories(tag_group, dao)) > 0


# ----------------------------------------------------------------- split
async def split_group(guild, tag_group, dao):
    """Single → split: create the solved category, move solved channels into it,
    delete the divider, and record both seq=0 rows. Idempotent and resumable:
    re-runs when already split are a no-op; a reused-by-name solved category is
    not re-cloned.
    """
    if dao.get_for_group(tag_group.id):
        return  # already split (fast path, no lock)
    primary = _category_by_id(guild, tag_group.category_id)
    if primary is None:
        return
    async with layout_lock:
        if dao.get_for_group(tag_group.id):   # re-check under lock — closes TOCTOU
            return
        base = primary.name
        divider_pos = solve_divider_position(primary)
        solved_cat = discord.utils.get(
            guild.categories, name=solved_category_name(base)
        )
        if solved_cat is None:
            solved_cat = await primary.clone(name=solved_category_name(base))
            await solved_cat.edit(position=primary.position + 1)
        if divider_pos is not False:
            movers = [
                ch for ch in list(primary.channels)
                if ch.name != SOLVE_DIVIDER and ch.position > divider_pos
            ]
            for ch in movers:
                await ch.edit(category=solved_cat)
            divider = discord.utils.get(primary.channels, name=SOLVE_DIVIDER)
            if divider is not None:
                await divider.delete(reason=DELETE_REASON)
        dao.commit(TagGroupCategoryData(
            group_id=tag_group.id, category_id=primary.id,
            role=ROLE_UNSOLVED, seq=0))
        dao.commit(TagGroupCategoryData(
            group_id=tag_group.id, category_id=solved_cat.id,
            role=ROLE_SOLVED, seq=0))


# ----------------------------------------------------------------- placement
async def _allocate(guild, tag_group, dao, role_rows, role):
    """Create the next numbered category for *role* and record its row."""
    if len(guild.categories) >= MAX_CATEGORIES:
        raise RuntimeError("no_category_space")
    template = _category_by_id(guild, tag_group.category_id)
    if template is None:
        raise RuntimeError("primary_category_missing")
    async with layout_lock:
        next_seq = max((r.seq for r in role_rows), default=-1) + 1
        name = overflow_category_name(base_name(guild, tag_group), role, next_seq)
        existing = discord.utils.get(guild.categories, name=name)
        new_cat = existing or await template.clone(name=name)
        if existing is None:
            await new_cat.edit(position=template.position + 1)
        dao.commit(TagGroupCategoryData(
            group_id=tag_group.id, category_id=new_cat.id, role=role, seq=next_seq))
    return new_cat


async def _pick_or_allocate(guild, tag_group, dao, rows, role):
    role_rows = sorted((r for r in rows if r.role == role), key=lambda r: r.seq)
    for r in role_rows:
        cat = _category_by_id(guild, r.category_id)
        if cat is not None and puzzle_count(cat) < CATEGORY_SOFT_LIMIT:
            return cat
    return await _allocate(guild, tag_group, dao, role_rows, role)


async def pick_unsolved_category(guild, tag_group, dao):
    """Category a new puzzle should be created in. Splits the group first if a
    single category would cross the soft limit."""
    rows = dao.get_for_group(tag_group.id)
    if not rows:
        primary = _home_category(guild, tag_group)
        if primary is None:
            return None
        if puzzle_count(primary) + 1 <= CATEGORY_SOFT_LIMIT:
            return primary
        await split_group(guild, tag_group, dao)
        rows = dao.get_for_group(tag_group.id)
    return await _pick_or_allocate(guild, tag_group, dao, rows, ROLE_UNSOLVED)


async def pick_solved_category(guild, tag_group, dao):
    """Category a just-solved puzzle's channel should move into (split state only)."""
    rows = dao.get_for_group(tag_group.id)
    return await _pick_or_allocate(guild, tag_group, dao, rows, ROLE_SOLVED)


# ----------------------------------------------------------------- solve/unsolve moves
def group_id_for_category(category_id, dao):
    """The tag_group id owning *category_id* via the join table, or None."""
    row = dao.get_by_category(category_id)
    return row.group_id if row is not None else None


async def move_channel_to_solved(guild, tag_group, dao, channel):
    """Split state: move a just-solved channel into a solved category with room.
    Returns the source category it left (for empty-overflow cleanup), or None."""
    source = channel.category
    target = await pick_solved_category(guild, tag_group, dao)
    if target is None or (source is not None and target.id == source.id):
        return None
    async with layout_lock:
        await channel.edit(category=target)
    return source


async def move_channel_to_unsolved(guild, tag_group, dao, channel):
    """Split state: move a just-unsolved channel back to an unsolved category."""
    source = channel.category
    target = await pick_unsolved_category(guild, tag_group, dao)
    if target is None or (source is not None and target.id == source.id):
        return None
    async with layout_lock:
        await channel.edit(category=target)
    return source


# ----------------------------------------------------------------- cleanup
async def cleanup_empty_overflow(guild, tag_group, dao, category):
    """Delete *category* and its join row if it is an OVERFLOW category (seq>=1)
    that now holds zero puzzle channels. Never touches a primary (seq=0) category
    and never reverts split→single. Best-effort: logs and swallows Discord errors.
    """
    if category is None:
        return
    row = dao.get_by_category(category.id)
    if row is None or row.seq < 1:
        return
    if puzzle_count(category) > 0:
        return
    try:
        async with layout_lock:
            await category.delete(reason=DELETE_REASON)
        dao.delete(row.id)
    except Exception:
        logger.exception("cleanup_empty_overflow: failed to remove category %s", category.id)


# ----------------------------------------------------------------- teardown
async def delete_group_categories(guild, tag_group, dao):
    """Split state: delete every category the group owns (channels and all) and
    drop the join rows. Returns True if it handled deletion, False if the group
    is in single-category state (caller should run its legacy path).
    """
    rows = dao.get_for_group(tag_group.id)
    if not rows:
        return False
    async with layout_lock:
        for row in rows:
            cat = _category_by_id(guild, row.category_id)
            if cat is None:
                continue
            for ch in list(cat.channels):
                try:
                    await ch.delete(reason=DELETE_REASON)
                except Exception:
                    logger.exception(
                        "delete_group_categories: could not delete channel %s",
                        getattr(ch, "id", "?"))
            try:
                await cat.delete(reason=DELETE_REASON)
            except Exception:
                logger.exception(
                    "delete_group_categories: could not delete category %s", row.category_id)
    dao.delete_for_group(tag_group.id)
    return True
