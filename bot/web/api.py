"""Dashboard read API and live-activity WebSocket."""
import asyncio
import logging
import time

from quart import Blueprint, jsonify, make_response, request, send_file, session, websocket

from .typed_app import bot_app, puzzles_cog

from . import data, events
from .auth import (
    ADMIN_ROLES,
    current_user,
    get_or_fetch_member,
    hunt_visible,
    login_required,
    resolve_membership,
    user_can_manage,
)
from bot.core import role_gate
from bot.store import MultiTeamDb
from bot.core.sheet_location import drive_folder_link, spreadsheet_link
from bot.utils import config
from bot.core.role_gate import organiser_gate_active

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")

# How often the live-activity WebSocket re-checks that the connected user still
# belongs to a guild (so a kick/role-change ends the stream within this window).
_WS_AUTH_REFRESH_SECONDS = 60


def _sheet_base(sheet_id):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid=" if sheet_id else None


@api_bp.route("/guilds")
@login_required
async def list_guilds():
    """Return the guilds the current user belongs to.
    Pass ?admin=1 to restrict to guilds where the user holds an admin role."""
    user = current_user()
    assert user is not None  # @login_required
    admin_only = request.args.get("admin") == "1"
    membership, _ = await resolve_membership(user["id"])
    guilds_map = await data.get_guilds_map()
    result = []
    for guild_id, info in guilds_map.items():
        snowflake = info["guild_snowflake"]
        if snowflake not in membership:
            continue
        can_administer = await user_can_manage(user["id"], snowflake, roles=ADMIN_ROLES)
        if admin_only and not can_administer:
            continue
        # Only the ADMIN tier's pinned state travels here, for the dashboard's
        # "not pinned" banner. An unpinned ORGANISER tier is a legitimate steady
        # state (the small-team convenience, M9), and nagging about it would train
        # people to ignore the banner that matters.
        guild = bot_app().bot.get_guild(int(snowflake)) if snowflake else None
        result.append({
            "id": guild_id,
            "guild_snowflake": str(snowflake) if snowflake else None,
            "guild_name": info["guild_name"],
            "can_administer": can_administer,
            "admin_tier_pinned": (
                role_gate.tier_is_pinned(guild, ADMIN_ROLES) if guild is not None
                else True   # unresolvable guild: stay quiet rather than cry wolf
            ),
        })
    return jsonify({"guilds": result})


