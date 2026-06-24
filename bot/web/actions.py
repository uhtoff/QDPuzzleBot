"""Dashboard write-actions (Phase 2).

Writes go through the bot's own store objects on the event-loop thread (NOT the
read pool's worker threads), so they share the bot's connection and stay
serialized with the bot's own command DB calls. Channel/sheet side-effects reuse
the bot's cog logic so a web action behaves exactly like the matching ! command.
"""
import asyncio
import logging

import discord
from googleapiclient.errors import HttpError
from quart import Blueprint, current_app, jsonify, request

from . import data, events
from bot.web import data as web_data
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
from bot.core.errors import SheetRefreshError

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
    return jsonify({"error": "discord_error"}), 502


async def _audit(action, *, puzzle_id=None, hunt_uid=None, detail=None):
    """Best-effort audit log write — never raises."""
    try:
        u = current_user() or {}
        await web_data.execute(
            "INSERT INTO web_audit_log (user_id, username, action, puzzle_id, hunt_uid, detail) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (u.get("id"), u.get("username"), action, puzzle_id, hunt_uid, detail),
        )
    except Exception:
        logger.exception("web audit log write failed")


def _board_event(uid, guild_snowflake):
    """Lightweight board-invalidation event; guild_id must be the int snowflake to pass the
    ws_activity membership filter (api.py:171). None snowflake → None (event harmlessly dropped)."""
    return {"type": "board", "uid": uid,
            "guild_id": int(guild_snowflake) if guild_snowflake else None}


def _notify_board(uid, guild_snowflake):
    """Best-effort board push — never raises."""
    try:
        events.publish(_board_event(uid, guild_snowflake))
    except Exception:
        logger.exception("board push failed")


async def _refresh_parent_minimeta_pin(cog, guild, hunt_obj, puzzle):
    """If *puzzle* is a mini-puzzle, refresh its PARENT minimeta's pinned mini-list
    (the mini's solved/unsolved state shows there). Mirrors the bang !solve_mini/
    !unsolve_mini path, which calls info(update=True) on the minimeta channel.
    No-op for non-minis; web_refresh_info is itself best-effort."""
    if cog is None or not getattr(puzzle, "parent_puzzle_id", None):
        return
    from bot.store import PuzzleJsonDb
    parent = PuzzleJsonDb.get_by_attr(id=puzzle.parent_puzzle_id)
    if parent is not None:
        await cog.web_refresh_info(guild, hunt_obj, parent)


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


