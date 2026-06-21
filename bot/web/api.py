"""Dashboard read API and live-activity WebSocket."""
import asyncio
import logging

from quart import Blueprint, jsonify, session, websocket

from . import data, events
from .auth import (
    current_user,
    hunt_visible,
    login_required,
    resolve_membership,
    user_can_manage,
)

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _sheet_base(sheet_id):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid=" if sheet_id else None


@api_bp.route("/hunts")
@login_required
async def list_hunts():
    """Hunts visible to the current user, split into active and archived (old)."""
    user = current_user()
    membership, roles_by_guild = await resolve_membership(user["id"])
    guilds_map = await data.get_guilds_map()
    hunts = await data.get_all_hunts()

    active, archived = [], []
    for hunt in hunts:
        if not hunt_visible(hunt, membership, roles_by_guild, guilds_map):
            continue
        payload = data.hunt_to_json(hunt, guilds_map)
        (archived if hunt["archived"] else active).append(payload)
    return jsonify({"active": active, "archived": archived})


async def _load_visible_hunt(uid):
    """Return (hunt_row, guild_info) if the current user may see it, else (None, status)."""
    user = current_user()
    hunt = await data.get_hunt_by_uid(uid)
    if not hunt:
        return None, 404
    membership, roles_by_guild = await resolve_membership(user["id"])
    guilds_map = await data.get_guilds_map()
    if not hunt_visible(hunt, membership, roles_by_guild, guilds_map):
        return None, 403
    return hunt, guilds_map.get(hunt["guild_id"], {})


@api_bp.route("/hunts/<uid>")
@login_required
async def hunt_detail(uid):
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        return jsonify({"error": "not_found_or_forbidden"}), guild_or_status

    guild_snowflake = guild_or_status.get("guild_snowflake")
    puzzles = await data.get_hunt_puzzles(hunt["id"], guild_snowflake)
    board = data.build_board(puzzles)
    solved = sum(1 for p in puzzles if p["solved"])
    # Archived hunts are read-only even for organisers.
    can_manage = (not hunt["archived"]) and await user_can_manage(current_user()["id"], guild_snowflake)
    return jsonify({
        "hunt": {
            "uid": hunt["uid"],
            "name": hunt["name"],
            "url": hunt["url"],
            "archived": bool(hunt["archived"]),
            "guild_snowflake": str(guild_snowflake) if guild_snowflake else None,
            "sheet_base_url": _sheet_base(hunt["google_sheet_id"]),
            "archive_sheet_base_url": _sheet_base(hunt["archive_google_sheet_id"]),
            "can_manage": can_manage,
        },
        "summary": {"total": len(puzzles), "solved": solved},
        "board": board,
    })


@api_bp.route("/hunts/<uid>/puzzles")
@login_required
async def hunt_puzzles(uid):
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        return jsonify({"error": "not_found_or_forbidden"}), guild_or_status
    puzzles = await data.get_hunt_puzzles(hunt["id"], guild_or_status.get("guild_snowflake"))
    return jsonify({"puzzles": puzzles})


@api_bp.websocket("/ws/activity")
async def ws_activity():
    """Push live channel-activity events for guilds the user belongs to."""
    user = session.get("user")
    if not user:
        await websocket.close(1008)  # policy violation / unauthenticated
        return
    membership, _ = await resolve_membership(user["id"])
    queue = events.subscribe()
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            event = await queue.get()
            if event.get("guild_id") in membership:
                await websocket.send_json(event)
    except asyncio.CancelledError:
        raise
    finally:
        events.unsubscribe(queue)
