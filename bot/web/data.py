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
        "google_sheet_id, solved_sheet_id, start_time, solve_time "
        "FROM hunts"
    )


async def get_hunt_by_uid(uid):
    return await fetch_one(
        "SELECT id, uid, name, url, guild_id, role_id, archived, "
        "google_sheet_id, solved_sheet_id, start_time, solve_time, "
        "start_timestamp, username, password, url_sep, puzzle_prefix "
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


async def get_hunt_by_channel_id(channel_id):
    """Resolve the hunt that owns a puzzle channel. Works for archived hunts too
    (the hunt row persists after the channel is deleted), so it authorizes frozen
    transcripts that a live channel-permission check could not."""
    return await fetch_one(
        "SELECT h.* FROM hunts h JOIN puzzles p ON p.hunt_id = h.id "
        "WHERE p.channel_id = %s LIMIT 1",
        (channel_id,),
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
        "puzzles.status, puzzles.solved, puzzles.on_solved_sheet, puzzles.solution, "
        "puzzles.priority, puzzles.puzzle_type, puzzles.notes, "
        "puzzles.start_time, puzzles.solve_time, "
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
                "mini_puzzles": [],
                "rounds": [],
                "sheets": [],
            }
            puzzles[pid] = puz
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

    puzzle_list = list(puzzles.values())
    derive_meta_kind(puzzle_list)
    derive_is_minimeta(puzzle_list)
    return puzzle_list


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
