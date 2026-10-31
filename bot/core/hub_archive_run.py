"""The locked archive-to-hub run, shared by !archive_hunt_to_hub and the
dashboard's POST /api/hunts/<uid>/archive-to-hub, so both do the same thing and
post the same messages."""
import logging

import discord

from bot.core import hunt_archive
from bot.core.locks import puzzles_lock
from bot.store import PuzzleJsonDb

logger = logging.getLogger(__name__)

# Hunt ids with a web-started archive in flight (maintained by the web route).
RUNNING_HUB_ARCHIVES: set[int] = set()


def archive_summary(report):
    """The completion line shared by !archive_hunt and !archive_hunt_to_hub."""
    summary = (f":white_check_mark: Archived hunt into **{report.final_name}** — "
               f"{report.channels_archived} channel(s), {report.messages_moved} message(s) moved, "
               f"{report.voice_deleted} voice channel(s) removed.")
    if report.failures:
        summary += f"\n:warning: {len(report.failures)} channel(s) failed: {', '.join(report.failures)}"
    return summary


async def _fallback_status_channel(hub, hunt, guild, invoker):
    """The hub archive channel (found by topic, or created as the service would),
    for a run with no usable admin channel. None if it can't be had."""
    try:
        team = hunt_archive.resolve_team_name(guild, hunt, invoker)
        final = hunt_archive.archived_channel_name(hunt, team)
        return await hunt_archive.ensure_hub_archive_channel(hub, hunt, final)
    except Exception:
        logger.exception("archive_hunt_to_hub: could not get a status channel on the hub")
        return None


async def run_archive_to_hub(bot, service, guild, hunt, invoker, hub, hub_row, *, status_channel):
    """Archive *hunt* into *hub* under every member puzzle's lock, posting
    progress to *status_channel* (anything with an async `send`; None = the hub
    archive channel) and the summary to the hub archive channel and
    *status_channel*. Returns the HuntArchiveReport, or None after a logged error."""
    try:
        if status_channel is None:
            status_channel = await _fallback_status_channel(hub, hunt, guild, invoker)
        member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_hunt(hunt.id)]
        async with puzzles_lock(member_puzzle_ids):
            status = await status_channel.send("⏳ Archiving…") if status_channel is not None else None

            async def on_progress(done, total, name):
                if status is None:
                    return
                try:
                    await status.edit(content=f"⏳ Archived {done}/{total} — {name}")
                except discord.HTTPException:
                    pass

            report = await service.archive_hunt_to_hub(
                guild, hunt, invoker, hub, hub_row, on_progress=on_progress)
            summary = archive_summary(report)
            if report.left_guild:
                summary += "\nThe bot has left this server; its owner can now delete it."
            if report.failures:
                summary += ("\nThe hunt hasn't been moved on the dashboard yet and the bot is staying: "
                            "re-run !archive_hunt_to_hub <hub id> from this hunt's general channel to finish.")
            hub_dest = hub.get_channel(report.hub_channel_id)
            if hub_dest is not None:
                try:
                    await hub_dest.send(summary)
                except discord.HTTPException:
                    logger.exception("archive_hunt_to_hub: could not post summary to hub channel %s", report.hub_channel_id)
            if status_channel is not None and status_channel is not hub_dest:
                try:
                    # The channel this ran in is usually archived by now, and the bot
                    # may have left the server, so this reply is best-effort only.
                    await status_channel.send(summary)
                except discord.HTTPException:
                    pass
            return report
    except Exception:
        logger.exception("archive_hunt_to_hub: run failed for hunt %s", getattr(hunt, "id", None))
        return None