async def _visible_hunt_for_round(round_id):
    """Resolve a round's hunt and confirm the user may see it (same shape as above)."""
    user = current_user()
    hunt = await data.get_hunt_for_round(round_id)
    if not hunt:
        return None, (jsonify({"error": "round_not_found"}), 404)
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
        await _audit("note", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        _notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))
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
        await _audit("erase_note", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        _notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))
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
            await _audit("update_puzzle", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
            _notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))
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
            await _audit("add_sheet", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
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
            await _audit("remove_sheet", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
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
        warning = None
        try:
            await cog.service.solve(guild, hunt_obj, puzzle, solution)
        except SheetRefreshError as exc:
            # The solve is fully applied and the stale meta(s) are badged; surface
            # a non-fatal toast so the user knows a sheet needs a Rebuild.
            warning = str(exc)
        except HttpError:
            # Best-effort: the solve is recorded; a Sheets archive hiccup must
            # not fail the dashboard action (mirrors the old web path).
            logger.exception("web solve: sheet archive failed for puzzle %s", puzzle.id)
            warning = "A sheet update failed; the solve is saved. Use Rebuild to retry."
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        await cog.web_post_solved_embed(guild, hunt_obj, puzzle)
        await _refresh_parent_minimeta_pin(cog, guild, hunt_obj, puzzle)
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
        await _audit("solve", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"], detail=puzzle.solution)
        resp = {"ok": True, "solution": puzzle.solution}
        if warning:
            resp["sheet_warning"] = warning
        return jsonify(resp)


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
    if mode not in ("move", "add", "remove", "clear"):
        return jsonify({"error": "bad_mode"}), 400

    from bot.store import HuntJsonDb, PuzzleJsonDb, RoundJsonDb

    # "clear" unassigns from ALL metas, so it carries no meta_code to validate.
    meta_round = RoundJsonDb.get_by_attr(meta_code=meta_code)
    if mode != "clear" and (meta_round is None or meta_round.hunt_id != ctx["hunt"]["id"]):
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
        if mode == "clear" and puzzle.is_metapuzzle():
            # A metapuzzle can't be unassigned (mirrors "move", which never clears
            # a metapuzzle's tags — doing so would orphan its round).
            return jsonify({"error": "cannot_unassign_meta"}), 400

        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = bot.get_guild(int(snowflake)) if snowflake else None

        warning = None
        try:
            if mode == "move":
                await cog.service.move_to_meta(guild, puzzle, meta_round)
            elif mode == "add":
                await cog.service.add_to_meta(puzzle, meta_round)
            elif mode == "remove":
                await cog.service.remove_from_meta(puzzle, meta_round)
            elif mode == "clear":
                await cog.service.clear_metas(puzzle)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        except SheetRefreshError as exc:
            # The tag mutation already committed and the stale meta(s) are badged;
            # surface a non-fatal toast so the user can Rebuild.
            warning = str(exc)
        except HttpError:
            # The tag mutation already committed; a Sheets refresh hiccup is
            # best-effort and must not fail the retag (mirrors the old web path).
            # Unexpected (non-Sheets) errors propagate to a 500 rather than being
            # masked as success.
            logger.exception("retag: sheet refresh failed for puzzle %s", puzzle.id)
            warning = "A sheet refresh failed; the change is saved. Use Rebuild to retry."
        await _audit("retag", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"], detail=meta_code)
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        if hunt_obj is not None:
            asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
        resp = {"ok": True}
        if warning:
            resp["sheet_warning"] = warning
        return jsonify(resp)


# --- Rebuild a meta roll-up sheet (organiser only) --------------------------

@actions_bp.route("/rollups/<int:round_id>/rebuild", methods=["POST"])
@login_required
async def rebuild_rollup(round_id):
    """Rebuild one meta round's roll-up sheet, clearing its stale badge on success.
    Keyed on round_id (a rollup is per round); organiser-gated via the round's hunt.
    Serialization is handled inside refresh_meta's per-round rollup lock."""
    ctx, err = await _visible_hunt_for_round(round_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb, RoundJsonDb

    meta_round = RoundJsonDb.get_by_attr(id=round_id)
    if meta_round is None:
        return jsonify({"error": "round_not_found"}), 404

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    try:
        await cog.service.refresh_meta(hunt_obj, meta_round)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    except Exception:
        # refresh_meta has already re-badged last_ok=0; report the failure so the
        # SPA can toast and leave the badge in place for another retry.
        logger.exception("rebuild: refresh failed for round %s", round_id)
        return jsonify({"ok": False, "error": "internal_error"})
    await _audit("rebuild_rollup", hunt_uid=ctx["hunt"]["uid"], detail=str(round_id))
    _notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))
    return jsonify({"ok": True})


# --- Rollup source_mode setter (organiser only) -----------------------------

@actions_bp.route("/rollups/<int:round_id>", methods=["PATCH"])
@login_required
async def set_source_mode(round_id):
    """Set the aggregation source_mode for a meta round, then rebuild its sheet.
    Body: {"source_mode": "round" | "subtree" | "explicit"}. Organiser-gated."""
    ctx, err = await _visible_hunt_for_round(round_id)
    if err:
        return err
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    mode = (body.get("source_mode") or "").strip()
    allowed = ("round", "subtree", "explicit")
    if mode not in allowed:
        return jsonify({"error": "bad_source_mode", "allowed": allowed}), 400

    from bot.store import HuntJsonDb, RoundJsonDb, RollupsJsonDb
    from bot.store.rollup_data import RollupData

    meta_round = RoundJsonDb.get_by_attr(id=round_id)
    if meta_round is None:
        return jsonify({"error": "round_not_found"}), 404

    rollup = RollupsJsonDb.get_by_attr(round_id=meta_round.id)
    if rollup is None:
        rollup = RollupData(hunt_id=meta_round.hunt_id, round_id=meta_round.id)
    rollup.source_mode = mode
    RollupsJsonDb.commit(rollup)

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    try:
        await cog.service.refresh_meta(hunt_obj, meta_round)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    except Exception as exc:
        logger.exception("set_source_mode: refresh failed for round %s", round_id)
        return jsonify({"ok": False, "error": str(exc)})
    return jsonify({"ok": True, "source_mode": mode})


# --- Nesting setter (organiser only) ----------------------------------------

@actions_bp.route("/rounds/<int:round_id>/nest", methods=["POST"])
@login_required
async def set_nesting(round_id):
    """Nest a round under another, or clear nesting.
    Body: {"parent_round_id": <id>|null}. Null clears the nesting."""
    ctx, err = await _visible_hunt_for_round(round_id)
    if err:
        return err
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    parent_id = body.get("parent_round_id")  # None/null clears — int nests

    from bot.store import RoundJsonDb

    child = RoundJsonDb.get_by_attr(id=round_id)
    if child is None:
        return jsonify({"error": "round_not_found"}), 404

    # Validate parent exists (and belongs to same hunt) if nesting, not clearing
    if parent_id is not None:
        parent = RoundJsonDb.get_by_attr(id=parent_id)
        if parent is None or parent.hunt_id != child.hunt_id:
            return jsonify({"error": "bad_parent_round"}), 400

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503

    try:
        await cog.service.set_parent_group(child, parent_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
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
    """Create a puzzle (channel + sheet) via the bot.

    An empty ``meta_code`` creates an *unassigned* puzzle (no meta tag); the
    channel lands in the hunt category (see PuzzleService.create_puzzle).
    """
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
    meta_round = None
    if meta_code:
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
    await _audit("create_puzzle", puzzle_id=puzzle.id, hunt_uid=uid, detail=name)
    # Best-effort: keep the Unassigned Puzzles tab in sync (a no-meta create adds one).
    asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
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
    await _audit("create_meta", puzzle_id=puzzle.id, hunt_uid=uid, detail=new_round.meta_code)
    return jsonify({"ok": True, "round_id": new_round.id, "meta_code": new_round.meta_code})


# --- Minimeta mini-puzzle creation (organiser only) --------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/minis", methods=["POST"])
@login_required
async def create_mini(puzzle_id):
    """Create a mini-puzzle (Discord thread) under a minimeta puzzle."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        minimeta = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if minimeta is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = bot.get_guild(int(snowflake)) if snowflake else None
        try:
            mini = await cog.service.create_mini(guild, hunt_obj, minimeta, name)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        await _audit("create_mini", puzzle_id=puzzle_id, hunt_uid=ctx["hunt"]["uid"], detail=name)
        _notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))
        # The new mini shows in the PARENT minimeta's pinned mini-list; refresh that
        # pin from the web edge too (the bang !add_mini path calls info(update=True)).
        await cog.web_refresh_info(guild, hunt_obj, minimeta)
        return jsonify({
            "ok": True,
            "mini_id": mini.id,
            "name": mini.name,
            "channel_id": str(mini.channel_id) if mini.channel_id else None,
        })


# --- Unsolve a puzzle (any member of the hunt's guild) -----------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/unsolve", methods=["POST"])
@login_required
async def unsolve_puzzle(puzzle_id):
    """Mark a solved puzzle unsolved via the bot."""
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
        if not puzzle.solved and not puzzle.solution:
            return jsonify({"error": "not_solved"}), 400

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = bot.get_guild(int(snowflake)) if snowflake else None
        try:
            await cog.service.unsolve(guild, hunt_obj, puzzle)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        await _audit("unsolve", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        _notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))
        await _refresh_parent_minimeta_pin(cog, guild, hunt_obj, puzzle)

        return jsonify({"ok": True})


@actions_bp.route("/evidence/<int:item_id>", methods=["PATCH"])
@login_required
async def update_evidence(item_id):
    from bot.store import EvidenceJsonDb, HuntJsonDb
    body = await request.get_json()
    target_puzzle_id = body.get("target_puzzle_id")
    item = EvidenceJsonDb.set_target(item_id, target_puzzle_id)
    if item is None:
        return jsonify({"error": "not found"}), 404
    # Re-render the Evidence sheet so the new Target column value shows (mirrors the
    # bang refresh). Best-effort: the routing is already persisted.
    cog = current_app.bot.get_cog("Puzzles")
    hunt_obj = HuntJsonDb.get_by_attr(id=item.hunt_id)
    if cog is not None and hunt_obj is not None:
        asyncio.create_task(cog.service.refresh_evidence(hunt_obj))
    return jsonify({"id": item.id, "target_puzzle_id": item.target_puzzle_id})
