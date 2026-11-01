"""An archived hunt is read-only, including for admins (M29 follow-up).

The route-prologue extraction turned seven silent archived-check omissions into
explicit `allow_archived=True` keywords. Making them visible was the finding;
deciding them was a separate call, and the decision was that all four
questionable ones should be guarded.

Guarded now:
  patch_hunt_settings        editing settings on a finished hunt
  set_hunt_role              assigning the team role on a finished hunt
  post_hunt_solved_sheet     creating the solved sheet on a finished hunt
  convert_to_feeder_preview  the strongest case -- its own action,
                             convert_to_feeder, was ALREADY guarded, so the
                             preview succeeded and the real thing then 403'd

Still exempt, deliberately:
  post_hunt_archive   it IS the archive toggle; guarding it blocks unarchiving
  get_puzzle_images   a read
  delete_hunt         admins must be able to delete archived hunts (it carried
                      a marker comment saying so long before this work)

This is a BEHAVIOUR change, not a refactor: each of the four now returns 403
where it previously succeeded.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_hunt, make_hunt_ctx, make_puzzle, make_web_app,
                            start_patches, stop_patches)

# route -> (http method, path, the service attr that must NOT be reached)
CASES = [
    ("patch_hunt_settings", "patch", "/api/hunts/U1/settings"),
    ("set_hunt_role", "post", "/api/hunts/U1/role"),
    ("post_hunt_solved_sheet", "post", "/api/hunts/U1/solved-sheet"),
    ("patch_hunt_sheet_mode", "patch", "/api/hunts/U1/sheet-mode"),
    ("convert_to_feeder_preview", "get", "/api/puzzles/9/convert-to-feeder/preview"),
]


@asynccontextmanager
async def _noop_lock(_id):
    yield


def _web(archived):
    cog = MagicMock()
    cog.service.update_hunt_settings = AsyncMock()
    cog.service.set_hunt_role = AsyncMock(return_value=[])
    cog.service.create_hunt_solved_sheet = AsyncMock(return_value="SHEET")
    app = make_web_app("actions", cog=cog)

    ctx = make_hunt_ctx(archived=archived)
    pdb = MagicMock(); pdb.get_by_attr.return_value = make_puzzle(id=9)
    hdb = MagicMock(); hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions.hunt_sheet_lock", _noop_lock),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    start_patches(patches)
    return SimpleNamespace(client=app.test_client(), cog=cog, patches=patches)


async def _call(web, method, path):
    try:
        resp = await getattr(web.client, method)(path, json={})
        return resp, await resp.get_json()
    finally:
        stop_patches(web.patches)


@pytest.mark.asyncio
@pytest.mark.parametrize("name, method, path", CASES, ids=[c[0] for c in CASES])
async def test_an_archived_hunt_refuses_the_route(name, method, path):
    """Permission mocks all ALLOW, so a 403 here can only be the archived gate
    -- not a tier refusal wearing the same status code."""
    resp, body = await _call(_web(archived=True), method, path)

    assert resp.status_code == 403, f"{name}: {resp.status_code} {body}"
    assert body["error"] == "hunt_archived", f"{name}: {body}"


@pytest.mark.asyncio
@pytest.mark.parametrize("name, method, path", CASES, ids=[c[0] for c in CASES])
async def test_a_live_hunt_still_reaches_the_route(name, method, path):
    """The keeper. Without it, a guard that refused unconditionally -- or a
    route accidentally left permanently 403 -- would pass the test above."""
    resp, body = await _call(_web(archived=False), method, path)

    assert resp.status_code != 403, f"{name}: unexpectedly refused: {body}"
    assert (body or {}).get("error") != "hunt_archived", f"{name}: {body}"
