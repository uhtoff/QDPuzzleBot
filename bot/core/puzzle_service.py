"""Ctx-free service layer for puzzle operations (M3).

The canonical implementation of each puzzle operation lives here. The Discord
bang-commands and the web dashboard are thin edges that call into this service;
the service does DB + Google Sheets + intrinsic Discord mutations and RAISES on
failure. Presentation (embeds, confirmations) and error-tolerance policy stay at
the edges.
"""
import asyncio
import datetime
import logging

import discord
import pytz
from googleapiclient.errors import HttpError

from bot.store import PuzzleJsonDb, RoundJsonDb, HuntJsonDb, GuildSettingsDb, SheetsJsonDb, RollupsJsonDb, EvidenceJsonDb  # noqa: F401  # GuildSettingsDb kept as a tests' patch target
from bot.store.puzzle_data import PuzzleData, AdditionalSheetData
from bot.store.rollup_data import RollupData
from bot.store.round_data import RoundData
from bot.core.locks import rollup_lock
from bot.core.errors import SheetRefreshError
from bot.utils import urls
from bot.core.discord_channels import (
    clean_name, get_or_create_channel, solve_divider_position,
    MAX_CHANNELS_PER_GUILD, MAX_CHANNELS_PER_CATEGORY, MAX_CATEGORIES,
)
from bot.core.reconcile import ReconcileReport, ReconcileAction  # noqa: F401

