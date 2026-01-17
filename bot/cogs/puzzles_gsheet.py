""" Google Drive integration for puzzle organization

This is a separate cog so that Google Drive integration
can be easily disabled; simply omit this file.
"""
import asyncio
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
    METAPUZZLE_SHEET = "Meta Tab template"
    ROUND_SHEET = "OVERVIEW Template"
    INITIAL_OFFSET = 7
    sheets_service = None
    spreadsheet_id = None
    archive_spreadsheet_id = None

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
        self.spreadsheet_id = spreadsheet_id

    def set_archive_spreadsheet_id(self, archive_spreadsheet_id):
        self.archive_spreadsheet_id = archive_spreadsheet_id

    def get_spreadsheet_id(self):
        return self.spreadsheet_id

    def get_archive_spreadsheet_id(self):
        return self.archive_spreadsheet_id

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

    def get_overview(self):
        return get_sheet().values().get(spreadsheetId=self.get_spreadsheet_id(),
                                        range="OVERVIEW!B9:B"
                                        ).execute()

    def get_overview_urls(self):
        return get_sheet().values().get(spreadsheetId=self.get_spreadsheet_id(),
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

    async def update_puzzle_info(self, puzzle: PuzzleData, update_tab_name = False, name = None, page_id = None):

        puzzle_name = name or puzzle.name

        new_sheet_id = page_id or puzzle.google_page_id
        # requests = [
        #     self.update_cell(puzzle_name, self.get_row(config.puzzle_cell_name), self.get_column(config.puzzle_cell_name), self.STRING_INPUT,
        #         new_sheet_id),
        #     self.update_cell(self.get_puzzle_data().url, self.get_row(config.puzzle_cell_link), self.get_column(config.puzzle_cell_link), self.STRING_INPUT,
        #         new_sheet_id),
        # ]
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

    async def enter_solution(self):
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
        await self.batch_update(body)

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
        # self.get_puzzle_data().google_page_id = new_sheet_id
        # self.set_sheet_hidden(False)
        return new_sheet_id

    async def add_new_puzzle_sheet(self, puzzle: PuzzleData, index = INITIAL_OFFSET):
        if puzzle.metapuzzle == 1:
            puzzle.google_page_id = await self.add_new_sheet(self.get_unique_tab_name(puzzle.name), index, self.METAPUZZLE_SHEET)
        else:
            puzzle.google_page_id = await self.add_new_sheet(self.get_unique_tab_name(puzzle.name), index)

    # async def add_new_overview_sheet(self, index):
    #     overview_name = "OVERVIEW - " + self.get_puzzle_data().name
    #     new_sheet_id = await self.add_new_sheet(self.get_unique_tab_name(overview_name), index, self.ROUND_SHEET)
    #     return new_sheet_id

    # def get_overview_page_id(self):
    #     return self.overview_page_id

    # def set_overview_page_id(self, overview_page_id):
    #     self.overview_page_id = overview_page_id

    # async def copy_puzzle_info(self, start):
    #     body = {
    #         'requests': [
    #             {
    #                 "copyPaste": {
    #                     "source": {
    #                         "sheetId": self.get_page_id_by_name(self.ROUND_SHEET),
    #                         "startRowIndex": 4,
    #                         "endRowIndex": 5,
    #                         "startColumnIndex": 1,
    #                         "endColumnIndex": 7
    #                     },
    #                     "destination": {
    #                         "sheetId": self.overview_page_id,
    #                         "startRowIndex": start,
    #                         "endRowIndex": start + 1,
    #                         "startColumnIndex": 1,
    #                         "endColumnIndex": 7
    #                     },
    #                     "pasteType": "PASTE_NORMAL",
    #                     "pasteOrientation": "NORMAL"
    #                 }
    #             }
    #
    #         ]
    #     }
    #     await self.batch_update(body)

    def set_sheet_hidden(self, sheet_id, hidden=True):
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

    # def remove_puzzle_from_overview(self):
    #     if self.get_puzzle_overview_row():
    #         row_number = int(self.get_puzzle_overview_row())
    #         if row_number:
    #             body = {
    #                         'deleteRange': {
    #                             'range': {
    #                                 'sheetId': self.get_overview_page_id(),
    #                                 "startRowIndex": row_number,
    #                                 "endRowIndex": row_number + 1,
    #                                 "startColumnIndex": 1,
    #                                 "endColumnIndex": 7
    #                             },
    #                             'shiftDimension': 'ROWS'
    #                         }
    #                     }
    #             return body
    #         else:
    #             return False

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

    async def delete_round_spreadsheet(self, round_data: RoundData ):
        await self.delete_sheet(round_data.google_page_id)

    # async def mark_deleted_puzzle_spreadsheet(self, puzzle_data: PuzzleData, hunt_round: RoundData):
    #     self.set_puzzle_data(puzzle_data)
    #     self.set_overview_page_id(hunt_round.google_page_id)
    #     new_sheet_name = "DELETED - " + puzzle_data.name
    #     requests = [self.set_sheet_hidden(puzzle_data.google_page_id),
    #                 self.set_sheet_name(new_sheet_name,puzzle_data.google_page_id),
    #                 self.move_sheet_to_end(puzzle_data.google_page_id),
    #                 self.remove_puzzle_from_overview()]
    #     updates = {
    #         'requests': requests
    #     }
    #     await self.batch_update(updates, puzzle_data.archived)

    async def delete_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        # self.set_overview_page_id(hunt_round.google_page_id)
        # requests = [self.remove_puzzle_from_overview()]
        # if requests[0]:
        #     updates = {
        #         'requests': requests
        #     }
        #     self.batch_update(updates)
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
        # updates = {
        #     'requests': requests
        # }
        # await self.batch_update(updates)

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
        # sheet_requests = []
        # for sheet in puzzle_data.additional_sheets:
        #     if len(sheet.google_page_id) > 0:
        #         sheet_requests.append(self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution),
        #                                      self.get_column(config.puzzle_cell_solution), self.STRING_INPUT,
        #                                      sheet.google_page_id))
        # updates = {
        #     'requests': sheet_requests
        # }
        # await self.batch_update(updates, puzzle_data.solved)

    async def archive_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        requests = []
        requests.extend([self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution), self.get_column(config.puzzle_cell_solution), self.STRING_INPUT, puzzle_data.google_page_id),
            self.update_cell("Solved", self.get_row(config.puzzle_cell_progress), self.get_column(config.puzzle_cell_progress), self.STRING_INPUT, puzzle_data.google_page_id),
            self.move_sheet_to_end(puzzle_data.google_page_id),
            self.change_tab_colour('green', puzzle_data.google_page_id)])
        # updates = {
        #     'requests': requests
        # }
        # await self.batch_update(updates)
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
        # get_drive().files().update(fileId=new_file_id, body=body).execute()
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

    # async def create_round_overview_spreadsheet(self, round_data: RoundData, hunt: HuntData):
    #     """Creates new round overview spreadsheet"""
    #     self.set_puzzle_data(round_data)
    #     overview_index = hunt.num_rounds + self.INITIAL_OFFSET
    #     new_page_id = self.add_new_overview_sheet(overview_index)
    #     requests = [self.update_cell(round_data.name, 3, 1, self.STRING_INPUT, new_page_id),
    #                 self.update_cell(hunt.url, 0, 1, self.STRING_INPUT, new_page_id)]
    #     updates = {
    #         'requests': requests
    #     }
    #     await self.batch_update(updates)

    async def create_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        """Creates new puzzle spreadsheet and adds puzzle data to the overview spreadsheet."""
        # self.set_puzzle_data(puzzle_data)
        # self.overview_page_id = hunt_round.google_page_id
        # puzzle_index = hunt.num_rounds + self.INITIAL_OFFSET
        puzzle_index = self.INITIAL_OFFSET
        await self.add_new_puzzle_sheet(puzzle_data, puzzle_index)
        # self.copy_puzzle_info(hunt_round.num_puzzles + 3)
        await self.update_puzzle_info(puzzle_data)
        # self.update_puzzle_info(hunt_round.num_puzzles + 3)

    async def create_additional_spreadsheet(self, puzzle_data: PuzzleData, name = None, puzzle = False):
        index = self.get_puzzle_sheet_index(puzzle_data)
        if name:
            sheet_name = f"{name} ({puzzle_data.name})"
        else:
            sheet_name = f"Extra Sheet for {puzzle_data.name}"
        sheet_id = await self.add_new_sheet(sheet_name, index+1)
        await self.update_puzzle_info( puzzle_data, False,name,sheet_id)
        return sheet_id

    async def add_metapuzzle_data(self, puzzle: PuzzleData, round_puzzles: List[PuzzleData]):
        # self.overview_page_id = hunt_round.google_page_id
        # overview_name = self.get_page_name_by_id(self.overview_page_id)
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
        # self.overview_page_id = hunt_round.google_page_id
        # overview_name = self.get_page_name_by_id(self.overview_page_id)
        requests = [self.update_cell("Puzzle Round",4,0,self.STRING_INPUT,puzzle.google_page_id),
                    self.update_cell("Puzzle Title", 4, 1, self.STRING_INPUT,puzzle.google_page_id),
                    self.update_cell("Puzzle Solution", 4, 2, self.STRING_INPUT,puzzle.google_page_id),]
        row = 5
        for round_puzzle in round_puzzles:
            if round_puzzle.id == puzzle.id: # Skip self
                continue
            if round_puzzle.tags[0] if round_puzzle.tags else None is not None:
                round_name = RoundJsonDb.get_by_attr(id=round_puzzle.tags[0]).name
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