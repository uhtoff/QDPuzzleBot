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
    ADMIN_ROLES,
    ORGANISER_ROLES,
    current_user,
    hunt_visible,
    login_required,
    resolve_membership,
    resolve_role_names,
)
from bot.core.locks import puzzle_lock
from bot.core.errors import SheetRefreshError

logger = logging.getLogger(__name__)

actions_bp = Blueprint("actions", __name__, url_prefix="/api")

MAX_NOTE_LEN = 1000

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


async def _resolve_visible_hunt(lookup_coro, not_found_key):
    """Shared core: await lookup_coro, check visibility, return (context, None) or (None, err).

    context has keys: hunt (row), guild_snowflake.
    """
    user = current_user()
    hunt = await lookup_coro
    if not hunt:
        return None, (jsonify({"error": not_found_key}), 404)
    membership, roles_by_guild = await resolve_membership(user["id"])
    guilds_map = await data.get_guilds_map()
    if not hunt_visible(hunt, membership, roles_by_guild, guilds_map):
        return None, (jsonify({"error": "forbidden"}), 403)
    guild = guilds_map.get(hunt["guild_id"], {})
    return {"hunt": hunt, "guild_snowflake": guild.get("guild_snowflake")}, None


async def _visible_hunt_for_puzzle(puzzle_id):
    return await _resolve_visible_hunt(data.get_hunt_for_puzzle(puzzle_id), "puzzle_not_found")


async def _visible_hunt_by_uid(uid):
    return await _resolve_visible_hunt(data.get_hunt_by_uid(uid), "hunt_not_found")


async def _visible_hunt_for_tag_group(tag_group_id):
    return await _resolve_visible_hunt(data.get_hunt_for_tag_group(tag_group_id), "round_not_found")


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

    from bot.store import HuntJsonDb, PuzzleJsonDb

    cog = current_app.bot.get_cog("Puzzles")

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        attribution = f"{_display_name(current_user())} (web)"
        await cog.service.add_note(hunt_obj, puzzle, text, attribution)
        await _audit("note", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        # board push is emitted by the service (notifier.board_changed)
        return jsonify({"notes": puzzle.notes})


@actions_bp.route("/puzzles/<int:puzzle_id>/notes/<int:index>", methods=["DELETE"])
@login_required
async def erase_note(puzzle_id, index):
    """Erase a note by 1-based index (matching the bot's !erase_note)."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err

    from bot.store import HuntJsonDb, PuzzleJsonDb

    cog = current_app.bot.get_cog("Puzzles")

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        erased = await cog.service.erase_note(hunt_obj, puzzle, index)
        if not erased:
            return jsonify({"error": "bad_index", "notes": puzzle.notes}), 400
        await _audit("erase_note", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        # board push is emitted by the service (notifier.board_changed)
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

        cog = current_app.bot.get_cog("Puzzles")
        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])

        changed = False
        for web_field, db_field, err_key in [
            ("status", "status", "bad_status"),
            ("priority", "priority", "bad_priority"),
            ("puzzle_type", "puzzle_type", None),
        ]:
            if web_field not in body:
                continue
            value = (body.get(web_field) or "").strip()
            try:
                await cog.service.update_puzzle_field(hunt_obj, puzzle, db_field, value)
            except ValueError:
                return jsonify({"error": err_key or f"bad_{web_field}"}), 400
            changed = True

        if changed:
            await _audit("update_puzzle", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
            # board push is emitted by the service (notifier.board_changed)
            if cog is not None:
                snowflake = ctx["guild_snowflake"]
                guild = current_app.bot.get_guild(int(snowflake)) if snowflake else None
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
    """Mark a puzzle solved / partially solved / change or add solution via the bot.

    Body: {"solution": "...", "mode": "full"|"partial"|"change"|"add"}.
    Default mode is "full".  mark-complete = frontend sends
    mode:"full", solution:"✅" (no new mode needed).
    """
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    body = await request.get_json(silent=True) or {}
    solution = (body.get("solution") or "").strip()
    mode = (body.get("mode") or "full").strip().lower()
    if mode not in ("full", "partial", "change", "add"):
        return jsonify({"error": "bad_mode"}), 400

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        # Empty-solution guards (mirrors cog's guards per mode).
        if mode == "full" and not solution and not puzzle.solution:
            return jsonify({"error": "empty_solution"}), 400
        if mode in ("change", "add") and not solution:
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
            if mode == "full":
                await cog.service.solve(guild, hunt_obj, puzzle, solution)
            elif mode == "partial":
                await cog.service.add_partial(guild, hunt_obj, puzzle, solution)
            elif mode == "change":
                await cog.service.change_solution(guild, hunt_obj, puzzle, solution)
            else:  # add
                await cog.service.add_solution(guild, hunt_obj, puzzle, solution)
        except SheetRefreshError as exc:
            warning = str(exc)
        except HttpError:
            logger.exception("web solve: sheet update failed for puzzle %s", puzzle.id)
            warning = "A sheet update failed; the change is saved. Use Rebuild to retry."
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)

        # Post solved embed + refresh minimeta pin only for full solves.
        if mode == "full":
            await cog.web_post_solved_embed(guild, hunt_obj, puzzle)
            await _refresh_parent_minimeta_pin(cog, guild, hunt_obj, puzzle)

        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
        await _audit(
            f"solve_{mode}",
            puzzle_id=puzzle.id,
            hunt_uid=ctx["hunt"]["uid"],
            detail=puzzle.solution,
        )
        resp = {"ok": True, "solution": puzzle.solution}
        if warning:
            resp["sheet_warning"] = warning
        return jsonify(resp)


# --- Link a puzzle (any member of the hunt's guild) ------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/link", methods=["POST"])
@login_required
async def set_puzzle_link(puzzle_id):
    """Set the puzzle link URL, via the bot (mirrors !link). No role gate."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    body = await request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()

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

        await cog.service.set_puzzle_url(guild, hunt_obj, puzzle, url)
        await _audit("link", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))

    return jsonify({"ok": True})


