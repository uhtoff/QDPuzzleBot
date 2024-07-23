import datetime
import errno
import json
import logging
from pathlib import Path
from typing import List

import pytz
from .puzzle_data import _PuzzleJsonDb, PuzzleData
from .puzzle_settings import _GuildSettingsDb, GuildSettings

logger = logging.getLogger(__name__)
class MySQLPuzzleJsonDb(_PuzzleJsonDb):
    def __init__(self, dir_path: Path, mydb):
        self.dir_path = dir_path
        self.mydb = mydb

    def puzzle_path(self, puzzle, round_id=None, hunt_id=None, guild_id=None) -> Path:
        """Store puzzle metadata to the path `guild/category/puzzle.json`

        Use unique ASCII ids (e.g. the discord id snowflakes) for each part of the
        path, to avoid potential shenanigans with unicode handling.

        Note:
            For convenience, with the Google Drive cog, the relative path
            to the puzzle metadata file will be saved in a column in the nexus
            spreadsheet.
        """
        if isinstance(puzzle, PuzzleData):
            puzzle_id = puzzle.channel_id
            round_id = puzzle.round_id
            guild_id = puzzle.guild_id
            hunt_id = puzzle.hunt_id
        elif isinstance(puzzle, (int, str)):
            puzzle_id = puzzle
            if round_id is None or guild_id is None or hunt_id is None:
                raise ValueError(f"round_id / guild_id not passed for puzzle {puzzle}")
        else:
            raise ValueError(f"Unknown puzzle type: {type(puzzle)} for {puzzle}")
        # TODO: Database would be better here .. who wants to sort through puzzle metadata by these ids?
        return (self.dir_path / str(guild_id) / str(hunt_id) / str(round_id) / str(puzzle_id)).with_suffix(".json")

    def commit(self, puzzle_data: PuzzleData):
        """Update puzzle metadata file"""
        puzzle_path = self.puzzle_path(puzzle_data)
        puzzle_path.parent.parent.mkdir(exist_ok=True)
        puzzle_path.parent.mkdir(exist_ok=True)
        with puzzle_path.open("w") as fp:
            fp.write(puzzle_data.to_json(indent=4))
        cursor = self.mydb.cursor()
        notes_json = json.dumps(puzzle_data.notes)
        data = (puzzle_data.name, puzzle_data.hunt_name, puzzle_data.hunt_id,
                puzzle_data.round_name, puzzle_data.round_id, puzzle_data.guild_id, puzzle_data.channel_id,
                puzzle_data.channel_mention, puzzle_data.voice_channel_id, puzzle_data.hunt_url,
                puzzle_data.google_sheet_id, puzzle_data.google_page_id, puzzle_data.status,
                puzzle_data.solution, puzzle_data.priority, puzzle_data.puzzle_type,
                notes_json, puzzle_data.start_time, puzzle_data.solve_time, puzzle_data.archive_time,)
        if puzzle_data.id > 0:
            update_stmt = ("UPDATE `puzzles` SET `name`=%s,`hunt_name`=%s,`hunt_id`=%s,"
                           "`round_name`=%s,`round_id`=%s,`guild_id`=%s,`channel_id`=%s,"
                           "`channel_mention`=%s,`voice_channel_id`=%s,`hunt_url`=%s,"
                           "`google_sheet_id`=%s,`google_page_id`=%s,`status`=%s,"
                           "`solution`=%s,`priority`=%s,`puzzle_type`=%s,"
                           "`notes`=%s,`start_time`=%s,`solve_time`=%s,`archive_time`=%s WHERE id=%s")
            data = data + (puzzle_data.id,)
            cursor.execute(update_stmt,data)
        else:
            insert_stmt = ("INSERT INTO `puzzles` (`name`, `hunt_name`, `hunt_id`, "
                           "`round_name`, `round_id`, `guild_id`, `channel_id`, "
                           "`channel_mention`, `voice_channel_id`, `hunt_url`, "
                           "`google_sheet_id`, `google_page_id`, `status`, "
                           "`solution`, `priority`, `puzzle_type`, "
                           "`notes`, `start_time`, `solve_time`, `archive_time`) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
            cursor.execute(insert_stmt, data)
            puzzle_data.id = cursor.lastrowid
        cursor.close()
        self.mydb.commit()

    def delete(self, puzzle_data):
        puzzle_path = self.puzzle_path(puzzle_data)
        try:
            puzzle_path.unlink()
        except IOError:
            pass

    def get(self, guild_id, puzzle_id, round_id, hunt_id) -> PuzzleData:
        cursor = self.mydb.cursor(dictionary = True)
        cursor.execute("SELECT * FROM puzzles WHERE channel_id = %s", (puzzle_id,))
        row = cursor.fetchone()
        if row:
            puz = PuzzleData()
            puz.id = row['id']
            puz.channel_id = row['channel_id']
            puz.round_id = row['round_id']
            puz.hunt_id = row['hunt_id']
            puz.guild_id = row['guild_id']
            puz.name = row['name']
            puz.hunt_name = row['hunt_name']
            puz.round_name = row['round_name']
            puz.channel_mention = row['channel_mention']
            puz.voice_channel_id = row['voice_channel_id']
            puz.hunt_url = row['hunt_url']
            puz.google_sheet_id = row['google_sheet_id']
            puz.google_page_id = row['google_page_id']
            puz.status = row['status']
            puz.solution = row['solution']
            puz.priority = row['priority']
            puz.puzzle_type = row['puzzle_type']
            puz.notes = json.loads(row['notes'])
            puz.start_time = row['start_time']
            puz.solve_time = row['solve_time']
            puz.archive_time = row['archive_time']
            return puz
        else:
            try:
                with self.puzzle_path(puzzle_id, hunt_id=hunt_id, round_id=round_id, guild_id=guild_id).open() as fp:
                    return PuzzleData.from_json(fp.read())
            except (IOError, OSError) as exc:
                # can also just catch FileNotFoundError
                if exc.errno == errno.ENOENT:
                    raise MissingPuzzleError(f"Unable to find puzzle {puzzle_id} for {round_id}")
                raise


    def get_all(self, guild_id, hunt_id="*") -> List[PuzzleData]:
        puzzle_datas = []
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT * FROM puzzles WHERE hunt_id = %s", (hunt_id,))
        rows = cursor.fetchall()
        for row in rows:
            puz = PuzzleData()
            puz.id = row['id']
            puz.channel_id = row['channel_id']
            puz.round_id = row['round_id']
            puz.hunt_id = row['hunt_id']
            puz.guild_id = row['guild_id']
            puz.name = row['name']
            puz.hunt_name = row['hunt_name']
            puz.round_name = row['round_name']
            puz.channel_mention = row['channel_mention']
            puz.voice_channel_id = row['voice_channel_id']
            puz.hunt_url = row['hunt_url']
            puz.google_sheet_id = row['google_sheet_id']
            puz.google_page_id = row['google_page_id']
            puz.status = row['status']
            puz.solution = row['solution']
            puz.priority = row['priority']
            puz.puzzle_type = row['puzzle_type']
            puz.notes = json.loads(row['notes'])
            puz.start_time = row['start_time']
            puz.solve_time = row['solve_time']
            puz.archive_time = row['archive_time']
            puzzle_datas.append(puz)

        return PuzzleData.sort_by_round_start(puzzle_datas)

    def get_all_fs(self, guild_id, hunt_id="*") -> List[PuzzleData]:
        paths = self.dir_path.rglob(f"{guild_id}/{hunt_id}/*/*.json")
        puzzle_datas = []
        for path in paths:
            try:
                with path.open() as fp:
                    puzzle_datas.append(PuzzleData.from_json(fp.read()))
            except Exception:
                logger.exception(f"Unable to load puzzle data from {path}")
        return PuzzleData.sort_by_round_start(puzzle_datas)

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

    def aggregate_json(self) -> dict:
        """Aggregate all puzzle metadata into a single JSON object, for convenience

        Might be handy with a JSON viewer such as `IPython.display.JSON`.
        """
        paths = self.dir_path.rglob(f"*/*.json")
        result = {}
        for path in paths:
            relpath = path.relative_to(self.dir_path)
            with path.open() as fp:
                result[str(relpath)] = json.load(fp)
        return result

class MySQLGuildSettingsDb():
    def __init__(self, dir_path: Path, mydb):
        self.dir_path = dir_path
        self.cached_settings = {}
        self.mydb = mydb

    def get(self, guild_id: int) -> GuildSettings:
        settings_path = self.dir_path / str(guild_id) / "settings.json"
        if settings_path.exists():
            with settings_path.open() as fp:
                settings = GuildSettings.from_json(fp.read())
        else:
            # Populate empty settings file
            settings = GuildSettings(guild_id=guild_id)
            self.commit(settings)
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute("SELECT * FROM guilds WHERE guild_id = %s", (guild_id,))
        row = cursor.fetchone()
        if row:
            settings.id = row['id']
        cursor.close()
        for hunt_id in settings.hunt_settings:
            hunt = settings.hunt_settings[hunt_id]
            cursor = self.mydb.cursor(dictionary=True)
            cursor.execute("SELECT * FROM hunts WHERE hunt_id = %s", (hunt_id,))
            row = cursor.fetchone()
            if row:
                hunt.id = row['id']
            cursor.close()
        return settings

    def get_cached(self, guild_id: int) -> GuildSettings:
        if guild_id in self.cached_settings:
            return self.cached_settings[guild_id]
        settings = self.get(guild_id)
        self.cached_settings[guild_id] = settings
        return settings

    def commit(self, settings: GuildSettings):
        settings_path = self.dir_path / str(settings.guild_id) / "settings.json"
        settings_path.parent.parent.mkdir(exist_ok=True)
        settings_path.parent.mkdir(exist_ok=True)
        with settings_path.open("w") as fp:
            fp.write(settings.to_json(indent=4))
        self.cached_settings[settings.guild_id] = settings
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
        for hunt_id in settings.hunt_settings:
            hunt = settings.hunt_settings[hunt_id]
            cursor = self.mydb.cursor()
            data = (hunt_id, settings.id, hunt.hunt_url_sep,
                    hunt.hunt_name, hunt.hunt_url, hunt.hunt_puzzle_prefix,
                    hunt.drive_sheet_id, hunt.role_id, hunt.username, hunt.password)
            if hunt.id > 0:
                update_stmt = ("UPDATE `hunts` SET `hunt_id`=%s, `guild_id`=%s,"
                               "`hunt_url_sep`=%s,`hunt_name`=%s,`hunt_url`=%s,"
                               "`hunt_puzzle_prefix`=%s,`drive_sheet_id`=%s,`role_id`=%s,"
                               "`username`=%s,`password`=%s "
                               "WHERE id=%s")
                data = data + (hunt.id,)
                cursor.execute(update_stmt, data)
            else:
                insert_stmt = ("INSERT INTO `hunts`(`hunt_id`, `guild_id`, "
                               "`hunt_url_sep`, `hunt_name`, `hunt_url`, "
                               "`hunt_puzzle_prefix`, `drive_sheet_id`, `role_id`, "
                               "`username`, `password`) "
                               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
                cursor.execute(insert_stmt, data)
                hunt.id = cursor.lastrowid
            cursor.close()
            self.mydb.commit()
        for round_channel in settings.category_mapping:
            hunt_channel = settings.category_mapping[round_channel]
            insert_stmt = ("INSERT INTO `rounds` (`hunt_id`, `round_channel`) "
                           "VALUE (%s,%s)")
            data = (settings.hunt_settings[hunt_channel].id, round_channel)
            cursor = self.mydb.cursor()
            try:
                cursor.execute(insert_stmt, data)
                cursor.close()
                self.mydb.commit()
            except:
                pass