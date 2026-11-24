"""The pure half of POST /api/hunts/<uid>/unread (the chat extension's unread list).

Read state lives in the extension, as Discord message ids ("markers"; snowflakes
sort by time): one `baseline` for every channel plus per-channel `read` markers.
The server only answers "what is newer than these", folding in the user's own
latest message per channel (posting in a channel counts as having read it).
No DB or Quart here; the route in api.py does the I/O.
"""
import datetime

DISCORD_EPOCH_MS = 1420070400000
MAX_READ_ENTRIES = 500


def snowflake_time(marker):
    """The moment a snowflake encodes, as naive UTC (like messages.created_at)."""
    ms = (int(marker) >> 22) + DISCORD_EPOCH_MS
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=ms)


def _digits(value):
    """A non-negative id sent as a string of digits, else None (ints are refused:
    JS loses precision past 2**53, so the extension must send strings)."""
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def parse_markers(body):
    """(baseline, {channel_id: marker}) from the request body, or None if malformed."""
    if not isinstance(body, dict):
        return None
    baseline = _digits(body.get("baseline"))
    if baseline is None:
        return None
    raw = body.get("read", {})
    if not isinstance(raw, dict) or len(raw) > MAX_READ_ENTRIES:
        return None
    read = {}
    for key, value in raw.items():
        cid, marker = _digits(key), _digits(value)
        if cid is None or marker is None:
            return None
        read[cid] = marker
    return baseline, read


def effective_markers(channel_ids, baseline, read, own_last):
    """Per channel, the latest of: the baseline, its read marker, the user's own last message."""
    return {c: max(baseline, read.get(c, 0), own_last.get(c, 0)) for c in channel_ids}


def _iso(value):
    """Same rendering as bot.web.data._iso (kept here so this module stays pure)."""
    if isinstance(value, datetime.datetime):
        return value.replace(tzinfo=datetime.timezone.utc).isoformat()
    return None if value is None else str(value)


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def _last(counts, lasts, cid):
    row = lasts.get(counts.get(cid, {}).get("last_id"))
    if row is None:
        return None
    return {"id": str(row["id"]), "author_name": row.get("author_name") or "",
            "content": row.get("content") or "", "created_at": _iso(row.get("created_at"))}


def _entry(cid, counts, lasts, **fields):
    return {"channel_id": str(cid), **fields,
            "unread": counts.get(cid, {}).get("unread", 0), "last": _last(counts, lasts, cid)}


def build_channels(hunt, puzzles, guild_snowflake, markers, counts, lasts):
    """The response's channel list: the hunt's general channel, then each top-level
    puzzle with a channel, in the order given. Every channel is returned, read or not."""
    out = []
    general = hunt.get("channel_id")
    if general:
        gid = int(general)
        out.append(_entry(
            gid, counts, lasts, kind="general", puzzle_id=None, name=f"{hunt['name']} (general)",
            rounds=[], solved=False, solution=None, newly_solved=False, sheet_url=None,
            discord_url=(f"https://discord.com/channels/{guild_snowflake}/{gid}"
                         if guild_snowflake else None)))
    for p in puzzles:
        if not p.get("channel_id") or p.get("parent_puzzle_id"):
            continue
        cid = int(p["channel_id"])
        solved = bool(p.get("solved"))
        solve_time = _parse_time(p.get("solve_time"))
        marker = markers.get(cid)
        newly = (solved and solve_time is not None and marker is not None
                 and solve_time > snowflake_time(marker))
        out.append(_entry(
            cid, counts, lasts, kind="puzzle", puzzle_id=p["id"], name=p["name"],
            rounds=[r["name"] for r in p.get("rounds") or []], solved=solved,
            solution=p.get("solution"), newly_solved=newly, sheet_url=p.get("sheet_url"),
            discord_url=p.get("discord_url")))
    return out
