"""Read-only database access for the dashboard.

Uses a dedicated connection pool (separate from the bot's single shared
connection) and runs every query in a worker thread via ``asyncio.to_thread`` so
dashboard traffic never stalls the bot's event loop or races on its connection.
All functions return plain JSON-friendly dicts.
"""
import asyncio
import datetime
import json
import logging
import time
from types import SimpleNamespace

from mysql.connector import pooling
from mysql.connector.errors import PoolError

from bot.core import voice_presence
from bot.core.voice import voice_url
from bot.core.activity_sparkline import squash
from bot.core.sheet_location import (
    additional_sheet_location, hunt_sheet_location, puzzle_location, spreadsheet_for)

from bot.utils import config

logger = logging.getLogger(__name__)

_pool = None

# mysql-connector's pool queue is non-blocking (a bare queue.get(block=False)):
# get_connection() raises PoolError the instant every connection is checked out,
# rather than waiting for one to free up. The canvas tile viewer requests dozens
# of tiles in a burst, and each one calls _load_visible_hunt (2 pooled queries),
# so a low pool size turned "briefly busy" into hard 500s on some tiles. 10 gives
# headroom for that burst; the retry below covers whatever size still isn't enough.
_POOL_SIZE = 10
_POOL_RETRY_ATTEMPTS = 5
_POOL_RETRY_DELAY_SECONDS = 0.05


def _get_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="dashboard",
            pool_size=_POOL_SIZE,
            host="localhost",
            user=config.mysql_username,
            password=config.mysql_password,
            database=config.database,
            charset="utf8mb4",
            autocommit=True,
        )
    return _pool


def _get_connection():
    """Acquire a pooled connection, retrying briefly if the pool is momentarily
    exhausted rather than failing the request outright (see _POOL_SIZE above).
    The final attempt is unguarded, so a PoolError still propagates."""
    for _ in range(_POOL_RETRY_ATTEMPTS - 1):
        try:
            return _get_pool().get_connection()
        except PoolError:
            # A BLOCKING sleep, deliberately: this function is only ever
            # reached from _run / _run_write, and those are only ever entered
            # through the asyncio.to_thread calls below — so it runs on a
            # worker thread, never the event loop. Calling _get_connection
            # from a coroutine directly would stall the Discord gateway
            # heartbeat here; use fetch_all / fetch_one / execute instead.
            time.sleep(_POOL_RETRY_DELAY_SECONDS)
    return _get_pool().get_connection()


def _run(sql, params=(), *, one=False):
    conn = _get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(sql, params)
            return cursor.fetchone() if one else cursor.fetchall()
        finally:
            cursor.close()
    finally:
        conn.close()  # returns the connection to the pool


