"""Dashboard parity for !media_stats / !media_sweep.

Organiser-gated and hunt-scoped like the bang commands, and calling the same
bot/core/media_gc seam so the two edges cannot report different numbers.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt_ctx, make_web_app, start_patches, stop_patches


def _orphans(*sizes):
    return [SimpleNamespace(id=i, size=s) for i, s in enumerate(sizes, start=1)]


@pytest.fixture
def web():
    app = make_web_app("actions")
    ctx = make_hunt_ctx()
    audit = AsyncMock()

    gc = MagicMock()
    gc.orphaned_media.return_value = []
    gc.sweep.return_value = SimpleNamespace(count=0, bytes_freed=0)

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", audit),
        # The routes import media_gc inside the function body, so the module
        # attribute is what they look up on every call.
        patch("bot.core.media_gc.orphaned_media", gc.orphaned_media),
        patch("bot.core.media_gc.sweep", gc.sweep),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), ctx=ctx, gc=gc, audit=audit)
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
class TestMediaStatsRoute:
    async def test_reports_count_and_summed_size(self, web):
        web.gc.orphaned_media.return_value = _orphans(100, 25)

        res = await web.client.get("/api/hunts/U1/media/stats")

        assert res.status_code == 200
        # Summed over both rows: one row could not tell a sum from a last-wins.
        assert await res.get_json() == {"count": 2, "bytes": 125}
        web.gc.orphaned_media.assert_called_once_with(10)

    async def test_reports_nothing_for_a_clean_hunt(self, web):
        res = await web.client.get("/api/hunts/U1/media/stats")
        assert await res.get_json() == {"count": 0, "bytes": 0}

    async def test_never_sweeps(self, web):
        web.gc.orphaned_media.return_value = _orphans(100)
        await web.client.get("/api/hunts/U1/media/stats")
        web.gc.sweep.assert_not_called()

    async def test_non_organisers_are_refused(self, web):
        with patch("bot.web.actions.require_organiser",
                   AsyncMock(return_value=({"error": "insufficient_role"}, 403))):
            res = await web.client.get("/api/hunts/U1/media/stats")
        assert res.status_code == 403
        web.gc.orphaned_media.assert_not_called()

    async def test_archived_hunt_is_refused(self, web):
        web.ctx["hunt"]["archived"] = True
        res = await web.client.get("/api/hunts/U1/media/stats")
        assert res.status_code == 403
        assert (await res.get_json())["error"] == "hunt_archived"


@pytest.mark.asyncio
class TestMediaSweepRoute:
    async def test_sweeps_the_hunt_and_reports_what_went(self, web):
        web.gc.sweep.return_value = SimpleNamespace(count=3, bytes_freed=4096)

        res = await web.client.post("/api/hunts/U1/media/sweep")

        assert res.status_code == 200
        assert await res.get_json() == {"ok": True, "count": 3, "bytes": 4096}
        web.gc.sweep.assert_called_once_with(10)

    async def test_writes_an_audit_row(self, web):
        web.gc.sweep.return_value = SimpleNamespace(count=3, bytes_freed=4096)

        await web.client.post("/api/hunts/U1/media/sweep")

        web.audit.assert_awaited_once()
        assert web.audit.await_args.args[0] == "sweep_media"
        assert web.audit.await_args.kwargs["hunt_uid"] == "U1"
        # What was destroyed has to be in the log, not just that a sweep ran.
        assert "count=3" in web.audit.await_args.kwargs["detail"]

    async def test_non_organisers_are_refused(self, web):
        with patch("bot.web.actions.require_organiser",
                   AsyncMock(return_value=({"error": "insufficient_role"}, 403))):
            res = await web.client.post("/api/hunts/U1/media/sweep")
        assert res.status_code == 403
        web.gc.sweep.assert_not_called()
        web.audit.assert_not_awaited()

    async def test_archived_hunt_is_refused(self, web):
        web.ctx["hunt"]["archived"] = True
        res = await web.client.post("/api/hunts/U1/media/sweep")
        assert res.status_code == 403
        web.gc.sweep.assert_not_called()

    async def test_get_is_not_a_sweep(self, web):
        """The destructive edge is POST-only: a crawler or a prefetched link
        must not be able to delete a hunt's images."""
        res = await web.client.get("/api/hunts/U1/media/sweep")
        assert res.status_code == 405
        web.gc.sweep.assert_not_called()
