"""Plan C: dashboard parity for a hunt's own working sheets.

Same permission bar as the puzzle-sheet routes (any authenticated user who can
see the hunt, while it is unarchived), and the same hunt-keyed lock the Discord
edge takes, so a dashboard add cannot race a !add_sheet in the hunt channel.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt


def _sheet(id, name, page_id="P1"):
    return SimpleNamespace(id=id, puzzle_name=name, google_page_id=page_id)


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    cog = MagicMock()
    cog.service.add_hunt_sheet = AsyncMock(return_value=_sheet(4, "alt text", "P7"))
    cog.service.remove_hunt_sheet = AsyncMock()
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt(id=10)
    audit = AsyncMock()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._audit", audit),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx,
                              audit=audit)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
class TestAddHuntSheetRoute:
    async def test_creates_the_sheet_and_returns_it(self, web):
        res = await web.client.post("/api/hunts/U1/sheets", json={"name": "alt text"})
        assert res.status_code == 200
        body = await res.get_json()
        assert body["ok"] is True
        # The response carries what the service actually created, not the name
        # the client sent -- add_hunt_sheet uniquifies a clashing tab name, and
        # echoing the request would show the wrong one in the UI.
        assert body["sheet"] == {"id": 4, "name": "alt text", "page_id": "P7"}
        web.cog.service.add_hunt_sheet.assert_awaited_once()
        assert web.cog.service.add_hunt_sheet.await_args.args[1] == "alt text"

    async def test_returns_the_uniquified_name_not_the_requested_one(self, web):
        """Two people adding 'scratch' get 'scratch' and 'scratch (2)'. A route
        that echoed the request body would show both as 'scratch'."""
        web.cog.service.add_hunt_sheet = AsyncMock(
            return_value=_sheet(5, "scratch (2)", "P9"))
        res = await web.client.post("/api/hunts/U1/sheets", json={"name": "scratch"})
        assert (await res.get_json())["sheet"]["name"] == "scratch (2)"

    async def test_a_blank_name_is_allowed(self, web):
        """The service falls back to 'Sheet for <hunt>', mirroring !add_sheet."""
        res = await web.client.post("/api/hunts/U1/sheets", json={})
        assert res.status_code == 200
        assert web.cog.service.add_hunt_sheet.await_args.args[1] == ""

    async def test_archived_hunt_is_refused(self, web):
        web.ctx["hunt"]["archived"] = True
        res = await web.client.post("/api/hunts/U1/sheets", json={"name": "x"})
        assert res.status_code == 403
        assert (await res.get_json())["error"] == "hunt_archived"
        web.cog.service.add_hunt_sheet.assert_not_awaited()

    async def test_takes_the_hunt_sheet_lock(self, web):
        """The dashboard write must serialise against a concurrent !add_sheet in
        the hunt channel, which holds this same lock. Observed from INSIDE the
        service call: asserting after the request would pass even unlocked."""
        from bot.core.locks import _HUNT_SHEET_LOCKS
        seen = []

        async def observe(hunt, name):
            seen.append(_HUNT_SHEET_LOCKS[10].locked())
            return _sheet(4, "alt text", "P7")

        web.cog.service.add_hunt_sheet = AsyncMock(side_effect=observe)
        await web.client.post("/api/hunts/U1/sheets", json={"name": "alt text"})
        assert seen == [True]
        assert not _HUNT_SHEET_LOCKS[10].locked()

    async def test_writes_an_audit_entry(self, web):
        await web.client.post("/api/hunts/U1/sheets", json={"name": "alt text"})
        assert web.audit.await_args.args[0] == "add_hunt_sheet"


@pytest.mark.asyncio
class TestRemoveHuntSheetRoute:
    async def test_removes_the_sheet(self, web):
        res = await web.client.delete("/api/hunts/U1/sheets/4")
        assert res.status_code == 200
        assert (await res.get_json())["ok"] is True
        web.cog.service.remove_hunt_sheet.assert_awaited_once()
        # The id from the URL, not from anywhere else.
        assert web.cog.service.remove_hunt_sheet.await_args.args[1] == 4

    async def test_deletes_the_id_in_the_url_not_a_fixed_one(self, web):
        """A route that hard-coded or mis-read the id would pass a single-id
        test. Two different ids, checked separately."""
        await web.client.delete("/api/hunts/U1/sheets/7")
        assert web.cog.service.remove_hunt_sheet.await_args.args[1] == 7

    async def test_unknown_sheet_is_404(self, web):
        """remove_hunt_sheet looks the id up among THIS hunt's sheets, so a id
        from another hunt arrives here as sheet_not_found."""
        web.cog.service.remove_hunt_sheet = AsyncMock(
            side_effect=RuntimeError("sheet_not_found"))
        res = await web.client.delete("/api/hunts/U1/sheets/999")
        assert res.status_code == 404
        assert (await res.get_json())["error"] == "sheet_not_found"

    async def test_archived_hunt_is_refused(self, web):
        web.ctx["hunt"]["archived"] = True
        res = await web.client.delete("/api/hunts/U1/sheets/4")
        assert res.status_code == 403
        web.cog.service.remove_hunt_sheet.assert_not_awaited()

    async def test_takes_the_hunt_sheet_lock(self, web):
        from bot.core.locks import _HUNT_SHEET_LOCKS
        seen = []

        async def observe(hunt, sheet_id):
            seen.append(_HUNT_SHEET_LOCKS[10].locked())

        web.cog.service.remove_hunt_sheet = AsyncMock(side_effect=observe)
        await web.client.delete("/api/hunts/U1/sheets/4")
        assert seen == [True]

    async def test_writes_an_audit_entry(self, web):
        await web.client.delete("/api/hunts/U1/sheets/4")
        assert web.audit.await_args.args[0] == "remove_hunt_sheet"
