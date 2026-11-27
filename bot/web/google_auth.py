"""Optional Google OAuth 2.0 / OIDC account linking routes.

Requests only the ``openid email`` scopes to verify that the signed-in Discord
user owns the Google account. The Google access token is used once to fetch
``/userinfo`` and immediately discarded — Puzzbot never requests Google Drive
scopes on the user's account and stores no Google OAuth tokens.
"""
import base64
import hashlib
import logging
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
from quart import Blueprint, jsonify, redirect, request, session

from bot.core import drive_sharing
from bot.store import GoogleLinkDb
from bot.utils import config

from .auth import current_user, login_required, safe_next_path
from .typed_app import bot_app

logger = logging.getLogger(__name__)

google_auth_bp = Blueprint("google_auth", __name__, url_prefix="/api/auth/google")

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_OAUTH_SCOPE = "openid email"
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _redirect_target(nxt: str | None, status: str) -> str:
    """Build the post-callback SPA URL with ``google_link=<status>`` attached,
    preserving any existing query parameters on *nxt*."""
    base = (config.web_frontend_url or "").rstrip("/")
    raw = f"{base}{nxt}" if nxt else (config.web_frontend_url or "/")
    parts = urlsplit(raw)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "google_link"]
    query.append(("google_link", status))
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query), parts.fragment))


async def _exchange_code(code: str, code_verifier: str) -> dict:
    data = {
        "client_id": config.web_google_oauth_client_id,
        "client_secret": config.web_google_oauth_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": config.web_google_oauth_redirect_uri,
    }
    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as http:
        async with http.post(GOOGLE_TOKEN_URL, data=data) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _fetch_userinfo(access_token: str) -> dict:
    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as http:
        async with http.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


@google_auth_bp.route("/link")
@login_required
async def link_google():
    if not config.web_google_oauth_enabled:
        return jsonify({"error": "google_oauth_not_configured"}), 503

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    session["google_oauth_state"] = state
    session["google_oauth_verifier"] = verifier

    nxt = safe_next_path(request.args.get("next"))
    if nxt:
        session["google_link_next"] = nxt
    else:
        session.pop("google_link_next", None)

    params = {
        "response_type": "code",
        "client_id": config.web_google_oauth_client_id,
        "redirect_uri": config.web_google_oauth_redirect_uri,
        "scope": GOOGLE_OAUTH_SCOPE,
        "state": state,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return redirect(f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}")


@google_auth_bp.route("/callback")
async def callback_google():
    if not config.web_google_oauth_enabled:
        return jsonify({"error": "google_oauth_not_configured"}), 503

    nxt = safe_next_path(session.pop("google_link_next", None))
    expected_state = session.pop("google_oauth_state", None)
    verifier = session.pop("google_oauth_verifier", None)

    user = current_user()
    if user is None:
        return redirect(_redirect_target(nxt, "auth_required"))

    if request.args.get("error"):
        return redirect(_redirect_target(nxt, "cancelled"))

    code = request.args.get("code")
    state = request.args.get("state")
    if (
        not code
        or not state
        or not isinstance(expected_state, str)
        or not isinstance(verifier, str)
        or not secrets.compare_digest(state, expected_state)
    ):
        return jsonify({"error": "invalid_oauth_state"}), 400

    try:
        token = await _exchange_code(code, verifier)
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("missing access_token in Google token response")
        info = await _fetch_userinfo(access_token)
    except Exception:
        logger.exception("Google OAuth exchange failed for user %s", user["id"])
        return redirect(_redirect_target(nxt, "error"))

    sub = info.get("sub")
    email = info.get("email")
    email_verified = info.get("email_verified")
    if (
        email_verified is not True
        or not isinstance(sub, str)
        or not sub.strip()
        or not isinstance(email, str)
        or not email.strip()
    ):
        logger.warning("Google userinfo rejected for user %s (email_verified=%r)", user["id"], email_verified)
        return redirect(_redirect_target(nxt, "error"))

    norm_email = email.strip().lower()
    user_id = int(user["id"])
    prev = GoogleLinkDb.get(user_id)
    GoogleLinkDb.upsert(user_id, sub.strip(), norm_email)
    drive_sharing.clear_user_cooldowns(user_id)

    bot = bot_app().bot
    if prev is not None and str(prev["google_email"]) != norm_email:
        drive_sharing.schedule_revoke_user_active_shares(
            bot, user_id, email=str(prev["google_email"])
        )
    drive_sharing.schedule_preshare_user_active_hunts(bot, user_id)

    return redirect(_redirect_target(nxt, "linked"))


@google_auth_bp.route("", methods=["DELETE"])
@login_required
async def unlink_google():
    user = current_user()
    assert user is not None  # @login_required
    user_id = int(user["id"])
    prev = GoogleLinkDb.unlink(user_id)
    drive_sharing.clear_user_cooldowns(user_id)
    if prev is not None:
        drive_sharing.schedule_revoke_user_active_shares(bot_app().bot, user_id)
    return jsonify({"ok": True, "unlinked": prev is not None})