def _run_write(sql, params=()):
    """Run a single writing statement on a pooled connection.

    No commit: the pool is created with autocommit=True (see _get_pool), so the
    statement commits itself and an explicit commit() would be a no-op.
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
        finally:
            cursor.close()
    finally:
        conn.close()


async def fetch_all(sql, params=()):
    return await asyncio.to_thread(_run, sql, params, one=False)


async def fetch_one(sql, params=()):
    return await asyncio.to_thread(_run, sql, params, one=True)


async def execute(sql, params=()):
    """Run a writing statement on the dedicated web connection and commit."""
    return await asyncio.to_thread(_run_write, sql, params)


def _iso(value):
    """Render a DB datetime (stored naive UTC) as an ISO-8601 UTC string."""
    if isinstance(value, datetime.datetime):
        return value.replace(tzinfo=datetime.timezone.utc).isoformat()
    return None


# --- Hunts / guilds ---------------------------------------------------------

async def get_guilds_map():
    """{guilds.id -> {guild_snowflake, guild_name, website_url}} keyed by row id."""
    rows = await fetch_all("SELECT id, guild_id, guild_name, website_url FROM guilds")
    return {
        row["id"]: {
            "guild_snowflake": row["guild_id"],
            "guild_name": row["guild_name"],
            "website_url": row["website_url"],
        }
        for row in rows
    }


async def get_all_hunts():
    return await fetch_all(
        "SELECT id, uid, name, url, guild_id, role_id, archived, "
        "google_sheet_id, solved_sheet_id, start_time, solve_time "
        "FROM hunts"
    )


async def get_hunt_by_uid(uid):
    return await fetch_one(
        "SELECT id, uid, name, url, guild_id, role_id, roster_role_id, archived, "
        "google_sheet_id, solved_sheet_id, sheet_mode, drive_folder_id, start_time, solve_time, "
        "start_timestamp, username, password, url_sep, puzzle_prefix, "
        "voice_channel_id, voice_per_puzzle, header_style "
        "FROM hunts WHERE uid = %s",
        (uid,),
    )


async def get_hunt_for_puzzle(puzzle_id):
    """The hunt row a puzzle belongs to, for write-action access checks."""
    return await fetch_one(
        "SELECT hunts.id, hunts.uid, hunts.name, hunts.url, hunts.guild_id, "
        "hunts.role_id, hunts.archived, hunts.google_sheet_id, "
        "hunts.solved_sheet_id "
        "FROM hunts JOIN puzzles ON puzzles.hunt_id = hunts.id "
        "WHERE puzzles.id = %s",
        (puzzle_id,),
    )


async def get_hunt_for_tag_group(tag_group_id):
    """The hunt row a tag_group belongs to, for the rollup-rebuild access check."""
    return await fetch_one(
        "SELECT hunts.id, hunts.uid, hunts.name, hunts.url, hunts.guild_id, "
        "hunts.role_id, hunts.archived, hunts.google_sheet_id, "
        "hunts.solved_sheet_id "
        "FROM hunts JOIN tag_group ON tag_group.hunt_id = hunts.id "
        "WHERE tag_group.id = %s",
        (tag_group_id,),
    )


async def get_hunt_for_evidence(item_id):
    """The hunt row an evidence item belongs to, for write-action access checks."""
    return await fetch_one(
        "SELECT hunts.id, hunts.uid, hunts.name, hunts.url, hunts.guild_id, "
        "hunts.role_id, hunts.archived, hunts.google_sheet_id, "
        "hunts.solved_sheet_id "
        "FROM hunts JOIN evidence_items ON evidence_items.hunt_id = hunts.id "
        "WHERE evidence_items.id = %s",
        (item_id,),
    )


def region_to_json(region):
    """One canvas region as the dashboard sees it.

    `shape` is parsed back to an object rather than passed through as a JSON
    string, so the viewer never has to double-decode. It was written by
    canvas_shape.parse_shape, which is the only thing that produces the column.
    """
    import json as _json
    return {
        "id": region.id,
        "shape": _json.loads(region.shape),
        "label": region.label,
        "note": region.note,
        "puzzle_id": region.puzzle_id,
        "role": region.role,
    }


def mark_to_json(mark):
    """One markup mark as the dashboard sees it.

    shape and style are parsed back to objects rather than passed through as
    JSON strings, so the viewer never has to double-decode. Both were written
    by markup_shape, which is the only thing that produces those columns.
    """
    import json as _json
    return {
        "id": mark.id,
        "media_id": mark.media_id,
        "kind": mark.kind,
        "shape": _json.loads(mark.shape),
        "style": _json.loads(mark.style),
        "text": mark.text,
    }


def puzzle_image_to_json(image):
    """One puzzle image as the dashboard sees it.

    media_url is built the same way get_evidence builds it: string
    interpolation from the id, with no media row lookup on this path.
    """
    return {
        "id": image.id,
        "media_id": image.media_id,
        "media_url": f"/api/media/{image.media_id}",
        "source_region_id": image.source_region_id,
        "position": image.position,
    }


def canvas_to_json(canvas_row, regions, media=None):
    """One canvas with its regions inline.

    The media dimensions and tile state ride along so the viewer can size its
    viewport and choose a zoom level before fetching a single tile — and can show
    progress instead of a broken image while tiling is still pending.
    """
    out = {
        "id": canvas_row.id,
        "name": canvas_row.name,
        "note": canvas_row.note,
        "media_id": canvas_row.media_id,
        "regions": [region_to_json(r) for r in regions],
    }
    if media is not None:
        out.update({
            "width": media.width,
            "height": media.height,
            "tile_status": media.tile_status,
            "tile_levels": media.tile_levels,
        })
    return out


async def get_hunt_for_canvas(canvas_id):
    """The hunt row a canvas belongs to, for write-action access checks."""
    return await fetch_one(
        "SELECT hunts.id, hunts.uid, hunts.name, hunts.url, hunts.guild_id, "
        "hunts.role_id, hunts.archived, hunts.google_sheet_id, "
        "hunts.solved_sheet_id "
        "FROM hunts JOIN hunt_canvases ON hunt_canvases.hunt_id = hunts.id "
        "WHERE hunt_canvases.id = %s",
        (canvas_id,),
    )


async def get_hunt_by_channel_id(channel_id):
    """Resolve the hunt that owns a puzzle channel. Works for archived hunts too
    (the hunt row persists after the channel is deleted), so it authorizes frozen
    transcripts that a live channel-permission check could not."""
    return await fetch_one(
        "SELECT h.* FROM hunts h JOIN puzzles p ON p.hunt_id = h.id "
        "WHERE p.channel_id = %s LIMIT 1",
        (channel_id,),
    )


def voice_fields(guild_snowflake, voice_channel_id):
    """The voice_channel_id / voice_url / voice_count trio shared by hunt and puzzle."""
    if not voice_channel_id:
        return {"voice_channel_id": None, "voice_url": None, "voice_count": 0}
    return {
        "voice_channel_id": str(voice_channel_id),
        "voice_url": voice_url(guild_snowflake, voice_channel_id) if guild_snowflake else None,
        "voice_count": voice_presence.count(int(voice_channel_id)),
    }


def hunt_to_json(hunt, guilds_map):
    guild = guilds_map.get(hunt["guild_id"], {})
    return {
        "uid": hunt["uid"],
        "name": hunt["name"],
        "url": hunt["url"],
        "archived": bool(hunt["archived"]),
        "guild_snowflake": guild.get("guild_snowflake"),
        "guild_name": guild.get("guild_name"),
        "start_time": _iso(hunt.get("start_time")),
        "solve_time": _iso(hunt.get("solve_time")),
    }


# --- Puzzles ----------------------------------------------------------------

def _hunt_sheets_view(hunt):
    """The hunt row as the location rule reads it (attribute access)."""
    return SimpleNamespace(
        google_sheet_id=hunt["google_sheet_id"], solved_sheet_id=hunt["solved_sheet_id"])


async def find_puzzle_by_sheet(spreadsheet_id, gid):
    """The puzzle a Google Sheets URL (spreadsheet id + optional tab gid) belongs to.

    Returns ``{"puzzle": <row>, "hunt": <hunt row>}`` or None; visibility is the
    caller's job. Files mode: a puzzle with its own ``google_sheet_id`` matches
    on that alone (gid ignored). Tabs mode (gid required): the puzzle's
    location (``spreadsheet_for``) must be the requested spreadsheet AND its
    ``google_page_id`` (varchar) must equal the gid. A mini resolves to its
    parent minimeta either way.
    """
    if not spreadsheet_id:
        return None
    cols = ("p.id, p.name, p.channel_id, p.solved, p.parent_puzzle_id, p.hunt_id, "
            "p.google_sheet_id, p.google_page_id, p.on_solved_sheet")
    match = await fetch_one(
        f"SELECT {cols} FROM puzzles p WHERE p.google_sheet_id = %s "
        "ORDER BY p.parent_puzzle_id IS NOT NULL, p.id LIMIT 1",
        (spreadsheet_id,),
    )
    if match is None and gid is not None:
        candidates = await fetch_all(
            f"SELECT {cols}, h.google_sheet_id AS h_sheet, h.solved_sheet_id AS h_solved "
            "FROM puzzles p JOIN hunts h ON h.id = p.hunt_id "
            "WHERE (p.google_sheet_id IS NULL OR p.google_sheet_id = '') "
            "AND p.google_page_id = %s AND (h.google_sheet_id = %s OR h.solved_sheet_id = %s) "
            "ORDER BY p.parent_puzzle_id IS NOT NULL, p.id",
            (str(gid), spreadsheet_id, spreadsheet_id),
        )
        for cand in candidates:
            where = spreadsheet_for(
                SimpleNamespace(google_sheet_id=cand["google_sheet_id"],
                                on_solved_sheet=bool(cand["on_solved_sheet"])),
                cand["h_sheet"] or "", cand["h_solved"] or "")
            if where == spreadsheet_id:
                match = cand
                break
    if match is None:
        return None
    if match["parent_puzzle_id"] is not None:
        match = await fetch_one(
            f"SELECT {cols} FROM puzzles p WHERE p.id = %s", (match["parent_puzzle_id"],))
        if match is None:
            return None
    hunt = await fetch_one(
        "SELECT id, uid, name, guild_id, role_id, roster_role_id, archived, "
        "google_sheet_id, solved_sheet_id, sheet_mode FROM hunts WHERE id = %s",
        (match["hunt_id"],),
    )
    if hunt is None:
        return None
    return {"puzzle": match, "hunt": hunt}


async def get_hunt_puzzles(hunt, guild_snowflake):
    """Flat list of every puzzle in the hunt, each with its rounds and last activity.

    ``hunt`` is the hunt row (dict). A puzzle tagged into multiple rounds appears
    once with a ``rounds`` array. Every sheet carries its server-built ``url``.
    """
    hunt_id = hunt["id"]
    hunt_view = _hunt_sheets_view(hunt)
    rows = await fetch_all(
        "SELECT puzzles.id, puzzles.name, puzzles.url, puzzles.channel_id, "
        "puzzles.google_page_id, puzzles.google_sheet_id, "
        "EXISTS(SELECT 1 FROM tag_group mt WHERE mt.target_puzzle_id = puzzles.id) AS is_target, "
        "puzzles.status, puzzles.solved, puzzles.on_solved_sheet, puzzles.solution, "
        "puzzles.priority, puzzles.puzzle_type, puzzles.notes, "
        "puzzles.start_time, puzzles.solve_time, puzzles.voice_channel_id, "
        "tag_group.id AS tag_group_id, tag_group.name AS tag_group_name, "
        "tag_group.target_puzzle_id AS tag_group_target_puzzle_id, tag_group.group_code AS tag_group_group_code, "
        "tag_group.type AS tag_group_type, tags.edge_type AS tag_edge_type, "
        "act.last_human_message_at, act.last_author_name "
        "FROM puzzles "
        "LEFT JOIN tags ON puzzles.id = tags.puzzle_id "
        "LEFT JOIN tag_group ON tags.tag_group_id = tag_group.id "
        "LEFT JOIN channel_activity act ON act.channel_id = puzzles.channel_id "
        "WHERE puzzles.hunt_id = %s AND puzzles.parent_puzzle_id IS NULL "
        "ORDER BY is_target DESC, puzzles.solved ASC, puzzles.id ASC",
        (hunt_id,),
    )

    puzzles = {}
    start_times = {}  # pid -> naive-UTC start_time, for the activity sparkline
    for row in rows:
        pid = row["id"]
        puz = puzzles.get(pid)
        if puz is None:
            puz = {
                "id": pid,
                "name": row["name"],
                "url": row["url"],
                "channel_id": str(row["channel_id"]) if row["channel_id"] else None,
                "discord_url": (
                    f"https://discord.com/channels/{guild_snowflake}/{row['channel_id']}"
                    if guild_snowflake and row["channel_id"] else None
                ),
                "discord_deep_url": (
                    f"discord://-/channels/{guild_snowflake}/{row['channel_id']}"
                    if guild_snowflake and row["channel_id"] else None
                ),
                "sheet_page": row["google_page_id"],
                "sheet_url": puzzle_location(hunt_view, SimpleNamespace(
                    google_sheet_id=row["google_sheet_id"],
                    on_solved_sheet=bool(row["on_solved_sheet"]),
                    google_page_id=row["google_page_id"])).url,
                "is_meta": bool(row["is_target"]),
                "status": row["status"],
                "solved": bool(row["solved"]),
                "on_solved_sheet": bool(row["on_solved_sheet"]),
                "solution": row["solution"],
                "priority": row["priority"],
                "puzzle_type": row["puzzle_type"],
                "notes": _load_notes(row["notes"]),
                "start_time": _iso(row.get("start_time")),
                "solve_time": _iso(row.get("solve_time")),
                "last_activity": (
                    {
                        "at": _iso(row["last_human_message_at"]),
                        "author": row["last_author_name"],
                    }
                    if row["last_human_message_at"] else None
                ),
                "parent_puzzle_id": row.get("parent_puzzle_id"),
                **voice_fields(guild_snowflake, row.get("voice_channel_id")),
                "mini_puzzles": [],
                "rounds": [],
                "sheets": [],
            }
            puzzles[pid] = puz
            start_times[pid] = row.get("start_time")
        if row["tag_group_id"] is not None:
            gid = row["tag_group_id"]
            existing = next((r for r in puz["rounds"] if r["id"] == gid), None)
            if existing is None:
                existing = {
                    "id": gid,
                    "name": row["tag_group_name"],
                    "group_code": row["tag_group_group_code"],
                    "is_target_of": row["tag_group_target_puzzle_id"] == pid,
                    "edges": [],
                    "group_type": row["tag_group_type"],
                }
                puz["rounds"].append(existing)
            if row["tag_edge_type"] and row["tag_edge_type"] not in existing["edges"]:
                existing["edges"].append(row["tag_edge_type"])

    sheet_rows = await fetch_all(
        "SELECT s.id, s.puzzle_id, s.puzzle_name, s.google_page_id, "
        "s.google_sheet_id, s.on_solved_sheet "
        "FROM additional_sheets s JOIN puzzles ON puzzles.id = s.puzzle_id "
        "WHERE puzzles.hunt_id = %s",
        (hunt_id,),
    )
    for s in sheet_rows:
        puz = puzzles.get(s["puzzle_id"])
        if puz is not None:
            puz["sheets"].append({
                "id": s["id"], "name": s["puzzle_name"], "page_id": s["google_page_id"],
                "url": additional_sheet_location(hunt_view, SimpleNamespace(
                    google_sheet_id=s["google_sheet_id"],
                    on_solved_sheet=bool(s["on_solved_sheet"]),
                    google_page_id=s["google_page_id"])).url,
            })

    mini_rows = await fetch_all(
        "SELECT id, name, solved, solution, channel_id, parent_puzzle_id "
        "FROM puzzles WHERE hunt_id = %s AND parent_puzzle_id IS NOT NULL "
        "ORDER BY id ASC",
        (hunt_id,),
    )
    for m in mini_rows:
        parent = puzzles.get(m["parent_puzzle_id"])
        if parent is not None:
            parent["mini_puzzles"].append({
                "id": m["id"],
                "name": m["name"],
                "solved": bool(m["solved"]),
                "solution": m["solution"],
                "channel_id": str(m["channel_id"]) if m["channel_id"] else None,
            })

    # Puzzle-keyed (minimeta) rollups — excluded from get_rollups_by_tag_group since
    # they are not group badges. Attach stale/error directly to each minimeta puzzle.
    rollup_rows = await fetch_all(
        "SELECT target_puzzle_id, last_ok, last_error "
        "FROM rollups WHERE hunt_id = %s AND target_puzzle_id IS NOT NULL",
        (hunt_id,),
    )
    for r in rollup_rows:
        puz = puzzles.get(r["target_puzzle_id"])
        if puz is not None:
            puz["rollup_stale"] = not r["last_ok"]
            puz["rollup_error"] = r["last_error"]

    # Activity sparkline: unsolved top-level puzzles only (solved ones have none;
    # minis are recorded but not drawn). Decoration only: if this query fails the
    # list still loads, with no sparklines (not empty ones, which would read as
    # "nobody has touched it").
    try:
        slot_rows = await fetch_all(
            "SELECT s.puzzle_id, s.slot_start, s.chat_count, s.sheet_edited "
            "FROM puzzle_activity_slots s JOIN puzzles p ON p.id = s.puzzle_id "
            "WHERE p.hunt_id = %s AND p.solved = 0 AND p.parent_puzzle_id IS NULL "
            "ORDER BY s.slot_start",
            (hunt_id,),
        )
    except Exception:
        logger.exception("Activity slots query failed for hunt %s", hunt_id)
        slot_rows = None
    slots_by_puzzle: dict[int, list] = {}
    for r in slot_rows or []:
        slots_by_puzzle.setdefault(r["puzzle_id"], []).append(
            (r["slot_start"], int(r["chat_count"]), bool(r["sheet_edited"])))
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    for pid, puz in puzzles.items():
        puz["activity"] = None
        if puz["solved"] or slot_rows is None:
            continue
        slots = slots_by_puzzle.get(pid, [])
        start = start_times.get(pid) or (slots[0][0] if slots else None)
        if start is None:
            continue
        act = squash(slots, start, now)
        act["last_sheet_edit_at"] = _iso(act["last_sheet_edit_at"])
        puz["activity"] = act

    puzzle_list = list(puzzles.values())
    derive_meta_kind(puzzle_list)
    derive_is_minimeta(puzzle_list)
    return puzzle_list


async def get_hunt_sheets(hunt):
    """The hunt's OWN working sheets -- those belonging to no puzzle.

    A separate query rather than a widening of the puzzle-sheet join in
    get_hunt_puzzles: that join's output is attached to a puzzle dict, and these
    rows have no puzzle. (Turning it into a LEFT JOIN would change nothing either
    -- its `WHERE puzzles.hunt_id = %s` discards the null side.)

    Ordered by id so the dashboard numbers them the same way !list_sheets does.
    """
    rows = await fetch_all(
        "SELECT id, puzzle_name, google_page_id FROM additional_sheets "
        "WHERE hunt_id = %s AND puzzle_id IS NULL ORDER BY id ASC",
        (hunt["id"],),
    )
    hunt_view = _hunt_sheets_view(hunt)
    return [
        {"id": r["id"], "name": r["puzzle_name"], "page_id": r["google_page_id"],
         "url": hunt_sheet_location(hunt_view, r["google_page_id"]).url}
        for r in rows
    ]


def derive_meta_kind(puzzles):
    """Annotate each puzzle dict in-place with ``meta_kind`` — the single label
    shown for a target puzzle, derived (not stored on the puzzle) from the feeder
    edges of the group(s) it is the target of.

    Precedence (highest first) — a target gets exactly one label:
      - ``runaround``: the group's stored type is Runaround (a specialised meta;
        it keeps feeds edges, so it is not edge-distinguishable — read the type).
      - ``metameta``: a meta all of whose feeds-edge feeders are themselves metas.
      - ``meta``: any feeds-edge feeder (an informs edge alongside feeds stays a meta).
      - ``capstone``: only informs-edge feeders, no feeds.
    A target with no qualifying feeders falls back to its stored group type
    (Capstone -> capstone), else meta. Non-targets get ``None``.

    Mutates and returns ``puzzles`` (a list of the dicts built by get_hunt_puzzles).
    """
    is_meta_by_id = {p["id"]: p["is_meta"] for p in puzzles}
    group_feeders = {}  # tag_group_id -> list[(feeder_puzzle_id, edge_type)]
    for p in puzzles:
        for r in p["rounds"]:
            if not r["is_target_of"]:
                for e in r["edges"]:
                    group_feeders.setdefault(r["id"], []).append((p["id"], e))

    for p in puzzles:
        if not p["is_meta"]:
            p["meta_kind"] = None
            continue
        target_groups = [r for r in p["rounds"] if r["is_target_of"]]
        group_types = {r.get("group_type") for r in target_groups}
        feeders = [fe for r in target_groups for fe in group_feeders.get(r["id"], [])]
        feeds_pids = [pid for pid, edge in feeders if edge == "feeds"]
        has_informs = any(edge == "informs" for _, edge in feeders)
        if "Runaround" in group_types:
            p["meta_kind"] = "runaround"
        elif feeds_pids and all(is_meta_by_id.get(pid) for pid in feeds_pids):
            p["meta_kind"] = "metameta"
        elif feeds_pids:
            p["meta_kind"] = "meta"
        elif has_informs:
            p["meta_kind"] = "capstone"
        elif "Capstone" in group_types:
            p["meta_kind"] = "capstone"
        else:
            p["meta_kind"] = "meta"
    return puzzles


def derive_is_minimeta(puzzles):
    """Annotate each puzzle dict in-place with ``is_minimeta`` — derived, not stored.

    A puzzle is a minimeta if it owns mini-puzzle children (the authoritative
    structural signal) OR still carries the legacy ``puzzle_type == 'Minimeta'``
    marker (covers a freshly-made minimeta with no minis yet). Mirrors the derived
    ``meta_kind`` pattern so the UI never gates on the user-editable puzzle_type.
    """
    for p in puzzles:
        p["is_minimeta"] = bool(p.get("mini_puzzles")) or p.get("puzzle_type") == "Minimeta"
    return puzzles


def _load_notes(raw):
    if not raw:
        return []
    try:
        notes = json.loads(raw)
        return notes if isinstance(notes, list) else []
    except (ValueError, TypeError):
        return []


async def get_all_tag_groups(hunt_id):
    """Every tag_group in the hunt (id, name, group_code, type), so build_board
    can surface bare groups — ones with no tagged puzzles yet — as drop targets."""
    rows = await fetch_all(
        "SELECT id, name, group_code, type, target_puzzle_id "
        "FROM tag_group WHERE hunt_id = %s ORDER BY id ASC",
        (hunt_id,),
    )
    return [
        {
            "id": r["id"], "name": r["name"], "group_code": r["group_code"],
            "type": r["type"], "target_puzzle_id": r["target_puzzle_id"],
        }
        for r in rows
    ]


async def get_rollups_by_tag_group(hunt_id):
    """Map each meta tag_group in the hunt to its roll-up sheet's freshness, for the
    dashboard "sheet stale" badge (M5). A group with no rollup row is treated as
    not-stale (never refreshed yet, or no sheet). Includes source_mode (M7)."""
    rows = await fetch_all(
        # tag_group_id IS NOT NULL excludes M8 puzzle-keyed (minimeta) roll-ups, which are
        # not group badges and would otherwise add a stray None key to this map.
        "SELECT tag_group_id, last_ok, last_error, source_mode FROM rollups "
        "WHERE hunt_id = %s AND tag_group_id IS NOT NULL",
        (hunt_id,),
    )
    return {
        row["tag_group_id"]: {
            "stale": not row["last_ok"],
            "error": row["last_error"],
            "source_mode": row.get("source_mode") or "round",
        }
        for row in rows
    }


def build_board(puzzles, rollups_by_tag_group=None, all_groups=None):
    """Group a flat puzzle list into tag groups (metas) for the dashboard board.

    Returns a list of groups plus an "Unassigned" bucket. Each group reports its
    target solution and feeder solve counts, plus whether its roll-up sheet is stale
    (from rollups_by_tag_group). Puzzles in several groups appear in each.

    all_groups (every tag_group in the hunt) surfaces bare groups — ones with no
    tagged puzzles yet — as empty drop targets on the arrange view. Without it
    build_board only sees groups some puzzle references, so a freshly-created
    empty group would be invisible and could never receive puzzles.
    """
    rollups_by_tag_group = rollups_by_tag_group or {}
    group_info = {g["id"]: g for g in (all_groups or [])}
    groups = {}
    order = []

    def ensure(tag_group_id, name, group_code):
        if tag_group_id not in groups:
            ru = rollups_by_tag_group.get(tag_group_id, {})
            groups[tag_group_id] = {
                "tag_group_id": tag_group_id,
                "name": name,
                "group_code": group_code,
                "puzzles": [],
                "target_solution": None,
                "target_solved": False,
                "num_feeders": 0,
                "num_solved_feeders": 0,
                "rollup_stale": bool(ru.get("stale", False)),
                "rollup_error": ru.get("error"),
                "source_mode": ru.get("source_mode") or "round",
                "group_type": group_info.get(tag_group_id, {}).get("type"),
                "target_puzzle_id": group_info.get(tag_group_id, {}).get("target_puzzle_id") or 0,
            }
            order.append(tag_group_id)
        return groups[tag_group_id]

    for puz in puzzles:
        for rnd in puz["rounds"]:
            grp = ensure(rnd["id"], rnd["name"], rnd["group_code"])
            grp["puzzles"].append(puz)
            if rnd["is_target_of"]:
                grp["target_solved"] = puz["solved"]
                grp["target_solution"] = puz["solution"]
            else:
                grp["num_feeders"] += 1
                if puz["solved"]:
                    grp["num_solved_feeders"] += 1
        # Unassigned Feeders bucket: a non-meta puzzle whose answer feeds nothing
        # (no 'feeds' edge to any meta). It still appears under any group it
        # informs or is a grab-bag ('none') member of, but also surfaces here as
        # needing a feed target.
        # Target puzzles are excluded; mini-meta feeders are already absent
        # (get_hunt_puzzles drops puzzles with a parent_puzzle_id).
        feeds_something = any("feeds" in rnd["edges"] for rnd in puz["rounds"])
        if not puz["is_meta"] and not feeds_something:
            grp = ensure(0, "Unassigned Feeders", None)
            grp["puzzles"].append(puz)
            grp["num_feeders"] += 1
            if puz["solved"]:
                grp["num_solved_feeders"] += 1

    # Surface bare groups (no tagged puzzles) as empty drop targets, appended
    # after the puzzle-populated ones. Only groups with a group_code can be drop
    # targets on the arrange view, so skip the codeless ones.
    for g in (all_groups or []):
        if g.get("group_code"):
            ensure(g["id"], g["name"], g["group_code"])

    # Per-group signals for ranking: feeder edge types, which puzzle ids feed
    # each group (for metameta detection), and whether each target is solved.
    # meta-vs-capstone is edge-derived (any 'feeds' feeder = meta; informs-only
    # = capstone) so it stays correct even when the stored type is stale —
    # the stored type is only a fallback for a group with no feeders yet.
    feeder_edges = {}    # gid -> set of feeder edge types
    feeder_pids = {}     # gid -> list[int] of puzzle ids with a "feeds" edge
    target_solved = {}   # gid -> bool
    is_meta_by_id = {p["id"]: p["is_meta"] for p in puzzles}

    for puz in puzzles:
        for rnd in puz["rounds"]:
            gid = rnd["id"]
            if rnd["is_target_of"]:
                target_solved[gid] = puz["solved"]
            else:
                feeder_edges.setdefault(gid, set()).update(rnd["edges"])
                if "feeds" in rnd["edges"]:
                    feeder_pids.setdefault(gid, []).append(puz["id"])

    def _is_capstone(gid):
        edges = feeder_edges.get(gid, set())
        if "feeds" in edges:
            return False
        if "informs" in edges:
            return True
        return group_info.get(gid, {}).get("type") == "Capstone"

    def _meta_sub_rank(gid):
        # 0 = runaround (stored type), 1 = metameta (all feeds-feeders are metas),
        # 2 = regular meta.
        if group_info.get(gid, {}).get("type") == "Runaround":
            return 0
        pids = feeder_pids.get(gid, [])
        if pids and all(is_meta_by_id.get(pid) for pid in pids):
            return 1
        return 2

    def _rank(gid):
        # Group order:
        #   0  Unassigned Feeders
        #   1  unsolved runaround
        #   2  unsolved metameta
        #   3  unsolved meta
        #   4  unsolved capstone
        #   5  plain (no target)
        #   6  solved runaround
        #   7  solved metameta
        #   8  solved meta
        #   9  solved capstone
        if gid == 0:
            return 0
        has_target = bool(group_info.get(gid, {}).get("target_puzzle_id")) or gid in target_solved
        if not has_target:
            return 5
        if _is_capstone(gid):
            return 9 if target_solved.get(gid) else 4
        sub = _meta_sub_rank(gid)
        return (6 + sub) if target_solved.get(gid) else (1 + sub)

    # Within a rank, oldest first — group id is auto-increment (creation order).
    return [groups[rid] for rid in sorted(order, key=lambda gid: (_rank(gid), gid))]


# --- Activity ---------------------------------------------------------------

def get_evidence(hunt_id, source_puzzle_id=None, target_puzzle_id=None):
    """Return evidence items for a hunt with resolved source and target names.

    source_puzzle_id/source_puzzle_name are None for evidence that belongs to
    the hunt rather than to any one puzzle; source_sheet_id/source_sheet_name
    name a hunt-level working sheet instead, and source_region_id/
    source_region_name a region drawn on a canvas. media_url is relative so it
    works on whichever origin is serving the SPA.
    """
    from bot.store import EvidenceJsonDb
    from bot.core.puzzle_service import (
        resolve_puzzle_names, resolve_region_names, resolve_sheet_names)
    items = EvidenceJsonDb.get_all(hunt_id, source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
    puzzle_ids = ({i.source_puzzle_id for i in items if i.source_puzzle_id}
                  | {i.target_puzzle_id for i in items if i.target_puzzle_id})
    names = resolve_puzzle_names(puzzle_ids)
    sheet_names = resolve_sheet_names(
        getattr(i, "source_sheet_id", None) for i in items)
    # Batched like the other two: a canvas with many informs-regions would
    # otherwise be an N+1 down the length of the evidence board.
    region_names = resolve_region_names(
        getattr(i, "source_region_id", None) for i in items)
    return [
        {
            "id": i.id,
            "source_puzzle_id": i.source_puzzle_id,
            "source_puzzle_name": names.get(i.source_puzzle_id, "") if i.source_puzzle_id else None,
            "source_sheet_id": i.source_sheet_id,
            "source_sheet_name": (sheet_names.get(i.source_sheet_id) or f"Sheet {i.source_sheet_id}")
                                 if i.source_sheet_id else None,
            "source_region_id": getattr(i, "source_region_id", None),
            # Same shape and same last-ditch fallback as the sheet above: the
            # DAO already applies the label/canvas-name/filename chain, so a
            # miss here means the region row itself went away.
            "source_region_name": (region_names.get(getattr(i, "source_region_id", None))
                                   or f"Region {i.source_region_id}")
                                  if getattr(i, "source_region_id", None) else None,
            "target_puzzle_id": i.target_puzzle_id,
            "target_puzzle_name": names.get(i.target_puzzle_id, "") if i.target_puzzle_id else None,
            "note": i.note,
            "media_id": i.media_id,
            "media_url": f"/api/media/{i.media_id}" if i.media_id else None,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in items
    ]


# --- Messages -----------------------------------------------------------------

async def get_channel_messages(channel_id, before=None, limit=50):
    """Return up to `limit` messages for a channel, ascending by id (render order).
    `before` is an exclusive snowflake cursor for scrolling into history."""
    limit = max(1, min(int(limit), 200))
    if before:
        rows = await fetch_all(
            "SELECT id, author_id, author_name, author_avatar, kind, content, embeds, "
            "reference_id, created_at, edited_at FROM messages "
            "WHERE channel_id=%s AND id < %s ORDER BY id DESC LIMIT %s",
            (channel_id, before, limit),
        )
    else:
        rows = await fetch_all(
            "SELECT id, author_id, author_name, author_avatar, kind, content, embeds, "
            "reference_id, created_at, edited_at FROM messages "
            "WHERE channel_id=%s ORDER BY id DESC LIMIT %s",
            (channel_id, limit),
        )
    rows = list(reversed(rows))  # ascending for the viewer
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join(["%s"] * len(ids))
    atts = await fetch_all(
        "SELECT id, message_id, filename, content_type, size, local_path, download_status "
        f"FROM message_attachments WHERE message_id IN ({placeholders})",
        tuple(ids),
    )
    by_msg = {}
    for a in atts:
        by_msg.setdefault(a["message_id"], []).append({
            "id": str(a["id"]),
            "filename": a["filename"],
            "content_type": a["content_type"],
            "size": a["size"],
            "url": (f"/api/attachments/{a['id']}" if a["download_status"] == 1 else None),
        })
    out = []
    for r in rows:
        out.append({
            "id": str(r["id"]),
            "author_id": str(r["author_id"]),
            "author_name": r["author_name"],
            "author_avatar": r["author_avatar"],
            "kind": r["kind"],
            "content": r["content"],
            "embeds": (json.loads(r["embeds"]) if r["embeds"] else []),
            "reference_id": (str(r["reference_id"]) if r["reference_id"] else None),
            "created_at": _iso(r["created_at"]) if isinstance(r["created_at"], datetime.datetime) else r["created_at"],
            "edited_at": (_iso(r["edited_at"]) if isinstance(r["edited_at"], datetime.datetime) else None),
            "attachments": by_msg.get(r["id"], []),
        })
    return out


async def get_attachment_with_channel(attachment_id):
    """Attachment row plus the channel it belongs to, for auth + file serving."""
    return await fetch_one(
        "SELECT a.id, a.filename, a.content_type, a.local_path, a.download_status, "
        "m.channel_id FROM message_attachments a JOIN messages m ON m.id = a.message_id "
        "WHERE a.id = %s LIMIT 1",
        (attachment_id,),
    )
