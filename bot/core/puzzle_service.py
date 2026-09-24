"""Ctx-free service layer for puzzle operations (M3).

The canonical implementation of each puzzle operation lives here. The Discord
bang-commands and the web dashboard are thin edges that call into this service;
the service does DB + Google Sheets + intrinsic Discord mutations and RAISES on
failure. Presentation (embeds, confirmations) and error-tolerance policy stay at
the edges.
"""
import asyncio
import contextvars
import datetime
import functools
import inspect
import logging
from typing import AbstractSet, Any, Callable

import discord
import pytz
from googleapiclient.errors import HttpError

from bot.store import PuzzleJsonDb, TagGroupJsonDb, HuntJsonDb, GuildSettingsDb, SheetsJsonDb, RollupsJsonDb, EvidenceJsonDb, TagGroupCategoryDb, HuntCategoryDb, CanvasRegionJsonDb  # noqa: F401  # GuildSettingsDb kept as a tests' patch target
from bot.core import category_layout, hunt_archive, media_gc
from bot.core.constants import STATUSES, PRIORITIES, UNSOLVED_STATUS, PARTIAL_STATUS
from bot.store.puzzle_data import PuzzleData, AdditionalSheetData
from bot.store.rollup_data import RollupData
from bot.store.tag_group_data import TagGroupData
from bot.store.hunt_data import HuntData
from bot.core.locks import rollup_lock, puzzle_lock
from bot.core.errors import SheetRefreshError
from bot.core.sheet_tables import (
    EVIDENCE_TABLE_NAME, UNASSIGNED_TABLE_NAME, contribution_table_name, select_table,
)
from bot.utils import config, urls
from bot.core.discord_channels import (
    clean_channel_name, clean_category_name, get_or_create_channel, solve_divider_position, create_forum_post,
    MAX_CHANNELS_PER_GUILD, MAX_CATEGORIES,
    SOLVE_CATEGORY, SOLVE_DIVIDER,
)
from bot.core.reconcile import ReconcileReport, ReconcileAction  # noqa: F401
from bot.core.target_glyph import marked_name
from bot.core.forum_config import desired_tags_for, apply_forum_tags  # noqa: F401  # forum_config kept as a tests' import target
from bot.core.target_extract import select_extract_puzzles

logger = logging.getLogger(__name__)


# Re-entrancy depth for board-push dedupe. A contextvar (not an instance attr) so
# concurrent operations in separate asyncio tasks each get an independent count.
_board_push_depth = contextvars.ContextVar("_board_push_depth", default=0)


