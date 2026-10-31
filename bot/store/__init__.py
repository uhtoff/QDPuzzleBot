import os
from typing import Any, Optional, Protocol, Sequence, cast

import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract


class DictCursor(Protocol):
    """The dict-row cursor surface the DAOs use (``cursor(dictionary=True)``).

    mysql-connector types the generic ``cursor()`` as returning the abstract
    cursor, whose fetches yield ``RowType | Dict[...]`` unions; the concrete
    dict cursors (pure-Python and C-extension) share no dict-typed base class.
    This protocol pins the actual runtime shape: rows are always dicts.
    """

    def execute(self, operation: str, params: Sequence[Any] = ...) -> Any: ...
    def executemany(self, operation: str, seq_params: Sequence[Sequence[Any]]) -> Any: ...
    def fetchone(self) -> Optional[dict[str, Any]]: ...
    def fetchall(self) -> list[dict[str, Any]]: ...
    def close(self) -> Any: ...
    @property
    def lastrowid(self) -> Optional[int]: ...

from .puzzle_settings import GuildSettings
from .puzzle_data import PuzzleData, MissingPuzzleError, AdditionalSheetData
from .tag_group_data import TagGroupData, MissingTagGroupError, resolve_group, GroupMatch
from .rollup_data import RollupData
from .hunt_data import HuntData, MissingHuntError
from .evidence_data import EvidenceData
from .media_data import MediaData
from .canvas_data import CanvasRegionData, HuntCanvasData
from .markup_data import MediaMarkupData
from .puzzle_image_data import PuzzleImageData
from .mysqldb import MySQLPuzzleJsonDb, MySQLGuildSettingsDb, MySQLTagGroupJsonDb, MySQLHuntJsonDb, MySQLAdditionalSheetsDb, MySQLChannelActivityDb, MySQLRollupJsonDb, MySQLEvidenceDb, MySQLTagGroupCategoryDb, MySQLHuntCategoryDb, MySQLMessageDb, MySQLMediaDb, MySQLHuntCanvasDb, MySQLCanvasRegionDb, MySQLMediaMarkupDb, MySQLPuzzleImageDb, MySQLHuntInviteDb, MySQLHuntAccessLogDb

from bot.utils import config

class MySQLReconnector:
    # Every cursor()/commit()/rollback() call runs _ensure() synchronously on
    # whatever thread calls it -- for the bang/web write paths, that's the bot's
    # event loop. Without an explicit connect_timeout, mysql-connector-python
    # falls back to the OS socket default, which can block for tens of seconds
    # to minutes against an unreachable host -- stalling every command bot-wide
    # for the whole outage window (#89). Bound it so a down/unreachable MySQL
    # fails fast instead; this doesn't move the query off the event loop, but it
    # caps the worst-case per-call stall from "unbounded" to a few seconds.
    _CONNECT_TIMEOUT_SECONDS = 5

    def __init__(self, **cfg):
        cfg.setdefault("connect_timeout", self._CONNECT_TIMEOUT_SECONDS)
        self._cfg = cfg
        # Deliberately NOT connected here. These objects are constructed at module
        # import (bot/store/__init__.py, below), so connecting eagerly meant MySQL
        # being down at boot raised straight out of the import: the supervisor then
        # restart-looped a database that was still recovering, hammering it. Only
        # *established* connections self-heal (_ensure), so there was no retry to
        # save it either. Connecting on first use instead means the bot boots, and
        # the first query after MySQL returns simply succeeds.
        #
        # It also stops every ancillary script and test that merely imports the
        # package from requiring a live database.
        self._conn: Optional[MySQLConnectionAbstract] = None

    def _connect(self) -> MySQLConnectionAbstract:
        self._conn = cast(MySQLConnectionAbstract, mysql.connector.connect(**self._cfg))
        # keep your existing behaviour
        self._conn.autocommit = True
        return self._conn

    def _ensure(self) -> MySQLConnectionAbstract:
        """Return a live connection, opening or reviving one as needed."""
        if self._conn is None:
            return self._connect()
        try:
            # Will reconnect automatically if dropped
            self._conn.ping(reconnect=True, attempts=2, delay=1)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass
            return self._connect()
        return self._conn

    def cursor(self, *args, **kwargs):
        return self._ensure().cursor(*args, **kwargs)

    def dict_cursor(self) -> DictCursor:
        """A ``dictionary=True`` cursor, typed as such (see DictCursor)."""
        return cast(DictCursor, self._ensure().cursor(dictionary=True))

    def commit(self):
        # No-op while autocommit is on (set in _connect and _ensure): every statement
        # commits itself, so there is no open transaction for this to close. Kept
        # because it is part of the connection surface a real transaction would need.
        #
        # Atomicity in this codebase comes from STATEMENT SHAPE, not from this call --
        # multi-statement operations are ordered so a mid-op failure is recoverable
        # (bulk_update_edge_type), collapsed into one statement (set_positions), or
        # backed by an FK cascade (delete_message). If a future operation genuinely
        # needs a transaction, run it on a DEDICATED autocommit=False connection: this
        # one is shared by several DAOs on the event loop, so an open transaction here
        # would span their interleaved statements.
        return self._ensure().commit()

    def rollback(self):
        return self._ensure().rollback()

    def close(self):
        if self._conn is None:
            return None
        try:
            return self._conn.close()
        except Exception:
            return None
        finally:
            # Drop the dead handle so a later call reconnects rather than pinging
            # a closed connection.
            self._conn = None

    def __getattr__(self, name):
        """
        Forward everything else (e.g., .cmd_query, .database, etc.)
        to the underlying mysql.connector connection.

        Goes through _ensure() so a forwarded attribute works on a
        not-yet-connected reconnector, as it did when __init__ connected eagerly.
        """
        # Only reached when normal lookup fails, so _cfg/_conn (set in __init__)
        # never route here -- no recursion.
        return getattr(self._ensure(), name)

