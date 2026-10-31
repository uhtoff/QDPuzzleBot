"""Every dashboard route in frontend/src/App.tsx must also be in the server's
_CLIENT_ROUTES, or refreshing that page 404s: the server only falls back to
index.html for paths it knows. /guilds/<id>/teams shipped without its entry."""
import re
from pathlib import Path

from bot.web import _is_client_route

APP_TSX = Path(__file__).resolve().parent.parent / "frontend" / "src" / "App.tsx"


def _app_routes():
    paths = re.findall(r'<Route\s+path="([^"]+)"', APP_TSX.read_text())
    return [p for p in paths if p not in ("/", "*")]


def _sample(route):
    """A concrete URL path for a react-router pattern: :guildId → a number, other
    params → an id-like token."""
    parts = []
    for seg in route.strip("/").split("/"):
        if seg.startswith(":"):
            parts.append("2" if seg.lower().endswith("id") else "ABC123")
        else:
            parts.append(seg)
    return "/".join(parts)


def test_app_tsx_has_routes_to_check():
    assert len(_app_routes()) >= 3


def test_every_dashboard_route_falls_back_to_index_html():
    missing = [r for r in _app_routes() if not _is_client_route(_sample(r))]
    assert missing == [], f"add these to bot/web/__init__.py _CLIENT_ROUTES: {missing}"


def test_trailing_slash_and_unknown_paths():
    assert _is_client_route("guilds/2/teams/")
    assert not _is_client_route("guilds/2/nope")
