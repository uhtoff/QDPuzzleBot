"""Discord OAuth login and session/visibility helpers.

OAuth uses only the ``identify`` scope: it tells us *who* the user is. Guild
membership and team roles are read from the live bot (``guild.get_member``),
which is more reliable than Discord's REST member endpoint and needs no extra
scope. See the ``site-hunt-visibility`` project note for the visibility model.
"""
import functools
import logging
import secrets
from urllib.parse import urlencode

import aiohttp
from quart import Blueprint, current_app, jsonify, redirect, request, session

from bot.utils import config

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

DISCORD_API = "https://discord.com/api"
AUTHORIZE_URL = f"{DISCORD_API}/oauth2/authorize"
TOKEN_URL = f"{DISCORD_API}/oauth2/token"
ME_URL = f"{DISCORD_API}/users/@me"
OAUTH_SCOPE = "identify"


@auth_bp.route("/login")
async def login():
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
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
    return redirect(config.web_frontend_url or "/")


@auth_bp.route("/logout", methods=["POST"])
async def logout():
    session.clear()
    return jsonify({"ok": True})


@auth_bp.route("/me")
async def me():
    user = session.get("user")
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": user})


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


def login_required(view):
    @functools.wraps(view)
    async def wrapper(*args, **kwargs):
        if not session.get("user"):
            return jsonify({"error": "auth_required"}), 401
        return await view(*args, **kwargs)
    return wrapper


async def resolve_membership(user_id):
    """Return (membership, roles_by_guild) for a Discord user from the live bot.

    ``membership`` is a set of guild snowflakes the user belongs to; ``roles_by_guild``
    maps each of those snowflakes to the set of the user's role ids there.
    """
    bot = current_app.bot
    membership = set()
    roles_by_guild = {}
    for guild in bot.guilds:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None
        if member is not None:
            membership.add(guild.id)
            roles_by_guild[guild.id] = {role.id for role in member.roles}
    return membership, roles_by_guild


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
