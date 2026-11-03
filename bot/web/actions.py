"""Dashboard write-actions (Phase 2).

Writes go through the bot's own store objects on the event-loop thread (NOT the
read pool's worker threads), so they share the bot's connection and stay
serialized with the bot's own command DB calls. Channel/sheet side-effects reuse
the bot's cog logic so a web action behaves exactly like the matching ! command.
"""
import asyncio
import datetime
import functools
import logging
import re
from typing import Any

import discord
import pytz
from googleapiclient.errors import HttpError
from quart import Blueprint, jsonify, request

from .typed_app import bot_app, puzzles_cog

from . import data, events
from bot.web import data as web_data
from .auth import (
    ADMIN_ROLES,
    ORGANISER_ROLE_NAMES,
    ORGANISER_ROLES,
    current_user,
    hunt_visible,
    login_required,
    organiser_tier_bypassed,
    guild_for,
    resolve_membership,
    resolve_member_role_ids,
)
from bot.core import multi_team, role_gate, template_link
from bot.core.sheet_location import (additional_sheet_location, hunt_sheet_location,
                                     spreadsheet_link)
from bot.store import GuildSettingsDb, HuntJsonDb, HuntInviteDb, HuntAccessLogDb, MultiTeamDb
from bot.web import team_access
from bot.core.locks import (puzzle_lock, puzzles_lock, convert_lock_ids,
                            hunt_create_lock, hunt_sheet_lock)
from bot.core.errors import SheetRefreshError
from bot.core.media_ingest import ingest_image_bytes
from bot.core.media_tiling import run_bounded_image_work, schedule_tiling

logger = logging.getLogger(__name__)

actions_bp = Blueprint("actions", __name__, url_prefix="/api")


_ERROR_SLUG = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")


def _error_slug(exc):
    """The client-safe rendering of a RuntimeError raised by the service layer.

    bot/core signals every user-facing refusal by raising RuntimeError with a
    machine-readable slug ("already_meta", "no_category_space"); that vocabulary
    is the API contract and the SPA keys on it, so it must pass through intact.
    RuntimeError is a builtin, though, so an *unintended* one from a library
    deep in the call stack reaches the same handler -- and echoing its text
    hands the client internal detail (SQL, paths, driver messages). Anything
    that is not slug-shaped is logged here and reported generically.
    """
    text = str(exc)
    if _ERROR_SLUG.match(text):
        return text
    logger.warning("unexpected RuntimeError surfaced from a web route: %s",
                   text, exc_info=exc)
    return "internal_error"


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
            "INSERT INTO audit_log (user_id, username, action, puzzle_id, hunt_uid, detail) "
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


async def _notify_board_soon(uid, guild_snowflake):
    """Async adapter around `_notify_board` for use as `schedule_tiling`'s
    `after` callback, which awaits whatever it's given. `_notify_board` itself
    stays synchronous so the ordinary (non-tiling) call sites need no extra
    event-loop hop."""
    _notify_board(uid, guild_snowflake)


async def _notify_board_for_hunt_id(hunt_id):
    """`_notify_board`, resolved from a bare hunt id.

    `_crop_region_media` only ever has `media.hunt_id` in scope (it has no
    caller ctx to draw uid/guild_snowflake from -- the old `_push_board`
    resolved the same way, via `HuntJsonDb.get_by_attr(id=hunt_id)`), so this
    keeps that one `schedule_tiling(after=...)` site working the same way.
    """
    from bot.store import HuntJsonDb
    hunt_row = HuntJsonDb.get_by_attr(id=hunt_id)
    if hunt_row is None:
        return
    guilds_map = await data.get_guilds_map()
    guild_snowflake = guilds_map.get(hunt_row.guild_id, {}).get("guild_snowflake")
    _notify_board(hunt_row.uid, guild_snowflake)


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
    assert user is not None  # every caller is behind @login_required
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


async def _visible_hunt_for_evidence(item_id):
    return await _resolve_visible_hunt(data.get_hunt_for_evidence(item_id), "not_found")


async def _visible_hunt_for_canvas(canvas_id):
    return await _resolve_visible_hunt(data.get_hunt_for_canvas(canvas_id), "canvas_not_found")


async def _route_ctx(resolver, allow_archived=False, tier=None, need_cog=True):
    """(ctx, hunt_obj, cog, guild, err) for the full route prologue: resolve the
    hunt via `resolver` (an already-called `_visible_hunt_for_*` coroutine),
    enforce the archived lock unless `allow_archived`, gate on `tier` (falsy =
    open, truthy = organiser-gated), then -- unless need_cog is False -- load
    the hunt's HuntJsonDb row, the puzzles cog, and its live guild.

    allow_archived=True is the only way to skip the hunt_archived 403 check --
    it exists so a route that must keep working on an archived hunt says so
    at the call site instead of by omission.

    tier is checked for bare truthiness, not compared against a specific
    string: tests/test_web_route_gating.py's AST walker only resolves a gate
    through an `if <param>:` / `if not <param>:` test on one of THIS
    function's own positional-or-keyword parameters (the same trick
    _canvas_ctx's `require_manage` already relies on) -- an `==` comparison,
    or a keyword-only parameter (a bare `*` in the signature), is invisible
    to it and would make every caller resolve identically regardless of what
    was actually passed. Stage 1 only ever calls this with tier="organiser"
    or tier=None, so a bare truthy check is enough; a later stage that folds
    in admin-gated routes will need to extend this dispatch and the walker
    together, not just this function.
    """
    ctx, err = await resolver
    if err:
        return None, None, None, None, err
    assert ctx is not None
    if ctx["hunt"]["archived"] and not allow_archived:
        return None, None, None, None, (jsonify({"error": "hunt_archived"}), 403)
    if tier:
        perm = await require_organiser(ctx["guild_snowflake"])
        if perm is not None:
            return None, None, None, None, perm
    if not need_cog:
        return ctx, None, None, None, None
    from bot.store import HuntJsonDb
    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return None, None, None, None, (jsonify({"error": "hunt_not_found"}), 404)
    cog = puzzles_cog()
    if cog is None:
        return None, None, None, None, (jsonify({"error": "bot_unavailable"}), 503)
    snowflake = ctx["guild_snowflake"]
    guild = bot_app().bot.get_guild(int(snowflake)) if snowflake else None
    return ctx, hunt_obj, cog, guild, None


