""" Google Drive integration for puzzle organization

This is a separate cog so that Google Drive integration
can be easily disabled; simply omit this file.
"""

import datetime
import logging
import string
import traceback
from typing import List, Optional

import discord
from discord.ext import commands, tasks
import gspread_asyncio
import gspread_formatting
import pytz

from bot.utils import urls, config
from bot.store import MissingPuzzleError, PuzzleData, PuzzleJsonDb, GuildSettings, GuildSettingsDb, HuntSettings, RoundData, RoundJsonDb, HuntJsonDb, HuntData
from bot.utils.gsheets import get_sheet, get_drive
from bot.utils.appscript import create_project, add_javascript
from bot.utils.gsheet_nexus import update_nexus
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def escape_apostrophes(name):
    return name.replace("'", "''")


class GoogleSheets(commands.Cog):
    STRING_INPUT = "stringValue"
    FORMULA_INPUT = "formulaValue"
    PUZZLE_SHEET = "Tab template"
    ROUND_SHEET = "OVERVIEW Template"
    INITIAL_OFFSET = 7
    sheets_service = None
    spreadsheet_id = None

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
        print(self.sheets_service.get(spreadsheetId=self.get_spreadsheet_id()).execute())

    def set_puzzle_data(self, puzzle_data):
        self._puzzle_data = puzzle_data

    def get_puzzle_data(self):
        return self._puzzle_data

    def set_spreadsheet_id(self, spreadsheet_id):
        self.spreadsheet_id = spreadsheet_id

    def get_spreadsheet_id(self):
        return self.spreadsheet_id

    def get_page_id(self):
        return self.get_puzzle_data().google_page_id

    def batch_update(self, body, wrap = True):
        return self.sheets_service.batchUpdate(spreadsheetId=self.get_spreadsheet_id(), body=body).execute()

    def sheet_list(self):
        return self.sheets_service.get(spreadsheetId=self.get_spreadsheet_id()).execute()

    def get_page_id_by_name(self, name):
        for sheet in self.sheet_list()["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['sheetId']

    def get_page_name_by_id(self, id):
        for sheet in self.sheet_list()["sheets"]:
            if (sheet['properties']['sheetId']) == int(id):
                return sheet['properties']['title']

    def get_overview(self):
        return self.sheets_service.values().get(spreadsheetId=self.get_spreadsheet_id(),
                                        range="OVERVIEW!B9:B"
                                        ).execute()

    def get_overview_urls(self):
        return self.sheets_service.values().get(spreadsheetId=self.get_spreadsheet_id(),
                                        range="OVERVIEW!C9:C",
                                        valueRenderOption='FORMULA'
                                        ).execute()

    def get_row(self, ref):
        return int(ref[1]) - 1

    def get_column(self, ref):
        return ord(ref[0].lower()) - 97

    def get_puzzle_overview_row(self):
        body = {
            'dataFilters': [
                {
                    "gridRange":
                        {
                            'sheetId': self.get_overview_page_id(),
                            'startRowIndex': 4,
                            'endRowIndex': 150,
                            'startColumnIndex': 2,
                            'endColumnIndex': 3
                        }
                }

            ],
            "includeGridData": True
        }
        response = self.sheets_service.getByDataFilter(
            spreadsheetId=self.get_spreadsheet_id(),
            body=body
        ).execute()
        row_number = 4
        for row in response['sheets'][0]['data'][0]['rowData']:
            try:
                if str(row['values'][0]['userEnteredValue']['formulaValue']).find(
                        str(self.get_puzzle_data().google_page_id)) > 0:
                    return row_number
                else:
                    row_number = row_number + 1
            except KeyError:
                pass

    def update_puzzle_info(self, overview_row = 0):
        puzzle_name = self.get_puzzle_data().name
        new_sheet_id = self.get_page_id_by_name(puzzle_name)
        # overview_page_name = self.get_page_name_by_id(self.overview_page_id)
        # requests = [self.update_cell(puzzle_name, overview_row, 1, self.STRING_INPUT,
        #                              self.overview_page_id),
        #             self.update_cell('=hyperlink("#gid=' + str(new_sheet_id) + '";"LINK")', overview_row, 2,
        #                               self.FORMULA_INPUT,
        #                               self.overview_page_id),
        #             self.update_cell("='" + escape_apostrophes(puzzle_name) + "'!B5", overview_row, 3,
        #                              self.FORMULA_INPUT,
        #                              self.overview_page_id),
        #             self.update_cell("='" + escape_apostrophes(puzzle_name) + "'!B4", overview_row, 4,
        #                              self.FORMULA_INPUT,
        #                              self.overview_page_id),
        #             self.update_cell("='" + escape_apostrophes(str(overview_page_name)) + "'!B" + str(overview_row + 1), 0, 1,
        #                              self.FORMULA_INPUT,
        #                              new_sheet_id),
        #             self.update_cell(self.get_puzzle_data().url, 1, 1, self.STRING_INPUT,
        #                              new_sheet_id)]
        requests = [
            self.update_cell(puzzle_name, self.get_row(config.puzzle_cell_name), self.get_column(config.puzzle_cell_name), self.STRING_INPUT,
                new_sheet_id),
            self.update_cell(self.get_puzzle_data().url, self.get_row(config.puzzle_cell_link), self.get_column(config.puzzle_cell_link), self.STRING_INPUT,
                new_sheet_id),
            self.set_sheet_name(puzzle_name)
        ]
        updates = {
            'requests': requests
        }
        self.batch_update(updates)

    def enter_solution(self):
        body={
            'requests': [
                {
                    'updateCells': {
                        'rows': [
                            {
                                'values':
                                    {
                                        'userEnteredValue': {
                                            'stringValue': self.get_puzzle_data().solution
                                        }
                                    }
                            }
                        ],
                        'fields': 'userEnteredValue',
                        'range': {
                            'sheetId': self.get_puzzle_data().google_page_id,
                            'startRowIndex': self.get_row(config.puzzle_cell_solution),
                            'endRowIndex': self.get_column(config.puzzle_cell_solution),
                            'startColumnIndex': 1,
                            'endColumnIndex': 2
                        }
                    }
                }
            ]
        }
        self.batch_update(body)

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

    def move_sheet_to_end(self):
        sheets = self.sheet_list().get('sheets',[])
        new_index = len(sheets) - 1
        body = {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': self.get_puzzle_data().google_page_id,
                            'index': new_index
                        },
                        'fields': 'index'

                    }
                }
        return body

    def change_tab_colour(self, colour):
        body = {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': self.get_puzzle_data().google_page_id,
                            'tabColor': {colour: 1}
                        },
                        'fields': 'tabColor'

                    }
                }
        return body

    def add_new_sheet(self, name, index=INITIAL_OFFSET, sheet_type=PUZZLE_SHEET):
        sheet_id = self.get_page_id_by_name(sheet_type)
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
        new_sheet = self.batch_update(body)
        new_sheet_id = new_sheet['replies'][1]['duplicateSheet']['properties']['sheetId']
        self.get_puzzle_data().google_page_id = new_sheet_id
        # self.set_sheet_hidden(False)
        return new_sheet_id

    def add_new_puzzle_sheet(self, index):
        self.add_new_sheet(self.get_puzzle_data().name, index)

    def add_new_overview_sheet(self, index):
        overview_name = "OVERVIEW - " + self.get_puzzle_data().name
        new_sheet_id = self.add_new_sheet(overview_name, index, self.ROUND_SHEET)
        return new_sheet_id
    def get_overview_page_id(self):
        return self.overview_page_id

    def set_overview_page_id(self, overview_page_id):
        self.overview_page_id = overview_page_id

    def copy_puzzle_info(self, start):
        body = {
            'requests': [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": self.get_page_id_by_name(self.ROUND_SHEET),
                            "startRowIndex": 4,
                            "endRowIndex": 5,
                            "startColumnIndex": 1,
                            "endColumnIndex": 7
                        },
                        "destination": {
                            "sheetId": self.overview_page_id,
                            "startRowIndex": start,
                            "endRowIndex": start + 1,
                            "startColumnIndex": 1,
                            "endColumnIndex": 7
                        },
                        "pasteType": "PASTE_NORMAL",
                        "pasteOrientation": "NORMAL"
                    }
                }

            ]
        }
        self.batch_update(body)

    def set_sheet_hidden(self, hidden=True):
        sheet_id = self.get_page_id()
        body = {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'hidden': hidden
                        },
                        'fields': 'hidden'

                    }
                }
        return body

    def set_sheet_name(self, name):
        sheet_id = self.get_page_id()
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

    def remove_puzzle_from_overview(self):
        if self.get_puzzle_overview_row():
            row_number = int(self.get_puzzle_overview_row())
            if row_number:
                body = {
                            'deleteRange': {
                                'range': {
                                    'sheetId': self.get_overview_page_id(),
                                    "startRowIndex": row_number,
                                    "endRowIndex": row_number + 1,
                                    "startColumnIndex": 1,
                                    "endColumnIndex": 7
                                },
                                'shiftDimension': 'ROWS'
                            }
                        }
                return body
            else:
                return False

    def delete_sheet(self, page_id = None):
        if page_id is None:
            page_id = self.get_page_id()
        body = {
            'requests': [
                {
                    'deleteSheet': {
                        'sheetId': page_id,
                    }
                }
            ]
        }
        self.batch_update(body)

    async def update_puzzle(self, puzzle_data):
        self.set_puzzle_data(puzzle_data)
        self.update_puzzle_info()

    async def delete_round_spreadsheet(self, round_data: RoundData ):
        self.delete_sheet(round_data.google_page_id)

    async def mark_deleted_puzzle_spreadsheet(self, puzzle_data: PuzzleData, hunt_round: RoundData):
        self.set_puzzle_data(puzzle_data)
        self.set_overview_page_id(hunt_round.google_page_id)
        new_sheet_name = "DELETED - " + puzzle_data.name
        requests = [self.set_sheet_hidden(),
                    self.set_sheet_name(new_sheet_name),
                    self.move_sheet_to_end(),
                    self.remove_puzzle_from_overview()]
        updates = {
            'requests': requests
        }
        self.batch_update(updates)

    async def delete_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        self.set_puzzle_data(puzzle_data)
        # self.set_overview_page_id(hunt_round.google_page_id)
        # requests = [self.remove_puzzle_from_overview()]
        # if requests[0]:
        #     updates = {
        #         'requests': requests
        #     }
        #     self.batch_update(updates)
        self.delete_sheet(puzzle_data.google_page_id)

    async def archive_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        self.set_puzzle_data(puzzle_data)
        requests = [self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution), self.get_column(config.puzzle_cell_solution)),
            self.update_cell("Solved", self.get_row(config.puzzle_cell_progress), self.get_column(config.puzzle_cell_progress)),
            self.move_sheet_to_end(),
            self.change_tab_colour('green')]
        updates = {
            'requests': requests
        }
        self.batch_update(updates)

    async def create_hunt_spreadsheet(self, hunt_name):
        permission = {
            'type': 'anyone',
            'role': 'writer'
        }
        body = {
            'name': hunt_name
        }
        new_file = get_drive().files().copy(fileId=config.master_spreadsheet).execute()
        new_file_id = new_file['id']
        get_drive().files().update(fileId=new_file_id, body=body).execute()
        get_drive().permissions().create(fileId=new_file_id, body=permission).execute()
        return new_file_id

    def create_round_overview_spreadsheet(self, round_data: RoundData, hunt: HuntData):
        """Creates new round overview spreadsheet"""
        self.set_puzzle_data(round_data)
        overview_index = hunt.num_rounds + self.INITIAL_OFFSET
        new_page_id = self.add_new_overview_sheet(overview_index)
        requests = [self.update_cell(round_data.name, 3, 1, self.STRING_INPUT, new_page_id),
                    self.update_cell(hunt.url, 0, 1, self.STRING_INPUT, new_page_id)]
        updates = {
            'requests': requests
        }
        self.batch_update(updates)

    def create_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        """Creates new puzzle spreadsheet and adds puzzle data to the overview spreadsheet."""
        self.set_puzzle_data(puzzle_data)
        # self.overview_page_id = hunt_round.google_page_id
        # puzzle_index = hunt.num_rounds + self.INITIAL_OFFSET
        puzzle_index = self.INITIAL_OFFSET
        self.add_new_puzzle_sheet(puzzle_index)
        # self.copy_puzzle_info(hunt_round.num_puzzles + 3)
        self.update_puzzle_info()
        # self.update_puzzle_info(hunt_round.num_puzzles + 3)

    def add_metapuzzle_data(self, puzzle: PuzzleData, round_puzzles: List[PuzzleData]):
        self.set_puzzle_data(puzzle)
        # self.overview_page_id = hunt_round.google_page_id
        # overview_name = self.get_page_name_by_id(self.overview_page_id)
        requests = [self.update_cell("Puzzle titles",5,0,self.STRING_INPUT,self.get_page_id()),
                    self.update_cell("Puzzle solutions", 5, 1, self.STRING_INPUT,self.get_page_id()),]
        row = 6
        for round_puzzle in round_puzzles:
            if round_puzzle.id == puzzle.id: # Skip self
                continue
            requests.append(self.update_cell(round_puzzle.name, row, 0, self.STRING_INPUT, self.get_page_id()))
            if round_puzzle.solution:
                solution = round_puzzle.solution
            else:
                solution = "Unsolved"
            requests.append(self.update_cell(solution, row, 1, self.STRING_INPUT, self.get_page_id()))
            row += 1
        for x in range(50):
            requests.append(self.update_cell("", row, 0, self.STRING_INPUT, self.get_page_id()))
            requests.append(self.update_cell("", row, 1, self.STRING_INPUT, self.get_page_id()))
            row += 1
        updates = {
            'requests': requests
        }
        self.batch_update(updates)

    def retrieve_overview_page_id(self, puzzle_data):
        self.set_puzzle_data(puzzle_data)
        return self.get_overview_page_id()

async def setup(bot):
    # Comment this out if google-drive-related package are not installed!
    await bot.add_cog(GoogleSheets(bot))