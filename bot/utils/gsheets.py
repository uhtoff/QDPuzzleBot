import asyncio
import logging
import random
import socket
import ssl
import threading
import time
from http.client import HTTPException

import google_auth_httplib2
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import build_http

logger = logging.getLogger(__name__)

# HTTP statuses worth retrying: 429 (rate limit) and transient server errors.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
# Transient transport failures (intermittent connectivity / Sheets outages).
_RETRYABLE_EXCEPTIONS = (socket.timeout, ConnectionError, TimeoutError, ssl.SSLError, HTTPException)
_MAX_RETRIES = 5
_BASE_DELAY = 1.0   # seconds; grows exponentially with jitter
_MAX_DELAY = 60.0

# Build the service objects once and reuse them. Rebuilding on every call re-reads
# the credentials file and re-runs auth setup, adding latency and load for nothing.
_sheets_service = None
_drive_service = None

# The googleapiclient service's shared httplib2.Http is NOT thread-safe, and we
# drive .execute() both on the event loop (execute_sync) and on worker threads
# (execute_with_retry -> asyncio.to_thread); concurrent .execute() calls on one
# Http corrupted the C heap (e01c81f). Rather than serialise every call behind
# one global lock (a slow Drive copy would stall every hunt), each thread gets
# its own Http and passes it to request.execute(http=...). Request objects are
# built without I/O, so only .execute() needs the per-thread Http.
_thread_local = threading.local()


def _thread_http():
    http = getattr(_thread_local, "http", None)
    if http is None:
        http = google_auth_httplib2.AuthorizedHttp(_credentials(), http=build_http())
        _thread_local.http = http
    return http


def _execute(request):
    return request.execute(http=_thread_http())


def _credentials():
    return Credentials.from_service_account_file("google_secrets.json")


def get_sheet():
    global _sheets_service
    if _sheets_service is None:
        try:
            service = build("sheets", "v4", credentials=_credentials())
            _sheets_service = service.spreadsheets()
        except HttpError:
            logger.exception("Google Sheets API error building service")
            raise
    return _sheets_service


def get_drive():
    global _drive_service
    if _drive_service is None:
        try:
            _drive_service = build("drive", "v3", credentials=_credentials())
        except HttpError:
            logger.exception("Google Drive API error building service")
            raise
    return _drive_service


def _is_retryable(error):
    if isinstance(error, HttpError):
        return getattr(error.resp, "status", None) in _RETRYABLE_STATUSES
    return isinstance(error, _RETRYABLE_EXCEPTIONS)


def _retry_after_seconds(error):
    """Return the Retry-After delay (seconds) advertised by the server, if any."""
    resp = getattr(error, "resp", None)
    if resp is None:
        return None
    retry_after = resp.get("retry-after")
    if not retry_after:
        return None
    try:
        # Sheets sends a delta-seconds value; an HTTP-date is also legal but rare,
        # in which case we just fall back to exponential backoff.
        return float(retry_after)
    except (TypeError, ValueError):
        return None


def _backoff_delay(attempt, error):
    delay = _retry_after_seconds(error)
    if delay is not None:
        return delay
    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
    return delay + random.uniform(0, delay * 0.25)  # full-ish jitter to avoid sync'd retries


def execute_sync(request, max_retries=_MAX_RETRIES):
    """Execute a google-api-python-client request, retrying transient failures
    (HTTP 429 / 5xx and connection errors) with exponential backoff and jitter.

    Blocking; safe to call from synchronous code paths. Async callers should use
    execute_with_retry() instead so the backoff sleeps don't stall the event loop.
    """
    attempt = 0
    while True:
        try:
            return _execute(request)
        except Exception as error:
            if not _is_retryable(error) or attempt >= max_retries:
                raise
            delay = _backoff_delay(attempt, error)
            attempt += 1
            _log_retry(error, attempt, max_retries, delay)
            time.sleep(delay)


def _log_retry(error, attempt, max_retries, delay):
    status = getattr(getattr(error, "resp", None), "status", type(error).__name__)
    logger.warning(
        "Google API call failed (%s); retry %d/%d in %.1fs",
        status, attempt, max_retries, delay,
    )


async def execute_with_retry(request, max_retries=_MAX_RETRIES, on_retry=None):
    """Execute a request, running the blocking call in a worker thread and the
    backoff sleeps in the event loop so neither stalls the bot.

    If on_retry is given, it is awaited once — before the first backoff sleep —
    so callers can let the user know the call is slow / being retried.
    """
    attempt = 0
    notified = False
    while True:
        try:
            return await asyncio.to_thread(_execute, request)
        except Exception as error:
            if not _is_retryable(error) or attempt >= max_retries:
                raise
            delay = _backoff_delay(attempt, error)
            attempt += 1
            _log_retry(error, attempt, max_retries, delay)
            if on_retry is not None and not notified:
                notified = True
                try:
                    await on_retry()
                except Exception:
                    logger.exception("Sheets on_retry notification failed")
            await asyncio.sleep(delay)