# --- Notes (any member of the hunt's guild) ---------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/notes", methods=["POST"])
@login_required
async def add_note(puzzle_id):
    ctx, hunt_obj, cog, _guild, err = await _route_ctx(_visible_hunt_for_puzzle(puzzle_id))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

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
        attribution = f"{_display_name(current_user())} (web)"
        await cog.service.add_note(hunt_obj, puzzle, text, attribution)
        await _audit("note", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        # board push is emitted by the service (notifier.board_changed)
        return jsonify({"notes": puzzle.notes})


@actions_bp.route("/puzzles/<int:puzzle_id>/notes/<int:index>", methods=["DELETE"])
@login_required
async def erase_note(puzzle_id, index):
    """Erase a note by 1-based index (matching the bot's !erase_note)."""
    ctx, hunt_obj, cog, _guild, err = await _route_ctx(_visible_hunt_for_puzzle(puzzle_id))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
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
    ctx, hunt_obj, cog, guild, err = await _route_ctx(_visible_hunt_for_puzzle(puzzle_id))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    body = await request.get_json(silent=True) or {}
    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

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
            await cog.web_refresh_info(guild, hunt_obj, puzzle)
    return jsonify({"ok": True})


# --- Additional sheets (any member of the hunt's guild, like !add_sheet) -----

@actions_bp.route("/puzzles/<int:puzzle_id>/sheets", methods=["POST"])
@login_required
async def add_sheet(puzzle_id):
    """Create an additional working sheet (name optional), via the bot."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(_visible_hunt_for_puzzle(puzzle_id))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        try:
            sheet = await cog.service.add_sheet(hunt_obj, puzzle, name)
            await _audit("add_sheet", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
            await cog.web_refresh_info(guild, hunt_obj, puzzle)
        except RuntimeError as exc:
            return jsonify({"error": _error_slug(exc)}), 400
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        return jsonify({
            "ok": True,
            "sheet": {"id": sheet.id, "name": sheet.puzzle_name, "page_id": sheet.google_page_id,
                      "url": additional_sheet_location(hunt_obj, sheet).url},
        })


@actions_bp.route("/puzzles/<int:puzzle_id>/sheets/<int:sheet_id>", methods=["DELETE"])
@login_required
async def remove_sheet(puzzle_id, sheet_id):
    """Remove an additional sheet (deletes the Google tab and the DB row)."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        try:
            await cog.service.remove_sheet(hunt_obj, puzzle, sheet_id)
            await _audit("remove_sheet", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
            await cog.web_refresh_info(guild, hunt_obj, puzzle)
        except RuntimeError as exc:
            return jsonify({"error": _error_slug(exc)}), 404
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        return jsonify({"ok": True})


@actions_bp.route("/hunts/<uid>/sheets", methods=["POST"])
@login_required
async def add_hunt_sheet(uid):
    """Create a working sheet belonging to the HUNT rather than to a puzzle.

    Same permission bar as adding one to a puzzle (any authenticated user who can
    see the hunt, while it is unarchived) -- this is ordinary hunt work, not
    administration. The web equivalent of `!add_sheet` in a hunt channel.
    """
    ctx, hunt_obj, cog, _guild, err = await _route_ctx(_visible_hunt_by_uid(uid))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()

    # The hunt-keyed sheet lock, not puzzle_lock: this write has no puzzle, and
    # it must serialise against a concurrent !add_sheet in the hunt channel,
    # which holds this same lock.
    async with hunt_sheet_lock(ctx["hunt"]["id"]):
        try:
            sheet = await cog.service.add_hunt_sheet(hunt_obj, name)
            await _audit("add_hunt_sheet", hunt_uid=ctx["hunt"]["uid"])
        except RuntimeError as exc:
            return jsonify({"error": _error_slug(exc)}), 400
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        return jsonify({
            "ok": True,
            "sheet": {"id": sheet.id, "name": sheet.puzzle_name,
                      "page_id": sheet.google_page_id,
                      "url": hunt_sheet_location(hunt_obj, sheet.google_page_id).url},
        })


@actions_bp.route("/hunts/<uid>/sheets/<int:sheet_id>", methods=["DELETE"])
@login_required
async def remove_hunt_sheet(uid, sheet_id):
    """Remove one of the hunt's own working sheets (Google tab and DB row)."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    async with hunt_sheet_lock(ctx["hunt"]["id"]):
        try:
            # remove_hunt_sheet looks the id up among THIS hunt's sheets and
            # raises sheet_not_found otherwise, so a sheet id from another hunt
            # cannot be deleted through this route.
            await cog.service.remove_hunt_sheet(hunt_obj, sheet_id)
            await _audit("remove_hunt_sheet", hunt_uid=ctx["hunt"]["uid"])
        except RuntimeError as exc:
            return jsonify({"error": _error_slug(exc)}), 404
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
    ctx, hunt_obj, cog, guild, err = await _route_ctx(_visible_hunt_for_puzzle(puzzle_id))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    body = await request.get_json(silent=True) or {}
    solution = (body.get("solution") or "").strip()
    mode = (body.get("mode") or "full").strip().lower()
    if mode not in ("full", "partial", "change", "add"):
        return jsonify({"error": "bad_mode"}), 400

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        # State invariants (mirrors !solve/!partial/!change_solution/!add_solution):
        # full/partial refuse an already-solved puzzle; change/add on a puzzle
        # that isn't solved yet fall back to the solve/partial path instead of
        # stamping solve_time or a "Partially Solved" status without solving.
        if mode in ("full", "partial") and puzzle.solved:
            return jsonify({"error": "already_solved"}), 400
        if mode in ("change", "add") and not puzzle.solved:
            mode = "full" if mode == "change" else "partial"

        # Empty-solution guards (mirrors cog's guards per mode).
        if mode == "full" and not solution and not puzzle.solution:
            return jsonify({"error": "empty_solution"}), 400
        if mode in ("change", "add") and not solution:
            return jsonify({"error": "empty_solution"}), 400

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

        # Post the mode-appropriate embed + refresh the pinned info (mirrors the
        # bang equivalents); only full solves also refresh a parent minimeta pin,
        # since only solve()/unsolve() touch minimeta rollup state.
        if mode == "full":
            await cog.web_post_solved_embed(guild, hunt_obj, puzzle)
            await _refresh_parent_minimeta_pin(cog, guild, hunt_obj, puzzle)
        else:
            await cog.web_post_solution_update_embed(guild, hunt_obj, puzzle, mode)

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
    ctx, hunt_obj, cog, guild, err = await _route_ctx(_visible_hunt_for_puzzle(puzzle_id))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    body = await request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        await cog.service.set_puzzle_url(guild, hunt_obj, puzzle, url)
        await _audit("link", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        await cog.web_refresh_info(guild, hunt_obj, puzzle)
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))

    return jsonify({"ok": True})


# --- Make minimeta (organiser+) ------------------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/make-minimeta", methods=["POST"])
@login_required
async def make_minimeta_puzzle(puzzle_id):
    """Mark a puzzle as a minimeta cluster parent (mirrors !make_minimeta)."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(_visible_hunt_for_puzzle(puzzle_id))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    # Ungated: building a minimeta cluster is a Create-area operation, open on
    # the bang edge (!make_minimeta). Removing one stays organiser.
    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        await cog.service.make_minimeta(puzzle)
        await _audit("make_minimeta", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
        await cog.web_refresh_info(guild, hunt_obj, puzzle)

    return jsonify({"ok": True})


# --- Remove minimeta (organiser+) -----------------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/remove-minimeta", methods=["POST"])
@login_required
async def remove_minimeta_puzzle(puzzle_id):
    """Revert a minimeta to an ordinary puzzle — delete its minis + rollup table
    (mirrors !remove_minimeta). Organiser-gated, puzzle-locked, audited, board pushed."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        try:
            await cog.service.remove_minimeta(guild, hunt_obj, puzzle)
        # SheetRefreshError subclasses RuntimeError, so it must be caught first —
        # otherwise a stale-sheet warning would be misreported as not_minimeta.
        except SheetRefreshError as exc:
            await _audit("remove_minimeta", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
            await cog.web_refresh_info(guild, hunt_obj, puzzle)
            return jsonify({"ok": True, "sheet_warning": str(exc)})
        except RuntimeError:
            return jsonify({"error": "not_minimeta"}), 400
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)

        await _audit("remove_minimeta", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        await cog.web_refresh_info(guild, hunt_obj, puzzle)
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))

    return jsonify({"ok": True})


# --- Convert puzzle to meta (organiser+) ----------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-meta", methods=["POST"])
@login_required
async def convert_to_meta(puzzle_id):
    """Convert an existing feeder puzzle into a metapuzzle, via the bot (mirrors !ctm)."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import PuzzleJsonDb

    # Same single sorted acquisition as the other convert routes (H5). This route
    # promotes a bare puzzle, so the set is normally just the puzzle itself — but it
    # is resolved the same way so the promote path can never become the odd one out.
    async with puzzles_lock(convert_lock_ids(puzzle_id=puzzle_id)):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        if puzzle.is_target:
            return jsonify({"error": "already_meta"}), 400

        warning = None
        try:
            tag_group, _summary = await cog.service.convert_to_type(
                guild, hunt_obj, puzzle, "Metapuzzle"
            )
        # SheetRefreshError subclasses RuntimeError, so it must be caught first
        # or the warning path is dead and sheet failures 500.
        except (HttpError, SheetRefreshError):
            logger.exception("convert_to_meta: sheet update failed for puzzle %s", puzzle_id)
            warning = "A sheet update failed; the change is saved. Use Rebuild to retry."
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        except RuntimeError as exc:
            if str(exc) == "already_meta":
                return jsonify({"error": "already_meta"}), 400
            raise

        await _audit("convert_to_meta", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))

    resp: dict[str, Any] = {"ok": True}
    if warning:
        resp["sheet_warning"] = warning
    return jsonify(resp)


# --- Rename a puzzle (organiser+) -----------------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/rename", methods=["POST"])
@login_required
async def rename_puzzle(puzzle_id):
    """Rename a puzzle, via the bot (mirrors !rename). Organiser+ only."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    body = await request.get_json(silent=True) or {}
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import PuzzleJsonDb

    # Duplicate check — match bang behaviour: reject on clash, but exclude the
    # puzzle being renamed so it never clashes with itself.
    if PuzzleJsonDb.check_duplicates_in_hunt(new_name, ctx["hunt"]["id"], exclude_puzzle_id=puzzle_id):
        return jsonify({"error": "duplicate_name"}), 400

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        warning = None
        try:
            await cog.service.rename_puzzle(guild, hunt_obj, puzzle, new_name)
        except SheetRefreshError as exc:
            warning = str(exc)

        await _audit("rename", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])

    resp: dict[str, Any] = {"ok": True}
    if warning:
        resp["sheet_warning"] = warning
    return jsonify(resp)


# --- Shared helper for organiser-gated actions (used by retag/create next) ---

async def require_organiser(guild_snowflake, roles=ORGANISER_ROLES):
    """Return None if the user holds an allowed role in the guild (or the
    Organiser tier is bypassed for this guild), else an error tuple."""
    user = current_user()
    assert user is not None  # every calling route is behind @login_required
    role_ids = await resolve_member_role_ids(user["id"], guild_snowflake)
    if role_ids is None:
        return jsonify({"error": "forbidden"}), 403
    if organiser_tier_bypassed(guild_snowflake, roles):
        return None
    guild = guild_for(guild_snowflake)
    if guild is None:
        return jsonify({"error": "forbidden"}), 403
    if not role_gate.member_holds_tier(role_ids, guild, roles):
        return jsonify({"error": "insufficient_role"}), 403
    return None


async def require_admin(guild_snowflake):
    """Shorthand for require_organiser with ADMIN_ROLES (just forwards)."""
    return await require_organiser(guild_snowflake, roles=ADMIN_ROLES)


# --- Hunt create / delete (admin only) ------------------------------------

_CREATE_HUNT_REQUIRED = {"guild_id", "name", "url", "start_timestamp"}
_CREATE_HUNT_OPTIONAL = {"username", "password", "role_name", "create_solved_sheet",
                         "layout_mode", "confirm_existing_role", "sheet_mode"}


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
    if body.get("sheet_mode", "tabs") not in ("tabs", "files"):
        return jsonify({"error": "bad_sheet_mode"}), 400
    start_timestamp = body["start_timestamp"]
    if isinstance(start_timestamp, bool) or not isinstance(start_timestamp, (int, float)):
        return jsonify({"error": "bad_start_timestamp"}), 400
    try:
        datetime.datetime.fromtimestamp(start_timestamp, tz=pytz.UTC)
    except (OverflowError, OSError, ValueError):
        return jsonify({"error": "bad_start_timestamp"}), 400

    guild_id = body["guild_id"]
    guilds_map = await data.get_guilds_map()
    guild_info = guilds_map.get(guild_id)
    if guild_info is None:
        return jsonify({"error": "guild_not_found"}), 404
    guild_snowflake = guild_info["guild_snowflake"]

    perm = await require_admin(guild_snowflake)
    if perm:
        return perm

    bot = bot_app().bot
    cog = puzzles_cog()
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    guild = bot.get_guild(int(guild_snowflake)) if guild_snowflake else None
    if guild is None:
        return jsonify({"error": "guild_unavailable"}), 503

    user = current_user()
    assert user is not None  # @login_required
    creator = guild.get_member(int(user["id"]))
    if creator is None:
        try:
            creator = await guild.fetch_member(int(user["id"]))
        except discord.HTTPException:
            creator = None

    fields = {k: v for k, v in body.items() if k in _CREATE_HUNT_OPTIONAL}
    try:
        new_hunt = await cog.service.create_hunt(
            guild,
            name=body["name"],
            url=body["url"],
            start_timestamp=body["start_timestamp"],
            guild_id=guild_id,
            creator=creator,
            **fields,
        )
    except RuntimeError as exc:
        if "hunt_exists" in str(exc):
            return jsonify({"error": "hunt_exists"}), 409
        if str(exc) == "files_mode_no_solved_sheet":
            return jsonify({"error": "files_mode_no_solved_sheet"}), 409
        if "existing_role_confirm" in str(exc):
            role_name = body.get("role_name")
            return jsonify({
                "needs_confirm": "existing_role",
                "message": (f"Role '{role_name}' already exists. Normally each team "
                            f"gets its own role per hunt — reuse it and add you to it?"),
            }), 200
        raise
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    # Post + pin the welcome message in the new hunt channel (parity with !newhunt).
    # Routed through DiscordNotifier.welcome — the same seam every other
    # welcome/pin flow uses — so a future change to welcome/pin semantics reaches
    # both bang and web hunt creation together (#81). Best-effort: welcome() never
    # raises, so a Discord hiccup can't fail hunt creation, which already committed.
    channel = guild.get_channel(new_hunt.channel_id) if new_hunt.channel_id else None
    if channel is not None:
        from bot.core.notifier import DiscordNotifier
        await DiscordNotifier(bot).welcome(guild, new_hunt, channel=channel)

    await _audit("create_hunt", hunt_uid=new_hunt.uid)
    resp = {"uid": new_hunt.uid}
    if body.get("role_name") and not new_hunt.role_id:
        resp["warning"] = "role_not_applied"
    return jsonify(resp)


# --- Hunt settings (admin only) --------------------------------------------

_SETTINGS_KEYS = {"url", "start_timestamp", "username", "password", "url_sep", "puzzle_prefix"}


@actions_bp.route("/hunts/<uid>/settings", methods=["PATCH"])
@login_required
async def patch_hunt_settings(uid):
    """Update whitelisted per-hunt settings. Admin only."""
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), need_cog=False)
    if err:
        return err
    assert ctx is not None
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

    cog = puzzles_cog()
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503

    await cog.service.update_hunt_settings(hunt_obj, **body)
    guild = bot_app().bot.get_guild(int(ctx["guild_snowflake"])) if ctx["guild_snowflake"] else None
    if guild is not None:
        await cog.service.notifier.refresh_info(guild, hunt_obj)
    await _audit("hunt_settings", hunt_uid=uid)
    return jsonify({"ok": True})


@actions_bp.route("/hunts/<uid>/role", methods=["POST"])
@login_required
async def set_hunt_role(uid):
    """Gate an open hunt behind a team role. Admin only. Returns
    {"ok": True, "failed_categories": [...]}, or a needs_confirm prompt when the
    role already exists."""
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), need_cog=False)
    if err:
        return err
    assert ctx is not None
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    role_name = (body.get("role_name") or "").strip()
    if not role_name:
        return jsonify({"error": "missing_role_name"}), 400
    confirm_existing = bool(body.get("confirm_existing_role"))

    from bot.store import HuntJsonDb
    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404
    cog = puzzles_cog()
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    guild = bot_app().bot.get_guild(int(ctx["guild_snowflake"])) if ctx["guild_snowflake"] else None
    if guild is None:
        return jsonify({"error": "guild_unavailable"}), 503

    user = current_user()
    assert user is not None  # @login_required
    requester = guild.get_member(int(user["id"]))
    if requester is None:
        try:
            requester = await guild.fetch_member(int(user["id"]))
        except discord.HTTPException:
            requester = None

    try:
        failed = await cog.service.set_hunt_role(
            guild, hunt_obj, role_name=role_name,
            requester=requester, confirm_existing_role=confirm_existing,
        )
    except RuntimeError as exc:
        s = str(exc)
        if "already_gated" in s:
            return jsonify({"error": "already_gated"}), 409
        if "existing_role_confirm" in s:
            return jsonify({
                "needs_confirm": "existing_role",
                "message": (f"Role '{role_name}' already exists. Normally each team "
                            f"gets its own role per hunt — reuse it and add you to it?"),
            }), 200
        if "role_not_applied" in s:
            return jsonify({"error": "role_not_applied"}), 422
        raise
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("set_hunt_role", hunt_uid=uid, detail=role_name)
    return jsonify({"ok": True, "failed_categories": failed})


# --- Hunt archive actions (admin only) -------------------------------------

@actions_bp.route("/hunts/<uid>/archive", methods=["POST"])
@login_required
async def post_hunt_archive(uid):
    """Toggle hunt archived state. Admin only."""
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), allow_archived=True, need_cog=False)
    if err:
        return err
    assert ctx is not None
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

    if bool(archived) and not hunt_obj.archived and getattr(hunt_obj, "roster_role_id", 0):
        # A team hunt's server is meant to be emptied into the hub, not frozen in place.
        return jsonify({"error": "use_hub_archive"}), 409

    hunt_obj.archived = bool(archived)
    HuntJsonDb.commit(hunt_obj)
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("hunt_archive", hunt_uid=uid, detail=f"archived={bool(archived)}")
    return jsonify({"ok": True, "archived": bool(archived)})


@actions_bp.route("/hunts/<uid>/complete", methods=["POST"])
@login_required
async def post_hunt_complete(uid):
    """Mark this team's hunt finished (or not), as !hunt_complete does. Admin only."""
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), allow_archived=True, need_cog=False)
    if err:
        return err
    assert ctx is not None
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb

    body = await request.get_json(silent=True)
    if not isinstance((body or {}).get("complete"), bool):
        return jsonify({"error": "bad_value", "field": "complete"}), 400
    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404
    resp, done = await _complete_response(hunt_obj, body)
    if done is not None:
        _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
        await _audit("hunt_complete", hunt_uid=uid,
                     detail=f"complete={done[0]} changed={done[1]}")
    return resp


# Strong references to in-flight archive tasks, so they aren't garbage-collected.
_ARCHIVE_TASKS: set = set()


@actions_bp.route("/hunts/<uid>/archive-to-hub", methods=["POST"])
@login_required
async def post_hunt_archive_to_hub(uid):
    """Archive a team hunt onto its hub, as !archive_hunt_to_hub does. Admin
    only; the user must also be an admin on the hub. Runs in the background."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(_visible_hunt_by_uid(uid))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.core import hub_channel, hunt_archive, role_panel
    from bot.core.hub_archive_run import RUNNING_HUB_ARCHIVES, run_archive_to_hub

    if getattr(hunt_obj, "layout_mode", "") == "forum":
        return jsonify({"error": "forum_not_supported"}), 400
    if not getattr(hunt_obj, "roster_role_id", 0):
        return jsonify({"error": "not_a_team_hunt"}), 400
    roster = role_panel.find_roster_role(bot_app().bot, hunt_obj.roster_role_id)
    if roster is None:
        return jsonify({"error": "hub_role_missing"}), 409

    body = await request.get_json(silent=True) or {}
    if body.get("confirm_name") != hunt_obj.name:
        return jsonify({"error": "confirm_mismatch"}), 400
    if hunt_obj.id in RUNNING_HUB_ARCHIVES:
        return jsonify({"error": "archive_running"}), 409
    if guild is None:
        return jsonify({"error": "bot_unavailable"}), 503

    user_id = int((current_user() or {}).get("id") or 0)
    invoker = guild.get_member(user_id)
    if invoker is None:
        try:
            invoker = await guild.fetch_member(user_id)
        except discord.NotFound:
            invoker = None
        except discord.HTTPException:
            return jsonify({"error": "discord_unavailable"}), 503
    if invoker is None:
        return jsonify({"error": "not_a_member"}), 403
    try:
        hub, hub_row = await hunt_archive.resolve_hub(bot_app().bot, roster.guild.id, guild, invoker)
    except hunt_archive.HubUnavailable as e:
        return jsonify({"error": "hub_unavailable", "message": str(e)}), 403

    admin_channel, _reason = hub_channel.find(hub)
    # Re-check with no await before the add: the awaits above let a second POST in.
    if hunt_obj.id in RUNNING_HUB_ARCHIVES:
        return jsonify({"error": "archive_running"}), 409
    RUNNING_HUB_ARCHIVES.add(hunt_obj.id)

    async def _run():
        try:
            await run_archive_to_hub(bot_app().bot, cog.service, guild, hunt_obj, invoker,
                                     hub, hub_row, status_channel=admin_channel)
        finally:
            RUNNING_HUB_ARCHIVES.discard(hunt_obj.id)

    task = asyncio.create_task(_run())
    _ARCHIVE_TASKS.add(task)
    task.add_done_callback(_ARCHIVE_TASKS.discard)
    await _audit("hunt_archive_to_hub", hunt_uid=uid, detail=f"hub={hub.id}")
    return jsonify({"started": True,
                    "progress_channel": admin_channel.name if admin_channel is not None else None}), 202


@actions_bp.route("/hunts/<uid>/solved-sheet", methods=["POST"])
@login_required
async def post_hunt_solved_sheet(uid):
    """Create the hunt's solved puzzles Google Sheet (one-way). Admin only."""
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), need_cog=False)
    if err:
        return err
    assert ctx is not None
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404

    cog = puzzles_cog()
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503

    # One seam with !use_solved_sheet (C7); the lock is held here, at the edge,
    # so the check and the create are one critical section. Unlocked, two
    # invocations both passed the check and created two Drive spreadsheets.
    try:
        async with hunt_sheet_lock(hunt_obj.id):
            sheet_id = await cog.service.create_hunt_solved_sheet(hunt_obj)
    except RuntimeError as exc:
        slug = str(exc)
        if slug in ("solved_sheet_exists", "files_mode_no_solved_sheet"):
            return jsonify({"error": slug}), 409
        if slug == "gsheet_unavailable":
            return jsonify({"error": slug}), 503
        logger.exception("web solved sheet creation failed for hunt %s", hunt_obj.id)
        return jsonify({"error": "sheet_creation_failed"}), 500
    except Exception:
        logger.exception("web solved sheet creation failed for hunt %s", hunt_obj.id)
        return jsonify({"error": "sheet_creation_failed"}), 500

    await _audit("hunt_solved_sheet", hunt_uid=uid)
    return jsonify({"ok": True, "solved_sheet_id": sheet_id})


