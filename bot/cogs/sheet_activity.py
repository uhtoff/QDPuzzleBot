"""Sheet-edit activity for files-mode hunts (the dashboard sparkline's orange marks).

Every 5 minutes, list each live files-mode hunt's Drive folder for spreadsheets
modified in the last 10 minutes -- one Drive call per hunt, never per puzzle --
and mark the edit's slot for the puzzle that owns the file. Edits whose last
modifier is the bot's own service account are ignored (solve tidy-up, headers,
roll-ups); anonymous "anyone with the link" editors count. The 10-minute window
overlaps the 5-minute tick so a slow or skipped tick loses nothing, and marking a
slot is idempotent. The Solved folder is not polled: solved puzzles have no
sparkline.
"""
import asyncio
import datetime
import logging

from discord.ext import commands, tasks

from bot.store import SheetActivityDb

logger = logging.getLogger(__name__)

POLL_MINUTES = 5
LOOK_BACK = datetime.timedelta(minutes=10)
_SPREADSHEET = "application/vnd.google-apps.spreadsheet"


class SheetActivity(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        try:
            await self.poll_once(datetime.datetime.now(datetime.timezone.utc))
        except Exception:
            # Activity tracking must never break the bot, nor stop the loop.
            logger.exception("Sheet activity poll failed")

    @poll.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def poll_once(self, now: datetime.datetime) -> None:
        sheets = self.bot.get_cog("GoogleSheets")
        if sheets is None:
            return
        folders = await asyncio.to_thread(SheetActivityDb.files_hunt_folders)
        for hunt_id, folder_id in folders:
            try:
                files = await sheets.list_modified_since(folder_id, now - LOOK_BACK)
                for f in files:
                    if f.get("mimeType") != _SPREADSHEET:
                        continue
                    if (f.get("lastModifyingUser") or {}).get("me"):
                        continue  # the bot's own edit
                    edited_at = datetime.datetime.fromisoformat(f["modifiedTime"])
                    await asyncio.to_thread(
                        SheetActivityDb.mark_sheet_edited, f["id"], edited_at)
            except Exception:
                logger.exception("Sheet activity poll failed for hunt %s", hunt_id)


async def setup(bot):
    await bot.add_cog(SheetActivity(bot))
