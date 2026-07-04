"""Multi-category layout for an *owner* — a tag_group (round) or a hunt's own
general container — that outgrows one Discord category (50-channel cap). Owns
placement, allocation, the single→split transition, empty-overflow cleanup,
and teardown.

Generic over the owner: every function here only needs ``owner.id``,
``owner.category_id``, and ``owner.name`` (a ``TagGroupData`` or ``HuntData``
both qualify), plus a ``dao`` — ``TagGroupCategoryDb`` or ``HuntCategoryDb`` —
exposing the same duck-typed interface (``get_for_group``, ``get_by_category``,
``delete_for_group``, ``make_row``) so the same split/allocate/teardown code
serves both without caring which owner it's called for.

An owner with no category rows (in its `dao`) is in the legacy single-category
layout (one category + the ———solved——— divider); callers keep today's
divider behaviour for it. Once an owner splits, every category it owns has a
row recording its role ('unsolved'/'solved') and seq.
"""
import asyncio
import logging

import discord

from bot.core.discord_channels import DELETE_REASON, MAX_CATEGORIES, MAX_CHANNELS_PER_CATEGORY, SOLVE_DIVIDER, clean_channel_name, clean_category_name, solve_divider_position
from bot.store.tag_group_category_data import ROLE_SOLVED, ROLE_UNSOLVED

logger = logging.getLogger(__name__)

# Allocate/split when a category WOULD cross this, leaving a small headroom
# below the hard cap so a create never fails mid-flight — just enough room to
# delete the solve divider and land the new channel (2 slots), not a large
# fixed buffer. A hunt server already tends to have a lot of categories, so
# splitting/overflowing earlier than necessary means more categories than
# needed; keep the buffer minimal rather than proportional to the cap.
CATEGORY_SOFT_LIMIT = max(1, MAX_CHANNELS_PER_CATEGORY - 2)

# Serialises all category-structure mutations (create category, move channel
# between categories, delete category) this module performs.
layout_lock = asyncio.Lock()

# Consolidation hysteresis: a fold/un-split only happens if the result leaves
# at least this many free slots below CATEGORY_SOFT_LIMIT — otherwise a merge
# right at the split threshold would oscillate (each split↔merge cycle costs a
# category create + N channel moves + a delete). Fixed, not proportional, for
# the same reason the soft-limit headroom is: the cap is config-shrinkable for
# tests, and a proportional margin either vanishes at cap 50 or devours the
# whole range at test caps.
CONSOLIDATE_MARGIN = 4


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


def group_chat_channel(guild, tag_group):
    """The dedicated text channel for a target-less (Group-type) round's
    primary category, or None if the group has a target, its primary category
    can't be resolved (forum mode, or missing), or no channel matches. Same
    lookup group_rename_plan uses for its own rename step: the channel was
    created from clean_channel_name(tag_group.name) and every rename keeps
    that invariant, so matching by name (not by category name, which can gain
    a create-time collision suffix the channel never receives) is required.
    """
    if tag_group.has_target:
        return None
    primary = primary_category(guild, tag_group)
    if primary is None:
        return None
    return discord.utils.get(primary.text_channels, name=clean_channel_name(tag_group.name))


def base_name(guild, tag_group):
    """The group's primary (unsolved seq=0) category name; fall back to the
    cleaned group name if the category has gone missing."""
    primary = _category_by_id(guild, tag_group.category_id)
    return primary.name if primary is not None else clean_category_name(tag_group.name)