@actions_bp.route("/hunts/<uid>/sheet-mode", methods=["PATCH"])
@login_required
async def patch_hunt_sheet_mode(uid):
    """Switch the hunt between "tabs" and "files" sheet mode (only while it has no
    puzzles or groups). Admin only, like the solved-sheet route."""
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), need_cog=False)
    if err:
        return err
    assert ctx is not None
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    mode = body.get("sheet_mode")
    if mode not in ("tabs", "files"):
        return jsonify({"error": "bad_sheet_mode"}), 400

    from bot.store import HuntJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404
    cog = puzzles_cog()
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503

    # Same parent the hunt-creation path uses; "" / unset means the Drive root.
    settings = GuildSettingsDb.get(int(ctx["guild_snowflake"]))
    parent_id = (getattr(settings, "drive_parent_id", "") or None)
    try:
        async with hunt_sheet_lock(hunt_obj.id):
            await cog.service.set_sheet_mode(hunt_obj, mode, guild_parent_id=parent_id)
    except RuntimeError as exc:
        slug = str(exc)
        if slug in ("sheet_mode_locked", "files_mode_no_solved_sheet"):
            return jsonify({"error": slug}), 409
        if slug == "bad_sheet_mode":
            return jsonify({"error": slug}), 400
        if slug == "gsheet_unavailable":
            return jsonify({"error": slug}), 503
        logger.exception("web sheet-mode switch failed for hunt %s", hunt_obj.id)
        return jsonify({"error": "sheet_mode_failed"}), 500
    except Exception:
        logger.exception("web sheet-mode switch failed for hunt %s", hunt_obj.id)
        return jsonify({"error": "sheet_mode_failed"}), 500

    # Same re-pin as !sheet_mode: the welcome gains/loses the Drive folder link.
    guild = bot_app().bot.get_guild(int(ctx["guild_snowflake"])) if ctx["guild_snowflake"] else None
    if guild is not None:
        await cog.service.notifier.refresh_info(guild, hunt_obj)
    await _audit("hunt_sheet_mode", hunt_uid=uid, detail=f"sheet_mode={mode}")
    return jsonify({"ok": True, "sheet_mode": mode})


# --- Retag puzzles between metas (organiser only) ---------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/meta", methods=["POST"])
@login_required
async def retag_puzzle(puzzle_id):
    """Move/add/remove a puzzle to/from a metapuzzle, via the bot's own logic.

    Body: {"group_code": "ABC123", "mode": "move"|"add"|"remove"}. "move" also
    relocates the Discord channel to the meta's category (mirrors !move_to_meta).
    """
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), need_cog=False)
    if err:
        return err
    assert ctx is not None
    # Ungated: tagging is a Tag-area operation, open on the bang edge
    # (!feeds / !informs / !remove_from) so a solver can tidy a meta's
    # membership without an organiser around. Reaching here already means
    # a logged-in guild member who passes hunt_visible.
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
        if mode == "remove":
            assert target_group is not None  # guaranteed by the bad_group_code check above
            if puzzle.id == target_group.target_puzzle_id:
                return jsonify({"error": "cannot_remove_own_meta"}), 400
        if mode == "clear" and puzzle.is_target:
            # A metapuzzle can't be unassigned (mirrors "move", which never clears
            # a metapuzzle's tags — doing so would orphan its round).
            return jsonify({"error": "cannot_unassign_meta"}), 400

        bot = bot_app().bot
        cog = puzzles_cog()
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
                        # The Feeds line is written once, after the loop.
                        await cog.service.untag_from(puzzle, g, refresh_headers=False)
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
        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        if mode == "clear":
            # Every untag above skipped its own write; one now (never raises).
            await cog.service.refresh_header_links(hunt_obj, [puzzle], reload=True)
        await _audit("retag", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"], detail=group_code)
        # Best-effort: keep the Unassigned Puzzles tab in sync.
        if hunt_obj is not None:
            asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
        resp: dict[str, Any] = {"ok": True}
        if warning:
            resp["sheet_warning"] = warning
        return jsonify(resp)


# --- Convert a metapuzzle back to a feeder (organiser only, destructive) -----

@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-feeder/preview", methods=["GET"])
@login_required
async def convert_to_feeder_preview(puzzle_id):
    """Read-only preview for the convert confirm dialog: how many feeders (and
    which) demoting this metapuzzle would orphan by deleting its round."""
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), tier="organiser", need_cog=False)
    if err:
        return err
    assert ctx is not None

    from bot.store import PuzzleJsonDb

    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    if puzzle is None:
        return jsonify({"error": "puzzle_not_found"}), 404
    if not puzzle.is_target:
        return jsonify({"error": "not_a_metapuzzle"}), 400

    cog = puzzles_cog()
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
    family's edge wrapper — organiser-gated, group-locked, audited, board pushed —
    and treats a Sheets failure as a non-fatal warning (the demotion is committed)."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import PuzzleJsonDb

    # Target + every member, in ONE sorted acquisition (H5), like the other convert
    # edges and delete_round. This demote is group-wide even though it never reaches
    # bulk_re_edge: TagGroupJsonDb.delete drops the group's rows in `tags` too, so a
    # member's in-flight commit would re-INSERT its edge against the deleted group
    # (#27) — an FK violation, not merely a lost update. Nothing on
    # PuzzleService.convert_to_puzzle's path takes a puzzle lock, so this single
    # acquisition cannot nest.
    async with puzzles_lock(convert_lock_ids(puzzle_id=puzzle_id)):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        if not puzzle.is_target:
            return jsonify({"error": "not_a_metapuzzle"}), 400

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
        resp: dict[str, Any] = {"ok": True}
        if orphaned is not None:
            resp["orphaned"] = orphaned
        if warning:
            resp["sheet_warning"] = warning
        return jsonify(resp)


# --- Convert endpoints for capstone, group, runaround ----------------------

def _convert_to_type_handler(puzzle_id, new_type, error_if_not_meta: bool | None = True):
    """Shared handler for convert-to-capstone/group/runaround.

    error_if_not_meta: True = metas only, False = non-metas only, None = any
    state — convert_to_type promotes a bare puzzle in place (solved or not),
    same as the bang commands."""
    async def handler():
        from bot.store import HuntJsonDb, PuzzleJsonDb

        ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
        if err:
            return err
        assert ctx is not None
        if ctx["hunt"]["archived"]:
            return jsonify({"error": "hunt_archived"}), 403
        perm = await require_organiser(ctx["guild_snowflake"])
        if perm:
            return perm

        # Target + every member, in ONE sorted acquisition (H5): convert_to_type ->
        # bulk_re_edge mutates every member, and taking the target here and the
        # members deeper would invert the order delete/archive acquires them in.
        async with puzzles_lock(convert_lock_ids(puzzle_id=puzzle_id)):
            puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
            if puzzle is None:
                return jsonify({"error": "puzzle_not_found"}), 404

            if error_if_not_meta is True and not puzzle.is_target:
                return jsonify({"error": "not_a_metapuzzle"}), 400
            if error_if_not_meta is False and puzzle.is_target:
                return jsonify({"error": "already_a_metapuzzle"}), 400

            hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
            cog = puzzles_cog()
            if cog is None:
                return jsonify({"error": "bot_unavailable"}), 503
            snowflake = ctx["guild_snowflake"]
            guild = bot_app().bot.get_guild(int(snowflake)) if snowflake else None

            warning = None
            summary = None  # stays None if convert_to_type raises before returning
            try:
                tag_group, summary = await cog.service.convert_to_type(
                    guild, hunt_obj, puzzle, new_type
                )
            # SheetRefreshError subclasses RuntimeError, so it must be caught first —
            # otherwise a stale-sheet warning would be misreported as a 400 error.
            except SheetRefreshError as exc:
                warning = str(exc)
            except RuntimeError as exc:
                return jsonify({"error": _error_slug(exc)}), 400
            except (discord.RateLimited, discord.HTTPException) as exc:
                return _discord_error_response(exc)
            except HttpError:
                logger.exception("convert_to_%s: sheet update failed for puzzle %s", new_type.lower(), puzzle.id)
                warning = "A sheet update failed; the change is saved. Use Rebuild to retry."

            asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
            await _audit(f"convert_to_{new_type.lower()}", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
            resp: dict[str, Any] = {"ok": True, "summary": summary}
            if warning:
                resp["sheet_warning"] = warning
            return jsonify(resp)

    return handler


@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-capstone", methods=["POST"])
@login_required
async def convert_to_capstone(puzzle_id):
    """Convert a puzzle to a capstone (informs edge).

    A metapuzzle changes group type and flips all feeder edges to informs; a
    regular puzzle (solved or not) is promoted in place straight to capstone.
    Mirrors the !convert_to_capstone bang command."""
    handler = _convert_to_type_handler(puzzle_id, "Capstone", error_if_not_meta=None)
    return await handler()


@actions_bp.route("/puzzles/<int:puzzle_id>/convert-to-runaround", methods=["POST"])
@login_required
async def convert_to_runaround_puzzle(puzzle_id):
    """Convert a puzzle to a runaround (subtree source mode).

    A metapuzzle changes group type and source mode without changing
    membership edges; a regular puzzle (solved or not) is promoted in place.
    Mirrors the !convert_to_runaround bang command."""
    handler = _convert_to_type_handler(puzzle_id, "Runaround", error_if_not_meta=None)
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
    """Demote a metapuzzle back to a regular puzzle. Alias of convert-to-feeder,
    kept for backward compat. Delegates entirely to convert_to_feeder so the two
    routes can't drift (HttpError handling, orphaned-feeder count, and the
    refresh/audit-on-warning behaviour stay identical)."""
    return await convert_to_feeder(puzzle_id)


# --- Delete a puzzle (organiser only) ----------------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>", methods=["DELETE"])
@login_required
async def delete_puzzle(puzzle_id):
    """Permanently delete a puzzle: DB record, sheet tab, Discord channel, and
    meta sheet refresh. Organiser-gated, puzzle-locked, audited, board pushed.
    A sheet-refresh failure is a non-fatal warning (deletion is committed)."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        warning = None
        try:
            await cog.service.delete_puzzle(guild, hunt_obj, puzzle)
        except ValueError:
            # Target puzzles cannot be deleted as ordinary puzzles (would orphan
            # the owning tag_group's target_puzzle_id).
            return jsonify({"error": "cannot_delete_target"}), 400
        except SheetRefreshError as exc:
            warning = str(exc)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)

        await _audit("delete_puzzle", puzzle_id=puzzle_id, hunt_uid=ctx["hunt"]["uid"])
        resp: dict[str, Any] = {"ok": True}
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
    # Deliberately no archived 403 guard — admins must be able to delete archived hunts.
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), allow_archived=True, need_cog=False)
    if err:
        return err
    assert ctx is not None
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    from bot.store import HuntJsonDb, PuzzleJsonDb

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404

    bot = bot_app().bot
    cog = puzzles_cog()
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    snowflake = ctx["guild_snowflake"]
    guild = bot.get_guild(int(snowflake)) if snowflake else None

    member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_hunt(hunt_obj.id)]

    try:
        async with puzzles_lock(member_puzzle_ids):
            failed = await cog.service.delete_hunt(guild, hunt_obj)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("delete_hunt", hunt_uid=uid,
                 detail=f"name={hunt_obj.name}")
    resp: dict[str, Any] = {"ok": True}
    if failed:
        resp["failed_channels"] = failed
    return jsonify(resp)


# --- Delete a round (organiser only) -----------------------------------------

@actions_bp.route("/tag_groups/<int:tag_group_id>", methods=["DELETE"])
@login_required
async def delete_tag_group(tag_group_id):
    """Delete a tag group. Body `keep_puzzles` (default true): keep every
    member puzzle's DB record/sheet/channel, deleting only the round's own
    target puzzle (or dedicated chat channel, for a target-less Group round)
    and relocating kept puzzles' channels into the hunt's general category;
    pass false to delete every member puzzle's DB record, sheet tab, and
    Discord channel too. Either way the group's Discord category and DB
    record are always deleted. Per-puzzle/channel failures are isolated.
    Returns failed_channels (names whose Discord channel could not be
    deleted or relocated) alongside ok:true so the caller can surface what
    remains. Organiser-gated, audited, board pushed."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_tag_group(tag_group_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import PuzzleJsonDb, TagGroupJsonDb

    tag_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if tag_group is None:
        return jsonify({"error": "round_not_found"}), 404

    body = await request.get_json(silent=True) or {}
    keep_puzzles = bool(body.get("keep_puzzles", True))

    member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_tag_group(tag_group.id)]

    try:
        async with puzzles_lock(member_puzzle_ids):
            failed_channels = await cog.service.delete_round(
                guild, hunt_obj, tag_group, keep_puzzles=keep_puzzles)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("delete_round", hunt_uid=ctx["hunt"]["uid"],
                 detail=f"tag_group_id={tag_group_id} name={tag_group.name}")
    resp: dict[str, Any] = {"ok": True}
    if failed_channels:
        resp["failed_channels"] = failed_channels
    return jsonify(resp)


