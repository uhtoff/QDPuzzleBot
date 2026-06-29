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

import discord
import pytz
from googleapiclient.errors import HttpError

from bot.store import PuzzleJsonDb, TagGroupJsonDb, HuntJsonDb, GuildSettingsDb, SheetsJsonDb, RollupsJsonDb, EvidenceJsonDb, TagGroupCategoryDb  # noqa: F401  # GuildSettingsDb kept as a tests' patch target
from bot.core import category_layout
from bot.core.constants import STATUSES, PRIORITIES
from bot.store.puzzle_data import PuzzleData, AdditionalSheetData
from bot.store.rollup_data import RollupData
from bot.store.tag_group_data import TagGroupData
from bot.store.hunt_data import HuntData
from bot.core.locks import rollup_lock
from bot.core.errors import SheetRefreshError
from bot.utils import urls
from bot.core.discord_channels import (
    clean_name, get_or_create_channel, solve_divider_position, create_forum_post,
    MAX_CHANNELS_PER_GUILD, MAX_CHANNELS_PER_CATEGORY, MAX_CATEGORIES,
    SOLVE_CATEGORY, SOLVE_DIVIDER,
)
from bot.core.reconcile import ReconcileReport, ReconcileAction  # noqa: F401
from bot.core.forum_config import desired_tags_for, apply_forum_tags  # noqa: F401  # forum_config kept as a tests' import target

logger = logging.getLogger(__name__)


# Re-entrancy depth for board-push dedupe. A contextvar (not an instance attr) so
# concurrent operations in separate asyncio tasks each get an independent count.
_board_push_depth = contextvars.ContextVar("_board_push_depth", default=0)


