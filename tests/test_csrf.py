"""Phase 1 (CSRF): double-submit helpers gate mutating requests only."""
import pytest

from bot.web import _csrf_required, _csrf_ok   # pure helpers added in Phase 2


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutating_methods_require_csrf(method):
    assert _csrf_required(method) is True


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_do_not_require_csrf(method):
    assert _csrf_required(method) is False


def test_csrf_ok_when_tokens_present_and_equal():
    assert _csrf_ok("k7Qabc", "k7Qabc") is True


@pytest.mark.parametrize("header,cookie", [
    (None, "abc"), ("abc", None), (None, None), ("", ""), ("abc", "xyz"),
])
def test_csrf_rejects_missing_or_mismatched(header, cookie):
    assert _csrf_ok(header, cookie) is False