def pushes_board(hunt: "str | Callable[[dict[str, Any]], Any]" = "hunt", when=None):
    """Decorator: emit a single ``notifier.board_changed`` after a PuzzleService
    mutation, instead of every method calling the notifier by hand.

    The board push fires when the wrapped coroutine returns normally OR raises
    ``SheetRefreshError`` — that error means the mutation *was* applied and only a
    downstream meta sheet went stale, so the dashboard must still refresh. Any
    other exception (validation error, rollback) propagates without a push.

    ``hunt`` names the parameter holding the HuntData (default ``"hunt"``), or is a
    callable ``(bound_args: dict) -> HuntData`` for methods that derive the hunt
    from a puzzle/group's ``hunt_id``. ``when`` is an optional predicate on the
    return value — when supplied, the push only fires if it is truthy (e.g.
    ``erase_note`` skips the push on a no-op index).

    A re-entrancy guard collapses nested decorated calls to one push (the
    outermost), so e.g. ``create_tag_group`` → ``create_puzzle`` emits one event.
    """
    def decorator(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            token = _board_push_depth.set(_board_push_depth.get() + 1)
            sheet_error = None
            try:
                result = await fn(*args, **kwargs)
            except SheetRefreshError as exc:
                # Mutation applied; sheet(s) stale — push, then re-raise below.
                result, sheet_error = None, exc
            finally:
                depth = _board_push_depth.get()
                _board_push_depth.reset(token)
            if depth == 1 and (when is None or when(result)):
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                self = bound.arguments["self"]
                target = hunt(bound.arguments) if callable(hunt) else bound.arguments.get(hunt)
                await self.notifier.board_changed(target)
            if sheet_error is not None:
                raise sheet_error
            return result

        setattr(wrapper, "_pushes_board", True)  # read via getattr by the board-push tests
        return wrapper

    return decorator


def evidence_cell_content(item) -> str:
    """Text for one evidence datum in a rendered sheet cell.

    Where the item has an image, the dashboard media URL is appended as PLAIN
    TEXT, not =HYPERLINK(...): the roll-up writer uses valueInputOption="RAW"
    on purpose (puzzles_gsheet._values_update) so a solved answer like "=A1" is
    stored literally instead of becoming a live formula that resolves to #REF
    once the puzzle moves to the solved sheet. A formula written under RAW is
    shown verbatim, so it would be noise rather than a link.
    """
    note = item.note or ""
    media_id = getattr(item, "media_id", None)
    if not media_id:
        return note
    base = (config.web_frontend_url or "").rstrip("/")
    if not base:
        return note or "[image]"
    url = f"{base}/api/media/{media_id}"
    return f"{note}\n{url}" if note else url


def evidence_source_label(item, sheet_names, region_names=None) -> str:
    """Display name for an evidence item's source in a rendered table.

    Empty for puzzle-sourced items — contribution_rows uses the member puzzle's
    own name for those — and empty for items with no source at all, which fall
    back to an em dash there. A hunt-level working sheet renders as its name, and
    a canvas region as its label (falling back to its canvas's name).

    `sheet_names` and `region_names` are {id: name} maps resolved once for the
    whole batch; looking each one up here would be an N+1 across a rendered table.
    """
    region_id = getattr(item, "source_region_id", None)
    if region_id:
        # The DAO's join already applies the label-else-canvas-name fallback, so
        # that rule lives in one place rather than being repeated here.
        return (region_names or {}).get(region_id) or f"Region {region_id}"
    sheet_id = getattr(item, "source_sheet_id", None)
    if not sheet_id:
        return ""
    # ON DELETE SET NULL means a missing sheet should be impossible, but a row
    # read in the gap would otherwise render a blank Source cell.
    return sheet_names.get(sheet_id) or f"Sheet {sheet_id}"


def resolve_sheet_names(sheet_ids):
    """Return a {sheet_id: name} map for hunt-level working sheets, in one query."""
    ids = {i for i in sheet_ids if i}
    if not ids:
        return {}
    return SheetsJsonDb.get_names_by_ids(ids)


def resolve_region_names(region_ids):
    """Return a {region_id: label-or-canvas-name} map for canvas regions, in one
    query. Mirrors resolve_sheet_names; the label fallback lives in the DAO."""
    ids = {i for i in region_ids if i}
    if not ids:
        return {}
    return CanvasRegionJsonDb.get_names_by_ids(ids)


def resolve_puzzle_names(puzzle_ids):
    """Return a {puzzle_id: name} map with a 'Puzzle {id}' fallback for unknown ids.

    Does a single batched query for all ids (get_by_attr per id was N+1).
    """
    found = PuzzleJsonDb.get_names_by_ids(puzzle_ids)
    return {pid: found.get(pid, f"Puzzle {pid}") for pid in puzzle_ids}


def is_metameta(tag_group, all_tag_groups: list, puzzles_in_group: list) -> bool:
    """Return True if the group's feeders are themselves meta groups (have targets).

    A group is metameta when its feeding members are target puzzles of other groups
    — i.e. the group aggregates meta answers, not feeder answers.
    """
    target_puzzle_ids = {g.target_puzzle_id for g in all_tag_groups if g.target_puzzle_id}
    return any(
        p.has_edge(tag_group.id, "feeds") and p.id in target_puzzle_ids
        for p in puzzles_in_group
    )


TYPE_DEFAULTS = {
    "Metapuzzle": {"default_edge": "feeds",   "default_source_mode": "round"},
    "Capstone":   {"default_edge": "informs", "default_source_mode": "round"},
    "Runaround":  {"default_edge": "feeds",   "default_source_mode": "subtree"},
    "Group":      {"default_edge": "none",    "default_source_mode": "round"},
}

# Stored tag_group.type values.
GROUP_TYPES = tuple(TYPE_DEFAULTS)

# Create-time input labels mapped to stored types.
GROUP_TYPE_INPUT_ALIASES = {"Round": "Metapuzzle"}

# Ordered create-time type labels offered by the create-meta UI — the single
# source of truth for the dashboard dropdown (exposed via the hunt-detail API).
# Derived, not hand-listed: input aliases first (e.g. "Round"), then the stored
# GROUP_TYPES, so a new type or alias surfaces in the dropdown automatically.
CREATE_META_TYPES = tuple(GROUP_TYPE_INPUT_ALIASES) + GROUP_TYPES

# The source_mode a freshly created group has before anything overrides it --
# the column default. Only a type whose TYPE_DEFAULTS entry differs from this
# needs a post-create set_source_mode call.
GROUP_DEFAULT_SOURCE_MODE = "round"

# Create-time target-puzzle naming, keyed on the INPUT LABEL rather than the
# stored type. "Round" and "Metapuzzle" both store as "Metapuzzle" but name
# their target differently, so TYPE_DEFAULTS -- keyed by stored type -- cannot
# express this. That is why the review's "key it off TYPE_DEFAULTS" does not
# work as stated (M30).
CREATE_META_NAME_SUFFIX = {"Round": " - meta"}


def meta_puzzle_name_for(input_label, name):
    """Name for the target puzzle of a group created as `input_label`.

    Returns False for a type that has no target puzzle, matching
    create_tag_group's `meta_puzzle_name` contract.
    """
    stored = GROUP_TYPE_INPUT_ALIASES.get(input_label, input_label)
    if stored == "Group":
        return False
    return name + CREATE_META_NAME_SUFFIX.get(input_label, "")


def default_source_mode_for(round_type):
    """The source_mode this stored type wants, or None if it wants the default."""
    mode = TYPE_DEFAULTS.get(round_type, {}).get("default_source_mode")
    return mode if mode and mode != GROUP_DEFAULT_SOURCE_MODE else None


def default_edge_for_group(group) -> str:
    """The edge a newly-added puzzle should get to maintain the group's status quo:
    a bare group with no target puzzle pools with 'none'; a group whose target is a
    capstone informs; any other fed meta (meta / metameta / runaround) feeds."""
    if not group.target_puzzle_id:
        return "none"
    return TYPE_DEFAULTS.get(group.type, {}).get("default_edge", "feeds")


class PuzzleService:
    def __init__(self, bot, notifier=None):
        self.bot = bot
        if notifier is None:
            from bot.core.notifier import NullNotifier
            notifier = NullNotifier()
        self.notifier = notifier

        # Deferred rename state (moved from Puzzles cog; see Phase 6).
        self._pending_renames: dict[int, str] = {}
        self._rename_tasks: set = set()

    async def safe_rename(self, channel, name, ctx=None):
        """Rename a Discord channel, coalescing rapid renames under rate limit.

        Moved from Puzzles cog (Phase 6). `ctx` may be None (web callers); no user-
        facing message is sent in that case."""
        try:
            await channel.edit(name=name)
            return True
        except discord.RateLimited as exc:
            already_pending = channel.id in self._pending_renames
            self._pending_renames[channel.id] = name
            if ctx is not None:
                await ctx.send(
                    f"⏳ Discord is rate-limiting renames on this channel; "
                    f"I'll apply the new name in about {round(exc.retry_after)}s."
                )
            if not already_pending:
                task = asyncio.create_task(self._deferred_rename(channel, exc.retry_after))
                self._rename_tasks.add(task)
                task.add_done_callback(self._rename_tasks.discard)
            return False

    async def _deferred_rename(self, channel, delay):
        """Wait out the rename bucket, then apply the latest requested name,
        coalescing any further renames queued while we were waiting."""
        attempts = 0
        while attempts < 6:
            await asyncio.sleep(delay + 1)
            name = self._pending_renames.get(channel.id)
            if name is None:
                return
            attempts += 1
            try:
                await channel.edit(name=name)
            except discord.RateLimited as exc:
                delay = exc.retry_after
                continue
            except discord.HTTPException:
                logger.exception("Deferred rename of channel %s failed", channel.id)
                self._pending_renames.pop(channel.id, None)
                return
            # Applied. If a newer name was queued meanwhile, loop to apply it too.
            if self._pending_renames.get(channel.id) == name:
                self._pending_renames.pop(channel.id, None)
                return
            delay = 0
        logger.warning("Gave up deferred rename of channel %s after repeated rate limits", channel.id)
        self._pending_renames.pop(channel.id, None)

    async def _has_contribution_table(self, gsheet_cog, page_id):
        """True when the bot's OWN contribution Table is on `page_id`'s tab.

        The licence for demote_tab_to_puzzle, which deletes the tab's four
        leftmost columns. "Any native Table is present" is NOT that licence: a
        solver may add a table of their own to a tab the bot never promoted (a
        minimeta made but never rendered; a convert_to_meta whose
        promote_tab_to_group failed after is_target was committed), and demoting
        then destroys columns A-D of their grid with no undo. Matching is by the
        name the bot gives its own Table — see bot/core/sheet_tables.py.
        """
        sheets = (await gsheet_cog.sheet_list(gsheet_cog.get_spreadsheet_id()))["sheets"]
        return any(
            select_table(s.get("tables"), contribution_table_name(page_id)) is not None
            for s in sheets
            if s["properties"]["sheetId"] == int(page_id)
        )

    def _gsheet_cog(self, hunt):
        """Resolve the GoogleSheets cog and point it at this hunt's spreadsheets.
        Returns None if the cog isn't loaded (e.g. in tests)."""
        cog = self.bot.get_cog("GoogleSheets")
        if cog is not None and hunt is not None:
            cog.set_spreadsheet_id(hunt.google_sheet_id)
            cog.set_solved_sheet_id(hunt.solved_sheet_id)
        return cog

    # --- forum tag helpers ---

    async def _update_forum_tags(self, guild, hunt, puzzle):
        """Best-effort: update forum post tags to match desired state.

        Computes metameta/minimeta from the live DB state and applies the
        correct structural + solved tag(s).  No-op when hunt is not in
        forum mode or guild/puzzle.channel_id are missing.
        """
        try:
            if getattr(hunt, "layout_mode", None) != "forum":
                return
            if guild is None or not getattr(puzzle, "channel_id", 0):
                return

            thread = guild.get_channel_or_thread(puzzle.channel_id)
            if thread is None:
                return

            # Resolve the owning groups; pick the primary (first) for tagging.
            group_ids = list(getattr(puzzle, "tags", []) or [])
            primary_group: TagGroupData | None = None
            if not group_ids:
                # Unassigned feeder: only "solved" tag can apply
                desired = set()
                if getattr(puzzle, "solved", False):
                    desired.add("solved")
            else:
                primary_group = TagGroupJsonDb.get_by_attr(id=group_ids[0])
                if primary_group is None:
                    return  # orphaned tag reference
                all_groups = TagGroupJsonDb.get_all(hunt_id=hunt.id)
                puzzles_in_group = PuzzleJsonDb.get_all_from_tag_group(primary_group.id)
                metameta = is_metameta(primary_group, all_groups, puzzles_in_group)
                minimeta = bool(self.get_minis(puzzle.id))
                desired = desired_tags_for(primary_group, puzzle,
                                           metameta=metameta,
                                           minimeta=minimeta)

            # Resolve the forum channel (parent container of the thread).
            cid = getattr(thread, "parent_id", None)
            if cid is None and primary_group is not None:
                cid = getattr(primary_group, "category_id", None)
            if cid is None:
                return
            forum = guild.get_channel(cid)
            if forum is None:
                return

            await apply_forum_tags(thread, forum, desired)
        except Exception:
            logger.exception("_update_forum_tags failed for puzzle %s", puzzle.id)

    def _is_metameta_group(self, hunt, group):
        """Whether *group*'s feeders include another group's target (a sub-meta)."""
        all_groups = TagGroupJsonDb.get_all(hunt_id=hunt.id)
        members = PuzzleJsonDb.get_all_from_tag_group(group.id)
        return is_metameta(group, all_groups, members)

    def _target_kind(self, hunt, puzzle, group=None):
        """Glyph kind for *puzzle* as a target ('runaround'/'metameta'/'meta'/
        'capstone'), or None if not a target. *group* may be supplied by the create
        path where target_puzzle_id isn't committed yet."""
        if group is None:
            if not getattr(puzzle, "is_target", False):
                return None
            group = TagGroupJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
            if group is None:
                return None
        if group.type == "Runaround":
            return "runaround"
        if self._is_metameta_group(hunt, group):
            return "metameta"
        if group.type == "Metapuzzle":
            return "meta"
        if group.type == "Capstone":
            return "capstone"
        return None

    def desired_channel_name(self, guild, hunt, puzzle, group=None):
        """Canonical Discord channel name: ⭐ (if this is a category's OWN target) +
        kind glyph + clean name; bare clean name for a feeder."""
        kind = self._target_kind(hunt, puzzle, group=group)
        clean = clean_channel_name(puzzle.name)
        if kind is None:
            return clean
        owning = group if group is not None else TagGroupJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
        is_primary = owning is not None and category_layout.primary_category(guild, owning) is not None
        return marked_name(kind, clean, is_primary)

    def _desired_target_channel_name(self, guild, hunt, group):
        """Glyphed channel name a group's target puzzle should carry (no puzzle
        object needed — the kind is derived from the group)."""
        kind = self._target_kind(hunt, None, group=group)
        is_primary = category_layout.primary_category(guild, group) is not None
        return marked_name(kind, clean_channel_name(group.name), is_primary)

    async def _refresh_channel_glyph(self, guild, hunt, puzzle):
        """Rename *puzzle*'s channel to its desired glyphed name if it differs."""
        if guild is None or not getattr(puzzle, "channel_id", 0):
            return
        channel = guild.get_channel_or_thread(puzzle.channel_id)
        if channel is None:
            return
        want = self.desired_channel_name(guild, hunt, puzzle)
        if channel.name != want:
            await self.safe_rename(channel, want)

    async def backfill_glyphs(self, guild, hunt):
        """Rename every target channel in *hunt* to its desired glyphed name.
        Idempotent; returns the count of channels actually renamed."""
        renamed = 0
        for group in TagGroupJsonDb.get_all(hunt_id=hunt.id):
            if not group.target_puzzle_id:
                continue
            puzzle = PuzzleJsonDb.get_by_attr(id=group.target_puzzle_id)
            if puzzle is None or not getattr(puzzle, "channel_id", 0):
                continue
            channel = guild.get_channel_or_thread(puzzle.channel_id)
            if channel is None:
                continue
            want = self.desired_channel_name(guild, hunt, puzzle, group=group)
            if channel.name != want:
                try:
                    await self.safe_rename(channel, want)
                    renamed += 1
                except Exception:
                    logger.warning(
                        "backfill_glyphs: failed to rename channel %s for puzzle %s",
                        puzzle.channel_id, puzzle.id, exc_info=True)
        return renamed

    # --- !reconcile ---

    def _home_category_for_puzzle(self, guild, puzzle):
        """Discord category the puzzle's single channel belongs in = tags[0]'s group.
        Returns (category_or_None, home_group_or_None).

        Named for its argument because category_layout._home_category answers a
        different question (a GROUP's category, returned bare rather than as a
        tuple) and this file calls both, four lines apart.
        """
        if not puzzle.tags:
            return None, None
        home_group = TagGroupJsonDb.get_by_attr(id=puzzle.tags[0])
        if home_group is None or not home_group.category_id:
            return None, home_group
        # Delegate rather than re-derive: for an in-place-converted / channel-mode
        # group, category_id names a CHANNEL, and a bare guild.categories lookup
        # returns None -- after which _reconcile_channel recreates the puzzle's
        # channel at guild ROOT, outside the hunt's role-gating overwrites (M3).
        # category_layout._home_category has carried that fallback all along, and
        # this service already calls it in three other places.
        return category_layout._home_category(guild, home_group), home_group

    async def _reconcile_channel(self, guild, hunt, puzzle, report, *, apply, hunt_category_ids=()):
        """Make the puzzle's Discord channel match the DB. Returns the list of
        actions taken/needed. Resolution order: keep+fix existing > relink named >
        create. Welcome is reposted ONLY when a brand-new channel is created.

        ``hunt_category_ids`` scopes step 2's relink search to this hunt's own
        round/group categories, so a same-named channel belonging to a different
        hunt (or a different puzzle) in the same guild can't be hijacked."""
        # Mini-puzzles live as threads inside their minimeta's channel (see
        # create_mini) and are deliberately untagged. Reconcile has no
        # thread-creation path, so a mini here would be wrongly promoted to a
        # standalone text channel — skip channel reconciliation for them.
        if puzzle.parent_puzzle_id is not None:
            return []
        actions = []
        category, home_group = self._home_category_for_puzzle(guild, puzzle)
        want_name = self.desired_channel_name(guild, hunt, puzzle)

        # 1. Stored id resolves to a live channel: fix category/name drift only.
        live = guild.get_channel(puzzle.channel_id) if puzzle.channel_id else None
        if live is not None:
            if category is not None and getattr(live, "category", None) is not category:
                actions.append(ReconcileAction("channel_move", puzzle.name,
                                               f"-> category {category.id}"))
                if apply:
                    await live.edit(category=category)
            if live.name != want_name:
                actions.append(ReconcileAction("channel_rename", puzzle.name,
                                               f"{live.name} -> {want_name}"))
                if apply:
                    await live.edit(name=want_name)
            return actions

        # 2. A channel with the right name already exists in one of this hunt's
        # own categories: relink + move. Scoped to hunt_category_ids so a
        # same-named channel in another hunt/round can't be adopted.
        existing = discord.utils.get(
            (c for c in guild.channels if getattr(c.category, "id", None) in hunt_category_ids),
            name=want_name,
        )
        if existing is not None:
            actions.append(ReconcileAction("channel_relink", puzzle.name,
                                           f"-> #{existing.id}"))
            if apply:
                if category is not None and getattr(existing, "category", None) is not category:
                    await existing.edit(category=category)
                puzzle.channel_id = existing.id
                puzzle.channel_name = existing.name
                puzzle.channel_mention = existing.mention
                PuzzleJsonDb.commit(puzzle)
            return actions

        # 3. Nothing found: create + welcome.
        actions.append(ReconcileAction("channel_create", puzzle.name, "no channel found"))
        if apply:
            text_channel, created = await get_or_create_channel(
                guild=guild, category=category, channel_name=want_name, channel_type="text",
            )
            if created and category is not None:
                divider = solve_divider_position(category)
                if divider is not False:
                    await text_channel.edit(position=divider)
            puzzle.channel_id = text_channel.id
            puzzle.channel_name = text_channel.name
            puzzle.channel_mention = text_channel.mention
            PuzzleJsonDb.commit(puzzle)
            if created and self.notifier is not None:
                await self.notifier.welcome(
                    guild, hunt, tag_group=home_group, puzzle=puzzle, channel=text_channel,
                )
        return actions

    async def _fetch_present_tab_ids(self, hunt):
        """Fetch each of the hunt's spreadsheets' tab ids ONCE, for _reconcile_tabs
        to use as an O(1) membership test across an entire reconcile() run instead
        of one full spreadsheets().get per puzzle (M12). Keyed by spreadsheet id,
        because a puzzle's tab lives on either the main spreadsheet or the
        archive/solved spreadsheet (puzzle.on_solved_sheet). Returns None when
        there is no GoogleSheets cog.

        Staleness within the run is fine: recreating puzzle A's missing tab does
        not change whether puzzle B's distinct page id was present in the
        snapshot -- each puzzle is judged only against its own id. Do NOT refresh
        the snapshot mid-loop. Raises on a fetch failure; the caller decides how
        to degrade.
        """
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return None
        present_ids = {}
        main_id = gsheet_cog.get_spreadsheet_id()
        sheets = (await gsheet_cog.sheet_list(main_id))["sheets"]
        present_ids[main_id] = frozenset(
            str(s["properties"]["sheetId"]) for s in sheets)
        solved_id = gsheet_cog.get_solved_sheet_id()
        if solved_id:
            sheets = (await gsheet_cog.sheet_list(solved_id))["sheets"]
            present_ids[solved_id] = frozenset(
                str(s["properties"]["sheetId"]) for s in sheets)
        return present_ids

    async def _reconcile_tabs(self, hunt, puzzle, *, apply, present_ids=None):
        """Ensure the puzzle's Sheets tab(s) exist. Recreate a missing tab and write
        the fresh sheetId back to the DB. No-op without a GoogleSheets cog.

        present_ids, when given, is the {spreadsheet_id: frozenset(str(page_id))}
        snapshot from _fetch_present_tab_ids -- a set-membership test replacing
        the per-call get_page_name_by_id scan (M12). Leave it None (the default)
        to keep the original per-puzzle lookup, e.g. for a caller reconciling a
        single puzzle outside of a full reconcile() run.
        """
        actions = []
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return actions
        spreadsheet = (gsheet_cog.get_solved_sheet_id() if puzzle.on_solved_sheet
                       else gsheet_cog.get_spreadsheet_id())

        async def _tab_present(page_id):
            if not page_id:
                return False
            if present_ids is not None:
                return str(page_id) in present_ids.get(spreadsheet, frozenset())
            return await gsheet_cog.get_page_name_by_id(page_id, spreadsheet) is not None

        if not await _tab_present(puzzle.google_page_id):
            actions.append(ReconcileAction("tab_create", puzzle.name, "main tab missing"))
            if apply:
                existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
                insert_idx = await gsheet_cog.compute_insert_index(
                    getattr(puzzle, 'is_target', False), existing)
                await gsheet_cog.add_new_puzzle_sheet(puzzle, insert_idx)
                await gsheet_cog.update_puzzle_info(puzzle)
                PuzzleJsonDb.commit(puzzle)
        return actions

    async def reconcile(self, guild, hunt, *, apply):
        """Make Discord + Sheets match the DB for an entire hunt. Returns a
        ReconcileReport. Never deletes — orphans are only collected. Each puzzle is
        isolated: one failure is recorded and the run continues."""
        report = ReconcileReport()
        puzzles = PuzzleJsonDb.get_all_from_hunt(hunt.id)
        rounds = TagGroupJsonDb.get_all(hunt.id)
        hunt_category_ids = self._hunt_category_ids(hunt, rounds)

        try:
            present_ids = await self._fetch_present_tab_ids(hunt)
        except Exception:  # noqa: BLE001
            # A slow reconcile beats one that refuses to run: fall back to the
            # per-puzzle get_page_name_by_id lookup inside _reconcile_tabs.
            logger.exception(
                "reconcile: could not pre-fetch tab snapshot; falling back to "
                "per-puzzle tab lookups")
            present_ids = None

        for puzzle in puzzles:
            try:
                for action in await self._reconcile_channel(
                        guild, hunt, puzzle, report, apply=apply,
                        hunt_category_ids=hunt_category_ids):
                    report.actions.append(action)
                for action in await self._reconcile_tabs(
                        hunt, puzzle, apply=apply, present_ids=present_ids):
                    report.actions.append(action)
            except Exception as error:  # noqa: BLE001
                logger.exception("reconcile: failed for puzzle %s", puzzle.name)
                report.add_failure(puzzle.name, error)

        for tag_group in rounds:
            if tag_group.category_id and discord.utils.get(
                    guild.categories, id=tag_group.category_id) is None:
                report.add("round_category_broken",
                           getattr(tag_group, "group_code", None) or str(tag_group.id),
                           f"category {tag_group.category_id} missing")

        self._collect_orphan_channels(
            guild, hunt, puzzles, report, hunt_category_ids)
        await self._collect_orphan_tabs(hunt, puzzles, report)
        return report

    @staticmethod
    def _hunt_category_ids(hunt, rounds):
        """Every Discord category id this hunt owns, not just each round's primary
        `category_id`: a split/overflowed round parks channels in the extra
        categories recorded in `tag_group_category`, and channel-home groups park
        theirs in the hunt's own general category and its `hunt_category` overflow
        rows. Scoping reconcile to the primary ids alone left relink unable to see
        a channel that had merely overflowed, so step 3 created a DUPLICATE at
        guild root — outside the hunt's gating overwrites (M6)."""
        ids = {r.category_id for r in rounds if r.category_id}
        for tag_group in rounds:
            ids.update(row.category_id
                       for row in TagGroupCategoryDb.get_for_group(tag_group.id))
        ids.add(getattr(hunt, "category_id", None))
        ids.update(row.category_id for row in HuntCategoryDb.get_for_group(hunt.id))
        return frozenset(cid for cid in ids if cid)

    def _collect_orphan_channels(self, guild, hunt, puzzles, report, category_ids):
        valid_ids = {p.channel_id for p in puzzles if p.channel_id}
        # Widening the scan to the hunt's general category (M6) brings the hunt's
        # OWN channel into range. It is not a puzzle channel, and orphans are what
        # `!reconcile apply-orphans` deletes.
        if getattr(hunt, "channel_id", None):
            valid_ids.add(hunt.channel_id)
        for category in guild.categories:
            if category.id not in category_ids:
                continue
            for channel in getattr(category, "channels", []):
                # discord.ChannelType is an enum: ChannelType.text == "text" is
                # False, so this must compare against the enum member itself.
                if getattr(channel, "type", discord.ChannelType.text) is not \
                        discord.ChannelType.text:
                    continue
                if channel.id in valid_ids:
                    continue
                if "———" in getattr(channel, "name", ""):
                    continue
                report.add_orphan_channel(channel.id, channel.name)

    # Structural tabs that live in the spreadsheet template (or are otherwise
    # not tracked by a page id) and must never be treated as orphan puzzle tabs.
    RESERVED_TAB_TITLES = frozenset({
        "REFERENCE", "Unassigned Feeders", "Unassigned Puzzles", "Evidence",
        "Tab template", "Meta Tab template", "Overview",
    })

    async def _collect_orphan_tabs(self, hunt, puzzles, report):
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return
        spreadsheet = gsheet_cog.get_spreadsheet_id()
        valid = {str(p.google_page_id) for p in puzzles if p.google_page_id and not p.on_solved_sheet}
        # Additional per-puzzle sheets are real puzzle tabs, not orphans.
        for puzzle in puzzles:
            for sheet in getattr(puzzle, "additional_sheets", None) or []:
                if sheet.google_page_id:
                    valid.add(str(sheet.google_page_id))
        # Bot-managed structural tabs tracked on the hunt are never orphans.
        for page_id in (getattr(hunt, "unassigned_sheet_page_id", None),
                        getattr(hunt, "evidence_sheet_page_id", None)):
            if page_id is not None:
                valid.add(str(page_id))
        try:
            sheets = (await gsheet_cog.sheet_list(spreadsheet))["sheets"]
        except Exception:  # noqa: BLE001
            logger.exception("reconcile: could not list tabs for orphan scan")
            return
        for sheet in sheets:
            props = sheet.get("properties", {})
            if props.get("hidden"):
                continue
            title = props.get("title", "")
            # Skip template/structural tabs not tracked by a page id (e.g. REFERENCE).
            if title in self.RESERVED_TAB_TITLES:
                continue
            sheet_id = props.get("sheetId")
            if str(sheet_id) in valid:
                continue
            report.add_orphan_tab(sheet_id, title or str(sheet_id))

    async def delete_orphans(self, guild, hunt, report):
        """Delete the orphan channels/tabs recorded in `report`. Tolerant of
        already-missing items. Returns (deleted_count, failure_count)."""
        deleted = 0
        failures = 0
        for channel_id, name in report.orphan_channels:
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            try:
                await channel.delete(reason="reconcile: orphan channel (no DB puzzle)")
                deleted += 1
            except Exception:  # noqa: BLE001
                logger.exception("reconcile: failed to delete orphan channel %s", channel_id)
                failures += 1

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            spreadsheet = gsheet_cog.get_spreadsheet_id()
            for sheet_id, title in report.orphan_tabs:
                try:
                    await gsheet_cog.delete_sheet_tab(sheet_id, spreadsheet)
                    deleted += 1
                except Exception:  # noqa: BLE001
                    logger.exception("reconcile: failed to delete orphan tab %s", sheet_id)
                    failures += 1
        return deleted, failures

    async def refresh_target(self, hunt, tag_group):
        """Rebuild a meta (or meta-meta) roll-up sheet, tracking the outcome on the
        group's `rollups` record (find-or-create). On success: last_ok=1, error
        cleared. On a Sheets error: last_ok=0 + last_error persisted, then RE-RAISES
        so the caller can surface a toast — the durable badge is recorded either way.

        Serialized per group via rollup_lock so only one writer touches a given meta
        sheet at a time; the DB read happens INSIDE the lock so each holder renders
        post-commit state (no lost update against a concurrent !rebuild)."""
        if hunt is None or tag_group is None or not tag_group.target_puzzle_id:
            return
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            # No sheets in play (tests / cog unloaded): nothing to render or badge.
            return

        async with rollup_lock(tag_group.id):
            metapuzzle = PuzzleJsonDb.get_by_attr(id=tag_group.target_puzzle_id)
            if metapuzzle is None:
                return
            rollup = self._find_or_create_rollup(tag_group)
            try:
                await self._render_rollup(gsheet_cog, tag_group, rollup, metapuzzle)
                await self._record_rollup_tab(gsheet_cog, rollup, metapuzzle)
                self._mark_rollup(rollup, ok=True)
            except Exception as exc:
                self._mark_rollup(rollup, ok=False, error=str(exc))
                raise

    def _find_or_create_rollup(self, tag_group):
        """Return the rollup row for this tag group, creating an unsaved one (with
        the current-meta defaults) if none exists yet. Not committed until
        _mark_rollup writes the refresh outcome."""
        rollup = RollupsJsonDb.get_by_attr(tag_group_id=tag_group.id)
        if rollup is None:
            rollup = RollupData(hunt_id=tag_group.hunt_id, tag_group_id=tag_group.id)
        return rollup

    async def set_source_mode(self, hunt, target_group, mode):
        """Set the aggregation source_mode for a tag group, then refresh its sheet.

        Validates *mode* against SOURCE_MODES, creates the rollup row if needed,
        commits the change, then calls refresh_target.  Raises ValueError for an
        unrecognised mode.  Any refresh failure propagates to the caller.
        """
        from bot.core.constants import SOURCE_MODES
        if mode not in SOURCE_MODES:
            raise ValueError(
                f"source_mode must be one of: {', '.join(SOURCE_MODES)}"
            )
        rollup = self._find_or_create_rollup(target_group)
        rollup.source_mode = mode
        RollupsJsonDb.commit(rollup)
        await self.refresh_target(hunt, target_group)

    async def refresh_minimeta(self, hunt, minimeta):
        """Roll a minimeta's mini-puzzles (its parent_puzzle_id children) up onto the
        minimeta's OWN sheet, tracking the outcome on a puzzle-keyed `rollups` record.

        This is the puzzle-keyed counterpart to refresh_meta: the rollup engine is
        otherwise round-keyed, but a minimeta is a puzzle, so membership is its children
        (not round feeders) and the write target is the minimeta's own tab. Serialized
        on a key namespace disjoint from the round locks. Renders ALL minis (answers
        fill in as they solve) and mirrors refresh_meta's badge bookkeeping + re-raise.
        """
        if hunt is None or minimeta is None:
            return
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return

        async with rollup_lock(("minimeta", minimeta.id)):
            members = self.get_minis(minimeta.id)
            rollup = self._find_or_create_rollup_for_puzzle(hunt, minimeta)
            try:
                await gsheet_cog.render_minimeta_contribution(minimeta, members)
                await self._record_rollup_tab(gsheet_cog, rollup, minimeta)
                self._mark_rollup(rollup, ok=True)
            except Exception as exc:
                self._mark_rollup(rollup, ok=False, error=str(exc))
                raise

    def _find_or_create_rollup_for_puzzle(self, hunt, minimeta):
        """The rollup row for a minimeta's puzzle-keyed roll-up (find-or-create).
        round_id stays NULL; identity is target_puzzle_id."""
        rollup = RollupsJsonDb.get_by_attr(target_puzzle_id=minimeta.id)
        if rollup is None:
            rollup = RollupData(hunt_id=hunt.id, target_puzzle_id=minimeta.id)
        return rollup

    async def select_rollup_members(self, tag_group, rollup) -> list:
        """Select feeder membership by rollup.source_mode, returning the ordered
        list[PuzzleData] with NO Google-Sheet side-effects.

        Falsy/missing source_mode defaults to 'round'. Shared by _render_rollup so
        the sheet writer and any read path agree on membership."""
        mode = rollup.source_mode or "round"
        feeders = PuzzleJsonDb.get_all_from_tag_group(tag_group.id)

        if mode == "subtree":
            return await self.collect_subtree_puzzles(tag_group)
        elif mode == "explicit":
            return [
                p for p in feeders
                if p.has_edge(tag_group.id, "none")
            ]
        else:
            # 'round' (default): the group's contributing feeders. 'feeds' is the
            # universal column default (migration 012) and means "this round's
            # feeders" — which, since a Capstone re-edges its feeders
            # feeds -> informs, is the feeds + informs set. A deliberately
            # different edge_type_filter narrows to that single edge. 'none'
            # (pooled) members are excluded — they belong to 'explicit' rollups.
            filt = rollup.edge_type_filter
            contributing = ("feeds", "informs") if filt in (None, "", "feeds") else (filt,)
            return [
                p for p in feeders
                if (p.edges_for(tag_group.id) or {"feeds"}) & set(contributing)
            ]

    async def _render_rollup(self, gsheet_cog, tag_group, rollup, metapuzzle):
        """Render the unified contribution table for tag_group's target.

        Selects feeder membership via select_rollup_members and delegates to the
        cog's render_contribution (contribution_rows -> reconcile_table). Evidence
        routed to this target (informs content) is fetched and passed through so
        informs members render one content row per datum."""
        members = await self.select_rollup_members(tag_group, rollup)
        raw_evidence = list(
            EvidenceJsonDb.get_all(rollup.hunt_id, target_puzzle_id=metapuzzle.id))
        sheet_names = resolve_sheet_names(
            getattr(e, "source_sheet_id", None) for e in raw_evidence)
        region_names = resolve_region_names(
            getattr(e, "source_region_id", None) for e in raw_evidence)
        evidence = [
            {
                "id": e.id,
                "source_puzzle_id": e.source_puzzle_id,
                "source_label": evidence_source_label(e, sheet_names, region_names),
                "content": evidence_cell_content(e),
            }
            for e in raw_evidence
        ]
        await gsheet_cog.render_contribution(metapuzzle, tag_group.id, members, evidence=evidence)

    def _mark_rollup(self, rollup, ok, error=None):
        """Persist the refresh outcome (badge state) on the rollup row."""
        rollup.last_ok = 1 if ok else 0
        rollup.last_error = None if ok else (error or "")[:1000]
        rollup.updated_at = datetime.datetime.now(tz=pytz.UTC)
        RollupsJsonDb.commit(rollup)

    async def _record_rollup_tab(self, gsheet_cog, rollup, metapuzzle):
        """Record the trusted tab pointer (the meta puzzle's google_page_id) and,
        best-effort, a stable developer-metadata id as substrate for M7. A metadata
        API failure NEVER fails the refresh — M5 renders via google_page_id."""
        page_id = metapuzzle.google_page_id
        rollup.tab_page_id = int(page_id) if page_id not in (None, "") else None
        # A rollup is keyed by EITHER a round (meta rounds) or a target puzzle (minimetas).
        ident = rollup.tag_group_id if rollup.tag_group_id else f"puzzle:{rollup.target_puzzle_id}"
        if rollup.metadata_id is None and rollup.tab_page_id is not None:
            try:
                _, rollup.metadata_id = await gsheet_cog.stamp_rollup_metadata(
                    metapuzzle, f"rollup:{ident}")
            except Exception:
                logger.exception(
                    "refresh_meta: developer-metadata stamp failed for %s "
                    "(non-fatal; google_page_id remains the pointer)", ident)

    async def refresh_targets(self, hunt, tag_groups):
        """Refresh several tag groups with FAILURE ISOLATION: every group is
        attempted (a failure on one must not skip the others), each failure is
        already badged by refresh_target, and the failed group ids are returned for the
        caller to surface. None groups are skipped. This is the shared isolation path
        for solve / retag / the bang solution-edits / !rebuild."""
        failed = []
        for tag_group in tag_groups:
            if tag_group is None:
                continue
            try:
                await self.refresh_target(hunt, tag_group)
            except Exception:
                failed.append(tag_group.id)
        return failed

    @pushes_board(hunt=lambda a: HuntJsonDb.get_by_attr(id=a["puzzle"].hunt_id))
    async def tag_with_edge(self, puzzle, group, edge):
        """Upsert an edge for (puzzle, group) in the DB, then keep in-memory
        ``puzzle.tags`` + ``puzzle.tag_edges`` in sync.

        Mirrors add_to_meta's path but with typed edges.
        """
        PuzzleJsonDb.tag_puzzle_with_edge(puzzle.id, group.id, edge)
        # Pooled exclusivity: 'none' and contributing edges can't coexist.
        if edge in ("feeds", "informs"):
            self._drop_local_edge(puzzle, group, "none")
        elif edge == "none":
            for e in ("feeds", "informs"):
                self._drop_local_edge(puzzle, group, e)
        # A group's own target self-edge is mutually exclusive with a deliberate
        # self-feed/self-inform (#40): enabling a self-feed must drop 'target', and
        # turning the self-feed back off ('target') must drop feeds/informs — else
        # the puzzle ends up with both edges and the dashboard's "No self-feed"
        # off-switch can never re-engage (it reads as already on) or is a no-op.
        if puzzle.id == group.target_puzzle_id:
            if edge in ("feeds", "informs"):
                self._drop_local_edge(puzzle, group, "target")
            elif edge == "target":
                for e in ("feeds", "informs"):
                    self._drop_local_edge(puzzle, group, e)
        puzzle.add_edge(group.id, edge)
        PuzzleJsonDb.commit(puzzle)
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        failed = await self.refresh_targets(hunt, [group])
        if failed:
            raise SheetRefreshError(failed_group_ids=failed)

    def _drop_local_edge(self, puzzle, group, edge):
        """Remove one edge in DB + memory (pooled-exclusivity helper)."""
        if puzzle.has_edge(group.id, edge):
            PuzzleJsonDb.delete_tag_edge(puzzle.id, group.id, edge)
            puzzle.remove_edge(group.id, edge)

    async def tag_edge_only(self, puzzle, group, edge, guild=None):
        """Tag *puzzle* into *group* with *edge*, removing any other edges
        of the **same type** (other groups carrying *edge* are swept).

        If ``edge == "feeds"`` and guild is provided, also move the channel
        to the group's category.  Delegates the actual tagging to
        :meth:`tag_with_edge`.
        """
        # Sweep other groups that carry the same edge type
        other_groups = [] 
        for gid in list(puzzle.tags):
            if gid != group.id and edge in puzzle.edges_for(gid):
                other_group = TagGroupJsonDb.get_by_attr(id=gid)
                if other_group is not None:
                    await self.untag_from(puzzle, other_group, edge=edge)
                    other_groups.append(other_group)

        # Move channel for feeds edges (backward compat with move_to_meta). Routed
        # through move_channel_to_owner so a full target category auto-splits
        # instead of the channel silently staying put (or erroring) — same
        # machinery delete_round's relocation already uses.
        if edge == "feeds" and guild is not None and puzzle.channel_id:
            channel = guild.get_channel(puzzle.channel_id)
            if channel is not None:
                await category_layout.move_channel_to_owner(
                    guild, group, TagGroupCategoryDb, channel, puzzle.solved,
                )

        await self.tag_with_edge(puzzle, group, edge)

    @pushes_board(hunt=lambda a: HuntJsonDb.get_by_attr(id=a["puzzle"].hunt_id))
    async def untag_from(self, puzzle, group, edge=None):
        """Remove *puzzle* from *group*, optionally guarded by edge type.

        When *edge* is provided and the membership edge doesn't match, this is
        a no-op.  Mirrors remove_from_meta's guard against removing the last
        tag of a metapuzzle.
        """
        if edge is not None and not puzzle.has_edge(group.id, edge):
            return
        if puzzle.is_target and len(puzzle.tags) <= 1 and (
            edge is None or puzzle.edges_for(group.id) == {edge}
        ):
            raise ValueError("cannot remove the last tag from a metapuzzle")
        if edge is None:
            PuzzleJsonDb.delete_tag(puzzle.id, group.id)
            puzzle.tag_edges.pop(group.id, None)
            if group.id in puzzle.tags:
                puzzle.tags.remove(group.id)
        else:
            PuzzleJsonDb.delete_tag_edge(puzzle.id, group.id, edge)
            puzzle.remove_edge(group.id, edge)
        PuzzleJsonDb.commit(puzzle)
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        failed = await self.refresh_targets(hunt, [group])
        if failed:
            raise SheetRefreshError(failed_group_ids=failed)

    @pushes_board()
    async def bulk_re_edge(self, tag_group, new_edge, hunt, *, refresh=True):
        """Flip every member of *tag_group* to *new_edge*.

        Returns a human-readable summary string, e.g.
        ``'flipped 3 feeds + 2 none → informs'`` or ``'0 members'``.

        ``refresh=False`` skips the roll-up render, for a caller that renders once
        itself after all its own durable mutations are committed. A render here
        raises the raw Sheets error, and `convert_to_type` has teardown and
        source_mode still to do (M7) — so it opts out rather than being aborted
        halfway by a stale sheet.
        """
        members = PuzzleJsonDb.get_all_from_tag_group(tag_group.id)
        # The target isn't a feeder — its self-edge stays 'none' and is never
        # flipped or counted in the sweep.
        members = [m for m in members if m.id != tag_group.target_puzzle_id]
        if not members:
            return "0 members"

        # Count existing edge types before the flip
        counter = {}
        for p in members:
            for current in (p.edges_for(tag_group.id) or {"feeds"}):
                if current in ("feeds", "informs", "none"):
                    counter[current] = counter.get(current, 0) + 1

        # Every member's puzzle mutex must be held across the flip: a command
        # already in flight on a member (e.g. !solve, loaded pre-flip) can commit
        # after bulk_update_edge_type runs, and its bidirectional tag-sync then
        # diffs against its stale in-memory edge and silently reverts the flip for
        # that member (#27). The CALLER holds them -- this method takes no lock of
        # its own. Locking here (under an edge already holding the target's lock)
        # is what made convert and delete/archive acquire overlapping puzzles in
        # opposite orders and deadlock (H5); every convert edge now takes the
        # target + members in one sorted puzzles_lock(convert_lock_ids(...)).
        PuzzleJsonDb.bulk_update_edge_type(tag_group.id, new_edge)
        # bulk_update_edge_type also flips any feeds/informs/none self-edge the
        # target itself carries (a legacy row, or one added via the web/bang
        # self-feed path) to new_edge — that row must be removed, not just
        # shadowed, or the target ends up with both new_edge and 'target' to
        # its own group and shows up as its own contributing feeder.
        if tag_group.target_puzzle_id:
            PuzzleJsonDb.delete_tag_edge(
                tag_group.target_puzzle_id, tag_group.id, new_edge)
            PuzzleJsonDb.tag_puzzle_with_edge(
                tag_group.target_puzzle_id, tag_group.id, "target")

        # Build summary string listing only non-zero edge types
        parts = [f"{v} {k}" for k, v in counter.items() if v]
        summary = "flipped " + " + ".join(parts) + f" → {new_edge}"

        if refresh:
            await self.refresh_target(hunt, tag_group)
        return summary

    @pushes_board()
    async def convert_to_type(self, guild, hunt, puzzle, new_type):
        """Convert *puzzle* into a group of *new_type*.

        If the puzzle is already backed by a group, updates type + source_mode
        and (unless Runaround) flips members to the default edge.  If not yet
        grouped, promotes in-place: creates a TagGroupData row, tags the puzzle,
        sets ``is_target=True``, and commits.

        Returns ``(tag_group, summary_string)``.
        """
        # Captured BEFORE the promote branch tags the puzzle into its own new
        # group, so this is purely the groups hosting it — see
        # _refresh_host_target_glyphs.
        host_group_ids = list(getattr(puzzle, "tags", None) or [])
        tag_group = TagGroupJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
        already_grouped = tag_group is not None

        if tag_group is None:
            # Bare puzzle — promote in-place (no Discord channel creation)
            tag_group = TagGroupData(
                name=puzzle.name,
                hunt_id=hunt.id,
                category_id=puzzle.channel_id,
                type=new_type,
                # Minted BEFORE the first commit: group_code is UNIQUE since
                # migration 042, and the row used to be inserted holding the
                # column default first. generate_uid only SELECTs, so it does
                # not need the row to exist.
                group_code=TagGroupJsonDb.generate_uid("group_code", 6, puzzle.name),
            )
            TagGroupJsonDb.commit(tag_group)
            tag_group.target_puzzle_id = puzzle.id
            TagGroupJsonDb.commit(tag_group)
            puzzle.add_tag(tag_group.id)
            puzzle.is_target = True
            PuzzleJsonDb.commit(puzzle)
            # The target's self-edge is 'target' — it is the thing fed, not a feeder.
            PuzzleJsonDb.tag_puzzle_with_edge(puzzle.id, tag_group.id, "target")
            puzzle.add_edge(tag_group.id, "target")

            # Restructure the sheet tab in place (header -> B1, contribution Table
            # -> A4) BEFORE source_mode/edges render into it. Without this the tab
            # keeps its offset-0 header and the lazily-created Table hides column A
            # (and the header's first column with it).
            gsheet_cog = self._gsheet_cog(hunt)
            if gsheet_cog is not None:
                await gsheet_cog.promote_tab_to_group(puzzle)
        else:
            # Already grouped — just update type (the tab is already structured).
            tag_group.type = new_type
            TagGroupJsonDb.commit(tag_group)

        # Flip edges (unless Runaround or edge is "none")
        if new_type != "Runaround":
            default_edge = TYPE_DEFAULTS[new_type]["default_edge"]
            # No render here: set_source_mode below ends in one, after teardown.
            summary = await self.bulk_re_edge(
                tag_group, default_edge, hunt, refresh=False)
        else:
            summary = "no edge change (runaround)"

        # Group has no metapuzzle: tear down the former target now that the edge
        # flip (which excludes the target's self-edge) has run. Only for a
        # PRE-EXISTING group — never the promote-in-place branch above, which just
        # created the group around this puzzle (that would delete it immediately).
        torn_down = already_grouped and new_type == "Group" and tag_group.target_puzzle_id == puzzle.id
        if torn_down:
            await self._teardown_group_target(guild, hunt, tag_group, puzzle)

        # Set source_mode from defaults. Done last (after edges/teardown are
        # committed) because it ends in a sheet refresh that can raise
        # (SheetRefreshError): if that happens the type/edge/teardown state is
        # already durable and only the sheet render is stale, matching the
        # web caller's "saved, sheet warning" handling. Doing this earlier used
        # to let a refresh failure skip the edge flip and teardown entirely,
        # and rendered the sheet against the pre-flip membership regardless.
        default_sm = TYPE_DEFAULTS[new_type]["default_source_mode"]
        await self.set_source_mode(hunt, tag_group, default_sm)

        # Update forum post tags (structural type may have changed).
        await self._update_forum_tags(guild, hunt, puzzle)
        # Skip the glyph refresh when the target was just torn down: `puzzle` no
        # longer exists (its DB row was deleted) and its channel was repurposed
        # into the group's general chat, not renamed to a glyphed puzzle name.
        if not torn_down:
            await self._refresh_channel_glyph(guild, hunt, puzzle)
            # The channel is a different KIND now, so its pinned welcome is stale:
            # wrong wording, and missing the group code + meta/capstone tagging
            # commands. The bang edge re-renders its own pin (_run_convert_to_type
            # -> info(update=True)), but the web convert routes have no equivalent;
            # doing it here covers both edges from the one service seam.
            await self.notifier.refresh_info(guild, hunt, puzzle)

        # Either direction changes whether this puzzle's HOSTS are metametas.
        await self._refresh_host_target_glyphs(
            guild, hunt, [gid for gid in host_group_ids if gid != tag_group.id])

        return tag_group, summary

    async def _refresh_host_target_glyphs(self, guild, hunt, group_ids):
        """Re-glyph the target puzzle of each group in *group_ids*.

        Gaining or losing a member that is another group's target flips a host
        between meta and metameta (``is_metameta``), so the host's OWN target
        channel should gain or lose the second 🧩. Nothing else does this: every
        other ``_refresh_channel_glyph`` call site refreshes the puzzle it was
        handed, never a host, so before this a host kept a stale glyph until
        someone ran ``!backfill_glyphs``.

        Best-effort per host: the conversion is already committed, and a stale
        glyph must not fail it."""
        for group_id in group_ids:
            try:
                group = TagGroupJsonDb.get_by_attr(id=group_id)
                if group is None or not group.target_puzzle_id:
                    continue
                host_target = PuzzleJsonDb.get_by_attr(id=group.target_puzzle_id)
                if host_target is None:
                    continue
                await self._refresh_channel_glyph(guild, hunt, host_target)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "could not refresh host glyph for group %s", group_id, exc_info=True)

    @pushes_board()
    async def convert_tag_group_type(self, guild, hunt, tag_group, new_type):
        """Change a TagGroup's type, keyed on the group (not its target puzzle).

        - Targeted group → any type: resolve the target and delegate to
          convert_to_type (which handles retype/re-edge and, for Group, teardown).
        - Bare Group → targeted type: create a meta (promote).
        Returns (tag_group, summary). Raises RuntimeError("<code>") on recoverable
        problems."""
        if tag_group.target_puzzle_id:
            target = PuzzleJsonDb.get_by_attr(id=tag_group.target_puzzle_id)
            if target is None:
                raise RuntimeError("target_missing")
            return await self.convert_to_type(guild, hunt, target, new_type)
        if new_type == "Group":
            return tag_group, "no change (already a group)"
        return await self._promote_group_to_target(guild, hunt, tag_group, new_type)

    async def _promote_group_to_target(self, guild, hunt, tag_group, new_type):
        """Bare Group → targeted type: create a meta puzzle for the group and
        repurpose the group's existing general chat channel as the meta channel.

        The meta channel name (clean_channel_name(name)) equals the group's general
        channel name, so create_puzzle's get_or_create_channel finds the existing
        channel and returns created=False, leaving the puzzle unlinked. We adopt
        that channel onto the meta and swap its pin to the meta welcome. (In the
        rare case create_puzzle made a fresh channel instead — e.g. the general
        channel had gone missing — we retire the leftover general channel.)

        We deliberately do NOT delete-then-recreate: channel.delete() only evicts
        the channel from discord.py's cache on the later CHANNEL_DELETE gateway
        event, so a subsequent create could still find the stale channel and skip
        linking. Repurposing in place sidesteps that race and preserves history.
        Non-forum only (forum is out of scope)."""
        tag_group.type = new_type
        TagGroupJsonDb.commit(tag_group)

        chat = None
        if guild is not None and getattr(hunt, "layout_mode", None) != "forum":
            # Find the general chat by its bare name FIRST, then pre-rename it to
            # the glyphed target name so create_puzzle (which now computes the
            # glyphed name) matches it as created=False and we adopt it in place —
            # otherwise create_puzzle spawns a fresh glyphed channel and we'd
            # wrongly retire (delete) this channel and its history.
            chat = category_layout.group_chat_channel(guild, tag_group)
            if chat is not None:
                want = self._desired_target_channel_name(guild, hunt, tag_group)
                if getattr(chat, "name", None) != want:
                    await self.safe_rename(chat, want)

        puzzle = await self.create_puzzle(
            guild, hunt, tag_group, tag_group.name, is_meta=True, notifier=self.notifier,
        )

        adopted = False
        if chat is not None:
            if puzzle.channel_id and puzzle.channel_id != chat.id:
                # create_puzzle made a fresh meta channel (general channel was gone):
                # retire the now-duplicate general channel.
                await self._safe_delete_channel(guild, chat.id)
            elif not puzzle.channel_id:
                # create_puzzle reused the general channel by name but left it
                # unlinked (created=False): adopt it as the meta channel.
                puzzle.channel_id = chat.id
                puzzle.channel_name = getattr(chat, "name", None)
                puzzle.channel_mention = getattr(chat, "mention", None)
                PuzzleJsonDb.commit(puzzle)
                adopted = True

        tag_group.target_puzzle_id = puzzle.id
        TagGroupJsonDb.commit(tag_group)

        # The adopted channel still carries the group welcome pin — swap it to the
        # meta welcome now that the target is set (refresh_info resolves the group
        # via target_puzzle_id). A fresh channel already got its meta welcome from
        # create_puzzle, so only refresh when we adopted.
        if adopted and self.notifier is not None:
            await self.notifier.refresh_info(guild, hunt, puzzle)

        default_sm = TYPE_DEFAULTS[new_type]["default_source_mode"]
        await self.set_source_mode(hunt, tag_group, default_sm)
        default_edge = TYPE_DEFAULTS[new_type]["default_edge"]
        summary = await self.bulk_re_edge(tag_group, default_edge, hunt)
        return tag_group, summary

    async def _teardown_group_target(self, guild, hunt, tag_group, target):
        """Convert a targeted group into a bare Group: delete the target meta's
        sheet + DB record, clear the group's target, and repurpose the meta's
        channel into the group's general chat channel (rename + group welcome pin).
        The DB mutation is the durable part; Discord/Sheets steps are best-effort.
        Non-forum only for the channel repurpose (forum is out of scope)."""
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            try:
                await gsheet_cog.delete_puzzle_spreadsheet(target)
            except Exception:
                logger.exception("_teardown_group_target: sheet delete failed for puzzle %s", target.id)

        channel_id = getattr(target, "channel_id", 0)
        # Snapshot before the delete: deleting the target cascades its tags rows,
        # so any group it also fed (the metameta case) loses its edge with no
        # further notice and keeps a contribution row for a puzzle that no longer
        # exists. delete_round and delete_puzzle both refresh those groups.
        other_fed_group_ids = [t for t in getattr(target, "tags", []) if t != tag_group.id]
        PuzzleJsonDb.delete(target.id)
        tag_group.target_puzzle_id = 0
        TagGroupJsonDb.commit(tag_group)
        if other_fed_group_ids:
            await self.refresh_targets(
                hunt, [TagGroupJsonDb.get_by_attr(id=gid) for gid in other_fed_group_ids])

        if guild is not None and channel_id and getattr(hunt, "layout_mode", None) != "forum":
            channel = guild.get_channel(channel_id)
            if channel is not None:
                await self.safe_rename(channel, clean_channel_name(tag_group.name))
                await self.notifier.refresh_group_info(guild, hunt, tag_group)

    async def collect_subtree_puzzles(self, root_group):
        """The puzzles a runaround (source_mode='subtree') rolls up: every puzzle
        reachable by an unbroken chain of 'feeds' edges up to `root_group`. Solved
        and unsolved feeders both surface — the sheet shows the full structure and
        answers fill in as they solve (matching 'round' mode and minimeta render).

        Starting at the runaround group, take its direct 'feeds' feeders; whenever a
        feeder is itself a group's target puzzle (a meta / meta-meta), descend into
        that group's feeders too — that's "the ones below". Only 'feeds' is ever
        followed: 'informs' (capstone evidence), 'none' (pooled grab-bag) and
        minimeta sub-answers (parent_puzzle_id children) are never surfaced, and the
        group's own target — carrying a 'target' self-edge — is excluded for free.

        A 'feeds' edge graph may legitimately contain cycles (mutual meta-to-meta
        feeding), so a visited-groups set is the cycle guard: each group is walked at
        most once, which guarantees termination without an arbitrary depth cap.
        Edgeless membership counts as 'feeds' (migration-012 default), matching the
        'round'-mode convention in select_rollup_members."""
        all_groups = TagGroupJsonDb.get_all(hunt_id=root_group.hunt_id)
        group_by_target = {g.target_puzzle_id: g for g in all_groups if g.target_puzzle_id}
        seen_ids = set()                       # collected puzzle ids (dedupe output)
        visited_groups = {root_group.id}       # groups already walked (cycle guard)
        puzzles = []
        stack = [root_group]
        while stack:
            group = stack.pop()
            for p in PuzzleJsonDb.get_all_from_tag_group(group.id):
                if p.parent_puzzle_id is not None:
                    continue                   # minimeta sub-answers belong to their own sheet only
                if not (p.has_edge(group.id, "feeds") or not p.edges_for(group.id)):
                    continue                   # follow 'feeds' only — skip informs / none / target
                if p.id not in seen_ids:
                    seen_ids.add(p.id)         # collect every feeder; answers fill in as they solve
                    puzzles.append(p)
                child = group_by_target.get(p.id)
                if child is not None and child.id not in visited_groups:
                    visited_groups.add(child.id)
                    stack.append(child)        # this feeder is a meta — descend into its feeders
        return puzzles

    @pushes_board()
    async def solve(self, guild, hunt, puzzle, solution):
        """Mark a puzzle solved: set status/solution/solve_time, refresh metas,
        stamp the round solve_time if this is the round's meta, archive the sheet,
        and persist. RAISES on Sheets failure; edges decide tolerance and
        presentation (channel reposition + embed live at the edges).

        Returns True if the solve was applied, False if rejected (empty solution
        with no existing solution — matches _solve_impl's guard). Raises
        SheetRefreshError at the very end (after archive + commit) if one or more
        meta sheets failed to refresh — the solve itself is fully applied; each
        failed meta is badged for the dashboard."""
        solution = solution.strip().upper()
        if solution == "" and puzzle.solution in [None, ""]:
            return False

        puzzle.status = "solved"
        if puzzle.solution and solution:
            puzzle.solution += "/" + solution
        elif solution:
            puzzle.solution = solution
        puzzle.solved = True
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(puzzle)

        # M8: archive the mini-puzzle thread on solve.
        if puzzle.parent_puzzle_id is not None and guild is not None:
            thread = guild.get_channel_or_thread(puzzle.channel_id)
            if thread is not None:
                try:
                    await thread.edit(archived=True)
                except Exception:
                    logger.exception("Failed to archive mini-puzzle thread %s on solve", puzzle.channel_id)

        # Failure isolation: attempt every meta this puzzle feeds, badge any that
        # fail, and keep going (one stale sheet must not skip the others, the group
        # solve_time stamp, or the archive below).
        failed_group_ids = []
        failed_puzzle_ids = []
        for tag in puzzle.tags:
            target_group = TagGroupJsonDb.get_by_attr(id=tag)
            try:
                await self.refresh_target(hunt, target_group)
            except Exception:
                failed_group_ids.append(tag)
            if target_group and target_group.target_puzzle_id == puzzle.id:
                target_group.solve_time = datetime.datetime.now(tz=pytz.UTC)
                target_group.status = "solved"   # M6: a group is solved when its meta is
                TagGroupJsonDb.commit(target_group)

        # M8: a solved mini rolls up onto its parent minimeta's own sheet (puzzle-keyed).
        # Best-effort: the badge records staleness; a failure here must not block archive
        # or fail the solve (minis don't gate the round-meta SheetRefreshError toast).
        if puzzle.parent_puzzle_id is not None:
            parent = PuzzleJsonDb.get_by_attr(id=puzzle.parent_puzzle_id)
            if parent is not None:
                try:
                    await self.refresh_minimeta(hunt, parent)
                except Exception:
                    logger.exception("solve: minimeta rollup refresh failed for parent %s",
                                     puzzle.parent_puzzle_id)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            # Failure isolation (matches the meta-refresh loop above): a Sheets
            # failure here must not skip the second commit, forum-tag update, or
            # board push below — the solve is already committed as applied, so an
            # uncaught raise here would incorrectly present it as failed while
            # leaving the DB out of sync with what did/didn't move to the archive.
            try:
                await gsheet_cog.move_puzzle_to_solved_sheet(puzzle)
            except Exception:
                logger.exception("solve: failed to archive spreadsheet for puzzle %s", puzzle.id)
                failed_puzzle_ids.append(puzzle.id)

        PuzzleJsonDb.commit(puzzle)
        for sheet in puzzle.additional_sheets:
            SheetsJsonDb.commit(sheet)
        # Update forum post tags (solved status changed).
        await self._update_forum_tags(guild, hunt, puzzle)

        if failed_group_ids or failed_puzzle_ids:
            raise SheetRefreshError(failed_group_ids=failed_group_ids, failed_puzzle_ids=failed_puzzle_ids)
        return True

    @pushes_board()
    async def unsolve(self, guild, hunt, puzzle):
        """Clear a puzzle's solve state and restore its sheet.

        For mini-puzzles (parent_puzzle_id set), also unarchives the thread.
        """
        puzzle.status = UNSOLVED_STATUS
        puzzle.solution = ""
        puzzle.solved = False
        puzzle.solve_time = None

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            # Exclude this puzzle from the zone computation: its sheet is still at
            # the solved end (not yet moved), so including it would anchor the zone
            # at the wrong position.
            existing = [p for p in PuzzleJsonDb.get_all_from_hunt(hunt.id) if p.id != puzzle.id]
            restore_idx = await gsheet_cog.compute_insert_index(puzzle.is_target, existing)
            await gsheet_cog.restore_puzzle_from_solved_sheet(puzzle, restore_index=restore_idx)

        # Commit AFTER the sheet restore (mirrors solve()'s ordering): restore
        # mutates puzzle.on_solved_sheet/google_page_id and each additional sheet's
        # google_page_id, so committing first would persist stale/dangling ids.
        PuzzleJsonDb.commit(puzzle)
        for sheet in puzzle.additional_sheets:
            SheetsJsonDb.commit(sheet)

        # Refresh metas so the unsolved answer disappears from the meta sheet.
        # Collected and reported at the end, exactly as solve does (M27): these
        # used to be logged and swallowed, which left the bang's
        # `except SheetRefreshError` unreachable and made it claim
        # ":white_check_mark: Sheets all restored." over a meta sheet still
        # showing the retracted answer.
        failed_group_ids = []
        for tag in puzzle.tags:
            try:
                target_group = TagGroupJsonDb.get_by_attr(id=tag)
                if target_group is not None:
                    await self.refresh_target(hunt, target_group)
            except Exception:
                logger.exception("unsolve: meta refresh failed for group %s", tag)
                failed_group_ids.append(tag)

        # M8: re-render the parent minimeta's sheet so the unsolved mini's answer clears.
        if puzzle.parent_puzzle_id is not None:
            parent = PuzzleJsonDb.get_by_attr(id=puzzle.parent_puzzle_id)
            if parent is not None:
                try:
                    await self.refresh_minimeta(hunt, parent)
                except Exception:
                    logger.exception("unsolve: minimeta rollup refresh failed for parent %s",
                                     puzzle.parent_puzzle_id)

        # M8: unarchive the mini-puzzle thread.
        if puzzle.parent_puzzle_id is not None and guild is not None:
            thread = guild.get_channel_or_thread(puzzle.channel_id)
            if thread is not None:
                try:
                    await thread.edit(archived=False)
                except Exception:
                    logger.exception("Failed to unarchive mini-puzzle thread %s", puzzle.channel_id)

        # Mirror round-solve undo if this puzzle was a round meta.
        for tag in puzzle.tags:
            round_ = TagGroupJsonDb.get_by_attr(id=tag)
            if round_ and round_.target_puzzle_id == puzzle.id:
                round_.solve_time = None
                round_.status = "active"
                TagGroupJsonDb.commit(round_)

        # Update forum post tags (solved status changed).
        await self._update_forum_tags(guild, hunt, puzzle)

        # Last, so everything above still runs. The DB mutation is committed and
        # pushes_board fires on this error by design -- only a meta sheet is
        # stale, and !rebuild is the right remedy, which is what the message says.
        if failed_group_ids:
            raise SheetRefreshError(failed_group_ids=failed_group_ids)

    @pushes_board()
    async def convert_to_puzzle(self, guild, hunt, puzzle):
        """Demote a metapuzzle back to an ordinary puzzle (ctx-free seam for the
        !convert_to_puzzle bang and the web endpoint).

        Deletes the round whose meta this puzzle is — which ORPHANS every feeder
        tagged to that round — drops the round id from the puzzle's
        tags, clears the is_target flag, commits, and reverts the puzzle's sheet
        restructure. Mirrors solve's durability ordering: the DB mutation is
        committed before the sheet revert, so a Sheets failure (which
        demote_tab_to_puzzle raises) leaves the demotion saved; the edge presents
        it as a non-fatal warning.

        Returns the number of feeders orphaned by the round deletion (every puzzle
        that was tagged to the deleted round, excluding the meta itself)."""
        orphaned = len(self.feeders_orphaned_by_convert(puzzle))
        # Captured before the group delete drops its id from puzzle.tags — see
        # _refresh_host_target_glyphs.
        host_group_ids = list(getattr(puzzle, "tags", None) or [])
        target_group = TagGroupJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
        if target_group is not None:
            # A SPLIT round owns several Discord categories mapped only by its
            # join rows, which FK-cascade off the tag_group row — handle the
            # Discord footprint BEFORE the row delete (same ordering as
            # delete_round/delete_hunt), or the categories are orphaned with
            # no map left to find them by. Legacy single-category rounds keep
            # today's behaviour: everything stays physically in place.
            if guild is not None and category_layout.is_split(
                    target_group, TagGroupCategoryDb):
                await self._teardown_converted_group_categories(
                    guild, hunt, target_group)
            TagGroupJsonDb.delete(target_group.id)
            # Drop every edge to the deleted group (normally just the 'target'
            # self-edge), not just puzzle.tags — leaving one in tag_edges makes the
            # next commit's tag-sync try to INSERT a row against the tag_group we
            # just deleted, failing with an IntegrityError.
            puzzle.tag_edges.pop(target_group.id, None)
            if target_group.id in puzzle.tags:
                puzzle.tags.remove(target_group.id)
        puzzle.is_target = False
        PuzzleJsonDb.commit(puzzle)
        await self._refresh_channel_glyph(guild, hunt, puzzle)
        # Back to a plain puzzle: re-render the pinned welcome for the new kind,
        # and re-glyph the hosts that just stopped having a sub-target.
        await self.notifier.refresh_info(guild, hunt, puzzle)
        await self._refresh_host_target_glyphs(
            guild, hunt,
            [gid for gid in host_group_ids
             if target_group is None or gid != target_group.id])

        gsheet_cog = self._gsheet_cog(hunt)
        # Same guard as remove_minimeta: demote only a tab the bot actually
        # promoted. convert_to_meta commits is_target BEFORE promote_tab_to_group,
        # so a Sheets failure there leaves a targeted puzzle on an untouched tab —
        # demoting that deletes columns A-D of an ordinary solver grid.
        if gsheet_cog is not None and puzzle.google_page_id:
            if await self._has_contribution_table(gsheet_cog, puzzle.google_page_id):
                await gsheet_cog.demote_tab_to_puzzle(puzzle)
        return orphaned

    async def absorb_category(self, guild, hunt, category):
        """Adopt a HAND-MADE category into the hunt's layout: relocate every
        puzzle channel in *category* through the general split/overflow
        machinery (solved puzzles land on the solved side; general splits or
        allocates overflow as needed), delete any hand-made solve-divider
        channel, and delete the category itself once fully emptied. The
        cleanup path for categories split by hand before the overflow engine
        existed.

        Refuses (ValueError) a category the engine already manages — a
        group's/hunt's home or a split/overflow member — absorbing one of
        those would fight the layout the bot itself maintains.

        Channels that aren't this hunt's puzzles are left in place and
        reported; any of those (or a failed move) keeps the category alive.
        Returns {"moved", "skipped", "failed", "deleted"}."""
        if category_layout.is_managed_category(category.id):
            raise ValueError("cannot absorb a bot-managed category")
        moved, skipped, failed = 0, [], []
        blockers = 0
        for ch in list(category.channels):
            if getattr(ch, "name", "") == self._SOLVE_DIVIDER_NAME:
                try:
                    await ch.delete(reason=self._DISCORD_DELETE_REASON)
                except Exception:
                    logger.exception("absorb_category: could not delete divider %s", ch.id)
                    blockers += 1
                continue
            puzzle = PuzzleJsonDb.get_by_attr(channel_id=ch.id)
            if puzzle is None or puzzle.hunt_id != hunt.id:
                skipped.append(getattr(ch, "name", str(getattr(ch, "id", "?"))))
                blockers += 1
                continue
            if await category_layout.move_channel_to_owner(
                    guild, hunt, HuntCategoryDb, ch, puzzle.solved):
                moved += 1
            else:
                failed.append(puzzle.name)
                blockers += 1
        deleted = False
        if blockers == 0:
            try:
                await category.delete(reason=self._DISCORD_DELETE_REASON)
                deleted = True
            except Exception:
                logger.exception("absorb_category: could not delete category %s",
                                 category.id)
        return {"moved": moved, "skipped": skipped, "failed": failed,
                "deleted": deleted}

    async def _teardown_converted_group_categories(self, guild, hunt, tag_group):
        """convert_to_puzzle's category handling for a SPLIT round: every member
        puzzle (including the demoted meta itself) keeps its channel, so
        relocate them all out of the doomed categories — into a sibling group
        they're also tagged to, else the hunt's general container — then tear
        the categories and join rows down. Best-effort per channel: one that
        cannot be relocated is left in place and protected from the category
        teardown, mirroring delete_round's keep_puzzles handling."""
        owned = {r.category_id
                 for r in category_layout.group_categories(tag_group, TagGroupCategoryDb)}
        keep_ids = set()
        for member in PuzzleJsonDb.get_all_from_tag_group(tag_group.id):
            if not member.channel_id:
                continue
            channel = guild.get_channel(member.channel_id)
            if channel is None or channel.category_id not in owned:
                continue
            if not await self._relocate_kept_puzzle(guild, hunt, tag_group, member, channel):
                keep_ids.add(member.channel_id)
        await category_layout.delete_group_categories(
            guild, tag_group, TagGroupCategoryDb, keep_channel_ids=frozenset(keep_ids))

    def feeders_orphaned_by_convert(self, puzzle):
        """Read-only: the feeders that converting *puzzle* back to a feeder would
        orphan — every puzzle tagged to its tag group except the meta itself.
        Powers the web convert confirm, which must name the orphan count up front.
        Returns [] if the puzzle backs no group."""
        target_group = TagGroupJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
        if target_group is None:
            return []
        return [
            p for p in PuzzleJsonDb.get_all_from_tag_group(target_group.id)
            if p.id != puzzle.id
        ]

    # --- delete_puzzle / delete_round --- ctx-free delete seams ----------------

    _DISCORD_DELETE_REASON = "bot-delete"
    _SOLVE_DIVIDER_NAME = "———solved———"

    async def _safe_delete_channel(self, guild, channel_id):
        """Delete a Discord channel OR thread by id, tolerating already-missing or
        rate-limited. Returns True if deleted, False otherwise. Guild=None is a no-op.

        get_channel_or_thread, not get_channel: a mini-puzzle's channel_id is a thread
        snowflake and threads live in a separate cache, so get_channel alone reports
        "already gone" and silently leaves the thread behind. The fetch fallback covers
        the archived case — minis archive on solve, and archived threads are dropped
        from that cache, so a solved mini's thread is only reachable over the API.
        """
        if guild is None or not channel_id:
            return False
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.NotFound:
                return False
            except (discord.RateLimited, discord.HTTPException):
                logger.exception("delete: could not fetch channel %s", channel_id)
                return False
        try:
            await channel.delete(reason=self._DISCORD_DELETE_REASON)
            return True
        except discord.NotFound:
            return False
        except (discord.RateLimited, discord.HTTPException):
            logger.exception("delete: could not delete channel %s", channel_id)
            return False

    @pushes_board()
    async def delete_puzzle(self, guild, hunt, puzzle):
        """Delete a puzzle: sheet tab, DB record, Discord channel, then refresh any
        parent metas. Ctx-free seam for !delete_puzzle and the web endpoint.
        Guild may be None (skips channel deletion).
        Raises SheetRefreshError if a parent meta refresh fails (deletion already committed
        and channel already deleted). The bang edge should send warnings to the hunt
        general channel rather than ctx.send after calling this.
        Raises ValueError if the puzzle is a target (meta/capstone/runaround): deleting
        it as an ordinary puzzle would orphan its tag_group.target_puzzle_id."""
        if puzzle.is_target:
            raise ValueError(
                "cannot delete a target puzzle; delete its tag group or "
                "demote the target first"
            )
        # Minis are children keyed by parent_puzzle_id, and that FK is ON DELETE
        # SET NULL -- so deleting their parent left the rows behind with a null
        # parent, which is exactly what get_unassigned selects on. They surfaced
        # on the Unassigned Feeders tab and the board, each holding a thread id
        # whose parent channel had just been deleted (M1).
        #
        # Deleted directly rather than through delete_mini: that seam re-renders
        # the parent's rollup after every removal, and the parent is about to be
        # deleted -- its own docstring warns against exactly that. Recursion is
        # bounded because a mini has no children of its own.
        for mini in PuzzleJsonDb.get_children(puzzle.id):
            await self.delete_puzzle(guild, hunt, mini)
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
        target_groups = [TagGroupJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        PuzzleJsonDb.delete(puzzle.id)
        failed = await self.refresh_targets(hunt, target_groups)
        channel = guild.get_channel(puzzle.channel_id) \
            if guild is not None and puzzle.channel_id else None
        category = getattr(channel, "category", None)
        await self._safe_delete_channel(guild, puzzle.channel_id)
        # A puzzle deletion is the most common shrink event — trigger the
        # vacated owner's consolidation (reclaim/fold/un-split), resolved
        # across both join tables, in the background. Best-effort: the guild
        # cache may not yet reflect the channel deletion (it updates on the
        # gateway event), in which case the next drop catches it instead.
        if category is not None:
            category_layout.maybe_consolidate_category(guild, category)
        if failed:
            raise SheetRefreshError(failed_group_ids=failed)

    @pushes_board()
    async def delete_round(self, guild, hunt, tag_group, keep_puzzles=True):
        """Delete a group's Discord category (including its solved-divider channel)
        and the group DB record. Per-puzzle/channel failures are isolated: one
        failure is logged and the run continues. Guild may be None (skips all
        Discord operations). Returns the list of puzzle names whose Discord
        channel could not be deleted or relocated.

        keep_puzzles (default True): only tear down the round's own footprint —
        its target puzzle (sheet + DB + channel) if it has one, or its dedicated
        chat channel if it doesn't (a target-less Group round) — and relocate
        every other member puzzle's Discord channel into the hunt's general
        category instead of deleting it (their DB rows/sheets are untouched
        either way). Pass keep_puzzles=False for the old all-or-nothing
        behavior that deletes every member puzzle. Forum-layout rounds always
        do the full delete regardless of keep_puzzles — forum threads have no
        "move to the general category" equivalent.

        Full delete (keep_puzzles=False, or forum layout regardless of
        keep_puzzles) still protects any member puzzle that is a DIFFERENT tag
        group's target — deleting it would orphan the nested group's
        target_puzzle_id, the same case delete_puzzle forbids outright — and
        refreshes every other tag group fed by a deleted puzzle, so neither
        path can orphan a nested meta's target_puzzle_id or leave a stale
        contribution row on an unrelated meta's sheet. Non-forum layouts
        relocate the protected puzzle's channel out of the round category
        (below); forum threads have no relocation equivalent, so a protected
        puzzle's thread is simply left in place, untouched.
        """
        narrow = keep_puzzles and hunt.layout_mode != "forum"
        is_forum = hunt.layout_mode == "forum"
        all_puzzles = PuzzleJsonDb.get_all_from_tag_group(tag_group.id)

        if narrow:
            target_puzzle = next(
                (p for p in all_puzzles
                 if tag_group.target_puzzle_id and p.id == tag_group.target_puzzle_id),
                None,
            )
            to_delete = [target_puzzle] if target_puzzle else []
            to_keep = [p for p in all_puzzles if p is not target_puzzle]
        else:
            # Delete only this round's own target and members tagged
            # EXCLUSIVELY to this group. A member also tagged to another group
            # (a shared feeder, or a nested meta whose target puzzle also
            # happens to feed this round) is kept and relocated instead —
            # deleting it would remove it from the other group too, or (for a
            # nested meta's target) orphan that group's target_puzzle_id, the
            # same case delete_puzzle forbids outright. Kept members are
            # auto-untagged from this group when its row is deleted below.
            # Applies to forum layout too (which always takes this branch,
            # since keep_puzzles never yields narrow=True for forum).
            to_delete = [
                p for p in all_puzzles
                if p.id == tag_group.target_puzzle_id
                or (not p.is_target and set(p.tags) <= {tag_group.id})
            ]
            to_keep = [p for p in all_puzzles if p not in to_delete]

        # Minis are deliberately untagged (see create_mini), so
        # get_all_from_tag_group never returns them and neither branch could
        # ever have swept them -- they were orphaned instead (M1). Extend AFTER
        # to_keep is computed, and only over what is actually being deleted:
        # the narrow branch RELOCATES its members, and a relocated puzzle's
        # minis must move with it, not die with the round.
        for parent in list(to_delete):
            to_delete.extend(PuzzleJsonDb.get_children(parent.id))

        failed_channels = []
        other_fed_group_ids = set()
        for puzzle in to_delete:
            gsheet_cog = self._gsheet_cog(hunt)
            if gsheet_cog is not None:
                try:
                    await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
                except Exception:
                    logger.exception("delete_round: sheet delete failed for puzzle %s", puzzle.id)
            other_fed_group_ids.update(tag for tag in puzzle.tags if tag != tag_group.id)
            PuzzleJsonDb.delete(puzzle.id)
            if guild is not None and puzzle.channel_id:
                if not await self._safe_delete_channel(guild, puzzle.channel_id):
                    failed_channels.append(puzzle.name)

        # A deleted puzzle may also feed an UNRELATED tag group's target (this
        # round's own tag_group is excluded above); refresh those so their
        # contribution tables don't keep a stale row for the deleted puzzle.
        if other_fed_group_ids:
            other_target_groups = [
                TagGroupJsonDb.get_by_attr(id=gid) for gid in other_fed_group_ids
            ]
            failed_refresh = await self.refresh_targets(hunt, other_target_groups)
            if failed_refresh:
                logger.error(
                    "delete_round: failed to refresh target groups %s after deleting "
                    "member puzzles of group %s", failed_refresh, tag_group.id)

        relocation_failed_channel_ids = set()
        if to_keep and guild is not None and not is_forum:
            owned_category_ids = {tag_group.category_id} if tag_group.category_id else set()
            owned_category_ids |= {
                row.category_id for row in TagGroupCategoryDb.get_for_group(tag_group.id)
            }
            for puzzle in to_keep:
                if not puzzle.channel_id:
                    continue
                channel = guild.get_channel(puzzle.channel_id)
                if channel is None or channel.category_id not in owned_category_ids:
                    continue
                if not await self._relocate_kept_puzzle(
                        guild, hunt, tag_group, puzzle, channel, owned_category_ids):
                    failed_channels.append(puzzle.name)
                    relocation_failed_channel_ids.add(puzzle.channel_id)

        if narrow and guild is not None:
            group_chat_ch = category_layout.group_chat_channel(guild, tag_group)
            if group_chat_ch is not None:
                if not await self._safe_delete_channel(guild, group_chat_ch.id):
                    failed_channels.append(tag_group.name)

        # Relocate FOREIGN channels — ones physically inside this group's owned
        # categories whose puzzle is NOT a member of this group at all (e.g. a
        # channel-home meta consolidated/promoted into this category, plus its
        # feeders). all_puzzles/to_delete/to_keep only cover this group's own
        # members, so these would otherwise be missed here and then swept up
        # (deleted) by the blanket per-category delete below. Move them to the
        # hunt's general category BEFORE that teardown; their DB rows/edges
        # belong to another group and are left untouched.
        if guild is not None and not is_forum:
            foreign_owned_category_ids = {tag_group.category_id} if tag_group.category_id else set()
            foreign_owned_category_ids |= {
                row.category_id for row in TagGroupCategoryDb.get_for_group(tag_group.id)
            }
            member_channel_ids = {p.channel_id for p in all_puzzles if p.channel_id}
            for category_id in foreign_owned_category_ids:
                category = category_layout._category_by_id(guild, category_id)
                if category is None:
                    continue
                for channel in list(category.channels):
                    if getattr(channel, "name", "") == self._SOLVE_DIVIDER_NAME:
                        continue
                    if channel.id in member_channel_ids:
                        continue
                    foreign_puzzle = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
                    if foreign_puzzle is None:
                        continue
                    if not await category_layout.move_channel_to_owner(
                            guild, hunt, HuntCategoryDb, channel, foreign_puzzle.solved):
                        failed_channels.append(foreign_puzzle.name)
                        relocation_failed_channel_ids.add(channel.id)

        if guild is not None:
            if hunt.layout_mode == "forum":
                # Forum mode: delete the forum channel (cascades its posts)
                forum_ch = guild.get_channel(tag_group.category_id)
                if forum_ch is not None:
                    try:
                        await forum_ch.delete(reason=self._DISCORD_DELETE_REASON)
                    except Exception:
                        logger.exception("delete_round: could not delete forum %s",
                                         tag_group.category_id)
            else:
                # Split rounds own several categories (unsolved/solved/overflow); delete
                # them all + drop the join rows. delete_group_categories returns False for
                # single-category rounds, which keep the legacy divider+category teardown.
                # keep_channel_ids protects any narrow-mode relocation failure from being
                # swept up in the blanket per-category channel delete. Must run BEFORE
                # TagGroupJsonDb.delete below: tag_group_category rows FK-cascade off the
                # tag_group row, so deleting that first would wipe the rows this reads.
                if not await category_layout.delete_group_categories(
                    guild, tag_group, TagGroupCategoryDb,
                    keep_channel_ids=frozenset(relocation_failed_channel_ids),
                ) and tag_group.category_id:
                    category = discord.utils.get(guild.categories, id=tag_group.category_id)
                    if category is not None:
                        for ch in list(category.channels):
                            if getattr(ch, "name", "") == self._SOLVE_DIVIDER_NAME:
                                try:
                                    await ch.delete(reason=self._DISCORD_DELETE_REASON)
                                except Exception:
                                    logger.exception("delete_round: could not delete divider channel")
                        try:
                            await category.delete(reason=self._DISCORD_DELETE_REASON)
                        except Exception:
                            logger.exception("delete_round: could not delete category %s",
                                             tag_group.category_id)

        TagGroupJsonDb.delete(tag_group.id)
        return failed_channels

    async def _relocate_kept_puzzle(self, guild, hunt, tag_group, puzzle, channel,
                                    doomed_category_ids: AbstractSet[int] = frozenset()):
        """Relocate a kept puzzle's channel out of the category owned by the
        group being deleted (``tag_group``). Prefers moving it into a category
        belonging to another group ``puzzle`` is also tagged to — so a shared
        puzzle lands with its other round rather than in the catch-all general
        category — and only falls back to the hunt's own general category
        if none of its other groups have a usable category. Both cases go
        through ``category_layout.move_channel_to_owner``, which auto-splits
        the target the same way round categories already do, rather than a
        separate general-category-only mechanism. Returns True if moved,
        False if every option failed (caller should treat the channel as
        needing manual attention)."""
        for other_gid in puzzle.tags:
            if other_gid == tag_group.id:
                continue
            other_group = TagGroupJsonDb.get_by_attr(id=other_gid)
            if other_group is None:
                continue
            # Skip a group whose own home category is being deleted here: for a
            # channel-home target consolidated INTO the doomed category,
            # move_channel_to_owner would resolve the destination back to that
            # same category and return a false 'already placed' success —
            # leaving the channel to be swept up and orphaned. Fall through to
            # the general fallback (or the next tagged group) instead.
            home = category_layout._home_category(guild, other_group)
            if home is not None and home.id in doomed_category_ids:
                continue
            if await category_layout.move_channel_to_owner(
                guild, other_group, TagGroupCategoryDb, channel, puzzle.solved
            ):
                return True
        return await category_layout.move_channel_to_owner(
            guild, hunt, HuntCategoryDb, channel, puzzle.solved
        )

    @pushes_board()
    async def add_partial(self, guild, hunt, puzzle, solution):
        """Mark a puzzle as partially solved: set status, append solution,
        commit and refresh metas. Does NOT set solve_time and does NOT push
        to Google Sheets (matches _partial_impl).
        """
        solution = solution.strip().upper()
        puzzle.status = PARTIAL_STATUS
        if puzzle.solution:
            puzzle.solution += "/" + solution
        else:
            puzzle.solution = solution
        PuzzleJsonDb.commit(puzzle)

        tag_groups = [TagGroupJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed = await self.refresh_targets(hunt, tag_groups)
        if failed:
            raise SheetRefreshError(failed_group_ids=failed)

    @pushes_board()
    async def change_solution(self, guild, hunt, puzzle, solution):
        """Replace the solution on an already-solved puzzle: set new solution,
        stamp solve_time, commit, refresh metas and push to Google Sheets."""
        solution = solution.strip().upper()
        puzzle.solution = solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(puzzle)

        tag_groups = [TagGroupJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed_group_ids = await self.refresh_targets(hunt, tag_groups)
        failed_puzzle_ids = []
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            # Failure isolation (same reasoning as solve()'s archive call): the
            # solution change is already committed, so a Sheets failure here must
            # not swallow any metas already collected in `failed_group_ids` above, nor
            # skip the board push that tells the dashboard the mutation applied.
            try:
                await gsheet_cog.update_solution(puzzle)
            except Exception:
                logger.exception("change_solution: failed to update sheet for puzzle %s", puzzle.id)
                failed_puzzle_ids.append(puzzle.id)

        if failed_group_ids or failed_puzzle_ids:
            raise SheetRefreshError(failed_group_ids=failed_group_ids, failed_puzzle_ids=failed_puzzle_ids)

    @pushes_board()
    async def add_solution(self, guild, hunt, puzzle, solution):
        """Append a solution to an already-solved puzzle: append with slash,
        stamp solve_time, commit, refresh metas and push to Google Sheets."""
        solution = solution.strip().upper()
        puzzle.solution += "/" + solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(puzzle)

        tag_groups = [TagGroupJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed_group_ids = await self.refresh_targets(hunt, tag_groups)
        failed_puzzle_ids = []
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            try:
                await gsheet_cog.update_solution(puzzle)
            except Exception:
                logger.exception("add_solution: failed to update sheet for puzzle %s", puzzle.id)
                failed_puzzle_ids.append(puzzle.id)

        if failed_group_ids or failed_puzzle_ids:
            raise SheetRefreshError(failed_group_ids=failed_group_ids, failed_puzzle_ids=failed_puzzle_ids)

    @pushes_board()
    async def update_puzzle_field(self, hunt, puzzle, field, value):
        """Set status, priority, or puzzle_type on *puzzle* and commit.

        Validates *field* against the allowed set and validates *value* against
        the known constants for status and priority.  Raises ValueError on bad
        field or bad value.  puzzle_type is free-form (no value validation).

        status is special-cased: "solved"/"backsolved" are labels the solve
        machinery (solve()/unsolve()) owns, not freeform status values, so this
        method only allows toggling between them on an already-solved puzzle
        (solved/backsolved reflect the same solved state, just how it was
        reached) and refuses to set or clear them any other way — use solve()/
        unsolve() for that.
        """
        _VALID_FIELDS = {"status", "priority", "puzzle_type"}
        if field not in _VALID_FIELDS:
            raise ValueError(f"Unknown puzzle field {field!r}; must be one of {sorted(_VALID_FIELDS)}")

        if field == "status":
            if value.lower() not in {s.lower() for s in STATUSES}:
                raise ValueError(f"Invalid status {value!r}; must be one of {STATUSES}")
            _SOLVED_LABELS = {"solved", "backsolved"}
            if value.lower() in _SOLVED_LABELS:
                if not puzzle.solved:
                    raise ValueError(
                        f"Status {value!r} can only be set by solving the puzzle, not by !status")
            elif puzzle.solved:
                raise ValueError(
                    "This puzzle is solved; use the unsolve command to change its status")
            # Store the canonical lowercase spelling whatever case came in. Callers
            # legitimately send Title Case (the slash choices and the dashboard
            # picker are both user-facing labels), and accepting it while storing it
            # verbatim is what left the column holding both "Stuck" and "stuck".
            value = value.lower()
        elif field == "priority":
            if value.lower() not in {p.lower() for p in PRIORITIES}:
                raise ValueError(f"Invalid priority {value!r}; must be one of {PRIORITIES}")
            value = value.lower()   # same canonicalisation as status, same reason

        setattr(puzzle, field, value)
        PuzzleJsonDb.commit(puzzle)
    @pushes_board()
    async def add_note(self, hunt, puzzle, text, attribution):
        """Append a note to the puzzle.  Each surface passes its own attribution
        string (e.g. a Discord jump URL or "Alice (web)").  Commits immediately."""
        puzzle.notes.append(f"{text} - {attribution}")
        PuzzleJsonDb.commit(puzzle)
    @pushes_board(when=bool)
    async def erase_note(self, hunt, puzzle, index):
        """Remove the note at 1-based *index*.  Returns True on success, False if
        the index is out of range.  Does NOT commit on failure."""
        if not (1 <= index <= len(puzzle.notes)):
            return False
        del puzzle.notes[index - 1]
        PuzzleJsonDb.commit(puzzle)
        return True

    async def _delete_additional_sheet(self, hunt, puzzle, sheet):
        """Delete an additional sheet's Google tab (best-effort) and its DB row,
        and drop it from the in-memory puzzle. Shared by the web + Discord removal
        edges."""
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            try:
                await gsheet_cog.delete_sheet(sheet.google_page_id, puzzle.solved)
            except HttpError as exc:
                # Idempotent: "No sheet with id" means the tab is already gone
                # (e.g. moved to the archive spreadsheet on solve) — not an error.
                if "No sheet with id" in str(exc):
                    logger.info("Additional sheet %s tab already absent; treating delete as done.", sheet.id)
                else:
                    logger.exception("Failed to delete Google tab for additional sheet %s", sheet.id)
            except Exception:
                logger.exception("Failed to delete Google tab for additional sheet %s", sheet.id)
        SheetsJsonDb.delete(sheet.id)
        puzzle.additional_sheets = [s for s in puzzle.additional_sheets if s.id != sheet.id]

    @pushes_board()
    async def set_puzzle_url(self, guild, hunt, puzzle, url):
        """Set the puzzle URL, commit and push to Google Sheets. Mirrors !link."""
        puzzle.url = url
        PuzzleJsonDb.commit(puzzle)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.update_puzzle(puzzle)

    @pushes_board(hunt=lambda a: HuntJsonDb.get_by_attr(id=a["puzzle"].hunt_id))
    async def make_minimeta(self, puzzle):
        """Mark a puzzle as a minimeta cluster parent. Idempotent."""
        puzzle.puzzle_type = "Minimeta"
        PuzzleJsonDb.commit(puzzle)

    @pushes_board()
    async def remove_minimeta(self, guild, hunt, puzzle):
        """Revert a minimeta back to an ordinary puzzle: delete every mini-puzzle
        child (thread + sheet tab + DB row), strip the rollup contribution Table from
        the minimeta's own sheet, delete the puzzle-keyed rollup row, and clear the
        Minimeta type. The inverse of make_minimeta + the minis created by create_mini.

        Guard mirrors the derived is_minimeta (bot/web/data.py): a puzzle qualifies if
        it owns minis OR still carries the Minimeta type marker; otherwise raises
        RuntimeError("not_minimeta"). Guild may be None (skips Discord thread deletion).
        Per-mini failures are isolated (logged, run continues) like delete_round.

        A Sheets failure (in the sheet_list scan or demote) raises before puzzle_type is cleared and committed, so the operation stays retryable — the inverse of convert_to_puzzle's DB-first ordering.
        """
        minis = self.get_minis(puzzle.id)
        if puzzle.puzzle_type != "Minimeta" and not minis:
            raise RuntimeError("not_minimeta")

        for mini in minis:
            try:
                await self.delete_puzzle(guild, hunt, mini)
            except Exception:
                logger.exception("remove_minimeta: failed to delete mini %s", mini.id)

        # Revert the minimeta's own tab. render_minimeta_contribution promotes the tab
        # to target layout, so demote_tab_to_puzzle (the inverse) strips the Table.
        # Guard on OUR Table actually existing — a minimeta made but never rendered has
        # none, and demoting it would delete the solver grid (see
        # _has_contribution_table; "any Table" used to be enough, and a solver's own
        # table on the tab was enough to trigger the delete).
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None and puzzle.google_page_id:
            if await self._has_contribution_table(gsheet_cog, puzzle.google_page_id):
                await gsheet_cog.demote_tab_to_puzzle(puzzle)

        rollup = RollupsJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
        if rollup is not None:
            RollupsJsonDb.delete(rollup.id)

        puzzle.puzzle_type = None
        PuzzleJsonDb.commit(puzzle)

    @pushes_board()
    async def convert_to_meta(self, guild, hunt, puzzle):
        """Convert an existing feeder puzzle into a meta.

        Creates a new round wrapping the puzzle, tags the puzzle to that
        round, sets is_target=True and commits. Returns (round, puzzle).
        Raises RuntimeError("already_meta") if the puzzle is already a meta.
        """
        if puzzle.is_target:
            raise RuntimeError("already_meta")

        new_round = TagGroupData(
            name=puzzle.name,
            hunt_id=hunt.id,
            category_id=puzzle.channel_id,
            type="Metapuzzle",
            # Minted before the first commit — see the note at the sibling site.
            group_code=TagGroupJsonDb.generate_uid("group_code", 6, puzzle.name),
        )
        TagGroupJsonDb.commit(new_round)
        new_round.target_puzzle_id = puzzle.id
        TagGroupJsonDb.commit(new_round)

        puzzle.add_tag(new_round.id)
        puzzle.is_target = True
        PuzzleJsonDb.commit(puzzle)
        # The target's self-edge is 'target' — it is the thing fed, not a feeder.
        PuzzleJsonDb.tag_puzzle_with_edge(puzzle.id, new_round.id, "target")
        puzzle.add_edge(new_round.id, "target")

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.promote_tab_to_group(puzzle)

        # Update forum post tags (newly converted to meta).
        await self._update_forum_tags(guild, hunt, puzzle)
        await self._refresh_channel_glyph(guild, hunt, puzzle)

        return new_round, puzzle

    def _highest_value_nested_meta(self, guild, hunt, cat):
        """The nested channel-home meta inside *cat* whose extraction frees the most
        channels, as (meta, value), or None. value = 1 (target channel) + exclusive
        plain feeders currently in *cat*."""
        all_groups = TagGroupJsonDb.get_all(hunt.id)
        target_ids = {g.target_puzzle_id for g in all_groups if g.target_puzzle_id}
        best = None
        for g in all_groups:
            if category_layout.primary_category(guild, g) is not None or not g.category_id:
                continue
            home = guild.get_channel(g.category_id)
            if home is None or getattr(home, "category", None) is not cat:
                continue
            target_puzzle = PuzzleJsonDb.get_by_attr(id=g.target_puzzle_id)
            if target_puzzle is None:
                continue
            members = PuzzleJsonDb.get_all_from_tag_group(g.id)
            feeders = select_extract_puzzles(g, target_puzzle, members, target_ids)[1:]
            value = 1 + sum(
                1 for p in feeders
                if getattr(guild.get_channel(p.channel_id), "category", None) is cat)
            if best is None or value > best[1]:
                best = (g, value)
        return best

    async def _maybe_relieve_overflow(self, guild, hunt, round_data):
        """Best-effort: before a create would overflow, auto-extract a channel-home
        meta to relieve pressure (Plan 4). Any failure falls through to the normal
        split / hard-fail path. At most one extraction; non-recursive.

        Applies to round puzzles (owner = the round) and to unassigned puzzles,
        whose owner is the hunt's general category (owner = the hunt) — otherwise
        the general category would only ever split and never auto-extract a nested
        meta the way rounds do (Case B below)."""
        if not category_layout.AUTO_EXTRACT_ON_OVERFLOW:
            return
        # Unassigned puzzles have no round; the hunt itself owns the general
        # category (via HuntCategoryDb), so treat it as the overflow owner.
        if round_data is not None:
            owner, owner_dao = round_data, TagGroupCategoryDb
        else:
            owner, owner_dao = hunt, HuntCategoryDb
        primary = category_layout.primary_category(guild, owner)
        if primary is None:
            # Case A (a channel-home meta with no real category) is round-only:
            # the hunt's general category is always a real category.
            if round_data is None:
                return
            host = category_layout._home_category(guild, round_data)
            if (host is None
                    or category_layout.puzzle_count(host) + 1
                        <= category_layout.CATEGORY_SOFT_LIMIT):
                return
            try:
                await self.extract_target_to_category(guild, hunt, round_data)
            except RuntimeError:
                logger.info("auto-extract (Case A) skipped for meta %s",
                            getattr(round_data, "id", "?"))
            return
        if category_layout.is_split(owner, owner_dao):
            return
        if (category_layout.puzzle_count(primary) + 1
                <= category_layout.CATEGORY_SOFT_LIMIT):
            return
        best = self._highest_value_nested_meta(guild, hunt, primary)
        if best is None or best[1] < category_layout.EXTRACT_MIN_VALUE:
            return
        try:
            await self.extract_target_to_category(guild, hunt, best[0])
        except RuntimeError:
            logger.info("auto-extract (Case B) skipped for meta %s",
                        getattr(best[0], "id", "?"))

    async def extract_target_to_category(self, guild, hunt, meta):
        """Give a channel-home *meta* its own Discord category.

        Moves meta's target channel + exclusive plain feeders into a freshly cloned
        category, flips ``meta.category_id`` from the target channel id to the new
        category id (so the group becomes an ordinary single-category owner), and
        triggers consolidation on any source categories it emptied.

        Raises RuntimeError("already_own_category") if meta already owns a real
        category, "no_category_space" at the guild category cap, or
        "no_template_category" if the hunt template category is missing.
        Returns the new discord category.
        """
        if category_layout._category_by_id(guild, meta.category_id) is not None:
            raise RuntimeError("already_own_category")
        if len(guild.categories) >= MAX_CATEGORIES:
            raise RuntimeError("no_category_space")
        ref_category = discord.utils.get(guild.categories, id=hunt.category_id)
        if ref_category is None:
            raise RuntimeError("no_template_category")

        # Which puzzles move (pure edge logic; data from the DAOs).
        target_puzzle = PuzzleJsonDb.get_by_attr(id=meta.target_puzzle_id)
        if target_puzzle is None:
            raise RuntimeError("no_target_puzzle")
        members = PuzzleJsonDb.get_all_from_tag_group(meta.id)
        all_groups = TagGroupJsonDb.get_all(hunt.id)
        target_puzzle_ids = {g.target_puzzle_id for g in all_groups if g.target_puzzle_id}
        puzzles = select_extract_puzzles(meta, target_puzzle, members, target_puzzle_ids)

        sources = set()
        async with category_layout.layout_lock:
            new_name = clean_category_name(meta.name)
            if discord.utils.get(guild.categories, name=new_name) is not None:
                new_name = new_name + " — " + clean_category_name(hunt.name)
            new_category = await ref_category.clone(name=new_name)
            await new_category.edit(position=ref_category.position + 1)
            divider = None
            if SOLVE_CATEGORY is False:
                divider, _ = await get_or_create_channel(
                    guild=guild, category=new_category, channel_name=SOLVE_DIVIDER,
                    channel_type="text",
                )

            # Ordering: target + unsolved feeders, then the divider, then solved
            # feeders. Sub-metas never appear here (excluded by select_extract_puzzles).
            unsolved = [p for p in puzzles if not p.solved]
            solved = [p for p in puzzles if p.solved]
            pos = 0
            for p in unsolved:
                channel = guild.get_channel(p.channel_id)
                if channel is None:
                    logger.warning(
                        "extract_target_to_category: channel %s for puzzle %s not "
                        "found; skipping", p.channel_id, p.id)
                    continue
                src = getattr(channel, "category", None)
                if src is not None and src.id != new_category.id:
                    sources.add(src.id)
                await channel.edit(category=new_category, position=pos)
                pos += 1
            if divider is not None:
                await divider.edit(position=pos)
                pos += 1
            for p in solved:
                channel = guild.get_channel(p.channel_id)
                if channel is None:
                    logger.warning(
                        "extract_target_to_category: channel %s for puzzle %s not "
                        "found; skipping", p.channel_id, p.id)
                    continue
                src = getattr(channel, "category", None)
                if src is not None and src.id != new_category.id:
                    sources.add(src.id)
                await channel.edit(category=new_category, position=pos)
                pos += 1

            meta.category_id = new_category.id
            TagGroupJsonDb.commit(meta)
            await self._refresh_channel_glyph(guild, hunt, target_puzzle)

        # Consolidate each vacated source so the previous owner folds/un-splits.
        for cid in sources:
            src = category_layout._category_by_id(guild, cid)
            if src is not None:
                category_layout.maybe_consolidate_category(guild, src)
        return new_category

    def _channels_to_consolidate(self, guild, hunt, meta):
        """(channels_with_puzzle, owned_categories) to move when consolidating *meta*.

        Category-owning meta: every non-divider channel across its owned categories
        (primary + split rows); nested channel-home metas ride along. Channel-home
        meta: its extract-set (target + exclusive plain feeders), no owned categories."""
        primary = category_layout.primary_category(guild, meta)
        if primary is None:
            target_puzzle = PuzzleJsonDb.get_by_attr(id=meta.target_puzzle_id)
            if target_puzzle is None:
                raise RuntimeError("no_target_puzzle")
            members = PuzzleJsonDb.get_all_from_tag_group(meta.id)
            all_groups = TagGroupJsonDb.get_all(hunt.id)
            target_ids = {g.target_puzzle_id for g in all_groups if g.target_puzzle_id}
            puzzles = select_extract_puzzles(meta, target_puzzle, members, target_ids)
            moves = []
            for p in puzzles:
                ch = guild.get_channel(p.channel_id)
                if ch is not None:
                    moves.append((ch, p))
            return moves, []
        owned = [primary]
        if category_layout.is_split(meta, TagGroupCategoryDb):
            for row in category_layout.group_categories(meta, TagGroupCategoryDb):
                cat = category_layout._category_by_id(guild, row.category_id)
                if cat is not None and cat.id not in {c.id for c in owned}:
                    owned.append(cat)
        moves = []
        for cat in owned:
            for ch in list(cat.channels):
                if ch.name == SOLVE_DIVIDER:
                    continue
                moves.append((ch, PuzzleJsonDb.get_by_attr(channel_id=ch.id)))
        return moves, owned

    async def _place_channel_in_category(self, category, channel, solved):
        """Reparent *channel* into *category*, honouring its solve divider (above
        if unsolved, below if solved). Returns True if placed, False if the edit
        failed (e.g. the category hit Discord's hard channel cap) — mirrors
        move_channel_to_owner's fail-soft contract so a failed target placement is
        left in place (and protected by keep_ids) rather than aborting the drain."""
        try:
            async with category_layout.layout_lock:
                divider_pos = solve_divider_position(category)
                if divider_pos is not False:
                    pos = (max((c.position for c in category.channels), default=0) + 1
                           if solved else divider_pos)
                else:
                    pos = max((c.position for c in category.channels), default=-1) + 1
                await channel.edit(category=category, position=pos)
        except Exception:
            logger.exception(
                "_place_channel_in_category: could not place channel %s",
                getattr(channel, "id", "?"))
            return False
        return True

    async def consolidate_to(self, guild, hunt, meta, destination, force=False):
        """Fold *meta* into *destination*'s category, reverting meta to channel-home.

        Raises RuntimeError("no_target_puzzle") if meta's target is missing,
        "bad_destination" if the destination has no category, or "would_overflow"
        if placing the channels would push the destination past the soft cap and not
        *force*. Returns *destination*."""
        target_puzzle = PuzzleJsonDb.get_by_attr(id=meta.target_puzzle_id)
        if target_puzzle is None:
            raise RuntimeError("no_target_puzzle")
        dest_primary = category_layout.primary_category(guild, destination)
        if dest_primary is None:
            dest_primary = category_layout._home_category(guild, destination)
        if dest_primary is None:
            raise RuntimeError("bad_destination")

        moves, owned = self._channels_to_consolidate(guild, hunt, meta)
        incoming = sum(1 for _c, p in moves if not getattr(p, "solved", False))
        if (not force and not category_layout.is_split(destination, TagGroupCategoryDb)
                and category_layout.puzzle_count(dest_primary) + incoming
                    > category_layout.CATEGORY_SOFT_LIMIT):
            raise RuntimeError("would_overflow")

        keep_ids = set()
        owned_ids = {c.id for c in owned}
        for channel, puzzle in moves:
            solved = getattr(puzzle, "solved", False)
            if getattr(puzzle, "is_target", False):
                moved = await self._place_channel_in_category(dest_primary, channel, solved)
            else:
                moved = await category_layout.move_channel_to_owner(
                    guild, destination, TagGroupCategoryDb, channel, solved)
            if not moved or getattr(channel, "category_id", None) in owned_ids:
                keep_ids.add(channel.id)
                logger.warning(
                    "consolidate_to: channel %s not relocated out of the source; "
                    "leaving it in place", getattr(channel, "id", "?"))

        if owned:
            if not await category_layout.delete_group_categories(
                    guild, meta, TagGroupCategoryDb,
                    keep_channel_ids=frozenset(keep_ids)):
                # non-split fallback: only delete a category with no real leftover.
                for cat in owned:
                    remaining = [ch for ch in list(cat.channels)
                                 if ch.name != SOLVE_DIVIDER and ch.id not in keep_ids]
                    if remaining:
                        logger.warning(
                            "consolidate_to: category %s still holds %d channel(s); "
                            "not deleting", getattr(cat, "id", "?"), len(remaining))
                        continue
                    try:
                        for ch in list(cat.channels):
                            if ch.id in keep_ids:
                                continue
                            await ch.delete(reason=self._DISCORD_DELETE_REASON)
                        await cat.delete(reason=self._DISCORD_DELETE_REASON)
                    except Exception:
                        logger.warning("consolidate_to: failed to delete category %s",
                                       getattr(cat, "id", "?"), exc_info=True)

        meta.category_id = target_puzzle.channel_id
        TagGroupJsonDb.commit(meta)
        await self._refresh_channel_glyph(guild, hunt, target_puzzle)
        return destination

    @pushes_board()
    async def rename_puzzle(self, guild, hunt, puzzle, name):
        """Rename a puzzle: set name/channel_name, commit, refresh metas.

        Caller must check for duplicate names before calling. The Discord
        channel rename is guarded on `guild is not None` so that web calls
        (and tests with _no_guild) skip it.
        """
        puzzle.name = name
        puzzle.channel_name = self.desired_channel_name(guild, hunt, puzzle)

        # Rename the Discord channel (skip when guild is None — web + test path).
        # Only edit when the normalised name actually changes: adding punctuation
        # Discord strips (e.g. "Monster Mash" -> "Monster Mash!") leaves
        # channel_name unchanged, and a no-op edit would waste a rename-bucket token.
        if guild is not None and puzzle.channel_id:
            channel = guild.get_channel(puzzle.channel_id)
            if channel is not None and channel.name != puzzle.channel_name:
                await self.safe_rename(channel, puzzle.channel_name)

        PuzzleJsonDb.commit(puzzle)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.update_puzzle(puzzle, True)

        # Refresh metas this puzzle feeds to (same failure-isolation as solve).
        failed = []
        for tag in puzzle.tags:
            target_group = TagGroupJsonDb.get_by_attr(id=tag)
            if target_group is not None:
                try:
                    await self.refresh_target(hunt, target_group)
                except Exception:
                    failed.append(tag)

        if failed:
            raise SheetRefreshError(failed_group_ids=failed)

    @pushes_board()
    async def rename_group(self, guild, hunt, tag_group, name):
        """Rename a group: rename its Discord container(s) and set tag_group.name.

        GROUP ONLY — never renames the target puzzle, its channel, or its sheet
        tab. Caller must check for duplicate names before calling. Discord renames
        are skipped when guild is None (web/test path). Plan is computed from the
        group's CURRENT name, so it must run before the name is mutated.
        """
        if guild is not None:
            for obj, new_name in category_layout.group_rename_plan(
                guild, tag_group, name, TagGroupCategoryDb
            ):
                await self.safe_rename(obj, new_name)

        tag_group.name = name
        TagGroupJsonDb.commit(tag_group)

    def get_minis(self, minimeta_id):
        """Return ordered list of mini-puzzle PuzzleData for a minimeta."""
        return PuzzleJsonDb.get_children(minimeta_id)

    async def solve_mini_by_number(self, guild, hunt, minimeta_id, number, solution):
        """Solve the Nth mini-puzzle (1-indexed) under the given minimeta puzzle id."""
        minis = self.get_minis(minimeta_id)
        if number < 1 or number > len(minis):
            raise ValueError(f"No mini #{number} — {len(minis)} mini(s) exist")
        return await self.solve(guild, hunt, minis[number - 1], solution)

    async def unsolve_mini_by_number(self, guild, hunt, minimeta_id, number):
        """Unsolve the Nth mini-puzzle (1-indexed) under the given minimeta puzzle id."""
        minis = self.get_minis(minimeta_id)
        if number < 1 or number > len(minis):
            raise ValueError(f"No mini #{number} — {len(minis)} mini(s) exist")
        await self.unsolve(guild, hunt, minis[number - 1])

    async def delete_mini(self, guild, hunt, mini):
        """Delete a single mini-puzzle (thread + sheet tab + DB row), then re-render
        its minimeta's rollup Table so the removed row disappears from the parent's
        own sheet. Returns the parent minimeta PuzzleData so the caller can refresh
        its pinned mini-list. Guild may be None (skips thread deletion).

        A separate seam from delete_puzzle because the parent's roll-up is
        puzzle-keyed, not a tag_group target: delete_puzzle's refresh_targets never
        touches it (a mini is deliberately untagged — see create_mini). Kept OFF
        delete_puzzle itself so remove_minimeta's teardown doesn't re-render the
        Table once per mini before stripping it.

        Raises ValueError if *mini* is not a mini-puzzle. No @pushes_board: the
        nested delete_puzzle already pushes, and the board carries the mini list,
        not the sheet render.
        """
        if not mini.parent_puzzle_id:
            raise ValueError("not a mini-puzzle")
        minimeta = PuzzleJsonDb.get_by_attr(id=mini.parent_puzzle_id)
        await self.delete_puzzle(guild, hunt, mini)
        await self.refresh_minimeta(hunt, minimeta)
        return minimeta

    async def delete_mini_by_number(self, guild, hunt, minimeta_id, number):
        """Delete the Nth mini-puzzle (1-indexed) under the given minimeta puzzle id.
        Returns the deleted mini's PuzzleData (its row is already gone)."""
        minis = self.get_minis(minimeta_id)
        if number < 1 or number > len(minis):
            raise ValueError(f"No mini #{number} — {len(minis)} mini(s) exist")
        mini = minis[number - 1]
        await self.delete_mini(guild, hunt, mini)
        return mini

    @pushes_board()
    async def create_mini(self, guild, hunt, minimeta, name, notifier=None):
        """Create a mini-puzzle: thread in minimeta's channel + Google tab + PuzzleData row.

        The mini is a child (parent_puzzle_id) of the minimeta — NOT a round feeder, so
        it is deliberately left untagged: it rolls up onto the minimeta's own sheet via
        the puzzle-keyed refresh_minimeta path (solve/unsolve/create), never onto the
        minimeta's round meta. Auto-sets puzzle_type='Minimeta' on the parent.
        """
        await self.make_minimeta(minimeta)

        mini = PuzzleData(
            name=name,
            hunt_id=hunt.id,
            start_time=datetime.datetime.now(tz=pytz.UTC),
            status='unstarted',
            priority='medium',
            puzzle_type='Mini',
            parent_puzzle_id=minimeta.id,
        )

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
            insert_idx = await gsheet_cog.compute_insert_index(False, existing)  # minis are regular
            await gsheet_cog.create_puzzle_spreadsheet(mini, index=insert_idx)

        PuzzleJsonDb.commit(mini)

        channel = guild.get_channel(minimeta.channel_id)
        thread = await channel.create_thread(
            name=clean_channel_name(name),
            type=discord.ChannelType.public_thread,
        )
        mini.channel_id = thread.id
        mini.channel_name = thread.name
        PuzzleJsonDb.commit(mini)

        # Best-effort welcome message in the new thread: name the parent minimeta and
        # link to the mini's Google sheet tab. A Discord hiccup must not fail the create.
        try:
            sheet_url = urls.spreadsheet_url(hunt.google_sheet_id, mini.google_page_id)
            await thread.send(
                f"This thread is for mini puzzle **{mini.name}** from mini meta "
                f"**{minimeta.name}**.\n{sheet_url}"
            )
        except Exception:
            logger.exception("create_mini: failed to post welcome message for mini %s", mini.id)

        # Render the (now larger) mini list onto the minimeta's own sheet. Best-effort:
        # a Sheets hiccup must not fail the create — the badge records the staleness.
        try:
            await self.refresh_minimeta(hunt, minimeta)
        except Exception:
            logger.exception("create_mini: minimeta rollup refresh failed for %s", minimeta.id)
        return mini

    @pushes_board()
    async def add_sheet(self, hunt, puzzle, name):
        """Create an extra working sheet (name optional). Raises
        RuntimeError("solved_no_sheets") if the puzzle is solved. Returns the
        AdditionalSheetData. Does NOT refresh the pinned info — the edge does."""
        if puzzle.solved:
            raise RuntimeError("solved_no_sheets")
        sheet = AdditionalSheetData(
            puzzle_id=puzzle.id,
            puzzle_name=name or f"Extra sheet for {puzzle.name}",
        )
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            sheet.google_page_id = await gsheet_cog.create_additional_spreadsheet(puzzle, name)
        puzzle.additional_sheets.append(sheet)
        SheetsJsonDb.commit(sheet)
        return sheet

    @pushes_board()
    async def remove_sheet(self, hunt, puzzle, sheet_id):
        """Remove an additional sheet by id. Raises RuntimeError("sheet_not_found")
        if absent. Does NOT refresh the pinned info — the edge does."""
        sheet = next((s for s in puzzle.additional_sheets if s.id == sheet_id), None)
        if sheet is None:
            raise RuntimeError("sheet_not_found")
        await self._delete_additional_sheet(hunt, puzzle, sheet)
    async def remove_sheet_by_index(self, hunt, puzzle, index):
        """Remove an additional sheet by 1-based index. Raises IndexError if out of bounds.
        Converts the index to a sheet id and delegates to remove_sheet."""
        sheets = puzzle.additional_sheets
        if index < 1 or index > len(sheets):
            raise IndexError(f"Sheet index {index} out of range (1–{len(sheets)})")
        sheet_id = sheets[index - 1].id
        await self.remove_sheet(hunt, puzzle, sheet_id)

    async def create_hunt_solved_sheet(self, hunt) -> str:
        """Create the hunt's solved-puzzles spreadsheet and record it (C7).

        Returns the new spreadsheet id. Raises RuntimeError("solved_sheet_exists")
        when the hunt already has one, and RuntimeError("gsheet_unavailable")
        when the Sheets cog is not loaded.

        Lock-free by the bot/core convention -- hunt_sheet_lock is held at the
        EDGE (see its docstring), so both callers wrap this call and the service
        never re-acquires it. That wrapping is what makes the exists-check and
        the create one critical section: unlocked, two invocations (a
        double-click, or bang and web together) both passed the check and
        created two Drive spreadsheets, orphaning one.

        The web route and !use_solved_sheet each hand-rolled this and had
        already drifted: the bang tested `len(hunt.solved_sheet_id) > 0`, which
        raises TypeError on a NULL column, and committed the hunt row even with
        no Sheets cog. Truthiness covers both spellings of "not set", and the
        commit happens only after a sheet really exists.
        """
        if hunt.solved_sheet_id:
            raise RuntimeError("solved_sheet_exists")
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            raise RuntimeError("gsheet_unavailable")
        hunt.solved_sheet_id = await gsheet_cog.create_solved_sheet(hunt.name)
        HuntJsonDb.commit(hunt)
        return hunt.solved_sheet_id

    @pushes_board()
    async def add_hunt_sheet(self, hunt, name):
        """Create a working sheet that belongs to the HUNT, not to a puzzle.

        Somewhere to pick at something found in a page's alt text, or loose on the
        hunt site, before anyone knows which puzzle it belongs to. Returns the
        AdditionalSheetData.

        A plain blank tab, not the PUZZLE_SHEET template: create_additional_
        spreadsheet derives its insert index from the puzzle's own tab and writes
        a header hyperlink from the puzzle's url/name, none of which means
        anything here. Always on the hunt's MAIN spreadsheet -- a hunt sheet is
        never solved, so the solved spreadsheet is never the target.
        """
        gsheet_cog = self._gsheet_cog(hunt)
        sheet = AdditionalSheetData(
            hunt_id=hunt.id,
            puzzle_name=name or f"Sheet for {hunt.name}",
        )
        if gsheet_cog is not None:
            # create_blank_tab does NOT dedupe the way add_new_sheet does, and the
            # Sheets API rejects a duplicate tab title, so uniquify first and keep
            # the name we actually used.
            unique = await gsheet_cog.get_unique_tab_name(
                sheet.puzzle_name, hunt.google_sheet_id)
            sheet.puzzle_name = unique
            sheet.google_page_id = await gsheet_cog.create_blank_tab(
                hunt.google_sheet_id, unique)
        SheetsJsonDb.commit(sheet)
        return sheet

    def get_hunt_sheets(self, hunt):
        """The hunt's own working sheets, ordered as !list_sheets numbers them."""
        return SheetsJsonDb.get_by_hunt(hunt.id)

    @pushes_board()
    async def remove_hunt_sheet(self, hunt, sheet_id):
        """Remove one of the hunt's own working sheets by id. Raises
        RuntimeError("sheet_not_found") if absent.

        Not _delete_additional_sheet: that reads puzzle.solved to pick the
        spreadsheet and mutates puzzle.additional_sheets, and a hunt sheet has
        neither a puzzle nor a solved state.
        """
        sheet = next((s for s in SheetsJsonDb.get_by_hunt(hunt.id)
                      if s.id == sheet_id), None)
        if sheet is None:
            raise RuntimeError("sheet_not_found")
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            try:
                # archive=False: a hunt sheet is never on the solved spreadsheet.
                await gsheet_cog.delete_sheet(sheet.google_page_id, False)
            except Exception:
                # Idempotent, as _delete_additional_sheet is: a tab someone
                # already removed by hand must not leave an undeletable row.
                logger.exception(
                    "Failed to delete Google tab for hunt sheet %s", sheet.id)
        SheetsJsonDb.delete(sheet.id)

    async def remove_hunt_sheet_by_index(self, hunt, index):
        """Remove one of the hunt's own sheets by its 1-based !list_sheets number.
        Raises IndexError if out of bounds."""
        sheets = SheetsJsonDb.get_by_hunt(hunt.id)
        if index < 1 or index > len(sheets):
            raise IndexError(f"Sheet index {index} out of range (1–{len(sheets)})")
        await self.remove_hunt_sheet(hunt, sheets[index - 1].id)

    # -------------------------------------------------------------------------
    # Create helpers (M3.5) — canonical paths used by bang + web edges
    # -------------------------------------------------------------------------

    async def _cleanup_orphan_puzzle(self, hunt, puzzle):
        """Best-effort removal of a puzzle's Google sheet + DB row after a failed
        create. Never raises (we are already handling a create failure)."""
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            try:
                await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
            except Exception:
                logger.exception("orphan cleanup: failed to delete sheet for %s", puzzle)
        try:
            if getattr(puzzle, "id", 0):
                PuzzleJsonDb.delete(puzzle.id)
        except Exception:
            logger.exception("orphan cleanup: failed to delete DB row for %s", puzzle)

    @pushes_board()
    async def create_puzzle(self, guild, hunt, round_data, name, is_meta=False, notifier=None):
        """Create a new puzzle: DB row + Google sheet + Discord channel.

        Mirrors the old cog path but calls *discord_channels* module functions
        directly (no cog delegation) and fires notification via the injected
        notifier.  Raises the underlying exception on failure so edges can
        decide tolerance.
        """
        # Auto-extract a channel-home meta if this create would otherwise
        # overflow (Plan 4). Best-effort; a Case-A extraction flips
        # round_data.category_id so the picks below see a real category.
        await self._maybe_relieve_overflow(guild, hunt, round_data)

        # No round (an unassigned puzzle) → drop the channel in the hunt's own category
        # rather than at the guild root.
        forum_channel = None
        if round_data is not None:
            if hunt.layout_mode == "forum":
                # Forum mode: resolve the forum channel from the round's category_id
                forum_channel = guild.get_channel(round_data.category_id)
                category = None  # no category layout needed for threads
            else:
                # Round puzzles flow through the multi-category layout: this splits the
                # round and/or allocates an overflow category as needed, so a single
                # category never hits the 50 cap.
                category = await category_layout.pick_unsolved_category(
                    guild, round_data, TagGroupCategoryDb,
                )
        else:
            # Unassigned puzzles flow through the same multi-category layout as
            # rounds, keyed to the hunt itself rather than a tag_group — so the
            # hunt's general category splits/allocates an overflow category as
            # needed instead of hard-blocking creation at the 50 cap.
            category = await category_layout.pick_unsolved_category(
                guild, hunt, HuntCategoryDb,
            )

        # Guild-wide ceiling still applies (overflow can't conjure channel slots).
        # Threads don't count toward the 500 cap.
        if hunt.layout_mode != "forum" and len(guild.channels) >= MAX_CHANNELS_PER_GUILD:
            raise RuntimeError("guild_full")

        new_puzzle = PuzzleData(
            name=name,
            hunt_id=hunt.id,
            start_time=datetime.datetime.now(tz=pytz.UTC),
            status='unstarted',
            priority='medium',
        )
        if is_meta:
            new_puzzle.is_target = True
        if hunt.url:
            prefix = hunt.puzzle_prefix or 'puzzle'
            hunt_url_base = urls.normalize_external_url(hunt.url).rstrip("/")
            p_name = urls.puzzle_url_slug(name, sep=hunt.url_sep)
            new_puzzle.url = f"{hunt_url_base}/{prefix}/{p_name}"
        if round_data is not None:
            new_puzzle.tags.append(round_data.id)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
            insert_idx = await gsheet_cog.compute_insert_index(new_puzzle.is_target, existing)
            await gsheet_cog.create_puzzle_spreadsheet(new_puzzle, index=insert_idx)

        PuzzleJsonDb.commit(new_puzzle)

        # Persist the membership edge for the assigned group. A bare commit inserts
        # the tags row with the DB default ('feeds'), so without this every new puzzle
        # would feed its group regardless of type. A group's own target (is_meta) is a
        # member of its group but neither feeds nor informs it, so it carries a 'target'
        # self-edge. A feeder gets the type-matched edge: feeds a meta/runaround,
        # informs a capstone, 'none' for a bare group (no target to feed or inform).
        if round_data is not None:
            edge = "target" if is_meta else default_edge_for_group(round_data)
            PuzzleJsonDb.tag_puzzle_with_edge(new_puzzle.id, round_data.id, edge)
            new_puzzle.add_edge(round_data.id, edge)

        try:
            channel_name = self.desired_channel_name(
                guild, hunt, new_puzzle, group=round_data if is_meta else None)
            if hunt.layout_mode == "forum" and round_data is not None:
                thread = await create_forum_post(forum_channel, channel_name)
                new_puzzle.channel_id = thread.id
                new_puzzle.channel_mention = thread.mention
                new_puzzle.channel_name = thread.name
                PuzzleJsonDb.commit(new_puzzle)
                # Notify (welcome) after channel + DB are committed.
                if notifier is not None:
                    await notifier.welcome(
                        guild, hunt,
                        tag_group=round_data,
                        puzzle=new_puzzle,
                        channel=thread,
                    )
                # Apply forum post tags to the new thread.
                await self._update_forum_tags(guild, hunt, new_puzzle)
            else:
                # Re-pick the target category NOW: the pick at the top of this
                # method ran before seconds-long sheet awaits, during which
                # concurrent creates can fill that category past the cap, or a
                # background consolidation can delete an overflow category
                # outright. The early pick stays as the fail-fast capacity
                # probe (no_category_space raises before any sheet/DB work);
                # this one is the authoritative placement.
                owner, owner_dao = (
                    (round_data, TagGroupCategoryDb) if round_data is not None
                    else (hunt, HuntCategoryDb))
                category = await category_layout.pick_unsolved_category(
                    guild, owner, owner_dao)
                text_channel, created = await get_or_create_channel(
                    guild=guild, category=category, channel_name=channel_name,
                    channel_type="text",
                )
                if created:
                    if category is not None:
                        divider = solve_divider_position(category)
                        if divider is not False:
                            await text_channel.edit(position=divider)
                    new_puzzle.channel_mention = text_channel.mention
                    new_puzzle.channel_id = text_channel.id
                    new_puzzle.channel_name = text_channel.name
                    PuzzleJsonDb.commit(new_puzzle)
                    # Notify (welcome) after channel + DB are committed.
                    if notifier is not None:
                        await notifier.welcome(
                            guild, hunt,
                            tag_group=round_data,
                            puzzle=new_puzzle,
                            channel=text_channel,
                        )
        except Exception:
            # Channel create (or post-create work) failed after the sheet + DB row
            # were committed — clean up both so we don't strand an orphan.
            await self._cleanup_orphan_puzzle(hunt, new_puzzle)
            raise

        # Refresh metas for every tag that was assigned.
        for tag in new_puzzle.tags:
            try:
                target_group = TagGroupJsonDb.get_by_attr(id=tag)
                if target_group is not None:
                    await self.refresh_target(hunt, target_group)
            except Exception:
                logger.exception("create_puzzle: sheet update failed for group %s", tag)
        return new_puzzle

    @pushes_board()
    async def create_tag_group(self, guild, hunt, name, notifier=None,
                               meta_puzzle_name=None, round_type="Metapuzzle"):
        """Create a new metapuzzle/round: category clone + group row + optional meta puzzle.

        Returns (TagGroupData, PuzzleData) when ``meta_puzzle_name`` is not False,
        else (TagGroupData, None).  Raises RuntimeError("<code>") on recoverable
        problems; raises the underlying exception otherwise.
        ``meta_puzzle_name``: name for the meta puzzle. Defaults to group ``name``.
        Pass a string to override it.  Pass ``False`` to skip (no meta / Group).
        ``round_type`` lets callers specify group classification.
        """
        mp_name = meta_puzzle_name if isinstance(meta_puzzle_name, str) else name
        create_meta = meta_puzzle_name is not False
        if TagGroupJsonDb.check_duplicates_in_hunt(name, hunt):
            raise RuntimeError("round_exists")
        if create_meta and PuzzleJsonDb.check_duplicates_in_hunt(mp_name, hunt.id):
            raise RuntimeError("puzzle_name_clash")
        if len(guild.categories) >= MAX_CATEGORIES:
            raise RuntimeError("no_category_space")

        new_category_name = clean_category_name(name)
        existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category:
            new_category_name = new_category_name + " — " + clean_category_name(hunt.name)
            existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category:
            raise RuntimeError("category_exists")

        ref_category = discord.utils.get(guild.categories, id=hunt.category_id)
        if ref_category is None:
            raise RuntimeError("no_template_category")

        new_category = None
        forum_channel = None
        group_text_channel = None
        new_round = None
        puzzle = None
        is_group = round_type == "Group"
        try:
            # --- Group: no forum, no category clone (forum mode) or with clone
            # (channel mode) — but always a plain text channel for chat. ---
            if is_group:
                host_category = ref_category if hunt.layout_mode == "forum" else None
                if hunt.layout_mode != "forum":
                    new_category = await ref_category.clone(name=new_category_name)
                    await new_category.edit(position=ref_category.position + 1)
                    host_category = new_category
                group_text_channel, _created_flag = await get_or_create_channel(
                    guild=guild, category=host_category,
                    channel_name=clean_channel_name(name), channel_type="text",
                )

            # --- Forum vs channel layout (non-Group types) ---
            elif hunt.layout_mode == "forum":
                from types import SimpleNamespace
                from bot.core.forum_config import forum_create_kwargs, render_welcome_plaintext
                kwargs = forum_create_kwargs()
                # Placeholder topic before the row/group_code exist; synced for real
                # after creation. Carry the name so the fallback isn't a bare "?".
                placeholder_group = SimpleNamespace(name=name, group_code="", type=round_type)
                placeholder_kind = "capstone" if round_type == "Capstone" else "meta"
                kwargs["topic"] = render_welcome_plaintext(
                    placeholder_kind, placeholder_group, None, hunt
                )
                forum_channel = await guild.create_forum(
                    name=new_category_name, category=ref_category,
                    **kwargs,
                )
            else:
                new_category = await ref_category.clone(name=new_category_name)
                await new_category.edit(position=ref_category.position + 1)
                if SOLVE_CATEGORY is False:
                    await get_or_create_channel(
                        guild=guild, category=new_category, channel_name=SOLVE_DIVIDER,
                        channel_type="text", position=500,
                    )

            if is_group:
                # Forum-mode Group has no cloned category, so it is identified by
                # its own text channel. Channel-mode Group clones a real category
                # for its puzzles — store THAT so puzzles assigned to the group
                # land under it (category_layout resolves category_id against
                # guild.categories; the cog resolves channel-mode groups via the
                # channel's parent category — see Puzzles.cog_before_invoke).
                if hunt.layout_mode == "forum":
                    assert group_text_channel is not None  # created above for every Group
                    parent_id = group_text_channel.id
                else:
                    assert new_category is not None  # cloned above in channel mode
                    parent_id = new_category.id
            elif hunt.layout_mode == "forum":
                assert forum_channel is not None  # created above in forum mode
                parent_id = forum_channel.id
            else:
                assert new_category is not None  # cloned above in channel mode
                parent_id = new_category.id
            new_round = TagGroupData(
                name=name, hunt_id=hunt.id,
                category_id=parent_id,
                type=round_type,
                # Minted before the first commit — see the note at the sibling
                # site. Both branches below needed a code anyway; hoisting it
                # into the constructor means the row is never inserted without
                # one, which is what the UNIQUE index of 042 requires.
                group_code=TagGroupJsonDb.generate_uid('group_code', 6, name),
            )
            # Initial commit so the row has an id (rollback safety).
            TagGroupJsonDb.commit(new_round)

            if is_group:
                # Group: no meta puzzle — but post a welcome.
                if notifier is not None:
                    await notifier.welcome(
                        guild, hunt, tag_group=new_round,
                        puzzle=None, channel=group_text_channel,
                    )
                return new_round, None

            # Create the meta puzzle for this round (skip for Metaless variants).
            # group_code is already set (minted at construction), so the welcome
            # still fires with a real code.
            if create_meta:
                puzzle = await self.create_puzzle(
                    guild, hunt, new_round, mp_name, is_meta=True, notifier=notifier,
                )
                new_round.target_puzzle_id = puzzle.id

            TagGroupJsonDb.commit(new_round)

            # Forum was created with a placeholder topic before the row/group_code
            # existed — sync the real guidelines now that they're populated.
            if hunt.layout_mode == "forum":
                from bot.core.forum_config import sync_forum_guidelines
                await sync_forum_guidelines(guild, hunt, new_round, puzzle)

            return new_round, puzzle
        except Exception:
            # Roll back so partial create leaves neither a meta-less round row
            # nor an orphan category.
            if new_round is not None and getattr(new_round, "id", 0):
                try:
                    TagGroupJsonDb.delete(new_round.id)
                except Exception:
                    logger.exception("create_round: rollback of round row failed")
            if puzzle is not None:
                await self._cleanup_orphan_puzzle(hunt, puzzle)
            if new_category is not None:
                try:
                    for ch in list(new_category.channels):
                        await ch.delete(reason="create_round rollback")
                    await new_category.delete(reason="create_round rollback")
                except Exception:
                    logger.exception("create_round: rollback of orphan category failed")
            if forum_channel is not None:
                try:
                    await forum_channel.delete(reason="create_round rollback")
                except Exception:
                    logger.exception("create_round: rollback of orphan forum failed")
            if group_text_channel is not None:
                try:
                    await group_text_channel.delete(reason="create_round rollback")
                except Exception:
                    logger.exception("create_round: rollback of group text channel failed")
            raise

    def _sole_informs_target(self, puzzle):
        """The puzzle id this puzzle solely informs (an 'informs' edge to a group
        with a target), or None if it informs zero or several targets."""
        targets = []
        for gid in list(puzzle.tags):
            if not puzzle.has_edge(gid, "informs"):
                continue
            group = TagGroupJsonDb.get_by_attr(id=gid)
            if group is not None and group.target_puzzle_id:
                targets.append(group.target_puzzle_id)
        return targets[0] if len(targets) == 1 else None

    @pushes_board()
    async def log_clues(self, hunt, puzzle, notes: list, media_id=None,
                        source_sheet_id=None) -> list:
        """Insert one evidence_item per note. Returns list of EvidenceData.

        `puzzle` may be None: evidence can belong to the hunt rather than to any
        one puzzle (e.g. something found in the hunt's alt text). With no source
        there is no informs edge to follow, so such items are created untargeted
        for manual routing via the Evidence panel.

        `source_sheet_id` names a hunt-level working sheet as the provenance. Like
        a sourceless item it creates no informs edge -- an edge joins two puzzles,
        and a working sheet is not one.

        If the source puzzle informs exactly one target, each clue is auto-routed
        to that informed target (target_puzzle_id) so it surfaces in the target's
        roll-up contribution table. Zero or several informs targets -> left
        untargeted for manual routing via the Evidence panel.
        """
        target_id = self._sole_informs_target(puzzle) if puzzle is not None else None
        items = []
        for note in notes:
            item = EvidenceJsonDb.log(hunt.id, puzzle.id if puzzle else None, note,
                                      media_id=media_id,
                                      source_sheet_id=source_sheet_id)
            if target_id is not None:
                item = EvidenceJsonDb.set_target(item.id, target_id) or item
            items.append(item)
        return items

    def _tab_claimed_by_puzzle(self, page_id) -> bool:
        """True when a puzzle's own tab, or any additional sheet, occupies this page.

        Two indexed lookups rather than a scan -- this runs only on the
        duplicate-name branch, so it is off the hot path entirely.
        """
        if PuzzleJsonDb.get_by_attr(google_page_id=str(page_id)):
            return True
        return bool(SheetsJsonDb.get_by_page_id(page_id))

    async def _ensure_bot_tab(self, hunt, gsheet_cog, tab_name) -> int:
        """Create `tab_name` on the hunt spreadsheet, tolerating one that exists.

        Returns the page id to treat as ours. A tab that no puzzle and no
        additional sheet claims is a leftover from an earlier run and is adopted;
        anything else keeps its contents and we take a uniquified name instead
        (D5).

        Adopting by name alone is never safe. ensure_evidence_table opens with a
        full-tab _values_clear and runs exactly on this path, so a stolen tab is
        a wiped tab; ensure_unassigned_table is milder but still writes the bot
        header over A1 and reconciles rows into a live solving grid.
        """
        try:
            return await gsheet_cog.create_blank_tab(hunt.google_sheet_id, tab_name)
        except Exception:
            page_id = await gsheet_cog.get_page_id_by_name(tab_name)
            if page_id is None:
                # Nothing carries the name, so this was not a duplicate-name
                # rejection -- a quota trip or a dead token. Let it surface.
                raise

        if not self._tab_claimed_by_puzzle(page_id):
            return page_id

        unique = await gsheet_cog.get_unique_tab_name(tab_name, hunt.google_sheet_id)
        return await gsheet_cog.create_blank_tab(hunt.google_sheet_id, unique)

    async def refresh_evidence(self, hunt):
        """Render the Evidence sheet tab as a native Table (mirrors Unassigned
        Feeders). Lazily creates the tab + Table, pins the tab just after Unassigned
        Feeders, and reconciles rows keyed on the hidden evidence id.
        """
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return

        # Serialize per hunt PER TAB, exactly as refresh_unassigned does and for
        # the same reason (M22) -- reconcile_table's read-then-write means two
        # concurrent evidence refreshes lose one another's appended rows. The
        # ("evidence", hunt.id) key is deliberately distinct from the unassigned
        # one: different tabs, no shared rows, no reason to queue behind it.
        async with rollup_lock(("evidence", hunt.id)):
            # Lazy-create the tab + native Table. ensure_evidence_table pins the tab to
            # index 1 (after Unassigned Feeders), writes the header and wraps it in a
            # Table, and returns the table id to persist on the hunt.
            dirty = False
            if hunt.evidence_sheet_page_id is None:
                hunt.evidence_sheet_page_id = await self._ensure_bot_tab(
                    hunt, gsheet_cog, "Evidence")
                dirty = True

            if hunt.evidence_table_id is None:
                table_id = await gsheet_cog.ensure_evidence_table(hunt.evidence_sheet_page_id)
                hunt.evidence_table_id = table_id
                dirty = True

            if dirty:
                HuntJsonDb.commit(hunt)

            # Reconcile the native Table against the full desired set. The hidden id
            # column (col A) is the reconcile key — evidence item ids are unique, unlike
            # the Source Puzzle name. Columns: id, Source Puzzle, Note, Target Puzzle,
            # Logged At.
            items = EvidenceJsonDb.get_all(hunt.id)
            puzzle_ids = ({i.source_puzzle_id for i in items if i.source_puzzle_id}
                          | {i.target_puzzle_id for i in items if i.target_puzzle_id})
            names = resolve_puzzle_names(puzzle_ids)
            sheet_names = resolve_sheet_names(
                getattr(i, "source_sheet_id", None) for i in items)
            region_names = resolve_region_names(
                getattr(i, "source_region_id", None) for i in items)
            rows = [
                [
                    i.id,
                    # A null source is evidence belonging to the hunt rather than to
                    # a puzzle; the old fallback rendered it as "Puzzle None". A
                    # hunt-level working sheet names itself.
                    (names.get(i.source_puzzle_id, f"Puzzle {i.source_puzzle_id}")
                     if i.source_puzzle_id
                     else (evidence_source_label(i, sheet_names, region_names) or "—")),
                    evidence_cell_content(i),
                    names.get(i.target_puzzle_id, "") if i.target_puzzle_id else "",
                    str(i.created_at)[:16] if i.created_at else "",
                ]
                for i in items
            ]
            await gsheet_cog.reconcile_table(
                page_id=hunt.evidence_sheet_page_id,
                desired_rows=rows,
                data_col_count=5,
                table_name=EVIDENCE_TABLE_NAME,
            )

    async def refresh_evidence_safe(self, hunt):
        """Fire-and-forget wrapper for refresh_evidence. Never raises and logs
        any failure (with hunt id) to discord.log. Use this for every
        asyncio.create_task(...) trigger of refresh_evidence."""
        try:
            await self.refresh_evidence(hunt)
        except Exception:
            logger.exception(
                "background refresh_evidence failed for hunt %s",
                getattr(hunt, "id", "?"),
            )

    async def refresh_informs_targets(self, hunt, puzzle):
        """Re-render the roll-up for each target this puzzle informs, so a newly
        logged clue's information surfaces in the target's contribution table.
        Best-effort: a sheet error on one target is logged, not raised."""
        for gid in list(puzzle.tags):
            if not puzzle.has_edge(gid, "informs"):
                continue
            group = TagGroupJsonDb.get_by_attr(id=gid)
            if group is None or not group.target_puzzle_id:
                continue
            try:
                await self.refresh_target(hunt, group)
            except Exception:
                logger.exception("refresh_informs_targets: failed for group %s", gid)

    async def link_informs_for_evidence(self, hunt, source_puzzle_id, target_puzzle_id):
        """Auto-add an 'informs' edge from the source puzzle to the group whose target
        is target_puzzle_id, so routed evidence renders. Never evicts feeds. Best-effort.

        An informs edge joins two puzzles, so a sourceless (hunt-level) evidence
        item -- source_puzzle_id is None -- has no edge to create; the falsy
        check below already covers that case, along with a missing target.

        Holds the source puzzle's lock for the whole body (M5). This method is the
        documented exception to PuzzleService being lock-free: it is only ever
        launched as a bare create_task from the web layer, so no caller already holds
        that lock. asyncio.Lock is not reentrant -- if a future caller acquires the
        lock before calling this, it deadlocks permanently.
        """
        if not (source_puzzle_id and target_puzzle_id):
            return
        async with puzzle_lock(source_puzzle_id):
            group = TagGroupJsonDb.get_by_attr(target_puzzle_id=target_puzzle_id)
            source = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
            if group is None or source is None:
                return
            if not source.has_edge(group.id, "informs"):
                await self.tag_with_edge(source, group, "informs")

    async def unlink_informs_for_evidence(self, hunt, source_puzzle_id, target_puzzle_id):
        """Remove the 'informs' edge once the source has no remaining evidence
        routed to that target. Edges aren't tagged with who created them, so
        this also removes an edge a human added manually to the same pair.

        Same reasoning as link_informs_for_evidence: a sourceless evidence item
        (source_puzzle_id is None) never had an edge to begin with, and the
        falsy check below already returns early for it.

        Holds the source puzzle's lock for the whole body (M5). This method is the
        documented exception to PuzzleService being lock-free: it is only ever
        launched as a bare create_task from the web layer, so no caller already holds
        that lock. asyncio.Lock is not reentrant -- if a future caller acquires the
        lock before calling this, it deadlocks permanently.
        """
        if not (source_puzzle_id and target_puzzle_id):
            return
        async with puzzle_lock(source_puzzle_id):
            remaining = EvidenceJsonDb.get_all(
                hunt.id, source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
            if remaining:
                return
            group = TagGroupJsonDb.get_by_attr(target_puzzle_id=target_puzzle_id)
            source = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
            if group is None or source is None:
                return
            if source.has_edge(group.id, "informs"):
                await self.untag_from(source, group, edge="informs")

    async def link_informs_for_evidence_safe(self, hunt, source_puzzle_id, target_puzzle_id):
        """Fire-and-forget wrapper for link_informs_for_evidence. Never raises
        and logs any failure (with hunt id) to discord.log. Use this for every
        asyncio.create_task(...) trigger of link_informs_for_evidence."""
        try:
            await self.link_informs_for_evidence(hunt, source_puzzle_id, target_puzzle_id)
        except Exception:
            logger.exception(
                "background link_informs_for_evidence failed for hunt %s",
                getattr(hunt, "id", "?"),
            )

    async def unlink_informs_for_evidence_safe(self, hunt, source_puzzle_id, target_puzzle_id):
        """Fire-and-forget wrapper for unlink_informs_for_evidence. Never raises
        and logs any failure (with hunt id) to discord.log. Use this for every
        asyncio.create_task(...) trigger of unlink_informs_for_evidence."""
        try:
            await self.unlink_informs_for_evidence(hunt, source_puzzle_id, target_puzzle_id)
        except Exception:
            logger.exception(
                "background unlink_informs_for_evidence failed for hunt %s",
                getattr(hunt, "id", "?"),
            )

    async def refresh_target_for_puzzle(self, hunt, target_puzzle_id):
        """Re-render the roll-up whose target is the given puzzle (best-effort).
        Used after evidence is manually routed to a target."""
        if not target_puzzle_id:
            return
        group = TagGroupJsonDb.get_by_attr(target_puzzle_id=target_puzzle_id)
        if group is None:
            return
        try:
            await self.refresh_target(hunt, group)
        except Exception:
            logger.exception("refresh_target_for_puzzle: failed for puzzle %s", target_puzzle_id)

    async def refresh_unassigned(self, hunt, show_round=False):
        """Incremental reconcile of the Unassigned Puzzles tab.

        Creates the tab and table lazily on first call. Appends new puzzles,
        updates answer/length for existing ids (e.g. solved), prunes classified ones.
        """
        from bot.core.unassigned import unassigned_row

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return

        tab_name = "Unassigned Feeders"

        # Serialize per hunt PER TAB (M22). reconcile_table is read-then-write
        # across three awaits (read the key column, next_row = max(existing)+1,
        # then batch_update), so two of the ~21 fire-and-forget refreshes
        # interleaving compute the same next_row and the second append overwrites
        # the first. Keyed ("unassigned", hunt.id) rather than hunt.id alone: the
        # Evidence tab is a different tab that cannot race this one, so a shared
        # per-hunt key would serialize the two for nothing.
        #
        # Deadlock-safe on the same grounds as refresh_meta: every caller is
        # lock-free (fire-and-forget create_task sites, plus the awaited
        # !rebuild_unassigned, which holds no rollup lock), and nothing inside
        # takes another lock.
        async with rollup_lock(("unassigned", hunt.id)):
            # Lazy-create tab + table
            dirty = False
            if hunt.unassigned_sheet_page_id is None:
                hunt.unassigned_sheet_page_id = await self._ensure_bot_tab(
                    hunt, gsheet_cog, tab_name)
                dirty = True

            if hunt.unassigned_table_id is None:
                table_id = await gsheet_cog.ensure_unassigned_table(
                    hunt.unassigned_sheet_page_id,
                    show_round,
                )
                hunt.unassigned_table_id = table_id
                dirty = True

            if dirty:
                HuntJsonDb.commit(hunt)

            # Fetch current unassigned puzzles from DB and reconcile the native Table
            # against the full desired set — reconcile_table does the single diff
            # (read sheet keys, append/update/prune) and grows the Table. Column 3
            # (Length) is number-typed, so it must be written numeric.
            pool = PuzzleJsonDb.get_unassigned(hunt.id)
            desired = [unassigned_row(p) for p in pool]

            await gsheet_cog.reconcile_table(
                page_id=hunt.unassigned_sheet_page_id,
                desired_rows=desired,
                data_col_count=4,   # id, name, answer, length (Notes col E is solver-owned)
                numeric_cols=(3,),
                table_name=UNASSIGNED_TABLE_NAME,
            )

    async def refresh_unassigned_safe(self, hunt, show_round=False):
        """Fire-and-forget wrapper for the background reconcile.

        Never raises and logs any failure (with hunt id) to discord.log, so a
        broken background sync is visible there instead of surfacing only as a
        stray asyncio "Task exception was never retrieved" trace. Use this for
        every asyncio.create_task(...) trigger; the awaited !rebuild path calls
        refresh_unassigned directly so it can report to the channel.
        """
        try:
            await self.refresh_unassigned(hunt, show_round=show_round)
        except Exception:
            logger.exception(
                "background refresh_unassigned failed for hunt %s",
                getattr(hunt, "id", "?"),
            )

    # -- Hunt create / delete ------------------------------------------

    def _gating_overwrites(self, guild, team_role):
        """Overwrite dict that gates a category to ``team_role``: @everyone hidden,
        the team role visible, and the bot always visible (so denying @everyone can
        never lock the bot out of channels it manages)."""
        return {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            team_role: discord.PermissionOverwrite(view_channel=True),
            guild.me: discord.PermissionOverwrite(view_channel=True),
        }

    async def _resolve_gating_role(self, guild, role_name, actor, confirm_existing_role):
        """Resolve the role to gate a hunt with, enforcing two invariants:
        never gate a zero-member role, and confirm reuse of an existing role.

        Returns the ``discord.Role`` to gate with, or ``None`` when no role was
        requested. Raises ``RuntimeError("existing_role_confirm")`` when the name
        matches an existing role and it hasn't been confirmed, or
        ``RuntimeError("role_not_applied")`` when a requested role can't be made
        visible (no actor, or the actor couldn't be added and the role is empty).
        """
        if not role_name:
            return None
        if actor is None:
            raise RuntimeError("role_not_applied")

        role = discord.utils.get(guild.roles, name=role_name)
        if role is not None and not confirm_existing_role:
            raise RuntimeError("existing_role_confirm")

        created = False
        if role is None:
            role = await guild.create_role(
                name=role_name, colour=discord.Colour.random(),
                mentionable=True, reason="bot-role",
            )
            created = True

        seeded = actor in role.members
        if not seeded:
            try:
                await actor.add_roles(role, reason="hunt-gating")
                seeded = True
            except discord.HTTPException:
                logger.warning("could not add %s to gating role %r", actor, role_name)
                seeded = False

        if not seeded and not role.members:
            # The role would have zero members -> an invisible hunt. Don't gate.
            if created:
                try:
                    await role.delete(reason="rollback: gating role left empty")
                except discord.HTTPException:
                    logger.warning("could not delete empty rollback role %r", role_name)
            raise RuntimeError("role_not_applied")

        return role

    async def _apply_gating_overwrites(self, guild, hunt, overwrites):
        """Retroactively apply ``overwrites`` to a hunt's existing categories: the
        main category, the solved category (iff SOLVE_CATEGORY), and each existing
        round/group category. Synced child channels inherit, so we edit categories
        only. Returns a list of category names that failed to update."""
        categories = []
        main = guild.get_channel(hunt.category_id)
        if main is not None:
            categories.append(main)
        if SOLVE_CATEGORY:
            solved = discord.utils.get(
                guild.categories, name=category_layout.solved_category_name(hunt.name)
            )
            if solved is not None:
                categories.append(solved)
        for tag_group in TagGroupJsonDb.get_all(hunt.id):
            cat = category_layout.primary_category(guild, tag_group)
            if cat is not None:
                categories.append(cat)

        failed = []
        for category in categories:
            try:
                await category.edit(overwrites=overwrites)
            except discord.HTTPException:
                name = getattr(category, "name", str(getattr(category, "id", "?")))
                logger.warning("gating overwrite failed for category %s", name)
                failed.append(name)
        return failed

    @pushes_board()
    async def set_hunt_role(self, guild, hunt, *, role_name, requester,
                            confirm_existing_role=False):
        """Gate an OPEN hunt (role_id 0) to ``role_name``: set role_id and apply the
        Discord overwrite to every existing category. Raises RuntimeError with
        "already_gated" / "existing_role_confirm" / "role_not_applied". Returns the
        list of category names whose overwrite update failed (empty on full success).
        """
        if hunt.role_id != 0:
            raise RuntimeError("already_gated")

        team_role = await self._resolve_gating_role(
            guild, role_name, requester, confirm_existing_role
        )
        if team_role is None:
            # _resolve_gating_role returns None only for an empty role_name —
            # there is nothing to gate with (callers validate, so this is a guard).
            raise RuntimeError("role_not_applied")
        hunt.role_id = team_role.id
        HuntJsonDb.commit(hunt)

        overwrites = self._gating_overwrites(guild, team_role)
        return await self._apply_gating_overwrites(guild, hunt, overwrites)

    async def create_hunt(self, guild, *, name, url, start_timestamp,
                          role_name=None, username=None, password=None,
                          create_solved_sheet=False, layout_mode="channel",
                          guild_id=0, creator=None, confirm_existing_role=False):
        """Create a hunt: Discord category/channels + Google Sheet + DB row.

        ``layout_mode`` is fixed at creation: "channel" (default) clones a
        category per round; "forum" creates a forum channel per round. It cannot
        be changed once the hunt has rounds, so it is set here only.

        Raises RuntimeError("hunt_exists") if the name is already taken, or
        RuntimeError("bad_layout_mode") for an unrecognised ``layout_mode``.
        Returns the committed HuntData.
        """
        if layout_mode not in ("channel", "forum"):
            raise RuntimeError("bad_layout_mode")
        # Scoped to the INVOKING guild (M6). guild.id is the Discord snowflake --
        # deliberately not this method's own guild_id kwarg, which carries the
        # guilds table's row id. Unscoped, this refused a name already used in a
        # DIFFERENT guild, so !hunt's own guild-scoped pre-check would accept a
        # cross-guild duplicate and then this would reject it.
        # guild is dereferenced unguarded a few lines below (create_category),
        # so it is required here too -- no None branch to invent.
        if HuntJsonDb.check_duplicates(name, guild_id=guild.id):
            raise RuntimeError("hunt_exists")

        # existing_role_confirm must propagate (entry point prompts + retries);
        # role_not_applied is non-fatal here -> fall back to an OPEN hunt.
        try:
            team_role = await self._resolve_gating_role(
                guild, role_name, creator, confirm_existing_role
            )
        except RuntimeError as exc:
            if "existing_role_confirm" in str(exc):
                raise
            team_role = None
        overwrites = self._gating_overwrites(guild, team_role) if team_role else {}

        # Category is brand-new, so channels are created directly (no get_or_create lookup).
        category_name = clean_category_name(name)
        category = await guild.create_category(
            category_name, position=max(len(guild.categories) - 2, 0),
            overwrites=overwrites,
        )
        text_channel = await guild.create_text_channel(
            "general", category=category, reason="bot-hunt-general", position=0
        )
        if SOLVE_CATEGORY:
            await guild.create_category(
                category_layout.solved_category_name(name),
                position=max(len(guild.categories) - 2, 0),
                overwrites=overwrites,
            )
        else:
            await guild.create_text_channel(
                SOLVE_DIVIDER, category=category, reason="bot-round", position=500
            )

        gsheet_cog = self._gsheet_cog(None)
        google_sheet_id = ""
        solved_sheet_id = ""
        if gsheet_cog is not None:
            google_sheet_id = await gsheet_cog.create_hunt_spreadsheet(name)
            if create_solved_sheet:
                solved_sheet_id = await gsheet_cog.create_solved_sheet(name)

        new_hunt = HuntData(
            name=name,
            category_id=category.id,
            channel_id=text_channel.id,
            guild_id=guild_id,
            url=urls.normalize_external_url(url),
            uid=HuntJsonDb.generate_uid('uid'),
        )
        new_hunt.layout_mode = layout_mode
        new_hunt.start_timestamp = start_timestamp
        new_hunt.start_time = datetime.datetime.fromtimestamp(start_timestamp, tz=pytz.UTC)
        if username:
            new_hunt.username = username
        if password:
            new_hunt.password = password
        if team_role is not None:
            new_hunt.role_id = team_role.id
        if google_sheet_id:
            new_hunt.google_sheet_id = google_sheet_id
        if solved_sheet_id:
            new_hunt.solved_sheet_id = solved_sheet_id

        HuntJsonDb.commit(new_hunt)
        if self.notifier is not None:
            # Not @pushes_board: there is no pre-existing hunt to bind the
            # decorator's `hunt` parameter to (this call creates it) — push
            # manually now that new_hunt exists, so connected dashboards can
            # invalidate their hunt list instead of waiting on a reload
            # (review #54).
            await self.notifier.board_changed(new_hunt)
        return new_hunt

    @pushes_board()
    async def delete_hunt(self, guild, hunt) -> list:
        """Tear down a hunt: puzzles, rounds, spreadsheet files, Discord channels, DB row.

        Per-puzzle channel failures are isolated and returned as a list of puzzle names.
        """
        failed_channels = []

        # Delete each puzzle's DB row + Discord channel. Deliberately NO per-puzzle
        # tab delete here (unlike delete_round/delete_puzzle, where the spreadsheet
        # survives): both Drive files are deleted wholesale below, taking every tab
        # with them, so a per-puzzle deleteSheet would be N serialised Sheets calls
        # (all behind gsheets._execute_lock, blocking every other hunt) for tabs
        # about to vanish. A puzzle's tab is in the working file, or in the archive
        # file when on_solved_sheet — and on_solved_sheet is only ever set when the
        # hunt has a solved sheet, which is deleted here too (review M20).
        puzzles = PuzzleJsonDb.get_all_from_hunt(hunt.id)
        for puzzle in puzzles:
            PuzzleJsonDb.delete(puzzle.id)
            if guild is not None and puzzle.channel_id:
                if not await self._safe_delete_channel(guild, puzzle.channel_id):
                    failed_channels.append(puzzle.name)

        # Delete each group's category + DB row
        tag_groups = TagGroupJsonDb.get_all(hunt.id)
        for tag_group in tag_groups:
            # TagGroupJsonDb.delete happens at the end of this iteration: tag_group_category
            # rows FK-cascade off the tag_group row, so deleting it first would wipe the
            # rows delete_group_categories below needs to read.
            if guild is not None and tag_group.category_id:
                if hunt.layout_mode == "forum":
                    # Forum mode: category_id holds a forum channel id
                    forum_ch = guild.get_channel(tag_group.category_id)
                    if forum_ch is not None:
                        try:
                            await forum_ch.delete(reason=self._DISCORD_DELETE_REASON)
                        except Exception:
                            logger.exception("delete_hunt: could not delete forum %s",
                                             tag_group.category_id)
                else:
                    group_chat_ch = category_layout.group_chat_channel(guild, tag_group)
                    if group_chat_ch is not None:
                        await self._safe_delete_channel(guild, group_chat_ch.id)
                    # Split rounds own several categories (unsolved/solved/overflow);
                    # delete_group_categories tears down all of them + the DB join
                    # rows (also FK-cascaded now, but Discord objects still need an
                    # explicit API call). Returns False for single-category groups,
                    # which keep the legacy divider+category teardown below.
                    if not await category_layout.delete_group_categories(
                        guild, tag_group, TagGroupCategoryDb,
                    ):
                        category = discord.utils.get(guild.categories, id=tag_group.category_id)
                        if category is not None:
                            for ch in list(category.channels):
                                if getattr(ch, "name", "") == self._SOLVE_DIVIDER_NAME:
                                    try:
                                        await ch.delete(reason=self._DISCORD_DELETE_REASON)
                                    except Exception:
                                        logger.exception("delete_hunt: could not delete divider channel")
                            try:
                                await category.delete(reason=self._DISCORD_DELETE_REASON)
                            except Exception:
                                logger.exception("delete_hunt: could not delete group category %s",
                                                 tag_group.category_id)
            TagGroupJsonDb.delete(tag_group.id)

        # Delete spreadsheet files (whole Drive file, not just tabs)
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            if hunt.google_sheet_id:
                await gsheet_cog.delete_hunt_spreadsheet(hunt.google_sheet_id)
            if hunt.solved_sheet_id:
                await gsheet_cog.delete_hunt_spreadsheet(hunt.solved_sheet_id)

        # Delete hunt channel + category (best-effort; failures logged, not raised)
        if guild is not None:
            if hunt.channel_id:
                try:
                    await self._safe_delete_channel(guild, hunt.channel_id)
                except Exception:
                    logger.exception("delete_hunt: could not delete hunt channel %s", hunt.channel_id)
            if hunt.category_id:
                # The hunt's general category can split/overflow the same way a
                # round's can (see create_puzzle/delete_round); delete_group_categories
                # tears down all of unsolved/solved/overflow + the DB join rows.
                # Returns False for a still-single-category hunt, which keeps the
                # legacy divider+category teardown below.
                if not await category_layout.delete_group_categories(
                    guild, hunt, HuntCategoryDb,
                ):
                    category = discord.utils.get(guild.categories, id=hunt.category_id)
                    if category is not None:
                        for ch in list(category.channels):
                            if getattr(ch, "name", "") == self._SOLVE_DIVIDER_NAME:
                                try:
                                    await ch.delete(reason=self._DISCORD_DELETE_REASON)
                                except Exception:
                                    logger.exception("delete_hunt: could not delete divider channel")
                        try:
                            await category.delete(reason=self._DISCORD_DELETE_REASON)
                        except Exception:
                            logger.exception("delete_hunt: could not delete hunt category %s", hunt.category_id)

        # Before the row goes: `media` cascades from `hunts`, and media_gc
        # reclaims from rows, so files not removed here become unreachable.
        media_gc.remove_hunt_media(hunt.id)
        HuntJsonDb.delete(hunt.id)
        return failed_channels

    def _archive_thread_exists(self, dest, thread_name):
        """True if an active thread with *thread_name* already exists on *dest* —
        used to make archive_hunt resumable after a partial run."""
        return any(getattr(t, "name", None) == thread_name for t in getattr(dest, "threads", []))

    def _hunt_categories(self, guild, hunt):
        """Every live category the hunt owns: main + solved-by-name + any
        split/overflow categories the hunt's own general container owns + each
        group's categories."""
        category_ids = set()
        if hunt.category_id:
            category_ids.add(hunt.category_id)
        solved = discord.utils.get(
            guild.categories, name=category_layout.solved_category_name(hunt.name))
        if solved is not None:
            category_ids.add(solved.id)
        for row in category_layout.group_categories(hunt, HuntCategoryDb):
            if row.category_id:
                category_ids.add(row.category_id)
        for tg in TagGroupJsonDb.get_all(hunt.id):
            if tg.category_id:
                category_ids.add(tg.category_id)
            for row in category_layout.group_categories(tg, TagGroupCategoryDb):
                if row.category_id:
                    category_ids.add(row.category_id)
        return [c for c in
                (discord.utils.get(guild.categories, id=cid) for cid in category_ids)
                if c is not None]

    async def _archive_channel_into(self, guild, dest, webhook, ch, report):
        """Archive one hunt channel onto *dest*: a text channel becomes a thread
        (then is deleted), the solve divider and voice/other channels are just
        deleted. Failures are isolated onto *report*."""
        try:
            if ch.name == self._SOLVE_DIVIDER_NAME:
                if not await self._safe_delete_channel(guild, ch.id):
                    report.failures.append(
                        getattr(ch, "name", str(getattr(ch, "id", "?"))))
            elif isinstance(ch, discord.TextChannel):
                thread_name = hunt_archive.thread_name_for_channel(ch)
                if self._archive_thread_exists(dest, thread_name):
                    # A prior run archived this but failed to delete it — finish that.
                    if await self._safe_delete_channel(guild, ch.id):
                        report.channels_skipped += 1
                    else:
                        report.failures.append(
                            getattr(ch, "name", str(getattr(ch, "id", "?"))))
                else:
                    r = await hunt_archive.archive_and_delete_channel(
                        self.bot, dest, webhook, ch, thread_name=thread_name)
                    report.channels_archived += 1
                    report.messages_moved += r.messages_moved
            else:  # voice / stage / other
                if await self._safe_delete_channel(guild, ch.id):
                    report.voice_deleted += 1
                else:
                    report.failures.append(
                        getattr(ch, "name", str(getattr(ch, "id", "?"))))
        except Exception:
            logger.exception("archive_hunt: failed on channel %s",
                             getattr(ch, "name", getattr(ch, "id", "?")))
            report.failures.append(getattr(ch, "name", str(getattr(ch, "id", "?"))))

    async def _sweep_hunt_categories(self, guild, dest, webhook, categories, report, *,
                                     keep_channel_id, keep_category_id, on_progress):
        """Condense every channel in *categories* into threads on *dest* (deleting
        each), then delete every category except *keep_category_id*.
        *keep_channel_id* is skipped: the surviving archive channel when *dest*
        lives in the hunt's own guild, None when *dest* is on a hub."""
        total = sum(1 for c in categories for ch in c.channels if ch.id != keep_channel_id)
        done = 0
        for category in categories:
            for ch in list(category.channels):
                if ch.id == keep_channel_id:
                    continue
                done += 1
                await self._archive_channel_into(guild, dest, webhook, ch, report)
                if on_progress is not None:
                    await on_progress(done, total, getattr(ch, "name", ""))
            if category.id != keep_category_id:
                try:
                    await category.delete(reason=self._DISCORD_DELETE_REASON)
                    report.categories_deleted += 1
                except Exception:
                    logger.exception("archive_hunt: could not delete category %s", category.id)
                    report.failures.append(getattr(category, "name", str(category.id)))

    def _drop_split_layout_rows(self, hunt):
        """Archive keeps every hunt/tag_group DB row (only the flag below
        changes), so no FK cascade ever fires — drop the split-layout join
        rows explicitly, or they point at the deleted categories forever and
        is_split() stays true for the archived hunt and every archived round."""
        HuntCategoryDb.delete_for_group(hunt.id)
        for tg in TagGroupJsonDb.get_all(hunt.id):
            TagGroupCategoryDb.delete_for_group(tg.id)

    async def archive_hunt(self, guild, hunt, invoker, *, on_progress=None):
        """Condense a whole hunt into threads on its general channel, delete its
        categories/channels (voice included), rename the surviving channel, and
        mark the hunt archived. Google Sheets and DB rows are kept.

        Per-channel failures are isolated and collected on the report; one bad
        channel never aborts the run.
        """
        report = hunt_archive.HuntArchiveReport()
        dest = guild.get_channel(hunt.channel_id)
        if dest is None:
            report.failures.append("general-channel-missing")
            return report
        webhook = await hunt_archive.ensure_webhook(dest)

        categories = self._hunt_categories(guild, hunt)
        dest_category_id = dest.category.id if dest.category is not None else None
        # Process dest's own category last so we never strand the surviving channel.
        ordered = ([c for c in categories if c.id != dest_category_id] +
                   [c for c in categories if c.id == dest_category_id])
        await self._sweep_hunt_categories(
            guild, dest, webhook, ordered, report,
            keep_channel_id=dest.id, keep_category_id=dest_category_id,
            on_progress=on_progress)

        # dest survives alone: detach it and delete its now-empty category.
        if dest_category_id is not None:
            main_cat = None
            try:
                await dest.edit(category=None)
                main_cat = discord.utils.get(guild.categories, id=dest_category_id)
                if main_cat is not None:
                    await main_cat.delete(reason=self._DISCORD_DELETE_REASON)
                    report.categories_deleted += 1
            except Exception:
                logger.exception("archive_hunt: could not detach/delete main category")
                report.failures.append(getattr(main_cat, "name", str(dest_category_id)))

        team = hunt_archive.resolve_team_name(guild, hunt, invoker)
        report.final_name = hunt_archive.archived_channel_name(hunt, team)
        await self.safe_rename(dest, report.final_name)

        self._drop_split_layout_rows(hunt)

        hunt.archived = True
        HuntJsonDb.commit(hunt)
        return report

    async def archive_hunt_to_hub(self, guild, hunt, invoker, hub, hub_row, *, on_progress=None):
        """Archive a hunt living on a team server into *hub* (already validated
        by hunt_archive.resolve_hub): condense EVERY channel, the general one
        included since the whole server is about to be abandoned, into threads on
        a per-hunt hub channel. Then re-home the hunt row to the hub so the
        dashboard still shows it once the team server is gone, and leave the team
        server if nothing else is homed there.

        Re-runnable: the hub channel is found again by its topic, and threads
        already written are skipped by name (see _sweep_hunt_categories)."""
        report = hunt_archive.HuntArchiveReport()
        team = hunt_archive.resolve_team_name(guild, hunt, invoker)
        report.final_name = hunt_archive.archived_channel_name(hunt, team)
        dest = await hunt_archive.ensure_hub_archive_channel(hub, hunt, report.final_name)
        report.hub_channel_id = dest.id
        webhook = await hunt_archive.ensure_webhook(dest)

        categories = self._hunt_categories(guild, hunt)
        general = guild.get_channel(hunt.channel_id)
        general_category_id = (general.category.id
                               if general is not None and general.category is not None else None)
        # The general channel is where admins usually run this: take it last.
        ordered = ([c for c in categories if c.id != general_category_id] +
                   [c for c in categories if c.id == general_category_id])
        await self._sweep_hunt_categories(
            guild, dest, webhook, ordered, report,
            keep_channel_id=None, keep_category_id=None, on_progress=on_progress)

        self._drop_split_layout_rows(hunt)

        # Re-home. hunts.guild_id is the guilds ROW id, not a snowflake. role_id
        # stays as a historical record: archived hunts never reach the role check,
        # and 0 would mean "legacy hunt".
        team_guild_row_id = hunt.guild_id
        hunt.guild_id = hub_row.id
        hunt.channel_id = dest.id
        hunt.archived = True
        HuntJsonDb.commit(hunt)

        # Leave only a clean, unreferenced server: on failure an admin must be able
        # to re-run from a surviving channel.
        if not report.failures and HuntJsonDb.count_for_guild(team_guild_row_id) == 0:
            try:
                await guild.leave()
                report.left_guild = True
            except discord.HTTPException:
                logger.exception("archive_hunt_to_hub: could not leave team guild %s", guild.id)
        return report

    # -- Hunt settings -------------------------------------------------

    _UNSET = object()

    @pushes_board()
    async def update_hunt_settings(self, hunt, *, url: Any = _UNSET, start_timestamp: Any = _UNSET,
                             username: Any = _UNSET, password: Any = _UNSET, url_sep: Any = _UNSET,
                             puzzle_prefix: Any = _UNSET):
        """Apply whitelisted per-hunt settings and commit. Only provided keys change."""
        if url is not self._UNSET:
            hunt.url = urls.normalize_external_url(url)
        if start_timestamp is not self._UNSET:
            hunt.start_timestamp = start_timestamp
            hunt.start_time = datetime.datetime.fromtimestamp(start_timestamp, tz=pytz.UTC)
        if username is not self._UNSET:
            hunt.username = username
        if password is not self._UNSET:
            hunt.password = password
        if url_sep is not self._UNSET:
            hunt.url_sep = url_sep
        if puzzle_prefix is not self._UNSET:
            hunt.puzzle_prefix = puzzle_prefix
        HuntJsonDb.commit(hunt)
        return hunt