# --- Log a clue (any member of the hunt's guild) --------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/clues", methods=["POST"])
@login_required
async def log_clue(puzzle_id):
    """Log a single clue/note for a puzzle, via the bot (mirrors !clue). No role gate."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    body = await request.get_json(silent=True) or {}
    note = (body.get("note") or "").strip()
    if not note:
        return jsonify({"error": "empty_note"}), 400

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        cog = current_app.bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503

        cog.service.log_clues(hunt_obj, puzzle, [note])
        asyncio.create_task(cog.service.refresh_evidence(hunt_obj))
        asyncio.create_task(cog.service.refresh_informs_targets(hunt_obj, puzzle))
        await _audit("clue", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])

    return jsonify({"ok": True})


# --- Make minimeta (organiser+) ------------------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/make-minimeta", methods=["POST"])
@login_required
async def make_minimeta_puzzle(puzzle_id):
    """Mark a puzzle as a minimeta cluster parent (mirrors !make_minimeta)."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    perm = await require_organiser(ctx["guild_snowflake"])
    if perm is not None:
        return perm

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        cog = current_app.bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503

        await cog.service.make_minimeta(puzzle)
        await _audit("make_minimeta", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))

    return jsonify({"ok": True})


# --- Convert puzzle to meta (organiser+) ----------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-meta", methods=["POST"])
@login_required
async def convert_to_meta(puzzle_id):
    """Convert an existing feeder puzzle into a metapuzzle, via the bot (mirrors !ctm)."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    perm = await require_organiser(ctx["guild_snowflake"])
    if perm is not None:
        return perm

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        if puzzle.is_target:
            return jsonify({"error": "already_meta"}), 400

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        cog = current_app.bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503

        warning = None
        try:
            tag_group, _summary = await cog.service.convert_to_type(
                None, hunt_obj, puzzle, "Metapuzzle"
            )
        except RuntimeError as exc:
            if str(exc) == "already_meta":
                return jsonify({"error": "already_meta"}), 400
            raise
        except (HttpError, SheetRefreshError):
            logger.exception("convert_to_meta: sheet update failed for puzzle %s", puzzle_id)
            warning = "A sheet update failed; the change is saved. Use Rebuild to retry."

        await _audit("convert_to_meta", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))

    resp = {"ok": True}
    if warning:
        resp["sheet_warning"] = warning
    return jsonify(resp)


# --- Rename a puzzle (organiser+) -----------------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/rename", methods=["POST"])
@login_required
async def rename_puzzle(puzzle_id):
    """Rename a puzzle, via the bot (mirrors !rename). Organiser+ only."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    perm = await require_organiser(ctx["guild_snowflake"])
    if perm is not None:
        return perm

    body = await request.get_json(silent=True) or {}
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import HuntJsonDb, PuzzleJsonDb

    # Duplicate check — match bang behaviour: reject on clash.
    if PuzzleJsonDb.check_duplicates_in_hunt(new_name, ctx["hunt"]["id"]):
        return jsonify({"error": "duplicate_name"}), 400

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        cog = current_app.bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503

        # Pass the guild so the service renames the Discord channel too (mirrors
        # the bang); safe_rename tolerates the rename rate-limit via deferral.
        snowflake = ctx["guild_snowflake"]
        guild = current_app.bot.get_guild(int(snowflake)) if snowflake else None

        warning = None
        try:
            await cog.service.rename_puzzle(guild, hunt_obj, puzzle, new_name)
        except SheetRefreshError as exc:
            warning = str(exc)

        await _audit("rename", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])

    resp = {"ok": True}
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