def pushes_board(hunt="hunt", when=None):
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

        wrapper._pushes_board = True
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
            cog.set_archive_spreadsheet_id(hunt.archive_google_sheet_id)
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

    async def _reconcile_channel(self, guild, hunt, puzzle, report, *, apply):
        """Make the puzzle's Discord channel match the DB. Returns the list of
        actions taken/needed. Resolution order: keep+fix existing > relink named >
        create. Welcome is reposted ONLY when a brand-new channel is created."""
        # Mini-puzzles live as threads inside their minimeta's channel (see
        # create_mini) and are deliberately untagged. Reconcile has no
        # thread-creation path, so a mini here would be wrongly promoted to a
        # standalone text channel — skip channel reconciliation for them.
        if puzzle.parent_puzzle_id is not None:
            return []
        actions = []
        category, home_group = self._home_category(guild, puzzle)
        want_name = clean_name(puzzle.name)

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

        # 2. A channel with the right name already exists somewhere: relink + move.
        existing = discord.utils.get(guild.channels, name=want_name)
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
        spreadsheet = (gsheet_cog.get_archive_spreadsheet_id() if puzzle.archived
                       else gsheet_cog.get_spreadsheet_id())

        def _tab_present(page_id):
            if not page_id:
                return False
            return gsheet_cog.get_page_name_by_id(page_id, spreadsheet) is not None

        if not _tab_present(puzzle.google_page_id):
            actions.append(ReconcileAction("tab_create", puzzle.name, "main tab missing"))
            if apply:
                existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
                insert_idx = gsheet_cog.compute_insert_index(
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

        for puzzle in puzzles:
            try:
                for action in await self._reconcile_channel(guild, hunt, puzzle, report, apply=apply):
                    report.actions.append(action)
                for action in await self._reconcile_tabs(hunt, puzzle, apply=apply):
                    report.actions.append(action)
            except Exception as error:  # noqa: BLE001
                logger.exception("reconcile: failed for puzzle %s", puzzle.name)
                report.add_failure(puzzle.name, error)

        rounds = TagGroupJsonDb.get_all(hunt.id)
        for tag_group in rounds:
            if tag_group.category_id and discord.utils.get(
                    guild.categories, id=tag_group.category_id) is None:
                report.add("round_category_broken",
                           getattr(tag_group, "group_code", None) or str(tag_group.id),
                           f"category {tag_group.category_id} missing")

        self._collect_orphan_channels(guild, rounds, puzzles, report)
        self._collect_orphan_tabs(hunt, puzzles, report)
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

    def _collect_orphan_tabs(self, hunt, puzzles, report):
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return
        spreadsheet = gsheet_cog.get_spreadsheet_id()
        valid = {str(p.google_page_id) for p in puzzles if p.google_page_id and not p.archived}
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
            sheets = gsheet_cog.sheet_list(spreadsheet)["sheets"]
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

        # Move channel for feeds edges (backward compat with move_to_meta)
        if edge == "feeds" and guild is not None and puzzle.channel_id:
            channel = guild.get_channel(puzzle.channel_id)
            # group.category_id is the group's "home container" id, which is only a
            # real Discord category for channel-layout groups; primary_category
            # resolves it as a category (None for forum/group/in-place homes — the
            # move is then skipped, as before).
            new_category = category_layout.primary_category(guild, group)
            if channel is not None and new_category is not None and channel.category_id != new_category.id:
                # Position: unsolved above divider, solved below divider, target at position 0
                from bot.core.discord_channels import solve_divider_position
                divider_pos = solve_divider_position(new_category)
                if puzzle.solved and divider_pos is not False:
                    # Place solved puzzles at the end (after the divider)
                    position = max((ch.position for ch in new_category.channels), default=0) + 1
                elif divider_pos is not False:
                    # Place unsolved just above the divider
                    position = divider_pos - 1
                else:
                    # No divider (single-category layout) — place after target (position 2)
                    position = 2
                await channel.edit(category=new_category, position=position)

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

        PuzzleJsonDb.bulk_update_edge_type(tag_group.id, new_edge)
        # bulk_update flips every row including the target's — re-pin the target to
        # 'target' so a type conversion can never make a target feed/inform itself.
        if tag_group.target_puzzle_id:
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

        # Update forum post tags (structural type may have changed).
        await self._update_forum_tags(guild, hunt, puzzle)

        return tag_group, summary

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
            await gsheet_cog.archive_puzzle_spreadsheet(puzzle)
            puzzle.archive_time = datetime.datetime.now(tz=pytz.UTC)

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
        puzzle.archive_time = None
        PuzzleJsonDb.commit(puzzle)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            # Exclude this puzzle from the zone computation: its sheet is still at
            # the solved end (not yet moved), so including it would anchor the zone
            # at the wrong position.
            existing = [p for p in PuzzleJsonDb.get_all_from_hunt(hunt.id) if p.id != puzzle.id]
            restore_idx = gsheet_cog.compute_insert_index(puzzle.is_target, existing)
            await gsheet_cog.restore_puzzle_spreadsheet(puzzle, restore_index=restore_idx)

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
            TagGroupJsonDb.delete(target_group.id)
            if target_group.id in puzzle.tags:
                puzzle.tags.remove(target_group.id)
        puzzle.is_target = False
        PuzzleJsonDb.commit(puzzle)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.demote_tab_to_puzzle(puzzle)
        return orphaned

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
        await self._safe_delete_channel(guild, puzzle.channel_id)
        if failed:
            raise SheetRefreshError(failed)

    @pushes_board()
    async def delete_round(self, guild, hunt, tag_group):
        """Delete all puzzles in a group (sheet + DB + Discord channel per puzzle),
        delete the group's Discord category (including its solved-divider channel),
        and delete the group DB record. Per-puzzle failures are isolated: one failure
        is logged and the run continues. Guild may be None (skips all Discord deletions).
        Returns the list of puzzle names whose Discord channels could not be deleted."""
        puzzles = PuzzleJsonDb.get_all_from_tag_group(tag_group.id)
        failed_channels = []
        for puzzle in puzzles:
            gsheet_cog = self._gsheet_cog(hunt)
            if gsheet_cog is not None:
                try:
                    await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
                except Exception:
                    logger.exception("delete_round: sheet delete failed for puzzle %s", puzzle.id)
            PuzzleJsonDb.delete(puzzle.id)
            if guild is not None and puzzle.channel_id:
                if not await self._safe_delete_channel(guild, puzzle.channel_id):
                    failed_channels.append(puzzle.name)

        TagGroupJsonDb.delete(tag_group.id)

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
                if not await category_layout.delete_group_categories(
                    guild, tag_group, TagGroupCategoryDb
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
        return failed_channels

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
            await gsheet_cog.update_solution(puzzle)

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
            await gsheet_cog.update_solution(puzzle)

        if failed:
            raise SheetRefreshError(failed)

    @pushes_board()
    async def update_puzzle_field(self, hunt, puzzle, field, value):
        """Set status, priority, or puzzle_type on *puzzle* and commit.

        Validates *field* against the allowed set and validates *value* against
        the known constants for status and priority.  Raises ValueError on bad
        field or bad value.  puzzle_type is free-form (no value validation).
        """
        _VALID_FIELDS = {"status", "priority", "puzzle_type"}
        if field not in _VALID_FIELDS:
            raise ValueError(f"Unknown puzzle field {field!r}; must be one of {sorted(_VALID_FIELDS)}")

        if field == "status":
            if value.lower() not in {s.lower() for s in STATUSES}:
                raise ValueError(f"Invalid status {value!r}; must be one of {STATUSES}")
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

        return new_round, puzzle

    @pushes_board()
    async def rename_puzzle(self, guild, hunt, puzzle, name):
        """Rename a puzzle: set name/channel_name, commit, refresh metas.

        Caller must check for duplicate names before calling. The Discord
        channel rename is guarded on `guild is not None` so that web calls
        (and tests with _no_guild) skip it.
        """
        puzzle.name = name
        puzzle.channel_name = clean_name(name)

        # Rename the Discord channel (skip when guild is None — web + test path).
        if guild is not None and puzzle.channel_id:
            channel = guild.get_channel(puzzle.channel_id)
            if channel is not None:
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
            insert_idx = gsheet_cog.compute_insert_index(False, existing)  # minis are regular
            await gsheet_cog.create_puzzle_spreadsheet(mini, index=insert_idx)

        PuzzleJsonDb.commit(mini)

        channel = guild.get_channel(minimeta.channel_id)
        thread = await channel.create_thread(
            name=clean_name(name),
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
            category_id = hunt.category_id
            category = discord.utils.get(guild.categories, id=category_id) if category_id else None

        # Guild-wide ceiling still applies (overflow can't conjure channel slots).
        # Threads don't count toward the 500 cap.
        if hunt.layout_mode != "forum" and len(guild.channels) >= MAX_CHANNELS_PER_GUILD:
            raise RuntimeError("guild_full")
        # Hunt category (no round) keeps the simple hard guard.
        if round_data is None and category is not None and len(category.channels) >= MAX_CHANNELS_PER_CATEGORY:
            raise RuntimeError("category_full")

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
            hunt_url_base = hunt.url.rstrip("/")
            p_name = clean_name(name).replace("-", hunt.url_sep)
            new_puzzle.url = f"{hunt_url_base}/{prefix}/{p_name}"
        if round_data is not None:
            new_puzzle.tags.append(round_data.id)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
            insert_idx = gsheet_cog.compute_insert_index(new_puzzle.is_target, existing)
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
            channel_name = clean_name(new_puzzle.name)
            if hunt.layout_mode == "forum":
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

        new_category_name = clean_name(name)
        existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category:
            new_category_name = new_category_name + " — " + clean_name(hunt.name)
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
                    channel_name=new_category_name, channel_type="text",
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
                parent_id = (
                    group_text_channel.id if hunt.layout_mode == "forum"
                    else new_category.id
                )
            elif hunt.layout_mode == "forum":
                parent_id = forum_channel.id
            else:
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

    def log_clues(self, hunt, puzzle, notes: list) -> list:
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
                page_id = gsheet_cog.get_page_id_by_name(tab_name)
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

    async def create_hunt(self, guild, *, name, url, start_timestamp,
                          role_name=None, username=None, password=None,
                          create_archive_sheet=False, layout_mode="channel",
                          guild_id=0):
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

        team_role = None
        if role_name:
            team_role = discord.utils.get(guild.roles, name=role_name)
            if team_role is None:
                team_role = await guild.create_role(
                    name=role_name, colour=discord.Colour.random(),
                    mentionable=True, reason="bot-role",
                )

        # Category is brand-new, so channels are created directly (no get_or_create lookup).
        category_name = clean_name(name)
        category = await guild.create_category(
            category_name, position=max(len(guild.categories) - 2, 0)
        )
        text_channel = await guild.create_text_channel(
            "general", category=category, reason="bot-hunt-general", position=0
        )
        if SOLVE_CATEGORY:
            await guild.create_category(
                f"{name}-solved", position=max(len(guild.categories) - 2, 0)
            )
        else:
            await guild.create_text_channel(
                SOLVE_DIVIDER, category=category, reason="bot-round", position=500
            )

        gsheet_cog = self._gsheet_cog(None)
        google_sheet_id = ""
        archive_google_sheet_id = ""
        if gsheet_cog is not None:
            google_sheet_id = await gsheet_cog.create_hunt_spreadsheet(name)
            if create_archive_sheet:
                archive_google_sheet_id = await gsheet_cog.create_hunt_archive_spreadsheet(name)

        new_hunt = HuntData(
            name=name,
            category_id=category.id,
            channel_id=text_channel.id,
            guild_id=guild_id,
            url=url,
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
        if archive_google_sheet_id:
            new_hunt.archive_google_sheet_id = archive_google_sheet_id

        HuntJsonDb.commit(new_hunt)
        return new_hunt

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
            TagGroupJsonDb.delete(tag_group.id)
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

        # Delete spreadsheet files (whole Drive file, not just tabs)
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            if hunt.google_sheet_id:
                await gsheet_cog.delete_hunt_spreadsheet(hunt.google_sheet_id)
            if hunt.archive_google_sheet_id:
                await gsheet_cog.delete_hunt_spreadsheet(hunt.archive_google_sheet_id)

        # Delete hunt channel + category (best-effort; failures logged, not raised)
        if guild is not None:
            if hunt.channel_id:
                try:
                    await self._safe_delete_channel(guild, hunt.channel_id)
                except Exception:
                    logger.exception("delete_hunt: could not delete hunt channel %s", hunt.channel_id)
            if hunt.category_id:
                category = discord.utils.get(guild.categories, id=hunt.category_id)
                if category is not None:
                    try:
                        await category.delete(reason=self._DISCORD_DELETE_REASON)
                    except Exception:
                        logger.exception("delete_hunt: could not delete hunt category %s", hunt.category_id)

        HuntJsonDb.delete(hunt.id)
        return failed_channels

    # -- Hunt settings -------------------------------------------------

    _UNSET = object()

    def update_hunt_settings(self, hunt, *, url=_UNSET, start_timestamp=_UNSET,
                             username=_UNSET, password=_UNSET, url_sep=_UNSET,
                             puzzle_prefix=_UNSET):
        """Apply whitelisted per-hunt settings and commit. Only provided keys change."""
        if url is not self._UNSET:
            hunt.url = url
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

