import datetime
import random
import string
import json
import logging
from mysql.connector.errors import IntegrityError
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from . import MySQLReconnector

from bot.core.discord_channels import clean_channel_name
from bot.core.activity_slots import slot_start
from bot.core.constants import HEADER_STYLES
from .puzzle_data import PuzzleData, MissingPuzzleError, AdditionalSheetData
from .tag_group_data import TagGroupData
from .tag_group_category_data import TagGroupCategoryData
from .hunt_category_data import HuntCategoryData
from .rollup_data import RollupData
from .hunt_data import HuntData
from .puzzle_settings import GuildSettings
from .evidence_data import EvidenceData
from .media_data import MediaData
from .canvas_data import CanvasRegionData, HuntCanvasData
from .markup_data import MediaMarkupData
from .puzzle_image_data import PuzzleImageData

logger = logging.getLogger(__name__)

#: MariaDB/MySQL ER_DUP_ENTRY -- a UNIQUE index rejected the write.
DUPLICATE_ENTRY_ERRNO = 1062


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
    # Written only by set_voice_channel / set_voice_per_puzzle / set_header_style, so a stale
    # HuntData saved whole can't revert them.
    SPECIAL_ATTR = ['voice_channel_id', 'voice_per_puzzle', 'header_style']
    def get_by_attr(self,**kwargs):
        """Retrieve Hunt by attribute.  Only first sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            row = self._fetch_one(
                f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s ORDER BY `id` ASC LIMIT 1", (value,))
            if row:
                return HuntData.import_dict(row)
            logger.info(f"Unable to find hunt for {keyword} - {value}")
            return None

    def by_voice_channel(self, channel_id):
        """The hunt whose general voice channel is *channel_id*, or None. Silent
        (no "Unable to find" log): called for every voice join in the guild."""
        row = self._fetch_one(
            "SELECT * FROM `hunts` WHERE `voice_channel_id` = %s LIMIT 1", (channel_id,))
        return HuntData.import_dict(row) if row else None

    def get_by_name_for_guild(self, name, guild_snowflake):
        """Retrieve a Hunt by name, scoped to the Discord guild that owns it (M6).

        `hunts.guild_id` FKs `guilds.id` (the guilds table's own row id), NOT the
        Discord snowflake -- see bot/web/notifier.py's board_changed for the same
        confusion causing a silent failure elsewhere. Scoping by the invoking
        guild's snowflake therefore needs the join below, not a bare
        `hunts.guild_id = %s`. A name is not unique even within one guild, so this
        takes the oldest match (see get_by_attr / tests/test_single_row_lookups.py).
        """
        row = self._fetch_one(
            "SELECT hunts.* FROM `hunts` JOIN `guilds` ON hunts.guild_id = guilds.id "
            "WHERE guilds.guild_id = %s AND hunts.name = %s "
            "ORDER BY hunts.id ASC LIMIT 1",
            (guild_snowflake, name),
        )
        if row:
            return HuntData.import_dict(row)
        logger.info(f"Unable to find hunt named {name} for guild {guild_snowflake}")
        return None

    def check_duplicates(self, value, field='name', guild_id=None):
        """Duplicate check for hunts, optionally scoped to a Discord guild (M6).

        The base implementation (`_MySQLBaseDb.check_duplicates`) is a bare
        `WHERE field = value` with no guild filter, making hunt names one
        namespace across every guild the bot serves. When `guild_id` (a Discord
        guild snowflake) is given, the check is scoped to that guild via the same
        guilds-join used by get_by_name_for_guild, so the same name is free to
        exist in more than one guild. Callers that omit guild_id keep the
        previous global behaviour.
        """
        if guild_id is None:
            return super().check_duplicates(value, field)
        row = self._fetch_one(
            f"SELECT 1 FROM `{self.TABLE_NAME}` JOIN `guilds` ON hunts.guild_id = guilds.id "
            f"WHERE guilds.guild_id = %s AND hunts.`{field}` = %s LIMIT 1",
            (guild_id, value),
        )
        return row is not None

    def count_for_guild(self, guild_row_id) -> int:
        """How many hunt rows, archived or not, point at `guilds.id` *guild_row_id*.

        archive_hunt_to_hub leaves a team server only when this is zero: an
        archived hunt still homed there would vanish from the dashboard once the
        bot left, because hunt_visible needs the user to share that guild with
        the bot."""
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM `hunts` WHERE guild_id = %s", (guild_row_id,))
        return int(row["n"]) if row else 0

    def live_team_hunt_for_guild(self, guild_snowflake):
        """The live hunt on the guild whose Discord id is *guild_snowflake* that has
        a hub roster role -- a team server holds exactly one -- or None. Hub-hosted
        hunts have no roster role, so the hub itself never matches."""
        row = self._fetch_one(
            "SELECT hunts.* FROM `hunts` JOIN `guilds` ON hunts.guild_id = guilds.id "
            "WHERE guilds.guild_id = %s AND hunts.archived = 0 "
            "AND hunts.roster_role_id IS NOT NULL AND hunts.roster_role_id <> 0 "
            "ORDER BY hunts.id ASC LIMIT 1",
            (guild_snowflake,),
        )
        return HuntData.import_dict(row) if row else None

    def live_invited_hunts_for_roster(self, roster_role_id):
        """[(hunt, team guild snowflake)] for live hunts whose hub roster role is
        *roster_role_id* and whose invites have gone out (invites_sent_time set):
        the hunts a newly-given hub role should trigger an invite DM for."""
        rows = self._fetch_all(
            "SELECT hunts.*, guilds.guild_id AS guild_snowflake FROM `hunts` "
            "JOIN `guilds` ON hunts.guild_id = guilds.id "
            "WHERE hunts.roster_role_id = %s AND hunts.archived = 0 "
            "AND hunts.invites_sent_time IS NOT NULL ORDER BY hunts.id ASC",
            (roster_role_id,),
        )
        return [(HuntData.import_dict(r), int(r["guild_snowflake"])) for r in rows]

    def live_team_hunts(self):
        """[(hunt, team guild snowflake)] for every live hunt with a hub roster role,
        invites sent or not: the hunts someone losing a hub role may have to leave."""
        rows = self._fetch_all(
            "SELECT hunts.*, guilds.guild_id AS guild_snowflake FROM `hunts` "
            "JOIN `guilds` ON hunts.guild_id = guilds.id "
            "WHERE hunts.archived = 0 "
            "AND hunts.roster_role_id IS NOT NULL AND hunts.roster_role_id <> 0 "
            "ORDER BY hunts.id ASC",
        )
        return [(HuntData.import_dict(r), int(r["guild_snowflake"])) for r in rows]

    def team_hunts(self):
        """[(hunt, guild snowflake)] for every hunt with a hub roster role, archived
        or not (running first, newest first): the Team servers page's list. The
        snowflake is the guild now homing the row (an archived hunt's is the hub)."""
        rows = self._fetch_all(
            "SELECT hunts.*, guilds.guild_id AS guild_snowflake FROM `hunts` "
            "JOIN `guilds` ON hunts.guild_id = guilds.id "
            "WHERE hunts.roster_role_id IS NOT NULL AND hunts.roster_role_id <> 0 "
            "ORDER BY hunts.archived ASC, hunts.id DESC",
        )
        return [(HuntData.import_dict(r), int(r["guild_snowflake"])) for r in rows]

    def guild_snowflake_for_hunt(self, hunt_id):
        """The Discord snowflake of the guild now homing *hunt_id*, or None."""
        row = self._fetch_one(
            "SELECT guilds.guild_id AS guild_snowflake FROM `hunts` "
            "JOIN `guilds` ON hunts.guild_id = guilds.id WHERE hunts.id = %s",
            (hunt_id,),
        )
        return int(row["guild_snowflake"]) if row else None

    def mark_invites_sent(self, hunt_id):
        """Stamp hunts.invites_sent_time with now (UTC) unless already set: the first
        completed !send_invites wins. Its own UPDATE -- HuntData has no such field,
        so the generic commit() can never clear it."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self._exec(
            "UPDATE `hunts` SET `invites_sent_time` = COALESCE(`invites_sent_time`, %s) "
            "WHERE `id` = %s",
            (now, hunt_id),
        )

    def set_mod_invite_code(self, hunt_id, code):
        """Store (or clear, with None) hunts.mod_invite_code: the reusable moderator
        invite to the hunt's team server. Its own UPDATE -- HuntData has no such
        field, so the generic commit() can never clear it."""
        self._exec("UPDATE `hunts` SET `mod_invite_code` = %s WHERE `id` = %s",
                   (code, hunt_id))

    def set_voice_channel(self, hunt_id, channel_id):
        """Store (or clear, with None) the hunt's shared voice channel. Its own
        UPDATE so a stale HuntData commit() cannot overwrite it."""
        self._exec("UPDATE `hunts` SET `voice_channel_id` = %s WHERE `id` = %s",
                   (channel_id, hunt_id))

    def voice_channel_id(self, hunt_id):
        """The stored hunt voice channel id (None if unset or no such hunt). A
        narrow read so a caller holding a stale HuntData can check the truth."""
        row = self._fetch_one(
            "SELECT `voice_channel_id` FROM `hunts` WHERE `id` = %s", (hunt_id,))
        return row["voice_channel_id"] if row else None

    def set_voice_per_puzzle(self, hunt_id, on):
        """Set the per-puzzle voice channel toggle. Its own UPDATE (see above)."""
        self._exec("UPDATE `hunts` SET `voice_per_puzzle` = %s WHERE `id` = %s",
                   (1 if on else 0, hunt_id))

    def set_header_style(self, hunt_id, style):
        """Set the hunt's puzzle-sheet header style. Its own UPDATE (see above)."""
        if style not in HEADER_STYLES:
            raise ValueError(f"unknown header style: {style!r}")
        self._exec("UPDATE `hunts` SET `header_style` = %s WHERE `id` = %s",
                   (style, hunt_id))

    def mod_invite_code(self, hunt_id):
        row = self._fetch_one(
            "SELECT `mod_invite_code` FROM `hunts` WHERE `id` = %s", (hunt_id,))
        return row["mod_invite_code"] if row else None


class MySQLHuntInviteDb(_MySQLBaseDb):
    """hunt_invites: one row per single-use team-server invite DM'd to a holder of
    a hunt's hub role (slice 4). A record, not a gate."""
    TABLE_NAME = 'hunt_invites'

    def record(self, hunt_id, user_id, code):
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self._exec(
            "INSERT INTO `hunt_invites` (hunt_id, user_id, code, created_at) "
            "VALUES (%s, %s, %s, %s)",
            (hunt_id, user_id, code, now),
        )

    def invited_user_ids(self, hunt_id):
        """Everyone ever DM'd an invite for *hunt_id*."""
        rows = self._fetch_all(
            "SELECT DISTINCT user_id FROM `hunt_invites` WHERE hunt_id = %s", (hunt_id,))
        return {int(r["user_id"]) for r in rows}

    def pending_codes(self, hunt_id, user_id):
        """Codes of *user_id*'s invites for *hunt_id* that nobody has arrived on."""
        rows = self._fetch_all(
            "SELECT code FROM `hunt_invites` WHERE hunt_id = %s AND user_id = %s "
            "AND joined_at IS NULL ORDER BY id ASC",
            (hunt_id, user_id),
        )
        return [r["code"] for r in rows]

    def for_hunt(self, hunt_id):
        """Invite rows (user_id, created_at, joined_at) for *hunt_id*, oldest first.
        The code column is deliberately never selected: it must not reach the API."""
        return self._fetch_all(
            "SELECT user_id, created_at, joined_at FROM `hunt_invites` "
            "WHERE hunt_id = %s ORDER BY id ASC",
            (hunt_id,),
        )

    def for_hunts(self, hunt_ids):
        """{hunt_id: invite rows} for many hunts in one query (rows as for_hunt,
        still no code column). Hunts with no invites are absent."""
        hunt_ids = list(hunt_ids)
        if not hunt_ids:
            return {}
        marks = ", ".join(["%s"] * len(hunt_ids))
        rows = self._fetch_all(
            "SELECT hunt_id, user_id, created_at, joined_at FROM `hunt_invites` "
            f"WHERE hunt_id IN ({marks}) ORDER BY id ASC",
            tuple(hunt_ids),
        )
        out = {}
        for r in rows:
            out.setdefault(int(r["hunt_id"]), []).append(
                {k: r[k] for k in ("user_id", "created_at", "joined_at")})
        return out

    def forget(self, hunt_id, user_id):
        """Drop *user_id*'s invite rows for *hunt_id*, so being given the hub role
        again DMs them a fresh invite like anyone else."""
        self._exec("DELETE FROM `hunt_invites` WHERE hunt_id = %s AND user_id = %s",
                   (hunt_id, user_id))

    def mark_joined(self, hunt_id, user_id):
        """Note that *user_id* arrived on *hunt_id*'s team server (a no-op for
        someone who was never DM'd)."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self._exec(
            "UPDATE `hunt_invites` SET joined_at = %s "
            "WHERE hunt_id = %s AND user_id = %s AND joined_at IS NULL",
            (now, hunt_id, user_id),
        )


class MySQLHuntAccessLogDb(_MySQLBaseDb):
    """hunt_access_log: durable record of access changes on a hunt's team server
    (slice 5). No foreign key to hunts: the record outlives a deleted hunt."""
    TABLE_NAME = 'hunt_access_log'

    def record(self, hunt_id, user_id, username, action, via, actor_id=None, detail=None):
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self._exec(
            "INSERT INTO `hunt_access_log` "
            "(created_at, hunt_id, user_id, username, action, via, actor_id, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (now, hunt_id, user_id, username, action, via, actor_id, detail),
        )

    def for_hunt(self, hunt_id):
        """Rows for *hunt_id*, oldest first."""
        return self._fetch_all(
            "SELECT * FROM `hunt_access_log` WHERE hunt_id = %s ORDER BY created_at ASC, id ASC",
            (hunt_id,),
        )


class MySQLMultiTeamDb(_MySQLBaseDb):
    """multi_team_hunts / multi_team_members: hunts of several teams on one hub
    guild that share hub channels and a combined archive. Datetimes are naive UTC."""
    TABLE_NAME = 'multi_team_hunts'

    def create(self, hub_guild_id, name, hub_category_id=None):
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO `multi_team_hunts` (hub_guild_id, name, hub_category_id, created_at) "
                "VALUES (%s, %s, %s, %s)", (hub_guild_id, name, hub_category_id, now))
            return cursor.lastrowid
        finally:
            cursor.close()

    def get(self, mt_id):
        return self._fetch_one("SELECT * FROM `multi_team_hunts` WHERE id = %s", (mt_id,))

    def running_for_hub(self, hub_guild_id):
        """Un-archived multi-team hunts on the hub by name, each with team_count
        (the members whose hunt is not archived) and the first team's hunt url and
        start_timestamp (None with no team), which setup fills in for a joiner."""
        first = ("(SELECT h.{col} FROM `multi_team_members` x "
                 "JOIN `hunts` h ON h.id = x.hunt_id "
                 "WHERE x.multi_team_id = m.id ORDER BY h.id ASC LIMIT 1) AS {col}")
        return self._fetch_all(
            "SELECT m.*, (SELECT COUNT(*) FROM `multi_team_members` x "
            "JOIN `hunts` h ON h.id = x.hunt_id "
            "WHERE x.multi_team_id = m.id AND h.archived = 0) AS team_count, "
            + first.format(col="url") + ", " + first.format(col="start_timestamp") + " "
            "FROM `multi_team_hunts` m "
            "WHERE m.hub_guild_id = %s AND m.archived_time IS NULL "
            "ORDER BY m.name ASC, m.id ASC",
            (hub_guild_id,),
        )

    def running_by_name(self, hub_guild_id, name):
        """The un-archived multi-team hunt on the hub called *name*
        (case-insensitive), or None."""
        return self._fetch_one(
            "SELECT * FROM `multi_team_hunts` "
            "WHERE hub_guild_id = %s AND archived_time IS NULL AND LOWER(name) = LOWER(%s) "
            "ORDER BY id ASC LIMIT 1",
            (hub_guild_id, name),
        )

    def set_channels(self, mt_id, no_spoiler_channel_id, finishers_channel_id):
        self._exec(
            "UPDATE `multi_team_hunts` SET no_spoiler_channel_id = %s, "
            "finishers_channel_id = %s WHERE id = %s",
            (no_spoiler_channel_id, finishers_channel_id, mt_id),
        )

    def mark_archived(self, mt_id):
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self._exec("UPDATE `multi_team_hunts` SET archived_time = %s WHERE id = %s",
                   (now, mt_id))

    def link(self, hunt_id, mt_id):
        """Put *hunt_id* in multi-team hunt *mt_id*. Same id again is a no-op;
        a different one raises ValueError (a hunt is in at most one)."""
        existing = self._fetch_one(
            "SELECT multi_team_id FROM `multi_team_members` WHERE hunt_id = %s", (hunt_id,))
        if existing:
            if int(existing["multi_team_id"]) != int(mt_id):
                raise ValueError(
                    f"hunt {hunt_id} is already in multi-team hunt {existing['multi_team_id']}")
            return
        self._exec("INSERT INTO `multi_team_members` (hunt_id, multi_team_id) VALUES (%s, %s)",
                   (hunt_id, mt_id))

    def for_hunt(self, hunt_id):
        """*hunt_id*'s member row joined with its multi-team hunt, or None."""
        return self._fetch_one(
            "SELECT x.multi_team_id, x.completed_time, m.name, m.hub_guild_id, "
            "m.no_spoiler_channel_id, m.finishers_channel_id, m.archived_time "
            "FROM `multi_team_members` x "
            "JOIN `multi_team_hunts` m ON m.id = x.multi_team_id "
            "WHERE x.hunt_id = %s",
            (hunt_id,),
        )

    def members(self, mt_id):
        """The member hunts of *mt_id*: hunt_id, completed_time, roster_role_id
        and archived (from hunts)."""
        return self._fetch_all(
            "SELECT x.hunt_id, x.completed_time, h.roster_role_id, h.archived "
            "FROM `multi_team_members` x JOIN `hunts` h ON h.id = x.hunt_id "
            "WHERE x.multi_team_id = %s ORDER BY x.hunt_id ASC",
            (mt_id,),
        )

    def set_completed(self, hunt_id, completed):
        """Mark *hunt_id*'s team finished (or not). True only when the state
        changed. Matched on completed_time's NULL-ness so rowcount is reliable."""
        if completed:
            sql = ("UPDATE `multi_team_members` SET completed_time = UTC_TIMESTAMP() "
                   "WHERE hunt_id = %s AND completed_time IS NULL")
        else:
            sql = ("UPDATE `multi_team_members` SET completed_time = NULL "
                   "WHERE hunt_id = %s AND completed_time IS NOT NULL")
        cursor = self.mydb.cursor()
        try:
            cursor.execute(sql, (hunt_id,))
            return cursor.rowcount > 0
        finally:
            cursor.close()


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

    def get_by_page_id(self, page_id):
        """Return the additional sheet occupying this tab, or None.

        Covers BOTH hunt-level working sheets and puzzle-owned extra sheets --
        unlike get_by_hunt, which deliberately excludes the latter. Used to prove
        a tab is not somebody's before the bot adopts it by name (D5).
        """
        return self._fetch_one(
            "SELECT * FROM additional_sheets WHERE google_page_id = %s LIMIT 1",
            (str(page_id),),
        )

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
    # voice_channel_id: written only by set_voice_channel (a stale PuzzleData
    # saved whole must not clear it, or its voice channel is never cleaned up).
    SPECIAL_ATTR = ['tags', 'additional_sheets', 'tag_edges', 'is_target', 'is_minimeta',
                    'voice_channel_id']

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
        row = self._fetch_one(
            "SELECT * FROM puzzles WHERE channel_id = %s ORDER BY `id` ASC LIMIT 1", (puzzle_id,))
        if row:
            return self.construct_puzzle(row)
        raise MissingPuzzleError(f"Unable to find puzzle {puzzle_id} for {round_id}")

    def get_by_attr(self,**kwargs):
        """Retrieve puzzle by attribute.  Only first sent attribute processed"""
        keyword, value = kwargs.popitem()
        if keyword:
            row = self._fetch_one(
                f"SELECT * FROM `{self.TABLE_NAME}` WHERE `{keyword}` = %s ORDER BY `id` ASC LIMIT 1", (value,))
            if row:
                return self.construct_puzzle(row)
            logger.info(f"Unable to find puzzle for {keyword} - {value}")
            return None

    def set_voice_channel(self, puzzle_id, channel_id):
        """Store (or clear, with None) a puzzle's voice channel. A single narrow
        UPDATE: a full commit() of a stale object could overwrite concurrent edits
        (see the docstring on commit)."""
        self._exec("UPDATE `puzzles` SET `voice_channel_id` = %s WHERE `id` = %s",
                   (channel_id, puzzle_id))

    def voice_channel_id(self, puzzle_id):
        """The stored voice channel id of the puzzle (None if unset or no such
        puzzle). A narrow read so a caller holding a stale PuzzleData can check."""
        row = self._fetch_one(
            "SELECT `voice_channel_id` FROM `puzzles` WHERE `id` = %s", (puzzle_id,))
        return row["voice_channel_id"] if row else None

    def is_solved(self, puzzle_id) -> bool:
        """The puzzle's current solved flag (False if no such puzzle). Narrow read."""
        row = self._fetch_one("SELECT `solved` FROM `puzzles` WHERE `id` = %s", (puzzle_id,))
        return bool(row and row["solved"])

    def channel_id_for_voice(self, voice_channel_id):
        """The text channel id of the puzzle whose voice channel is
        *voice_channel_id*, or None. One narrow, indexed query and no hydration:
        called for every voice join/leave in the guild. Silent (no log)."""
        row = self._fetch_one(
            "SELECT `channel_id` FROM `puzzles` WHERE `voice_channel_id` = %s LIMIT 1",
            (voice_channel_id,))
        return row["channel_id"] if row else None

    def voice_due(self, cutoff: datetime.datetime) -> List[PuzzleData]:
        """Solved puzzles in live hunts that still hold a voice channel and were
        solved at or before *cutoff* (naive UTC). solve_time is cleared on unsolve,
        so an unsolved puzzle never matches."""
        rows = self._fetch_all(
            "SELECT puzzles.* FROM puzzles JOIN hunts ON puzzles.hunt_id = hunts.id "
            "WHERE hunts.archived = 0 AND puzzles.voice_channel_id IS NOT NULL "
            "AND puzzles.voice_channel_id <> 0 AND puzzles.solved = 1 "
            "AND puzzles.solve_time IS NOT NULL AND puzzles.solve_time <= %s",
            (cutoff,))
        return self.construct_puzzles(rows)

    def with_voice(self, hunt_id) -> List[PuzzleData]:
        """Every puzzle of the hunt that has a voice channel id."""
        rows = self._fetch_all(
            "SELECT puzzles.* FROM puzzles WHERE hunt_id = %s "
            "AND voice_channel_id IS NOT NULL AND voice_channel_id <> 0", (hunt_id,))
        return self.construct_puzzles(rows)

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

    def _hydrate_is_target(self, puzzles):
        """Derive is-target: True iff some tag_group targets this puzzle.

        Eager-loaded here (the single read seam) so is-meta reads stay free
        attribute accesses. Lookup on tag_group.target_puzzle_id."""
        by_id = {p.id: p for p in puzzles}
        rows = self._fetch_all(
            "SELECT DISTINCT target_puzzle_id FROM tag_group"
            f" WHERE target_puzzle_id IN ({self._placeholders(len(by_id))})",
            tuple(by_id),
        )
        targeted = {row['target_puzzle_id'] for row in rows}
        for pid, puzzle in by_id.items():
            puzzle.is_target = pid in targeted

    def _hydrate_is_minimeta(self, puzzles):
        """Derive is-minimeta: True iff this puzzle has mini-children.

        A minimeta is keyed by parent_puzzle_id (the puzzle-keyed roll-up path),
        not tag_group.target_puzzle_id, so it is invisible to get_is_target even
        though its tab is promoted to the same shifted target geometry. Eager-load
        it here, the single read seam, so is-minimeta reads stay free attribute
        accesses (e.g. _header_shift needs it to offset solution/progress writes)."""
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

    def record(self, channel_id, guild_id, at, author_name):
        """Upsert the most recent human (non-bot) message seen in a channel."""
        self._exec(
            "INSERT INTO channel_activity "
            "(channel_id, guild_id, last_human_message_at, last_author_name) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "guild_id=VALUES(guild_id), "
            "last_human_message_at=VALUES(last_human_message_at), "
            "last_author_name=VALUES(last_author_name)",
            (channel_id, guild_id, at, author_name),
        )

class MySQLPuzzleActivityDb(_MySQLBaseDb):
    """Per-puzzle activity in 5-minute slots (puzzle_activity_slots, migration 052).

    Each write looks the puzzle up with a plain SELECT, then INSERTs VALUES. Not
    INSERT ... SELECT: under InnoDB REPEATABLE READ that takes shared locks on
    every puzzles row it scans (channel_id / google_sheet_id are unindexed), once
    per human message, holding up concurrent solves and creates. A plain SELECT is
    a lock-free consistent read. A channel or file that belongs to no puzzle
    writes nothing.
    """
    TABLE_NAME = 'puzzle_activity_slots'

    def _bump(self, column, value, at, update):
        slot = slot_start(at)
        for row in self._fetch_all(f"SELECT id FROM puzzles WHERE {column} = %s", (value,)):
            self._exec(
                "INSERT INTO puzzle_activity_slots (puzzle_id, slot_start, chat_count, sheet_edited) "
                f"VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE {update}",
                (row["id"], slot, 1 if column == "channel_id" else 0,
                 1 if column == "google_sheet_id" else 0),
            )

    def add_chat(self, channel_id, at):
        """Count one human message in ``channel_id``'s puzzle, in ``at``'s slot."""
        self._bump("channel_id", channel_id, at, "chat_count = chat_count + 1")

    def mark_sheet_edited(self, google_sheet_id, at):
        """Mark ``at``'s slot edited for the puzzle whose own spreadsheet this is."""
        if not google_sheet_id:
            return
        self._bump("google_sheet_id", google_sheet_id, at, "sheet_edited = 1")

    def files_hunt_folders(self):
        """(hunt_id, drive_folder_id) for every live files-mode hunt."""
        rows = self._fetch_all(
            "SELECT id, drive_folder_id FROM hunts "
            "WHERE sheet_mode = 'files' AND archived = 0 AND drive_folder_id <> ''")
        return [(r["id"], r["drive_folder_id"]) for r in rows]

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
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
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
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
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

    def delete_if_still_orphaned(self, media_id: int) -> int:
        """Delete this media row only if nothing references it, returning rowcount.

        get_orphaned derives a list; this re-checks the single row at the moment
        of deletion, closing the window in which a sweep's own iteration lets a
        newly-attached image be deleted anyway (C3). A 0 return means the row
        was claimed while the sweep was running and must be left alone -- files
        included.
        """
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "DELETE FROM media WHERE id = %s"
                " AND NOT EXISTS (SELECT 1 FROM evidence_items ei WHERE ei.media_id = %s)"
                " AND NOT EXISTS (SELECT 1 FROM hunt_canvases hc WHERE hc.media_id = %s)"
                " AND NOT EXISTS (SELECT 1 FROM puzzle_images pi WHERE pi.media_id = %s)"
                " AND NOT EXISTS (SELECT 1 FROM canvas_regions cr WHERE cr.crop_media_id = %s)",
                (media_id, media_id, media_id, media_id, media_id),
            )
            return cursor.rowcount
        finally:
            cursor.close()


class MySQLHuntCanvasDb(_MySQLBaseDb):
    TABLE_NAME = 'hunt_canvases'

    def create(self, hunt_id: int, media_id: int, name: str,
               note=None) -> 'HuntCanvasData':
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
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
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
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
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
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
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
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
            cursor.execute(
                "SELECT * FROM guilds WHERE guild_id = %s ORDER BY `id` ASC LIMIT 1", (guild_id,))
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
                settings.drive_parent_id,
                settings.admin_role_ids or None, settings.organiser_role_ids or None,
                settings.template_link or None, settings.multi_team_category or "",
                settings.archive_category or "")
        if settings.id > 0:
            update_stmt = ("UPDATE `guilds` SET `guild_id`=%s,`guild_name`=%s, `website_url`=%s,"
                           "`discord_bot_channel`=%s,`discord_bot_emoji`=%s,`discord_use_voice_channels`=%s,"
                           "`drive_parent_id`=%s,"
                           "`admin_role_ids`=%s,`organiser_role_ids`=%s,`template_link`=%s,"
                           "`multi_team_category`=%s,`archive_category`=%s "
                           "WHERE id=%s")
            data = data + (settings.id,)
            cursor.execute(update_stmt, data)
        else:
            insert_stmt = ("INSERT INTO `guilds`(`guild_id`, `guild_name`, `website_url`,"
                           "`discord_bot_channel`, `discord_bot_emoji`, `discord_use_voice_channels`, "
                           "`drive_parent_id`, "
                           "`admin_role_ids`, `organiser_role_ids`, `template_link`, "
                           "`multi_team_category`, `archive_category`) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
            cursor.execute(insert_stmt, data)
            settings.id = cursor.lastrowid or 0
        cursor.close()
        # Keep the read cache coherent: a prior get_cached() may have cached a
        # None (no row yet) or a stale copy, which would otherwise shadow this
        # freshly-written row until process restart.
        self.cached_settings[settings.guild_id] = settings

    def mark_setup_complete(self, settings: GuildSettings) -> None:
        """Stamp guilds.setup_complete_time with now (UTC): !setup_server's
        run-once lock. Its own UPDATE, never part of commit(), so no ordinary
        settings write can clear it."""
        now = datetime.datetime.now(datetime.timezone.utc)
        cursor = self.mydb.cursor()
        try:
            cursor.execute(
                "UPDATE `guilds` SET `setup_complete_time`=%s WHERE `id`=%s",
                (now.replace(tzinfo=None), settings.id))
        finally:
            cursor.close()
        settings.setup_complete_time = now
        self.cached_settings[settings.guild_id] = settings