@actions_bp.route("/tag_groups/<int:tag_group_id>/rename", methods=["POST"])
@login_required
async def rename_tag_group(tag_group_id):
    """Rename a tag group + its Discord container(s), via the bot (mirrors
    !rename_group). GROUP ONLY — never renames the target puzzle. Organiser+ only."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_tag_group(tag_group_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    body = await request.get_json(silent=True) or {}
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import TagGroupJsonDb

    if TagGroupJsonDb.check_duplicates_in_hunt(
            new_name, hunt_obj, exclude_group_id=tag_group_id):
        return jsonify({"error": "duplicate_name"}), 400

    tag_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if tag_group is None:
        return jsonify({"error": "round_not_found"}), 404

    try:
        await cog.service.rename_group(guild, hunt_obj, tag_group, new_name)
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("rename_group", hunt_uid=ctx["hunt"]["uid"],
                 detail=f"tag_group_id={tag_group_id} name={new_name}")
    return jsonify({"ok": True})


@actions_bp.route("/tag_groups/<int:tag_group_id>/change-type", methods=["POST"])
@login_required
async def change_group_type(tag_group_id):
    """Change a tag group's type (Metapuzzle/Capstone/Runaround/Group), keyed on
    the group. Organiser-gated (matches delete/rename). Body {"type": "<label>"}.
    Group-menu equivalent of the puzzle-keyed convert bang commands."""
    from bot.core.puzzle_service import GROUP_TYPES, GROUP_TYPE_INPUT_ALIASES
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_tag_group(tag_group_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    body = await request.get_json(silent=True) or {}
    raw_type = body.get("type") or ""
    new_type = GROUP_TYPE_INPUT_ALIASES.get(raw_type, raw_type)
    if new_type not in GROUP_TYPES:
        return jsonify({"error": "invalid_type"}), 400

    from bot.store import TagGroupJsonDb
    tag_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if tag_group is None:
        return jsonify({"error": "round_not_found"}), 404

    warning = None
    summary = None  # stays None if convert_tag_group_type raises SheetRefreshError
    try:
        # Target + every member, in ONE sorted acquisition (H5), matching the
        # puzzle-keyed convert routes and the delete/archive edges.
        # convert_tag_group_type -> convert_to_type -> bulk_re_edge mutates every
        # member and now takes no lock of its own, so this is the only acquisition.
        async with puzzles_lock(convert_lock_ids(tag_group=tag_group)):
            _group, summary = await cog.service.convert_tag_group_type(
                guild, hunt_obj, tag_group, new_type)
    # SheetRefreshError subclasses RuntimeError, so it must be caught first —
    # otherwise a stale-sheet warning would be misreported as a 400 error.
    except SheetRefreshError as exc:
        warning = str(exc)
    except RuntimeError as exc:
        return jsonify({"error": _error_slug(exc)}), 400
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    except HttpError:
        # refresh_target re-raises the RAW Sheets error, so this route needs the
        # same catch its puzzle-keyed twin has: by the time a render fails the
        # retype is already committed, and a 500 here would both misreport a
        # saved mutation and skip the @pushes_board push (M7).
        logger.exception("change_group_type: sheet update failed for group %s",
                         tag_group_id)
        warning = "A sheet update failed; the change is saved. Use Rebuild to retry."

    asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
    await _audit("change_group_type", hunt_uid=ctx["hunt"]["uid"],
                 detail=f"tag_group_id={tag_group_id} type={new_type}")
    resp: dict[str, Any] = {"ok": True, "summary": summary}
    if warning:
        resp["sheet_warning"] = warning
    return jsonify(resp)


# --- Guild settings (admin only) ---------------------------------------------

# Whitelisted `guilds` columns the dashboard may write. Deliberately excludes
# guild_id/guild_name/id: those mirror Discord and are maintained by the join
# handler, not by hand.
_GUILD_SETTINGS_KEYS = {
    "discord_bot_channel", "drive_parent_id", "drive_resources_id",
    "discord_use_voice_channels", "website_url", "template_link",
}
_GUILD_ROLE_KEYS = {"admin_role_ids", "organiser_role_ids"}


def _role_ids_out(raw):
    """A stored comma-separated id column as a list of STRINGS.

    Every Discord id leaves this module as a string: snowflakes are 64-bit and
    exceed JavaScript's safe integer range, so a JSON number silently loses
    precision in the browser.
    """
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


async def _guild_settings_ctx(guild_id):
    """Resolve (guild_row, guild, settings) for an admin-gated guild route, or an
    error tuple. `guild_id` is the `guilds.id` row id the /api/guilds list uses,
    not the Discord snowflake."""
    guilds_map = await web_data.get_guilds_map()
    row = guilds_map.get(guild_id)
    if row is None:
        return None, (jsonify({"error": "guild_not_found"}), 404)
    snowflake = row["guild_snowflake"]
    perm = await require_admin(snowflake)
    if perm:
        return None, perm
    guild = bot_app().bot.get_guild(int(snowflake)) if snowflake else None
    if guild is None:
        return None, (jsonify({"error": "bot_unavailable"}), 503)
    settings = GuildSettingsDb.get(snowflake)
    if settings is None:
        return None, (jsonify({"error": "guild_not_found"}), 404)
    return (row, guild, settings), None


# --- Team-server setup (owner or Admin tier on the team server) -------------

async def _team_setup_ctx(guild_id):
    """Resolve (guild_row, guild, member) for the setup routes, or an error tuple.

    Not require_admin: a freshly cloned server's owner usually holds no `mod`
    yet, so the gate is !setup_server's own -- the server's owner, or anyone
    holding the Admin tier there."""
    from bot.core import role_gate
    guilds_map = await web_data.get_guilds_map()
    row = guilds_map.get(guild_id)
    if row is None:
        return None, (jsonify({"error": "guild_not_found"}), 404)
    snowflake = row["guild_snowflake"]
    guild = bot_app().bot.get_guild(int(snowflake)) if snowflake else None
    if guild is None:
        return None, (jsonify({"error": "bot_unavailable"}), 503)
    user_id = int((current_user() or {}).get("id") or 0)
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            member = None
        except discord.HTTPException:
            return None, (jsonify({"error": "discord_unavailable"}), 503)
    if member is None:
        return None, (jsonify({"error": "not_a_member"}), 403)
    if member.id != guild.owner_id and not role_gate.member_holds_tier(
            {r.id for r in member.roles}, guild, ADMIN_ROLES):
        return None, (jsonify({"error": "not_admin"}), 403)
    return (row, guild, member), None


@actions_bp.route("/guilds/<int:guild_id>/setup", methods=["GET"])
@login_required
async def get_guild_setup(guild_id):
    """What the setup page needs: template problems, a prefill from a partly
    finished setup, and the hubs the viewer may set this server up under."""
    from bot.core import hunt_archive, role_gate, role_panel, server_setup
    resolved, err = await _team_setup_ctx(guild_id)
    if err:
        return err
    assert resolved is not None
    _row, guild, member = resolved
    bot = bot_app().bot
    settings = GuildSettingsDb.get(guild.id)
    if settings is None:
        return jsonify({"error": "guild_not_found"}), 404

    hubs = []
    for hub in bot.guilds:
        if hub.id == guild.id:
            continue
        hub_settings = GuildSettingsDb.get(hub.id)
        if hub_settings is None or hub_settings.setup_complete_time is not None:
            continue
        if HuntJsonDb.live_team_hunt_for_guild(hub.id) is not None:
            continue
        try:
            hub_user = await hunt_archive.hub_member(hub, member.id)
        except discord.HTTPException:
            continue
        if hub_user is None or not role_gate.member_holds_tier(
                {r.id for r in hub_user.roles}, hub, ADMIN_ROLES):
            continue
        admin_ids = role_gate.tier_role_ids(hub, ADMIN_ROLES)
        roles = sorted(
            (r for r in hub.roles
             if not r.is_default() and not r.managed and r.id not in admin_ids
             and r.name not in ORGANISER_ROLE_NAMES),
            key=lambda r: r.position, reverse=True)
        hubs.append({"id": str(hub.id), "name": hub.name,
                     "roles": [{"id": str(r.id), "name": r.name} for r in roles],
                     "multi_team_hunts": [
                         {"id": m["id"], "name": m["name"], "team_count": int(m["team_count"]),
                          "url": m.get("url") or "", "start_timestamp": m.get("start_timestamp")}
                         for m in MultiTeamDb.running_for_hub(hub.id)]})

    existing = None
    hunt = HuntJsonDb.get_by_attr(guild_id=guild_id)
    if hunt is not None:
        mt = MultiTeamDb.for_hunt(hunt.id)
        roster = (role_panel.find_roster_role(bot, hunt.roster_role_id)
                  if hunt.roster_role_id else None)
        existing = {
            "hub_id": str(roster.guild.id) if roster is not None else None,
            "roster_role_id": str(hunt.roster_role_id) if hunt.roster_role_id else None,
            "hunt_name": hunt.name,
            "hunt_url": hunt.url,
            "multi_team": ({"id": mt["multi_team_id"], "name": mt["name"]}
                           if mt is not None else None),
        }
    done = settings.setup_complete_time
    return jsonify({
        "template_problems": server_setup.template_problems(guild, settings),
        "already_set_up": done.isoformat() if done is not None else None,
        "existing": existing,
        "hunt_uid": hunt.uid if hunt is not None else None,
        "hubs": hubs,
    })


_SETUP_REQUIRED = {"hub_id", "roster_role_id", "hunt_name", "hunt_url"}
_SETUP_OPTIONAL = {"start_timestamp", "username", "password", "create_solved_sheet",
                   "send_invites", "sheet_mode", "multi_team"}


def _snowflake_field(value):
    """An id sent as a string or an int, or None if it is neither."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


@actions_bp.route("/guilds/<int:guild_id>/setup", methods=["POST"])
@login_required
async def post_guild_setup(guild_id):
    """Run !setup_server's flow from the dashboard. A refusal or a stopped setup
    is a normal 200 outcome (`ok: false`); re-posting finishes a partial one."""
    from bot.core import server_setup, team_invites
    body = await request.get_json(silent=True) or {}
    missing = _SETUP_REQUIRED - set(body.keys())
    if missing:
        return jsonify({"error": "missing_keys", "required": sorted(missing)}), 400
    extra = set(body.keys()) - _SETUP_REQUIRED - _SETUP_OPTIONAL
    if extra:
        return jsonify({"error": "bad_keys", "unknown": sorted(extra)}), 400
    hub_id = _snowflake_field(body["hub_id"])
    roster_role_id = _snowflake_field(body["roster_role_id"])
    if hub_id is None:
        return jsonify({"error": "bad_value", "field": "hub_id"}), 400
    if roster_role_id is None:
        return jsonify({"error": "bad_value", "field": "roster_role_id"}), 400
    for key in ("hunt_name", "hunt_url", "username", "password"):
        value = body.get(key, "")
        if not isinstance(value, str) or (key in _SETUP_REQUIRED and not value.strip()):
            return jsonify({"error": "bad_value", "field": key}), 400
    raw_start = body.get("start_timestamp", 0)
    if isinstance(raw_start, bool) or not isinstance(raw_start, (int, float)):
        return jsonify({"error": "bad_value", "field": "start_timestamp"}), 400
    try:
        datetime.datetime.fromtimestamp(raw_start, tz=pytz.UTC)
        start_timestamp = int(raw_start)
    except (OverflowError, OSError, ValueError):
        return jsonify({"error": "bad_value", "field": "start_timestamp"}), 400
    for key in ("create_solved_sheet", "send_invites", "multi_team"):
        if not isinstance(body.get(key, False), bool):
            return jsonify({"error": "bad_value", "field": key}), 400
    sheet_mode = body.get("sheet_mode", "tabs")
    if sheet_mode not in ("tabs", "files"):
        return jsonify({"error": "bad_sheet_mode"}), 400
    if sheet_mode == "files" and body.get("create_solved_sheet", False):
        return jsonify({"error": "files_mode_no_solved_sheet"}), 409

    resolved, err = await _team_setup_ctx(guild_id)
    if err:
        return err
    assert resolved is not None
    _row, guild, member = resolved
    cog = puzzles_cog()
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    bot = bot_app().bot

    outcome = await server_setup.perform_setup(
        bot, cog.service, guild, member, hub_id, roster_role_id,
        body["hunt_name"], body["hunt_url"],
        start_timestamp=start_timestamp, username=body.get("username", ""),
        password=body.get("password", ""),
        create_solved_sheet=body.get("create_solved_sheet", False),
        sheet_mode=sheet_mode, multi_team=body.get("multi_team", False),
        invites_by_dashboard=bool(body.get("send_invites")), via_dashboard=True)

    invites = None
    if outcome.ok and outcome.hunt is not None and body.get("send_invites"):
        try:
            invites = team_invites.report_text(
                await team_invites.send_invites(bot, guild, outcome.hunt))
        except Exception:
            logger.exception("setup_server: sending invites failed for guild %s", guild_id)
            invites = ("Setup finished, but sending invites failed — the error has been "
                       f"logged. Run !send_invites {body['hunt_name']} in #bot-commands.")

    hunt_uid = outcome.hunt.uid if outcome.hunt is not None else None
    await _audit("setup_server", hunt_uid=hunt_uid,
                 detail=f"guild_id={guild_id} hub={hub_id} ok={outcome.ok}")
    return jsonify({"ok": outcome.ok, "lines": outcome.lines,
                    "error": outcome.error or None, "hunt_uid": hunt_uid,
                    "invites": invites})


def _team_hunt_out(hub, hunt, snowflake, bot, *, count=False):
    """The list/detail summary of one team hunt, plus the team guild when the bot
    can see it (running hunts only; an archived hunt's row lives on the hub)."""
    team_guild = (None if hunt.archived or snowflake is None
                  else bot.get_guild(int(snowflake)))
    here = None
    if count and team_guild is not None:
        here = sum(1 for m in team_guild.members if not m.bot)
    roster = hub.get_role(int(hunt.roster_role_id))
    out = {
        "uid": hunt.uid,
        "name": hunt.name,
        "team": roster.name if roster else None,
        "archived": bool(hunt.archived),
        "team_server": team_guild.name if team_guild is not None else None,
        "on_server_count": here,
        "invited_waiting_count": 0,
        "multi_team": None,
    }
    mt = MultiTeamDb.for_hunt(hunt.id)
    if mt is not None:
        out["multi_team"] = {"id": mt["multi_team_id"], "name": mt["name"],
                             "completed": mt["completed_time"] is not None,
                             "archived": mt["archived_time"] is not None}
    return out, team_guild


