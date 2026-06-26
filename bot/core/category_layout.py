"""Multi-category layout for a tag_group (round) that outgrows one Discord
category (50-channel cap). Owns placement, allocation, the single→split
transition, empty-overflow cleanup, and teardown.

A group with no `tag_group_category` rows is in the legacy single-category
layout (one category + the ———solved——— divider); callers keep today's
divider behaviour for it. Once a group splits, every category it owns has a
row recording its role ('unsolved'/'solved') and seq.
"""
import logging

import discord

from bot.core.discord_channels import SOLVE_DIVIDER, clean_name
from bot.store.tag_group_category_data import ROLE_SOLVED

logger = logging.getLogger(__name__)

# Allocate/split when a category WOULD cross this, leaving headroom below the
# hard cap (MAX_CHANNELS_PER_CATEGORY = 50) so a create never fails mid-flight.
CATEGORY_SOFT_LIMIT = 45


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
