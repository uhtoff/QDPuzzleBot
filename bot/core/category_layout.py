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

from bot.core.discord_channels import DELETE_REASON, SOLVE_DIVIDER, clean_name, solve_divider_position
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
        return  # already split
    primary = _category_by_id(guild, tag_group.category_id)
    if primary is None:
        return
    base = primary.name
    divider_pos = solve_divider_position(primary)
    async with layout_lock:
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