# ----------------------------------------------------------------- state
def _category_claimed(category_id):
    """True if *category_id* is already claimed by ANY owner: a row in either
    join table, or some tag_group's/hunt's own home category. Used to gate
    reuse-by-name adoption: an unclaimed same-name category is our own clone
    from a run that died before committing its row (safe to adopt); a claimed
    one merely shares the name — adopting it would commit a second owner and
    delete_group_categories would later destroy the other owner's channels.
    Imported lazily to keep this module import-decoupled from the DB layer.
    """
    from bot.store import TagGroupCategoryDb, HuntCategoryDb, TagGroupJsonDb, HuntJsonDb
    return (
        TagGroupCategoryDb.get_by_category(category_id) is not None
        or HuntCategoryDb.get_by_category(category_id) is not None
        or TagGroupJsonDb.get_by_attr(category_id=category_id) is not None
        or HuntJsonDb.get_by_attr(category_id=category_id) is not None
    )


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
        if solved_cat is not None and _category_claimed(solved_cat.id):
            solved_cat = None   # merely shares the name — clone fresh instead
        if solved_cat is None:
            solved_cat = await primary.clone(name=solved_category_name(base))
            await solved_cat.edit(position=primary.position + 1)
        if divider_pos is not False:
            # Text channels only: .channels also lists voice channels, whose
            # positions Discord numbers in a SEPARATE sorting bucket — comparing
            # one against the divider's text position is meaningless and could
            # drag a lobby channel into the solved category.
            movers = [
                ch for ch in list(primary.text_channels)
                if ch.name != SOLVE_DIVIDER and ch.position > divider_pos
            ]
            # Explicit positions, appended after anything already in the target
            # (the reused-by-name resume path): discord.py only re-sorts when
            # `position` is passed — without it each mover keeps its old
            # absolute position number in the new category.
            base_pos = max((c.position for c in solved_cat.channels), default=-1) + 1
            for i, ch in enumerate(movers):
                await ch.edit(category=solved_cat, position=base_pos + i)
            divider = discord.utils.get(primary.channels, name=SOLVE_DIVIDER)
            if divider is not None:
                await divider.delete(reason=DELETE_REASON)
        dao.commit(dao.make_row(tag_group.id, primary.id, ROLE_UNSOLVED, 0))
        dao.commit(dao.make_row(tag_group.id, solved_cat.id, ROLE_SOLVED, 0))


# ----------------------------------------------------------------- placement
async def _allocate(guild, tag_group, dao, role_rows, role):
    """Create the next numbered category for *role* and record its row."""
    async with layout_lock:
        # Everything is checked under the lock — including the guild category
        # ceiling: two concurrent allocations at 49 categories would otherwise
        # both pass the gate, and the loser's clone would surface a raw
        # HTTPException instead of the structured error callers map to a
        # friendly message.
        if len(guild.categories) >= MAX_CATEGORIES:
            raise RuntimeError("no_category_space")
        template = _category_by_id(guild, tag_group.category_id)
        if template is None:
            raise RuntimeError("primary_category_missing")
        # Re-read under the lock — closes the TOCTOU where two concurrent
        # allocations for the same group/role both compute the caller's
        # pre-lock role_rows and pick the same next_seq.
        role_rows = [r for r in dao.get_for_group(tag_group.id) if r.role == role]
        next_seq = max((r.seq for r in role_rows), default=-1) + 1
        name = overflow_category_name(base_name(guild, tag_group), role, next_seq)
        existing = discord.utils.get(guild.categories, name=name)
        if existing is not None and _category_claimed(existing.id):
            existing = None     # merely shares the name — clone fresh instead
        new_cat = existing or await template.clone(name=name)
        if existing is None:
            await new_cat.edit(position=template.position + 1)
        dao.commit(dao.make_row(tag_group.id, new_cat.id, role, next_seq))
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
    # Discord's channel.edit only re-sorts position when a `position` kwarg is
    # given (discord.py's abc._edit calls _move only in that case) — passing
    # just `category=` reparents the channel but keeps its OLD absolute
    # position number, which can easily be lower than everything already in
    # the new category, landing it at the top instead of the end. Always
    # request a position explicitly — computed INSIDE the lock (not before
    # it), so two concurrent moves into the same category can't both read the
    # same stale channel list and race for the same slot.
    async with layout_lock:
        position = max((ch.position for ch in target.channels), default=-1) + 1
        await channel.edit(category=target, position=position)
    return source