logger = logging.getLogger(__name__)


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

    # --- !reconcile ---

    def _home_category(self, guild, puzzle):
        """Discord category the puzzle's single channel belongs in = tags[0]'s round.
        Returns (category_or_None, home_round_or_None)."""
        if not puzzle.tags:
            return None, None
        home_round = RoundJsonDb.get_by_attr(id=puzzle.tags[0])
        if home_round is None or not home_round.category_id:
            return None, home_round
        category = discord.utils.get(guild.categories, id=home_round.category_id)
        return category, home_round

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
        category, home_round = self._home_category(guild, puzzle)
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
                    guild, hunt, hunt_round=home_round, puzzle=puzzle, channel=text_channel,
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
                await gsheet_cog.add_new_puzzle_sheet(puzzle, gsheet_cog.INITIAL_OFFSET)
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

        rounds = RoundJsonDb.get_all(hunt.id)
        for hunt_round in rounds:
            if hunt_round.category_id and discord.utils.get(
                    guild.categories, id=hunt_round.category_id) is None:
                report.add("round_category_broken",
                           getattr(hunt_round, "meta_code", None) or str(hunt_round.id),
                           f"category {hunt_round.category_id} missing")

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
        "REFERENCE", "Unassigned Puzzles", "Evidence",
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

    async def refresh_meta(self, hunt, hunt_round):
        """Rebuild a meta (or meta-meta) roll-up sheet, tracking the outcome on the
        round's `rollups` record (find-or-create). On success: last_ok=1, error
        cleared. On a Sheets error: last_ok=0 + last_error persisted, then RE-RAISES
        so the caller can surface a toast — the durable badge is recorded either way.

        Serialized per round via rollup_lock so only one writer touches a given meta
        sheet at a time; the DB read happens INSIDE the lock so each holder renders
        post-commit state (no lost update against a concurrent !rebuild)."""
        if hunt is None or hunt_round is None or not hunt_round.meta_id:
            return
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            # No sheets in play (tests / cog unloaded): nothing to render or badge.
            return

        async with rollup_lock(hunt_round.id):
            metapuzzle = PuzzleJsonDb.get_by_attr(id=hunt_round.meta_id)
            if metapuzzle is None:
                return
            rollup = self._find_or_create_rollup(hunt_round)
            try:
                await self._render_rollup(gsheet_cog, hunt_round, rollup, metapuzzle)
                await self._record_rollup_tab(gsheet_cog, rollup, metapuzzle)
                self._mark_rollup(rollup, ok=True)
            except Exception as exc:
                self._mark_rollup(rollup, ok=False, error=str(exc))
                raise

    def _find_or_create_rollup(self, hunt_round):
        """Return the rollup row for this meta round, creating an unsaved one (with
        the current-meta defaults) if none exists yet. Not committed until
        _mark_rollup writes the refresh outcome."""
        rollup = RollupsJsonDb.get_by_attr(round_id=hunt_round.id)
        if rollup is None:
            rollup = RollupData(hunt_id=hunt_round.hunt_id, round_id=hunt_round.id)
        return rollup

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
                await gsheet_cog.add_metapuzzle_data(minimeta, members)
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

    async def select_rollup_members(self, hunt_round, rollup) -> list:
        """Select feeder membership by rollup.source_mode, returning the ordered
        list[PuzzleData] with NO Google-Sheet side-effects.

        Falsy/missing source_mode defaults to 'round'. Shared by _render_rollup so
        the sheet writer and any read path agree on membership."""
        mode = rollup.source_mode or "round"
        feeders = PuzzleJsonDb.get_all_from_round(hunt_round.id)

        if mode == "subtree":
            return await self.collect_subtree_puzzles(hunt_round)
        elif mode == "explicit":
            return [
                p for p in feeders
                if p.tag_edges.get(hunt_round.id) == "aggregates-into"
            ]
        else:
            # 'round' (default): all round members, optionally filtered by edge_type_filter.
            filt = rollup.edge_type_filter or "feeds-meta"
            return [
                p for p in feeders
                if p.tag_edges.get(hunt_round.id, "feeds-meta") == filt
            ]

    async def _render_rollup(self, gsheet_cog, hunt_round, rollup, metapuzzle):
        """Select feeder membership via select_rollup_members and delegate to the
        existing renderer (add_metapuzzle_data or add_metametapuzzle_data)."""
        members = await self.select_rollup_members(hunt_round, rollup)
        mode = rollup.source_mode or "round"

        if mode == "subtree":
            await gsheet_cog.add_metametapuzzle_data(metapuzzle, members)
        else:
            await gsheet_cog.add_metapuzzle_data(metapuzzle, members)

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
        ident = rollup.round_id if rollup.round_id else f"puzzle:{rollup.target_puzzle_id}"
        if rollup.metadata_id is None and rollup.tab_page_id is not None:
            try:
                _, rollup.metadata_id = await gsheet_cog.stamp_rollup_metadata(
                    metapuzzle, f"rollup:{ident}")
            except Exception:
                logger.exception(
                    "refresh_meta: developer-metadata stamp failed for %s "
                    "(non-fatal; google_page_id remains the pointer)", ident)

    async def refresh_metas(self, hunt, rounds):
        """Refresh several meta rounds with FAILURE ISOLATION: every round is
        attempted (a failure on one must not skip the others), each failure is
        already badged by refresh_meta, and the failed round ids are returned for the
        caller to surface. None rounds are skipped. This is the shared isolation path
        for solve / retag / the bang solution-edits / !rebuild."""
        failed = []
        for hunt_round in rounds:
            if hunt_round is None:
                continue
            try:
                await self.refresh_meta(hunt, hunt_round)
            except Exception:
                failed.append(hunt_round.id)
        return failed

    async def move_to_meta(self, guild, puzzle, meta_round):
        """Retag a puzzle to meta_round (replacing its tags unless it is itself a
        metapuzzle), move its channel into the meta's category, commit, and refresh
        the affected metas. The tag mutation always commits; if any meta sheet fails
        to refresh, each is badged and a SheetRefreshError is raised at the end."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        old_tags = list(puzzle.tags)
        if guild is not None and puzzle.channel_id:
            channel = guild.get_channel(puzzle.channel_id)
            new_category = discord.utils.get(guild.categories, id=meta_round.category_id)
            if channel is not None and new_category is not None and channel.category_id != new_category.id:
                await channel.edit(category=new_category, position=2)
        if puzzle.is_metapuzzle() is False:
            puzzle.tags.clear()
        puzzle.tags.append(meta_round.id)
        PuzzleJsonDb.commit(puzzle)
        affected = [meta_round] + [RoundJsonDb.get_by_attr(id=t) for t in old_tags]
        failed = await self.refresh_metas(hunt, affected)
        await self.notifier.board_changed(hunt)
        if failed:
            raise SheetRefreshError(failed)

    async def add_to_meta(self, puzzle, meta_round):
        """Tag a puzzle to an additional meta (idempotent), commit, refresh."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        if meta_round.id not in puzzle.tags:
            puzzle.tags.append(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
        failed = await self.refresh_metas(hunt, [meta_round])
        await self.notifier.board_changed(hunt)
        if failed:
            raise SheetRefreshError(failed)

    async def remove_from_meta(self, puzzle, meta_round):
        """Untag a puzzle from a meta (idempotent), commit, refresh affected metas."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        old_tags = list(puzzle.tags)
        if meta_round.id in puzzle.tags:
            puzzle.tags.remove(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
        affected = [meta_round] + [RoundJsonDb.get_by_attr(id=t) for t in old_tags]
        failed = await self.refresh_metas(hunt, affected)
        await self.notifier.board_changed(hunt)
        if failed:
            raise SheetRefreshError(failed)

    async def clear_metas(self, puzzle):
        """Untag a puzzle from ALL metas in one shot (idempotent), commit, refresh
        every formerly-affected meta. Used by drag-to-Unassigned. Callers must
        reject metapuzzles before calling — clearing a metapuzzle's tags would
        orphan its round (mirrors move_to_meta's protection)."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        old_tags = list(puzzle.tags)
        if old_tags:
            puzzle.tags.clear()
            PuzzleJsonDb.commit(puzzle)
        affected = [RoundJsonDb.get_by_attr(id=t) for t in old_tags]
        failed = await self.refresh_metas(hunt, affected)
        await self.notifier.board_changed(hunt)
        if failed:
            raise SheetRefreshError(failed)

    async def set_parent_group(self, child_round, parent_id):
        """Nest `child_round` under group `parent_id` (the parent_group_id tree),
        rejecting containment cycles. M6 substrate — nothing calls this yet; M7 uses it.

        Guards group NESTING only. It has nothing to do with the tags/edge_type
        answer-feeding graph, so mutual A<->B feeding (and meta-to-meta feeding) is
        entirely unaffected — only a circular *containment* chain is forbidden.

        Pass parent_id=None to clear nesting. Raises ValueError on a self-nest or a
        cycle (the prospective parent is the child or one of its descendants)."""
        if parent_id is None:
            child_round.parent_group_id = None
            RoundJsonDb.commit(child_round)
            return
        if parent_id == child_round.id:
            raise ValueError("a group cannot be nested under itself")
        # Walk up from the prospective parent; reaching the child means nesting would
        # close a loop. A depth cap backstops a pre-existing broken chain.
        node_id, depth = parent_id, 0
        while node_id is not None:
            if node_id == child_round.id:
                raise ValueError(
                    f"nesting round {child_round.id} under {parent_id} would create a cycle")
            depth += 1
            if depth > 64:
                raise ValueError("parent_group chain too deep (possible pre-existing cycle)")
            node = RoundJsonDb.get_by_attr(id=node_id)
            if node is None:
                break
            node_id = node.parent_group_id
        child_round.parent_group_id = parent_id
        RoundJsonDb.commit(child_round)

    async def collect_subtree_rounds(self, root_round):
        """BFS from root_round gathering descendants linked by parent_group_id.

        Returns a list of round objects (including root_round itself). A depth cap
        of 64 backstops pre-existing broken chains or cycles."""
        all_rounds = RoundJsonDb.get_all(hunt_id=root_round.hunt_id)
        # Build a child map: parent_group_id -> [child rounds]
        children_of = {}
        for r in all_rounds:
            pid = r.parent_group_id
            if pid is not None:
                children_of.setdefault(pid, []).append(r)

        visited = {root_round.id}
        result = [root_round]
        queue = [root_round]
        depth = 0
        while queue and depth <= 64:
            next_queue = []
            for rnd in queue:
                for child in children_of.get(rnd.id, []):
                    if child.id not in visited:
                        visited.add(child.id)
                        result.append(child)
                        next_queue.append(child)
            queue = next_queue
            depth += 1
        return result

    async def collect_subtree_puzzles(self, root_round):
        """Collect all solved puzzles across the subtree rooted at `root_round`.

        Union of get_all_from_round for each descendant round, deduped by puzzle id,
        keeping only solved puzzles."""
        rounds = await self.collect_subtree_rounds(root_round)
        seen_ids = set()
        puzzles = []
        for rnd in rounds:
            if rnd.id == root_round.id:
                continue  # skip the meta round itself
            for p in PuzzleJsonDb.get_all_from_round(rnd.id):
                if p.id not in seen_ids and p.solved:
                    seen_ids.add(p.id)
                    puzzles.append(p)
        return puzzles

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
        # fail, and keep going (one stale sheet must not skip the others, the round
        # solve_time stamp, or the archive below).
        failed = []
        for tag in puzzle.tags:
            meta_round = RoundJsonDb.get_by_attr(id=tag)
            try:
                await self.refresh_meta(hunt, meta_round)
            except Exception:
                failed.append(tag)
            if meta_round and meta_round.meta_id == puzzle.id:
                meta_round.solve_time = datetime.datetime.now(tz=pytz.UTC)
                meta_round.status = "solved"   # M6: a round is solved when its meta is
                RoundJsonDb.commit(meta_round)

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
        await self.notifier.board_changed(hunt)
        if failed:
            raise SheetRefreshError(failed)
        return True

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
            await gsheet_cog.restore_puzzle_spreadsheet(puzzle)

        # Refresh metas so the unsolved answer disappears from the meta sheet.
        for tag in puzzle.tags:
            try:
                meta_round = RoundJsonDb.get_by_attr(id=tag)
                if meta_round is not None:
                    await self.refresh_meta(hunt, meta_round)
            except Exception:
                logger.exception("unsolve: meta refresh failed for round %s", tag)

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
            round_ = RoundJsonDb.get_by_attr(id=tag)
            if round_ and round_.meta_id == puzzle.id:
                round_.solve_time = None
                round_.status = "active"
                RoundJsonDb.commit(round_)

    async def convert_to_feeder(self, guild, hunt, puzzle):
        """Demote a metapuzzle back to an ordinary feeder (ctx-free seam for the
        !convert_to_feeder bang and the web endpoint).

        Deletes the round whose meta this puzzle is — which ORPHANS every feeder
        tagged to that round (capstone rule) — drops the round id from the puzzle's
        tags, clears the metapuzzle flag, commits, and reverts the puzzle's sheet
        restructure. Mirrors solve's durability ordering: the DB mutation is
        committed before the sheet revert, so a Sheets failure (which
        convert_puzzle_spreadsheet restores-and-re-raises) leaves the demotion
        saved; the edge presents it as a non-fatal warning.

        Returns the number of feeders orphaned by the round deletion (every puzzle
        that was tagged to the deleted round, excluding the meta itself)."""
        orphaned = len(self.feeders_orphaned_by_convert(puzzle))
        meta_round = RoundJsonDb.get_by_attr(meta_id=puzzle.id)
        if meta_round is not None:
            RoundJsonDb.delete(meta_round.id)
            if meta_round.id in puzzle.tags:
                puzzle.tags.remove(meta_round.id)
        puzzle.metapuzzle = False
        PuzzleJsonDb.commit(puzzle)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.convert_puzzle_spreadsheet(puzzle)

        await self.notifier.board_changed(hunt)
        return orphaned

    def feeders_orphaned_by_convert(self, puzzle):
        """Read-only: the feeders that converting *puzzle* back to a feeder would
        orphan — every puzzle tagged to its meta round except the meta itself.
        Powers the web convert confirm, which must name the orphan count up front.
        Returns [] if the puzzle backs no round."""
        meta_round = RoundJsonDb.get_by_attr(meta_id=puzzle.id)
        if meta_round is None:
            return []
        return [
            p for p in PuzzleJsonDb.get_all_from_round(meta_round.id)
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

    async def delete_puzzle(self, guild, hunt, puzzle):
        """Delete a puzzle: sheet tab, DB record, Discord channel, then refresh any
        parent metas. Ctx-free seam for !delete_puzzle and the web endpoint.
        Guild may be None (skips channel deletion).
        Raises SheetRefreshError if a parent meta refresh fails (deletion already committed
        and channel already deleted). The bang edge should send warnings to the hunt
        general channel rather than ctx.send after calling this."""
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
        meta_rounds = [RoundJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        PuzzleJsonDb.delete(puzzle.id)
        failed = await self.refresh_metas(hunt, meta_rounds)
        await self._safe_delete_channel(guild, puzzle.channel_id)
        await self.notifier.board_changed(hunt)
        if failed:
            raise SheetRefreshError(failed)

    async def delete_round(self, guild, hunt, hunt_round):
        """Delete all puzzles in a round (sheet + DB + Discord channel per puzzle),
        delete the round's Discord category (including its solved-divider channel),
        and delete the round DB record. Per-puzzle failures are isolated: one failure
        is logged and the run continues. Guild may be None (skips all Discord deletions).
        Returns the list of puzzle names whose Discord channels could not be deleted."""
        puzzles = PuzzleJsonDb.get_all_from_round(hunt_round.id)
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

        RoundJsonDb.delete(hunt_round.id)

        if guild is not None and hunt_round.category_id:
            category = discord.utils.get(guild.categories, id=hunt_round.category_id)
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
                                     hunt_round.category_id)

        await self.notifier.board_changed(hunt)
        return failed_channels

    async def add_partial(self, guild, hunt, puzzle, solution):
        """Mark a puzzle as partially solved: set status, append solution,
        commit and refresh metas. Does NOT set solve_time and does NOT push
        to Google Sheets (matches _partial_impl).
        """
        puzzle.status = "Partially Solved"
        if puzzle.solution:
            puzzle.solution += "/" + solution
        else:
            puzzle.solution = solution
        PuzzleJsonDb.commit(puzzle)

        rounds = [RoundJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed = await self.refresh_metas(hunt, rounds)
        await self.notifier.board_changed(hunt)
        if failed:
            raise SheetRefreshError(failed)

    async def change_solution(self, guild, hunt, puzzle, solution):
        """Replace the solution on an already-solved puzzle: set new solution,
        stamp solve_time, commit, refresh metas and push to Google Sheets."""
        puzzle.solution = solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(puzzle)

        rounds = [RoundJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed = await self.refresh_metas(hunt, rounds)
        await self.notifier.board_changed(hunt)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.update_solution(puzzle)

        if failed:
            raise SheetRefreshError(failed)

    async def add_solution(self, guild, hunt, puzzle, solution):
        """Append a solution to an already-solved puzzle: append with slash,
        stamp solve_time, commit, refresh metas and push to Google Sheets."""
        puzzle.solution += "/" + solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(puzzle)

        rounds = [RoundJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed = await self.refresh_metas(hunt, rounds)
        await self.notifier.board_changed(hunt)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.update_solution(puzzle)

        if failed:
            raise SheetRefreshError(failed)

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

    async def set_puzzle_url(self, guild, hunt, puzzle, url):
        """Set the puzzle URL, commit and push to Google Sheets. Mirrors !link."""
        puzzle.url = url
        PuzzleJsonDb.commit(puzzle)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.update_puzzle(puzzle)

        await self.notifier.board_changed(hunt)

    def make_minimeta(self, puzzle):
        """Mark a puzzle as a minimeta cluster parent. Idempotent."""
        puzzle.puzzle_type = "Minimeta"
        PuzzleJsonDb.commit(puzzle)

    async def convert_to_meta(self, guild, hunt, puzzle):
        """Convert an existing feeder puzzle into a meta.

        Creates a new round wrapping the puzzle, tags the puzzle to that
        round, sets metapuzzle=1 and commits. Returns (round, puzzle).
        Raises RuntimeError("already_meta") if the puzzle is already a meta.
        """
        if puzzle.is_metapuzzle():
            raise RuntimeError("already_meta")

        new_round = RoundData(
            name=puzzle.name,
            hunt_id=hunt.id,
            category_id=puzzle.channel_id,
            type="Metapuzzle",
            kind="meta",
        )
        RoundJsonDb.commit(new_round)
        new_round.meta_code = RoundJsonDb.generate_uid("meta_code", 6, puzzle.name)
        new_round.meta_id = puzzle.id
        RoundJsonDb.commit(new_round)

        puzzle.add_tag(new_round.id)
        puzzle.metapuzzle = 1
        PuzzleJsonDb.commit(puzzle)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.convert_puzzle_spreadsheet(puzzle)

        await self.notifier.board_changed(hunt)
        return new_round, puzzle

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
            meta_round = RoundJsonDb.get_by_attr(id=tag)
            if meta_round is not None:
                try:
                    await self.refresh_meta(hunt, meta_round)
                except Exception:
                    failed.append(tag)

        await self.notifier.board_changed(hunt)

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

    async def create_mini(self, guild, hunt, minimeta, name, notifier=None):
        """Create a mini-puzzle: thread in minimeta's channel + Google tab + PuzzleData row.

        The mini is a child (parent_puzzle_id) of the minimeta — NOT a round feeder, so
        it is deliberately left untagged: it rolls up onto the minimeta's own sheet via
        the puzzle-keyed refresh_minimeta path (solve/unsolve/create), never onto the
        minimeta's round meta. Auto-sets puzzle_type='Minimeta' on the parent.
        """
        self.make_minimeta(minimeta)

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
            await gsheet_cog.create_puzzle_spreadsheet(mini)

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
        await self.notifier.board_changed(hunt)
        return sheet

    async def remove_sheet(self, hunt, puzzle, sheet_id):
        """Remove an additional sheet by id. Raises RuntimeError("sheet_not_found")
        if absent. Does NOT refresh the pinned info — the edge does."""
        sheet = next((s for s in puzzle.additional_sheets if s.id == sheet_id), None)
        if sheet is None:
            raise RuntimeError("sheet_not_found")
        await self._delete_additional_sheet(hunt, puzzle, sheet)
        await self.notifier.board_changed(hunt)

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

    async def create_puzzle(self, guild, hunt, round_data, name, is_meta=False, notifier=None):
        """Create a new puzzle: DB row + Google sheet + Discord channel.

        Mirrors the old cog path but calls *discord_channels* module functions
        directly (no cog delegation) and fires notification via the injected
        notifier.  Raises the underlying exception on failure so edges can
        decide tolerance.
        """
        # No round (an unassigned puzzle) → drop the channel in the hunt's own category
        # rather than at the guild root.
        category_id = round_data.category_id if round_data else hunt.category_id
        category = discord.utils.get(guild.categories, id=category_id) if category_id else None

        # Space checks before we create any Google sheet (avoids orphan sheets).
        if len(guild.channels) >= MAX_CHANNELS_PER_GUILD:
            raise RuntimeError("guild_full")
        if category is not None and len(category.channels) >= MAX_CHANNELS_PER_CATEGORY:
            raise RuntimeError("category_full")

        new_puzzle = PuzzleData(
            name=name,
            hunt_id=hunt.id,
            start_time=datetime.datetime.now(tz=pytz.UTC),
            status='Unstarted',
            priority='Normal',
        )
        if is_meta:
            new_puzzle.metapuzzle = 1
        if hunt.url:
            prefix = hunt.puzzle_prefix or 'puzzle'
            hunt_url_base = hunt.url.rstrip("/")
            p_name = clean_name(name).replace("-", hunt.url_sep)
            new_puzzle.url = f"{hunt_url_base}/{prefix}/{p_name}"
        if round_data is not None:
            new_puzzle.tags.append(round_data.id)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.create_puzzle_spreadsheet(new_puzzle)

        PuzzleJsonDb.commit(new_puzzle)

        try:
            channel_name = clean_name(new_puzzle.name)
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
                        hunt_round=round_data,
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
                meta_round = RoundJsonDb.get_by_attr(id=tag)
                if meta_round is not None:
                    await self.refresh_meta(hunt, meta_round)
            except Exception:
                logger.exception("create_puzzle: sheet update failed for round %s", tag)

        await self.notifier.board_changed(hunt)
        return new_puzzle

    async def create_round(self, guild, hunt, name, notifier=None,
                           meta_puzzle_name=None, round_type="Metapuzzle", round_kind="meta"):
        """Create a new metapuzzle/round: category clone + round row + optional meta puzzle.

        Returns (RoundData, PuzzleData) when ``meta_puzzle_name`` is not False,
        else (RoundData, None).  Raises RuntimeError("<code>") on recoverable
        problems; raises the underlying exception otherwise.
        ``meta_puzzle_name``: name for the meta puzzle. Defaults to round ``name``.
        Pass a string to override it.  Pass ``False`` to skip (Metaless Round).
        ``round_type`` / ``round_kind`` let callers specify round classification.
        """
        mp_name = meta_puzzle_name if isinstance(meta_puzzle_name, str) else name
        create_meta = meta_puzzle_name is not False
        if RoundJsonDb.check_duplicates_in_hunt(name, hunt):
            raise RuntimeError("round_exists")
        if create_meta and PuzzleJsonDb.check_duplicates_in_hunt(mp_name, hunt.id):
            raise RuntimeError("puzzle_name_clash")
        if len(guild.categories) >= MAX_CATEGORIES:
            raise RuntimeError("no_category_space")

        new_category_name = clean_name(name)
        existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category or hunt.parallel_hunt:
            new_category_name = new_category_name + " — " + clean_name(hunt.name)
            existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category:
            raise RuntimeError("category_exists")

        ref_category = discord.utils.get(guild.categories, id=hunt.category_id)
        if ref_category is None:
            raise RuntimeError("no_template_category")

        new_category = None
        new_round = None
        puzzle = None
        try:
            new_category = await ref_category.clone(name=new_category_name)
            await new_category.edit(position=ref_category.position + 1)
            from bot.core.discord_channels import SOLVE_DIVIDER, SOLVE_CATEGORY
            if SOLVE_CATEGORY is False:
                await get_or_create_channel(
                    guild=guild, category=new_category, channel_name=SOLVE_DIVIDER,
                    channel_type="text", position=500,
                )

            new_round = RoundData(
                name=name, hunt_id=hunt.id,
                category_id=new_category.id,
                type=round_type, kind=round_kind,
            )
            RoundJsonDb.commit(new_round)

            # Create the meta puzzle for this round (skip for Metaless variants).
            if create_meta:
                puzzle = await self.create_puzzle(
                    guild, hunt, new_round, mp_name, is_meta=True, notifier=notifier,
                )
                new_round.meta_id = puzzle.id

            new_round.meta_code = RoundJsonDb.generate_uid('meta_code', 6, name)
            RoundJsonDb.commit(new_round)

            await self.notifier.board_changed(hunt)
            return new_round, puzzle
        except Exception:
            # Roll back so partial create leaves neither a meta-less round row
            # nor an orphan category.
            if new_round is not None and getattr(new_round, "id", 0):
                try:
                    RoundJsonDb.delete(new_round.id)
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
            raise

    def log_clues(self, hunt, puzzle, notes: list) -> list:
        """Insert one evidence_item per note. Returns list of EvidenceData."""
        return [EvidenceJsonDb.log(hunt.id, puzzle.id, note) for note in notes]

    async def refresh_evidence(self, hunt):
        """Render the Evidence sheet tab. Creates the tab lazily on first call."""
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return
        if hunt.evidence_sheet_page_id is None:
            page_id = await gsheet_cog.create_blank_tab(hunt.google_sheet_id, "Evidence")
            hunt.evidence_sheet_page_id = page_id
            HuntJsonDb.commit(hunt)
        items = EvidenceJsonDb.get_all(hunt.id)
        puzzle_ids = {i.source_puzzle_id for i in items} | {i.target_puzzle_id for i in items if i.target_puzzle_id}
        names = {pid: ((PuzzleJsonDb.get_by_attr(id=pid) or None) and PuzzleJsonDb.get_by_attr(id=pid).name) or f"Puzzle {pid}" for pid in puzzle_ids}
        rows = [
            [
                names.get(i.source_puzzle_id, f"Puzzle {i.source_puzzle_id}"),
                i.note,
                names.get(i.target_puzzle_id, "") if i.target_puzzle_id else "",
                str(i.created_at)[:16] if i.created_at else "",
            ]
            for i in items
        ]
        await gsheet_cog.aggregate_to_tab(
            hunt.google_sheet_id,
            hunt.evidence_sheet_page_id,
            ["Source Puzzle", "Note", "Target Puzzle", "Logged At"],
            rows,
        )

    async def refresh_unassigned(self, hunt, show_round=False):
        """Incremental reconcile of the Unassigned Puzzles tab.

        Creates the tab and table lazily on first call. Appends new puzzles,
        updates answer/length for existing ids (e.g. solved), prunes classified ones.
        """
        from bot.core.unassigned import unassigned_row, diff_rows

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return

        tab_name = "Unassigned Puzzles"

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

        # Fetch current unassigned puzzles from DB
        pool = PuzzleJsonDb.get_unassigned(hunt.id)
        desired = [unassigned_row(p) for p in pool]

        # Read current sheet keys and reconcile
        keys = await gsheet_cog.read_unassigned_keys(
            hunt.unassigned_sheet_page_id,
        )
        appends, updates, prunes = diff_rows(keys, desired)

        await gsheet_cog.apply_unassigned_changes(
            page_id=hunt.unassigned_sheet_page_id,
            table_id=hunt.unassigned_table_id,
            appends=appends,
            updates=updates,
            prunes=prunes,
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

