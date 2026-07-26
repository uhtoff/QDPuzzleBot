"""Embedded web dashboard for the puzzle bot.

The dashboard runs inside the bot's asyncio loop so it can read the live
discord.py client (membership, roles, channels) and share the database. It is
entirely optional: unless ``web.enabled`` is true in config.json, ``start_web``
does nothing and the bot behaves exactly as before.
"""
import asyncio
import logging
import os
import secrets
from pathlib import Path

from bot.utils import config

logger = logging.getLogger(__name__)

_CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _csrf_required(method):
    """True for state-changing HTTP methods (request.method is always upper-case)."""
    return method in _CSRF_METHODS


def _csrf_ok(header_token, cookie_token):
    """Double-submit check: both present and equal (constant-time)."""
    if not header_token or not cookie_token:
        return False
    return secrets.compare_digest(header_token, cookie_token)


def _valid_session_secret(secret):
    """A usable Quart session secret: present, >= 32 chars, not a known placeholder."""
    if not secret or len(secret) < 32:
        return False
    return secret.lower() not in {"changeme", "insecure", "secret", "password"}


def _max_body_bytes(attachment_cap: int) -> int:
    """Quart's own body-size guard, in bytes.

    It must sit ABOVE config.web_attachment_max_bytes, not below it: this check
    runs before any route, so a lower value makes the configured attachment cap
    unreachable and turns every large upload into a bare 413 instead of the
    JSON error the API is documented to return. The extra megabyte covers
    multipart boundary and header overhead around the file itself.
    """
    return attachment_cap + 1024 * 1024


def _dist_dir():
    """Resolve the built SPA directory (config override or default frontend/dist)."""
    if config.web_frontend_dist:
        return Path(config.web_frontend_dist)
    return Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def _register_spa(app):
    """Serve the built SPA so the whole dashboard is a single upstream.

    Static assets are served from disk; any non-/api path falls back to
    index.html so client-side routes (e.g. /hunts/ABC123) work on refresh.
    """
    from quart import abort, send_from_directory

    dist_dir = _dist_dir()
    if not (dist_dir / "index.html").is_file():
        logger.warning("SPA build not found at %s; serving API only. Run `npm run build`.", dist_dir)
        return
    dist_dir = str(dist_dir)

    async def _send_index():
        # index.html must always be revalidated so a rebuild (with new hashed
        # asset names) is picked up immediately. The hashed assets themselves are
        # safe to cache since their filenames change every build.
        resp = await send_from_directory(dist_dir, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/")
    async def spa_index():
        return await _send_index()

    @app.route("/<path:path>")
    async def spa_catchall(path):
        if path.startswith("api/"):
            abort(404)
        if os.path.isfile(os.path.join(dist_dir, path)):
            return await send_from_directory(dist_dir, path)
        return await _send_index()


def create_app(bot):
    """Build the Quart app. Imported lazily so the bot does not hard-depend on Quart."""
    from quart import jsonify, request

    from .typed_app import BotQuart
    from .auth import auth_bp
    from .api import api_bp
    from .actions import actions_bp

    app = BotQuart(__name__)
    app.secret_key = config.web_session_secret
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.web_oauth_redirect_uri.startswith("https://"),
        MAX_CONTENT_LENGTH=_max_body_bytes(config.web_attachment_max_bytes),
    )
    # Make the live bot reachable from request handlers.
    app.bot = bot

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(actions_bp)
    _register_spa(app)

    # Phase B: register WebNotifier as a board-push sink on the service's composite.
    from bot.web.notifier import WebNotifier
    cog = bot.get_cog("Puzzles")
    if cog is not None:
        cog.service.notifier.add_sink(WebNotifier())

    @app.after_request
    async def _set_csrf_cookie(resp):
        if not request.cookies.get("csrf_token"):
            resp.set_cookie(
                "csrf_token",
                secrets.token_urlsafe(32),
                samesite="Lax",
                secure=config.web_oauth_redirect_uri.startswith("https://"),
                httponly=False,
            )
        return resp

    @app.before_request
    async def _csrf_guard():
        if request.path.startswith("/api/") and _csrf_required(request.method):
            if not _csrf_ok(request.headers.get("X-CSRF-Token"), request.cookies.get("csrf_token")):
                return jsonify({"error": "csrf"}), 403

    return app


async def start_web(bot):
    """Start the web server as a background task on the bot's event loop."""
    if not config.web_enabled:
        logger.info("Web dashboard disabled (web.enabled is false); not starting.")
        return None
    if not _valid_session_secret(config.web_session_secret):
        logger.error(
            "Web dashboard enabled but web.session_secret is weak or missing; refusing to start."
        )
        return None
    try:
        app = create_app(bot)
    except Exception:
        logger.exception("Failed to build web app; dashboard will not start.")
        return None

    logger.info("Starting web dashboard on %s:%s", config.web_host, config.web_port)
    # Provide our own shutdown_trigger so hypercorn does NOT install signal
    # handlers. Otherwise it intercepts SIGTERM/SIGINT for a "graceful" web
    # shutdown and the bot process stops responding to plain `kill` — the
    # default (terminate on SIGTERM) is what we want. The event is kept on the
    # bot only to prevent garbage collection.
    bot._web_shutdown = asyncio.Event()

    async def _wait_shutdown() -> None:
        await bot._web_shutdown.wait()

    task = asyncio.create_task(
        app.run_task(
            host=config.web_host,
            port=config.web_port,
            shutdown_trigger=_wait_shutdown,
        ),
        name="web-dashboard",
    )
    task.add_done_callback(_log_web_task_failure)
    return task


def _log_web_task_failure(task: asyncio.Task) -> None:
    """Surface a crash in the background web-dashboard task.

    Without this, a hypercorn startup failure (e.g. port already bound) is
    silently swallowed — the bot keeps running with the dashboard down and
    nothing in the logs to say why."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Web dashboard task failed; dashboard is down.", exc_info=exc)
