import os
from typing import cast

import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract

from pathlib import Path

from .puzzle_settings import GuildSettings, HuntSettings, _GuildSettingsDb
from .puzzle_data import PuzzleData, _PuzzleJsonDb, MissingPuzzleError, AdditionalSheetData
from .tag_group_data import TagGroupData, _TagGroupJsonDb, MissingTagGroupError, resolve_group, GroupMatch
from .rollup_data import RollupData, _RollupJsonDb
from .hunt_data import HuntData, _HuntJsonDb, MissingHuntError
from .evidence_data import EvidenceData
from .mysqldb import MySQLPuzzleJsonDb, MySQLGuildSettingsDb, MySQLTagGroupJsonDb, MySQLHuntJsonDb, MySQLAdditionalSheetsDb, MySQLChannelActivityDb, MySQLRollupJsonDb, MySQLEvidenceDb, MySQLTagGroupCategoryDb, MySQLHuntCategoryDb

from bot.utils import config

DATA_DIR = Path(__file__).parent.parent.parent / "data"
if "LADDER_SPOT_DATA_DIR" in os.environ:
    # TODO: move to config.json??
    DATA_DIR = Path(os.environ["LADDER_SPOT_DATA_DIR"])

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
        self._connect()

    def _connect(self):
        self._conn = cast(MySQLConnectionAbstract, mysql.connector.connect(**self._cfg))
        # keep your existing behaviour
        self._conn.autocommit = True

    def _ensure(self):
        try:
            # Will reconnect automatically if dropped
            self._conn.ping(reconnect=True, attempts=2, delay=1)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = cast(MySQLConnectionAbstract, mysql.connector.connect(**self._cfg))
            self._conn.autocommit = True

    def cursor(self, *args, **kwargs):
        self._ensure()
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        # even with autocommit=True, your code calls commit()
        self._ensure()
        return self._conn.commit()

    def rollback(self):
        self._ensure()
        return self._conn.rollback()

    def close(self):
        try:
            return self._conn.close()
        except Exception:
            return None

    def __getattr__(self, name):
        """
        Forward everything else (e.g., .cmd_query, .database, etc.)
        to the underlying mysql.connector connection.
        """
        return getattr(self._conn, name)

if config.storage == 'mysql':
    mydb = MySQLReconnector(
        host="localhost",
        user=config.mysql_username,
        password=config.mysql_password,
        database=config.database,
        charset="utf8mb4",
    )
    PuzzleJsonDb = MySQLPuzzleJsonDb(mydb=mydb)
    GuildSettingsDb = MySQLGuildSettingsDb(dir_path=DATA_DIR, mydb=mydb)
    TagGroupJsonDb = MySQLTagGroupJsonDb(mydb=mydb)
    TagGroupCategoryDb = MySQLTagGroupCategoryDb(mydb=mydb)
    HuntCategoryDb = MySQLHuntCategoryDb(mydb=mydb)
    HuntJsonDb = MySQLHuntJsonDb(mydb=mydb)
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
    EvidenceJsonDb = MySQLEvidenceDb(mydb=mydb)