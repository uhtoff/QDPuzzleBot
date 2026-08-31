"""M36: request-level refusal tests for the gated mutating routes that had no
route test of their own.

tests/test_web_route_gating.py resolves each route's tier by walking the AST and
recording that `require_admin`/`require_organiser` appears somewhere in the call
graph. That proves the gate is CONSULTED; it cannot prove a refusal is RETURNED.
A handler that drops the `return perm` line still resolves to the right tier and
the map still passes, while every caller gets through.

So these tests drive the REAL gate through the real request stack -- no
`patch("...require_admin")` shortcut -- and assert both directions for each
route:

  * an under-privileged caller gets a 403 AND nothing is written (the relevant
    DAO/service mutator is never called), because a status-code-only assertion
    would pass against a handler that answers 403 after mutating;
  * a properly privileged caller gets through and the mutator DOES run, so the
    file cannot be satisfied by a handler that simply refuses everyone.

For the admin-tier routes the under-privileged caller holds the ORGANISER role,
which pins the tier as well as the refusal: dropping the gate to organiser is as
much a regression as dropping it altogether.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_hunt, make_hunt_ctx, make_puzzle, make_round,
                            make_web_app, start_patches, stop_patches)

# The fake guild is UNCONFIGURED (GuildSettingsDb.get_cached -> None), so
# role_gate bootstraps the tiers from these role NAMES.
EVERYONE_ID, ORGANISER_ID, ADMIN_ID = 1000, 1001, 1002

MEMBER = {EVERYONE_ID}
ORGANISER = {EVERYONE_ID, ORGANISER_ID}
ADMIN = {EVERYONE_ID, ADMIN_ID}


def _guild():
    """A guild carrying a real Organisers role.

    It has to: with no Organisers role `organiser_tier_bypassed` is True and
    every organiser-tier check passes for any member, so the organiser refusals
    below would be testing the bypass rather than the gate.
    """
    g = MagicMock()
    g.id = 123
    g.roles = [SimpleNamespace(id=EVERYONE_ID, name="everyone"),
               SimpleNamespace(id=ORGANISER_ID, name="Organisers"),
               SimpleNamespace(id=ADMIN_ID, name="admin")]
    return g


def _ran(mock):
    """How many times *mock* actually ran -- awaits for AsyncMock, calls otherwise."""
    return mock.await_count if isinstance(mock, AsyncMock) else mock.call_count


@pytest.fixture
def web():
    guild = _guild()

    cog = MagicMock()
    cog.web_refresh_info = AsyncMock()
    cog.service = MagicMock()
    cog.service.update_hunt_settings = AsyncMock()
    cog.service.set_hunt_role = AsyncMock(return_value=[])
    cog.service.reconcile = AsyncMock(return_value=SimpleNamespace(
        actions=[], orphan_channels=[], orphan_tabs=[], failures=[]))
    cog.service.delete_orphans = AsyncMock(return_value=(0, []))
    cog.service.refresh_target = AsyncMock()
    cog.service.make_minimeta = AsyncMock()
    cog.service.remove_minimeta = AsyncMock()
    cog.service.set_puzzle_url = AsyncMock()
    cog.service.refresh_unassigned_safe = AsyncMock()
    cog.service.notifier.refresh_info = AsyncMock()
    # C7: both edges now go through one service seam instead of hand-rolling
    # check -> create -> commit against the GoogleSheets cog.
    cog.service.create_hunt_solved_sheet = AsyncMock(return_value="NEWSHEET")

    gs_cog = MagicMock()
    gs_cog.create_solved_sheet = AsyncMock(return_value="NEWSHEET")

    app = make_web_app("actions", cog=cog)
    app.bot.get_cog.side_effect = lambda name: {"Puzzles": cog,
                                                "GoogleSheets": gs_cog}.get(name)
    app.bot.get_guild.return_value = guild

    ctx = make_hunt_ctx()

    hdb = MagicMock()
    # solved_sheet_id must start empty or post_hunt_solved_sheet short-circuits 409.
    hdb.get_by_attr.return_value = make_hunt(id=10, solved_sheet_id=None,
                                             name="Hunt", guild_id=1, uid="U1")
    tgdb = MagicMock()
    tgdb.get_by_attr.return_value = make_round(id=5)
    pzdb = MagicMock()
    pzdb.get_by_attr.return_value = make_puzzle(id=9)
    cdb = MagicMock()
    cdb.get_by_id.return_value = SimpleNamespace(id=3, hunt_id=10, media_id=7,
                                                 name="Extravaganza", note=None)
    rdb = MagicMock()
    rdb.get_by_canvas.return_value = []
    mdb = MagicMock()
    mdb.get_by_id.return_value = SimpleNamespace(id=7, hunt_id=10, width=1, height=1,
                                                 tile_status="ready", tile_levels=1)
    gsdb = MagicMock()
    gsdb.get_cached.return_value = None       # unconfigured -> bootstrap by name

    roles = AsyncMock(return_value=MEMBER)

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        # guild_for()/organiser_tier_bypassed() resolve through auth's bot_app.
        patch("bot.web.auth.bot_app",
              return_value=MagicMock(bot=MagicMock(get_guild=MagicMock(return_value=guild)))),
        # The one seam standing in for "who is calling". require_admin /
        # require_organiser themselves are NOT patched -- they are what is under
        # test here.
        patch("bot.web.actions.resolve_member_role_ids", roles),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_for_tag_group", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_for_canvas", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions.events.publish"),
        patch("bot.store.GuildSettingsDb", gsdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.TagGroupJsonDb", tgdb),
        patch("bot.store.PuzzleJsonDb", pzdb),
        patch("bot.store.CanvasJsonDb", cdb),
        patch("bot.store.CanvasRegionJsonDb", rdb),
        patch("bot.store.MediaJsonDb", mdb),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, gs_cog=gs_cog,
                              ctx=ctx, guild=guild, roles=roles, hdb=hdb, tgdb=tgdb,
                              pzdb=pzdb, cdb=cdb, mdb=mdb)
    finally:
        stop_patches(patches)


# --- The route table ------------------------------------------------------
#
# (id, tier, method, path, body, mutators) where `mutators` names the calls that
# must NOT happen on a refusal and MUST happen when allowed. The first is the
# route's primary write.

def _spec(id, tier, method, path, body, mutators):
    return pytest.param(tier, method, path, body, mutators, id=id)


GATED = [
    _spec("patch_hunt_settings", "admin", "PATCH", "/api/hunts/U1/settings",
          {"url": "http://example.test/h"},
          lambda w: [w.cog.service.update_hunt_settings]),
    _spec("set_hunt_role", "admin", "POST", "/api/hunts/U1/role",
          {"role_name": "Team Alpha"},
          lambda w: [w.cog.service.set_hunt_role]),
    _spec("post_hunt_archive", "admin", "POST", "/api/hunts/U1/archive",
          {"archived": True},
          lambda w: [w.hdb.commit]),
    _spec("post_hunt_solved_sheet", "admin", "POST", "/api/hunts/U1/solved-sheet",
          {},
          # Same assertion -- "the privileged caller's write happens" -- re-pointed
          # at the seam that now performs it. The create and the commit both moved
          # inside service.create_hunt_solved_sheet (C7).
          lambda w: [w.cog.service.create_hunt_solved_sheet]),
    _spec("reconcile", "organiser", "POST", "/api/hunts/U1/reconcile?mode=apply",
          {},
          lambda w: [w.cog.service.reconcile]),
    _spec("rebuild_rollup", "organiser", "POST", "/api/rollups/5/rebuild",
          {},
          lambda w: [w.cog.service.refresh_target]),
    _spec("remove_minimeta", "organiser", "POST", "/api/puzzles/9/remove-minimeta",
          {},
          lambda w: [w.cog.service.remove_minimeta]),
    _spec("update_canvas", "organiser", "PATCH", "/api/canvases/3",
          {"name": "Renamed"},
          lambda w: [w.cdb.update]),
]

# The caller one tier BELOW what the route demands.
_BELOW = {"admin": ORGANISER, "organiser": MEMBER}
_AT = {"admin": ADMIN, "organiser": ORGANISER}


async def _call(web, method, path, body):
    return await getattr(web.client, method.lower())(path, json=body)


@pytest.mark.parametrize("tier,method,path,body,mutators", GATED)
class TestGatedRoutesRefuseAndDoNotMutate:
    async def test_under_privileged_caller_is_refused_and_nothing_is_written(
            self, web, tier, method, path, body, mutators):
        web.roles.return_value = _BELOW[tier]

        resp = await _call(web, method, path, body)

        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "insufficient_role"
        for m in mutators(web):
            assert _ran(m) == 0, f"{m} ran despite the refusal"

    async def test_non_member_is_refused_and_nothing_is_written(
            self, web, tier, method, path, body, mutators):
        # resolve_member_role_ids returns None for someone who is not in the
        # guild at all -- the gate's other refusal branch.
        web.roles.return_value = None

        resp = await _call(web, method, path, body)

        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "forbidden"
        for m in mutators(web):
            assert _ran(m) == 0, f"{m} ran despite the refusal"

    async def test_privileged_caller_is_allowed_and_the_write_happens(
            self, web, tier, method, path, body, mutators):
        # Without this direction the refusals above would all still pass against
        # a handler that rejected every caller.
        web.roles.return_value = _AT[tier]

        resp = await _call(web, method, path, body)

        assert resp.status_code == 200, await resp.get_data()
        assert _ran(mutators(web)[0]) == 1


class TestAdminRoutesAreAdminNotOrganiser:
    """The tier itself, stated once rather than inferred from the table above:
    an organiser is the caller a mis-set gate would let through."""

    @pytest.mark.parametrize("path,body", [
        ("/api/hunts/U1/settings", {"url": "http://example.test/h"}),
    ])
    async def test_organiser_cannot_patch_hunt_settings(self, web, path, body):
        web.roles.return_value = ORGANISER
        resp = await web.client.patch(path, json=body)
        assert resp.status_code == 403
        assert _ran(web.cog.service.update_hunt_settings) == 0

    async def test_admin_can_patch_hunt_settings(self, web):
        web.roles.return_value = ADMIN
        resp = await web.client.patch("/api/hunts/U1/settings",
                                      json={"url": "http://example.test/h"})
        assert resp.status_code == 200
        web.cog.service.update_hunt_settings.assert_awaited_once()


# --- The two deliberately UNGATED routes in the same family ----------------
#
# POST /puzzles/<id>/link and POST /puzzles/<id>/make-minimeta carry no role
# gate by decision -- test_web_route_gating.py lists make-minimeta under
# UNGATED_BY_DECISION and /link among the ungated residue. So there is no
# under-privileged caller to refuse -- their only refusal is the archived hunt,
# and that is what is pinned here: it is the one write-block these two have, and
# nothing else asserted it.

UNGATED = [
    pytest.param("POST", "/api/puzzles/9/link", {"url": "http://example.test/p"},
                 lambda w: w.cog.service.set_puzzle_url, id="link"),
    pytest.param("POST", "/api/puzzles/9/make-minimeta", {},
                 lambda w: w.cog.service.make_minimeta, id="make_minimeta"),
]


@pytest.mark.parametrize("method,path,body,mutator", UNGATED)
class TestUngatedRoutesStillRefuseAnArchivedHunt:
    async def test_archived_hunt_is_refused_and_nothing_is_written(
            self, web, method, path, body, mutator):
        web.roles.return_value = MEMBER
        web.ctx["hunt"]["archived"] = True

        resp = await _call(web, method, path, body)

        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "hunt_archived"
        assert _ran(mutator(web)) == 0

    async def test_plain_member_is_allowed_on_a_live_hunt(
            self, web, method, path, body, mutator):
        """These two are open on purpose; a member with no role must get through,
        so a later tightening cannot happen silently."""
        web.roles.return_value = MEMBER

        resp = await _call(web, method, path, body)

        assert resp.status_code == 200, await resp.get_data()
        assert _ran(mutator(web)) == 1