@api_bp.route("/hunts")
@login_required
async def list_hunts():
    """Hunts visible to the current user, split into active and archived (old)."""
    user = current_user()
    assert user is not None  # @login_required
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
    assert user is not None  # every caller is behind @login_required
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
    user = current_user()
    assert user is not None  # @login_required
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "not_found_or_forbidden"}), guild_or_status
    assert isinstance(guild_or_status, dict)  # the (hunt, guild_info) branch

    guild_snowflake = guild_or_status.get("guild_snowflake")
    puzzles = await data.get_hunt_puzzles(hunt, guild_snowflake)
    rollups = await data.get_rollups_by_tag_group(hunt["id"])
    all_groups = await data.get_all_tag_groups(hunt["id"])
    hunt_sheets = await data.get_hunt_sheets(hunt)
    board = data.build_board(puzzles, rollups, all_groups)
    solved = sum(1 for p in puzzles if p["solved"])
    # Archived hunts are read-only even for organisers.
    can_manage = (not hunt["archived"]) and await user_can_manage(user["id"], guild_snowflake)
    can_administer = await user_can_manage(user["id"], guild_snowflake, roles=ADMIN_ROLES)
    _guild = bot_app().bot.get_guild(int(guild_snowflake)) if guild_snowflake else None
    # Fail safe: an unresolvable guild is treated as gated (organiser_gate_active
    # True), never as a bypass.
    gate_active = organiser_gate_active(_guild) if _guild is not None else True
    # Gating status: a non-zero role_id means the hunt is gated to a team role.
    # Resolve the role's name (best-effort) so the dashboard can show it.
    gating_role_id = hunt.get("role_id") or 0
    gating_role_name = None
    if gating_role_id and _guild is not None:
        _role = _guild.get_role(gating_role_id)
        if _role is not None:
            gating_role_name = _role.name
    from bot.core.puzzle_service import CREATE_META_TYPES
    mt = MultiTeamDb.for_hunt(hunt["id"])
    return jsonify({
        "hunt": {
            "uid": hunt["uid"],
            "name": hunt["name"],
            "url": hunt["url"],
            "archived": bool(hunt["archived"]),
            "is_gated": bool(gating_role_id),
            # A team hunt (tied to a hub role); the role id itself stays server-side.
            "is_team_hunt": bool(hunt.get("roster_role_id")),
            # Set when the hunt is one team of a multi-team hunt on a hub.
            "multi_team": ({"name": mt["name"], "completed": mt["completed_time"] is not None}
                           if mt is not None else None),
            "role_name": gating_role_name,
            "guild_snowflake": str(guild_snowflake) if guild_snowflake else None,
            "sheet_base_url": _sheet_base(hunt["google_sheet_id"]),
            "solved_sheet_base_url": _sheet_base(hunt["solved_sheet_id"]),
            "sheet_url": spreadsheet_link(hunt["google_sheet_id"]),
            "solved_sheet_url": spreadsheet_link(hunt["solved_sheet_id"]),
            "sheet_mode": hunt.get("sheet_mode") or "tabs",
            "drive_folder_url": drive_folder_link(hunt.get("drive_folder_id")),
            "can_manage": can_manage,
            "can_administer": can_administer,
            "organiser_gate_active": gate_active,
            "start_timestamp": hunt.get("start_timestamp"),
            "username": hunt.get("username") or "",
            "password": hunt.get("password") or "",
            "url_sep": hunt.get("url_sep") or "-",
            "puzzle_prefix": hunt.get("puzzle_prefix") or "puzzle",
        },
        "summary": {"total": len(puzzles), "solved": solved},
        "board": board,
        "hunt_sheets": hunt_sheets,
        "meta_types": list(CREATE_META_TYPES),
    })


@api_bp.route("/channels/<int:channel_id>/messages")
@login_required
async def channel_messages(channel_id):
    user = current_user()
    assert user is not None
    membership, roles_by_guild = await resolve_membership(user["id"])
    if not await _channel_messages_visible(channel_id, membership, roles_by_guild):
        return jsonify({"error": "not_found_or_forbidden"}), 404
    before = request.args.get("before", type=int)
    limit = request.args.get("limit", default=50, type=int)
    messages = await data.get_channel_messages(channel_id, before=before, limit=limit)
    return jsonify({"messages": messages})


@api_bp.route("/attachments/<int:attachment_id>")
@login_required
async def attachment_bytes(attachment_id):
    user = current_user()
    assert user is not None
    att = await data.get_attachment_with_channel(attachment_id)
    if att is None or att["download_status"] != 1 or not att["local_path"]:
        return jsonify({"error": "not_found"}), 404
    membership, roles_by_guild = await resolve_membership(user["id"])
    if not await _channel_messages_visible(att["channel_id"], membership, roles_by_guild):
        return jsonify({"error": "forbidden"}), 403
    from bot.core.message_media import media_root
    path = (media_root() / att["local_path"]).resolve()
    root = media_root().resolve()
    if root not in path.parents:            # defense in depth vs path traversal
        return jsonify({"error": "not_found"}), 404
    content_type = att["content_type"] or "application/octet-stream"
    # Only allowlisted raster types render inline; everything else is a download,
    # so force a Content-Disposition: attachment with the original filename
    # (otherwise the browser names the saved file after the /api/attachments/<id>
    # URL). The type here is Discord's claim about the file, not ours, so a bare
    # startswith("image/") would let through image/svg+xml — a scriptable
    # document, and stored XSS when served inline from our own origin. Reuse the
    # upload allowlist so there is one definition of "safe to render".
    from bot.core.media_store import ALLOWED_IMAGE_TYPES
    is_image = content_type in ALLOWED_IMAGE_TYPES
    resp = await send_file(path,
                           mimetype=content_type if is_image else "application/octet-stream",
                           as_attachment=not is_image,
                           attachment_filename=att["filename"])
    # These are arbitrary user-uploaded bytes served from the dashboard's own
    # origin, and the content type is Discord's claim about them, not ours:
    # without nosniff a file uploaded as image/* but sniffed as HTML executes
    # as script here. The media routes already set it; this one lagged.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


