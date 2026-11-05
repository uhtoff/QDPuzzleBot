"""Retry budget for rate-limited Google calls.

Sheets enforces its write quota per minute. At the 9 Oct 2026 hunt start a
!puzzle batchUpdate exhausted five retries (~34s of backoff) inside one
saturated minute and failed; the retries must outlast a whole quota window.
"""
from unittest.mock import AsyncMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from bot.utils import gsheets

QUOTA_WINDOW_SECONDS = 60


def _rate_limited():
    return HttpError(httplib2.Response({"status": 429}), b"{}")


async def test_rate_limit_retries_outlast_a_quota_minute():
    sleep = AsyncMock()
    with patch("bot.utils.gsheets._execute", side_effect=_rate_limited()), \
         patch("bot.utils.gsheets.asyncio.sleep", sleep), \
         patch("bot.utils.gsheets.random.uniform", return_value=0.0):
        with pytest.raises(HttpError):
            await gsheets.execute_with_retry(object())
    waited = sum(call.args[0] for call in sleep.await_args_list)
    assert waited > QUOTA_WINDOW_SECONDS


async def test_a_call_that_recovers_inside_the_budget_succeeds():
    # Rate-limited six times (one more than the old budget allowed), then
    # the quota window rolls over and the call goes through.
    outcomes = [_rate_limited()] * 6 + ["ok"]
    with patch("bot.utils.gsheets._execute", side_effect=outcomes), \
         patch("bot.utils.gsheets.asyncio.sleep", AsyncMock()):
        assert await gsheets.execute_with_retry(object()) == "ok"
