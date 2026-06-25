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

from mysql.connector import pooling

from bot.utils import config

logger = logging.getLogger(__name__)

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="dashboard",
            pool_size=3,
            host="localhost",
            user=config.mysql_username,
            password=config.mysql_password,
            database=config.database,
            charset="utf8mb4",
            autocommit=True,
        )
    return _pool


def _run(sql, params=(), *, one=False):
    conn = _get_pool().get_connection()
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
    """Run a writing statement on a pooled connection and commit."""
    conn = _get_pool().get_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
        finally:
            cursor.close()
        conn.commit()
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
        "google_sheet_id, archive_google_sheet_id, start_time, solve_time "
        "FROM hunts"
    )


async def get_hunt_by_uid(uid):
    return await fetch_one(
        "SELECT id, uid, name, url, guild_id, role_id, archived, "
        "google_sheet_id, archive_google_sheet_id, start_time, solve_time, "
        "start_timestamp, username, password, url_sep, puzzle_prefix "
        "FROM hunts WHERE uid = %s",
        (uid,),
    )


async def get_hunt_for_puzzle(puzzle_id):
    """The hunt row a puzzle belongs to, for write-action access checks."""
    return await fetch_one(
        "SELECT hunts.id, hunts.uid, hunts.name, hunts.url, hunts.guild_id, "
        "hunts.role_id, hunts.archived, hunts.google_sheet_id, "
        "hunts.archive_google_sheet_id "
        "FROM hunts JOIN puzzles ON puzzles.hunt_id = hunts.id "
        "WHERE puzzles.id = %s",
        (puzzle_id,),
    )


async def get_hunt_for_tag_group(tag_group_id):
    """The hunt row a tag_group belongs to, for the rollup-rebuild access check."""
    return await fetch_one(
        "SELECT hunts.id, hunts.uid, hunts.name, hunts.url, hunts.guild_id, "
        "hunts.role_id, hunts.archived, hunts.google_sheet_id, "
        "hunts.archive_google_sheet_id "
        "FROM hunts JOIN tag_group ON tag_group.hunt_id = hunts.id "
        "WHERE tag_group.id = %s",
        (tag_group_id,),
    )


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

async def get_hunt_puzzles(hunt_id, guild_snowflake):
    """Flat list of every puzzle in the hunt, each with its rounds and last activity.

    A puzzle tagged into multiple rounds appears once with a ``rounds`` array.
    """
    rows = await fetch_all(
        "SELECT puzzles.id, puzzles.name, puzzles.url, puzzles.channel_id, "
        "puzzles.google_page_id, "
        "EXISTS(SELECT 1 FROM tag_group mt WHERE mt.target_puzzle_id = puzzles.id) AS is_target, "
        "puzzles.status, puzzles.solved, puzzles.archived, puzzles.solution, "
        "puzzles.priority, puzzles.puzzle_type, puzzles.notes, "
        "puzzles.start_time, puzzles.solve_time, "
        "tag_group.id AS tag_group_id, tag_group.name AS tag_group_name, "
        "tag_group.target_puzzle_id AS tag_group_target_puzzle_id, tag_group.group_code AS tag_group_group_code, "
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
                "sheet_page": row["google_page_id"],
                "is_meta": bool(row["is_target"]),
                "status": row["status"],
                "solved": bool(row["solved"]),
                "archived": bool(row["archived"]),
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
                "mini_puzzles": [],
                "rounds": [],
                "sheets": [],
            }
            puzzles[pid] = puz
        if row["tag_group_id"] is not None:
            # A tag_group whose target_puzzle_id is this puzzle is the group this puzzle is the target of.
            puz["rounds"].append(
                {
                    "id": row["tag_group_id"],
                    "name": row["tag_group_name"],
                    "group_code": row["tag_group_group_code"],
                    "is_target_of": row["tag_group_target_puzzle_id"] == pid,
                }
            )

    sheet_rows = await fetch_all(
        "SELECT s.id, s.puzzle_id, s.puzzle_name, s.google_page_id "
        "FROM additional_sheets s JOIN puzzles ON puzzles.id = s.puzzle_id "
        "WHERE puzzles.hunt_id = %s",
        (hunt_id,),
    )
    for s in sheet_rows:
        puz = puzzles.get(s["puzzle_id"])
        if puz is not None:
            puz["sheets"].append(
                {"id": s["id"], "name": s["puzzle_name"], "page_id": s["google_page_id"]}
            )

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

    return list(puzzles.values())


def _load_notes(raw):
    if not raw:
        return []
    try:
        notes = json.loads(raw)
        return notes if isinstance(notes, list) else []
    except (ValueError, TypeError):
        return []


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


def build_board(puzzles, rollups_by_tag_group=None):
    """Group a flat puzzle list into tag groups (metas) for the dashboard board.

    Returns a list of groups plus an "Unassigned" bucket. Each group reports its
    target solution and feeder solve counts, plus whether its roll-up sheet is stale
    (from rollups_by_tag_group). Puzzles in several groups appear in each.
    """
    rollups_by_tag_group = rollups_by_tag_group or {}
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
            }
            order.append(tag_group_id)
        return groups[tag_group_id]

    for puz in puzzles:
        if not puz["rounds"]:
            grp = ensure(0, "Unassigned", None)
            grp["puzzles"].append(puz)
            grp["num_feeders"] += 1
            if puz["solved"]:
                grp["num_solved_feeders"] += 1
            continue
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

    return [groups[rid] for rid in order]


# --- Activity ---------------------------------------------------------------

def get_evidence(hunt_id, source_puzzle_id=None, target_puzzle_id=None):
    """Return evidence items for a hunt with resolved puzzle names."""
    from bot.store import EvidenceJsonDb
    from bot.core.puzzle_service import resolve_puzzle_names
    items = EvidenceJsonDb.get_all(hunt_id, source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
    puzzle_ids = {i.source_puzzle_id for i in items} | {i.target_puzzle_id for i in items if i.target_puzzle_id}
    names = resolve_puzzle_names(puzzle_ids)
    return [
        {
            "id": i.id,
            "source_puzzle_id": i.source_puzzle_id,
            "source_puzzle_name": names.get(i.source_puzzle_id, ""),
            "target_puzzle_id": i.target_puzzle_id,
            "target_puzzle_name": names.get(i.target_puzzle_id, "") if i.target_puzzle_id else None,
            "note": i.note,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in items
    ]


async def get_guild_activity(guild_snowflake):
    rows = await fetch_all(
        "SELECT channel_id, last_human_message_at, last_author_name "
        "FROM channel_activity WHERE guild_id = %s "
        "ORDER BY last_human_message_at DESC",
        (guild_snowflake,),
    )
    return [
        {
            "channel_id": str(row["channel_id"]),
            "at": _iso(row["last_human_message_at"]),
            "author": row["last_author_name"],
        }
        for row in rows
    ]
