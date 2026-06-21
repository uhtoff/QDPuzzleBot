"""Dashboard write-actions (Phase 2).

Writes go through the bot's own store objects on the event-loop thread (NOT the
read pool's worker threads), so they share the bot's connection and stay
serialized with the bot's own command DB calls. Channel/sheet side-effects reuse
the bot's cog logic so a web action behaves exactly like the matching ! command.
"""
import logging

from quart import Blueprint, current_app, jsonify, request

from . import data
from .auth import (
    ORGANISER_ROLES,
    current_user,
    hunt_visible,
    login_required,
    resolve_membership,
    resolve_role_names,
)

logger = logging.getLogger(__name__)

actions_bp = Blueprint("actions", __name__, url_prefix="/api")

MAX_NOTE_LEN = 1000


def _display_name(user):
    return user.get("global_name") or user.get("username") or "web user"


async def _visible_hunt_for_puzzle(puzzle_id):
    """Resolve a puzzle's hunt and confirm the user may see it.

    Returns (context, None) on success, or (None, (response, status)) to return.
    context has keys: hunt (row), guild_snowflake.
    """
    user = current_user()
    hunt = await data.get_hunt_for_puzzle(puzzle_id)
    if not hunt:
        return None, (jsonify({"error": "puzzle_not_found"}), 404)
    membership, roles_by_guild = await resolve_membership(user["id"])
    guilds_map = await data.get_guilds_map()
    if not hunt_visible(hunt, membership, roles_by_guild, guilds_map):
        return None, (jsonify({"error": "forbidden"}), 403)
    guild = guilds_map.get(hunt["guild_id"], {})
    return {"hunt": hunt, "guild_snowflake": guild.get("guild_snowflake")}, None


# --- Notes (any member of the hunt's guild) ---------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/notes", methods=["POST"])
@login_required
async def add_note(puzzle_id):
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err

    body = await request.get_json(silent=True) or {}
    text = (body.get("note") or "").strip()
    if not text:
        return jsonify({"error": "empty_note"}), 400
    text = text[:MAX_NOTE_LEN]

    from bot.store import PuzzleJsonDb

    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    if puzzle is None:
        return jsonify({"error": "puzzle_not_found"}), 404
    puzzle.notes.append(f"{text} - {_display_name(current_user())} (web)")
    PuzzleJsonDb.commit(puzzle)
    return jsonify({"notes": puzzle.notes})


@actions_bp.route("/puzzles/<int:puzzle_id>/notes/<int:index>", methods=["DELETE"])
@login_required
async def erase_note(puzzle_id, index):
    """Erase a note by 1-based index (matching the bot's !erase_note)."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err

    from bot.store import PuzzleJsonDb

    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    if puzzle is None:
        return jsonify({"error": "puzzle_not_found"}), 404
    if not (1 <= index <= len(puzzle.notes)):
        return jsonify({"error": "bad_index", "notes": puzzle.notes}), 400
    del puzzle.notes[index - 1]
    PuzzleJsonDb.commit(puzzle)
    return jsonify({"notes": puzzle.notes})


# --- Shared helper for organiser-gated actions (used by retag/create next) ---

async def require_organiser(guild_snowflake, roles=ORGANISER_ROLES):
    """Return None if the user holds an allowed role in the guild, else an error tuple."""
    names = await resolve_role_names(current_user()["id"], guild_snowflake)
    if names is None:
        return jsonify({"error": "forbidden"}), 403
    if not any(r in names for r in roles):
        return jsonify({"error": "insufficient_role"}), 403
    return None


# --- Retag puzzles between metas (organiser only) ---------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/meta", methods=["POST"])
@login_required
async def retag_puzzle(puzzle_id):
    """Move/add/remove a puzzle to/from a metapuzzle, via the bot's own logic.

    Body: {"meta_code": "ABC123", "mode": "move"|"add"|"remove"}. "move" also
    relocates the Discord channel to the meta's category (mirrors !move_to_meta).
    """
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    meta_code = (body.get("meta_code") or "").strip()
    mode = body.get("mode") or "move"
    if mode not in ("move", "add", "remove"):
        return jsonify({"error": "bad_mode"}), 400

    from bot.store import PuzzleJsonDb, RoundJsonDb

    meta_round = RoundJsonDb.get_by_attr(meta_code=meta_code)
    if meta_round is None or meta_round.hunt_id != ctx["hunt"]["id"]:
        return jsonify({"error": "bad_meta_code"}), 400
    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    if puzzle is None:
        return jsonify({"error": "puzzle_not_found"}), 404
    if mode == "remove" and puzzle.id == meta_round.meta_id:
        return jsonify({"error": "cannot_remove_own_meta"}), 400

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None

    if mode == "move":
        await cog.web_move_to_meta(guild, puzzle, meta_round)
    elif mode == "add":
        await cog.web_add_to_meta(puzzle, meta_round)
    else:
        await cog.web_remove_from_meta(puzzle, meta_round)
    return jsonify({"ok": True})