async def move_channel_to_unsolved(guild, tag_group, dao, channel):
    """Split state: move a just-unsolved channel back to an unsolved category."""
    source = channel.category
    target = await pick_unsolved_category(guild, tag_group, dao)
    if target is None or (source is not None and target.id == source.id):
        return None
    # See move_channel_to_solved: position must be explicit and computed
    # inside the lock, or the channel keeps its old (wrong) absolute position,
    # or two concurrent moves race for the same freshly-computed slot.
    async with layout_lock:
        position = max((ch.position for ch in target.channels), default=-1) + 1
        await channel.edit(category=target, position=position)
    return source


async def move_channel_to_owner(guild, owner, dao, channel, solved):
    """Relocate *channel* into whichever of *owner*'s categories is correct for
    *solved* — used when a puzzle's channel needs to move to a DIFFERENT
    owner entirely (e.g. delete_round's keep_puzzles relocation), as opposed
    to move_channel_to_solved/_unsolved's same-owner solve/unsolve reposition.

    Split state: delegates to move_channel_to_solved/_unsolved and cleans up
    any overflow category the move emptied. Legacy (not yet split) state:
    positions the channel relative to the existing solve divider if there's
    room, or proactively splits (mirroring pick_unsolved_category's own
    proactive split) and retries if the primary category is already at the
    soft limit — so an owner with room never gets force-split just because a
    channel arrived, matching the round soft-limit behaviour instead of
    special-casing "general" categories.

    Returns True if moved (or already correctly placed), False if there's no
    usable category for *owner* or the move failed.
    """
    if is_split(owner, dao):
        mover = move_channel_to_solved if solved else move_channel_to_unsolved
        try:
            source = await mover(guild, owner, dao, channel)
        except Exception:
            logger.exception("move_channel_to_owner: could not move channel %s",
                             getattr(channel, "id", "?"))
            return False
        if source is not None:
            # Table-agnostic: on a cross-owner relocation the vacated source
            # belongs to the OTHER join table (e.g. hunt overflow → round),
            # which the destination's dao would silently never match.
            await cleanup_source_category(guild, source)
        return True

    primary = _home_category(guild, owner)
    if primary is None:
        return False
    if channel.category_id == primary.id:
        return True
    if puzzle_count(primary) + 1 > CATEGORY_SOFT_LIMIT:
        if _category_by_id(guild, owner.category_id) is None:
            # The owner's home is a channel (in-place-converted meta / forum
            # mode), so `primary` is that channel's HOST category — not ours
            # to split. split_group would silently no-op on it (it resolves
            # via _category_by_id) and retrying recurses on identical state
            # forever. Skip the move instead, as the pre-overflow code did.
            return False
        try:
            await split_group(guild, owner, dao)
        except Exception:
            logger.exception("move_channel_to_owner: could not split %s to make room",
                             getattr(owner, "id", "?"))
            return False
        if not is_split(owner, dao):
            # No progress — split_group hit one of its silent no-op paths, and
            # retrying would re-enter with byte-identical state forever.
            return False
        return await move_channel_to_owner(guild, owner, dao, channel, solved)

    source = getattr(channel, "category", None)
    try:
        # Computed INSIDE the lock (not before it), so two concurrent moves
        # into primary can't both read the same stale channel list and race
        # for the same slot.
        async with layout_lock:
            divider_pos = solve_divider_position(primary)
            if divider_pos is not False:
                # Discord's channel-move inserts before the first existing channel
                # whose *own* position >= the requested value, so requesting
                # divider_pos - 1 can exactly collide with an existing channel
                # already sitting right before the divider (e.g. a lone target
                # puzzle with nothing between it and the divider yet) and land the
                # new channel ahead of it instead of after. Request the divider's
                # own position instead: that always inserts immediately before the
                # divider — after everything already above it — without
                # disturbing their order.
                position = (
                    max((ch.position for ch in primary.channels), default=0) + 1
                    if solved else divider_pos
                )
            else:
                position = max((ch.position for ch in primary.channels), default=-1) + 1
            await channel.edit(category=primary, position=position)
    except Exception:
        logger.exception("move_channel_to_owner: could not move channel %s",
                         getattr(channel, "id", "?"))
        return False
    # The legacy-destination branch vacates a source too — reclaim it if it
    # was an overflow category (of either join table).
    if source is not None:
        await cleanup_source_category(guild, source)
    return True


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
            # Re-check everything under the lock: between the fast-path checks
            # above and acquiring the lock, a concurrent move can legally land
            # a channel here (_pick_or_allocate returns any row with room) —
            # deleting then would drop that channel to the guild root, outside
            # the hunt's role-gated permission overwrites. The row can likewise
            # have been deleted by a concurrent cleanup of the same category.
            row = dao.get_by_category(category.id)
            if row is None or row.seq < 1:
                return
            if puzzle_count(category) > 0:
                return
            await category.delete(reason=DELETE_REASON)
            dao.delete(row.id)
    except Exception:
        logger.exception("cleanup_empty_overflow: failed to remove category %s", category.id)


