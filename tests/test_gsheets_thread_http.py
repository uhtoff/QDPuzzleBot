import threading
from unittest.mock import MagicMock, patch

import pytest

from bot.utils import gsheets


@pytest.fixture(autouse=True)
def _no_secrets():
    # _credentials() reads google_secrets.json from cwd; never touch it here.
    with patch("bot.utils.gsheets._credentials", return_value=MagicMock()):
        yield


def _in_thread(fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join()
    return out[0]


def test_same_thread_reuses_http():
    assert gsheets._thread_http() is gsheets._thread_http()


def test_threads_get_distinct_http():
    a = _in_thread(gsheets._thread_http)
    b = _in_thread(gsheets._thread_http)
    assert a is not b
    assert a is not gsheets._thread_http()


def test_execute_passes_thread_http():
    req = MagicMock()
    req.execute.return_value = {"ok": 1}
    assert gsheets.execute_sync(req) == {"ok": 1}
    req.execute.assert_called_once_with(http=gsheets._thread_http())


@pytest.mark.asyncio
async def test_execute_with_retry_runs_off_loop_with_thread_http():
    req = MagicMock()
    req.execute.return_value = {"ok": 2}
    sentinel = object()
    with patch("bot.utils.gsheets._thread_http", return_value=sentinel):
        assert await gsheets.execute_with_retry(req) == {"ok": 2}
    req.execute.assert_called_once_with(http=sentinel)


@pytest.mark.asyncio
async def test_execute_with_retry_http_is_worker_thread_http():
    seen = []
    req = MagicMock()
    req.execute.side_effect = lambda http: seen.append(http) or {}
    await gsheets.execute_with_retry(req)
    assert seen[0] is not gsheets._thread_http()  # not the loop thread's Http
