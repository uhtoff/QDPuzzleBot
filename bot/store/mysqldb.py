import datetime
import random
import string
import json
import logging
from pathlib import Path
from typing import List
import re

import pytz
from .puzzle_data import PuzzleData, MissingPuzzleError, AdditionalSheetData
from .tag_group_data import TagGroupData
from .rollup_data import RollupData
from .hunt_data import HuntData
from .puzzle_settings import GuildSettings
from .evidence_data import EvidenceData

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
        cursor.close()
        # if deleted_rows != 1:S
        #     raise MissingDataError(f"Unable to find puzzle {puzzle_id} for {round_id}")

    def check_duplicates(self, value, field = 'name'):
        """Ensures no duplicate name by default but can check any field if passed"""
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(f"SELECT 1 FROM `{self.TABLE_NAME}` WHERE `{field}` = %s LIMIT 1", (value,))
        found = cursor.fetchone() is not None
        cursor.close()
        return found

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

    def check_duplicates_in_hunt(self, name, hunt_data: HuntData) -> bool:
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(f"SELECT 1 FROM `{self.TABLE_NAME}` WHERE `name` = %s AND `hunt_id` = %s LIMIT 1", (name, hunt_data.id,))
        found = cursor.fetchone() is not None
        cursor.close()
        return found

class MySQLHuntJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'hunts'
    def get_by_attr(self,**kwargs):
        """Retrieve Hunt by attribute.  Only first sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            cursor = self.mydb.cursor(dictionary = True)
            try:
                cursor.execute(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s", (value,))
                row = cursor.fetchone()
            finally:
                cursor.close()
            if row:
                return HuntData.import_dict(row)
            logger.info(f"Unable to find hunt for {keyword} - {value}")
            return None

class MySQLTagGroupJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'tag_group'
    def get_lowest_code_in_hunt(self, hunt_id):
        """Retrieve lower code from database return 0 if none set"""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute("SELECT group_code FROM tag_group WHERE hunt_id = %s ORDER BY group_code DESC LIMIT 0,1", (hunt_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row:
            return row['group_code']
        return 0

    def check_duplicate_group_code(self, group_code):
        cursor = self.mydb.cursor()
        cursor.execute("SELECT 1 FROM tag_group WHERE group_code = %s LIMIT 1", (group_code,))
        found = cursor.fetchone() is not None
        cursor.close()
        return found

    def get_by_attr(self,**kwargs):
        """Retrieve TagGroup by attribute.  Only last sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            cursor = self.mydb.cursor(dictionary = True)
            try:
                cursor.execute(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s ORDER BY `id` ASC LIMIT 1", (value,))
                row = cursor.fetchone()
            finally:
                cursor.close()
            if row:
                return TagGroupData.import_dict(row)
            logger.info(f"Unable to find TagGroup for {keyword} - {value}")
            return None

    def get_all(self, hunt_id="*") -> List[TagGroupData]:
        """Retrieve all tag groups for hunt"""
        tag_group_datas = []
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tag_group WHERE hunt_id = %s", (hunt_id,))
        rows = cursor.fetchall()
        for row in rows:
            tag_group_datas.append(TagGroupData.import_dict(row))
        cursor.close()
        return tag_group_datas

    def delete(self, tag_group_id):
        super(MySQLTagGroupJsonDb, self).delete(tag_group_id)
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("DELETE FROM tags WHERE tag_group_id = %s", (tag_group_id,))
        cursor.close()

class MySQLAdditionalSheetsDb(_MySQLBaseDb):
    TABLE_NAME = 'additional_sheets'

    def get_by_puzzle(self, puzzle_id):
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT * FROM additional_sheets WHERE puzzle_id = %s ", (puzzle_id,))
        rows = cursor.fetchall()
        cursor.close()
        return rows


class MySQLRollupJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'rollups'

    def get_by_attr(self, **kwargs):
        """Retrieve a Rollup by attribute (e.g. round_id=). Last attr wins."""
        keyword, value = kwargs.popitem()
        if keyword:
            cursor = self.mydb.cursor(dictionary=True)
            try:
                cursor.execute(
                    f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s ORDER BY `id` ASC LIMIT 1",
                    (value,),
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
            if row:
                return RollupData.import_dict(row)
            return None

    def get_all_from_hunt(self, hunt_id) -> List[RollupData]:
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `hunt_id` = %s", (hunt_id,))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [RollupData.import_dict(row) for row in rows]

class MySQLPuzzleJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'puzzles'
    SPECIAL_ATTR = ['tags', 'additional_sheets', 'tag_edges']

    def construct_puzzle(self, row):
        puzzle = PuzzleData.import_dict(row)
        self.get_tags(puzzle)
        self.get_additional_sheets(puzzle)
        return puzzle

    def get(self, guild_id, puzzle_id, round_id, hunt_id) -> PuzzleData:
        """Retrieve single puzzle from database"""
        cursor = self.mydb.cursor(dictionary = True)
        try:
            cursor.execute("SELECT * FROM puzzles WHERE channel_id = %s", (puzzle_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row:
            return self.construct_puzzle(row)
        raise MissingPuzzleError(f"Unable to find puzzle {puzzle_id} for {round_id}")

    def get_by_attr(self,**kwargs):
        """Retrieve puzzle by attribute.  Only first sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            cursor = self.mydb.cursor(dictionary = True)
            try:
                cursor.execute(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s", (value,))
                row = cursor.fetchone()
            finally:
                cursor.close()
            if row:
                return self.construct_puzzle(row)
            logger.info(f"Unable to find puzzle for {keyword} - {value}")
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
            puzzle_datas.append(self.construct_puzzle(row))
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
            puzzle_datas.append(self.construct_puzzle(row))
        cursor.close()
        return puzzle_datas

    def get_all_from_tag_group(self, tag_group_id) -> List[PuzzleData]:
        """Retrieve all puzzles from database"""
        puzzle_datas = []
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT puzzles.* FROM puzzles LEFT JOIN tags ON puzzles.id = tags.puzzle_id WHERE tags.tag_group_id = %s", (tag_group_id,))
        rows = cursor.fetchall()
        for row in rows:
            puzzle_datas.append(self.construct_puzzle(row))
        cursor.close()
        return PuzzleData.sort_by_puzzle_start(puzzle_datas)

    def get_children(self, parent_puzzle_id: int):
        """Return all mini-puzzles whose parent_puzzle_id matches."""
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM puzzles WHERE parent_puzzle_id = %s ORDER BY id ASC",
            (parent_puzzle_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
        return [self.construct_puzzle(r) for r in rows]

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

    def check_duplicates_in_hunt(self, name, hunt_id) -> bool:
        cursor = self.mydb.cursor(dictionary = True)
        cursor.execute("SELECT 1 FROM puzzles "
                       "WHERE ( puzzles.name = %s OR puzzles.channel_name= %s ) AND puzzles.hunt_id = %s LIMIT 1", (name, discord_channel_slug(name), hunt_id))
        found = cursor.fetchone() is not None
        cursor.close()
        return found

    def get_unassigned(self, hunt_id) -> List[PuzzleData]:
        """Return non-meta puzzles that feed no meta (for the Unassigned Puzzles tab).

        Excludes mini-meta feeders (parent_puzzle_id set) — those are assigned to
        their mini-meta — and any puzzle tagged into a round that has a meta.
        """
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(
            "SELECT p.* FROM puzzles p"
            " WHERE p.hunt_id = %s"
            "   AND p.metapuzzle = 0"
            "   AND p.parent_puzzle_id IS NULL"   # mini-meta feeders are assigned
            "   AND NOT EXISTS ("
            "       SELECT 1 FROM tags t"
            "       JOIN tag_group r ON t.tag_group_id = r.id"
            "       WHERE t.puzzle_id = p.id AND r.target_puzzle_id > 0"
            "   )",
            (hunt_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
        return [self.construct_puzzle(r) for r in rows]

    def get_tags(self, puzzle):
        cursor = self.mydb.cursor(dictionary=True)
        select_stmt = "SELECT tags.tag_group_id, tags.edge_type FROM tags WHERE puzzle_id = %s"
        cursor.execute(select_stmt, (puzzle.id,))
        rows = cursor.fetchall()
        for row in rows:
            puzzle.tags.append(row['tag_group_id'])
            puzzle.tag_edges[row['tag_group_id']] = row['edge_type'] or "feeds"
        cursor.close()

    def get_additional_sheets(self, puzzle):
        cursor = self.mydb.cursor(dictionary=True)
        select_stmt = "SELECT * FROM additional_sheets WHERE puzzle_id = %s"
        cursor.execute(select_stmt, (puzzle.id,))
        rows = cursor.fetchall()
        for row in rows:
            sheet = AdditionalSheetData()
            for attr, value in sheet.__dict__.items():
                setattr(sheet, attr, row.get(attr, None))
            puzzle.additional_sheets.append(sheet)
        cursor.close()

    def tag_puzzle_with_edge(self, puzzle_id, tag_group_id, edge_type):
        """M7: idempotent upsert of a membership edge with typed edge_type.
        Relies on migration 005's UNIQUE KEY (tag_group_id, puzzle_id)."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s)"
                " ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)",
                (tag_group_id, puzzle_id, edge_type),
            )
        finally:
            cursor.close()
        self.mydb.commit()

    def delete_tag(self, puzzle_id, tag_group_id):
        """Delete a single tag membership row."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "DELETE FROM tags WHERE puzzle_id = %s AND tag_group_id = %s",
                (puzzle_id, tag_group_id),
            )
        finally:
            cursor.close()
        self.mydb.commit()

    def bulk_update_edge_type(self, tag_group_id, new_edge):
        """Update every member of a group to the same edge_type."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "UPDATE tags SET edge_type = %s WHERE tag_group_id = %s",
                (new_edge, tag_group_id),
            )
        finally:
            cursor.close()
        self.mydb.commit()

    def commit(self, puzzle):
        super(MySQLPuzzleJsonDb, self).commit(puzzle)
        cursor = self.mydb.cursor(dictionary=True)
        select_stmt = "SELECT tag_group_id FROM tags WHERE puzzle_id = %s"
        cursor.execute(select_stmt, (puzzle.id,))
        rows = cursor.fetchall()
        database_tags = []
        for row in rows:
            database_tags.append(row['tag_group_id'])
        tags_to_remove = set(database_tags) - set(puzzle.tags)
        if len(tags_to_remove) > 0:
            placeholders = ",".join(["%s"] * len(tags_to_remove))
            delete_stmt = f"DELETE FROM tags WHERE tag_group_id IN ({placeholders}) AND puzzle_id = %s"
            cursor.execute(delete_stmt, (*tags_to_remove, puzzle.id))
        tags_to_add = set(puzzle.tags) - set(database_tags)
        if len(tags_to_add) > 0:
            insert_stmt = "INSERT INTO tags (tag_group_id, puzzle_id) VALUES " + \
                ",".join(["(%s, %s)"] * len(tags_to_add))
            params = []
            for tag in tags_to_add:
                params.extend((tag, puzzle.id))
            cursor.execute(insert_stmt, params)
        cursor.close()
        self.mydb.commit()

    def delete(self, puzzle_id):
        super(MySQLPuzzleJsonDb, self).delete(puzzle_id)
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("DELETE FROM tags WHERE puzzle_id = %s", (puzzle_id,))
        cursor.close()

class MySQLChannelActivityDb(_MySQLBaseDb):
    TABLE_NAME = 'channel_activity'

    def record(self, channel_id, guild_id, at, author_id, author_name):
        """Upsert the most recent human (non-bot) message seen in a channel."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "INSERT INTO channel_activity "
                "(channel_id, guild_id, last_human_message_at, last_author_id, last_author_name) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "guild_id=VALUES(guild_id), "
                "last_human_message_at=VALUES(last_human_message_at), "
                "last_author_id=VALUES(last_author_id), "
                "last_author_name=VALUES(last_author_name)",
                (channel_id, guild_id, at, author_id, author_name),
            )
        finally:
            cursor.close()
        self.mydb.commit()

    def get_for_guild(self, guild_id) -> dict:
        """Return {channel_id: row} of latest activity for every channel in a guild."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM channel_activity WHERE guild_id = %s", (guild_id,))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return {row['channel_id']: row for row in rows}


class MySQLEvidenceDb(_MySQLBaseDb):
    TABLE_NAME = 'evidence_items'

    def log(self, hunt_id: int, source_puzzle_id: int, note: str) -> 'EvidenceData':
        """Insert one evidence item and return it."""
        import datetime
        now = datetime.datetime.utcnow()
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(
                "INSERT INTO evidence_items (hunt_id, source_puzzle_id, note, created_at)"
                " VALUES (%s, %s, %s, %s)",
                (hunt_id, source_puzzle_id, note, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        self.mydb.commit()
        e = EvidenceData()
        e.id = new_id
        e.hunt_id = hunt_id
        e.source_puzzle_id = source_puzzle_id
        e.note = note
        e.created_at = now
        return e

    def get_all(self, hunt_id: int, source_puzzle_id=None, target_puzzle_id=None):
        """Return evidence items for a hunt, optionally filtered."""
        where = ["hunt_id = %s"]
        params = [hunt_id]
        if source_puzzle_id is not None:
            where.append("source_puzzle_id = %s")
            params.append(source_puzzle_id)
        if target_puzzle_id == "unassigned":
            where.append("target_puzzle_id IS NULL")
        elif target_puzzle_id is not None:
            where.append("target_puzzle_id = %s")
            params.append(target_puzzle_id)
        sql = "SELECT * FROM evidence_items WHERE " + " AND ".join(where) + " ORDER BY created_at ASC"
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [EvidenceData.import_dict(r) for r in rows]

    def set_target(self, item_id: int, target_puzzle_id):
        """Set or clear target_puzzle_id on an item. Returns updated EvidenceData."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(
                "UPDATE evidence_items SET target_puzzle_id = %s WHERE id = %s",
                (target_puzzle_id, item_id),
            )
            cursor.execute("SELECT * FROM evidence_items WHERE id = %s", (item_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        self.mydb.commit()
        return EvidenceData.import_dict(row) if row else None


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
                    "UNION SELECT category_id as channel_id, 'Group' as channel_type FROM tag_group "
                    "UNION SELECT channel_id, 'Puzzle' as channel_type FROM puzzles "
                ") channel_types WHERE channel_id=%s")
        data = (channel_id,)
        try:
            cursor.execute(stmt, data)
            row = cursor.fetchone()
        finally:
            cursor.close()
        channel_type = None
        if row:
            channel_type = row['channel_type']
        return channel_type

    def get(self, guild_id: int) -> GuildSettings:
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM guilds WHERE guild_id = %s", (guild_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row:
            return GuildSettings.import_dict(row)
        return None

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