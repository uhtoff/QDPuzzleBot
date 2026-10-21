"""The SPA fallback serves index.html only for the dashboard's own client routes.

It used to answer EVERY non-/api path with index.html and a 200, so scanner
probes (/wp-login.php, /wp-admin/, /.env, /xmlrpc.php …) all got a page back —
which is part of why the site was being flagged for phishing. Now: real built
files are served, the known client routes get index.html, and everything else —
unknown paths, dotfiles, anything with a file extension — is a 404.
"""
from unittest.mock import MagicMock, patch

import pytest

INDEX_MARKER = "<!-- puzzbot spa index -->"


def _app(dist):
    from bot.web import create_app
    bot = MagicMock()
    bot.get_cog.return_value = None          # skip the WebNotifier sink wiring
    with patch("bot.web.config") as cfg:
        cfg.web_session_secret = "x" * 40
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        cfg.web_attachment_max_bytes = 1024
        cfg.web_frontend_dist = str(dist)
        return create_app(bot)


@pytest.fixture
def client(tmp_path):
    (tmp_path / "index.html").write_text(f"<html>{INDEX_MARKER}</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log('app')")
    (tmp_path / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    (tmp_path / ".secret").write_text("never serve me")   # a dotfile that exists on disk
    return _app(tmp_path).test_client()


@pytest.mark.parametrize("path", [
    "/",
    "/hunts/ABC123",
    "/hunts/ABC123/",
    "/hunts/a1b2c3",
    "/guilds/123456789012345678/settings",
    "/guilds/123456789012345678/settings/",
])
async def test_known_client_routes_get_the_spa(client, path):
    resp = await client.get(path)
    assert resp.status_code == 200
    assert INDEX_MARKER in await resp.get_data(as_text=True)
    assert resp.headers["Cache-Control"] == "no-cache"


@pytest.mark.parametrize("path,body", [
    ("/assets/app-abc123.js", "console.log('app')"),
    ("/favicon.ico", None),
])
async def test_real_built_files_are_served(client, path, body):
    resp = await client.get(path)
    assert resp.status_code == 200
    data = await resp.get_data(as_text=body is not None)
    if body is not None:
        assert data == body
    assert INDEX_MARKER not in str(data)


@pytest.mark.parametrize("path", [
    # the scanner probes that prompted this
    "/wp-login.php",
    "/wp-admin/",
    "/wp-admin/setup-config.php",
    "/xmlrpc.php",
    "/.env",
    "/.git/config",
    "/.secret",                       # dotfile even though it exists on disk
    "/.well-known/security.txt",
    # anything with a file extension that isn't a real file
    "/assets/missing-old-hash.js",
    "/hunts/ABC123.php",
    "/index.php",
    # unknown paths, and near-misses of the real client routes
    "/login",
    "/admin",
    "/hunts",
    "/hunts/",
    "/hunts/ABC123/extra",
    "/guilds/123/",
    "/guilds/not-a-number/settings",
    "/guilds/123/settings/extra",
    # the API keeps its own 404
    "/api/does-not-exist",
])
async def test_everything_else_is_a_404(client, path):
    resp = await client.get(path)
    assert resp.status_code == 404
    assert INDEX_MARKER not in await resp.get_data(as_text=True)
