"""Ctx-free service layer for puzzle operations (M3).

The canonical implementation of each puzzle operation lives here. The Discord
bang-commands and the web dashboard are thin edges that call into this service;
the service does DB + Google Sheets + intrinsic Discord mutations and RAISES on
failure. Presentation (embeds, confirmations) and error-tolerance policy stay at
the edges.
"""
import asyncio
import contextvars
from contextlib import AsyncExitStack
import datetime
import functools
import inspect
import logging
from typing import Any, Callable

import discord
import pytz
from googleapiclient.errors import HttpError

from bot.store import PuzzleJsonDb, TagGroupJsonDb, HuntJsonDb, GuildSettingsDb, SheetsJsonDb, RollupsJsonDb, EvidenceJsonDb, TagGroupCategoryDb, HuntCategoryDb  # noqa: F401  # GuildSettingsDb kept as a tests' patch target
from bot.core import category_layout, hunt_archive
from bot.core.constants import STATUSES, PRIORITIES
from bot.store.puzzle_data import PuzzleData, AdditionalSheetData
from bot.store.rollup_data import RollupData
from bot.store.tag_group_data import TagGroupData
from bot.store.hunt_data import HuntData
from bot.core.locks import rollup_lock, puzzle_lock
from bot.core.errors import SheetRefreshError
from bot.utils import urls
from bot.core.discord_channels import (
    clean_channel_name, clean_category_name, get_or_create_channel, solve_divider_position, create_forum_post,
    MAX_CHANNELS_PER_GUILD, MAX_CATEGORIES,
    SOLVE_CATEGORY, SOLVE_DIVIDER,
)
from bot.core.reconcile import ReconcileReport, ReconcileAction  # noqa: F401
from bot.core.target_glyph import glyphed_name
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


def resolve_puzzle_names(puzzle_ids):
    """Return a {puzzle_id: name} map with a 'Puzzle {id}' fallback for unknown ids.

    Does a single PuzzleJsonDb.get_by_attr lookup per id (no double-lookup).
    """
    names = {}
    for pid in puzzle_ids:
        p = PuzzleJsonDb.get_by_attr(id=pid)
        names[pid] = p.name if p else f"Puzzle {pid}"
    return names


def is_fed(tag_group_id: int, puzzles_in_group: list) -> bool:
    """Return True if any member of the group has a 'feeds' edge to it.

    A group with any feeds-edge member is a 'meta' in the old vocabulary.
    puzzles_in_group is the list returned by PuzzleJsonDb.get_all_from_tag_group.
    """
    return any(
        p.has_edge(tag_group_id, "feeds")
        for p in puzzles_in_group
    )


