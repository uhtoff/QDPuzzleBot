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


def _server_error():
    return HttpError(httplib2.Response({"status": 503}), b"{}")


async def test_non_idempotent_call_does_not_retry_an_ambiguous_failure():
    # A 5xx may arrive after Google applied the write; replaying a batch that
    # deletes rows by index would delete whatever moved into those rows.
    with patch("bot.utils.gsheets._execute", side_effect=[_server_error(), "ok"]) as ex, \
         patch("bot.utils.gsheets.asyncio.sleep", AsyncMock()):
        with pytest.raises(HttpError):
            await gsheets.execute_with_retry(object(), retry_ambiguous=False)
    assert ex.call_count == 1


async def test_non_idempotent_call_still_retries_a_rate_limit():
    # A 429 means the request was rejected, not applied: retrying is safe.
    with patch("bot.utils.gsheets._execute", side_effect=[_rate_limited(), "ok"]), \
         patch("bot.utils.gsheets.asyncio.sleep", AsyncMock()):
        assert await gsheets.execute_with_retry(object(), retry_ambiguous=False) == "ok"