async def require_admin(guild_snowflake):
    """Shorthand for require_organiser with ADMIN_ROLES (just forwards)."""
    return await require_organiser(guild_snowflake, roles=ADMIN_ROLES)


# --- Hunt create / delete (admin only) ------------------------------------

_CREATE_HUNT_REQUIRED = {"guild_id", "name", "url", "start_timestamp"}
_CREATE_HUNT_OPTIONAL = {"username", "password", "role_name", "create_archive_sheet",
                         "layout_mode"}


@actions_bp.route("/hunts", methods=["POST"])
@login_required
async def create_hunt():
    """Create a new hunt. Admin only. Returns the new hunt uid."""
    body = await request.get_json(silent=True) or {}

    missing = _CREATE_HUNT_REQUIRED - set(body.keys())
    if missing:
        return jsonify({"error": "missing_keys", "required": sorted(missing)}), 400
    extra = set(body.keys()) - _CREATE_HUNT_REQUIRED - _CREATE_HUNT_OPTIONAL
    if extra:
        return jsonify({"error": "bad_keys", "unknown": sorted(extra)}), 400
    if body.get("layout_mode", "channel") not in ("channel", "forum"):
        return jsonify({"error": "bad_layout_mode"}), 400

    guild_id = body["guild_id"]
    guilds_map = await data.get_guilds_map()
    guild_info = guilds_map.get(guild_id)
    if guild_info is None:
        return jsonify({"error": "guild_not_found"}), 404
    guild_snowflake = guild_info["guild_snowflake"]

    perm = await require_admin(guild_snowflake)
    if perm:
        return perm

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    guild = bot.get_guild(int(guild_snowflake)) if guild_snowflake else None
    if guild is None:
        return jsonify({"error": "guild_unavailable"}), 503

    fields = {k: v for k, v in body.items() if k in _CREATE_HUNT_OPTIONAL}
    try:
        new_hunt = await cog.service.create_hunt(
            guild,
            name=body["name"],
            url=body["url"],
            start_timestamp=body["start_timestamp"],
            guild_id=guild_id,
            **fields,
        )
    except RuntimeError as exc:
        if "hunt_exists" in str(exc):
            return jsonify({"error": "hunt_exists"}), 409
        raise
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    # Post + pin the welcome message in the new hunt channel (parity with !newhunt,
    # which does this via send_initial_hunt_channel_messages). Best-effort: a failure
    # here must not fail hunt creation, which already committed.
    channel = guild.get_channel(new_hunt.channel_id) if new_hunt.channel_id else None
    if channel is not None:
        try:
            from bot.store import GuildSettingsDb
            guild_data = GuildSettingsDb.get_cached(int(guild_snowflake))
            msg = await cog.send_initial_hunt_channel_messages(
                None, channel, hunt=new_hunt, guild_data=guild_data,
            )
            await msg.pin()
        except Exception:
            logger.exception("create_hunt: failed to send/pin welcome message")

    await _audit("create_hunt", hunt_uid=new_hunt.uid)
    return jsonify({"uid": new_hunt.uid})


# --- Hunt settings (admin only) --------------------------------------------

_SETTINGS_KEYS = {"url", "start_timestamp", "username", "password", "url_sep", "puzzle_prefix"}


@actions_bp.route("/hunts/<uid>/settings", methods=["PATCH"])
@login_required
async def patch_hunt_settings(uid):
    """Update whitelisted per-hunt settings. Admin only."""
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return err
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    extra = set(body.keys()) - _SETTINGS_KEYS
    if extra:
        return jsonify({"error": "bad_keys", "unknown": sorted(extra)}), 400

    from bot.store import HuntJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404

    cog = current_app.bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503

    cog.service.update_hunt_settings(hunt_obj, **body)
    await _audit("hunt_settings", hunt_uid=uid)
    return jsonify({"ok": True})


# --- Hunt archive actions (admin only) -------------------------------------

@actions_bp.route("/hunts/<uid>/archive", methods=["POST"])
@login_required
async def post_hunt_archive(uid):
    """Toggle hunt archived state. Admin only."""
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return err
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb

    body = await request.get_json(silent=True) or {}
    # "archived" key is optional; default to True (archive on POST without body).
    archived = body.get("archived", True)

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404

    hunt_obj.archived = bool(archived)
    HuntJsonDb.commit(hunt_obj)
    await _audit("hunt_archive", hunt_uid=uid, detail={"archived": bool(archived)})
    return jsonify({"ok": True, "archived": bool(archived)})


