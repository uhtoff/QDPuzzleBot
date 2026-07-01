import datetime
import random
import string
import json
import logging
from pathlib import Path
from typing import List

import pytz
from bot.core.discord_channels import clean_name
from .puzzle_data import PuzzleData, MissingPuzzleError, AdditionalSheetData
from .tag_group_data import TagGroupData
from .tag_group_category_data import TagGroupCategoryData
from .rollup_data import RollupData
from .hunt_data import HuntData
from .puzzle_settings import GuildSettings
from .evidence_data import EvidenceData

logger = logging.getLogger(__name__)


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


class MySQLTagGroupCategoryDb(_MySQLBaseDb):
    TABLE_NAME = 'tag_group_category'

    def get_for_group(self, group_id):
        """All category rows for a group, ordered by role then seq."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            # unsolved categories first, then solved; each by seq
            cursor.execute(
                "SELECT * FROM tag_group_category WHERE group_id = %s "
                "ORDER BY FIELD(`role`, 'unsolved', 'solved'), `seq` ASC",
                (group_id,),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [TagGroupCategoryData(**row) for row in rows]

    def get_by_category(self, category_id):
        """The single row owning a category, or None."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT * FROM tag_group_category WHERE category_id = %s LIMIT 1",
                (category_id,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
        return TagGroupCategoryData(**row) if row else None

    def delete_for_group(self, group_id):
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "DELETE FROM tag_group_category WHERE group_id = %s", (group_id,)
            )
        finally:
            cursor.close()
        self.mydb.commit()


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
    SPECIAL_ATTR = ['tags', 'additional_sheets', 'tag_edges', 'is_target', 'is_minimeta']

    def construct_puzzle(self, row):
        puzzle = PuzzleData.import_dict(row)
        self.get_tags(puzzle)
        self.get_additional_sheets(puzzle)
        self.get_is_target(puzzle)
        self.get_is_minimeta(puzzle)
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
        cursor.execute(
            "SELECT puzzles.* FROM puzzles WHERE puzzles.id IN "
            "(SELECT puzzle_id FROM tags WHERE tag_group_id = %s)",
            (tag_group_id,),
        )
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

    def check_duplicates_in_hunt(self, name, hunt_id, exclude_puzzle_id=None) -> bool:
        cursor = self.mydb.cursor(dictionary = True)
        query = ("SELECT 1 FROM puzzles "
                 "WHERE ( puzzles.name = %s OR puzzles.channel_name= %s ) AND puzzles.hunt_id = %s")
        params = [name, clean_name(name), hunt_id]
        if exclude_puzzle_id is not None:
            # A rename must not clash with the puzzle being renamed (its own row):
            # adding punctuation/emoji to a name would otherwise look like a duplicate.
            query += " AND puzzles.id != %s"
            params.append(exclude_puzzle_id)
        query += " LIMIT 1"
        cursor.execute(query, tuple(params))
        found = cursor.fetchone() is not None
        cursor.close()
        return found

    def get_unassigned(self, hunt_id) -> List[PuzzleData]:
        """Return feeder puzzles with no `feeds` edge to any meta (Unassigned Feeders tab).

        A puzzle whose answer feeds nothing is an unassigned feeder regardless of any
        informs / none membership. Excludes target puzzles (B2 derivation) and mini-meta feeders.
        """
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(
            "SELECT p.* FROM puzzles p"
            " WHERE p.hunt_id = %s"
            "   AND NOT EXISTS (SELECT 1 FROM tag_group mt WHERE mt.target_puzzle_id = p.id)"
            "   AND p.parent_puzzle_id IS NULL"   # mini-meta feeders are assigned
            "   AND NOT EXISTS ("
            "       SELECT 1 FROM tags t"
            "       JOIN tag_group r ON t.tag_group_id = r.id"
            "       WHERE t.puzzle_id = p.id"
            "         AND r.target_puzzle_id > 0"
            "         AND t.edge_type = 'feeds'"
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
            gid = row['tag_group_id']
            if gid not in puzzle.tags:
                puzzle.tags.append(gid)
            puzzle.tag_edges.setdefault(gid, set()).add(row['edge_type'] or "feeds")
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

    def get_is_target(self, puzzle):
        """Derive is-target: True iff some tag_group targets this puzzle.

        Eager-loaded here (the single read seam) so is-meta reads stay free
        attribute accesses. Lookup on tag_group.target_puzzle_id."""
        cursor = self.mydb.cursor()
        cursor.execute(
            "SELECT 1 FROM tag_group WHERE target_puzzle_id = %s LIMIT 1",
            (puzzle.id,),
        )
        puzzle.is_target = cursor.fetchone() is not None
        cursor.close()

    def get_is_minimeta(self, puzzle):
        """Derive is-minimeta: True iff this puzzle has mini-children.

        A minimeta is keyed by parent_puzzle_id (the puzzle-keyed roll-up path),
        not tag_group.target_puzzle_id, so it is invisible to get_is_target even
        though its tab is promoted to the same shifted target geometry. Eager-load
        it here, the single read seam, so is-minimeta reads stay free attribute
        accesses (e.g. _header_shift needs it to offset solution/progress writes)."""
        cursor = self.mydb.cursor()
        cursor.execute(
            "SELECT 1 FROM puzzles WHERE parent_puzzle_id = %s LIMIT 1",
            (puzzle.id,),
        )
        puzzle.is_minimeta = cursor.fetchone() is not None
        cursor.close()

    def tag_puzzle_with_edge(self, puzzle_id, tag_group_id, edge_type):
        """Add one membership edge (idempotent). With migration 020's
        UNIQUE (tag_group_id, puzzle_id, edge_type), each edge is its own row, so a
        puzzle may hold several edges to one group."""
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
        """Delete all membership edges for the pair (remove whole membership)."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "DELETE FROM tags WHERE puzzle_id = %s AND tag_group_id = %s",
                (puzzle_id, tag_group_id),
            )
        finally:
            cursor.close()
        self.mydb.commit()

    def delete_tag_edge(self, puzzle_id, tag_group_id, edge_type):
        """Delete a single typed edge row (leaves the pair's other edges intact)."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "DELETE FROM tags WHERE puzzle_id = %s AND tag_group_id = %s AND edge_type = %s",
                (puzzle_id, tag_group_id, edge_type),
            )
        finally:
            cursor.close()
        self.mydb.commit()

    def bulk_update_edge_type(self, tag_group_id, new_edge):
        """Convert members' feeds/informs/none edges to new_edge for a group, without
        creating duplicate (tag_group_id, puzzle_id, new_edge) rows. The target's
        'target' self-edge is left untouched here (bulk_re_edge re-pins it)."""
        cursor = self.mydb.cursor()
        try:
            # Puzzles that currently contribute (feeds/informs/none) to this group.
            cursor.execute(
                "SELECT DISTINCT puzzle_id FROM tags "
                "WHERE tag_group_id = %s AND edge_type IN ('feeds','informs','none')",
                (tag_group_id,),
            )
            pids = [r[0] for r in cursor.fetchall()]
            if pids:
                placeholders = ",".join(["%s"] * len(pids))
                cursor.execute(
                    f"DELETE FROM tags WHERE tag_group_id = %s "
                    f"AND edge_type IN ('feeds','informs','none') "
                    f"AND puzzle_id IN ({placeholders})",
                    (tag_group_id, *pids),
                )
                cursor.executemany(
                    "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)",
                    [(tag_group_id, pid, new_edge) for pid in pids],
                )
        finally:
            cursor.close()
        self.mydb.commit()

    def commit(self, puzzle):
        super(MySQLPuzzleJsonDb, self).commit(puzzle)
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(
            "SELECT tag_group_id, edge_type FROM tags WHERE puzzle_id = %s",
            (puzzle.id,),
        )
        db_pairs = {(r['tag_group_id'], r['edge_type']) for r in cursor.fetchall()}
        mem_pairs = {
            (gid, edge)
            for gid, edges in puzzle.tag_edges.items()
            for edge in edges
        }
        for gid, edge in db_pairs - mem_pairs:
            cursor.execute(
                "DELETE FROM tags WHERE puzzle_id = %s AND tag_group_id = %s AND edge_type = %s",
                (puzzle.id, gid, edge),
            )
        for gid, edge in mem_pairs - db_pairs:
            cursor.execute(
                "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)",
                (gid, puzzle.id, edge),
            )
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

    def get_by_id(self, item_id: int):
        """Return one EvidenceData by id, or None if missing."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM evidence_items WHERE id = %s", (item_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return EvidenceData.import_dict(row) if row else None

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

    def delete(self, item_id: int) -> None:
        """Delete an evidence item by id. No-op if the id does not exist."""
        cursor = self.mydb.cursor()
        try:
            cursor.execute("DELETE FROM evidence_items WHERE id = %s", (item_id,))
        finally:
            cursor.close()
        self.mydb.commit()

    def update_note(self, item_id: int, note: str):
        """Update the note text for an evidence item. Returns updated EvidenceData or None."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(
                "UPDATE evidence_items SET note = %s WHERE id = %s",
                (note, item_id),
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
            rows = cursor.fetchall()
        finally:
            cursor.close()
        # A channel id can legitimately match more than one role: promoting a
        # puzzle to a metapuzzle reuses its puzzle channel as the group's
        # category_id, so the union returns both 'Puzzle' and 'Group'. We must
        # consume the whole result set — leaving rows unread makes cursor.close()
        # raise mysql.connector "Unread result found", which aborts the command's
        # before-hook and looks like the bot hanging. Prefer the most specific
        # role so a command in the puzzle channel routes through the puzzle
        # branch (which then promotes to the group type via target_puzzle_id).
        types = {row['channel_type'] for row in rows}
        for channel_type in ('Puzzle', 'Hunt', 'Group'):
            if channel_type in types:
                return channel_type
        return None

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
        # Keep the read cache coherent: a prior get_cached() may have cached a
        # None (no row yet) or a stale copy, which would otherwise shadow this
        # freshly-written row until process restart.
        self.cached_settings[settings.guild_id] = settings