"""Dashboard write-actions (Phase 2).

Writes go through the bot's own store objects on the event-loop thread (NOT the
read pool's worker threads), so they share the bot's connection and stay
serialized with the bot's own command DB calls. Channel/sheet side-effects reuse
the bot's cog logic so a web action behaves exactly like the matching ! command.
"""
import logging

import discord
from googleapiclient.errors import HttpError
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
from bot.core.constants import STATUSES, PRIORITIES
from bot.core.locks import puzzle_lock

logger = logging.getLogger(__name__)

actions_bp = Blueprint("actions", __name__, url_prefix="/api")

MAX_NOTE_LEN = 1000

PUZZLE_STATUSES = set(STATUSES)
PUZZLE_PRIORITIES = set(PRIORITIES)


def _display_name(user):
    return user.get("global_name") or user.get("username") or "web user"


def _discord_error_response(exc):
    """Map a Discord HTTP/rate-limit error to a friendly JSON response."""
    if isinstance(exc, discord.RateLimited):
        return jsonify({"error": "discord_rate_limited", "retry_after": int(getattr(exc, "retry_after", 0))}), 429
    return jsonify({"error": "discord_error", "detail": str(exc)}), 502


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


async def _visible_hunt_by_uid(uid):
    """Resolve a hunt by uid and confirm the user may see it (same shape as above)."""
    user = current_user()
    hunt = await data.get_hunt_by_uid(uid)
    if not hunt:
        return None, (jsonify({"error": "hunt_not_found"}), 404)
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

    async with puzzle_lock(puzzle_id):
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

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        if not (1 <= index <= len(puzzle.notes)):
            return jsonify({"error": "bad_index", "notes": puzzle.notes}), 400
        del puzzle.notes[index - 1]
        PuzzleJsonDb.commit(puzzle)
        return jsonify({"notes": puzzle.notes})


# --- Status / priority / type and solve (any member of the hunt's guild) ----

@actions_bp.route("/puzzles/<int:puzzle_id>", methods=["PATCH"])
@login_required
async def update_puzzle(puzzle_id):
    """Update status / priority / puzzle_type and refresh the pinned info message."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    body = await request.get_json(silent=True) or {}
    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        changed = False
        if "status" in body:
            value = (body.get("status") or "").strip()
            if value.lower() not in PUZZLE_STATUSES:
                return jsonify({"error": "bad_status"}), 400
            puzzle.status = value
            changed = True
        if "priority" in body:
            value = (body.get("priority") or "").strip()
            if value.lower() not in PUZZLE_PRIORITIES:
                return jsonify({"error": "bad_priority"}), 400
            puzzle.priority = value
            changed = True
        if "puzzle_type" in body:
            puzzle.puzzle_type = (body.get("puzzle_type") or "").strip()
            changed = True

        if changed:
            PuzzleJsonDb.commit(puzzle)
            cog = current_app.bot.get_cog("Puzzles")
            if cog is not None:
                snowflake = ctx["guild_snowflake"]
                guild = current_app.bot.get_guild(int(snowflake)) if snowflake else None
                hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
                await cog.web_refresh_info(guild, hunt_obj, puzzle)
    return jsonify({"ok": True})


# --- Additional sheets (any member of the hunt's guild, like !add_sheet) -----

@actions_bp.route("/puzzles/<int:puzzle_id>/sheets", methods=["POST"])
@login_required
async def add_sheet(puzzle_id):
    """Create an additional working sheet (name optional), via the bot."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        cog = current_app.bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = current_app.bot.get_guild(int(snowflake)) if snowflake else None
        try:
            sheet = await cog.service.add_sheet(hunt_obj, puzzle, name)
            await cog.web_refresh_info(guild, hunt_obj, puzzle)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        return jsonify({
            "ok": True,
            "sheet": {"id": sheet.id, "name": sheet.puzzle_name, "page_id": sheet.google_page_id},
        })