@actions_bp.route("/hunts/<uid>/archive-sheet", methods=["POST"])
@login_required
async def post_hunt_archive_sheet(uid):
    """Create the hunt's archive Google Sheet (one-way). Admin only."""
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return err
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404

    if hunt_obj.archive_google_sheet_id:
        return jsonify({"error": "archive_sheet_exists"}), 409

    gsheet_cog = current_app.bot.get_cog("GoogleSheets")
    if gsheet_cog is None:
        return jsonify({"error": "gsheet_unavailable"}), 503

    gsheet_cog.set_spreadsheet_id(hunt_obj.google_sheet_id)
    gsheet_cog.set_archive_spreadsheet_id(hunt_obj.archive_google_sheet_id)

    try:
        sheet_id = await gsheet_cog.create_hunt_archive_spreadsheet(hunt_obj.name)
        hunt_obj.archive_google_sheet_id = sheet_id
        HuntJsonDb.commit(hunt_obj)
    except Exception:
        logger.exception("web archive sheet creation failed for hunt %s", hunt_obj.id)
        return jsonify({"error": "sheet_creation_failed"}), 500

    await _audit("hunt_archive_sheet", hunt_uid=uid)
    return jsonify({"ok": True, "archive_google_sheet_id": sheet_id})


# --- Retag puzzles between metas (organiser only) ---------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/meta", methods=["POST"])
@login_required
async def retag_puzzle(puzzle_id):
    """Move/add/remove a puzzle to/from a metapuzzle, via the bot's own logic.

    Body: {"group_code": "ABC123", "mode": "move"|"add"|"remove"}. "move" also
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
    group_code = (body.get("group_code") or "").strip()
    mode = body.get("mode") or "move"
    if mode not in ("move", "add", "remove", "clear"):
        return jsonify({"error": "bad_mode"}), 400

    explicit_edge = body.get("edge")
    # 'target' is system-assigned and normally not user-selectable, but the dashboard
    # needs it to turn a deliberate self-feed back OFF (restore the target self-edge,
    # not 'none' — see commit 2e1c9e9). It is guarded below to the group's own target.
    if explicit_edge is not None and explicit_edge not in ("feeds", "informs", "none", "target"):
        return jsonify({"error": "bad_edge"}), 400
    only = bool(body.get("only", False))

    from bot.store import HuntJsonDb, PuzzleJsonDb, TagGroupJsonDb
    from bot.core.puzzle_service import default_edge_for_group

    # "clear" unassigns from ALL metas, so it carries no group_code to validate.
    target_group = TagGroupJsonDb.get_by_attr(group_code=group_code)
    if mode != "clear" and (target_group is None or target_group.hunt_id != ctx["hunt"]["id"]):
        return jsonify({"error": "bad_group_code"}), 400

    # Default edge maintains the target group's status quo (bare group -> none,
    # capstone -> informs, fed meta -> feeds); an explicit edge overrides it.
    edge = explicit_edge or (default_edge_for_group(target_group) if target_group else "feeds")
    # Locks only the puzzle being retagged (keyed on puzzle_id), matching the bang
    # !move_to_meta decorator — a concurrent write to the meta puzzle itself is not
    # serialized against this retag, which is consistent with current behaviour.
    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        # 'target' is only meaningful as a group's OWN target self-edge (used to clear
        # a self-feed). Reject it for any ordinary membership so it stays system-owned.
        if explicit_edge == "target" and (
            target_group is None or target_group.target_puzzle_id != puzzle.id
        ):
            return jsonify({"error": "bad_edge"}), 400
        if mode == "remove" and puzzle.id == target_group.target_puzzle_id:
            return jsonify({"error": "cannot_remove_own_meta"}), 400
        if mode == "clear" and puzzle.is_target:
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
                # move → add+only with the group's default edge (status quo)
                await cog.service.tag_edge_only(puzzle, target_group, edge, guild)
            elif mode == "add" and only:
                await cog.service.tag_edge_only(puzzle, target_group, edge, guild)
            elif mode == "add":
                await cog.service.tag_with_edge(puzzle, target_group, edge)
            elif mode == "remove":
                await cog.service.untag_from(puzzle, target_group, edge=explicit_edge)
            elif mode == "clear":
                for gid in list(puzzle.tags):
                    g = TagGroupJsonDb.get_by_attr(id=gid)
                    if g is not None:
                        await cog.service.untag_from(puzzle, g)
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
        await _audit("retag", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"], detail=group_code)
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        if hunt_obj is not None:
            asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
        resp = {"ok": True}
        if warning:
            resp["sheet_warning"] = warning
        return jsonify(resp)


# --- Convert a metapuzzle back to a feeder (organiser only, destructive) -----

@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-feeder/preview", methods=["GET"])
@login_required
async def convert_to_feeder_preview(puzzle_id):
    """Read-only preview for the convert confirm dialog: how many feeders (and
    which) demoting this metapuzzle would orphan by deleting its round."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import PuzzleJsonDb

    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    if puzzle is None:
        return jsonify({"error": "puzzle_not_found"}), 404
    if not puzzle.is_target:
        return jsonify({"error": "not_a_metapuzzle"}), 400

    cog = current_app.bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    orphans = cog.service.feeders_orphaned_by_convert(puzzle)
    return jsonify({
        "orphan_count": len(orphans),
        "orphan_feeders": [{"id": p.id, "name": p.name} for p in orphans],
    })


