"""Phase 1 (B): session_secret strength validation must reject weak/placeholder secrets."""
import pytest

from bot.web import _valid_session_secret   # pure helper to be added in Phase 2


@pytest.mark.parametrize("secret", ["", "short", "changeme", "insecure", "x" * 31])
def test_rejects_weak_or_placeholder(secret):
    assert _valid_session_secret(secret) is False


def test_accepts_strong_secret():
    assert _valid_session_secret("k7Q" + "a" * 40) is True