@actions_bp.route("/puzzles/<int:puzzle_id>/sheets/<int:sheet_id>", methods=["DELETE"])
@login_required
async def remove_sheet(puzzle_id, sheet_id):
    """Remove an additional sheet (deletes the Google tab and the DB row)."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        cog = current_app.bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = current_app.bot.get_guild(int(snowflake)) if snowflake else None
        try:
            await cog.service.remove_sheet(hunt_obj, puzzle, sheet_id)
            await cog.web_refresh_info(guild, hunt_obj, puzzle)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 404
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        return jsonify({"ok": True})


@actions_bp.route("/puzzles/<int:puzzle_id>/solve", methods=["POST"])
@login_required
async def solve_puzzle(puzzle_id):
    """Mark a puzzle solved via the bot (posts the SOLVED embed, archives the sheet)."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    body = await request.get_json(silent=True) or {}
    solution = (body.get("solution") or "").strip()

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        if not solution and not puzzle.solution:
            return jsonify({"error": "empty_solution"}), 400

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = bot.get_guild(int(snowflake)) if snowflake else None
        try:
            await cog.service.solve(guild, hunt_obj, puzzle, solution)
        except HttpError:
            # Best-effort: the solve is recorded; a Sheets archive hiccup must
            # not fail the dashboard action (mirrors the old web path).
            logger.exception("web solve: sheet archive failed for puzzle %s", puzzle.id)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        await cog.web_post_solved_embed(guild, hunt_obj, puzzle)
        return jsonify({"ok": True, "solution": puzzle.solution})


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
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
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
    # Locks only the puzzle being retagged (keyed on puzzle_id), matching the bang
    # !move_to_meta decorator — a concurrent write to the meta puzzle itself is not
    # serialized against this retag, which is consistent with current behaviour.
    async with puzzle_lock(puzzle_id):
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

        try:
            if mode == "move":
                await cog.service.move_to_meta(guild, puzzle, meta_round)
            elif mode == "add":
                await cog.service.add_to_meta(puzzle, meta_round)
            elif mode == "remove":
                await cog.service.remove_from_meta(puzzle, meta_round)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        except HttpError:
            # The tag mutation already committed; a Sheets refresh hiccup is
            # best-effort and must not fail the retag (mirrors the old web path).
            # Unexpected (non-Sheets) errors propagate to a 500 rather than being
            # masked as success.
            logger.exception("retag: sheet refresh failed for puzzle %s", puzzle.id)
        return jsonify({"ok": True})


# --- Create puzzles and metas (organiser only) ------------------------------

async def _organiser_hunt(uid):
    """Resolve a visible, non-archived hunt and require organiser. Returns (ctx, err)."""
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return None, err
    if ctx["hunt"]["archived"]:
        return None, (jsonify({"error": "hunt_archived"}), 403)
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return None, perm
    return ctx, None


@actions_bp.route("/hunts/<uid>/puzzles", methods=["POST"])
@login_required
async def create_puzzle(uid):
    """Create a puzzle (channel + sheet) inside an existing meta, via the bot."""
    ctx, err = await _organiser_hunt(uid)
    if err:
        return err
    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    meta_code = (body.get("meta_code") or "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import HuntJsonDb, PuzzleJsonDb, RoundJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    meta_round = RoundJsonDb.get_by_attr(meta_code=meta_code)
    if meta_round is None or meta_round.hunt_id != hunt_obj.id:
        return jsonify({"error": "bad_meta_code"}), 400
    if PuzzleJsonDb.check_duplicates_in_hunt(name, hunt_obj.id):
        return jsonify({"error": "duplicate_name"}), 409

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    guild = bot.get_guild(int(ctx["guild_snowflake"]))
    try:
        puzzle = await cog.web_create_puzzle(guild, hunt_obj, meta_round, name)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    return jsonify({"ok": True, "puzzle_id": puzzle.id, "name": puzzle.name})


@actions_bp.route("/hunts/<uid>/metas", methods=["POST"])
@login_required
async def create_meta(uid):
    """Create a new metapuzzle (category + meta channel), via the bot (like !mp)."""
    ctx, err = await _organiser_hunt(uid)
    if err:
        return err
    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import HuntJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    guild = bot.get_guild(int(ctx["guild_snowflake"]))
    try:
        new_round, puzzle = await cog.web_create_round(guild, hunt_obj, name)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    return jsonify({"ok": True, "round_id": new_round.id, "meta_code": new_round.meta_code})