async def _visible_media(media_id, media=None):
    """(media, err) — the media -> hunt -> visibility chain shared by
    media_bytes, media_tile, and media_markup below.

    `err` is a ready-to-return (jsonify(...), status) tuple, or None. Models
    actions.py's `_media_ctx` (the good version of this chain), but keeps
    THIS file's own exact status codes/error keys, which the three callers
    already used identically ("not_found" / 404 for a missing media row or
    hunt, "not_found_or_forbidden" / the visibility status otherwise).

    `media` lets a caller that already fetched the row (media_tile, which
    gates on tile_status before checking visibility at all) pass it in rather
    than triggering a second lookup.
    """
    from bot.store import HuntJsonDb, MediaJsonDb
    if media is None:
        media = MediaJsonDb.get_by_id(media_id)
    if media is None:
        return None, (jsonify({"error": "not_found"}), 404)
    hunt_row = HuntJsonDb.get_by_attr(id=media.hunt_id)
    if hunt_row is None:
        return None, (jsonify({"error": "not_found"}), 404)
    hunt, guild_or_status = await _load_visible_hunt(hunt_row.uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return None, (jsonify({"error": "not_found_or_forbidden"}), guild_or_status)
    return media, None


@api_bp.route("/media/<int:media_id>")
@login_required
async def media_bytes(media_id):
    """Serve a stored image. Visibility is the owning hunt's.

    Deliberately authenticated: nothing here is reachable without a Discord
    session, which is why evidence images cannot be embedded in Google Sheets
    with =IMAGE() (Google fetches those unauthenticated). Creating an
    unauthenticated URL to satisfy Sheets would expose team working to anyone
    holding the link.
    """
    media, err = await _visible_media(media_id)
    if err:
        return err
    assert media is not None  # the (media, None) branch
    from bot.core.message_media import media_root
    path = (media_root() / media.path).resolve()
    root = media_root().resolve()
    if root not in path.parents:            # defense in depth vs path traversal
        return jsonify({"error": "not_found"}), 404
    if not path.is_file():
        return jsonify({"error": "not_found"}), 404
    resp = await send_file(path, mimetype=media.content_type or "application/octet-stream",
                           as_attachment=False,
                           attachment_filename=media.filename)
    # sniff_image_type only validates a fixed-length PREFIX, so a stored file can
    # be a byte-perfect PNG header with an arbitrary payload appended. That is
    # harmless while the browser treats it as the type we stored, and dangerous
    # the moment the browser is allowed to MIME-sniff the body into an
    # executable context — which is exactly what nosniff forbids. The stored
    # content_type above is used verbatim and never re-derived from the
    # extension or from anything the client sent.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@api_bp.route("/media/<int:media_id>/tiles/<int:z>/<int:y>/<int:x>")
@login_required
async def media_tile(media_id, z, y, x):
    """Serve one pyramid tile. Authorised exactly as /api/media/<id> is.

    z/y/x is libvips' "google" layout order — row before column. The <int:>
    converters reject anything that is not a non-negative integer, which is what
    makes building the path by interpolation safe; the resolve()-against-root
    check below is the same defence in depth the sibling routes carry.
    """
    from bot.store import MediaJsonDb
    media = MediaJsonDb.get_by_id(media_id)
    # tile_status gates this: an untiled row must not serve a pyramid left over
    # from anything else, and a pending one has no complete pyramid yet. Checked
    # before visibility, same precedence as before the extraction: an untiled
    # row 404s regardless of who's asking.
    if media is None or media.tile_status != "ready":
        return jsonify({"error": "not_found"}), 404
    media, err = await _visible_media(media_id, media=media)
    if err:
        return err
    assert media is not None  # the (media, None) branch
    if z >= media.tile_levels:
        return jsonify({"error": "not_found"}), 404
    from bot.core.image_tiler import blank_tile_bytes, tile_relpath
    from bot.core.message_media import media_root
    root = media_root().resolve()
    path = (media_root() / tile_relpath(media.hunt_id, media.id, z, y, x)).resolve()
    if root not in path.parents:            # defense in depth vs path traversal
        return jsonify({"error": "not_found"}), 404
    if not path.is_file():
        # dzsave omits any tile of a uniform colour. That is a legitimate hole in
        # the grid rather than an error — a mostly-white puzzle sheet has many —
        # but dzsave's OWN blank.png for this is a degenerate near-black
        # placeholder that does not reflect the tile's real colour (renders as
        # solid black in the viewer). Serve a real white tile instead.
        resp = await make_response(blank_tile_bytes())
        resp.headers["Content-Type"] = "image/png"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, max-age=86400, immutable"
        return resp
    resp = await send_file(path, mimetype="image/webp", as_attachment=False)
    # Same reasoning as media_bytes: the type is ours, and the browser is never
    # allowed to sniff the body into an executable context.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # Tiles are immutable for a given media id, so they cache hard. This is what
    # makes panning a 940-tile sheet feel instant on a second pass.
    resp.headers["Cache-Control"] = "private, max-age=86400, immutable"
    return resp


@api_bp.route("/media/<int:media_id>/markup")
@login_required
async def media_markup(media_id):
    """Every mark on one image. Authorised exactly as /api/media/<id> is."""
    from bot.core import markup as markup_service
    from bot.web import data as web_data
    _media, err = await _visible_media(media_id)
    if err:
        return err
    return jsonify([web_data.mark_to_json(m)
                    for m in markup_service.list_marks(media_id)])


@api_bp.route("/hunts/<uid>/media", methods=["POST"])
@login_required
async def upload_media(uid):
    """Store an uploaded image against a hunt and return its id and URL.

    The client-declared Content-Type is not trusted; save_image_bytes sniffs
    the magic bytes and rejects anything that is not PNG/JPEG/GIF/WebP. SVG is
    refused outright — it is a scriptable document, and serving one from our
    own origin would be stored XSS.
    """
    import functools
    from bot.core.media_ingest import ingest_image_bytes
    from bot.core.media_store import MediaRejected, max_upload_bytes
    from bot.core.media_tiling import schedule_tiling
    from bot.store import MediaJsonDb
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "hunt not found"}), guild_or_status
    assert isinstance(guild_or_status, dict)  # the (hunt, guild_info) branch
    if hunt["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    files = await request.files
    upload = files.get("file")
    if upload is None:
        return jsonify({"error": "file required"}), 400
    # Read one byte past the cap so an oversize body is detectable rather than
    # silently truncated to the limit (which would make an oversize upload
    # indistinguishable from one landing exactly on the boundary).
    raw = upload.read(max_upload_bytes() + 1)
    try:
        media = ingest_image_bytes(
            hunt_id=hunt["id"],
            filename=upload.filename or "image",
            raw=raw,
            origin="upload",
            db=MediaJsonDb,
        )
    except MediaRejected as exc:
        return jsonify({"error": str(exc)}), 400
    if media.tile_status == "pending":
        from bot.web.actions import _notify_board_soon
        guild_snowflake = guild_or_status.get("guild_snowflake")
        schedule_tiling(media, MediaJsonDb,
                        after=functools.partial(_notify_board_soon, hunt["uid"], guild_snowflake))
    return jsonify({
        "id": media.id, "url": f"/api/media/{media.id}",
        "width": media.width, "height": media.height,
        "tile_status": media.tile_status,
    }), 201


@api_bp.route("/hunts/<uid>/evidence/ocr-preview", methods=["POST"])
@login_required
async def ocr_evidence_preview(uid):
    """Extract text from an image about to be logged as evidence.

    Uses validate_image_bytes -- the same seam upload_media relies on -- but
    never writes to disk or creates a `media` row: this is a preview, called
    before "Log clue" is ever clicked. The real upload still only happens
    through upload_media, unchanged.
    """
    from bot.core.image_tiler import exceeds_pixel_limit, probe_dimensions_bytes
    from bot.core.media_store import MediaRejected, max_upload_bytes, validate_image_bytes
    from bot.core.media_tiling import run_bounded_image_work
    from bot.core.ocr import extract_text
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "hunt not found"}), guild_or_status
    if hunt["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    files = await request.files
    upload = files.get("file")
    if upload is None:
        return jsonify({"error": "file required"}), 400
    raw = upload.read(max_upload_bytes() + 1)
    try:
        validate_image_bytes(raw)
    except MediaRejected as exc:
        return jsonify({"error": str(exc)}), 400
    # The byte cap is not a pixel cap: PNG compresses a flat raster enormously,
    # so a ~1MB upload can decode to 1.6Gpx. Probe the header before anything
    # touches the pixels (C2).
    try:
        width, height = probe_dimensions_bytes(raw)
    except Exception:
        logger.exception("could not read dimensions for an OCR preview upload")
        return jsonify({"error": "Not a supported image."}), 400
    if exceeds_pixel_limit(width, height):
        return jsonify({"error": "Image too large (too many pixels)."}), 400

    try:
        # Under the shared image-work bound, not the default executor: a full
        # decode plus a tesseract run is exactly what MAX_CONCURRENT_TILING
        # exists to cap, and this route is member-tier (M10).
        text = await run_bounded_image_work(extract_text, raw)
    except Exception:
        logger.exception("OCR failed for an uploaded evidence preview")
        return jsonify({"error": "ocr_failed"}), 500
    return jsonify({"text": text})


@api_bp.route("/hunts/<uid>/canvases")
@login_required
async def hunt_canvases(uid):
    """A hunt's canvases, regions inline.

    One query per canvas for its regions rather than a join: a canvas has tens of
    regions, a hunt has a handful of canvases, and the flat shape the viewer wants
    would need regrouping anyway.
    """
    from bot.store import CanvasJsonDb, CanvasRegionJsonDb, MediaJsonDb
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "not_found_or_forbidden"}), guild_or_status
    canvases = []
    for row in CanvasJsonDb.get_by_hunt(hunt["id"]):
        canvases.append(data.canvas_to_json(
            row,
            CanvasRegionJsonDb.get_by_canvas(row.id),
            MediaJsonDb.get_by_id(row.media_id),
        ))
    return jsonify({"canvases": canvases})