@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-feeder", methods=["POST"])
@login_required
async def convert_to_feeder(puzzle_id):
    """Demote a metapuzzle back to a feeder via the bot's own service logic.

    Destructive: deletes the puzzle's meta round, orphaning every feeder tagged to
    it (the frontend confirms against the preview count first). Mirrors the solve
    family's edge wrapper — organiser-gated, puzzle-locked, audited, board pushed —
    and treats a Sheets failure as a non-fatal warning (the demotion is committed)."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        if not puzzle.is_target:
            return jsonify({"error": "not_a_metapuzzle"}), 400

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = bot.get_guild(int(snowflake)) if snowflake else None

        orphaned = None
        warning = None
        try:
            orphaned = await cog.service.convert_to_puzzle(guild, hunt_obj, puzzle)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        except SheetRefreshError as exc:
            warning = str(exc)
        except HttpError:
            logger.exception("web convert_to_feeder: sheet revert failed for puzzle %s", puzzle.id)
            warning = "A sheet update failed; the change is saved. Use Rebuild to retry."

        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
        await _audit("convert_to_feeder", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        resp = {"ok": True}
        if orphaned is not None:
            resp["orphaned"] = orphaned
        if warning:
            resp["sheet_warning"] = warning
        return jsonify(resp)


# --- Convert endpoints for capstone, group, runaround ----------------------

def _convert_to_type_handler(puzzle_id, new_type, error_if_not_meta=True):
    """Shared handler for convert-to-capstone/group/runaround."""
    async def handler():
        from bot.store import HuntJsonDb, PuzzleJsonDb

        ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
        if err:
            return err
        if ctx["hunt"]["archived"]:
            return jsonify({"error": "hunt_archived"}), 403
        perm = await require_organiser(ctx["guild_snowflake"])
        if perm:
            return perm

        async with puzzle_lock(puzzle_id):
            puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
            if puzzle is None:
                return jsonify({"error": "puzzle_not_found"}), 404

            if error_if_not_meta and not puzzle.is_target:
                return jsonify({"error": "not_a_metapuzzle"}), 400
            if not error_if_not_meta and puzzle.is_target:
                return jsonify({"error": "already_a_metapuzzle"}), 400

            hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
            cog = current_app.bot.get_cog("Puzzles")
            if cog is None:
                return jsonify({"error": "bot_unavailable"}), 503
            snowflake = ctx["guild_snowflake"]
            guild = current_app.bot.get_guild(int(snowflake)) if snowflake else None

            warning = None
            try:
                tag_group, summary = await cog.service.convert_to_type(
                    guild, hunt_obj, puzzle, new_type
                )
            except RuntimeError as exc:
                return jsonify({"error": str(exc)}), 400
            except (discord.RateLimited, discord.HTTPException) as exc:
                return _discord_error_response(exc)
            except SheetRefreshError as exc:
                warning = str(exc)
            except HttpError:
                logger.exception("convert_to_%s: sheet update failed for puzzle %s", new_type.lower(), puzzle.id)
                warning = "A sheet update failed; the change is saved. Use Rebuild to retry."

            asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
            await _audit(f"convert_to_{new_type.lower()}", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
            resp = {"ok": True, "summary": summary}
            if warning:
                resp["sheet_warning"] = warning
            return jsonify(resp)

    return handler


@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-capstone", methods=["POST"])
@login_required
async def convert_to_capstone(puzzle_id):
    """Convert a metapuzzle to a capstone (informs edge).

    Changes the group type and flips all feeder edges to informs. Mirrors the
    !convert_to_capstone bang command."""
    handler = _convert_to_type_handler(puzzle_id, "Capstone", error_if_not_meta=True)
    return await handler()


@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-runaround", methods=["POST"])
@login_required
async def convert_to_runaround_puzzle(puzzle_id):
    """Convert a metapuzzle to a runaround (subtree source mode).

    Changes the group type and source mode without changing membership edges.
    Mirrors the !convert_to_runaround bang command."""
    handler = _convert_to_type_handler(puzzle_id, "Runaround", error_if_not_meta=True)
    return await handler()


@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-group", methods=["POST"])
@login_required
async def convert_to_group(puzzle_id):
    """Convert a metapuzzle to a group (none edges).

    Changes the group type and flips all feeder edges to none.
    Mirrors the !convert_to_group bang command."""
    handler = _convert_to_type_handler(puzzle_id, "Group", error_if_not_meta=True)
    return await handler()


@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-metapuzzle", methods=["POST"])
@login_required
async def convert_to_metapuzzle(puzzle_id):
    """Convert an existing target (capstone/runaround/group) back to a plain
    Metapuzzle (feeds edge, round source mode).

    Unlike convert-to-meta (which promotes a bare feeder into a new group), this
    retypes a puzzle that is already a target. Mirrors the !convert_to_metapuzzle
    bang command."""
    handler = _convert_to_type_handler(puzzle_id, "Metapuzzle", error_if_not_meta=True)
    return await handler()


@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-puzzle", methods=["POST"])
@login_required
async def convert_to_puzzle(puzzle_id):
    """Demote a metapuzzle back to a regular puzzle. Alias of convert-to-feeder.

    For backward compat the old /convert-to-feeder route is also kept.
    Mirrors the !convert_to_puzzle bang command."""
    # Reuse the same logic as convert_to_feeder by calling it through the cog service directly
    from bot.store import HuntJsonDb, PuzzleJsonDb

    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        if not puzzle.is_target:
            return jsonify({"error": "not_a_metapuzzle"}), 400

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = bot.get_guild(int(snowflake)) if snowflake else None

        try:
            await cog.service.convert_to_puzzle(guild, hunt_obj, puzzle)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        except SheetRefreshError as exc:
            return jsonify({"ok": True, "sheet_warning": str(exc)})

        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
        await _audit("convert_to_puzzle", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        return jsonify({"ok": True})


# --- Delete a puzzle (organiser only) ----------------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>", methods=["DELETE"])
@login_required
async def delete_puzzle(puzzle_id):
    """Permanently delete a puzzle: DB record, sheet tab, Discord channel, and
    meta sheet refresh. Organiser-gated, puzzle-locked, audited, board pushed.
    A sheet-refresh failure is a non-fatal warning (deletion is committed)."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx["guild_snowflake"]
        guild = bot.get_guild(int(snowflake)) if snowflake else None

        warning = None
        try:
            await cog.service.delete_puzzle(guild, hunt_obj, puzzle)
        except SheetRefreshError as exc:
            warning = str(exc)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)

        await _audit("delete_puzzle", puzzle_id=puzzle_id, hunt_uid=ctx["hunt"]["uid"])
        resp = {"ok": True}
        if warning:
            resp["sheet_warning"] = warning
        return jsonify(resp)