if config.storage == 'mysql':
    mydb = MySQLReconnector(
        host="localhost",
        user=config.mysql_username,
        password=config.mysql_password,
        database=config.database,
        charset="utf8mb4",
    )
    PuzzleJsonDb = MySQLPuzzleJsonDb(mydb=mydb)
    GuildSettingsDb = MySQLGuildSettingsDb(mydb=mydb)
    TagGroupJsonDb = MySQLTagGroupJsonDb(mydb=mydb)
    TagGroupCategoryDb = MySQLTagGroupCategoryDb(mydb=mydb)
    HuntCategoryDb = MySQLHuntCategoryDb(mydb=mydb)
    HuntJsonDb = MySQLHuntJsonDb(mydb=mydb)
    HuntInviteDb = MySQLHuntInviteDb(mydb=mydb)
    HuntAccessLogDb = MySQLHuntAccessLogDb(mydb=mydb)
    SheetsJsonDb = MySQLAdditionalSheetsDb(mydb=mydb)
    RollupsJsonDb = MySQLRollupJsonDb(mydb=mydb)
    # The activity writer fires on *every* human message and is drained on a
    # background worker via asyncio.to_thread (see bot/cogs/activity.py). A
    # mysql.connector connection is not safe to share across threads, so give the
    # activity DB its own dedicated connection rather than reusing the shared one.
    activity_db = MySQLReconnector(
        host="localhost",
        user=config.mysql_username,
        password=config.mysql_password,
        database=config.database,
        charset="utf8mb4",
    )
    ChannelActivityDb = MySQLChannelActivityDb(mydb=activity_db)
    # The bridge worker writes on a background thread (asyncio.to_thread), and a
    # mysql.connector connection is not thread-safe to share; give it its own.
    message_db_conn = MySQLReconnector(
        host="localhost",
        user=config.mysql_username,
        password=config.mysql_password,
        database=config.database,
        charset="utf8mb4",
    )
    MessageDb = MySQLMessageDb(mydb=message_db_conn)
    EvidenceJsonDb = MySQLEvidenceDb(mydb=mydb)
    MediaJsonDb = MySQLMediaDb(mydb=mydb)
    CanvasJsonDb = MySQLHuntCanvasDb(mydb=mydb)
    CanvasRegionJsonDb = MySQLCanvasRegionDb(mydb=mydb)
    MediaMarkupJsonDb = MySQLMediaMarkupDb(mydb=mydb)
    PuzzleImageJsonDb = MySQLPuzzleImageDb(mydb=mydb)
    # The bridge cog's puzzle-channel gate queries run on a background thread via
    # asyncio.to_thread (bot/cogs/bridge.py _load_puzzle_channels/_lookup_puzzle_channel).
    # A mysql.connector connection is not thread-safe to share, so give this its own
    # dedicated connection rather than reusing the shared `mydb` (which every
    # synchronous, event-loop-thread DB call above also uses) -- sharing it caused a
    # SIGABRT heap-corruption crash under concurrent access.
    bridge_channels_conn = MySQLReconnector(
        host="localhost",
        user=config.mysql_username,
        password=config.mysql_password,
        database=config.database,
        charset="utf8mb4",
    )