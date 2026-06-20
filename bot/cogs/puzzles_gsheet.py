""" Google Drive integration for puzzle organization

This is a separate cog so that Google Drive integration
can be easily disabled; simply omit this file.
"""
import asyncio
import contextvars
import logging
import copy
from typing import List

from discord.ext import commands

from bot.utils import config
from bot.store import PuzzleData, RoundData, RoundJsonDb
from bot.utils.gsheets import get_sheet, get_drive

logger = logging.getLogger(__name__)

# Request-scoped target spreadsheets. Each Discord command runs in its own asyncio
# Task, and ContextVars are isolated per Task, so concurrent commands from different
# teams/hunts cannot overwrite each other's target spreadsheet.
_current_spreadsheet_id = contextvars.ContextVar("current_spreadsheet_id", default=None)
_current_archive_spreadsheet_id = contextvars.ContextVar("current_archive_spreadsheet_id", default=None)

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
        print(get_sheet().get(spreadsheetId=self.get_spreadsheet_id()).execute())

    def set_spreadsheet_id(self, spreadsheet_id):
        _current_spreadsheet_id.set(spreadsheet_id)

    def set_archive_spreadsheet_id(self, archive_spreadsheet_id):
        _current_archive_spreadsheet_id.set(archive_spreadsheet_id)

    def get_spreadsheet_id(self):
        return _current_spreadsheet_id.get()

    def get_archive_spreadsheet_id(self):
        return _current_archive_spreadsheet_id.get()

    async def batch_update(self, body, archive = False):
        spreadsheet_id = self.get_archive_spreadsheet_id() if archive else self.get_spreadsheet_id()
        req = get_sheet().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=body
        )

        return await asyncio.to_thread(req.execute)


    def sheet_list(self, spreadsheet_id = None):
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        return get_sheet().get(spreadsheetId=spreadsheet_id).execute()

    def get_grid_size(self, old_puzzle) -> tuple:
        spreadsheet_id = self.get_archive_spreadsheet_id() if old_puzzle.archived else self.get_spreadsheet_id()
        response = get_sheet().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))"
        ).execute()

        for sheet in response.get("sheets",[]):
            props = sheet.get("properties", {})
            if props.get("sheetId") == int(old_puzzle.google_page_id):
                return props.get("gridProperties", {}).get("rowCount", 0), props.get("gridProperties", {}).get("columnCount", 0)

        return 0,0

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
            meta = get_sheet().get(
                spreadsheetId=spreadsheet_id
            ).execute()
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

    def move_sheet_to_start(self, sheet_id = None):
        new_index = self.INITIAL_OFFSET
        body = {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'index': new_index
                        },
                        'fields': 'index'

                    }
                }
        return body

    def move_sheet_to_end(self, sheet_id = None):
        sheets = self.sheet_list().get('sheets',[])
        new_index = len(sheets) - 1
        body = {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'index': new_index
                        },
                        'fields': 'index'

                    }
                }
        return body

    def revert_tab_colour(self, sheet_id):
        body = {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'tabColor': None
                        },
                        'fields': 'tabColor'

                    }
                }
        return body

    def change_tab_colour(self, colour, sheet_id):
        body = {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'tabColor': {colour: 1}
                        },
                        'fields': 'tabColor'

                    }
                }
        return body

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
        body = {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'title': name
                        },
                        'fields': 'title'

                    }
                }
        return body


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
        copied = await asyncio.to_thread(copy_req.execute)
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
        await asyncio.to_thread(bu_req.execute)
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
        new_file = get_drive().files().copy(fileId=config.master_spreadsheet, body=body).execute()
        new_file_id = new_file['id']
        get_drive().permissions().create(fileId=new_file_id, body=permission).execute()
        return new_file_id

    async def create_hunt_archive_spreadsheet(self, hunt_name):
        permission = {
            'type': 'anyone',
            'role': 'writer'
        }
        new_file = get_drive().files().create(body={
            "name": hunt_name + " Archive",
            "mimeType": "application/vnd.google-apps.spreadsheet",
        }).execute()
        new_file_id = new_file['id']
        get_drive().permissions().create(fileId=new_file_id, body=permission).execute()
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
        await self.copy_puzzle(old_puzzle, puzzle_data)
        await self.delete_puzzle_spreadsheet(old_puzzle)
        await self.update_puzzle_info(puzzle_data, True)

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