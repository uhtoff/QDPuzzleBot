"""Ctx-free service layer for puzzle operations (M3).

The canonical implementation of each puzzle operation lives here. The Discord
bang-commands and the web dashboard are thin edges that call into this service;
the service does DB + Google Sheets + intrinsic Discord mutations and RAISES on
failure. Presentation (embeds, confirmations) and error-tolerance policy stay at
the edges.
"""
import datetime
import logging

import discord
import pytz
from googleapiclient.errors import HttpError

from bot.store import PuzzleJsonDb, RoundJsonDb, HuntJsonDb, GuildSettingsDb, SheetsJsonDb, RollupsJsonDb  # noqa: F401  # GuildSettingsDb kept as a tests' patch target
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

logger = logging.getLogger(__name__)


class PuzzleService:
    def __init__(self, bot, notifier=None):
        self.bot = bot
        if notifier is None:
            from bot.core.notifier import NullNotifier
            notifier = NullNotifier()
        self.notifier = notifier

    def _gsheet_cog(self, hunt):
        """Resolve the GoogleSheets cog and point it at this hunt's spreadsheets.
        Returns None if the cog isn't loaded (e.g. in tests)."""
        cog = self.bot.get_cog("GoogleSheets")
        if cog is not None and hunt is not None:
            cog.set_spreadsheet_id(hunt.google_sheet_id)
            cog.set_archive_spreadsheet_id(hunt.archive_google_sheet_id)
        return cog

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
        if rollup.metadata_id is None and rollup.tab_page_id is not None:
            try:
                _, rollup.metadata_id = await gsheet_cog.stamp_rollup_metadata(
                    metapuzzle, f"rollup:{rollup.round_id}")
            except Exception:
                logger.exception(
                    "refresh_meta: developer-metadata stamp failed for round %s "
                    "(non-fatal; google_page_id remains the pointer)", rollup.round_id)

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

    def make_minimeta(self, puzzle):
        """Mark a puzzle as a minimeta cluster parent. Idempotent."""
        puzzle.puzzle_type = "Minimeta"
        PuzzleJsonDb.commit(puzzle)

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

        Tags the mini into the minimeta's round (first tag) so refresh_meta
        rolls up its answer to the minimeta sheet on solve.
        Auto-sets the minimeta's puzzle_type to 'Minimeta' if not already set.
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

        # Tag into the minimeta's first round so refresh_meta fires on solve.
        if minimeta.tags:
            mini.tags.append(minimeta.tags[0])

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
        category = discord.utils.get(guild.categories, id=round_data.category_id) if round_data else None

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