# ----------------------------------------------------------------- consolidation
async def consolidate(guild, owner_id, dao):
    """The inverse of split/overflow, run after a count drops: per role
    (unsolved first — the side normal play keeps changing), reap dangling
    rows, reclaim empty overflow categories, and fold the highest-seq
    overflow back into lower-seq categories when everything fits with
    CONSOLIDATE_MARGIN to spare. Never renames anything (loose seq: Discord's
    name bucket is 2 edits / 10 min per channel, so renumbering surviving
    categories in lockstep would wedge the whole pass), and only ever deletes
    seq>=1 rows — seq=0 is structural. Idempotent; safe to re-trigger.
    """
    async with layout_lock:
        # The guild cache only reflects our own moves after the gateway
        # round-trips, so track what this run adds per target category and
        # count with it — otherwise two successive folds could overfill.
        added = {}

        def _count(cat):
            return puzzle_count(cat) + added.get(cat.id, 0)

        for role in (ROLE_UNSOLVED, ROLE_SOLVED):
            await _consolidate_role(guild, owner_id, dao, role, _count, added)


async def _consolidate_role(guild, owner_id, dao, role, _count, added):
    """One role's reap/reclaim/fold loop. Caller holds layout_lock."""
    while True:
        rows = sorted((r for r in dao.get_for_group(owner_id) if r.role == role),
                      key=lambda r: r.seq)
        # Pass 1: reap dangling rows and reclaim EMPTY overflow categories —
        # unconditionally, no margin gate (cleanup_empty_overflow's shipped
        # semantics). seq=0 rows are never touched.
        acted = False
        for row in rows:
            if row.seq < 1:
                continue
            cat = _category_by_id(guild, row.category_id)
            if cat is None:
                dao.delete(row.id)
                acted = True
            elif _count(cat) == 0:
                try:
                    await cat.delete(reason=DELETE_REASON)
                except Exception:
                    logger.exception("consolidate: could not delete empty category %s",
                                     row.category_id)
                    continue
                dao.delete(row.id)
                acted = True
        if acted:
            continue    # re-read rows and re-evaluate

        # Pass 2: fold the highest-seq overflow into the lower categories if
        # its occupants fit with CONSOLIDATE_MARGIN slots left over.
        if len(rows) < 2:
            return
        top = rows[-1]
        top_cat = _category_by_id(guild, top.category_id)
        lowers = [c for c in
                  (_category_by_id(guild, r.category_id) for r in rows[:-1])
                  if c is not None]
        if top_cat is None or not lowers:
            return
        movers = sorted(
            (ch for ch in top_cat.text_channels if ch.name != SOLVE_DIVIDER),
            key=lambda ch: ch.position)
        free = sum(max(0, CATEGORY_SOFT_LIMIT - _count(c)) for c in lowers)
        if len(movers) > free - CONSOLIDATE_MARGIN:
            return
        try:
            for ch in movers:
                target = next(c for c in lowers if _count(c) < CATEGORY_SOFT_LIMIT)
                position = (max((c2.position for c2 in target.channels), default=-1)
                            + 1 + added.get(target.id, 0))
                await ch.edit(category=target, position=position)
                added[target.id] = added.get(target.id, 0) + 1
            await top_cat.delete(reason=DELETE_REASON)
        except Exception:
            logger.exception("consolidate: fold of category %s failed midway "
                             "(safe: row survives, next run resumes)", top.category_id)
            return
        dao.delete(top.id)
        # Loop: a further fold may now be possible.