async def _complete_response(hunt_obj, body):
    """Validate {"complete": bool}, run multi_team.set_complete and shape the reply
    (shared by the team-server and hub routes). Returns (response, complete)."""
    complete = (body or {}).get("complete")
    if not isinstance(complete, bool):
        return (jsonify({"error": "bad_value", "field": "complete"}), 400), None
    try:
        outcome = await multi_team.set_complete(bot_app().bot, hunt_obj, complete)
    except multi_team.MultiTeamRefused as exc:
        return (jsonify({"error": "refused", "message": str(exc)}), 409), None
    except Exception:
        logger.exception("set_complete(%s) failed for hunt %s", complete, hunt_obj.id)
        return (jsonify({"error": "hub_update_failed"}), 502), None
    return (jsonify({"ok": True, "changed": outcome.changed,
                     "message": outcome.message}), 200), (complete, outcome.changed)


@actions_bp.route("/guilds/<int:guild_id>/team-hunts", methods=["GET"])
@login_required
async def list_team_hunts(guild_id):
    """The hub's team hunts, running first. Admin only (as Server settings)."""
    resolved, err = await _guild_settings_ctx(guild_id)
    if err:
        return err
    assert resolved is not None
    _row, hub, _settings = resolved
    bot = bot_app().bot
    hunts = []
    team_rows = team_access.hub_team_hunts(hub, HuntJsonDb.team_hunts())
    invites = HuntInviteDb.for_hunts([h.id for h, _ in team_rows])
    for hunt, snowflake in team_rows:
        out, team_guild = _team_hunt_out(hub, hunt, snowflake, bot, count=True)
        here_ids = ({m.id for m in team_guild.members}
                    if team_guild is not None else set())
        out["invited_waiting_count"] = len(
            team_access.waiting(invites.get(hunt.id, []), here_ids))
        hunts.append(out)
    return jsonify({"hunts": hunts})


@actions_bp.route("/guilds/<int:guild_id>/team-hunts/<uid>", methods=["GET"])
@login_required
async def get_team_hunt(guild_id, uid):
    """One team hunt's access: on the server now, invited-not-arrived, history."""
    resolved, err = await _guild_settings_ctx(guild_id)
    if err:
        return err
    assert resolved is not None
    _row, hub, _settings = resolved
    bot = bot_app().bot
    hunt = HuntJsonDb.get_by_attr(uid=uid)
    if (hunt is None or not hunt.roster_role_id
            or hub.get_role(int(hunt.roster_role_id)) is None):
        return jsonify({"error": "hunt_not_found"}), 404
    snowflake = HuntJsonDb.guild_snowflake_for_hunt(hunt.id)
    out, team_guild = _team_hunt_out(hub, hunt, snowflake, bot)

    def name_of(user_id):
        for g in (hub, team_guild):
            m = g.get_member(user_id) if g is not None else None
            if m is not None:
                return m.display_name
        return None

    on_server = None
    team_role_missing = False
    if team_guild is not None:
        on_server = team_access.members_now(team_guild, hunt)
        team_role_missing = (not hunt.role_id
                             or team_guild.get_role(int(hunt.role_id)) is None)
    here_ids = {int(m["user_id"]) for m in on_server or []}
    waiting = team_access.waiting(HuntInviteDb.for_hunt(hunt.id), here_ids)
    for w in waiting:
        w["name"] = name_of(int(w["user_id"])) or w["user_id"]
    out["invited_waiting_count"] = len(waiting)
    if on_server is not None:
        out["on_server_count"] = len(on_server)
    return jsonify({
        "hunt": out,
        "bot_on_server": team_guild is not None,
        "team_role_missing": team_role_missing,
        "on_server": on_server,
        "invited_waiting": waiting,
        "history": team_access.history(HuntAccessLogDb.for_hunt(hunt.id), name_of),
    })


@actions_bp.route("/guilds/<int:guild_id>/team-hunts/<uid>/complete", methods=["POST"])
@login_required
async def post_team_hunt_complete(guild_id, uid):
    """Mark a team hunt finished (or not) from the hub. Admin only (as Server settings)."""
    resolved, err = await _guild_settings_ctx(guild_id)
    if err:
        return err
    assert resolved is not None
    _row, hub, _settings = resolved
    hunt = HuntJsonDb.get_by_attr(uid=uid)
    if (hunt is None or not hunt.roster_role_id
            or hub.get_role(int(hunt.roster_role_id)) is None):
        return jsonify({"error": "hunt_not_found"}), 404
    body = await request.get_json(silent=True)
    resp, done = await _complete_response(hunt, body)
    if done is not None:
        await _audit("hunt_complete", hunt_uid=hunt.uid,
                     detail=f"complete={done[0]} changed={done[1]}")
    return resp


@actions_bp.route("/guilds/<int:guild_id>/multi-team/<int:mt_id>/archive", methods=["POST"])
@login_required
async def post_multi_team_archive(guild_id, mt_id):
    """Archive a multi-team hunt's hub channels now, as !archive_multi_team does.
    Admin only (as Server settings). Runs in the background and posts its summary
    to the hub admin channel."""
    resolved, err = await _guild_settings_ctx(guild_id)
    if err:
        return err
    assert resolved is not None
    _row, hub, _settings = resolved
    mt = MultiTeamDb.get(mt_id)
    if mt is None or int(mt["hub_guild_id"]) != hub.id or mt["archived_time"] is not None:
        return jsonify({"error": "multi_team_not_found"}), 404
    body = await request.get_json(silent=True) or {}
    if body.get("confirm_name") != mt["name"]:
        return jsonify({"error": "confirm_mismatch"}), 400
    try:
        multi_team.ensure_no_teams_playing(mt_id)
    except multi_team.MultiTeamRefused as exc:
        return jsonify({"error": "teams_still_playing", "message": str(exc)}), 409

    from bot.core import hub_channel

    async def _run():
        summary = await multi_team.archive_multi_team(bot_app().bot, hub, mt_id)
        await hub_channel.post(hub, summary, allowed_mentions=discord.AllowedMentions.none())

    task = asyncio.create_task(_run())
    _ARCHIVE_TASKS.add(task)
    task.add_done_callback(_ARCHIVE_TASKS.discard)
    await _audit("multi_team_archive", detail=f"hub={hub.id} multi_team={mt_id}")
    return jsonify({"ok": True, "started": True}), 202


@actions_bp.route("/guilds/<int:guild_id>/settings", methods=["GET"])
@login_required
async def get_guild_settings(guild_id):
    """This guild's settings, its available roles, and each authorisation tier's
    state. Admin only — the same tier the equivalent bang commands need."""
    resolved, err = await _guild_settings_ctx(guild_id)
    if err:
        return err
    assert resolved is not None
    row, guild, settings = resolved

    user = current_user()
    assert user is not None  # @login_required
    your_role_ids = await resolve_member_role_ids(user["id"], row["guild_snowflake"])

    tiers = {}
    for key, names in (("admin", ADMIN_ROLES), ("organiser", ORGANISER_ROLE_NAMES)):
        tiers[key] = {
            "pinned": role_gate.tier_is_pinned(guild, names),
            "role_ids": sorted(str(i) for i in role_gate.tier_role_ids(guild, names)),
        }

    return jsonify({
        "guild": {
            "id": guild_id,
            "guild_snowflake": str(row["guild_snowflake"]),
            "guild_name": row["guild_name"],
        },
        "settings": {
            **{key: getattr(settings, key, None) for key in _GUILD_SETTINGS_KEYS},
            **{key: _role_ids_out(getattr(settings, key, None))
               for key in _GUILD_ROLE_KEYS},
        },
        "tiers": tiers,
        "roles": [{"id": str(r.id), "name": r.name} for r in guild.roles],
        "your_role_ids": sorted(str(i) for i in (your_role_ids or ())),
    })


@actions_bp.route("/guilds/<int:guild_id>/settings", methods=["PATCH"])
@login_required
async def patch_guild_settings(guild_id):
    """Update whitelisted guild settings, including the authorisation role ids.
    Admin only. Web equivalent of !update_settings + !set_admin_roles /
    !set_organiser_roles."""
    resolved, err = await _guild_settings_ctx(guild_id)
    if err:
        return err
    assert resolved is not None
    row, guild, settings = resolved

    body = await request.get_json(silent=True) or {}
    extra = set(body.keys()) - _GUILD_SETTINGS_KEYS - _GUILD_ROLE_KEYS
    if extra:
        return jsonify({"error": "bad_keys", "unknown": sorted(extra)}), 400

    guild_role_ids = {r.id for r in guild.roles}
    parsed_roles = {}
    for key in _GUILD_ROLE_KEYS & set(body.keys()):
        value = body[key]
        if not isinstance(value, list):
            return jsonify({"error": "bad_value", "field": key}), 400
        try:
            ids = [int(str(v).strip()) for v in value if str(v).strip()]
        except ValueError:
            return jsonify({"error": "bad_value", "field": key}), 400
        # A role id from another server would gate the tier on something nobody
        # here can hold — an unrecoverable lockout for the admin tier.
        foreign = [i for i in ids if i not in guild_role_ids]
        if foreign:
            return jsonify({"error": "unknown_role",
                            "field": key,
                            "role_ids": [str(i) for i in foreign]}), 400
        parsed_roles[key] = ids

    # The template link is stored canonically; "" clears it.
    if "template_link" in body:
        raw = body["template_link"]
        if not isinstance(raw, str):
            return jsonify({"error": "bad_value", "field": "template_link"}), 400
        if raw.strip():
            canonical = template_link.normalise(raw)
            if canonical is None:
                return jsonify({"error": "bad_value", "field": "template_link"}), 400
            body["template_link"] = canonical
        else:
            body["template_link"] = ""

    # Mirrors !set_admin_roles: pinning admin ids the caller does not hold leaves
    # nobody able to undo it. An EMPTY list is the documented revert-to-names
    # escape hatch, not a lockout.
    admin_ids = parsed_roles.get("admin_role_ids")
    if admin_ids:
        user = current_user()
        assert user is not None  # @login_required
        your_role_ids = await resolve_member_role_ids(user["id"], row["guild_snowflake"])
        if not (set(your_role_ids or ()) & set(admin_ids)):
            return jsonify({"error": "would_lock_you_out"}), 400

    # Load-modify-save: GuildSettingsDb.commit writes EVERY column, so building a
    # fresh row here would blank whatever this request didn't send.
    for key in _GUILD_SETTINGS_KEYS & set(body.keys()):
        setattr(settings, key, body[key])
    for key, ids in parsed_roles.items():
        setattr(settings, key, ",".join(str(i) for i in ids))
    GuildSettingsDb.commit(settings)

    await _audit("guild_settings", detail=f"guild_id={guild_id} keys={sorted(body)}")
    return jsonify({"ok": True})


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

    Returns {ok, actions, orphan_channels, orphan_tabs, stray_files, deleted,
    failures}. stray_files (files hunts) are reported only, never deleted.
    """
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None
    if guild is None:
        return jsonify({"error": "guild_unavailable"}), 503

    mode = request.args.get("mode", "report")
    if mode not in ("report", "apply", "apply-orphans"):
        return jsonify({"error": "invalid_mode", "valid": ["report", "apply", "apply-orphans"]}), 400

    body = await request.get_json(silent=True) or {}

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
        # Files hunts: files in the hunt's folders matching no puzzle --
        # reported, never deleted (not part of apply-orphans).
        "stray_files": [{"id": fid, "name": name, "url": spreadsheet_link(fid)}
                        for fid, name in report.stray_files],
        "failures": [{"target": t, "error": e} for t, e in report.failures],
        "deleted": deleted,
    })


# --- Media sweeper (organiser only) ------------------------------------------
#
# The bang-command pair is !media_stats / !media_sweep; both edges call the same
# bot/core/media_gc functions, so the numbers cannot drift apart.

async def _media_gc_ctx(uid):
    """(hunt_id, err) for a hunt whose media the current user may sweep."""
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return None, err
    assert ctx is not None
    if ctx["hunt"]["archived"]:
        return None, (jsonify({"error": "hunt_archived"}), 403)
    perm = await require_organiser(ctx["guild_snowflake"])
    if perm:
        return None, perm
    return ctx["hunt"]["id"], None


@actions_bp.route("/hunts/<uid>/media/stats", methods=["GET"])
@login_required
async def media_stats(uid):
    """Count this hunt's orphaned media. Report only — mutates nothing."""
    from bot.core import media_gc
    hunt_id, err = await _media_gc_ctx(uid)
    if err:
        return err
    assert hunt_id is not None
    orphans = media_gc.orphaned_media(hunt_id)
    return jsonify({
        "count": len(orphans),
        # A floor, not a total: media.size covers the original image only, never
        # its tile pyramid. The panel says "at least" for the same reason.
        "bytes": sum(m.size or 0 for m in orphans),
    })


@actions_bp.route("/hunts/<uid>/media/sweep", methods=["POST"])
@login_required
async def media_sweep(uid):
    """Delete this hunt's orphaned media, their files and their tiles."""
    from bot.core import media_gc
    hunt_id, err = await _media_gc_ctx(uid)
    if err:
        return err
    assert hunt_id is not None
    # sweep() is synchronous filesystem teardown — an rmtree per row, and one
    # pyramid alone is ~940 files. Run it off the loop the Discord gateway
    # shares, as the tiling and crop paths already do.
    stats = await asyncio.to_thread(media_gc.sweep, hunt_id)
    await _audit("sweep_media", hunt_uid=uid,
                 detail=f"count={stats.count} bytes={stats.bytes_freed}")
    return jsonify({"ok": True, "count": stats.count, "bytes": stats.bytes_freed})


# --- Rebuild a meta roll-up sheet (organiser only) --------------------------

