"""T4 (docs/reviews/2026-08-07-comprehensive-review.md): MySQLReconnector must
not open a connection at construction time.

The reconnectors are built at module import, so an eager connect turned "MySQL
is down at boot" into an exception out of `import bot.store` -- the supervisor
then restart-looped a database that was still recovering. Only *established*
connections self-heal, so nothing retried.

Locally-named helpers -- the parallel runner shards by file.
"""
from unittest.mock import MagicMock, patch

import pytest

from bot.store import MySQLReconnector


def _lazy_cfg():
    return dict(host="localhost", user="u", password="p", database="d", charset="utf8mb4")


def test_lazy_construction_opens_no_connection():
    with patch("mysql.connector.connect") as connect:
        MySQLReconnector(**_lazy_cfg())
    connect.assert_not_called()


def test_lazy_construction_survives_a_down_database():
    """The crash-loop itself: import must not raise when MySQL is unreachable."""
    with patch("mysql.connector.connect", side_effect=OSError("connection refused")):
        MySQLReconnector(**_lazy_cfg())  # must not raise


def test_lazy_first_cursor_connects():
    conn = MagicMock()
    with patch("mysql.connector.connect", return_value=conn) as connect:
        db = MySQLReconnector(**_lazy_cfg())
        db.cursor()
    connect.assert_called_once()
    assert conn.autocommit is True, "autocommit must survive the lazy path"
    conn.cursor.assert_called_once()


def test_lazy_second_call_reuses_the_connection():
    """Once established, _ensure pings rather than reconnecting -- otherwise
    every query would pay a fresh handshake."""
    conn = MagicMock()
    with patch("mysql.connector.connect", return_value=conn) as connect:
        db = MySQLReconnector(**_lazy_cfg())
        db.cursor()
        db.dict_cursor()
        db.commit()
    assert connect.call_count == 1
    assert conn.ping.call_count == 2


def test_lazy_recovers_once_the_database_returns():
    """A bot that booted with MySQL down must work on the next query, not stay
    broken until restarted."""
    conn = MagicMock()
    with patch("mysql.connector.connect", side_effect=[OSError("refused"), conn]):
        db = MySQLReconnector(**_lazy_cfg())
        with pytest.raises(OSError):
            db.cursor()
        db.cursor()  # database is back
    conn.cursor.assert_called_once()


def test_lazy_close_without_a_connection_is_a_no_op():
    with patch("mysql.connector.connect") as connect:
        db = MySQLReconnector(**_lazy_cfg())
        assert db.close() is None
    connect.assert_not_called()


def test_lazy_forwarded_attribute_connects_first():
    """__getattr__ forwards to the live connection; it used to be able to assume
    one existed."""
    conn = MagicMock()
    conn.server_host = "localhost"
    with patch("mysql.connector.connect", return_value=conn):
        db = MySQLReconnector(**_lazy_cfg())
        assert db.server_host == "localhost"