async def cleanup_source_category(guild, category):
    """cleanup_empty_overflow for a category whose owning join table the
    caller doesn't know: a vacated source can belong to EITHER table — an
    unassigned puzzle leaving hunt overflow for a round's category, a member
    leaving a round for the hunt's general container, a puzzle deletion.
    Resolves the owner across both tables (lazily imported, same reason as
    _category_claimed) and delegates; untracked categories are left alone.
    """
    if category is None:
        return
    from bot.store import TagGroupCategoryDb, HuntCategoryDb
    for dao in (TagGroupCategoryDb, HuntCategoryDb):
        if dao.get_by_category(category.id) is not None:
            await cleanup_empty_overflow(guild, None, dao, category)
            return


# ----------------------------------------------------------------- teardown
async def delete_group_categories(guild, tag_group, dao, keep_channel_ids=frozenset()):
    """Split state: delete every category the group owns (channels and all,
    except any channel id in *keep_channel_ids* — left in place, since
    Discord doesn't cascade-delete children when their category goes) and
    drop the join rows. Returns True if it handled deletion, False if the
    group is in single-category state (caller should run its legacy path).
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
                if ch.id in keep_channel_ids:
                    continue
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


# ----------------------------------------------------------------- rename
def group_rename_plan(guild, tag_group, new_name, dao):
    """Return the ordered list of ``(discord_object, new_name)`` renames for a
    group rename — GROUP ONLY.

    Renames every category the group owns (the single primary category, or every
    split unsolved/solved/overflow category) and, for a metaless Group, its
    dedicated text channel. NEVER includes the target puzzle's channel. Objects
    Discord can no longer resolve are skipped. Forum layout (category_id is the
    text channel) is handled best-effort but is out of active scope.
    """
    new_cat_base = clean_category_name(new_name)
    new_chan_name = clean_channel_name(new_name)
    plan = []

    rows = group_categories(tag_group, dao)
    if rows:
        for row in rows:
            cat = _category_by_id(guild, row.category_id)
            if cat is not None:
                plan.append((cat, overflow_category_name(new_cat_base, row.role, row.seq)))
    else:
        primary = primary_category(guild, tag_group)
        if primary is not None:
            plan.append((primary, new_cat_base))

    if not tag_group.has_target:
        primary = primary_category(guild, tag_group)
        if primary is not None:
            # Channel-mode Group: the dedicated text channel lives inside the
            # category. Match by the group's stored name — the channel was
            # created from clean_channel_name(tag_group.name), and every rename
            # keeps that invariant. Matching off the category name instead breaks
            # on the create-time collision path, where the category gets a
            # " — <hunt>" suffix the channel never received.
            ch = discord.utils.get(
                primary.text_channels, name=clean_channel_name(tag_group.name))
            if ch is not None:
                plan.append((ch, new_chan_name))
        else:
            # Forum-mode Group: category_id IS the text channel.
            ch = guild.get_channel(tag_group.category_id)
            if ch is not None:
                plan.append((ch, new_chan_name))

    return plan
