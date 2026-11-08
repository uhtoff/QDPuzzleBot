"""In-memory count of humans in each voice channel, for the dashboard's live
voice badges. Rebuilt from the guilds' voice states on ready; kept current by
``on_state_change``. Nothing here is persisted."""
from bot.store import HuntJsonDb, PuzzleJsonDb

_counts: dict[int, int] = {}
# Discord guild snowflake per tracked channel: the activity feed keys events on
# it (see bot/cogs/activity.py), and the owning DB rows only know the guilds-table id.
_guilds: dict[int, int] = {}


def count(channel_id: int) -> int:
    return _counts.get(channel_id, 0)


def snapshot(guilds) -> None:
    _counts.clear()
    _guilds.clear()
    for guild in guilds:
        for channel in guild.voice_channels:
            humans = sum(1 for m in channel.members if not m.bot)
            if humans:
                _counts[channel.id] = humans
            _guilds[channel.id] = guild.id


def on_state_change(member, before_channel, after_channel) -> list[int]:
    """Apply a voice-state change; return the channel ids whose count changed."""
    if member.bot:
        return []
    if before_channel is not None and after_channel is not None \
            and before_channel.id == after_channel.id:
        return []
    changed: list[int] = []
    if before_channel is not None:
        _counts[before_channel.id] = max(0, count(before_channel.id) - 1)
        _guilds[before_channel.id] = before_channel.guild.id
        changed.append(before_channel.id)
    if after_channel is not None:
        _counts[after_channel.id] = count(after_channel.id) + 1
        _guilds[after_channel.id] = after_channel.guild.id
        changed.append(after_channel.id)
    return changed


def event_for(voice_channel_id: int) -> dict | None:
    """The websocket "voice" event for the puzzle or hunt owning this voice channel."""
    guild_id = _guilds.get(voice_channel_id)
    if guild_id is None:
        return None
    n = count(voice_channel_id)
    puzzle = PuzzleJsonDb.get_by_attr(voice_channel_id=voice_channel_id)
    if puzzle is not None:
        return {"type": "voice", "guild_id": guild_id,
                "channel_id": str(puzzle.channel_id), "voice_count": n}
    hunt = HuntJsonDb.get_by_attr(voice_channel_id=voice_channel_id)
    if hunt is not None:
        return {"type": "voice", "guild_id": guild_id,
                "channel_id": str(hunt.channel_id), "hunt_uid": hunt.uid,
                "voice_count": n}
    return None
