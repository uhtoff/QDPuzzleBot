"""Ctx-free service layer for puzzle operations (M3).

The canonical implementation of each puzzle operation lives here. The Discord
bang-commands and the web dashboard are thin edges that call into this service;
the service does DB + Google Sheets + intrinsic Discord mutations and RAISES on
failure. Presentation (embeds, confirmations) and error-tolerance policy stay at
the edges.
"""
import logging

import discord

from bot.store import PuzzleJsonDb, RoundJsonDb, HuntJsonDb

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