# --- Delete a hunt (admin only) -----------------------------------------------

@actions_bp.route("/hunts/<uid>", methods=["DELETE"])
@login_required
async def delete_hunt(uid):
    """Permanently delete a hunt: all puzzle DB records, sheet tabs + files, Discord
    channels, categories, and the hunt DB record. Admin only. No archived lockout.
    Returns failed_channels alongside ok:true so the caller can surface what remains."""
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return err
    # Deliberately no archived 403 guard — admins must be able to delete archived hunts.
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None

    try:
        failed = await cog.service.delete_hunt(guild, hunt_obj)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("delete_hunt", hunt_uid=uid,
                 detail=f"name={hunt_obj.name}")
    resp = {"ok": True}
    if failed:
        resp["failed_channels"] = failed
    return jsonify(resp)


# --- Delete a round (organiser only) -----------------------------------------

@actions_bp.route("/tag_groups/<int:tag_group_id>", methods=["DELETE"])
@login_required
async def delete_tag_group(tag_group_id):
    """Permanently delete a tag group: all its puzzle DB records, sheet tabs, Discord
    channels, the group Discord category, and the group DB record. Per-puzzle
    failures are isolated. Returns failed_channels (names whose Discord channel
    could not be deleted) alongside ok:true so the caller can surface what remains.
    Organiser-gated, audited, board pushed."""
    ctx, err = await _visible_hunt_for_tag_group(tag_group_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb, TagGroupJsonDb

    tag_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if tag_group is None:
        return jsonify({"error": "round_not_found"}), 404

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None

    try:
        failed_channels = await cog.service.delete_round(guild, hunt_obj, tag_group)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("delete_round", hunt_uid=ctx["hunt"]["uid"],
                 detail=f"tag_group_id={tag_group_id} name={tag_group.name}")
    resp = {"ok": True}
    if failed_channels:
        resp["failed_channels"] = failed_channels
    return jsonify(resp)


# --- Reconcile (organiser only) ----------------------------------------------

@actions_bp.route("/hunts/<uid>/reconcile", methods=["POST"])
@login_required
async def reconcile(uid):
    """Run the reconcile workflow for a hunt.

    Query param `mode`:
      report       — dry-run only; returns actions+orphans, nothing is applied.
      apply        — apply non-destructive fixes (channel create/rename, tab create).
      apply-orphans — also delete orphan Discord channels/tabs (requires explicit
                      `confirmed_orphans` body field listing all orphan names).

    Returns {ok, actions, orphan_channels, orphan_tabs, deleted, failures}.
    """
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    mode = request.args.get("mode", "report")
    if mode not in ("report", "apply", "apply-orphans"):
        return jsonify({"error": "invalid_mode", "valid": ["report", "apply", "apply-orphans"]}), 400

    body = await request.get_json(silent=True) or {}

    from bot.store import HuntJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None
    if guild is None:
        return jsonify({"error": "guild_unavailable"}), 503

    apply = mode in ("apply", "apply-orphans")
    report = await cog.service.reconcile(guild, hunt_obj, apply=apply)

    deleted = 0
    if mode == "apply-orphans":
        confirmed = set(body.get("confirmed_orphans") or [])
        report_orphan_names = {name for _, name in report.orphan_channels} | \
                              {title for _, title in report.orphan_tabs}
        if not confirmed >= report_orphan_names:
            return jsonify({
                "error": "orphan_confirmation_mismatch",
                "expected": sorted(report_orphan_names),
            }), 400
        deleted_count, _ = await cog.service.delete_orphans(guild, hunt_obj, report)
        deleted = deleted_count

    await _audit("reconcile", hunt_uid=uid, detail=f"mode={mode}")
    return jsonify({
        "ok": True,
        "actions": [{"kind": a.kind, "target": a.target, "detail": a.detail}
                    for a in report.actions],
        "orphan_channels": [{"id": cid, "name": name}
                            for cid, name in report.orphan_channels],
        "orphan_tabs": [{"id": sid, "title": title}
                        for sid, title in report.orphan_tabs],
        "failures": [{"target": t, "error": e} for t, e in report.failures],
        "deleted": deleted,
    })


# --- Rebuild a meta roll-up sheet (organiser only) --------------------------

@actions_bp.route("/rollups/<int:tag_group_id>/rebuild", methods=["POST"])
@login_required
async def rebuild_rollup(tag_group_id):
    """Rebuild one meta group's roll-up sheet, clearing its stale badge on success.
    Keyed on tag_group_id (a rollup is per group); organiser-gated via the group's hunt.
    Serialization is handled inside refresh_target's per-group rollup lock."""
    ctx, err = await _visible_hunt_for_tag_group(tag_group_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb, TagGroupJsonDb

    target_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if target_group is None:
        return jsonify({"error": "round_not_found"}), 404

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    try:
        await cog.service.refresh_target(hunt_obj, target_group)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    except Exception:
        # refresh_target has already re-badged last_ok=0; report the failure so the
        # SPA can toast and leave the badge in place for another retry.
        logger.exception("rebuild: refresh failed for group %s", tag_group_id)
        return jsonify({"ok": False, "error": "internal_error"})
    await _audit("rebuild_rollup", hunt_uid=ctx["hunt"]["uid"], detail=str(tag_group_id))
    _notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))
    return jsonify({"ok": True})


