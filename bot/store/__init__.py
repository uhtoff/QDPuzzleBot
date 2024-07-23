import os
import mysql.connector

from pathlib import Path

from .puzzle_settings import GuildSettings, HuntSettings, _GuildSettingsDb
from .puzzle_data import PuzzleData, _PuzzleJsonDb, MissingPuzzleError
from .fs import FilePuzzleJsonDb, FileGuildSettingsDb
from .mysqldb import MySQLPuzzleJsonDb, MySQLGuildSettingsDb

from bot.utils import config

DATA_DIR = Path(__file__).parent.parent.parent / "data"
if "LADDER_SPOT_DATA_DIR" in os.environ:
    # TODO: move to config.json??
    DATA_DIR = Path(os.environ["LADDER_SPOT_DATA_DIR"])


PuzzleJsonDb = _PuzzleJsonDb
GuildSettingsDb = _GuildSettingsDb
if config.storage == 'fs':
    PuzzleJsonDb = FilePuzzleJsonDb(dir_path=DATA_DIR)
    GuildSettingsDb = FileGuildSettingsDb(dir_path=DATA_DIR)
elif config.storage == 'mysql':
    mydb = mysql.connector.connect(
        host="localhost",
        user=config.mysql_username,
        password=config.mysql_password,
        database=config.database
    )
    mydb.autocommit = True
    PuzzleJsonDb = MySQLPuzzleJsonDb(dir_path=DATA_DIR, mydb=mydb)
    GuildSettingsDb = MySQLGuildSettingsDb(dir_path=DATA_DIR, mydb=mydb)
