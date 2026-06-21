"""Embedded web dashboard for the puzzle bot.

The dashboard runs inside the bot's asyncio loop so it can read the live
discord.py client (membership, roles, channels) and share the database. It is
entirely optional: unless ``web.enabled`` is true in config.json, ``start_web``
does nothing and the bot behaves exactly as before.
"""
import asyncio
import logging
import os
from pathlib import Path

from bot.utils import config

logger = logging.getLogger(__name__)


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
    from quart import Quart

    from .auth import auth_bp
    from .api import api_bp
    from .actions import actions_bp

    app = Quart(__name__)
    app.secret_key = config.web_session_secret
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.web_oauth_redirect_uri.startswith("https://"),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    )
    # Make the live bot reachable from request handlers.
    app.bot = bot

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(actions_bp)
    _register_spa(app)
    return app


async def start_web(bot):
    """Start the web server as a background task on the bot's event loop."""
    if not config.web_enabled:
        logger.info("Web dashboard disabled (web.enabled is false); not starting.")
        return None
    if not config.web_session_secret:
        logger.error("Web dashboard enabled but web.session_secret is empty; refusing to start.")
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
    task = asyncio.create_task(
        app.run_task(
            host=config.web_host,
            port=config.web_port,
            shutdown_trigger=bot._web_shutdown.wait,
        ),
        name="web-dashboard",
    )
    return task
