import datetime
import random
import string
import json
import logging
import unicodedata
from mysql.connector.errors import IntegrityError
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from . import MySQLReconnector

from bot.core.discord_channels import clean_channel_name
from .puzzle_data import PuzzleData, MissingPuzzleError, AdditionalSheetData
from .tag_group_data import TagGroupData
from .tag_group_category_data import TagGroupCategoryData
from .hunt_category_data import HuntCategoryData
from .rollup_data import RollupData
from .hunt_data import HuntData
from .puzzle_settings import GuildSettings
from .evidence_data import EvidenceData
from .feeder_pool_data import FeederPoolItemData, FeederClaimData
from .media_data import MediaData
from .canvas_data import CanvasRegionData, HuntCanvasData
from .markup_data import MediaMarkupData
from .puzzle_image_data import PuzzleImageData

logger = logging.getLogger(__name__)

#: MariaDB/MySQL ER_DUP_ENTRY -- a UNIQUE index rejected the write.
DUPLICATE_ENTRY_ERRNO = 1062

#: feeder_pool_items.text is varchar(255) -- MariaDB is strict, so a longer
#: line raises DataError (1406) rather than silently truncating.
MAX_FEEDER_TEXT = 255


def dedup_key(text):
    """The key two given feeders collide on.

    Must match the collation of `feeder_pool_items.text` (utf8mb4_uca1400_ai_ci --
    case- and accent-insensitive), or Python's dedup and the UNIQUE index disagree
    and add_many raises mid-paste, committing some lines and dropping the rest.

    Lives here rather than in bot/core/feeder_pool.py because that module is
    required to stay import-free (pure derivation over plain data), and this
    needs `unicodedata` to strip accents.
    """
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch))
    return stripped.casefold()


