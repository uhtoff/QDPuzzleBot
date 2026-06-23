"""Phase 1 (C): _audit records the acting user + action via the data.execute write helper."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_audit_writes_row_with_user_and_action():
    from bot.web import actions  # _audit added in Phase 3
    user = {"id": 4242, "username": "alice", "global_name": "Alice", "avatar": None}
    with patch("bot.web.actions.current_user", return_value=user), \
         patch("bot.web.data.execute", new=AsyncMock()) as ex:
        await actions._audit("solve", puzzle_id=7, hunt_uid="HUNTX", detail="ANSWER")
    ex.assert_awaited_once()
    sql = ex.await_args.args[0]
    params = ex.await_args.args[1]
    assert "web_audit_log" in sql
    assert 4242 in params and "solve" in params and 7 in params and "HUNTX" in params


@pytest.mark.asyncio
async def test_audit_never_raises_when_write_fails():
    from bot.web import actions
    user = {"id": 1, "username": "bob", "global_name": None, "avatar": None}
    with patch("bot.web.actions.current_user", return_value=user), \
         patch("bot.web.data.execute", new=AsyncMock(side_effect=RuntimeError("db down"))):
        # best-effort: a failed audit write must NOT propagate
        await actions._audit("retag", puzzle_id=1)
