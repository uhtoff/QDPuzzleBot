"""The landing page (/) explains itself in the raw HTML, and is the only indexable page.

Google Safe Browsing flagged puzzbot.com as "Deceptive pages" (domain-wide, no sample
URLs) twice. What a crawler read at / was an empty <div id="root"> on a noindexed page
that leads to a Discord login; the explanation only appeared once JavaScript ran. Now /
carries that explanation in its markup and drops noindex; every other route keeps
noindex, since it sits behind login.
"""
from unittest.mock import MagicMock, patch

import pytest

INDEX = (
    "<!doctype html><html><head>"
    '<meta name="robots" content="noindex" />'
    "<title>t</title></head><body>"
    '<div id="root"></div><script type="module" src="/assets/app.js"></script>'
    "</body></html>"
)


@pytest.fixture
def make_client(tmp_path):
    (tmp_path / "index.html").write_text(INDEX)
    patchers = []

    def make(site_name="Puzzbot", test_instance=False, contact=""):
        from bot.web import create_app
        p = patch("bot.web.config")
        cfg = p.start()
        patchers.append(p)
        cfg.web_session_secret = "x" * 40
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        cfg.web_attachment_max_bytes = 1024
        cfg.web_frontend_dist = str(tmp_path)
        cfg.web_site_name = site_name
        cfg.web_test_instance = test_instance
        cfg.web_contact = contact
        bot = MagicMock()
        bot.get_cog.return_value = None
        return create_app(bot).test_client()

    yield make
    for p in patchers:
        p.stop()


async def _get(client, path):
    resp = await client.get(path)
    assert resp.status_code == 200
    return resp, await resp.get_data(as_text=True)


async def test_landing_page_is_indexable_and_explains_itself(make_client):
    client = make_client(site_name="Puzzbot", test_instance=True, contact="admin@puzzbot.com")
    resp, body = await _get(client, "/")
    assert "noindex" not in body
    assert resp.headers["Cache-Control"] == "no-cache"
    assert resp.headers["Content-Type"].startswith("text/html")
    # the intro sits INSIDE #root (React replaces it on mount), before the app script
    root = body.split('<div id="root">', 1)[1].split("</div>", 1)[0]
    assert "<h1>Puzzbot</h1>" in root
    assert "A private dashboard for puzzle-hunt teams" in root
    assert "Test instance." in root
    assert '<a href="/api/auth/login?next=%2F">Log in with Discord</a>' in root
    assert "takes you to discord.com to sign in" in root
    assert '<a href="/privacy">Privacy</a>' in root
    assert "Contact admin@puzzbot.com." in root
    assert '<script type="module" src="/assets/app.js"></script>' in body


async def test_live_instance_has_no_test_line_and_no_empty_contact(make_client):
    client = make_client(site_name="Quarantine Decrypters", test_instance=False, contact="")
    _, body = await _get(client, "/")
    assert "<h1>Quarantine Decrypters</h1>" in body
    assert "Test instance" not in body
    assert "Contact" not in body


async def test_config_values_are_html_escaped(make_client):
    client = make_client(site_name="<script>x</script>", contact='a"b<c>@example.com')
    _, body = await _get(client, "/")
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;x&lt;/script&gt;" in body
    assert "a&quot;b&lt;c&gt;@example.com" in body


@pytest.mark.parametrize("path", ["/hunts/ABC123", "/privacy", "/guilds/123/settings"])
async def test_other_routes_stay_noindex_and_bare(make_client, path):
    client = make_client(test_instance=True, contact="admin@puzzbot.com")
    _, body = await _get(client, path)
    assert '<meta name="robots" content="noindex" />' in body
    assert '<div id="root"></div>' in body
    assert "Log in with Discord" not in body


def test_real_index_html_carries_the_markers_the_server_rewrites():
    """_landing_html swaps exact strings; if index.html's spelling drifts, / would
    silently go back to an empty, noindexed page. (Vite copies both through as-is.)"""
    from pathlib import Path
    from bot.web import _EMPTY_ROOT, _NOINDEX_META
    src = (Path(__file__).resolve().parent.parent / "frontend" / "index.html").read_text()
    assert _NOINDEX_META in src
    assert _EMPTY_ROOT in src