# --- Nesting setter (organiser only) ----------------------------------------

@actions_bp.route("/tag_groups/<int:tag_group_id>/nest", methods=["POST"])
@login_required
async def set_nesting(tag_group_id):
    """Nest a tag group under another, or clear nesting.
    Body: {"parent_tag_group_id": <id>|null}. Null clears the nesting."""
    ctx, err = await _visible_hunt_for_tag_group(tag_group_id)
    if err:
        return err
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    parent_id = body.get("parent_tag_group_id")  # None/null clears — int nests

    from bot.store import TagGroupJsonDb

    child = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if child is None:
        return jsonify({"error": "round_not_found"}), 404

    # Validate parent exists (and belongs to same hunt) if nesting, not clearing
    if parent_id is not None:
        parent = TagGroupJsonDb.get_by_attr(id=parent_id)
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
    group_code = (body.get("group_code") or "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import HuntJsonDb, PuzzleJsonDb, TagGroupJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    target_group = None
    if group_code:
        target_group = TagGroupJsonDb.get_by_attr(group_code=group_code)
        if target_group is None or target_group.hunt_id != hunt_obj.id:
            return jsonify({"error": "bad_group_code"}), 400
    if PuzzleJsonDb.check_duplicates_in_hunt(name, hunt_obj.id):
        return jsonify({"error": "duplicate_name"}), 409

    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    guild = bot.get_guild(int(ctx["guild_snowflake"]))
    try:
        from bot.core.notifier import DiscordNotifier
        puzzle = await cog.service.create_puzzle(
            guild, hunt_obj, target_group, name,
            notifier=DiscordNotifier(current_app.bot),
        )
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
    """Create a new metapuzzle/group (category + meta channel), via the bot (like !mp)."""
    ctx, err = await _organiser_hunt(uid)
    if err:
        return err
    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    group_type = (body.get("type") or "Metapuzzle").strip()
    from bot.core.puzzle_service import TYPE_DEFAULTS, GROUP_TYPE_INPUT_ALIASES
    if group_type not in TYPE_DEFAULTS and group_type not in GROUP_TYPE_INPUT_ALIASES:
        return jsonify({"error": "bad_type"}), 400

    from bot.store import HuntJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    bot = current_app.bot
    cog = bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    guild = bot.get_guild(int(ctx["guild_snowflake"]))
    try:
        from bot.core.notifier import DiscordNotifier
        # Mirror the bang path's meta naming: a Round's target puzzle is named
        # "<name> - meta" (other types keep the group name). See _round_impl.
        if group_type == "Group":
            meta_puzzle_name = False
        elif group_type == "Round":
            meta_puzzle_name = name + " - meta"
        else:
            meta_puzzle_name = name
        stored_type = GROUP_TYPE_INPUT_ALIASES.get(group_type, group_type)
        new_round, puzzle = await cog.service.create_tag_group(
            guild, hunt_obj, name,
            notifier=DiscordNotifier(bot),
            meta_puzzle_name=meta_puzzle_name,
            round_type=stored_type,
        )
        # Runaround groups aggregate over their subtree, not just direct feeders;
        # set source_mode like the bang path does after creation.
        if stored_type == "Runaround":
            await cog.service.set_source_mode(hunt_obj, new_round, "subtree")
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    await _audit("create_meta", puzzle_id=getattr(puzzle, "id", None), hunt_uid=uid, detail=new_round.group_code)
    return jsonify({"ok": True, "tag_group_id": new_round.id, "group_code": new_round.group_code})


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
        await cog.web_post_unsolved(guild, hunt_obj, puzzle)
        await _audit("unsolve", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        await _refresh_parent_minimeta_pin(cog, guild, hunt_obj, puzzle)

        return jsonify({"ok": True})


@actions_bp.route("/evidence/<int:item_id>", methods=["PATCH"])
@login_required
async def update_evidence(item_id):
    from bot.store import EvidenceJsonDb, HuntJsonDb
    body = await request.get_json()
    target_puzzle_id = body.get("target_puzzle_id")
    old = EvidenceJsonDb.get_by_id(item_id)
    old_target = old.target_puzzle_id if old else None
    item = EvidenceJsonDb.set_target(item_id, target_puzzle_id)
    if item is None:
        return jsonify({"error": "not found"}), 404
    # Re-render the Evidence sheet so the new Target column value shows (mirrors the
    # bang refresh). Best-effort: the routing is already persisted.
    cog = current_app.bot.get_cog("Puzzles")
    hunt_obj = HuntJsonDb.get_by_attr(id=item.hunt_id)
    if cog is not None and hunt_obj is not None:
        if item.target_puzzle_id:
            asyncio.create_task(cog.service.link_informs_for_evidence(
                hunt_obj, item.source_puzzle_id, item.target_puzzle_id))
        if old_target and old_target != item.target_puzzle_id:
            asyncio.create_task(cog.service.unlink_informs_for_evidence(
                hunt_obj, item.source_puzzle_id, old_target))
        asyncio.create_task(cog.service.refresh_evidence(hunt_obj))
        asyncio.create_task(cog.service.refresh_target_for_puzzle(hunt_obj, item.target_puzzle_id))
    return jsonify({"id": item.id, "target_puzzle_id": item.target_puzzle_id})
