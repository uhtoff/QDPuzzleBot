import datetime
import errno
import json
import logging
from pathlib import Path
from typing import List

import pytz
from .puzzle_data import _PuzzleJsonDb, PuzzleData, MissingPuzzleError
from .round_data import _RoundJsonDb, RoundData, MissingRoundError
from .hunt_data import _HuntJsonDb, HuntData, MissingHuntError
from .puzzle_settings import _GuildSettingsDb, GuildSettings

logger = logging.getLogger(__name__)


class _MySQLBaseDb:
    TABLE_NAME = None
    mydb = None

    def __init__(self, mydb):
        self.mydb = mydb

    def commit(self, object_to_commit):
        cursor = self.mydb.cursor()
        data = ()
        field_list = []
        database_id = 0
        for attr, value in object_to_commit.__dict__.items():
            if attr == "id":
                database_id = value
            else:
                if type(value) is list:
                    data += (json.dumps(value),)
                else:
                    data += (value,)
                field_list.append(attr)
        if database_id > 0:
            update_stmt = f"UPDATE `{self.TABLE_NAME}` SET "
            for field in field_list:
                update_stmt += f"`{field}` = %s, "
            update_stmt = update_stmt[:-2]
            update_stmt += " WHERE `id` = %s"
            data = data + (database_id,)
            cursor.execute(update_stmt, data)
        else:
            insert_stmt = f"INSERT INTO `{self.TABLE_NAME}` ("
            for field in field_list:
                insert_stmt += f"`{field}`, "
            insert_stmt = insert_stmt[:-2]
            insert_stmt += ") VALUES ("
            insert_stmt += "%s," * len(field_list)
            insert_stmt = insert_stmt[:-1]
            insert_stmt += ")"
            cursor.execute(insert_stmt, data)
            object_to_commit.id = cursor.lastrowid
        cursor.close()
        self.mydb.commit()

class MySQLHuntJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'hunts'
    def get_by_attr(self,**kwargs):
        """Retrieve Hunt by attribute.  Only first sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            cursor = self.mydb.cursor(dictionary = True)
            cursor.execute(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s", (value,))
            row = cursor.fetchone()
            if row:
                return HuntData.import_dict(row)
            else:
                raise MissingHuntError(f"Unable to find hunt for {keyword} - {value}")

class MySQLRoundJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'rounds'
    def delete(self, round_data):
        round_path = self.round_path(round_data)
        try:
            round_path.unlink()
        except IOError:
            pass

    def get(self, round_id) -> RoundData:
        """Retrieve single round from database"""
        cursor = self.mydb.cursor(dictionary = True)
        cursor.execute("SELECT * FROM rounds WHERE channel_id = %s", (round_id,))
        row = cursor.fetchone()
        if row:
            return RoundData.import_dict(row)
        else:
            raise MissingRoundError(f"Unable to find round {round_id}")

    def get_by_attr(self,**kwargs):
        """Retrieve Round by attribute.  Only first sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            cursor = self.mydb.cursor(dictionary = True)
            cursor.execute(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s", (value,))
            row = cursor.fetchone()
            if row:
                return RoundData.import_dict(row)
            else:
                raise MissingRoundError(f"Unable to find Round for {keyword} - {value}")

    def get_all(self, hunt_id="*") -> List[RoundData]:
        """Retrieve all rounds for hunt"""
        round_datas = []
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT * FROM rounds WHERE hunt_id = %s", (hunt_id,))
        rows = cursor.fetchall()
        for row in rows:
            round_datas.append(RoundData.import_dict(row))
        cursor.close()
        return round_datas
        # return RoundData.sort_by_round_start(round_datas) TODO - ideally return by round start time
class MySQLPuzzleJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'puzzles'
    #
    # def __init__(self, dir_path: Path, mydb):
    #     self.dir_path = dir_path
    #     self.mydb = mydb

    # def commit(self, puzzle_data: PuzzleData):
    #     """Update or insert puzzle metadata file"""
    #     cursor = self.mydb.cursor()
    #     notes_json = json.dumps(puzzle_data.notes)
    #     data = (puzzle_data.name, puzzle_data.hunt_name, puzzle_data.hunt_id,
    #             puzzle_data.round_name, puzzle_data.round_id, puzzle_data.guild_id, puzzle_data.channel_id,
    #             puzzle_data.channel_mention, puzzle_data.voice_channel_id, puzzle_data.hunt_url,
    #             puzzle_data.google_sheet_id, puzzle_data.google_page_id, puzzle_data.status,
    #             puzzle_data.solution, puzzle_data.priority, puzzle_data.puzzle_type,
    #             notes_json, puzzle_data.start_time, puzzle_data.solve_time, puzzle_data.archive_time,)
    #     if puzzle_data.id > 0:
    #         update_stmt = ("UPDATE `puzzles` SET `name`=%s,`hunt_name`=%s,`hunt_id`=%s,"
    #                        "`round_name`=%s,`round_id`=%s,`guild_id`=%s,`channel_id`=%s,"
    #                        "`channel_mention`=%s,`voice_channel_id`=%s,`hunt_url`=%s,"
    #                        "`google_sheet_id`=%s,`google_page_id`=%s,`status`=%s,"
    #                        "`solution`=%s,`priority`=%s,`puzzle_type`=%s,"
    #                        "`notes`=%s,`start_time`=%s,`solve_time`=%s,`archive_time`=%s WHERE id=%s")
    #         data = data + (puzzle_data.id,)
    #         cursor.execute(update_stmt,data)
    #     else:
    #         insert_stmt = ("INSERT INTO `puzzles` (`name`, `hunt_name`, `hunt_id`, "
    #                        "`round_name`, `round_id`, `guild_id`, `channel_id`, "
    #                        "`channel_mention`, `voice_channel_id`, `hunt_url`, "
    #                        "`google_sheet_id`, `google_page_id`, `status`, "
    #                        "`solution`, `priority`, `puzzle_type`, "
    #                        "`notes`, `start_time`, `solve_time`, `archive_time`) "
    #                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
    #         cursor.execute(insert_stmt, data)
    #         puzzle_data.id = cursor.lastrowid
    #     cursor.close()
    #     self.mydb.commit()

    def delete(self, puzzle_data):
        """Delete single puzzle from database"""
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_data.id,))
        deleted_rows = cursor.rowcount
        if deleted_rows != 1:
            raise MissingPuzzleError(f"Unable to find puzzle {puzzle_id} for {round_id}")

    def get(self, guild_id, puzzle_id, round_id, hunt_id) -> PuzzleData:
        """Retrieve single puzzle from database"""
        cursor = self.mydb.cursor(dictionary = True)
        cursor.execute("SELECT * FROM puzzles WHERE channel_id = %s", (puzzle_id,))
        row = cursor.fetchone()
        if row:
            return PuzzleData.import_dict(row)
        else:
            raise MissingPuzzleError(f"Unable to find puzzle {puzzle_id} for {round_id}")

    def get_by_attr(self,**kwargs):
        """Retrieve puzzle by attribute.  Only first sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            cursor = self.mydb.cursor(dictionary = True)
            cursor.execute(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s", (value,))
            row = cursor.fetchone()
            if row:
                return PuzzleData.import_dict(row)
            else:
                print(f"Unable to find puzzle for {keyword} - {value}")
                return None

    def get_all(self, guild_id, hunt_id="*") -> List[PuzzleData]:
        """Retrieve all puzzles from database"""
        puzzle_datas = []
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT puzzles.* FROM puzzles "
                       "LEFT JOIN rounds ON puzzles.round_id = rounds.id "
                       "LEFT JOIN hunts ON rounds.hunt_id = hunts.id "
                       "WHERE hunts.guild_id = %s", (guild_id,))
        rows = cursor.fetchall()
        for row in rows:
            puzzle_datas.append(PuzzleData.import_dict(row))
        cursor.close()
        return PuzzleData.sort_by_puzzle_start(puzzle_datas)

    def get_all_from_round(self, round_id) -> List[PuzzleData]:
        """Retrieve all puzzles from database"""
        puzzle_datas = []
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT * FROM puzzles WHERE round_id = %s", (round_id,))
        rows = cursor.fetchall()
        for row in rows:
            puzzle_datas.append(PuzzleData.import_dict(row))
        cursor.close()
        return PuzzleData.sort_by_puzzle_start(puzzle_datas)

    def get_all_fs(self, guild_id, hunt_id="*") -> List[PuzzleData]:
        """Retrieve all puzzles from file system (for import purposes)"""
        paths = self.dir_path.rglob(f"{guild_id}/{hunt_id}/*/*.json")
        puzzle_datas = []
        for path in paths:
            try:
                with path.open() as fp:
                    puzzle_datas.append(PuzzleData.from_json(fp.read()))
            except Exception:
                logger.exception(f"Unable to load puzzle data from {path}")
        return PuzzleData.sort_by_puzzle_start(puzzle_datas)

    def get_solved_puzzles_to_archive(self, guild_id, now=None, include_meta=False, minutes=1) -> List[PuzzleData]:
        """Returns list of all solved but unarchived puzzles"""
        all_puzzles = self.get_all(guild_id)
        now = now or datetime.datetime.now(tz=pytz.UTC)
        puzzles_to_archive = []
        for puzzle in all_puzzles:
            if puzzle.archive_time is not None:
                # already archived
                continue
            if puzzle.name == "meta" and not include_meta:
                # we usually do not want to archive meta channels, only do manually
                continue
            if puzzle.status == "solved" and puzzle.solve_time is not None:
                # found a solved puzzle
                if now - puzzle.solve_time > datetime.timedelta(minutes=minutes):
                    # enough time has passed, archive the channel
                    puzzles_to_archive.append(puzzle)
        return puzzles_to_archive

    def get_channel_type(self, channel_id) -> str:
        cursor = self.mydb.cursor(dictionary=True)
        stmt = ("SELECT channel_id, channel_type FROM ( "
                    "SELECT hunt_id as channel_id, 'Hunt' as channel_type FROM hunts "
                    "UNION SELECT channel_id, 'Puzzle' as channel_type FROM puzzles "
                    "UNION SELECT round_channel as channel_id, 'Round' as channel_type FROM rounds "
                ") channel_types WHERE channel_id=%s")
        data = (channel_id,)
        cursor.execute(stmt, data)
        row = cursor.fetchone()
        if row:
            return row['channel_type']

class MySQLGuildSettingsDb():
    def __init__(self, dir_path: Path, mydb):
        self.dir_path = dir_path
        self.cached_settings = {}
        self.mydb = mydb
    
    def get_channel_type(self, channel_id) -> str:
        cursor = self.mydb.cursor(dictionary=True)
        stmt = ("SELECT channel_id, channel_type FROM ( "
                    "SELECT category_id as channel_id, 'Hunt' as channel_type FROM hunts "
                    "UNION SELECT channel_id as channel_id, 'Hunt' as channel_type FROM hunts "
                    "UNION SELECT channel_id, 'Puzzle' as channel_type FROM puzzles "
                    "UNION SELECT category_id as channel_id, 'Round' as channel_type FROM rounds "
                    "UNION SELECT channel_id as channel_id, 'Round' as channel_type FROM rounds "
                ") channel_types WHERE channel_id=%s")
        data = (channel_id,)
        cursor.execute(stmt, data)
        row = cursor.fetchone()
        if row:
            return row['channel_type']
    
    def get(self, guild_id: int) -> GuildSettings:
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT * FROM guilds WHERE guild_id = %s", (guild_id,))
        row = cursor.fetchone()
        if row:
            return GuildSettings.import_dict(row)
        cursor.close()

    def get_cached(self, guild_id: int) -> GuildSettings:
        if guild_id in self.cached_settings:
            return self.cached_settings[guild_id]
        settings = self.get(guild_id)
        self.cached_settings[guild_id] = settings
        return settings

    def commit(self, settings: GuildSettings):
        cursor = self.mydb.cursor()
        data = (settings.guild_id, settings.guild_name, 
                settings.discord_bot_channel, settings.discord_bot_emoji, settings.discord_use_voice_channels,
                settings.drive_parent_id, settings.drive_resources_id)
        if settings.id > 0:
            update_stmt = ("UPDATE `guilds` SET `guild_id`=%s,`guild_name`=%s,"
                           "`discord_bot_channel`=%s,`discord_bot_emoji`=%s,`discord_use_voice_channels`=%s,"
                           "`drive_parent_id`=%s,`drive_resources_id`=%s "
                           "WHERE id=%s")
            data = data + (settings.id,)
            cursor.execute(update_stmt, data)
        else:
            insert_stmt = ("INSERT INTO `guilds`(`guild_id`, `guild_name`, "
                           "`discord_bot_channel`, `discord_bot_emoji`, `discord_use_voice_channels`, "
                           "`drive_parent_id`, `drive_resources_id`) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s)")
            cursor.execute(insert_stmt, data)
            settings.id = cursor.lastrowid
        cursor.close()
        self.mydb.commit()