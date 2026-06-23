"""Dashboard read API and live-activity WebSocket."""
import asyncio
import logging
import time

from quart import Blueprint, current_app, jsonify, session, websocket

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

# How often the live-activity WebSocket re-checks that the connected user still
# belongs to a guild (so a kick/role-change ends the stream within this window).
_WS_AUTH_REFRESH_SECONDS = 60


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
    rollups = await data.get_rollups_by_round(hunt["id"])
    board = data.build_board(puzzles, rollups)
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


@api_bp.route("/rollups/<int:round_id>/answers")
@login_required
async def rollup_answers(round_id):
    """Read-only aggregated answers for a meta round.

    Returns {mode, answers: [{name, solution}, ...]}. No writes — the endpoint
    never creates a rollup row or triggers a sheet refresh."""
    user = current_user()
    hunt = await data.get_hunt_for_round(round_id)
    if not hunt:
        return jsonify({"error": "round_not_found"}), 404
    membership, roles_by_guild = await resolve_membership(user["id"])
    guilds_map = await data.get_guilds_map()
    if not hunt_visible(hunt, membership, roles_by_guild, guilds_map):
        return jsonify({"error": "forbidden"}), 403

    from bot.store import RoundJsonDb, RollupsJsonDb
    from bot.store.rollup_data import RollupData

    meta_round = RoundJsonDb.get_by_attr(id=round_id)
    if meta_round is None:
        return jsonify({"error": "round_not_found"}), 404

    rollup = RollupsJsonDb.get_by_attr(round_id=round_id)
    if rollup is None:
        # In-memory default — never persist.
        rollup = RollupData(round_id=round_id)

    bot = current_app.bot
    cog = bot.get_cog("Puzzles") if bot else None
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503

    members = await cog.service.select_rollup_members(meta_round, rollup)
    return jsonify({
        "mode": rollup.source_mode or "round",
        "answers": [{"name": p.name, "solution": p.solution} for p in members],
    })


@api_bp.route("/evidence")
@login_required
async def list_evidence():
    from quart import request
    hunt_uid = request.args.get("hunt_uid")
    source_puzzle_id = request.args.get("source_puzzle_id", type=int)
    target_puzzle_id = request.args.get("target_puzzle_id")
    if target_puzzle_id and target_puzzle_id != "unassigned":
        try:
            target_puzzle_id = int(target_puzzle_id)
        except ValueError:
            target_puzzle_id = None
    if not hunt_uid:
        return jsonify({"error": "hunt_uid required"}), 400
    hunt, guild_or_status = await _load_visible_hunt(hunt_uid)
    if hunt is None:
        return jsonify({"error": "hunt not found"}), guild_or_status
    items = data.get_evidence(hunt["id"], source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
    return jsonify(items)


@api_bp.route("/evidence", methods=["POST"])
@login_required
async def add_evidence():
    from quart import request
    from bot.store import EvidenceJsonDb, HuntJsonDb, PuzzleJsonDb
    body = await request.get_json()
    hunt_uid = body.get("hunt_uid")
    source_puzzle_id = body.get("source_puzzle_id")
    notes = body.get("notes", [])
    if not hunt_uid or not source_puzzle_id or not notes:
        return jsonify({"error": "hunt_uid, source_puzzle_id, and notes required"}), 400
    hunt, guild_or_status = await _load_visible_hunt(hunt_uid)
    if hunt is None:
        return jsonify({"error": "hunt not found"}), guild_or_status
    puzzle = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
    if puzzle is None or puzzle.hunt_id != hunt["id"]:
        return jsonify({"error": "puzzle not found in hunt"}), 404
    items = [EvidenceJsonDb.log(hunt["id"], source_puzzle_id, n) for n in notes if n.strip()]
    # Re-render the Evidence sheet tab (mirrors the bang !clue/!clues fire-and-forget
    # refresh). Best-effort: the clues are already persisted in the DB.
    if items:
        cog = current_app.bot.get_cog("Puzzles")
        hunt_obj = HuntJsonDb.get_by_attr(id=hunt["id"])
        if cog is not None and hunt_obj is not None:
            asyncio.create_task(cog.service.refresh_evidence(hunt_obj))
    return jsonify([{"id": i.id, "note": i.note} for i in items]), 201


@api_bp.websocket("/ws/activity")
async def ws_activity():
    """Push live channel-activity events for guilds the user belongs to."""
    user = session.get("user")
    if not user:
        await websocket.close(1008)  # policy violation / unauthenticated
        return
    membership, _ = await resolve_membership(user["id"])
    queue = events.subscribe()
    # Membership is resolved once at connect, so without this a user who is kicked
    # (or whose roles change) keeps receiving events until they disconnect.
    # Periodically re-resolve and drop the connection if they no longer belong to
    # any guild.
    last_refresh = time.monotonic()
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_WS_AUTH_REFRESH_SECONDS)
            except asyncio.TimeoutError:
                event = None

            now = time.monotonic()
            if event is None or (now - last_refresh) >= _WS_AUTH_REFRESH_SECONDS:
                membership, _ = await resolve_membership(user["id"])
                last_refresh = now
                if not membership:
                    await websocket.close(1008)  # no longer a member of any guild
                    return

            if event is not None and event.get("guild_id") in membership:
                await websocket.send_json(event)
    except asyncio.CancelledError:
        raise
    finally:
        events.unsubscribe(queue)
