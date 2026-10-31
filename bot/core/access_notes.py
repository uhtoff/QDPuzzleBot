"""Short-lived notes the bot leaves for itself before changing someone's access,
so the role-change listener can tell its own changes (and why) from hand edits.

`key` is a role id, or the string "kick" for a removal from the guild."""
import time

TTL_SECONDS = 60

_now = time.monotonic
_notes = {}  # (guild_id, user_id, key) -> (via, stamped_at)


def expect(guild_id, user_id, key, via):
    now = _now()
    for k in [k for k, (_, at) in _notes.items() if now - at > TTL_SECONDS]:
        del _notes[k]
    _notes[(guild_id, user_id, key)] = (via, now)


def take(guild_id, user_id, key):
    entry = _notes.pop((guild_id, user_id, key), None)
    if entry is None:
        return None
    via, at = entry
    if _now() - at > TTL_SECONDS:
        return None
    return via


def discard(guild_id, user_id, key):
    """Drop a note whose Discord call failed, so it can't mislabel a later hand change."""
    _notes.pop((guild_id, user_id, key), None)
