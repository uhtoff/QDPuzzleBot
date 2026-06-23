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
        "google_sheet_id, archive_google_sheet_id, start_time, solve_time "
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


async def get_hunt_for_round(round_id):
    """The hunt row a round belongs to, for the rollup-rebuild access check."""
    return await fetch_one(
        "SELECT hunts.id, hunts.uid, hunts.name, hunts.url, hunts.guild_id, "
        "hunts.role_id, hunts.archived, hunts.google_sheet_id, "
        "hunts.archive_google_sheet_id "
        "FROM hunts JOIN rounds ON rounds.hunt_id = hunts.id "
        "WHERE rounds.id = %s",
        (round_id,),
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
        "puzzles.google_page_id, puzzles.metapuzzle, puzzles.metameta, "
        "puzzles.status, puzzles.solved, puzzles.archived, puzzles.solution, "
        "puzzles.priority, puzzles.puzzle_type, puzzles.notes, "
        "puzzles.start_time, puzzles.solve_time, "
        "rounds.id AS round_id, rounds.name AS round_name, "
        "rounds.meta_id AS round_meta_id, rounds.meta_code AS round_meta_code, "
        "act.last_human_message_at, act.last_author_name "
        "FROM puzzles "
        "LEFT JOIN tags ON puzzles.id = tags.puzzle_id "
        "LEFT JOIN rounds ON tags.round_id = rounds.id "
        "LEFT JOIN channel_activity act ON act.channel_id = puzzles.channel_id "
        "WHERE puzzles.hunt_id = %s AND puzzles.parent_puzzle_id IS NULL "
        "ORDER BY puzzles.metapuzzle DESC, puzzles.solved ASC, puzzles.id ASC",
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
                "is_meta": bool(row["metapuzzle"]),
                "is_metameta": bool(row["metameta"]),
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
        if row["round_id"] is not None:
            # A round whose meta_id is this puzzle is the round this puzzle "metas".
            puz["rounds"].append(
                {
                    "id": row["round_id"],
                    "name": row["round_name"],
                    "meta_code": row["round_meta_code"],
                    "is_meta_of": row["round_meta_id"] == pid,
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


async def get_rollups_by_round(hunt_id):
    """Map each meta round in the hunt to its roll-up sheet's freshness, for the
    dashboard "sheet stale" badge (M5). A round with no rollup row is treated as
    not-stale (never refreshed yet, or no sheet). Includes source_mode (M7)."""
    rows = await fetch_all(
        "SELECT round_id, last_ok, last_error, source_mode FROM rollups WHERE hunt_id = %s",
        (hunt_id,),
    )
    return {
        row["round_id"]: {
            "stale": not row["last_ok"],
            "error": row["last_error"],
            "source_mode": row.get("source_mode") or "round",
        }
        for row in rows
    }


def build_board(puzzles, rollups_by_round=None):
    """Group a flat puzzle list into rounds (metas) for the dashboard board.

    Returns a list of groups plus an "Unassigned" bucket. Each group reports its
    meta solution and feeder solve counts, plus whether its roll-up sheet is stale
    (from rollups_by_round). Puzzles in several rounds appear in each.
    """
    rollups_by_round = rollups_by_round or {}
    groups = {}
    order = []

    def ensure(round_id, name, meta_code):
        if round_id not in groups:
            ru = rollups_by_round.get(round_id, {})
            groups[round_id] = {
                "round_id": round_id,
                "name": name,
                "meta_code": meta_code,
                "puzzles": [],
                "meta_solution": None,
                "meta_solved": False,
                "num_feeders": 0,
                "num_solved_feeders": 0,
                "rollup_stale": bool(ru.get("stale", False)),
                "rollup_error": ru.get("error"),
                "source_mode": ru.get("source_mode") or "round",
            }
            order.append(round_id)
        return groups[round_id]

    for puz in puzzles:
        if not puz["rounds"]:
            grp = ensure(0, "Unassigned", None)
            grp["puzzles"].append(puz)
            grp["num_feeders"] += 1
            if puz["solved"]:
                grp["num_solved_feeders"] += 1
            continue
        for rnd in puz["rounds"]:
            grp = ensure(rnd["id"], rnd["name"], rnd["meta_code"])
            grp["puzzles"].append(puz)
            if rnd["is_meta_of"]:
                grp["meta_solved"] = puz["solved"]
                grp["meta_solution"] = puz["solution"]
            else:
                grp["num_feeders"] += 1
                if puz["solved"]:
                    grp["num_solved_feeders"] += 1

    return [groups[rid] for rid in order]


# --- Activity ---------------------------------------------------------------

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