@api_bp.route("/hunts/<uid>/puzzles")
@login_required
async def hunt_puzzles(uid):
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "not_found_or_forbidden"}), guild_or_status
    assert isinstance(guild_or_status, dict)  # the (hunt, guild_info) branch
    puzzles = await data.get_hunt_puzzles(hunt, guild_or_status.get("guild_snowflake"))
    return jsonify({"puzzles": puzzles})


@api_bp.route("/rollups/<int:tag_group_id>/answers")
@login_required
async def rollup_answers(tag_group_id):
    """Read-only aggregated answers for a meta tag_group.

    Returns {mode, answers: [{name, solution}, ...]}. No writes — the endpoint
    never creates a rollup row or triggers a sheet refresh."""
    user = current_user()
    assert user is not None  # @login_required
    hunt = await data.get_hunt_for_tag_group(tag_group_id)
    if not hunt:
        return jsonify({"error": "round_not_found"}), 404
    membership, roles_by_guild = await resolve_membership(user["id"])
    guilds_map = await data.get_guilds_map()
    if not hunt_visible(hunt, membership, roles_by_guild, guilds_map):
        return jsonify({"error": "forbidden"}), 403

    from bot.store import TagGroupJsonDb, RollupsJsonDb
    from bot.store.rollup_data import RollupData

    target_group = TagGroupJsonDb.get_by_attr(id=tag_group_id)
    if target_group is None:
        return jsonify({"error": "round_not_found"}), 404

    rollup = RollupsJsonDb.get_by_attr(tag_group_id=tag_group_id)
    if rollup is None:
        # In-memory default — never persist.
        rollup = RollupData(tag_group_id=tag_group_id)

    cog = puzzles_cog()
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503

    members = await cog.service.select_rollup_members(target_group, rollup)
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
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "hunt not found"}), guild_or_status
    items = data.get_evidence(hunt["id"], source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
    return jsonify(items)


@api_bp.route("/evidence", methods=["POST"])
@login_required
async def add_evidence():
    from quart import request
    from bot.store import HuntJsonDb, PuzzleJsonDb
    body = await request.get_json()
    hunt_uid = body.get("hunt_uid")
    source_puzzle_id = body.get("source_puzzle_id")
    source_sheet_id = body.get("source_sheet_id")
    media_id = body.get("media_id")
    notes = body.get("notes", [])
    if not hunt_uid:
        return jsonify({"error": "hunt_uid required"}), 400
    if not all(isinstance(n, str) for n in notes):
        return jsonify({"error": "notes must be strings"}), 400
    clean_notes = [n for n in notes if n.strip()]
    # An item with neither text nor an image is nothing at all. Either alone is
    # fine: a transcription with no picture, or a picture with no transcription.
    if not clean_notes and not media_id:
        return jsonify({"error": "a note or an image is required"}), 400
    hunt, guild_or_status = await _load_visible_hunt(hunt_uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "hunt not found"}), guild_or_status
    if hunt["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    # At most one source. Both set is a client bug -- silently preferring one
    # would attribute the evidence to something the user did not pick.
    if source_puzzle_id and source_sheet_id:
        return jsonify({"error": "only one source may be given"}), 400
    # source_puzzle_id is optional: evidence can belong to the hunt rather
    # than to any one puzzle.
    puzzle = None
    if source_puzzle_id:
        puzzle = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
        if puzzle is None or puzzle.hunt_id != hunt["id"]:
            return jsonify({"error": "puzzle not found in hunt"}), 404
    if source_sheet_id:
        # The id comes from the client; confirm it is one of THIS hunt's sheets
        # before attributing anything to it.
        from bot.store import SheetsJsonDb
        try:
            source_sheet_id = int(source_sheet_id)
        except (TypeError, ValueError):
            # A client-supplied id: a bad one is a 400, not an uncaught
            # ValueError surfacing as a 500 server fault.
            return jsonify({"error": "source_sheet_id must be a number"}), 400
        if not any(s.id == source_sheet_id
                   for s in SheetsJsonDb.get_by_hunt(hunt["id"])):
            return jsonify({"error": "sheet not found in hunt"}), 404
    # The id comes from the client; without this an item could cite another
    # hunt's private upload. Same invariant update_evidence already holds.
    if media_id is not None:
        # Imported locally to avoid a circular import between the api and
        # actions blueprints (same reason _audit is imported locally below).
        from bot.web.actions import _require_hunt_media
        _media, err = _require_hunt_media(media_id, hunt["id"])
        if err:
            return err
    cog = puzzles_cog()
    hunt_obj = HuntJsonDb.get_by_attr(id=hunt["id"])
    if cog is None or hunt_obj is None:
        return jsonify({"error": "bot_unavailable"}), 503
    # Route through the same service call the bang !clue/!clues commands use,
    # so API-submitted evidence gets auto-routed to a sole informs target and
    # its contribution rollup, instead of just landing in the Evidence tab.
    # An image with no transcription still needs one row, so fall back to a
    # single empty note rather than logging nothing.
    items = await cog.service.log_clues(hunt_obj, puzzle, clean_notes or [""],
                                        media_id=media_id,
                                        source_sheet_id=source_sheet_id)
    if items:
        asyncio.create_task(cog.service.refresh_evidence_safe(hunt_obj))
        if puzzle is not None:
            asyncio.create_task(cog.service.refresh_informs_targets(hunt_obj, puzzle))
        # This route replaces the actions blueprint's /clues endpoint as the
        # dashboard's clue-logging path (it is the only one that can log a
        # sourceless or image-only clue, since there is no puzzle id for a
        # URL). Carry over its audit entry so moving the panel here does not
        # silently drop the audit trail. Imported locally to avoid a circular
        # import between the api and actions blueprints.
        from bot.web.actions import _audit
        await _audit("clue", puzzle_id=puzzle.id if puzzle else None,
                     hunt_uid=hunt["uid"])
    return jsonify([{"id": i.id, "note": i.note} for i in items]), 201


async def _activity_event_visible(event, user_id, guild_id):
    """True if the user can actually see the channel an "activity" event came from.

    Guild membership alone isn't enough: one guild can host several teams whose
    channels are gated by a per-hunt Discord role overwrite (see hunt_visible),
    not by guild membership. Checking the live permission overwrites for the
    specific channel matches that same per-hunt gate without needing a DB lookup
    per message.
    """
    bot = bot_app().bot
    guild = bot.get_guild(guild_id)
    if guild is None:
        return False
    channel = guild.get_channel(int(event["channel_id"]))
    if channel is None:
        return False  # deleted/uncached channel: fail closed, a stale line is harmless to drop
    member = await get_or_fetch_member(guild, user_id)
    if member is None:
        return False  # not a member of this guild: can't view the channel
    return channel.permissions_for(member).view_channel


async def _board_event_visible(event, membership, roles_by_guild):
    """True if the user's team role grants them the hunt a "board" event names."""
    hunt = await data.get_hunt_by_uid(event["uid"])
    if hunt is None:
        return False
    guilds_map = await data.get_guilds_map()
    return hunt_visible(hunt, membership, roles_by_guild, guilds_map)


async def _channel_messages_visible(channel_id, membership, roles_by_guild):
    """Authorize a transcript request via the owning hunt (works for archived,
    channel-deleted hunts, unlike the live channel-permission check)."""
    hunt = await data.get_hunt_by_channel_id(channel_id)
    if hunt is None:
        return False
    guilds_map = await data.get_guilds_map()
    return hunt_visible(hunt, membership, roles_by_guild, guilds_map)


def _dashboard_origin():
    """The one browser origin the dashboard is served from, derived from the
    configured OAuth redirect uri (which must already match the public URL the
    browser uses, or the OAuth round-trip itself would fail). Returns None when
    it isn't configured, in which case the Origin check stands down rather than
    locking a misconfigured deployment out of its own dashboard."""
    from urllib.parse import urlsplit
    uri = getattr(config, "web_oauth_redirect_uri", "") or ""
    parts = urlsplit(uri)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _ws_origin_allowed(origin):
    """Same-origin check for the websocket handshake.

    Browsers do not apply the same-origin policy to websockets and Quart's
    before_request CSRF guard does not run for them, so any site could open this
    socket with the victim's cookies attached. Origin is the only handshake
    header that identifies the calling site; a browser always sends it, so a
    missing one is rejected too.
    """
    expected = _dashboard_origin()
    if expected is None:
        return True
    return origin == expected


@api_bp.websocket("/ws/activity")
async def ws_activity():
    """Push live channel-activity events for guilds the user belongs to."""
    if not _ws_origin_allowed(websocket.headers.get("Origin")):
        logger.warning("rejected activity websocket from origin %r",
                       websocket.headers.get("Origin"))
        await websocket.close(1008)  # policy violation / cross-site handshake
        return
    user = session.get("user")
    if not user:
        await websocket.close(1008)  # policy violation / unauthenticated
        return
    membership, roles_by_guild = await resolve_membership(user["id"])
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
                membership, roles_by_guild = await resolve_membership(user["id"])
                last_refresh = now
                if not membership:
                    await websocket.close(1008)  # no longer a member of any guild
                    return

            if event is None or event.get("guild_id") not in membership:
                continue

            # Guild membership only proves the user belongs to the guild; per-hunt
            # team-role visibility (hunt_visible) is a finer-grained gate that
            # membership alone does not enforce — check it per event type.
            # Default-deny: an unrecognised event type (e.g. a future type that may
            # carry hidden-hunt data) is dropped rather than sent on guild
            # membership alone. The directly-sent "ready" event bypasses this loop.
            visible = False
            if event.get("type") == "activity":
                visible = await _activity_event_visible(event, user["id"], event["guild_id"])
            elif event.get("type") == "board":
                visible = await _board_event_visible(event, membership, roles_by_guild)
            elif event.get("type") in ("message", "message_edit", "message_delete"):
                # Live channels only (archived channels emit no events); the live
                # channel-permission check matches the same per-hunt role gate.
                visible = await _activity_event_visible(event, user["id"], event["guild_id"])

            if visible:
                await websocket.send_json(event)
    except asyncio.CancelledError:
        raise
    finally:
        events.unsubscribe(queue)
