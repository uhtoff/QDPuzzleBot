import datetime
import random
import string
import errno
import json
import logging
from pathlib import Path
from typing import List
import re

import pytz
from .puzzle_data import _PuzzleJsonDb, PuzzleData, MissingPuzzleError
from .round_data import _RoundJsonDb, RoundData, MissingRoundError
from .hunt_data import _HuntJsonDb, HuntData, MissingHuntError
from .puzzle_settings import _GuildSettingsDb, GuildSettings

logger = logging.getLogger(__name__)

def discord_channel_slug(name):
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)  # remove special characters
    slug = re.sub(r'\s+', '-', slug)          # replace spaces with hyphens
    slug = re.sub(r'-+', '-', slug)           # collapse multiple hyphens
    slug = slug.strip('-')                    # remove leading/trailing hyphens
    return slug[:100]                         # enforce 100 character limit

class _MySQLBaseDb:
    TABLE_NAME = None
    SPECIAL_ATTR = []
    mydb = None

    def __init__(self, mydb):
        self.mydb = mydb

    def commit(self, object_to_commit):
        cursor = self.mydb.cursor()
        data = ()
        field_list = []
        database_id = 0
        for attr, value in object_to_commit.__dict__.items():
            if attr in self.SPECIAL_ATTR:
                continue
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

    def delete(self, delete_id):
        """Delete single puzzle from database by database id"""
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(f"DELETE FROM `{self.TABLE_NAME}` WHERE id = %s", (delete_id,))
        deleted_rows = cursor.rowcount
        cursor.close()
        # if deleted_rows != 1:S
        #     raise MissingDataError(f"Unable to find puzzle {puzzle_id} for {round_id}")

    def check_duplicates(self, value, field = 'name'):
        """Ensures no duplicate name by default but can check any field if passed"""
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(f"SELECT id FROM `{self.TABLE_NAME}` WHERE `{field}` = %s", (value,))
        duplicates = cursor.rowcount
        if duplicates > 0:
            return True
        else:
            return False

    def generate_uid(self, field = 'id', chars = 6, input = None):
        code = ""
        if input is not None:
            split_input = input.split()
            for word in split_input:
                if word[:2].isascii():
                    code += word[:2].upper()
            code = code[:6]

        while len(code) < chars:
            code += random.choice(string.ascii_letters).upper()

        loop_count = 0

        while self.check_duplicates(code, field):
            loop_count += 1
            if loop_count < 26:
                code = code[:5] + random.choice(string.ascii_letters).upper()
            elif loop_count < 512:
                code = code[:4]
                while len(code) < 6:
                    code += random.choice(string.ascii_letters).upper()
            else:
                code = code[:3]
                while len(code) < 6:
                    code += random.choice(string.ascii_letters).upper()

        return code

    def check_duplicates_in_hunt(self, name, hunt_data: HuntData):
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(f"SELECT id FROM `{self.TABLE_NAME}` WHERE `name` = %s AND `hunt_id` = %s", (name, hunt_data.id,))
        duplicates = cursor.rowcount
        if duplicates > 0:
            return True
        else:
            return False

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
                print(f"Unable to find hunt for {keyword} - {value}")
                return None

class MySQLRoundJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'rounds'
    def get_lowest_code_in_hunt(self, hunt_id):
        """Retrieve lower code from database return 0 if none set"""
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT meta_code FROM rounds WHERE hunt_id = %s ORDER BY meta_code DESC LIMIT 0,1", (hunt_id,))
        row = cursor.fetchone()
        if row:
            return row['meta_code']
        else:
            return 0

    def check_duplicate_meta_code(self, meta_code):
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT id FROM rounds WHERE meta_code = %s LIMIT 0,1", (meta_code,))
        row = cursor.fetchone()
        if row:
            return True
        else:
            return False

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
                print(f"Unable to find Round for {keyword} - {value}")
                return None

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

    def delete(self, round_id):
        super(MySQLRoundJsonDb, self).delete(round_id)
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(f"DELETE FROM tags WHERE round_id = %s", (round_id,))
        cursor.close()

class MySQLPuzzleJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'puzzles'
    SPECIAL_ATTR = ['tags']

    def get(self, guild_id, puzzle_id, round_id, hunt_id) -> PuzzleData:
        """Retrieve single puzzle from database"""
        cursor = self.mydb.cursor(dictionary = True)
        cursor.execute("SELECT * FROM puzzles WHERE channel_id = %s", (puzzle_id,))
        row = cursor.fetchone()
        if row:
            puzzle = PuzzleData.import_dict(row)
            self.get_tags(puzzle)
            return puzzle
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
                puzzle = PuzzleData.import_dict(row)
                self.get_tags(puzzle)
                return puzzle
            else:
                print(f"Unable to find puzzle for {keyword} - {value}")
                return None

    def get_all(self, guild_id, hunt_id="*") -> List[PuzzleData]:
        """Retrieve all puzzles from database"""
        puzzle_datas = []
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT puzzles.* FROM puzzles "
                       "LEFT JOIN hunts ON puzzles.hunt_id = hunts.id "
                       "WHERE hunts.guild_id = %s", (guild_id,))
        rows = cursor.fetchall()
        for row in rows:
            puzzle = PuzzleData.import_dict(row)
            self.get_tags(puzzle)
            puzzle_datas.append(puzzle)
        cursor.close()
        return PuzzleData.sort_by_puzzle_start(puzzle_datas)

    def get_all_from_hunt(self, hunt_id) -> List[PuzzleData]:
        """Retrieve all puzzles from database"""
        puzzle_datas = []
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT puzzles.* FROM puzzles "
                       "WHERE hunt_id = %s", (hunt_id,))
        rows = cursor.fetchall()
        for row in rows:
            puzzle = PuzzleData.import_dict(row)
            self.get_tags(puzzle)
            puzzle_datas.append(puzzle)
        cursor.close()
        return puzzle_datas

    def get_all_from_round(self, round_id) -> List[PuzzleData]:
        """Retrieve all puzzles from database"""
        puzzle_datas = []
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT puzzles.* FROM puzzles LEFT JOIN tags ON puzzles.id = tags.puzzle_id WHERE tags.round_id = %s", (round_id,))
        rows = cursor.fetchall()
        for row in rows:
            puzzle = PuzzleData.import_dict(row)
            self.get_tags(puzzle)
            puzzle_datas.append(puzzle)
        cursor.close()
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
            if puzzle.solved and puzzle.solve_time is not None:
                # found a solved puzzle
                if now - puzzle.solve_time > datetime.timedelta(minutes=minutes):
                    # enough time has passed, archive the channel
                    puzzles_to_archive.append(puzzle)
        return puzzles_to_archive

    def check_duplicates_in_hunt(self, name, hunt_id):
        cursor = self.mydb.cursor(dictionary = True)
        cursor.execute("SELECT puzzles.id FROM puzzles "
                       "WHERE ( puzzles.name = %s OR puzzles.channel_name= %s ) AND puzzles.hunt_id = %s", (name, discord_channel_slug(name), hunt_id))
        duplicates = cursor.rowcount
        if duplicates > 0:
            return True
        else:
            return False

    def get_tags(self, puzzle):
        cursor = self.mydb.cursor(dictionary=True)
        select_stmt = f"SELECT tags.round_id FROM tags WHERE puzzle_id = {puzzle.id}"
        cursor.execute(select_stmt)
        rows = cursor.fetchall()
        for row in rows:
            puzzle.tags.append(row['round_id'])
        cursor.close()

    def commit(self, puzzle):
        super(MySQLPuzzleJsonDb, self).commit(puzzle)
        cursor = self.mydb.cursor(dictionary=True)
        select_stmt = f"SELECT round_id FROM tags WHERE puzzle_id = {puzzle.id}"
        cursor.execute(select_stmt)
        rows = cursor.fetchall()
        database_tags = []
        for row in rows:
            database_tags.append(row['round_id'])
        tags_to_remove = set(database_tags) - set(puzzle.tags)
        if len(tags_to_remove) > 0:
            delete_stmt = f"DELETE FROM tags WHERE round_id IN ("
            delete_stmt += f"{','.join(map(str,tags_to_remove))}"
            delete_stmt += f") AND puzzle_id = {puzzle.id}"
            cursor.execute(delete_stmt)
        tags_to_add = set(puzzle.tags) - set(database_tags)
        if len(tags_to_add) > 0:
            insert_stmt = f"INSERT INTO tags (round_id, puzzle_id) VALUES "
            for tag in tags_to_add:
                insert_stmt += f"({tag}, {puzzle.id}),"
            insert_stmt = insert_stmt.rstrip(", ")
            cursor.execute(insert_stmt)
        cursor.close()
        cursor = self.mydb.cursor(dictionary=True)
        cursor.close()
        self.mydb.commit()

    def delete(self, puzzle_id):
        super(MySQLPuzzleJsonDb, self).delete(puzzle_id)
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(f"DELETE FROM tags WHERE puzzle_id = %s", (puzzle_id,))
        cursor.close()

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
                    "UNION SELECT category_id as channel_id, 'Group' as channel_type FROM rounds "
                    "UNION SELECT channel_id, 'Puzzle' as channel_type FROM puzzles "
                ") channel_types WHERE channel_id=%s")
        data = (channel_id,)
        cursor.execute(stmt, data)
        row = cursor.fetchone()
        channel_type = None
        if row:
            channel_type = row['channel_type']
        return channel_type

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
        data = (settings.guild_id, settings.guild_name, settings.website_url,
                settings.discord_bot_channel, settings.discord_bot_emoji, settings.discord_use_voice_channels,
                settings.drive_parent_id, settings.drive_resources_id)
        if settings.id > 0:
            update_stmt = ("UPDATE `guilds` SET `guild_id`=%s,`guild_name`=%s, `website_url`=%s,"
                           "`discord_bot_channel`=%s,`discord_bot_emoji`=%s,`discord_use_voice_channels`=%s,"
                           "`drive_parent_id`=%s,`drive_resources_id`=%s "
                           "WHERE id=%s")
            data = data + (settings.id,)
            cursor.execute(update_stmt, data)
        else:
            insert_stmt = ("INSERT INTO `guilds`(`guild_id`, `guild_name`, `website_url`,"
                           "`discord_bot_channel`, `discord_bot_emoji`, `discord_use_voice_channels`, "
                           "`drive_parent_id`, `drive_resources_id`) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)")
            cursor.execute(insert_stmt, data)
            settings.id = cursor.lastrowid
        cursor.close()
        self.mydb.commit()