@actions_bp.route("/rollups/<int:tag_group_id>/rebuild", methods=["POST"])
@login_required
async def rebuild_rollup(tag_group_id):
    """Rebuild one meta group's roll-up sheet, clearing its stale badge on success.
    Keyed on tag_group_id (a rollup is per group); organiser-gated via the group's hunt.
    Serialization is handled inside refresh_target's per-group rollup lock."""
    ctx, hunt_obj, cog, _guild, err = await _route_ctx(
        _visible_hunt_for_tag_group(tag_group_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import TagGroupJsonDb

    target_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if target_group is None:
        return jsonify({"error": "round_not_found"}), 404

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


# --- Create puzzles and metas (member-accessible, non-archived hunt required) ---


@actions_bp.route("/hunts/<uid>/puzzles", methods=["POST"])
@login_required
async def create_puzzle(uid):
    """Create a puzzle (channel + sheet) via the bot.

    An empty ``meta_code`` creates an *unassigned* puzzle (no meta tag); the
    channel lands in the hunt category (see PuzzleService.create_puzzle).
    """
    ctx, hunt_obj, cog, guild, err = await _route_ctx(_visible_hunt_by_uid(uid))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None
    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    group_code = (body.get("group_code") or "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import PuzzleJsonDb, TagGroupJsonDb

    target_group = None
    if group_code:
        target_group = TagGroupJsonDb.get_by_attr(group_code=group_code)
        if target_group is None or target_group.hunt_id != hunt_obj.id:
            return jsonify({"error": "bad_group_code"}), 400
    try:
        from bot.core.notifier import DiscordNotifier
        # Serialize check+create per hunt: a double-submit (or two creators) must
        # not both pass the duplicate check and each create a same-named puzzle.
        async with hunt_create_lock(hunt_obj.id):
            if PuzzleJsonDb.check_duplicates_in_hunt(name, hunt_obj.id):
                return jsonify({"error": "duplicate_name"}), 409
            puzzle = await cog.service.create_puzzle(
                guild, hunt_obj, target_group, name,
                notifier=DiscordNotifier(bot_app().bot),
            )
    except RuntimeError as exc:
        return jsonify({"error": _error_slug(exc)}), 400
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    except HttpError:
        # The sheet is made before the channel: a failure creates nothing.
        logger.exception("create_puzzle: sheet creation failed in hunt %s", uid)
        return jsonify({"error": "sheet_create_failed"}), 502
    await _audit("create_puzzle", puzzle_id=puzzle.id, hunt_uid=uid, detail=name)
    # Best-effort: keep the Unassigned Puzzles tab in sync (a no-meta create adds one).
    asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
    return jsonify({"ok": True, "puzzle_id": puzzle.id, "name": puzzle.name})


@actions_bp.route("/hunts/<uid>/metas", methods=["POST"])
@login_required
async def create_meta(uid):
    """Create a new metapuzzle/group (category + meta channel), via the bot (like !mp)."""
    # Creating a tag group is an organiser action, matching !round and the rest
    # of the group lifecycle (rename/delete/retype). Creating a PUZZLE stays open
    # -- see POST /hunts/<uid>/puzzles, deliberately ungated.
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    group_type = (body.get("type") or "Metapuzzle").strip()
    from bot.core.puzzle_service import TYPE_DEFAULTS, GROUP_TYPE_INPUT_ALIASES
    if group_type not in TYPE_DEFAULTS and group_type not in GROUP_TYPE_INPUT_ALIASES:
        return jsonify({"error": "bad_type"}), 400

    # create_tag_group's notifier needs the bot object itself, not just the guild.
    bot = bot_app().bot
    try:
        from bot.core.notifier import DiscordNotifier
        from bot.core.puzzle_service import default_source_mode_for, meta_puzzle_name_for
        meta_puzzle_name = meta_puzzle_name_for(group_type, name)
        stored_type = GROUP_TYPE_INPUT_ALIASES.get(group_type, group_type)
        # Same per-hunt create lock as create_puzzle: create_tag_group runs its own
        # duplicate check internally, so the whole call must be the critical section
        # (and it serializes against concurrent puzzle-creates in the hunt too).
        async with hunt_create_lock(hunt_obj.id):
            new_round, puzzle = await cog.service.create_tag_group(
                guild, hunt_obj, name,
                notifier=DiscordNotifier(bot),
                meta_puzzle_name=meta_puzzle_name,
                round_type=stored_type,
            )
        # Post-create: some types override the column's source_mode default.
        source_mode = default_source_mode_for(stored_type)
        if source_mode:
            await cog.service.set_source_mode(hunt_obj, new_round, source_mode)
    except RuntimeError as exc:
        return jsonify({"error": _error_slug(exc)}), 400
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    except HttpError:
        # The meta's sheet is made before its channel; create_tag_group rolls
        # the group back, so a failure creates nothing.
        logger.exception("create_meta: sheet creation failed in hunt %s", uid)
        return jsonify({"error": "sheet_create_failed"}), 502
    await _audit("create_meta", puzzle_id=getattr(puzzle, "id", None), hunt_uid=uid, detail=new_round.group_code)
    return jsonify({"ok": True, "tag_group_id": new_round.id, "group_code": new_round.group_code})


# --- Minimeta mini-puzzle creation (organiser only) --------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/minis", methods=["POST"])
@login_required
async def create_mini(puzzle_id):
    """Create a mini-puzzle (Discord thread) under a minimeta puzzle."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(_visible_hunt_for_puzzle(puzzle_id))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None
    # Ungated: adding a mini is a Create-area operation, open on the bang edge
    # (!add_mini). Deleting one stays organiser.
    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        minimeta = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if minimeta is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        try:
            mini = await cog.service.create_mini(guild, hunt_obj, minimeta, name)
        except RuntimeError as exc:
            return jsonify({"error": _error_slug(exc)}), 400
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        except HttpError:
            logger.exception("create_mini: sheet creation failed under puzzle %s", puzzle_id)
            return jsonify({"error": "sheet_create_failed"}), 502
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


# --- Delete one mini-puzzle (organiser only) ---------------------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/minis/<int:mini_id>", methods=["DELETE"])
@login_required
async def delete_mini(puzzle_id, mini_id):
    """Delete a single mini-puzzle under a minimeta: thread, sheet tab, DB row, and
    a re-render of the parent's rollup Table (mirrors !delete_mini). Organiser-gated,
    locked on the MINIMETA (as create_mini is), audited, board pushed.

    Nested under the parent so the pairing is checked, not assumed: a mini_id that
    is not this minimeta's child is a 404 rather than a back door onto the generic
    puzzle-delete route.
    """
    ctx, hunt_obj, cog, guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), tier="organiser")
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        mini = PuzzleJsonDb.get_by_attr(id=mini_id)
        if mini is None or mini.parent_puzzle_id != puzzle_id:
            return jsonify({"error": "mini_not_found"}), 404

        name = mini.name
        warning = None
        try:
            minimeta = await cog.service.delete_mini(guild, hunt_obj, mini)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except SheetRefreshError as exc:
            # Deletion is already committed — surface as a warning, not a failure.
            warning = str(exc)
            minimeta = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)

        await _audit("delete_mini", puzzle_id=mini_id, hunt_uid=ctx["hunt"]["uid"], detail=name)
        # The mini list lives on the PARENT's pinned info (as !delete_mini refreshes it).
        if minimeta is not None:
            await cog.web_refresh_info(guild, hunt_obj, minimeta)
        resp: dict[str, Any] = {"ok": True}
        if warning:
            resp["sheet_warning"] = warning
        return jsonify(resp)


# --- Unsolve a puzzle (any member of the hunt's guild) -----------------------

@actions_bp.route("/puzzles/<int:puzzle_id>/unsolve", methods=["POST"])
@login_required
async def unsolve_puzzle(puzzle_id):
    """Mark a solved puzzle unsolved via the bot."""
    ctx, hunt_obj, cog, guild, err = await _route_ctx(_visible_hunt_for_puzzle(puzzle_id))
    if err:
        return err
    assert ctx is not None and hunt_obj is not None and cog is not None

    from bot.store import PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        if not puzzle.solved and not puzzle.solution:
            return jsonify({"error": "not_solved"}), 400

        # Capture before service.unsolve clears it, for the channel message.
        prev_solution = puzzle.solution or ""
        warning = None
        try:
            await cog.service.unsolve(guild, hunt_obj, puzzle)
        except SheetRefreshError as exc:
            # The unsolve is committed; only a meta sheet went stale. Mirrors the
            # solve sibling. Without this the newly-reporting service (M27) would
            # 500 on an operation that in fact succeeded.
            warning = str(exc)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        await cog.web_post_unsolved(guild, hunt_obj, puzzle, prev_solution)
        await _audit("unsolve", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
        await _refresh_parent_minimeta_pin(cog, guild, hunt_obj, puzzle)

        if warning:
            return jsonify({"ok": True, "sheet_warning": warning})
        return jsonify({"ok": True})


@actions_bp.route("/evidence/<int:item_id>", methods=["PATCH"])
@login_required
async def update_evidence(item_id):
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_for_evidence(item_id), need_cog=False)
    if err:
        return err
    assert ctx is not None

    from bot.store import EvidenceJsonDb, HuntJsonDb, PuzzleJsonDb
    body = await request.get_json() or {}
    old = EvidenceJsonDb.get_by_id(item_id)
    if old is None:
        return jsonify({"error": "not_found"}), 404

    # A non-null target must belong to the same hunt as the evidence item, so
    # evidence can't be cross-linked into another hunt's puzzle.
    if body.get("target_puzzle_id") is not None:
        target = PuzzleJsonDb.get_by_attr(id=body["target_puzzle_id"])
        if target is None or target.hunt_id != old.hunt_id:
            return jsonify({"error": "cross_hunt_target"}), 400

    item = old

    # Apply note edit if provided
    note = (body.get("note") or "").strip()
    if note:
        item = EvidenceJsonDb.update_note(item_id, note) or item

    # Apply an image swap if the key is present (value may be None to detach).
    # The media must already exist and belong to THIS item's hunt: the id comes
    # from the client, and without the check an item could be pointed at another
    # hunt's private upload — the media route itself is hunt-visibility gated,
    # so this is the only place that would leak.
    if "media_id" in body:
        new_media_id = body.get("media_id")
        if new_media_id is not None:
            _media, err = _require_hunt_media(new_media_id, old.hunt_id)
            if err:
                return err
        item = EvidenceJsonDb.update_media(item_id, new_media_id) or item

    # Apply target reassignment if the key is present (value may be None to clear)
    cog = puzzles_cog()
    hunt_obj = HuntJsonDb.get_by_attr(id=item.hunt_id)
    target_changed = "target_puzzle_id" in body
    if target_changed:
        old_target = old.target_puzzle_id
        target_puzzle_id = body.get("target_puzzle_id")
        item = EvidenceJsonDb.set_target(item_id, target_puzzle_id) or item
        if cog is not None and hunt_obj is not None:
            if item.target_puzzle_id:
                asyncio.create_task(cog.service.link_informs_for_evidence_safe(
                    hunt_obj, item.source_puzzle_id, item.target_puzzle_id))
            if old_target and old_target != item.target_puzzle_id:
                asyncio.create_task(cog.service.unlink_informs_for_evidence_safe(
                    hunt_obj, item.source_puzzle_id, old_target))
                # The old target loses this contribution row — refresh it too.
                asyncio.create_task(cog.service.refresh_target_for_puzzle(hunt_obj, old_target))
            if item.target_puzzle_id:
                asyncio.create_task(cog.service.refresh_target_for_puzzle(hunt_obj, item.target_puzzle_id))

    if cog is not None and hunt_obj is not None:
        # A note-only edit must still refresh the current target so the edited
        # text shows in its contribution table (target unchanged here).
        if not target_changed and item.target_puzzle_id:
            asyncio.create_task(cog.service.refresh_target_for_puzzle(hunt_obj, item.target_puzzle_id))
        asyncio.create_task(cog.service.refresh_evidence_safe(hunt_obj))

    # Unconditional, and deliberately not left to the service: the only push
    # this route ever had came from the @pushes_board tag_with_edge/untag_from
    # behind link/unlink_informs_for_evidence_safe, which run ONLY when an
    # informs edge moves. A note edit, a media swap, or a target change on an
    # item with no edge is board-visible (HuntBoard.tsx invalidates
    # ["evidence", uid] on every board event) and pushed nothing. When an edge
    # DOES move both fire; the duplicate is harmless — a board event carries no
    # payload, so a second one is one more cache invalidation of the same keys,
    # which react-query collapses into the refetch already in flight.
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("update_evidence", puzzle_id=item.source_puzzle_id, hunt_uid=ctx["hunt"]["uid"])
    return jsonify({"id": item.id, "target_puzzle_id": item.target_puzzle_id,
                    "note": item.note, "media_id": item.media_id})


@actions_bp.route("/evidence/<int:item_id>", methods=["DELETE"])
@login_required
async def delete_evidence(item_id):
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_for_evidence(item_id), need_cog=False)
    if err:
        return err
    assert ctx is not None

    from bot.store import EvidenceJsonDb, HuntJsonDb
    old = EvidenceJsonDb.get_by_id(item_id)
    if old is None:
        return jsonify({"error": "not_found"}), 404

    hunt_obj = HuntJsonDb.get_by_attr(id=old.hunt_id)
    if hunt_obj is None:
        return jsonify({"error": "not_found"}), 404

    EvidenceJsonDb.delete(item_id)

    cog = puzzles_cog()
    if cog is not None:
        if old.target_puzzle_id:
            # Mirror the reassignment cleanup: drop the auto-added informs edge if
            # no routed evidence remains, and refresh the (old) target's table.
            asyncio.create_task(cog.service.unlink_informs_for_evidence_safe(
                hunt_obj, old.source_puzzle_id, old.target_puzzle_id))
            asyncio.create_task(cog.service.refresh_target_for_puzzle(
                hunt_obj, old.target_puzzle_id))
        asyncio.create_task(cog.service.refresh_evidence_safe(hunt_obj))

    # Same gap as update_evidence: deleting an item with no target runs no
    # decorated service call at all, so without this the row vanishes from the
    # DB and stays on every open Evidence panel.
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("delete_evidence", hunt_uid=hunt_obj.uid)
    return jsonify({"ok": True})


# --- Canvases and regions ---------------------------------------------------
#
# Two different permission bars, deliberately. Creating or deleting a canvas is
# organiser-level: it uploads or destroys a shared image everyone works on.
# Drawing, linking and editing REGIONS is open to any member who can see the
# hunt, exactly like logging evidence — everyone marks the picture up during a
# hunt, and gating that would defeat the point of the feature.

async def _canvas_ctx(canvas_id, require_manage=False):
    """(ctx, canvas_row, err) for a canvas the current user may act on."""
    from bot.store import CanvasJsonDb
    ctx, err = await _visible_hunt_for_canvas(canvas_id)
    if err:
        return None, None, err
    assert ctx is not None
    if ctx["hunt"]["archived"]:
        return None, None, (jsonify({"error": "hunt_archived"}), 403)
    if require_manage:
        perm = await require_organiser(ctx["guild_snowflake"])
        if perm is not None:
            return None, None, perm
    canvas_row = CanvasJsonDb.get_by_id(canvas_id)
    if canvas_row is None:
        return None, None, (jsonify({"error": "canvas_not_found"}), 404)
    return ctx, canvas_row, None


def _require_hunt_media(media_id, hunt_id):
    """(media, err) -- the media must exist and belong to `hunt_id`.

    The media id always comes from the client, and without this a request
    could point at another hunt's private upload: a canvas serving that
    hunt's bytes to anyone who can see this one, an evidence item or puzzle
    image citing it, or an /api/evidence POST attributing it to the wrong
    hunt. Same check, three call sites -- unified on one error key,
    `cross_hunt_media`.
    """
    from bot.store import MediaJsonDb
    media = MediaJsonDb.get_by_id(media_id)
    if media is None or media.hunt_id != hunt_id:
        return None, (jsonify({"error": "cross_hunt_media"}), 400)
    return media, None


def _require_hunt_puzzle(puzzle_id, hunt_id):
    """(puzzle, err) -- the puzzle must exist and belong to `hunt_id`.

    A region's link target arrives in the request body, so without this a
    region on this hunt's canvas could be pointed at another hunt's -- another
    GUILD's -- puzzle. An informs link is not merely cosmetic: it writes a real
    evidence_items row against the target, so an unchecked id lands a
    contribution, and a crop of this hunt's picture, inside a hunt the caller
    cannot see. Same object-scoping bar `_require_hunt_media` sets for the ids
    beside it -- one error key, `cross_hunt_puzzle`.
    """
    from bot.store import PuzzleJsonDb
    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    if puzzle is None or puzzle.hunt_id != hunt_id:
        return None, (jsonify({"error": "cross_hunt_puzzle"}), 400)
    return puzzle, None


async def _media_ctx(media_id):
    """(ctx, media_row, err) for an image the current user may mark up.

    The hunt comes from the media row rather than the URL, which is what stops
    a media id from another hunt being acted on under this hunt's visibility
    check — the same shape /api/media/<id> uses to serve the bytes.

    Marking up is NOT organiser-gated: it is the same bar as logging evidence,
    because marking up the picture is what everyone does during a hunt.
    """
    from bot.store import HuntJsonDb, MediaJsonDb
    media = MediaJsonDb.get_by_id(media_id)
    if media is None:
        return None, None, (jsonify({"error": "media_not_found"}), 404)
    hunt_row = HuntJsonDb.get_by_attr(id=media.hunt_id)
    if hunt_row is None:
        return None, None, (jsonify({"error": "media_not_found"}), 404)
    ctx, err = await _visible_hunt_by_uid(hunt_row.uid)
    if err:
        return None, None, err
    assert ctx is not None
    if ctx["hunt"]["archived"]:
        return None, None, (jsonify({"error": "hunt_archived"}), 403)
    return ctx, media, None


async def _region_crop_media_id(canvas_row, existing, shape):
    """The media id a region should point at after a (re)link.

    Reuses the existing crop whenever it already carries markup, instead of
    regenerating one underneath it. Ink is drawn on one specific image, so a
    fresh crop leaves every mark pointing at nothing it was drawn against -- and
    the orphaned media row is swept by the GC 24 hours later, taking its
    media_markup rows with it via CASCADE. The loss is silent and permanent.

    update_region has guarded this since the canvas work landed;
    create_puzzle_from_region performed the same link change through the same
    canvas_service.update_region and did not (M11). One helper so the third
    caller cannot drift again.
    """
    from bot.core import markup
    if existing is not None and existing.crop_media_id and markup.list_marks(existing.crop_media_id):
        return existing.crop_media_id
    return await _crop_region_media(canvas_row, shape)


async def _crop_region_media(canvas_row, shape):
    """Crop `shape`'s rect out of the canvas's original image and save it as
    its own media row, so an informs-region's evidence carries the actual
    picture rather than a text-only reference.

    Crops the canvas media's ORIGINAL (untiled) source file, not the tile
    pyramid — that isn't a single re-openable image. Runs the decode+crop off
    the event loop, the same reason media_tiling.tile_media does: PNG has no
    partial/strip decode in libvips, so cropping any size out of a large sheet
    still means a full decode, up to the same peak memory B1 already accepts
    for the pyramid. This only runs on an explicit informs-link action (rare),
    not per-tile-request, so the cost is bounded.

    Never raises: a crop is a convenience layered on top of the evidence row,
    not a precondition for it — same "never raises" contract as tile_media, so
    a failure here degrades to a text-only evidence row rather than blocking
    the region write.
    """
    from bot.core.canvas_shape import rect_bounds
    from bot.core.image_tiler import crop_bytes
    from bot.core.message_media import media_root
    from bot.store import MediaJsonDb
    media = MediaJsonDb.get_by_id(canvas_row.media_id)
    if media is None:
        return None
    try:
        rect = rect_bounds(shape)
        # width/height come back from crop_bytes too, but ingest_image_bytes
        # now re-probes them itself, so they go unused here. Bounded by the same
        # semaphore as tiling: a crop is a full decode of the source sheet and
        # contends for the same memory.
        png, _, _ = await run_bounded_image_work(
            crop_bytes, media_root() / media.path, rect)
        crop = ingest_image_bytes(
            hunt_id=media.hunt_id, filename="region-crop.png", raw=png,
            origin="canvas_crop", db=MediaJsonDb,
            # Server-generated bytes, not an upload: the byte cap is a hostile-
            # input control, and a large region re-encodes to lossless PNG well
            # past it — which silently produced a region with no image. The
            # pixel ceiling still applies.
            enforce_size_limit=False)
        if crop.tile_status == "pending":
            schedule_tiling(crop, MediaJsonDb,
                             after=functools.partial(_notify_board_for_hunt_id, media.hunt_id))
        return crop.id
    except Exception:
        logger.exception("could not crop region evidence for canvas media %s",
                         canvas_row.media_id)
        return None


@actions_bp.route("/hunts/<uid>/canvases", methods=["POST"])
@login_required
async def create_canvas(uid):
    from bot.store import CanvasJsonDb
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_by_uid(uid), tier="organiser", need_cog=False)
    if err:
        return err
    assert ctx is not None
    body = await request.get_json(silent=True) or {}
    media_id = body.get("media_id")
    if not media_id:
        return jsonify({"error": "media_id required"}), 400
    try:
        media_id = int(media_id)
    except (TypeError, ValueError):
        # Client-supplied: a bad id is a 400, not an uncaught ValueError -> 500.
        return jsonify({"error": "bad_media_id"}), 400
    media, err = _require_hunt_media(media_id, ctx["hunt"]["id"])
    if err:
        return err
    assert media is not None  # the (media, None) branch
    row = CanvasJsonDb.create(
        hunt_id=ctx["hunt"]["id"], media_id=media.id,
        name=(body.get("name") or "").strip()[:255],
        note=(body.get("note") or None))
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("create_canvas", hunt_uid=ctx["hunt"]["uid"])
    return jsonify(web_data.canvas_to_json(row, [], media)), 201


@actions_bp.route("/canvases/<int:canvas_id>", methods=["PATCH"])
@login_required
async def update_canvas(canvas_id):
    from bot.store import CanvasJsonDb, CanvasRegionJsonDb, MediaJsonDb
    ctx, canvas_row, err = await _canvas_ctx(canvas_id, require_manage=True)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    body = await request.get_json(silent=True) or {}
    name = (body.get("name") if "name" in body else canvas_row.name) or ""
    note = body.get("note") if "note" in body else canvas_row.note
    CanvasJsonDb.update(canvas_id, name=name.strip()[:255], note=note or None)
    row = CanvasJsonDb.get_by_id(canvas_id)
    assert row is not None
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("update_canvas", hunt_uid=ctx["hunt"]["uid"])
    return jsonify(web_data.canvas_to_json(
        row, CanvasRegionJsonDb.get_by_canvas(canvas_id),
        MediaJsonDb.get_by_id(row.media_id)))


@actions_bp.route("/canvases/<int:canvas_id>", methods=["DELETE"])
@login_required
async def delete_canvas(canvas_id):
    """Delete a canvas, its regions, their evidence rows, and its tiles.

    B1 deliberately left tile sweeping to whoever added the first delete path,
    because `media` rows only ever cascaded from `hunts` before now. This is that
    path: without the rmtree, deleting canvases would leak a 9.6 MB pyramid every
    time.
    """
    import shutil
    from bot.core.image_tiler import tiles_relpath
    from bot.core.message_media import media_root
    from bot.store import CanvasJsonDb, MediaJsonDb
    ctx, canvas_row, err = await _canvas_ctx(canvas_id, require_manage=True)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    media = MediaJsonDb.get_by_id(canvas_row.media_id)
    # Regions and their evidence rows go by FK cascade from hunt_canvases.
    CanvasJsonDb.delete(canvas_id)
    if media is not None:
        tiles = media_root() / tiles_relpath(media.hunt_id, media.id)
        # Best effort: a failure here leaks disk, which is far better than
        # refusing the delete and leaving a canvas nobody can remove.
        try:
            shutil.rmtree(tiles, ignore_errors=True)
        except Exception:
            logger.exception("could not remove tiles for media %s", media.id)
        if media.tile_status == "ready":
            # The pyramid we just removed is gone, but the row still believes
            # it is 'ready' — that combination is what left retile_canvas's
            # state machine refusing to help (a 'ready' row is a 409, on the
            # theory that rebuilding a good pyramid is pure cost). Reset to
            # 'pending', the same value a fresh upload gets when it needs
            # tiling, so the row and the pyramid agree again and a later
            # retile (or a new canvas built on the same media) can rebuild it.
            # 'none' rows are left alone: that image was never a tiling
            # candidate, and flipping it to 'pending' would misrepresent it as
            # one. Best effort, matching the rmtree above: a failure here
            # leaves a stale 'ready' row, which is recoverable by hand and far
            # better than turning a successful delete into a 500.
            try:
                MediaJsonDb.update_tiles(media.id, "pending", 0)
            except Exception:
                logger.exception("could not reset tile status for media %s", media.id)
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("delete_canvas", hunt_uid=ctx["hunt"]["uid"])
    return jsonify({"ok": True})


@actions_bp.route("/canvases/<int:canvas_id>/retile", methods=["POST"])
@login_required
async def retile_canvas(canvas_id):
    """Re-run tiling for a canvas stuck 'pending' or 'failed'.

    'pending' is retileable, not just 'failed': the status is only ever cleared
    by the in-process task that set it, so if the process died mid-build the row
    stays pending forever with nothing left to finish it, and the panel sits on
    "Preparing image" for good.

    Not organiser-gated — the same bar as ocr-preview: a stuck canvas blocks
    everyone hunting, and retiling changes no content.
    """
    from bot.store import MediaJsonDb
    ctx, canvas_row, err = await _canvas_ctx(canvas_id)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    media = MediaJsonDb.get_by_id(canvas_row.media_id)
    # 'ready' is excluded because rebuilding a good pyramid is pure cost, and
    # 'none' because that image was never a tiling candidate.
    if media is None or media.tile_status not in ("pending", "failed"):
        return jsonify({"error": "not_retileable"}), 409
    # Schedule BEFORE flipping the row: a 'pending' row may still have a live
    # build behind it (this route accepts 'pending' precisely because it cannot
    # tell a dead build from a live one), and schedule_tiling is what knows.
    # Resetting tile_levels first would otherwise discard a good pyramid's
    # metadata on behalf of a build that was never started.
    if not schedule_tiling(media, MediaJsonDb,
                           after=functools.partial(_notify_board_soon,
                                                   ctx["hunt"]["uid"],
                                                   ctx["guild_snowflake"])):
        return jsonify({"error": "already_tiling", "tile_status": "pending"}), 409
    MediaJsonDb.update_tiles(media.id, "pending", 0)
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("retile_canvas", hunt_uid=ctx["hunt"]["uid"])
    return jsonify({"ok": True, "tile_status": "pending"}), 202


@actions_bp.route("/canvases/<int:canvas_id>/regions", methods=["POST"])
@login_required
async def create_region(canvas_id):
    from bot.core import canvas as canvas_service
    from bot.core.canvas_shape import ShapeRejected
    ctx, canvas_row, err = await _canvas_ctx(canvas_id)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    body = await request.get_json(silent=True) or {}
    shape, role, puzzle_id = body.get("shape"), body.get("role"), body.get("puzzle_id")
    # Before the crop, not after: a request that is about to be refused should
    # not pay for a decode of the canvas original.
    if puzzle_id is not None:
        _puzzle, err = _require_hunt_puzzle(puzzle_id, ctx["hunt"]["id"])
        if err:
            return err
    media_id = None
    if role in (canvas_service.INFORMS, canvas_service.LOCATES) and puzzle_id is not None:
        media_id = await _crop_region_media(canvas_row, shape)
    try:
        region = canvas_service.create_region(
            canvas_id=canvas_id, hunt_id=ctx["hunt"]["id"], shape=shape,
            label=(body.get("label") or "").strip()[:255],
            note=(body.get("note") or None),
            puzzle_id=puzzle_id, role=role, media_id=media_id)
    except (ShapeRejected, canvas_service.RegionRejected) as exc:
        return jsonify({"error": str(exc)}), 400
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("create_region", hunt_uid=ctx["hunt"]["uid"])
    return jsonify(web_data.region_to_json(region)), 201


@actions_bp.route("/canvases/<int:canvas_id>/regions/<int:region_id>",
                  methods=["PATCH"])
@login_required
async def update_region(canvas_id, region_id):
    from bot.core import canvas as canvas_service
    from bot.core.canvas_shape import ShapeRejected
    from bot.store import CanvasRegionJsonDb
    ctx, canvas_row, err = await _canvas_ctx(canvas_id)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    existing = CanvasRegionJsonDb.get_by_id(region_id)
    # Checking the parent explicitly: without it, a region id from another hunt's
    # canvas would be edited under this hunt's visibility check.
    if existing is None or existing.canvas_id != canvas_id:
        return jsonify({"error": "region_not_found"}), 404
    body = await request.get_json(silent=True) or {}
    import json as _json
    shape = body["shape"] if "shape" in body else _json.loads(existing.shape)
    label = (body.get("label") if "label" in body else existing.label) or ""
    note = body.get("note") if "note" in body else existing.note
    puzzle_id = body.get("puzzle_id") if "puzzle_id" in body else existing.puzzle_id
    role = body.get("role") if "role" in body else existing.role
    # The effective id, not only a body-supplied one: a link already stored is
    # re-asserted by every PATCH that keeps it, and clearing a bad one still
    # works because that sends puzzle_id null and skips the check entirely.
    if puzzle_id is not None:
        _puzzle, err = _require_hunt_puzzle(puzzle_id, ctx["hunt"]["id"])
        if err:
            return err
    link_changed = (existing.puzzle_id != puzzle_id or existing.role != role)
    media_id = None
    if (link_changed and role in (canvas_service.INFORMS, canvas_service.LOCATES)
            and puzzle_id is not None):
        # Nothing in the UI can change a region's shape today, but PATCH accepts
        # one, so this path is real. See _region_crop_media_id for why an inked
        # crop is reused rather than regenerated.
        media_id = await _region_crop_media_id(canvas_row, existing, shape)
    try:
        region = canvas_service.update_region(
            region_id=region_id, hunt_id=ctx["hunt"]["id"], shape=shape,
            label=label.strip()[:255], note=note or None,
            puzzle_id=puzzle_id, role=role, media_id=media_id)
    except (ShapeRejected, canvas_service.RegionRejected) as exc:
        return jsonify({"error": str(exc)}), 400
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("update_region", hunt_uid=ctx["hunt"]["uid"])
    assert region is not None
    return jsonify(web_data.region_to_json(region))


@actions_bp.route("/canvases/<int:canvas_id>/regions/<int:region_id>",
                  methods=["DELETE"])
@login_required
async def delete_region(canvas_id, region_id):
    from bot.core import canvas as canvas_service
    from bot.store import CanvasRegionJsonDb
    ctx, canvas_row, err = await _canvas_ctx(canvas_id)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    existing = CanvasRegionJsonDb.get_by_id(region_id)
    if existing is None or existing.canvas_id != canvas_id:
        return jsonify({"error": "region_not_found"}), 404
    canvas_service.delete_region(region_id)
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("delete_region", hunt_uid=ctx["hunt"]["uid"])
    return jsonify({"ok": True})


@actions_bp.route("/media/<int:media_id>/markup", methods=["POST"])
@login_required
async def create_mark(media_id):
    from bot.core import markup as markup_service
    from bot.core.markup_shape import MarkupRejected
    ctx, media, err = await _media_ctx(media_id)
    if err:
        return err
    assert ctx is not None and media is not None
    body = await request.get_json(silent=True) or {}
    try:
        mark = markup_service.create_mark(
            media_id=media_id, mark=body.get("mark"), style=body.get("style"))
    except MarkupRejected as exc:
        return jsonify({"error": str(exc)}), 400
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("create_mark", hunt_uid=ctx["hunt"]["uid"])
    return jsonify(web_data.mark_to_json(mark)), 201


@actions_bp.route("/media/<int:media_id>/markup/<int:mark_id>", methods=["PATCH"])
@login_required
async def update_mark(media_id, mark_id):
    from bot.core import markup as markup_service
    from bot.core.markup_shape import MarkupRejected
    ctx, media, err = await _media_ctx(media_id)
    if err:
        return err
    assert ctx is not None and media is not None
    body = await request.get_json(silent=True) or {}
    try:
        mark = markup_service.update_mark(
            mark_id=mark_id, media_id=media_id,
            style=body.get("style"), text=body.get("text"))
    except markup_service.MarkNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except MarkupRejected as exc:
        return jsonify({"error": str(exc)}), 400
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    # Markup is open to any member who can see the hunt, and this edits ink that
    # may be someone else's -- audited for the same reason create_mark is.
    await _audit("update_mark", hunt_uid=ctx["hunt"]["uid"])
    return jsonify(web_data.mark_to_json(mark))


@actions_bp.route("/media/<int:media_id>/markup/<int:mark_id>", methods=["DELETE"])
@login_required
async def delete_mark(media_id, mark_id):
    from bot.core import markup as markup_service
    ctx, media, err = await _media_ctx(media_id)
    if err:
        return err
    assert ctx is not None and media is not None
    try:
        markup_service.delete_mark(mark_id=mark_id, media_id=media_id)
    except markup_service.MarkNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    # Erasing another member's ink was the one markup action that left no trail.
    await _audit("delete_mark", hunt_uid=ctx["hunt"]["uid"])
    return jsonify({"ok": True})


# --- Puzzle images (any member of the hunt's guild, like logging evidence) --
#
# A puzzle's ordered strip of images: canvas `locates` crops plus direct
# uploads. Same permission bar as notes/evidence — any member who can see an
# unarchived hunt, not organiser-gated.

@actions_bp.route("/puzzles/<int:puzzle_id>/images", methods=["GET"])
@login_required
async def get_puzzle_images(puzzle_id):
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), allow_archived=True, need_cog=False)
    if err:
        return err
    assert ctx is not None

    from bot.core import puzzle_images
    images = puzzle_images.list_images(puzzle_id)
    return jsonify([web_data.puzzle_image_to_json(i) for i in images])


@actions_bp.route("/puzzles/<int:puzzle_id>/images", methods=["POST"])
@login_required
async def add_puzzle_image(puzzle_id):
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), need_cog=False)
    if err:
        return err
    assert ctx is not None

    from bot.core import puzzle_images
    from bot.store import PuzzleJsonDb
    body = await request.get_json(silent=True) or {}
    media_id = body.get("media_id")
    if not media_id:
        return jsonify({"error": "media_id required"}), 400

    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    if puzzle is None:
        return jsonify({"error": "puzzle_not_found"}), 404

    # The media must belong to the same hunt as the puzzle, so a client-supplied
    # id can't cross-link another hunt's private upload onto this puzzle's
    # strip — same guard update_evidence carries for its image swap.
    _media, err = _require_hunt_media(media_id, puzzle.hunt_id)
    if err:
        return err

    image = puzzle_images.attach_upload(puzzle_id, media_id)
    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("add_puzzle_image", puzzle_id=puzzle_id, hunt_uid=ctx["hunt"]["uid"])
    return jsonify(web_data.puzzle_image_to_json(image)), 201


@actions_bp.route("/puzzles/<int:puzzle_id>/images", methods=["PATCH"])
@login_required
async def reorder_puzzle_images(puzzle_id):
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), need_cog=False)
    if err:
        return err
    assert ctx is not None

    from bot.core import puzzle_images
    body = await request.get_json(silent=True) or {}
    ordered_ids = body.get("ordered_ids") or []
    try:
        puzzle_images.reorder(puzzle_id, ordered_ids)
    except puzzle_images.ImageNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except puzzle_images.BadReorder as exc:
        # 400, not 404: every id named does exist and is owned — the list
        # itself is malformed, which is a client bug rather than a stale id.
        return jsonify({"error": str(exc)}), 400

    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("reorder_puzzle_images", puzzle_id=puzzle_id, hunt_uid=ctx["hunt"]["uid"])
    return jsonify({"ok": True})


@actions_bp.route("/puzzles/<int:puzzle_id>/images/<int:image_id>", methods=["DELETE"])
@login_required
async def delete_puzzle_image(puzzle_id, image_id):
    ctx, _hunt_obj, _cog, _guild, err = await _route_ctx(
        _visible_hunt_for_puzzle(puzzle_id), need_cog=False)
    if err:
        return err
    assert ctx is not None

    from bot.core import puzzle_images
    try:
        puzzle_images.detach(image_id, puzzle_id)
    except puzzle_images.ImageNotFound as exc:
        return jsonify({"error": str(exc)}), 404

    _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"])
    await _audit("delete_puzzle_image", puzzle_id=puzzle_id, hunt_uid=ctx["hunt"]["uid"])
    return jsonify({"ok": True})


@actions_bp.route("/canvases/<int:canvas_id>/regions/<int:region_id>/create-puzzle",
                  methods=["POST"])
@login_required
async def create_puzzle_from_region(canvas_id, region_id):
    """Create a puzzle from a drawn region, then link the region to it.

    Delegates to the SAME PuzzleService.create_puzzle the board's create button
    uses — Discord channel, sheet tab, tagged into the chosen round — so there is
    no duplicate creation logic and no "half-real puzzle" concept.

    Permission is the create path's, not the looser region-drawing bar: making a
    channel and a sheet tab is not the same act as drawing a box. It is not
    organiser-gated, because creating a puzzle from the board is not either.

    Locking mirrors the /hunts/<uid>/puzzles route exactly: hunt_create_lock
    around check+create, because create_puzzle does NOT take that lock itself
    (bot/core is lock-free by convention and callers lock). Taking a second,
    different lock here would be the documented self-deadlock shape.
    """
    from bot.core import canvas as canvas_service
    from bot.core.canvas_shape import ShapeRejected
    from bot.store import CanvasRegionJsonDb, HuntJsonDb, PuzzleJsonDb, TagGroupJsonDb
    ctx, canvas_row, err = await _canvas_ctx(canvas_id)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    existing = CanvasRegionJsonDb.get_by_id(region_id)
    if existing is None or existing.canvas_id != canvas_id:
        return jsonify({"error": "region_not_found"}), 404

    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    # Informs, not locates, when the client says nothing. A locates region is
    # geometry and nothing else — it produces no crop, no evidence row and
    # nothing the puzzle itself can see — so defaulting to it quietly threw away
    # the part of the picture the user had just drawn a box around. Informs
    # keeps that crop as evidence against the puzzle, which is recoverable;
    # picking locates on purpose is not lossy in the same way.
    role = body.get("role") or canvas_service.INFORMS
    if not name:
        return jsonify({"error": "empty_name"}), 400
    if role not in canvas_service.ROLES:
        return jsonify({"error": "bad_role"}), 400

    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    assert hunt_obj is not None  # row exists: the visible-hunt check just passed
    target_group = None
    if body.get("round_id"):
        try:
            round_id = int(body["round_id"])
        except (TypeError, ValueError):
            # Client-supplied: a bad id is the same 400 as an unknown one, not
            # an uncaught ValueError surfacing as a 500.
            return jsonify({"error": "bad_round"}), 400
        target_group = TagGroupJsonDb.get_by_attr(id=round_id)
        if target_group is None or target_group.hunt_id != hunt_obj.id:
            return jsonify({"error": "bad_round"}), 400

    bot = bot_app().bot
    cog = puzzles_cog()
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    guild = bot.get_guild(int(ctx["guild_snowflake"])) if ctx["guild_snowflake"] else None
    try:
        from bot.core.notifier import DiscordNotifier
        async with hunt_create_lock(hunt_obj.id):
            if PuzzleJsonDb.check_duplicates_in_hunt(name, hunt_obj.id):
                return jsonify({"error": "duplicate_name"}), 409
            puzzle = await cog.service.create_puzzle(
                guild, hunt_obj, target_group, name,
                notifier=DiscordNotifier(bot_app().bot),
            )
    except RuntimeError as exc:
        return jsonify({"error": _error_slug(exc)}), 400
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
    except HttpError:
        # The sheet is made before the channel: a failure creates nothing.
        logger.exception("create_puzzle_from_region: sheet creation failed in hunt %s",
                         hunt_obj.id)
        return jsonify({"error": "sheet_create_failed"}), 502

    # Link only after the puzzle really exists: a failed create must leave the
    # region exactly as it was, not pointing at a puzzle that was never made.
    import json as _json
    shape = _json.loads(existing.shape)
    link_changed = (existing.puzzle_id != puzzle.id or existing.role != role)
    media_id = None
    if link_changed and role in (canvas_service.INFORMS, canvas_service.LOCATES):
        # Same guard as update_region: promoting a region to a puzzle must not
        # destroy a member's annotations on its crop (M11).
        media_id = await _region_crop_media_id(canvas_row, existing, shape)
    try:
        region = canvas_service.update_region(
            region_id=region_id, hunt_id=hunt_obj.id,
            shape=shape, label=existing.label,
            note=existing.note, puzzle_id=puzzle.id, role=role, media_id=media_id)
    except (ShapeRejected, canvas_service.RegionRejected) as exc:
        return jsonify({"error": str(exc)}), 400

    _notify_board(hunt_obj.uid, ctx["guild_snowflake"])
    await _audit("create_puzzle_from_region", puzzle_id=puzzle.id,
                 hunt_uid=hunt_obj.uid, detail=name)
    asyncio.create_task(cog.service.refresh_unassigned_safe(hunt_obj))
    assert region is not None
    return jsonify(web_data.region_to_json(region)), 201


def _crop_and_extract_text(src, rect):
    """Compose crop + OCR in one executor hop.

    Two separate run_in_executor calls (crop, then OCR) would be two
    thread-pool round trips for one interactive click, for no benefit --
    both steps are CPU-bound and happen back to back regardless.
    """
    from bot.core.image_tiler import crop_bytes
    from bot.core.ocr import extract_text
    png, _, _ = crop_bytes(src, rect)
    return extract_text(png)


@actions_bp.route("/canvases/<int:canvas_id>/ocr-preview", methods=["POST"])
@login_required
async def ocr_canvas_region(canvas_id):
    """Extract text from a region's rect, without saving anything.

    Unlike _crop_region_media, failures here ARE surfaced to the caller: the
    whole point of this route is handing the user something back, so a silent
    failure would just look broken. Nothing is written to MediaJsonDb or
    EvidenceJsonDb here -- this is a preview, called before the region is ever
    submitted; the real (persisted) crop still only happens through the
    existing create_region/update_region/create_puzzle_from_region routes.
    """
    from bot.core.canvas_shape import ShapeRejected, rect_bounds
    from bot.core.message_media import media_root
    from bot.store import MediaJsonDb
    ctx, canvas_row, err = await _canvas_ctx(canvas_id)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    body = await request.get_json(silent=True) or {}
    try:
        rect = rect_bounds(body.get("shape"))
    except ShapeRejected as exc:
        return jsonify({"error": str(exc)}), 400
    media = MediaJsonDb.get_by_id(canvas_row.media_id)
    if media is None:
        return jsonify({"error": "canvas_missing_media"}), 400
    try:
        # Cropping a region decodes the whole sheet, so this contends for the
        # same memory as a pyramid build -- the bound _crop_region_media already
        # uses, which this route bypassed via the default executor (M10).
        text = await run_bounded_image_work(
            _crop_and_extract_text, media_root() / media.path, rect)
    except Exception:
        logger.exception("OCR failed for canvas media %s", media.id)
        return jsonify({"error": "ocr_failed"}), 500
    return jsonify({"text": text})
