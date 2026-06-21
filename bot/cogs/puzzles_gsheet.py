""" Google Drive integration for puzzle organization

This is a separate cog so that Google Drive integration
can be easily disabled; simply omit this file.
"""
import contextvars
import logging
import copy
from typing import List

from discord.ext import commands

from bot.utils import config
from bot.store import PuzzleData, RoundJsonDb
from bot.utils.gsheets import get_sheet, get_drive, execute_sync, execute_with_retry

logger = logging.getLogger(__name__)

# Request-scoped target spreadsheets. Each Discord command runs in its own asyncio
# Task, and ContextVars are isolated per Task, so concurrent commands from different
# teams/hunts cannot overwrite each other's target spreadsheet.
_current_spreadsheet_id = contextvars.ContextVar("current_spreadsheet_id", default=None)
_current_archive_spreadsheet_id = contextvars.ContextVar("current_archive_spreadsheet_id", default=None)
# Channel to notify if a Sheets call has to be retried, plus a one-shot flag so a
# single command posts at most one "retrying" notice. Request-scoped, like above.
_current_notify_channel = contextvars.ContextVar("current_notify_channel", default=None)
_notified_flag = contextvars.ContextVar("notified_flag", default=None)

class GoogleSheets(commands.Cog):
    STRING_INPUT = "stringValue"
    FORMULA_INPUT = "formulaValue"
    PUZZLE_SHEET = "Tab template"
    METAPUZZLE_SHEET = "Meta Tab template"
    FIRST_PUZZLE_ROW = 3
    FIRST_PUZZLE_COLUMN = 0
    FIRST_METAPUZZLE_COLUMN = 2
    INITIAL_OFFSET = 7
    sheets_service = None

    def __init__(self, bot):
        self.bot = bot
        self._puzzle_data = None
        self.overview_page_id = None
        self.sheets_service = get_sheet()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")

    @commands.command()
    async def read(self, ctx):
        print(await execute_with_retry(get_sheet().get(spreadsheetId=self.get_spreadsheet_id())))

    def set_spreadsheet_id(self, spreadsheet_id):
        _current_spreadsheet_id.set(spreadsheet_id)

    def set_archive_spreadsheet_id(self, archive_spreadsheet_id):
        _current_archive_spreadsheet_id.set(archive_spreadsheet_id)

    def get_spreadsheet_id(self):
        return _current_spreadsheet_id.get()

    def get_archive_spreadsheet_id(self):
        return _current_archive_spreadsheet_id.get()

    def set_notify_channel(self, channel):
        """Register the channel to warn if Sheets calls for this command stall."""
        _current_notify_channel.set(channel)
        _notified_flag.set([False])

    async def _notify_retry(self):
        """on_retry hook for execute_with_retry: warn the user once per command
        that a Google Sheets call is being retried (so the wait isn't silent)."""
        channel = _current_notify_channel.get()
        flag = _notified_flag.get()
        if channel is None or flag is None or flag[0]:
            return
        flag[0] = True
        try:
            await channel.send(
                "⏳ Google Sheets is busy (rate-limited); retrying your change — "
                "this may take a few seconds."
            )
        except Exception:
            logger.exception("Failed to send Sheets retry notice")

    async def batch_update(self, body, archive = False):
        spreadsheet_id = self.get_archive_spreadsheet_id() if archive else self.get_spreadsheet_id()
        req = get_sheet().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=body
        )

        return await execute_with_retry(req, on_retry=self._notify_retry)


    def sheet_list(self, spreadsheet_id = None):
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        return execute_sync(get_sheet().get(spreadsheetId=spreadsheet_id))

    def get_grid_size(self, old_puzzle) -> tuple:
        target = int(old_puzzle.google_page_id)
        # Look in the expected spreadsheet first, but fall back to the other one:
        # if the archived flag is out of sync with where the sheet actually lives,
        # querying only one would miss it and silently return (0, 0).
        primary = self.get_archive_spreadsheet_id() if old_puzzle.archived else self.get_spreadsheet_id()
        secondary = self.get_spreadsheet_id() if old_puzzle.archived else self.get_archive_spreadsheet_id()
        seen = []
        for spreadsheet_id in (primary, secondary):
            if not spreadsheet_id:
                continue
            response = execute_sync(get_sheet().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))"
            ))
            for sheet in response.get("sheets", []):
                props = sheet.get("properties", {})
                seen.append(props.get("sheetId"))
                if props.get("sheetId") == target:
                    grid = props.get("gridProperties", {})
                    return grid.get("rowCount", 0), grid.get("columnCount", 0)

        logger.warning(
            "get_grid_size: sheet %s not found (searched %s; saw ids %s)",
            target, [s for s in (primary, secondary) if s], seen,
        )
        return 0, 0

    async def update_grid_size(self, sheet_id, row_count, column_count):
        body = {
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {
                                "rowCount": row_count,
                                "columnCount": column_count
                            }
                        },
                        "fields": "gridProperties(rowCount,columnCount)"

                    }
                }
            ]
        }
        await self.batch_update(body)
        return body

    def get_unique_tab_name(self, desired_name: str, spreadsheet_id = None) -> str:
        if spreadsheet_id is None:
            existing = {
                s["properties"]["title"].casefold()
                for s in self.sheet_list()["sheets"]
            }
        else:
            meta = execute_sync(get_sheet().get(
                spreadsheetId=spreadsheet_id
            ))
            existing = {
                s["properties"]["title"].casefold()
                for s in meta["sheets"]
            }

        if desired_name.casefold() not in existing:
            return desired_name

        i = 2
        while True:
            candidate = f"{desired_name} ({i})"
            if candidate.casefold() not in existing:
                return candidate
            i += 1

    def get_page_id_by_name(self, name):
        for sheet in self.sheet_list()["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['sheetId']

    def get_page_name_by_id(self, id, spreadsheet_id = None):
        for sheet in self.sheet_list(spreadsheet_id)["sheets"]:
            if (sheet['properties']['sheetId']) == int(id):
                return sheet['properties']['title']

    def get_puzzle_sheet_index(self, puzzle:PuzzleData):
        name = puzzle.name
        for sheet in self.sheet_list()["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['index']
        return self.INITIAL_OFFSET

    def get_row(self, ref):
        return int(ref[1]) - 1

    def get_column(self, ref):
        return ord(ref[0].lower()) - 97

    async def update_puzzle_info(self, puzzle: PuzzleData, update_tab_name = False, name = None, page_id = None):

        puzzle_name = name or puzzle.name

        new_sheet_id = page_id or puzzle.google_page_id
        requests = [
            self.update_cell('=HYPERLINK("' + puzzle.url + '","' + puzzle_name + '")', self.get_row(config.puzzle_cell_name),
                             self.get_column(config.puzzle_cell_name), self.FORMULA_INPUT, new_sheet_id),
        ]
        if update_tab_name:
            unique_name = self.get_unique_tab_name(puzzle_name)
            requests.append(self.set_sheet_name(unique_name, puzzle.google_page_id))
        updates = {
            'requests': requests
        }
        await self.batch_update(updates, puzzle.archived)

    async def copy_puzzle(self, old_puzzle, new_puzzle):
        old_grid_rows, old_grid_columns = self.get_grid_size(old_puzzle)
        if not old_grid_rows or not old_grid_columns:
            # get_grid_size couldn't locate the source sheet; resizing to 0 rows
            # would be rejected by Sheets ("can't delete all the rows") and copying
            # nothing would lose data, so abort rather than corrupt the sheet.
            raise RuntimeError(
                f"Couldn't read the source sheet (page id {old_puzzle.google_page_id}) "
                "while converting the puzzle; aborting before resizing it to zero rows."
            )
        if new_puzzle.is_metapuzzle():
            new_grid_columns = old_grid_columns + 2
        else:
            new_grid_columns = old_grid_columns
        new_grid_rows = old_grid_rows
        await self.update_grid_size(new_puzzle.google_page_id, new_grid_rows, new_grid_columns)
        old_start_row = new_start_row = self.FIRST_PUZZLE_ROW
        if new_puzzle.is_metapuzzle():
            old_start_column = self.FIRST_PUZZLE_COLUMN
            new_start_column = self.FIRST_METAPUZZLE_COLUMN
        else:
            old_start_column = self.FIRST_METAPUZZLE_COLUMN
            new_start_column = self.FIRST_PUZZLE_COLUMN
        body = {
            'requests': [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": old_puzzle.google_page_id,
                            "startRowIndex": old_start_row,
                            "endRowIndex": old_grid_rows,
                            "startColumnIndex": old_start_column,
                            "endColumnIndex": old_grid_columns,
                        },
                        "destination": {
                            "sheetId": new_puzzle.google_page_id,
                            "startRowIndex": new_start_row,
                            "endRowIndex": new_grid_rows,
                            "startColumnIndex": new_start_column,
                            "endColumnIndex": new_grid_columns,
                        },
                        "pasteType": "PASTE_NORMAL",
                        "pasteOrientation": "NORMAL"
                    }
                }

            ]
        }

        await self.batch_update(body, old_puzzle.archived)

    def update_cell(self, value, start_row, start_column, type=STRING_INPUT, sheet_id=None):
        if sheet_id is None:
            sheet_id = self.get_puzzle_data().google_page_id
        body = {
                    'updateCells': {
                        'rows': [
                            {
                                'values':
                                    {
                                        'userEnteredValue': {
                                            type: value
                                        }
                                    }
                            }
                        ],
                        'fields': 'userEnteredValue',
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': start_row,
                            'endRowIndex': start_row+1,
                            'startColumnIndex': start_column,
                            'endColumnIndex': start_column+1
                        }
                    }
                }
        return body

    @staticmethod
    def _sheet_props_request(sheet_id, key, value):
        """Build an updateSheetProperties request that sets a single property."""
        return {
            'updateSheetProperties': {
                'properties': {
                    'sheetId': sheet_id,
                    key: value
                },
                'fields': key
            }
        }

    def move_sheet_to_start(self, sheet_id = None):
        return self._sheet_props_request(sheet_id, 'index', self.INITIAL_OFFSET)

    def move_sheet_to_end(self, sheet_id = None):
        sheets = self.sheet_list().get('sheets',[])
        new_index = len(sheets) - 1
        return self._sheet_props_request(sheet_id, 'index', new_index)

    def revert_tab_colour(self, sheet_id):
        return self._sheet_props_request(sheet_id, 'tabColor', None)

    def change_tab_colour(self, colour, sheet_id):
        return self._sheet_props_request(sheet_id, 'tabColor', {colour: 1})

    async def add_new_sheet(self, name, index=INITIAL_OFFSET, sheet_type=PUZZLE_SHEET):
        sheet_id = self.get_page_id_by_name(sheet_type)
        name = self.get_unique_tab_name(name)
        body = {
            'requests': [
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'hidden': False
                        },
                        'fields': 'hidden'

                    }
                },
                {
                    "duplicateSheet": {
                        "sourceSheetId": sheet_id,
                        "insertSheetIndex": index,
                        "newSheetName": name
                    }
                },
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'hidden': True
                        },
                        'fields': 'hidden'

                    }
                }

            ]
        }
        new_sheet = await self.batch_update(body)
        new_sheet_id = new_sheet['replies'][1]['duplicateSheet']['properties']['sheetId']
        return new_sheet_id

    async def add_new_puzzle_sheet(self, puzzle: PuzzleData, index = INITIAL_OFFSET):
        if puzzle.metapuzzle == 1:
            puzzle.google_page_id = await self.add_new_sheet(self.get_unique_tab_name(puzzle.name), index, self.METAPUZZLE_SHEET)
        else:
            puzzle.google_page_id = await self.add_new_sheet(self.get_unique_tab_name(puzzle.name), index)


    def set_sheet_name(self, name, sheet_id = None):
        return self._sheet_props_request(sheet_id, 'title', name)


    async def delete_sheet(self, page_id = None, archive = False):
        body = {
            'requests': [
                {
                    'deleteSheet': {
                        'sheetId': page_id,
                    }
                }
            ]
        }
        await self.batch_update(body, archive)

    async def update_puzzle(self, puzzle_data,update_name = False):
        await self.update_puzzle_info(puzzle_data, update_name)

    async def delete_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        await self.delete_sheet(puzzle_data.google_page_id, puzzle_data.archived)

    async def move_puzzle_spreadsheet(self, to_archive, page, tab_name):
        spreadsheet_to = self.get_archive_spreadsheet_id() if to_archive else self.get_spreadsheet_id()
        spreadsheet_from = self.get_spreadsheet_id() if to_archive else self.get_archive_spreadsheet_id()
        copy_req = get_sheet().sheets().copyTo(
            spreadsheetId=spreadsheet_from,
            sheetId=page,
            body={"destinationSpreadsheetId": spreadsheet_to}
        )
        copied = await execute_with_retry(copy_req, on_retry=self._notify_retry)
        new_sheet_id = copied['sheetId']
        await self.delete_sheet(page, not to_archive)
        unique_title = self.get_unique_tab_name(tab_name, spreadsheet_to)
        bu_req = get_sheet().batchUpdate(
            spreadsheetId=spreadsheet_to,
            body={
                "requests": [
                    self.set_sheet_name(unique_title, new_sheet_id),
                ]
            },
        )
        await execute_with_retry(bu_req, on_retry=self._notify_retry)
        return new_sheet_id

    async def restore_puzzle_spreadsheet(self, puzzle_data: PuzzleData, archive_spreadsheet = None):
        if puzzle_data.archived:
            puzzle_data.archived = False
            puzzle_data.google_page_id = await self.move_puzzle_spreadsheet(False, puzzle_data.google_page_id,
                                                                            puzzle_data.name)
        requests = []
        for sheet in puzzle_data.additional_sheets:
            if len(sheet.google_page_id) > 0:
                if self.get_archive_spreadsheet_id() and puzzle_data.solved:
                    current_name = self.get_page_name_by_id(sheet.google_page_id, self.get_archive_spreadsheet_id())
                    sheet.google_page_id = await self.move_puzzle_spreadsheet(False, sheet.google_page_id, current_name)
                requests.extend([self.update_cell("", self.get_row(config.puzzle_cell_solution),
                                             self.get_column(config.puzzle_cell_solution), self.STRING_INPUT,
                                             sheet.google_page_id),
                            self.update_cell("", self.get_row(config.puzzle_cell_progress),
                                             self.get_column(config.puzzle_cell_progress), self.STRING_INPUT,
                                             sheet.google_page_id),
                            self.move_sheet_to_start(sheet.google_page_id),
                            self.revert_tab_colour(sheet.google_page_id)])

        requests.extend([self.update_cell("", self.get_row(config.puzzle_cell_solution),
                                     self.get_column(config.puzzle_cell_solution), self.STRING_INPUT, puzzle_data.google_page_id),
                    self.update_cell("", self.get_row(config.puzzle_cell_progress),
                                     self.get_column(config.puzzle_cell_progress), self.STRING_INPUT, puzzle_data.google_page_id),
                    self.move_sheet_to_start(puzzle_data.google_page_id),
                    self.revert_tab_colour(puzzle_data.google_page_id)])
        updates = {
            'requests': requests
        }
        await self.batch_update(updates)


    async def update_solution(self, puzzle_data: PuzzleData):
        requests = [self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution),
                                     self.get_column(config.puzzle_cell_solution), self.STRING_INPUT, puzzle_data.google_page_id)
                    ]
        updates = {
            'requests': requests
        }
        await self.batch_update(updates, puzzle_data.archived)

    async def archive_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        requests = []
        requests.extend([self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution), self.get_column(config.puzzle_cell_solution), self.STRING_INPUT, puzzle_data.google_page_id),
            self.update_cell("Solved", self.get_row(config.puzzle_cell_progress), self.get_column(config.puzzle_cell_progress), self.STRING_INPUT, puzzle_data.google_page_id),
            self.move_sheet_to_end(puzzle_data.google_page_id),
            self.change_tab_colour('green', puzzle_data.google_page_id)])
        for sheet in puzzle_data.additional_sheets:
            if len(sheet.google_page_id) > 0:
                requests.extend([self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution),
                                             self.get_column(config.puzzle_cell_solution), self.STRING_INPUT,
                                             sheet.google_page_id),
                            self.update_cell("Solved", self.get_row(config.puzzle_cell_progress),
                                             self.get_column(config.puzzle_cell_progress), self.STRING_INPUT,
                                             sheet.google_page_id),
                            self.move_sheet_to_end(sheet.google_page_id),
                            self.change_tab_colour('green', sheet.google_page_id)])
        updates = {
            'requests': requests
        }
        await self.batch_update(updates)
        for sheet in puzzle_data.additional_sheets:
            if self.get_archive_spreadsheet_id():
                current_name = self.get_page_name_by_id(sheet.google_page_id)
                sheet.google_page_id = await self.move_puzzle_spreadsheet(True, sheet.google_page_id, current_name)
        if puzzle_data.is_metapuzzle() is False and self.get_archive_spreadsheet_id():
            puzzle_data.google_page_id = await self.move_puzzle_spreadsheet(True, puzzle_data.google_page_id, puzzle_data.name)
            puzzle_data.archived = True
            return
        else:
            return

    async def create_hunt_spreadsheet(self, hunt_name):
        permission = {
            'type': 'anyone',
            'role': 'writer'
        }
        body = {
            'name': hunt_name
        }
        new_file = await execute_with_retry(get_drive().files().copy(fileId=config.master_spreadsheet, body=body), on_retry=self._notify_retry)
        new_file_id = new_file['id']
        await execute_with_retry(get_drive().permissions().create(fileId=new_file_id, body=permission), on_retry=self._notify_retry)
        return new_file_id

    async def create_hunt_archive_spreadsheet(self, hunt_name):
        permission = {
            'type': 'anyone',
            'role': 'writer'
        }
        new_file = await execute_with_retry(get_drive().files().create(body={
            "name": hunt_name + " Archive",
            "mimeType": "application/vnd.google-apps.spreadsheet",
        }), on_retry=self._notify_retry)
        new_file_id = new_file['id']
        await execute_with_retry(get_drive().permissions().create(fileId=new_file_id, body=permission), on_retry=self._notify_retry)
        self.set_archive_spreadsheet_id(new_file_id)
        return new_file_id


    async def create_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        """Creates new puzzle spreadsheet and adds puzzle data to the overview spreadsheet."""
        puzzle_index = self.INITIAL_OFFSET
        await self.add_new_puzzle_sheet(puzzle_data, puzzle_index)
        await self.update_puzzle_info(puzzle_data)

    async def create_additional_spreadsheet(self, puzzle_data: PuzzleData, name = None, puzzle = False):
        index = self.get_puzzle_sheet_index(puzzle_data)
        if name:
            sheet_name = f"{name} ({puzzle_data.name})"
        else:
            sheet_name = f"Extra Sheet for {puzzle_data.name}"
        sheet_id = await self.add_new_sheet(sheet_name, index+1)
        await self.update_puzzle_info( puzzle_data, False,name,sheet_id)
        return sheet_id

    async def convert_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        """Converts puzzle sheet from a metapuzzle to a feeder and vice versa"""
        old_puzzle = copy.copy(puzzle_data)
        await self.add_new_puzzle_sheet(puzzle_data)
        try:
            await self.copy_puzzle(old_puzzle, puzzle_data)
            await self.delete_puzzle_spreadsheet(old_puzzle)
            await self.update_puzzle_info(puzzle_data, True)
        except Exception:
            # The convert isn't atomic: we've already created the replacement sheet.
            # If a later step fails, delete that orphan and restore the original page
            # id so a failed convert leaves the puzzle as it was rather than stranding
            # a duplicate tab (the source of the "Extra"/"Extra (2)" broken state).
            try:
                await self.delete_sheet(puzzle_data.google_page_id)
            except Exception:
                logger.exception("Failed to clean up half-created sheet %s after a failed convert",
                                 puzzle_data.google_page_id)
            puzzle_data.google_page_id = old_puzzle.google_page_id
            raise

    async def add_metapuzzle_data(self, puzzle: PuzzleData, round_puzzles: List[PuzzleData]):
        requests = [self.update_cell("Puzzle titles",4,0,self.STRING_INPUT,puzzle.google_page_id),
                    self.update_cell("Puzzle solutions", 4, 1, self.STRING_INPUT,puzzle.google_page_id),]
        row = 5
        for round_puzzle in round_puzzles:
            if round_puzzle.id == puzzle.id: # Skip self
                continue
            requests.append(self.update_cell(round_puzzle.name, row, 0, self.STRING_INPUT,puzzle.google_page_id))
            if round_puzzle.solution:
                if round_puzzle.solution == "✅":
                    continue
                else:
                    solution = round_puzzle.solution
            else:
                solution = "Unsolved"
            requests.append(self.update_cell(solution, row, 1, self.STRING_INPUT,puzzle.google_page_id))
            row += 1
        for x in range(50):
            requests.append(self.update_cell("", row, 0, self.STRING_INPUT,puzzle.google_page_id))
            requests.append(self.update_cell("", row, 1, self.STRING_INPUT,puzzle.google_page_id))
            row += 1
        updates = {
            'requests': requests
        }
        await self.batch_update(updates, puzzle.archived)

    async def add_metametapuzzle_data(self, puzzle: PuzzleData, round_puzzles: List[PuzzleData]):
        requests = [self.update_cell("Puzzle Round",4,0,self.STRING_INPUT,puzzle.google_page_id),
                    self.update_cell("Puzzle Title", 4, 1, self.STRING_INPUT,puzzle.google_page_id),
                    self.update_cell("Puzzle Solution", 4, 2, self.STRING_INPUT,puzzle.google_page_id),]
        row = 5
        for round_puzzle in round_puzzles:
            if round_puzzle.id == puzzle.id: # Skip self
                continue
            if round_puzzle.metapuzzle == 1:
                continue
            if len(round_puzzle.tags) > 1:
                continue

            if round_puzzle.tags:
                round = RoundJsonDb.get_by_attr(id=round_puzzle.tags[0])
                if round.meta_code == "RETAWR" or round.meta_code == "LAOFNO":
                    continue
                round_name = round.name
            else:
                round_name = "No Round"
            requests.append(self.update_cell(round_name, row, 0, self.STRING_INPUT, puzzle.google_page_id))
            requests.append(self.update_cell(round_puzzle.name, row, 1, self.STRING_INPUT,puzzle.google_page_id))
            if round_puzzle.solution:
                if round_puzzle.solution == "✅":
                    continue
                else:
                    solution = round_puzzle.solution
            else:
                solution = "Unsolved"
            requests.append(self.update_cell(solution, row, 2, self.STRING_INPUT,puzzle.google_page_id))
            row += 1
        for x in range(50):
            requests.append(self.update_cell("", row, 0, self.STRING_INPUT, puzzle.google_page_id))
            requests.append(self.update_cell("", row, 1, self.STRING_INPUT,puzzle.google_page_id))
            requests.append(self.update_cell("", row, 2, self.STRING_INPUT,puzzle.google_page_id))
            row += 1
        updates = {
            'requests': requests
        }
        await self.batch_update(updates, puzzle.archived)

async def setup(bot):
    # Comment this out if google-drive-related package are not installed!
    await bot.add_cog(GoogleSheets(bot))