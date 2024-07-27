""" Google Drive integration for puzzle organization

This is a separate cog so that Google Drive integration
can be easily disabled; simply omit this file.
"""

import datetime
import logging
import string
import traceback
from typing import Optional

import discord
from discord.ext import commands, tasks
import gspread_asyncio
import gspread_formatting
import pytz

from bot.utils import urls
from bot.store import MissingPuzzleError, PuzzleData, PuzzleJsonDb, GuildSettings, GuildSettingsDb, HuntSettings
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
    MASTER_SPREADSHEET_ID = "1CkLpcL8jUVBjSrs8tcfWFp2uGvnVTUJhl0FgXHsXH7M"

    def __init__(self, bot):
        self.bot = bot
        self._puzzle_data = None

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")

    @commands.command()
    async def read(self, ctx):
        print(get_sheet().get(spreadsheetId=self.get_spreadsheet_id()).execute())

    def set_puzzle_data(self, puzzle_data):
        self._puzzle_data = puzzle_data

    def get_puzzle_data(self):
        return self._puzzle_data

    def get_spreadsheet_id(self):
        return self.get_puzzle_data().google_sheet_id

    def get_page_id(self):
        return self.get_puzzle_data().google_page_id

    def batch_update(self, body):
        get_sheet().batchUpdate(spreadsheetId=self.get_spreadsheet_id(), body=body).execute()

    def sheet_list(self):
        return get_sheet().get(spreadsheetId=self.get_spreadsheet_id()).execute()

    def get_page_id_by_name(self, name):
        for sheet in self.sheet_list()["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['sheetId']

    def get_page_name_by_id(self, id):
        for sheet in self.sheet_list()["sheets"]:
            if (sheet['properties']['sheetId']) == int(id):
                return sheet['properties']['title']

    def get_overview(self):
        return get_sheet().values().get(spreadsheetId=self.get_spreadsheet_id(),
                                        range="OVERVIEW!B9:B"
                                        ).execute()

    def get_overview_urls(self):
        return get_sheet().values().get(spreadsheetId=self.get_spreadsheet_id(),
                                        range="OVERVIEW!C9:C",
                                        valueRenderOption='FORMULA'
                                        ).execute()

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
        response = get_sheet().getByDataFilter(
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

    def set_metadata(self, key, value):
        body = {
            'requests': [
                {
                    "createDeveloperMetadata": {
                        "developerMetadata": {
                            "metadataKey": str(key),
                            "metadataValue": str(value),
                            "location": {
                                "spreadsheet": True
                            },
                            "visibility": "PROJECT"
                        }
                    }
                }

            ]
        }
        self.batch_update(body)

    def update_metadata(self, key, value):
        body = {
            'requests': [
                {
                    "updateDeveloperMetadata": {
                        "dataFilters": [
                            {
                                "developerMetadataLookup": {
                                    "metadataKey": str(key)
                                }
                            }
                        ],
                        "developerMetadata": {
                            "metadataValue": str(value),
                        },
                        "fields": "metadataValue"
                    }
                }

            ]
        }
        self.batch_update(body)

    def delete_metadata(self, key):
        body = {
            'requests': [
                {
                    "deleteDeveloperMetadata": {
                        "dataFilter":
                            {
                                "developerMetadataLookup": {
                                    "metadataKey": str(key)
                                }
                            }
                    }
                }

            ]
        }
        self.batch_update(body)

    def get_metadata(self, key):
        body = {
            "dataFilters": [
                {
                    "developerMetadataLookup": {
                        "metadataKey": str(key)
                    }
                }
            ]
        }
        response = get_sheet().developerMetadata().search(
            spreadsheetId=self.get_spreadsheet_id(), body=body).execute()
        return response['matchedDeveloperMetadata'][0]['developerMetadata']['metadataValue']

    def update_puzzle_info(self, overview_row):
        puzzle_name = self.get_puzzle_data().name
        new_sheet_id = self.get_page_id_by_name(puzzle_name)
        overview_page_id = self.get_overview_page_id()
        overview_page_name = self.get_page_name_by_id(overview_page_id)
        requests = [self.update_cell(puzzle_name, overview_row, 1, self.STRING_INPUT,
                                     overview_page_id),
                    self.update_cell('=hyperlink("#gid=' + str(new_sheet_id) + '";"LINK")', overview_row, 2,
                                     self.FORMULA_INPUT,
                                     overview_page_id),
                    self.update_cell("='" + escape_apostrophes(puzzle_name) + "'!B5", overview_row, 3,
                                     self.FORMULA_INPUT,
                                     overview_page_id),
                    self.update_cell("='" + escape_apostrophes(puzzle_name) + "'!B4", overview_row, 4,
                                     self.FORMULA_INPUT,
                                     overview_page_id),
                    self.update_cell("='" + str(overview_page_name) + "'!B" + str(overview_row + 1), 0, 1,
                                     self.FORMULA_INPUT,
                                     new_sheet_id),
                    self.update_cell(self.get_puzzle_data().hunt_url, 1, 1, self.STRING_INPUT,
                                     new_sheet_id)]
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
                            'startRowIndex': 4,
                            'endRowIndex': 5,
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
        # body = {
        #     'requests': [
        #         {
        #             'updateCells': {
        #                 'rows': [
        #                     {
        #                         'values':
        #                             {
        #                                 'userEnteredValue': {
        #                                     type: value
        #                                 }
        #                             }
        #                     }
        #                 ],
        #                 'fields': 'userEnteredValue',
        #                 'range': {
        #                     'sheetId': sheet_id,
        #                     'startRowIndex': start_row,
        #                     'endRowIndex': start_row+1,
        #                     'startColumnIndex': start_column,
        #                     'endColumnIndex': start_column+1
        #                 }
        #             }
        #         }
        #     ]
        # }
        # self.batch_update(body)
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
        new_index = 0
        for sheet in self.sheet_list()["sheets"]:
            if sheet['properties']['index'] > new_index:
                new_index = sheet['properties']['index'] + 1
        body = {
            'requests': [
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': self.get_puzzle_data().google_page_id,
                            'index': new_index
                        },
                        'fields': 'index'

                    }
                }
            ]
        }
        self.batch_update(body)

    def change_tab_colour(self, colour):
        body = {
            'requests': [
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': self.get_puzzle_data().google_page_id,
                            'tabColor': {colour: 1}
                        },
                        'fields': 'tabColor'

                    }
                }
            ]
        }
        self.batch_update(body)

    def add_new_sheet(self, name, index=7, type=PUZZLE_SHEET):
        body = {
            'requests': [
                {
                    "duplicateSheet": {
                        "sourceSheetId": self.get_page_id_by_name(type),
                        "insertSheetIndex": index,
                        "newSheetName": name
                    }
                }

            ]
        }
        self.batch_update(body)
        self.get_puzzle_data().google_page_id = self.get_page_id_by_name(name)
        self.set_sheet_hidden(False)

    def add_new_puzzle_sheet(self, index):
        self.add_new_sheet(self.get_puzzle_data().name, index)

    def add_new_overview_sheet(self, index):
        overview_name = "OVERVIEW - " + self.get_puzzle_data().name
        self.add_new_sheet(overview_name, index, self.ROUND_SHEET)
        self.set_metadata(self.get_puzzle_data().round_id, self.get_page_id_by_name(overview_name))

    def get_overview_page_id(self):
        return self.get_metadata(self.get_puzzle_data().round_id)

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
                            "sheetId": self.get_overview_page_id(),
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
            'requests': [
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'hidden': hidden
                        },
                        'fields': 'hidden'

                    }
                }
            ]
        }
        self.batch_update(body)

    def set_sheet_name(self, name):
        sheet_id = self.get_page_id()
        body = {
            'requests': [
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'title': name
                        },
                        'fields': 'title'

                    }
                }
            ]
        }
        self.batch_update(body)

    def remove_puzzle_from_overview(self):
        row_number = int(self.get_puzzle_overview_row())
        body = {
            'requests': [
                {
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
            ]
        }
        self.batch_update(body)

    def delete_sheet(self):
        body = {
            'requests': [
                {
                    'deleteSheet': {
                        'sheetId': self.get_page_id(),
                    }
                }
            ]
        }
        self.batch_update(body)

    async def update_url(self, puzzle_data):
        self.set_puzzle_data(puzzle_data)
        self.update_cell(puzzle_data.hunt_url, 1, 1)

    async def archive_round_spreadsheet(self, round_data):
        self.set_puzzle_data(round_data)
        round_data.google_page_id = self.get_overview_page_id()
        self.delete_sheet()
        round_counter = int(self.get_metadata('num_rounds'))
        round_counter -= 1
        self.update_metadata('num_rounds', round_counter)
        # Delete round puzzle counter
        self.delete_metadata(round_data.round_name + "_puzzles")
        # Delete sheet reference
        self.delete_metadata(round_data.round_id)

    async def delete_puzzle_spreadsheet(self, puzzle_data):
        self.set_puzzle_data(puzzle_data)
        self.set_sheet_hidden()
        new_sheet_name = "DELETED - " + puzzle_data.name
        self.set_sheet_name(new_sheet_name)
        self.move_sheet_to_end()
        self.remove_puzzle_from_overview()
        puzzle_counter = puzzle_data.round_name + "_puzzles"
        num_puzzles = int(self.get_metadata(puzzle_counter)) - 1
        self.update_metadata(puzzle_counter, num_puzzles)

    async def archive_puzzle_spreadsheet(self, puzzle_data):
        self.set_puzzle_data(puzzle_data)
        self.update_cell(puzzle_data.solution, 4, 1)
        self.update_cell("Solved", 3, 1)
        self.move_sheet_to_end()
        self.change_tab_colour('green')

    async def create_hunt_spreadsheet(self, hunt_name):
        permission = {
            'type': 'anyone',
            'role': 'writer'
        }
        body = {
            'name': hunt_name
        }
        new_file = get_drive().files().copy(fileId=self.MASTER_SPREADSHEET_ID).execute()
        new_file_id = new_file['id']
        get_drive().files().update(fileId=new_file_id, body=body).execute()
        get_drive().permissions().create(fileId=new_file_id, body=permission).execute()
        return new_file_id

    async def create_round_overview_spreadsheet(self, round_data):
        self.set_puzzle_data(round_data)
        try:
            round_counter = int(self.get_metadata('num_rounds'))
        except:
            round_counter = 0
            self.set_metadata('num_rounds', 0)
        overview_index = round_counter + 7
        self.add_new_overview_sheet(overview_index)
        self.update_cell(round_data.name, 3, 1, self.STRING_INPUT, self.get_overview_page_id())
        self.update_cell(round_data.hunt_url, 0, 1, self.STRING_INPUT, self.get_overview_page_id())
        puzzle_counter = round_data.round_name + "_puzzles"
        self.set_metadata(puzzle_counter, 0)
        round_counter += 1
        self.update_metadata('num_rounds', round_counter)

    async def create_puzzle_spreadsheet(self, puzzle_data):
        self.set_puzzle_data(puzzle_data)
        round_counter = int(self.get_metadata('num_rounds'))
        puzzle_index = round_counter + 7
        self.add_new_puzzle_sheet(puzzle_index)
        puzzle_counter = puzzle_data.round_name + "_puzzles"
        num_puzzles = int(self.get_metadata(puzzle_counter)) + 1
        self.update_metadata(puzzle_counter, num_puzzles)
        # if get_sheet().values().get(spreadsheetId=self.get_spreadsheet_id(), range="OVERVIEW!B9").execute()['values'][0][0] != 'First Puzzle':
        #     self.copy_puzzle_info(num_puzzles + 7)
        #     self.update_puzzle_info(num_puzzles + 9)
        # else:
        #     self.update_puzzle_info(num_puzzles + 8)
        self.copy_puzzle_info(num_puzzles + 3)
        self.update_puzzle_info(num_puzzles + 3)

    def retrieve_overview_page_id(self, puzzle_data):
        self.set_puzzle_data(puzzle_data)
        return self.get_overview_page_id()

async def setup(bot):
    # Comment this out if google-drive-related package are not installed!
    await bot.add_cog(GoogleSheets(bot))