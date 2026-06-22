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

from bot.store import PuzzleJsonDb, RoundJsonDb, HuntJsonDb, GuildSettingsDb, SheetsJsonDb
from bot.store.puzzle_data import AdditionalSheetData

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
        """Rebuild a meta (or meta-meta) roll-up sheet. RAISES on a Sheets error;
        the caller decides whether to swallow it."""
        if hunt is None or hunt_round is None or not hunt_round.meta_id:
            return
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return
        metapuzzle = PuzzleJsonDb.get_by_attr(id=hunt_round.meta_id)
        if metapuzzle is None:
            return
        if metapuzzle.metameta:
            all_puzzles = PuzzleJsonDb.get_all_from_hunt(hunt_round.hunt_id)
            await gsheet_cog.add_metametapuzzle_data(metapuzzle, all_puzzles)
        else:
            round_puzzles = PuzzleJsonDb.get_all_from_round(hunt_round.id)
            await gsheet_cog.add_metapuzzle_data(metapuzzle, round_puzzles)

    async def move_to_meta(self, guild, puzzle, meta_round):
        """Retag a puzzle to meta_round (replacing its tags unless it is itself a
        metapuzzle), move its channel into the meta's category, commit, and refresh
        the affected metas. Sheet refresh errors propagate (web edge swallows)."""
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
        await self.refresh_meta(hunt, meta_round)
        for old_tag in old_tags:
            await self.refresh_meta(hunt, RoundJsonDb.get_by_attr(id=old_tag))

    async def add_to_meta(self, puzzle, meta_round):
        """Tag a puzzle to an additional meta (idempotent), commit, refresh."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        if meta_round.id not in puzzle.tags:
            puzzle.tags.append(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
        await self.refresh_meta(hunt, meta_round)

    async def remove_from_meta(self, puzzle, meta_round):
        """Untag a puzzle from a meta (idempotent), commit, refresh affected metas."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        old_tags = list(puzzle.tags)
        if meta_round.id in puzzle.tags:
            puzzle.tags.remove(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
        await self.refresh_meta(hunt, meta_round)
        for old_tag in old_tags:
            await self.refresh_meta(hunt, RoundJsonDb.get_by_attr(id=old_tag))

    async def solve(self, guild, hunt, puzzle, solution):
        """Mark a puzzle solved: set status/solution/solve_time, refresh metas,
        stamp the round solve_time if this is the round's meta, archive the sheet,
        and persist. RAISES on Sheets failure; edges decide tolerance and
        presentation (channel reposition + embed live at the edges).

        Returns True if the solve was applied, False if rejected (empty solution
        with no existing solution — matches _solve_impl's guard)."""
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

        for tag in puzzle.tags:
            meta_round = RoundJsonDb.get_by_attr(id=tag)
            await self.refresh_meta(hunt, meta_round)
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