class _MySQLBaseDb:
    TABLE_NAME: str
    SPECIAL_ATTR: List[str] = []

    #: Columns holding a code minted by generate_uid and protected by a UNIQUE
    #: index (migration 040). generate_uid SELECTs for a clash and then INSERTs,
    #: so two concurrent creates can both pass the check -- the index rejects the
    #: loser, and commit() re-mints and retries rather than surfacing a 500.
    GENERATED_UNIQUE_FIELDS: tuple = ()
    #: Total insert attempts before giving up. Each retry draws a fresh random
    #: code, so a genuine clash is resolved on the first or second try; a higher
    #: count would only mask an index that is wedged for some other reason.
    GENERATED_CODE_ATTEMPTS = 4

    def __init__(self, mydb: "MySQLReconnector"):
        self.mydb = mydb

    def _duplicate_generated_field(self, exc):
        """The generated column a duplicate-key error is complaining about, or
        None when the error is not one re-minting could fix."""
        if getattr(exc, "errno", None) != DUPLICATE_ENTRY_ERRNO:
            return None
        message = str(exc)
        for field in self.GENERATED_UNIQUE_FIELDS:
            # The index is named after its column (see migration 040), so either
            # spelling identifies it; require a name match so that a duplicate on
            # some *other* unique column is not silently retried.
            if f"uq_{self.TABLE_NAME}_{field}" in message or f"'{field}'" in message:
                return field
        return None

    def commit(self, object_to_commit, /):
        for attempt in range(1, self.GENERATED_CODE_ATTEMPTS + 1):
            try:
                return self._commit_once(object_to_commit)
            except IntegrityError as exc:
                field = self._duplicate_generated_field(exc)
                if field is None or attempt == self.GENERATED_CODE_ATTEMPTS:
                    raise
                reminted = self.generate_uid(field)
                logger.warning(
                    "`%s`.`%s` collided on %r (lost a create race); re-minted as %r",
                    self.TABLE_NAME, field, getattr(object_to_commit, field), reminted,
                )
                setattr(object_to_commit, field, reminted)

    def _commit_once(self, object_to_commit, /):
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
            if cursor.rowcount == 0 and not self._row_exists(database_id):
                # The row really is gone -- typically cascade-deleted underneath a
                # stale object (a snapshot held across a delete_round, or a web
                # delete landing mid bang-command). Deliberately a log, not a raise:
                # that path is legitimate, and raising would abort a command after
                # its Discord-side work is already done.
                logger.error(
                    "UPDATE on `%s` id=%s matched no row; the write was dropped",
                    self.TABLE_NAME, database_id,
                )
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

    def _row_exists(self, database_id) -> bool:
        """Does a row with this id still exist? Used to tell a genuinely missing row
        from a no-op UPDATE.

        `rowcount == 0` does NOT mean "no row matched": mysql-connector does not set
        CLIENT_FOUND_ROWS, so MariaDB reports 0 for an UPDATE that matched a row and
        changed no column. That is the COMMON case, not a rare one -- tags/tag_edges
        are in SPECIAL_ATTR, so any commit that changes only membership (every
        retag, every evidence link, tag_with_edge/untag_from) touches no scalar
        column and always reports 0. Logging on rowcount alone would fire an ERROR
        claiming data loss on every successful retag, and bury real errors in
        discord.log. One extra indexed SELECT, only on the already-zero path, buys
        an answer that is actually true.
        """
        return self._fetch_one(
            f"SELECT 1 FROM `{self.TABLE_NAME}` WHERE `id` = %s", (database_id,)
        ) is not None

    def _exec(self, sql, params=()):
        """Execute a statement whose result nobody reads (INSERT/UPDATE/DELETE)."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(sql, params)
        finally:
            cursor.close()

    def _fetch_one(self, sql, params=()):
        """Execute a SELECT and return its single row (a dict), or None."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(sql, params)
            return cursor.fetchone()
        finally:
            cursor.close()

    def _fetch_all(self, sql, params=()):
        """Execute a SELECT and return all its rows (a list of dicts)."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(sql, params)
            return cursor.fetchall() or []
        finally:
            cursor.close()

    def delete(self, delete_id, /):
        """Delete single puzzle from database by database id"""
        self._exec(f"DELETE FROM `{self.TABLE_NAME}` WHERE id = %s", (delete_id,))
        # if deleted_rows != 1:S
        #     raise MissingDataError(f"Unable to find puzzle {puzzle_id} for {round_id}")

    def check_duplicates(self, value, field = 'name'):
        """Ensures no duplicate name by default but can check any field if passed"""
        row = self._fetch_one(f"SELECT 1 FROM `{self.TABLE_NAME}` WHERE `{field}` = %s LIMIT 1", (value,))
        return row is not None

    # Capital letters + digits only — this is a bang-command shorthand users type
    # by hand, so no lowercase and nothing else ambiguous/punctuation-like.
    UID_CHARS = string.ascii_uppercase + string.digits

    def generate_uid(self, field = 'id', chars = 6, input = None):
        code = ""
        if input is not None:
            split_input = input.split()
            for word in split_input:
                alnum = "".join(ch for ch in word if ch.isascii() and ch.isalnum())
                if alnum:
                    code += alnum[:2].upper()
            code = code[:6]

        while len(code) < chars:
            code += random.choice(self.UID_CHARS)

        loop_count = 0

        while self.check_duplicates(code, field):
            loop_count += 1
            if loop_count < 26:
                code = code[:5] + random.choice(self.UID_CHARS)
            elif loop_count < 512:
                code = code[:4]
                while len(code) < 6:
                    code += random.choice(self.UID_CHARS)
            else:
                code = code[:3]
                while len(code) < 6:
                    code += random.choice(self.UID_CHARS)

        return code

    def check_duplicates_in_hunt(self, name, hunt_data: HuntData, exclude_group_id=None) -> bool:
        query = f"SELECT 1 FROM `{self.TABLE_NAME}` WHERE `name` = %s AND `hunt_id` = %s"
        params = [name, hunt_data.id]
        if exclude_group_id is not None:
            # A rename must not clash with the group being renamed (its own row):
            # adding punctuation/emoji to a name would otherwise look like a duplicate.
            query += " AND `id` != %s"
            params.append(exclude_group_id)
        query += " LIMIT 1"
        row = self._fetch_one(query, tuple(params))
        return row is not None

class MySQLHuntJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'hunts'
    GENERATED_UNIQUE_FIELDS = ('uid',)
    def get_by_attr(self,**kwargs):
        """Retrieve Hunt by attribute.  Only first sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            row = self._fetch_one(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s", (value,))
            if row:
                return HuntData.import_dict(row)
            logger.info(f"Unable to find hunt for {keyword} - {value}")
            return None

class MySQLTagGroupJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'tag_group'
    GENERATED_UNIQUE_FIELDS = ('group_code',)
    def get_by_attr(self,**kwargs):
        """Retrieve TagGroup by attribute.  Only last sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            row = self._fetch_one(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s ORDER BY `id` ASC LIMIT 1", (value,))
            if row:
                return TagGroupData.import_dict(row)
            logger.info(f"Unable to find TagGroup for {keyword} - {value}")
            return None

    def get_all(self, hunt_id: int) -> List[TagGroupData]:
        """Retrieve all tag groups for hunt"""
        tag_group_datas = []
        rows = self._fetch_all("SELECT * FROM tag_group WHERE hunt_id = %s", (hunt_id,))
        for row in rows:
            tag_group_datas.append(TagGroupData.import_dict(row))
        return tag_group_datas

    def delete(self, tag_group_id, /):
        super(MySQLTagGroupJsonDb, self).delete(tag_group_id)
        self._exec("DELETE FROM tags WHERE tag_group_id = %s", (tag_group_id,))


class MySQLTagGroupCategoryDb(_MySQLBaseDb):
    TABLE_NAME = 'tag_group_category'

    def make_row(self, owner_id, category_id, role, seq):
        """Build an (uncommitted) row for a category owned by tag_group ``owner_id``.
        Lets category_layout's generic split/allocate code construct a row without
        depending on which owner (tag_group vs hunt) it's building for."""
        return TagGroupCategoryData(group_id=owner_id, category_id=category_id, role=role, seq=seq)

    def get_for_group(self, group_id):
        """All category rows for a group, ordered by role then seq."""
        # unsolved categories first, then solved; each by seq
        rows = self._fetch_all(
            "SELECT * FROM tag_group_category WHERE group_id = %s "
            "ORDER BY FIELD(`role`, 'unsolved', 'solved'), `seq` ASC",
            (group_id,),
        )
        return [TagGroupCategoryData(**row) for row in rows]

    def get_by_category(self, category_id):
        """The single row owning a category, or None."""
        row = self._fetch_one(
            "SELECT * FROM tag_group_category WHERE category_id = %s LIMIT 1",
            (category_id,),
        )
        return TagGroupCategoryData(**row) if row else None

    def delete_for_group(self, group_id):
        self._exec(
            "DELETE FROM tag_group_category WHERE group_id = %s", (group_id,)
        )


class MySQLHuntCategoryDb(_MySQLBaseDb):
    """The hunt-level counterpart to MySQLTagGroupCategoryDb: tracks the extra
    categories a hunt's general container owns once it splits/overflows past
    the soft limit. Same shape and method names (duck-typed) so category_layout's
    generic split/allocate/pick/delete functions work unchanged against either DAO.
    """
    TABLE_NAME = 'hunt_category'

    def make_row(self, owner_id, category_id, role, seq):
        return HuntCategoryData(hunt_id=owner_id, category_id=category_id, role=role, seq=seq)

    def get_for_group(self, hunt_id):
        """All category rows for a hunt, ordered by role then seq."""
        rows = self._fetch_all(
            "SELECT * FROM hunt_category WHERE hunt_id = %s "
            "ORDER BY FIELD(`role`, 'unsolved', 'solved'), `seq` ASC",
            (hunt_id,),
        )
        return [HuntCategoryData(**row) for row in rows]

    def get_by_category(self, category_id):
        """The single row owning a category, or None."""
        row = self._fetch_one(
            "SELECT * FROM hunt_category WHERE category_id = %s LIMIT 1",
            (category_id,),
        )
        return HuntCategoryData(**row) if row else None

    def delete_for_group(self, hunt_id):
        self._exec(
            "DELETE FROM hunt_category WHERE hunt_id = %s", (hunt_id,)
        )


class MySQLAdditionalSheetsDb(_MySQLBaseDb):
    TABLE_NAME = 'additional_sheets'

    def get_by_puzzle(self, puzzle_id):
        return self._fetch_all("SELECT * FROM additional_sheets WHERE puzzle_id = %s ", (puzzle_id,))

    def get_by_hunt(self, hunt_id):
        """Return the hunt's OWN working sheets -- those with no puzzle.

        A puzzle's sheets belong to the hunt transitively and are loaded through
        the puzzle (construct_puzzle -> get_additional_sheets); they are
        deliberately excluded here. Ordered by id so !list_sheets and the index
        !remove_sheet takes from it always agree.
        """
        rows = self._fetch_all(
            "SELECT * FROM additional_sheets"
            " WHERE hunt_id = %s AND puzzle_id IS NULL ORDER BY id ASC",
            (hunt_id,),
        )
        sheets = []
        for row in rows:
            sheet = AdditionalSheetData()
            for attr in sheet.__dict__:
                if attr in row:
                    setattr(sheet, attr, row[attr])
            sheets.append(sheet)
        return sheets

    def get_names_by_ids(self, sheet_ids):
        """Return a {sheet_id: puzzle_name} map for the given ids, in one query.

        Mirrors MySQLPuzzleJsonDb.get_names_by_ids -- rendering a table calls this
        once for the whole batch rather than once per row.
        """
        ids = [int(i) for i in sheet_ids if i]
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        rows = self._fetch_all(
            f"SELECT id, puzzle_name FROM additional_sheets WHERE id IN ({placeholders})",
            tuple(ids),
        )
        return {r["id"]: r["puzzle_name"] for r in rows}


class MySQLRollupJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'rollups'

    def get_by_attr(self, **kwargs):
        """Retrieve a Rollup by attribute (e.g. round_id=). Last attr wins."""
        keyword, value = kwargs.popitem()
        if keyword:
            row = self._fetch_one(
                f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s ORDER BY `id` ASC LIMIT 1",
                (value,),
            )
            if row:
                return RollupData.import_dict(row)
            return None

    def get_all_from_hunt(self, hunt_id) -> List[RollupData]:
        rows = self._fetch_all(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `hunt_id` = %s", (hunt_id,))
        return [RollupData.import_dict(row) for row in rows]

class MySQLPuzzleJsonDb(_MySQLBaseDb):
    TABLE_NAME = 'puzzles'
    SPECIAL_ATTR = ['tags', 'additional_sheets', 'tag_edges', 'is_target', 'is_minimeta']

    def construct_puzzle(self, row):
        return self.construct_puzzles([row])[0]

    def construct_puzzles(self, rows) -> List[PuzzleData]:
        """Hydrate many rows in a fixed 4 queries, not 4 *per row* (T5).

        The per-puzzle path ran get_tags/get_additional_sheets/get_is_target/
        get_is_minimeta for every row, so a 100-puzzle hunt cost ~400 sequential
        round-trips -- and these are synchronous calls on the gateway thread, so
        the event loop is blocked for all of them. The list paths below all come
        through here; the four hydrators fan the whole batch into one IN (...)
        query each.
        """
        puzzles = [PuzzleData.import_dict(row) for row in rows]
        if not puzzles:
            return puzzles
        self._hydrate_tags(puzzles)
        self._hydrate_additional_sheets(puzzles)
        self._hydrate_is_target(puzzles)
        self._hydrate_is_minimeta(puzzles)
        return puzzles

    def get(self, guild_id, puzzle_id, round_id, hunt_id) -> PuzzleData:
        """Retrieve single puzzle from database"""
        row = self._fetch_one("SELECT * FROM puzzles WHERE channel_id = %s", (puzzle_id,))
        if row:
            return self.construct_puzzle(row)
        raise MissingPuzzleError(f"Unable to find puzzle {puzzle_id} for {round_id}")

    def get_by_attr(self,**kwargs):
        """Retrieve puzzle by attribute.  Only first sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            row = self._fetch_one(f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s", (value,))
            if row:
                return self.construct_puzzle(row)
            logger.info(f"Unable to find puzzle for {keyword} - {value}")
            return None

    def get_names_by_ids(self, ids) -> dict:
        """Return {puzzle_id: name} for the given ids, skipping unknown ids.

        A single query with no construct_puzzle hydration — for callers that only
        need names (e.g. resolve_puzzle_names), not full PuzzleData objects.
        """
        ids = list(ids)
        if not ids:
            return {}
        placeholders = ",".join(["%s"] * len(ids))
        rows = self._fetch_all(f"SELECT id, name FROM `{self.TABLE_NAME}` WHERE id IN ({placeholders})", tuple(ids))
        return {row["id"]: row["name"] for row in rows}

    def get_all(self, guild_id, hunt_id="*") -> List[PuzzleData]:
        """Retrieve all puzzles from database"""
        rows = self._fetch_all("SELECT puzzles.* FROM puzzles "
                       "LEFT JOIN hunts ON puzzles.hunt_id = hunts.id "
                       "WHERE hunts.guild_id = %s", (guild_id,))
        return PuzzleData.sort_by_puzzle_start(self.construct_puzzles(rows))

    def get_all_from_hunt(self, hunt_id) -> List[PuzzleData]:
        """Retrieve all puzzles from database"""
        rows = self._fetch_all("SELECT puzzles.* FROM puzzles "
                       "WHERE hunt_id = %s", (hunt_id,))
        return self.construct_puzzles(rows)

    def get_all_from_tag_group(self, tag_group_id) -> List[PuzzleData]:
        """Retrieve all puzzles from database"""
        rows = self._fetch_all(
            "SELECT puzzles.* FROM puzzles WHERE puzzles.id IN "
            "(SELECT puzzle_id FROM tags WHERE tag_group_id = %s)",
            (tag_group_id,),
        )
        return PuzzleData.sort_by_puzzle_start(self.construct_puzzles(rows))

    def get_children(self, parent_puzzle_id: int):
        """Return all mini-puzzles whose parent_puzzle_id matches."""
        rows = self._fetch_all(
            "SELECT * FROM puzzles WHERE parent_puzzle_id = %s ORDER BY id ASC",
            (parent_puzzle_id,),
        )
        return self.construct_puzzles(rows)

    # Table-specific signature; never called through a base-typed reference.
    def check_duplicates_in_hunt(self, name, hunt_id, exclude_puzzle_id=None) -> bool:  # pyright: ignore[reportIncompatibleMethodOverride]
        query = ("SELECT 1 FROM puzzles "
                 "WHERE ( puzzles.name = %s OR puzzles.channel_name= %s ) AND puzzles.hunt_id = %s")
        params = [name, clean_channel_name(name), hunt_id]
        if exclude_puzzle_id is not None:
            # A rename must not clash with the puzzle being renamed (its own row):
            # adding punctuation/emoji to a name would otherwise look like a duplicate.
            query += " AND puzzles.id != %s"
            params.append(exclude_puzzle_id)
        query += " LIMIT 1"
        row = self._fetch_one(query, tuple(params))
        return row is not None

    def get_unassigned(self, hunt_id) -> List[PuzzleData]:
        """Return feeder puzzles with no `feeds` edge to any meta (Unassigned Feeders tab).

        A puzzle whose answer feeds nothing is an unassigned feeder regardless of any
        informs / none membership. Excludes target puzzles (B2 derivation) and mini-meta feeders.
        """
        rows = self._fetch_all(
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
        return self.construct_puzzles(rows)

    @staticmethod
    def _placeholders(count: int) -> str:
        return ", ".join(["%s"] * count)

    def get_tags(self, puzzle):
        self._hydrate_tags([puzzle])

    def _hydrate_tags(self, puzzles):
        by_id = {p.id: p for p in puzzles}
        rows = self._fetch_all(
            "SELECT tags.puzzle_id, tags.tag_group_id, tags.edge_type FROM tags"
            f" WHERE puzzle_id IN ({self._placeholders(len(by_id))})",
            tuple(by_id),
        )
        for row in rows:
            puzzle = by_id[row['puzzle_id']]
            gid = row['tag_group_id']
            if gid not in puzzle.tags:
                puzzle.tags.append(gid)
            puzzle.tag_edges.setdefault(gid, set()).add(row['edge_type'] or "feeds")

    def get_additional_sheets(self, puzzle):
        self._hydrate_additional_sheets([puzzle])

    def _hydrate_additional_sheets(self, puzzles):
        by_id = {p.id: p for p in puzzles}
        rows = self._fetch_all(
            "SELECT * FROM additional_sheets"
            f" WHERE puzzle_id IN ({self._placeholders(len(by_id))})"
            " ORDER BY id ASC",
            tuple(by_id),
        )
        for row in rows:
            sheet = AdditionalSheetData()
            for attr in list(sheet.__dict__):
                setattr(sheet, attr, row.get(attr, None))
            by_id[row['puzzle_id']].additional_sheets.append(sheet)

    def get_is_target(self, puzzle):
        """Derive is-target: True iff some tag_group targets this puzzle.

        Eager-loaded here (the single read seam) so is-meta reads stay free
        attribute accesses. Lookup on tag_group.target_puzzle_id."""
        self._hydrate_is_target([puzzle])

    def _hydrate_is_target(self, puzzles):
        by_id = {p.id: p for p in puzzles}
        rows = self._fetch_all(
            "SELECT DISTINCT target_puzzle_id FROM tag_group"
            f" WHERE target_puzzle_id IN ({self._placeholders(len(by_id))})",
            tuple(by_id),
        )
        targeted = {row['target_puzzle_id'] for row in rows}
        for pid, puzzle in by_id.items():
            puzzle.is_target = pid in targeted

    def get_is_minimeta(self, puzzle):
        """Derive is-minimeta: True iff this puzzle has mini-children.

        A minimeta is keyed by parent_puzzle_id (the puzzle-keyed roll-up path),
        not tag_group.target_puzzle_id, so it is invisible to get_is_target even
        though its tab is promoted to the same shifted target geometry. Eager-load
        it here, the single read seam, so is-minimeta reads stay free attribute
        accesses (e.g. _header_shift needs it to offset solution/progress writes)."""
        self._hydrate_is_minimeta([puzzle])

    def _hydrate_is_minimeta(self, puzzles):
        by_id = {p.id: p for p in puzzles}
        rows = self._fetch_all(
            "SELECT DISTINCT parent_puzzle_id FROM puzzles"
            f" WHERE parent_puzzle_id IN ({self._placeholders(len(by_id))})",
            tuple(by_id),
        )
        parents = {row['parent_puzzle_id'] for row in rows}
        for pid, puzzle in by_id.items():
            puzzle.is_minimeta = pid in parents

    def tag_puzzle_with_edge(self, puzzle_id, tag_group_id, edge_type):
        """Add one membership edge (idempotent). With migration 020's
        UNIQUE (tag_group_id, puzzle_id, edge_type), each edge is its own row, so a
        puzzle may hold several edges to one group."""
        self._exec(
            "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s)"
            " ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)",
            (tag_group_id, puzzle_id, edge_type),
        )

    def delete_tag(self, puzzle_id, tag_group_id):
        """Delete all membership edges for the pair (remove whole membership)."""
        self._exec(
            "DELETE FROM tags WHERE puzzle_id = %s AND tag_group_id = %s",
            (puzzle_id, tag_group_id),
        )

    def delete_tag_edge(self, puzzle_id, tag_group_id, edge_type):
        """Delete a single typed edge row (leaves the pair's other edges intact)."""
        self._exec(
            "DELETE FROM tags WHERE puzzle_id = %s AND tag_group_id = %s AND edge_type = %s",
            (puzzle_id, tag_group_id, edge_type),
        )

    def bulk_update_edge_type(self, tag_group_id, new_edge):
        """Convert members' feeds/informs/none edges to new_edge for a group, without
        creating duplicate (tag_group_id, puzzle_id, new_edge) rows. The target's
        'target' self-edge is left untouched here (bulk_re_edge re-pins it).

        INSERT first, DELETE second, deliberately. Every statement autocommits on its
        own (see MySQLReconnector), so with the old DELETE-then-INSERT order a failure
        between the two destroyed the group's entire membership. In this order the
        worst case is a member briefly holding both its old edge and the new one --
        visible, harmless, and fixed by re-running the conversion.

        Not expressible as a single UPDATE: migration 020's
        UNIQUE (tag_group_id, puzzle_id, edge_type) is violated when a member already
        holds new_edge alongside an old edge -- hence also the edge_type <> new_edge
        clause on the DELETE, which stops it removing what the INSERT just wrote.
        """
        cursor = self.mydb.dict_cursor()
        try:
            # Puzzles that currently contribute (feeds/informs/none) to this group.
            cursor.execute(
                "SELECT DISTINCT puzzle_id FROM tags "
                "WHERE tag_group_id = %s AND edge_type IN ('feeds','informs','none')",
                (tag_group_id,),
            )
            pids = [r["puzzle_id"] for r in cursor.fetchall() or []]
            if pids:
                placeholders = ",".join(["%s"] * len(pids))
                cursor.executemany(
                    "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)",
                    [(tag_group_id, pid, new_edge) for pid in pids],
                )
                cursor.execute(
                    f"DELETE FROM tags WHERE tag_group_id = %s "
                    f"AND edge_type IN ('feeds','informs','none') "
                    f"AND edge_type <> %s "
                    f"AND puzzle_id IN ({placeholders})",
                    (tag_group_id, new_edge, *pids),
                )
        finally:
            cursor.close()

    def commit(self, puzzle, /):
        """Write the puzzle row, then reconcile its membership edges to match
        puzzle.tag_edges.

        NOT atomic, and cannot be made so without a transaction: the base-row UPDATE
        and the tag INSERT/DELETEs are separate autocommitting statements (see
        MySQLReconnector -- autocommit is on, so commit() is a no-op). A failure
        between them leaves the row updated and the edges stale.

        That is tolerated deliberately. The failure mode is self-correcting -- the
        next commit of the same puzzle re-diffs the edges and converges -- unlike
        bulk_update_edge_type, whose old ordering could destroy membership outright
        and which was reordered rather than left to chance.

        Note tags/tag_edges are in SPECIAL_ATTR, so the generic UPDATE skips them:
        the diff below is the ONLY thing that persists membership. Mutating
        puzzle.tags without puzzle.tag_edges (via add_edge/remove_edge) writes
        nothing.
        """
        super(MySQLPuzzleJsonDb, self).commit(puzzle)
        cursor = self.mydb.dict_cursor()
        cursor.execute(
            "SELECT tag_group_id, edge_type FROM tags WHERE puzzle_id = %s",
            (puzzle.id,),
        )
        db_pairs = {(r['tag_group_id'], r['edge_type']) for r in cursor.fetchall() or []}
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

    def delete(self, puzzle_id, /):
        super(MySQLPuzzleJsonDb, self).delete(puzzle_id)
        self._exec("DELETE FROM tags WHERE puzzle_id = %s", (puzzle_id,))

class MySQLChannelActivityDb(_MySQLBaseDb):
    TABLE_NAME = 'channel_activity'

    def record(self, channel_id, guild_id, at, author_id, author_name):
        """Upsert the most recent human (non-bot) message seen in a channel."""
        self._exec(
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

_UNSET: Any = object()  # sentinel: "argument not provided" (distinct from None)


class MySQLMessageDb(_MySQLBaseDb):
    TABLE_NAME = 'messages'

    def write(self, *, id, channel_id, guild_id, author_id, author_name,
              author_avatar, kind, content, reference_id, created_at, embeds=None):
        """Insert a captured message. Idempotent on the Discord message id.

        `embeds` is a JSON string (normalized embed payload) or None."""
        self._exec(
            "INSERT INTO messages "
            "(id, channel_id, guild_id, author_id, author_name, author_avatar, "
            " kind, content, embeds, reference_id, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE content=VALUES(content), embeds=VALUES(embeds)",
            (id, channel_id, guild_id, author_id, author_name, author_avatar,
             kind, content, embeds, reference_id, created_at),
        )

    def mark_edited(self, id, content, edited_at, embeds: Optional[str] = _UNSET):
        """Update a message's content/timestamp. `embeds` is only rewritten when a
        value is passed (a content-only edit leaves any captured embeds intact)."""
        if embeds is _UNSET:
            self._exec(
                "UPDATE messages SET content=%s, edited_at=%s WHERE id=%s",
                (content, edited_at, id),
            )
        else:
            self._exec(
                "UPDATE messages SET content=%s, embeds=%s, edited_at=%s WHERE id=%s",
                (content, embeds, edited_at, id),
            )

    def channel_has_messages(self, channel_id) -> bool:
        """True if any message is already stored for the channel. Used by the
        bridge to identify (and skip) the first welcome/info embed."""
        row = self._fetch_one(
            "SELECT 1 FROM messages WHERE channel_id=%s LIMIT 1", (channel_id,)
        )
        return row is not None

    def delete_message(self, id) -> list:
        """Hard-delete a message. Returns the local_path values its attachments had
        (relative paths) so the caller can unlink the bytes.

        One statement: migration 037's FK (message_attachments.message_id ->
        messages.id ON DELETE CASCADE) removes the attachment rows, so there is no
        two-statement window in which a failure could destroy them and leave the
        message. The paths are read first because the rows are gone afterwards, and
        returned only if the DELETE succeeded -- if it raises, the exception
        propagates and the caller never gets paths for files whose rows still exist.
        """
        rows = self._fetch_all(
            "SELECT local_path FROM message_attachments WHERE message_id=%s", (id,)
        )
        self._exec("DELETE FROM messages WHERE id=%s", (id,))
        return [r["local_path"] for r in rows if r.get("local_path")]

    def add_attachment(self, *, id, message_id, filename, content_type, size, source_url):
        self._exec(
            "INSERT INTO message_attachments "
            "(id, message_id, filename, content_type, size, source_url, download_status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE source_url=VALUES(source_url)",
            (id, message_id, filename, content_type, size, source_url, 0),
        )

    def set_attachment_local(self, id, local_path, download_status):
        self._exec(
            "UPDATE message_attachments SET local_path=%s, download_status=%s WHERE id=%s",
            (local_path, download_status, id),
        )


class MySQLEvidenceDb(_MySQLBaseDb):
    TABLE_NAME = 'evidence_items'

    def log(self, hunt_id: int, source_puzzle_id, note: str,
            media_id=None, source_sheet_id=None,
            source_region_id=None) -> 'EvidenceData':
        """Insert one evidence item and return it.

        source_puzzle_id may be None: evidence can belong to the hunt rather
        than to any one puzzle. media_id may be None: evidence can be text only.
        source_sheet_id names a hunt-level working sheet as the provenance;
        source_region_id names a region drawn on a canvas.
        """
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO evidence_items"
                " (hunt_id, source_puzzle_id, note, media_id, source_sheet_id,"
                "  source_region_id, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (hunt_id, source_puzzle_id, note, media_id, source_sheet_id,
                 source_region_id, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        e = EvidenceData()
        e.id = new_id or 0
        e.hunt_id = hunt_id
        e.source_puzzle_id = source_puzzle_id
        e.note = note
        e.media_id = media_id
        e.source_sheet_id = source_sheet_id
        e.source_region_id = source_region_id
        # Match import_dict's tzinfo-reattachment so a freshly-logged item and one
        # read back from the DB serialize identically (#33).
        e.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return e

    def delete_by_region(self, region_id: int) -> None:
        """Remove the evidence row(s) an informs-region spawned.

        Called by canvas.py when a link's role changes, its target changes, or it
        is cleared. The FK cascade covers deleting the region itself; this covers
        the cases where the region survives but its link does not.
        """
        self._exec(
            "DELETE FROM evidence_items WHERE source_region_id = %s", (region_id,))

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
        rows = self._fetch_all(sql, params)
        return [EvidenceData.import_dict(r) for r in rows]

    def get_by_id(self, item_id: int):
        """Return one EvidenceData by id, or None if missing."""
        row = self._fetch_one("SELECT * FROM evidence_items WHERE id = %s", (item_id,))
        return EvidenceData.import_dict(row) if row else None

    def set_target(self, item_id: int, target_puzzle_id):
        """Set or clear target_puzzle_id on an item. Returns updated EvidenceData."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "UPDATE evidence_items SET target_puzzle_id = %s WHERE id = %s",
                (target_puzzle_id, item_id),
            )
            cursor.execute("SELECT * FROM evidence_items WHERE id = %s", (item_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return EvidenceData.import_dict(row) if row else None

    def delete(self, item_id: int, /) -> None:
        """Delete an evidence item by id. No-op if the id does not exist."""
        self._exec("DELETE FROM evidence_items WHERE id = %s", (item_id,))

    def update_note(self, item_id: int, note: str):
        """Update the note text for an evidence item. Returns updated EvidenceData or None."""
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "UPDATE evidence_items SET note = %s WHERE id = %s",
                (note, item_id),
            )
            cursor.execute("SELECT * FROM evidence_items WHERE id = %s", (item_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return EvidenceData.import_dict(row) if row else None

    def update_media(self, item_id: int, media_id):
        """Point an evidence item at a different image. Returns it, or None.

        The previous media row and its file are deliberately left alone. That
        matches what already happens when an evidence item is deleted — nothing
        on this path garbage-collects media — and it means a replacement can
        never destroy the only copy of an image someone still wanted. media_id
        may be None, which detaches the image and leaves a text-only item.
        """
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "UPDATE evidence_items SET media_id = %s WHERE id = %s",
                (media_id, item_id),
            )
            cursor.execute("SELECT * FROM evidence_items WHERE id = %s", (item_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return EvidenceData.import_dict(row) if row else None


class MySQLFeederPoolDb(_MySQLBaseDb):
    TABLE_NAME = 'feeder_pool_items'

    def get_for_minimeta(self, minimeta_puzzle_id: int):
        """Every pool item for a minimeta, in paste order."""
        rows = self._fetch_all(
            "SELECT * FROM feeder_pool_items WHERE minimeta_puzzle_id = %s"
            " ORDER BY position ASC, id ASC",
            (minimeta_puzzle_id,))
        return [FeederPoolItemData.import_dict(r) for r in rows]

    def get_by_id(self, item_id: int):
        row = self._fetch_one(
            "SELECT * FROM feeder_pool_items WHERE id = %s", (item_id,))
        return FeederPoolItemData.import_dict(row) if row else None

    def next_position(self, minimeta_puzzle_id: int) -> int:
        row = self._fetch_one(
            "SELECT COALESCE(MAX(position), -1) AS p FROM feeder_pool_items"
            " WHERE minimeta_puzzle_id = %s", (minimeta_puzzle_id,))
        return int(row["p"]) + 1 if row else 0

    def add_many(self, minimeta_puzzle_id: int, texts):
        """Append `texts` that are not already in the pool. Returns the added ones.

        Re-pasting the same list is therefore harmless — an already-present text is
        skipped rather than duplicated or errored. Dedup is by `dedup_key`, matching
        the case-/accent-insensitive collation of the UNIQUE index it protects — two
        spellings of the same answer collide, and the first spelling committed wins;
        text is still stored verbatim.

        Raises ValueError, before anything is inserted, if any line is too long for
        the column — a paste is all-or-nothing rather than half-applied.
        """
        for text in texts:
            if len(text) > MAX_FEEDER_TEXT:
                display = text if len(text) <= 60 else text[:57] + "..."
                raise ValueError(
                    f"line too long ({len(text)} > {MAX_FEEDER_TEXT} chars): "
                    f"{display!r}")

        existing = {dedup_key(i.text) for i in self.get_for_minimeta(minimeta_puzzle_id)}
        position = self.next_position(minimeta_puzzle_id)
        added = []
        for text in texts:
            key = dedup_key(text)
            if key in existing:
                continue
            existing.add(key)
            item = FeederPoolItemData(
                minimeta_puzzle_id=minimeta_puzzle_id, text=text, position=position)
            self.commit(item)
            added.append(item)
            position += 1
        return added

    def set_text(self, item_id: int, text: str) -> None:
        try:
            self._exec(
                "UPDATE feeder_pool_items SET text = %s WHERE id = %s", (text, item_id))
        except IntegrityError as exc:
            if getattr(exc, "errno", None) == DUPLICATE_ENTRY_ERRNO:
                raise ValueError(f"{text!r} is already in the pool") from exc
            raise

    def set_note(self, item_id: int, note) -> None:
        self._exec(
            "UPDATE feeder_pool_items SET note = %s WHERE id = %s", (note, item_id))

    def delete(self, item_id: int, /) -> None:
        """Delete a pool item. Its claims cascade (migration 043)."""
        self._exec("DELETE FROM feeder_pool_items WHERE id = %s", (item_id,))


class MySQLFeederClaimDb(_MySQLBaseDb):
    TABLE_NAME = 'feeder_claims'

    def get_for_minimeta(self, minimeta_puzzle_id: int):
        """Every claim against every item in one minimeta's pool.

        The pure derivation module (added in a later task) computes all per-item and
        per-target state from this one list, so callers fetch once and pass it around
        rather than querying per item.
        """
        rows = self._fetch_all(
            "SELECT c.* FROM feeder_claims c"
            " JOIN feeder_pool_items i ON i.id = c.item_id"
            " WHERE i.minimeta_puzzle_id = %s"
            " ORDER BY c.item_id ASC, c.target_id ASC",
            (minimeta_puzzle_id,))
        return [FeederClaimData.import_dict(r) for r in rows]

    def get_for_target(self, target_id: int):
        rows = self._fetch_all(
            "SELECT * FROM feeder_claims WHERE target_id = %s ORDER BY item_id ASC",
            (target_id,))
        return [FeederClaimData.import_dict(r) for r in rows]

    def get(self, item_id: int, target_id: int):
        """The one claim `target_id` holds on `item_id`, or None."""
        row = self._fetch_one(
            "SELECT * FROM feeder_claims WHERE item_id = %s AND target_id = %s",
            (item_id, target_id))
        return FeederClaimData.import_dict(row) if row else None

    def add(self, item_id: int, target_id: int) -> None:
        """Claim an item for a target. Idempotent — re-claiming is a no-op."""
        self._exec(
            "INSERT IGNORE INTO feeder_claims (item_id, target_id, confirmed)"
            " VALUES (%s, %s, 0)", (item_id, target_id))

    def remove(self, item_id: int, target_id: int) -> None:
        self._exec(
            "DELETE FROM feeder_claims WHERE item_id = %s AND target_id = %s",
            (item_id, target_id))

    def set_confirmed_for_target(self, target_id: int, confirmed: bool) -> None:
        """Confirm (or demote) every claim held by one target — the solve/unsolve hook."""
        self._exec(
            "UPDATE feeder_claims SET confirmed = %s WHERE target_id = %s",
            (1 if confirmed else 0, target_id))


class MySQLMediaDb(_MySQLBaseDb):
    TABLE_NAME = 'media'

    def create(self, hunt_id: int, path: str, filename: str,
               content_type: str, size: int, origin: str,
               width: int = 0, height: int = 0,
               tile_status: str = "none") -> 'MediaData':
        """Insert one media row and return it.

        Dimensions are recorded for every image, tiled or not: they cost one
        cheap header read, and the frontend needs them to lay out a viewport
        before any bytes arrive.
        """
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO media"
                " (hunt_id, path, filename, content_type, size, origin, created_at,"
                "  width, height, tile_status, tile_levels)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)",
                (hunt_id, path, filename, content_type, size, origin, now,
                 width, height, tile_status),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        m = MediaData()
        m.id = new_id or 0
        m.hunt_id = hunt_id
        m.path = path
        m.filename = filename
        m.content_type = content_type
        m.size = size
        m.origin = origin
        m.width = width
        m.height = height
        m.tile_status = tile_status
        m.tile_levels = 0
        # Match import_dict's tzinfo-reattachment so a freshly-created row and one
        # read back from the DB serialize identically (same as EvidenceData).
        m.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return m

    def update_tiles(self, media_id: int, tile_status: str,
                     tile_levels: int = 0) -> None:
        """Move one row's pyramid state on.

        media_tiling is the only writer, which is what keeps the pyramid on disk
        and the row from drifting apart.
        """
        self._exec(
            "UPDATE media SET tile_status = %s, tile_levels = %s WHERE id = %s",
            (tile_status, tile_levels, media_id),
        )

    def get_by_id(self, media_id: int):
        """Return one MediaData by id, or None if missing."""
        row = self._fetch_one("SELECT * FROM media WHERE id = %s", (media_id,))
        return MediaData.import_dict(row) if row else None

    def get_orphaned(self, hunt_id: int, older_than) -> list:
        """The hunt's media rows nothing points at, created before `older_than`.

        The four LEFT JOINs are every foreign key into `media` there is
        (evidence items, canvases, puzzle images, region crops); a row is
        orphaned exactly when all four miss. `older_than` is a naive UTC
        datetime, matching how created_at is stored — media_ingest creates the
        row before its caller attaches it to anything, so a sweep with no age
        floor would delete images mid-upload.
        """
        rows = self._fetch_all(
            "SELECT m.* FROM media m"
            " LEFT JOIN evidence_items ei ON ei.media_id = m.id"
            " LEFT JOIN hunt_canvases hc ON hc.media_id = m.id"
            " LEFT JOIN puzzle_images pi ON pi.media_id = m.id"
            " LEFT JOIN canvas_regions cr ON cr.crop_media_id = m.id"
            " WHERE m.hunt_id = %s AND m.created_at < %s"
            " AND ei.id IS NULL AND hc.id IS NULL"
            " AND pi.id IS NULL AND cr.id IS NULL"
            " ORDER BY m.id",
            (hunt_id, older_than),
        )
        return [MediaData.import_dict(r) for r in rows]


class MySQLHuntCanvasDb(_MySQLBaseDb):
    TABLE_NAME = 'hunt_canvases'

    def create(self, hunt_id: int, media_id: int, name: str,
               note=None) -> 'HuntCanvasData':
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO hunt_canvases (hunt_id, media_id, name, note, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (hunt_id, media_id, name, note, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        c = HuntCanvasData()
        c.id = new_id or 0
        c.hunt_id, c.media_id, c.name, c.note = hunt_id, media_id, name, note
        c.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return c

    def get_by_id(self, canvas_id: int):
        row = self._fetch_one("SELECT * FROM hunt_canvases WHERE id = %s", (canvas_id,))
        return HuntCanvasData.import_dict(row) if row else None

    def get_by_hunt(self, hunt_id: int) -> list:
        rows = self._fetch_all(
            "SELECT * FROM hunt_canvases WHERE hunt_id = %s"
            " ORDER BY created_at, id", (hunt_id,))
        return [HuntCanvasData.import_dict(r) for r in rows]

    def update(self, canvas_id: int, name: str, note) -> None:
        self._exec(
            "UPDATE hunt_canvases SET name = %s, note = %s WHERE id = %s",
            (name, note, canvas_id))


def _region_display_name(row) -> str:
    """The label-else-canvas-else-filename chain for one joined region row.

    Module-level and pure so the branch order is testable without a database.
    See MySQLCanvasRegionDb.get_names_by_ids for why the chain runs this far.
    """
    label = (row.get("label") or "").strip()
    if label:
        return label
    canvas_name = (row.get("canvas_name") or "").strip()
    if canvas_name:
        return canvas_name
    filename = (row.get("filename") or "").strip()
    if filename:
        return f"Canvas ({filename})"
    return f"Canvas {row.get('canvas_id')}"


class MySQLCanvasRegionDb(_MySQLBaseDb):
    TABLE_NAME = 'canvas_regions'

    def create(self, canvas_id: int, shape: str, label: str = "", note=None,
               puzzle_id=None, role=None, crop_media_id=None) -> 'CanvasRegionData':
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO canvas_regions"
                " (canvas_id, shape, label, note, puzzle_id, role, crop_media_id, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (canvas_id, shape, label, note, puzzle_id, role, crop_media_id, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        r = CanvasRegionData()
        r.id = new_id or 0
        r.canvas_id, r.shape, r.label, r.note = canvas_id, shape, label, note
        r.puzzle_id, r.role = puzzle_id, role
        r.crop_media_id = crop_media_id
        r.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return r

    def get_by_id(self, region_id: int):
        row = self._fetch_one("SELECT * FROM canvas_regions WHERE id = %s", (region_id,))
        return CanvasRegionData.import_dict(row) if row else None

    def get_by_canvas(self, canvas_id: int) -> list:
        rows = self._fetch_all(
            "SELECT * FROM canvas_regions WHERE canvas_id = %s"
            " ORDER BY created_at, id", (canvas_id,))
        return [CanvasRegionData.import_dict(r) for r in rows]

    def update(self, region_id: int, shape: str, label: str, note,
               puzzle_id, role, crop_media_id=None) -> None:
        self._exec(
            "UPDATE canvas_regions SET shape = %s, label = %s, note = %s,"
            " puzzle_id = %s, role = %s, crop_media_id = %s WHERE id = %s",
            (shape, label, note, puzzle_id, role, crop_media_id, region_id))

    def get_names_by_ids(self, region_ids) -> dict:
        """{region_id: display name} for rendered Source columns, in one query.

        The fallback chain is the region's label, then its canvas's name, then
        "Canvas (the image's filename)", then "Canvas <id>". It has to run past
        the canvas name because hunt_canvases.name defaults to '' and nothing
        forces a canvas to be renamed, so an unlabelled region on an unnamed
        canvas used to render as a BLANK Source cell — which reads as "no
        source" rather than "a source nobody bothered to name". The filename is
        what the uploader actually recognises the picture by.

        This joins rather than reading canvas_regions alone so the chain lives in
        one place instead of being repeated by every caller. The chain itself is
        composed here in Python rather than as a SQL CASE: the branch a given row
        lands on is then something a test can actually exercise.
        """
        ids = [int(i) for i in region_ids if i]
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        rows = self._fetch_all(
            "SELECT r.id AS id, r.label AS label, r.canvas_id AS canvas_id,"
            " c.name AS canvas_name, m.filename AS filename"
            " FROM canvas_regions r"
            " JOIN hunt_canvases c ON c.id = r.canvas_id"
            # LEFT, though hunt_canvases.media_id is NOT NULL with an FK:
            # a row read in the gap of a cascading media delete would
            # otherwise drop out of the result set entirely and render as
            # "Region <id>" rather than as a canvas.
            " LEFT JOIN media m ON m.id = c.media_id"
            f" WHERE r.id IN ({placeholders})", tuple(ids))
        return {r["id"]: _region_display_name(r) for r in rows}


class MySQLPuzzleImageDb(_MySQLBaseDb):
    TABLE_NAME = 'puzzle_images'

    def create(self, puzzle_id: int, media_id: int, source_region_id=None,
               position: int = 0) -> 'PuzzleImageData':
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO puzzle_images"
                " (puzzle_id, media_id, source_region_id, position, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (puzzle_id, media_id, source_region_id, position, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        p = PuzzleImageData()
        p.id = new_id or 0
        p.puzzle_id, p.media_id = puzzle_id, media_id
        p.source_region_id, p.position = source_region_id, position
        p.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return p

    def get_by_puzzle(self, puzzle_id: int) -> list:
        """The puzzle's images in display order. `position` first, then id, so
        rows created before anyone reordered still have a stable order."""
        rows = self._fetch_all(
            "SELECT * FROM puzzle_images WHERE puzzle_id = %s"
            " ORDER BY position, id", (puzzle_id,))
        return [PuzzleImageData.import_dict(r) for r in rows]

    def get_by_id(self, image_id: int):
        row = self._fetch_one("SELECT * FROM puzzle_images WHERE id = %s", (image_id,))
        return PuzzleImageData.import_dict(row) if row else None

    def get_by_region(self, region_id: int):
        """At most one row per region — enforced by the unique index."""
        row = self._fetch_one(
            "SELECT * FROM puzzle_images WHERE source_region_id = %s", (region_id,))
        return PuzzleImageData.import_dict(row) if row else None

    def set_positions(self, ordered_ids) -> None:
        """Write a whole ordering at once. Whole-list rather than per-item so a
        half-applied reorder cannot leave the list in an order nobody chose.

        Genuinely one statement, which is what makes that guarantee true: every
        statement autocommits on its own (see MySQLReconnector), so the previous
        per-row UPDATE loop committed each row separately and could leave exactly the
        half-applied ordering this docstring disclaims.
        """
        ordered_ids = list(ordered_ids)
        if not ordered_ids:
            return
        cases = " ".join("WHEN %s THEN %s" for _ in ordered_ids)
        placeholders = ",".join(["%s"] * len(ordered_ids))
        params = []
        for index, image_id in enumerate(ordered_ids):
            params.extend((image_id, index))
        params.extend(ordered_ids)
        self._exec(
            f"UPDATE puzzle_images SET position = CASE id {cases} END "
            f"WHERE id IN ({placeholders})",
            tuple(params),
        )

    def delete_by_region(self, region_id: int) -> None:
        self._exec(
            "DELETE FROM puzzle_images WHERE source_region_id = %s", (region_id,))


class MySQLMediaMarkupDb(_MySQLBaseDb):
    TABLE_NAME = 'media_markup'

    def create(self, media_id: int, kind: str, shape: str, style: str,
               text=None) -> 'MediaMarkupData':
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO media_markup"
                " (media_id, kind, shape, style, text, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (media_id, kind, shape, style, text, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        m = MediaMarkupData()
        m.id = new_id or 0
        m.media_id, m.kind, m.shape, m.style, m.text = media_id, kind, shape, style, text
        m.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return m

    def get_by_id(self, mark_id: int):
        row = self._fetch_one("SELECT * FROM media_markup WHERE id = %s", (mark_id,))
        return MediaMarkupData.import_dict(row) if row else None

    def get_by_media(self, media_id: int) -> list:
        """Oldest first: paint order IS document order in SVG, so a later mark
        must draw on top of an earlier one."""
        rows = self._fetch_all(
            "SELECT * FROM media_markup WHERE media_id = %s"
            " ORDER BY created_at, id", (media_id,))
        return [MediaMarkupData.import_dict(r) for r in rows]

    def update(self, mark_id: int, style: str, text) -> None:
        """Only style and text are editable. Geometry is not: a redrawn stroke
        is a new mark, and allowing a shape rewrite would mean re-validating a
        kind the row already committed to."""
        self._exec(
            "UPDATE media_markup SET style = %s, text = %s WHERE id = %s",
            (style, text, mark_id))


class MySQLGuildSettingsDb():
    def __init__(self, mydb: "MySQLReconnector"):
        self.cached_settings: dict[int, Optional[GuildSettings]] = {}
        self.mydb = mydb

    def get_channel_type(self, channel_id) -> Optional[str]:
        cursor = self.mydb.dict_cursor()
        stmt = ("SELECT channel_id, channel_type FROM ( "
                    "SELECT category_id as channel_id, 'Hunt' as channel_type FROM hunts "
                    "UNION SELECT channel_id as channel_id, 'Hunt' as channel_type FROM hunts "
                    "UNION SELECT category_id as channel_id, 'Group' as channel_type FROM tag_group "
                    "UNION SELECT channel_id, 'Puzzle' as channel_type FROM puzzles "
                ") channel_types WHERE channel_id=%s")
        data = (channel_id,)
        try:
            cursor.execute(stmt, data)
            rows = cursor.fetchall() or []
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

    def get(self, guild_id: int) -> Optional[GuildSettings]:
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute("SELECT * FROM guilds WHERE guild_id = %s", (guild_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row:
            return GuildSettings.import_dict(row)
        return None

    def get_cached(self, guild_id: int) -> Optional[GuildSettings]:
        if guild_id in self.cached_settings:
            return self.cached_settings[guild_id]
        settings = self.get(guild_id)
        self.cached_settings[guild_id] = settings
        return settings

    def commit(self, settings: GuildSettings):
        cursor = self.mydb.cursor()
        data = (settings.guild_id, settings.guild_name, settings.website_url,
                settings.discord_bot_channel, settings.discord_bot_emoji, settings.discord_use_voice_channels,
                settings.drive_parent_id, settings.drive_resources_id,
                settings.admin_role_ids or None, settings.organiser_role_ids or None)
        if settings.id > 0:
            update_stmt = ("UPDATE `guilds` SET `guild_id`=%s,`guild_name`=%s, `website_url`=%s,"
                           "`discord_bot_channel`=%s,`discord_bot_emoji`=%s,`discord_use_voice_channels`=%s,"
                           "`drive_parent_id`=%s,`drive_resources_id`=%s,"
                           "`admin_role_ids`=%s,`organiser_role_ids`=%s "
                           "WHERE id=%s")
            data = data + (settings.id,)
            cursor.execute(update_stmt, data)
        else:
            insert_stmt = ("INSERT INTO `guilds`(`guild_id`, `guild_name`, `website_url`,"
                           "`discord_bot_channel`, `discord_bot_emoji`, `discord_use_voice_channels`, "
                           "`drive_parent_id`, `drive_resources_id`, "
                           "`admin_role_ids`, `organiser_role_ids`) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
            cursor.execute(insert_stmt, data)
            settings.id = cursor.lastrowid or 0
        cursor.close()
        # Keep the read cache coherent: a prior get_cached() may have cached a
        # None (no row yet) or a stale copy, which would otherwise shadow this
        # freshly-written row until process restart.
        self.cached_settings[settings.guild_id] = settings