def is_informed(tag_group_id: int, puzzles_in_group: list) -> bool:
    """Return True if any member of the group has an 'informs' edge to it.

    A group with any informs-edge member is a 'capstone' in the design vocabulary.
    """
    return any(
        p.has_edge(tag_group_id, "informs")
        for p in puzzles_in_group
    )


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

    def desired_channel_name(self, hunt, puzzle, group=None):
        """Canonical Discord channel name: the target glyph (if any) prefixed to
        clean_channel_name(puzzle.name)."""
        kind = self._target_kind(hunt, puzzle, group=group)
        return glyphed_name(kind, clean_channel_name(puzzle.name))

    async def _refresh_channel_glyph(self, guild, hunt, puzzle):
        """Rename *puzzle*'s channel to its desired glyphed name if it differs."""
        if guild is None or not getattr(puzzle, "channel_id", 0):
            return
        channel = guild.get_channel_or_thread(puzzle.channel_id)
        if channel is None:
            return
        want = self.desired_channel_name(hunt, puzzle)
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
            want = self.desired_channel_name(hunt, puzzle, group=group)
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

    def _home_category(self, guild, puzzle):
        """Discord category the puzzle's single channel belongs in = tags[0]'s group.
        Returns (category_or_None, home_group_or_None)."""
        if not puzzle.tags:
            return None, None
        home_group = TagGroupJsonDb.get_by_attr(id=puzzle.tags[0])
        if home_group is None or not home_group.category_id:
            return None, home_group
        category = discord.utils.get(guild.categories, id=home_group.category_id)
        return category, home_group

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
        category, home_group = self._home_category(guild, puzzle)
        want_name = self.desired_channel_name(hunt, puzzle)

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

    async def _reconcile_tabs(self, hunt, puzzle, *, apply):
        """Ensure the puzzle's Sheets tab(s) exist. Recreate a missing tab and write
        the fresh sheetId back to the DB. No-op without a GoogleSheets cog."""
        actions = []
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return actions
        spreadsheet = (gsheet_cog.get_solved_sheet_id() if puzzle.on_solved_sheet
                       else gsheet_cog.get_spreadsheet_id())

        async def _tab_present(page_id):
            if not page_id:
                return False
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
        hunt_category_ids = frozenset(r.category_id for r in rounds if r.category_id)

        for puzzle in puzzles:
            try:
                for action in await self._reconcile_channel(
                        guild, hunt, puzzle, report, apply=apply,
                        hunt_category_ids=hunt_category_ids):
                    report.actions.append(action)
                for action in await self._reconcile_tabs(hunt, puzzle, apply=apply):
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

        self._collect_orphan_channels(guild, rounds, puzzles, report)
        await self._collect_orphan_tabs(hunt, puzzles, report)
        return report

    def _collect_orphan_channels(self, guild, rounds, puzzles, report):
        valid_ids = {p.channel_id for p in puzzles if p.channel_id}
        category_ids = {r.category_id for r in rounds if r.category_id}
        for category in guild.categories:
            if category.id not in category_ids:
                continue
            for channel in getattr(category, "channels", []):
                if getattr(channel, "type", "text") != "text":
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
        evidence = [
            {"id": e.id, "source_puzzle_id": e.source_puzzle_id, "content": e.note}
            for e in EvidenceJsonDb.get_all(rollup.hunt_id, target_puzzle_id=metapuzzle.id)
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
            raise SheetRefreshError(failed)

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
            raise SheetRefreshError(failed)

    @pushes_board()
    async def bulk_re_edge(self, tag_group, new_edge, hunt):
        """Flip every member of *tag_group* to *new_edge*.

        Returns a human-readable summary string, e.g.
        ``'flipped 3 feeds + 2 none → informs'`` or ``'0 members'``.
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

        # Hold every member's puzzle mutex for the flip. Without this, a command
        # already in flight on a member (e.g. !solve, loaded pre-flip) can commit
        # after bulk_update_edge_type runs; its bidirectional tag-sync then diffs
        # against its stale in-memory edge and silently reverts the flip for that
        # member (#27). Sorted for a consistent lock order across callers.
        async with AsyncExitStack() as stack:
            for member_id in sorted({m.id for m in members}):
                await stack.enter_async_context(puzzle_lock(member_id))

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
        tag_group = TagGroupJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
        already_grouped = tag_group is not None

        if tag_group is None:
            # Bare puzzle — promote in-place (no Discord channel creation)
            tag_group = TagGroupData(
                name=puzzle.name,
                hunt_id=hunt.id,
                category_id=puzzle.channel_id,
                type=new_type,
            )
            TagGroupJsonDb.commit(tag_group)
            tag_group.target_puzzle_id = puzzle.id
            tag_group.group_code = TagGroupJsonDb.generate_uid("group_code", 6, puzzle.name)
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

        # Set source_mode from defaults
        default_sm = TYPE_DEFAULTS[new_type]["default_source_mode"]
        await self.set_source_mode(hunt, tag_group, default_sm)

        # Flip edges (unless Runaround or edge is "none")
        if new_type != "Runaround":
            default_edge = TYPE_DEFAULTS[new_type]["default_edge"]
            summary = await self.bulk_re_edge(tag_group, default_edge, hunt)
        else:
            summary = "no edge change (runaround)"

        # Group has no metapuzzle: tear down the former target now that the edge
        # flip (which excludes the target's self-edge) has run. Only for a
        # PRE-EXISTING group — never the promote-in-place branch above, which just
        # created the group around this puzzle (that would delete it immediately).
        torn_down = already_grouped and new_type == "Group" and tag_group.target_puzzle_id == puzzle.id
        if torn_down:
            await self._teardown_group_target(guild, hunt, tag_group, puzzle)

        # Update forum post tags (structural type may have changed).
        await self._update_forum_tags(guild, hunt, puzzle)
        # Skip the glyph refresh when the target was just torn down: `puzzle` no
        # longer exists (its DB row was deleted) and its channel was repurposed
        # into the group's general chat, not renamed to a glyphed puzzle name.
        if not torn_down:
            await self._refresh_channel_glyph(guild, hunt, puzzle)

        return tag_group, summary

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
            chat = category_layout.group_chat_channel(guild, tag_group)

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
        PuzzleJsonDb.delete(target.id)
        tag_group.target_puzzle_id = 0
        TagGroupJsonDb.commit(tag_group)

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

        puzzle.status = "Solved"
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
        failed = []
        for tag in puzzle.tags:
            target_group = TagGroupJsonDb.get_by_attr(id=tag)
            try:
                await self.refresh_target(hunt, target_group)
            except Exception:
                failed.append(tag)
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
                failed.append(puzzle.id)

        PuzzleJsonDb.commit(puzzle)
        for sheet in puzzle.additional_sheets:
            SheetsJsonDb.commit(sheet)
        # Update forum post tags (solved status changed).
        await self._update_forum_tags(guild, hunt, puzzle)

        if failed:
            raise SheetRefreshError(failed)
        return True

    @pushes_board()
    async def unsolve(self, guild, hunt, puzzle):
        """Clear a puzzle's solve state and restore its sheet.

        For mini-puzzles (parent_puzzle_id set), also unarchives the thread.
        """
        puzzle.status = "Unsolved"
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
        for tag in puzzle.tags:
            try:
                target_group = TagGroupJsonDb.get_by_attr(id=tag)
                if target_group is not None:
                    await self.refresh_target(hunt, target_group)
            except Exception:
                logger.exception("unsolve: meta refresh failed for group %s", tag)

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

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
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
        """Delete a Discord channel by id, tolerating already-missing or rate-limited.
        Returns True if deleted, False otherwise. Guild=None is a no-op."""
        if guild is None or not channel_id:
            return False
        channel = guild.get_channel(channel_id)
        if channel is None:
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
            raise SheetRefreshError(failed)

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
            # A member puzzle that is itself a DIFFERENT tag group's target (a
            # nested meta whose target puzzle also happens to feed this round)
            # must never be deleted here: that would orphan the nested group's
            # target_puzzle_id, the same case delete_puzzle forbids outright.
            # Only this round's own target (if any) is fair game for full delete.
            # Applies to forum layout too (which always takes this branch,
            # since keep_puzzles never yields narrow=True for forum).
            to_delete = [
                p for p in all_puzzles
                if not p.is_target or p.id == tag_group.target_puzzle_id
            ]
            to_keep = [p for p in all_puzzles if p not in to_delete]

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
                if not await self._relocate_kept_puzzle(guild, hunt, tag_group, puzzle, channel):
                    failed_channels.append(puzzle.name)
                    relocation_failed_channel_ids.add(puzzle.channel_id)

        if narrow and guild is not None:
            group_chat_ch = category_layout.group_chat_channel(guild, tag_group)
            if group_chat_ch is not None:
                if not await self._safe_delete_channel(guild, group_chat_ch.id):
                    failed_channels.append(tag_group.name)

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

    async def _relocate_kept_puzzle(self, guild, hunt, tag_group, puzzle, channel):
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
        puzzle.status = "Partially Solved"
        if puzzle.solution:
            puzzle.solution += "/" + solution
        else:
            puzzle.solution = solution
        PuzzleJsonDb.commit(puzzle)

        tag_groups = [TagGroupJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed = await self.refresh_targets(hunt, tag_groups)
        if failed:
            raise SheetRefreshError(failed)

    @pushes_board()
    async def change_solution(self, guild, hunt, puzzle, solution):
        """Replace the solution on an already-solved puzzle: set new solution,
        stamp solve_time, commit, refresh metas and push to Google Sheets."""
        solution = solution.strip().upper()
        puzzle.solution = solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(puzzle)

        tag_groups = [TagGroupJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed = await self.refresh_targets(hunt, tag_groups)
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            # Failure isolation (same reasoning as solve()'s archive call): the
            # solution change is already committed, so a Sheets failure here must
            # not swallow any metas already collected in `failed` above, nor skip
            # the board push that tells the dashboard the mutation applied.
            try:
                await gsheet_cog.update_solution(puzzle)
            except Exception:
                logger.exception("change_solution: failed to update sheet for puzzle %s", puzzle.id)
                failed.append(puzzle.id)

        if failed:
            raise SheetRefreshError(failed)

    @pushes_board()
    async def add_solution(self, guild, hunt, puzzle, solution):
        """Append a solution to an already-solved puzzle: append with slash,
        stamp solve_time, commit, refresh metas and push to Google Sheets."""
        solution = solution.strip().upper()
        puzzle.solution += "/" + solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(puzzle)

        tag_groups = [TagGroupJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed = await self.refresh_targets(hunt, tag_groups)
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            try:
                await gsheet_cog.update_solution(puzzle)
            except Exception:
                logger.exception("add_solution: failed to update sheet for puzzle %s", puzzle.id)
                failed.append(puzzle.id)

        if failed:
            raise SheetRefreshError(failed)

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
        elif field == "priority":
            if value.lower() not in {p.lower() for p in PRIORITIES}:
                raise ValueError(f"Invalid priority {value!r}; must be one of {PRIORITIES}")

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
        # Guard on the Table actually existing — a minimeta made but never rendered has
        # no Table, and demoting it would delete the solver grid.
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None and puzzle.google_page_id:
            sheets = (await gsheet_cog.sheet_list(gsheet_cog.get_spreadsheet_id()))["sheets"]
            has_table = any(
                bool(s.get("tables"))
                for s in sheets
                if s["properties"]["sheetId"] == int(puzzle.google_page_id)
            )
            if has_table:
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
        )
        TagGroupJsonDb.commit(new_round)
        new_round.group_code = TagGroupJsonDb.generate_uid("group_code", 6, puzzle.name)
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
            unsolved = [p for p in puzzles if p.status != "solved"]
            solved = [p for p in puzzles if p.status == "solved"]
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

        # Consolidate each vacated source so the previous owner folds/un-splits.
        for cid in sources:
            src = category_layout._category_by_id(guild, cid)
            if src is not None:
                category_layout.maybe_consolidate_category(guild, src)
        return new_category

    @pushes_board()
    async def rename_puzzle(self, guild, hunt, puzzle, name):
        """Rename a puzzle: set name/channel_name, commit, refresh metas.

        Caller must check for duplicate names before calling. The Discord
        channel rename is guarded on `guild is not None` so that web calls
        (and tests with _no_guild) skip it.
        """
        puzzle.name = name
        puzzle.channel_name = self.desired_channel_name(hunt, puzzle)

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
            raise SheetRefreshError(failed)

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
            status='Unstarted',
            priority='Normal',
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
            status='Unstarted',
            priority='Normal',
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
                hunt, new_puzzle, group=round_data if is_meta else None)
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
            )
            # Initial commit so the row has an id (rollback safety).
            TagGroupJsonDb.commit(new_round)

            if is_group:
                # Group: no meta puzzle — but post a welcome.
                new_round.group_code = TagGroupJsonDb.generate_uid('group_code', 6, name)
                TagGroupJsonDb.commit(new_round)
                if notifier is not None:
                    await notifier.welcome(
                        guild, hunt, tag_group=new_round,
                        puzzle=None, channel=group_text_channel,
                    )
                return new_round, None

            # Create the meta puzzle for this round (skip for Metaless variants).
            # Set group_code before create_puzzle so the welcome fires with a real code.
            new_round.group_code = TagGroupJsonDb.generate_uid('group_code', 6, name)
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
    async def log_clues(self, hunt, puzzle, notes: list) -> list:
        """Insert one evidence_item per note. Returns list of EvidenceData.

        If the source puzzle informs exactly one target, each clue is auto-routed
        to that informed target (target_puzzle_id) so it surfaces in the target's
        roll-up contribution table. Zero or several informs targets -> left
        untargeted for manual routing via the Evidence panel.
        """
        target_id = self._sole_informs_target(puzzle)
        items = []
        for note in notes:
            item = EvidenceJsonDb.log(hunt.id, puzzle.id, note)
            if target_id is not None:
                item = EvidenceJsonDb.set_target(item.id, target_id) or item
            items.append(item)
        return items

    async def refresh_evidence(self, hunt):
        """Render the Evidence sheet tab as a native Table (mirrors Unassigned
        Feeders). Lazily creates the tab + Table, pins the tab just after Unassigned
        Feeders, and reconciles rows keyed on the hidden evidence id.
        """
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return

        # Lazy-create the tab + native Table. ensure_evidence_table pins the tab to
        # index 1 (after Unassigned Feeders), writes the header and wraps it in a
        # Table, and returns the table id to persist on the hunt.
        dirty = False
        if hunt.evidence_sheet_page_id is None:
            page_id = await gsheet_cog.create_blank_tab(hunt.google_sheet_id, "Evidence")
            hunt.evidence_sheet_page_id = page_id
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
        puzzle_ids = {i.source_puzzle_id for i in items} | {i.target_puzzle_id for i in items if i.target_puzzle_id}
        names = resolve_puzzle_names(puzzle_ids)
        rows = [
            [
                i.id,
                names.get(i.source_puzzle_id, f"Puzzle {i.source_puzzle_id}"),
                i.note,
                names.get(i.target_puzzle_id, "") if i.target_puzzle_id else "",
                str(i.created_at)[:16] if i.created_at else "",
            ]
            for i in items
        ]
        await gsheet_cog.reconcile_table(
            page_id=hunt.evidence_sheet_page_id,
            desired_rows=rows,
            data_col_count=5,
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
        is target_puzzle_id, so routed evidence renders. Never evicts feeds. Best-effort."""
        if not (source_puzzle_id and target_puzzle_id):
            return
        group = TagGroupJsonDb.get_by_attr(target_puzzle_id=target_puzzle_id)
        source = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
        if group is None or source is None:
            return
        if not source.has_edge(group.id, "informs"):
            await self.tag_with_edge(source, group, "informs")

    async def unlink_informs_for_evidence(self, hunt, source_puzzle_id, target_puzzle_id):
        """Remove the auto-added 'informs' edge only if the source has no remaining
        evidence routed to that target (a manual informs is preserved)."""
        if not (source_puzzle_id and target_puzzle_id):
            return
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

        # Lazy-create tab + table
        dirty = False
        if hunt.unassigned_sheet_page_id is None:
            try:
                page_id = await gsheet_cog.create_blank_tab(hunt.google_sheet_id, tab_name)
            except Exception:
                # A tab with this name may already exist from an earlier run —
                # reuse it rather than failing (addSheet errors on duplicate names).
                page_id = await gsheet_cog.get_page_id_by_name(tab_name)
                if page_id is None:
                    raise
            hunt.unassigned_sheet_page_id = page_id
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
        if HuntJsonDb.check_duplicates(name):
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

        # Delete each puzzle's sheet tab + DB row + Discord channel
        puzzles = PuzzleJsonDb.get_all_from_hunt(hunt.id)
        for puzzle in puzzles:
            gsheet_cog = self._gsheet_cog(hunt)
            if gsheet_cog is not None:
                try:
                    await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
                except Exception:
                    logger.exception("delete_hunt: sheet delete failed for puzzle %s", puzzle.id)
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

        HuntJsonDb.delete(hunt.id)
        return failed_channels

    def _archive_thread_exists(self, dest, thread_name):
        """True if an active thread with *thread_name* already exists on *dest* —
        used to make archive_hunt resumable after a partial run."""
        return any(getattr(t, "name", None) == thread_name for t in getattr(dest, "threads", []))

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

        # Every category the hunt owns: main + solved-by-name + any split/overflow
        # categories the hunt's own general container owns + each group's categories.
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

        dest_category_id = dest.category.id if dest.category is not None else None
        categories = [c for c in
                      (discord.utils.get(guild.categories, id=cid) for cid in category_ids)
                      if c is not None]
        # Process dest's own category last so we never strand the surviving channel.
        ordered = ([c for c in categories if c.id != dest_category_id] +
                   [c for c in categories if c.id == dest_category_id])

        total = sum(1 for c in ordered for ch in c.channels if ch.id != dest.id)
        done = 0
        for category in ordered:
            for ch in list(category.channels):
                if ch.id == dest.id:
                    continue
                done += 1
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
                if on_progress is not None:
                    await on_progress(done, total, getattr(ch, "name", ""))
            if category.id != dest_category_id:
                try:
                    await category.delete(reason=self._DISCORD_DELETE_REASON)
                    report.categories_deleted += 1
                except Exception:
                    logger.exception("archive_hunt: could not delete category %s", category.id)
                    report.failures.append(getattr(category, "name", str(category.id)))

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

        # Archive keeps every hunt/tag_group DB row (only the flag below
        # changes), so no FK cascade ever fires — drop the split-layout join
        # rows explicitly, or they point at the deleted categories forever and
        # is_split() stays true for the archived hunt and every archived round.
        HuntCategoryDb.delete_for_group(hunt.id)
        for tg in TagGroupJsonDb.get_all(hunt.id):
            TagGroupCategoryDb.delete_for_group(tg.id)

        hunt.archived = True
        HuntJsonDb.commit(hunt)
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

