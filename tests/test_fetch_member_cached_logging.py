"""_fetch_member_cached should treat a 404 Unknown Member as the expected
"not a member" answer (quiet log, negative-cached) and reserve WARNING+traceback
for genuinely unexpected REST failures."""
import logging
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.web import auth


def _make_not_found():
    response = MagicMock()
    response.status = 404
    response.reason = "Not Found"
    return discord.NotFound(response, {"message": "Unknown Member", "code": 10007})


@pytest.fixture(autouse=True)
def _clear_cache():
    auth._not_member_cache.clear()
    yield
    auth._not_member_cache.clear()


class TestFetchMemberCachedLogging:
    async def test_not_found_logged_quietly_and_cached(self, caplog):
        guild = MagicMock(id=123)
        guild.fetch_member = AsyncMock(side_effect=_make_not_found())

        with caplog.at_level(logging.DEBUG, logger="bot.web.auth"):
            member = await auth._fetch_member_cached(guild, 456)

        assert member is None
        assert (guild.id, 456) in auth._not_member_cache
        # Expected "not a member" — must NOT be a WARNING and must NOT carry a traceback.
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == []
        assert all(r.exc_info is None for r in caplog.records)

    async def test_unexpected_error_logged_loudly(self, caplog):
        guild = MagicMock(id=123)
        guild.fetch_member = AsyncMock(side_effect=RuntimeError("boom"))

        with caplog.at_level(logging.DEBUG, logger="bot.web.auth"):
            member = await auth._fetch_member_cached(guild, 456)

        assert member is None
        assert (guild.id, 456) in auth._not_member_cache
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None
