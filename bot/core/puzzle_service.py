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

from bot.store import PuzzleJsonDb, RoundJsonDb, HuntJsonDb, GuildSettingsDb, SheetsJsonDb, RollupsJsonDb
from bot.store.puzzle_data import AdditionalSheetData
from bot.store.rollup_data import RollupData
from bot.core.locks import rollup_lock
from bot.core.errors import SheetRefreshError

logger = logging.getLogger(__name__)


class PuzzleService:
    def __init__(self, bot):
        self.bot = bot

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
                if metapuzzle.metameta:
                    all_puzzles = PuzzleJsonDb.get_all_from_hunt(hunt_round.hunt_id)
                    await gsheet_cog.add_metametapuzzle_data(metapuzzle, all_puzzles)
                else:
                    round_puzzles = PuzzleJsonDb.get_all_from_round(hunt_round.id)
                    await gsheet_cog.add_metapuzzle_data(metapuzzle, round_puzzles)
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

    async def _refresh_metas(self, hunt, rounds):
        """Refresh several meta rounds with FAILURE ISOLATION: every round is
        attempted (a failure on one must not skip the others), each failure is
        already badged by refresh_meta, and the failed round ids are returned for the
        caller to surface. None rounds are skipped."""
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
        failed = await self._refresh_metas(hunt, affected)
        if failed:
            raise SheetRefreshError(failed)

    async def add_to_meta(self, puzzle, meta_round):
        """Tag a puzzle to an additional meta (idempotent), commit, refresh."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        if meta_round.id not in puzzle.tags:
            puzzle.tags.append(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
        failed = await self._refresh_metas(hunt, [meta_round])
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
        failed = await self._refresh_metas(hunt, affected)
        if failed:
            raise SheetRefreshError(failed)

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
                RoundJsonDb.commit(meta_round)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.archive_puzzle_spreadsheet(puzzle)
            puzzle.archive_time = datetime.datetime.now(tz=pytz.UTC)

        PuzzleJsonDb.commit(puzzle)
        for sheet in puzzle.additional_sheets:
            SheetsJsonDb.commit(sheet)
        if failed:
            raise SheetRefreshError(failed)
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

    async def remove_sheet(self, hunt, puzzle, sheet_id):
        """Remove an additional sheet by id. Raises RuntimeError("sheet_not_found")
        if absent. Does NOT refresh the pinned info — the edge does."""
        sheet = next((s for s in puzzle.additional_sheets if s.id == sheet_id), None)
        if sheet is None:
            raise RuntimeError("sheet_not_found")
        await self._delete_additional_sheet(hunt, puzzle, sheet)
