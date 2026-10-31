"""Discord OAuth login and session/visibility helpers.

OAuth uses only the ``identify`` scope: it tells us *who* the user is. Guild
membership and team roles are read from the live bot (``guild.get_member``),
which is more reliable than Discord's REST member endpoint and needs no extra
scope. See the ``site-hunt-visibility`` project note for the visibility model.
"""
import functools
import logging
import secrets
import time
from urllib.parse import urlencode

import aiohttp
import discord
from quart import Blueprint, jsonify, redirect, request, session

from .typed_app import bot_app

from bot.utils import config
from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLE_NAMES, ORGANISER_ROLES  # noqa: F401  # ADMIN_ROLES re-exported via bot.web.auth
from bot.core import role_gate

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

DISCORD_API = "https://discord.com/api"
AUTHORIZE_URL = f"{DISCORD_API}/oauth2/authorize"
TOKEN_URL = f"{DISCORD_API}/oauth2/token"
ME_URL = f"{DISCORD_API}/users/@me"
OAUTH_SCOPE = "identify"


def safe_next_path(value):
    """*value* if it is a same-site relative path, else None. Guards the post-login
    redirect against open redirects: must start with a single "/", with no
    backslash, control character, or scheme."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return None
    if "\\" in value or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        return None
    return value


@auth_bp.route("/login")
async def login():
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    nxt = safe_next_path(request.args.get("next"))
    if nxt:
        session["login_next"] = nxt
    else:
        session.pop("login_next", None)
    params = {
        "response_type": "code",
        "client_id": config.web_oauth_client_id,
        "scope": OAUTH_SCOPE,
        "redirect_uri": config.web_oauth_redirect_uri,
        "state": state,
        "prompt": "none",
    }
    return redirect(f"{AUTHORIZE_URL}?{urlencode(params)}")


@auth_bp.route("/callback")
async def callback():
    if request.args.get("error"):
        return redirect(config.web_frontend_url or "/")
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state or state != session.pop("oauth_state", None):
        return jsonify({"error": "invalid_oauth_state"}), 400

    try:
        token = await _exchange_code(code)
        user = await _fetch_user(token["access_token"])
    except Exception:
        logger.exception("OAuth exchange failed")
        return jsonify({"error": "oauth_failed"}), 502

    session["user"] = {
        "id": int(user["id"]),
        "username": user.get("username"),
        "global_name": user.get("global_name"),
        "avatar": user.get("avatar"),
    }
    nxt = safe_next_path(session.pop("login_next", None))
    base = (config.web_frontend_url or "").rstrip("/")
    return redirect(f"{base}{nxt}" if nxt else (config.web_frontend_url or "/"))


@auth_bp.route("/logout", methods=["POST"])
async def logout():
    session.clear()
    return jsonify({"ok": True})


@auth_bp.route("/site")
async def site():
    """Public facts about this instance for the login page. Deliberately needs no
    session: it is what a signed-out visitor sees before choosing to log in."""
    return jsonify({
        "name": config.web_site_name,
        "test_instance": config.web_test_instance,
        "contact": config.web_contact,
    })


@auth_bp.route("/me")
async def me():
    user = session.get("user")
    if not user:
        return jsonify({"authenticated": False}), 401
    payload = {
        **user,
        "avatar_url": discord_avatar_url(user["id"], user.get("avatar")),
    }
    return jsonify({"authenticated": True, "user": payload})


async def _exchange_code(code):
    data = {
        "client_id": config.web_oauth_client_id,
        "client_secret": config.web_oauth_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.web_oauth_redirect_uri,
    }
    async with aiohttp.ClientSession() as http:
        async with http.post(TOKEN_URL, data=data) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _fetch_user(access_token):
    async with aiohttp.ClientSession() as http:
        async with http.get(ME_URL, headers={"Authorization": f"Bearer {access_token}"}) as resp:
            resp.raise_for_status()
            return await resp.json()


# --- Session / visibility helpers ------------------------------------------

def current_user():
    return session.get("user")


def discord_avatar_url(user_id, avatar_hash, size=64):
    """CDN URL for a user's Discord avatar, or None if they have no custom one.

    Animated avatars (hash prefixed 'a_') are served as .gif. The full integer
    user_id is used here on the server because a Discord snowflake exceeds JS's
    safe-integer range — the browser cannot rebuild this URL reliably.
    """
    if not avatar_hash:
        return None
    ext = "gif" if str(avatar_hash).startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size={size}"


def login_required(view):
    @functools.wraps(view)
    async def wrapper(*args, **kwargs):
        if not session.get("user"):
            return jsonify({"error": "auth_required"}), 401
        return await view(*args, **kwargs)
    return wrapper


# Negative-fetch cache: (user_id, guild_id) -> monotonic() timestamp of the last
# failed/absent fetch_member. Avoids re-issuing a failing REST round-trip for
# every request from a user in a guild they don't belong to (the dashboard
# polls hunt_detail every 30s per open tab).
_NOT_MEMBER_CACHE_TTL = 60
# Nothing evicts on read -- an expired entry is read past but left in place --
# so without a cap the dict grows for the life of the process, one key per
# (guild, user) pair ever probed. resolve_membership probes every guild the bot
# is in for every logged-in user, so that set is much larger than the membership
# it describes.
_NOT_MEMBER_CACHE_MAX = 10_000
_not_member_cache = {}


def _remember_not_member(key):
    """Record a negative fetch_member result, keeping the cache bounded.

    Python dicts preserve insertion order, so re-inserting the key puts it at
    the end and the oldest entries are the ones at the front. Expired entries go
    first (they are dead weight anyway); only if that frees nothing do we drop
    genuinely live-but-oldest ones.
    """
    now = time.monotonic()
    _not_member_cache.pop(key, None)          # re-insert at the end
    _not_member_cache[key] = now
    if len(_not_member_cache) <= _NOT_MEMBER_CACHE_MAX:
        return
    for stale in [k for k, at in _not_member_cache.items()
                  if now - at >= _NOT_MEMBER_CACHE_TTL]:
        del _not_member_cache[stale]
    while len(_not_member_cache) > _NOT_MEMBER_CACHE_MAX:
        del _not_member_cache[next(iter(_not_member_cache))]


async def _fetch_member_cached(guild, user_id):
    """guild.fetch_member with a short negative cache. Returns the member, or
    None if absent/unreachable. Logs (but doesn't distinguish) REST failures —
    they're treated the same as "not a member" for visibility purposes."""
    key = (guild.id, user_id)
    last_miss = _not_member_cache.get(key)
    if last_miss is not None and time.monotonic() - last_miss < _NOT_MEMBER_CACHE_TTL:
        return None
    try:
        member = await guild.fetch_member(user_id)
    except discord.NotFound:
        # 404 Unknown Member is the *expected* "user isn't in this guild" answer —
        # resolve_membership probes every guild the bot is in, so a logged-in user
        # legitimately 404s on all the ones they don't belong to. Log quietly and
        # without a traceback; reserve WARNING+exc_info for genuine REST failures.
        logger.debug(
            "user %s is not a member of guild %s (negative-cached for %ss)",
            user_id, guild.id, _NOT_MEMBER_CACHE_TTL,
        )
        _remember_not_member(key)
        return None
    except Exception:
        logger.warning(
            "fetch_member failed for user %s in guild %s (treating as not-a-member for %ss)",
            user_id, guild.id, _NOT_MEMBER_CACHE_TTL, exc_info=True,
        )
        _remember_not_member(key)
        return None
    if member is None:
        _remember_not_member(key)
    return member


async def get_or_fetch_member(guild, user_id):
    """Return the guild member for ``user_id``, or None if they're not in it.

    Prefers the local member cache. Under the members intent the bot chunks each
    guild at startup, so once ``guild.chunked`` is True the cache is complete and
    a ``get_member`` miss is authoritative — the user simply isn't a member, no
    REST call needed. Only during the brief pre-chunk window (or if chunking is
    unavailable) do we fall back to the negative-cached REST fetch. This avoids a
    404 round-trip per non-member per guild on every dashboard poll."""
    member = guild.get_member(user_id)
    if member is not None:
        return member
    if guild.chunked:
        return None
    return await _fetch_member_cached(guild, user_id)


async def resolve_membership(user_id):
    """Return (membership, roles_by_guild) for a Discord user from the live bot.

    ``membership`` is a set of guild snowflakes the user belongs to; ``roles_by_guild``
    maps each of those snowflakes to the set of the user's role ids there.
    """
    bot = bot_app().bot
    membership = set()
    roles_by_guild = {}
    for guild in bot.guilds:
        member = await get_or_fetch_member(guild, user_id)
        if member is not None:
            membership.add(guild.id)
            roles_by_guild[guild.id] = {role.id for role in member.roles}
    return membership, roles_by_guild


async def resolve_member_role_ids(user_id, guild_snowflake):
    """Return the set of the user's role IDS in a guild, or None if not a member.

    Ids, not names (M8): a role can be renamed, and any member with Manage Roles
    can mint one called `admin`. Which ids grant which tier is role_gate's call.
    """
    guild = guild_for(guild_snowflake)
    if guild is None:
        return None
    member = await get_or_fetch_member(guild, user_id)
    if member is None:
        return None
    return {role.id for role in member.roles}


def guild_for(guild_snowflake):
    """The live guild object for a snowflake, or None if the bot cannot see it."""
    return bot_app().bot.get_guild(guild_snowflake)


def organiser_tier_bypassed(guild_snowflake, roles):
    """True if `roles` is the Organiser tier (contains any of
    ORGANISER_ROLE_NAMES) and the guild has nothing standing for that tier —
    Organiser-tier checks then pass for every guild member. Always False for
    the Admin tier."""
    if not any(name in roles for name in ORGANISER_ROLE_NAMES):
        return False
    guild = guild_for(guild_snowflake)
    if guild is None:
        return False
    return not role_gate.organiser_gate_active(guild)


async def user_can_manage(user_id, guild_snowflake, roles=ORGANISER_ROLES):
    """True if the user holds an organiser/admin role in the guild, or the
    Organiser tier is bypassed for this guild (see organiser_tier_bypassed)."""
    if not guild_snowflake:
        return False
    role_ids = await resolve_member_role_ids(user_id, guild_snowflake)
    if role_ids is None:
        return False
    if organiser_tier_bypassed(guild_snowflake, roles):
        return True
    guild = guild_for(guild_snowflake)
    if guild is None:
        return False
    return role_gate.member_holds_tier(role_ids, guild, roles)


def hunt_visible(hunt, membership, roles_by_guild, guilds_map):
    """Apply the visibility model to one hunt row. Returns True/False.

    Active hunts require the team role (``role_id``); legacy hunts with role_id 0
    fall back to any member. Archived hunts require only guild membership.
    """
    guild = guilds_map.get(hunt["guild_id"])
    if not guild:
        return False
    snowflake = guild["guild_snowflake"]
    if snowflake not in membership:
        return False
    if hunt["archived"]:
        return True
    role_id = hunt.get("role_id") or 0
    if role_id == 0:
        return True  # legacy hunt, not yet backfilled with a team role
    return role_id in roles_by_guild.get(snowflake, set())
