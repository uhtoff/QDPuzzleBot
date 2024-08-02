import os.path
import json

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# The ID and range of a sample spreadsheet.
SPREADSHEET_ID = "1f-W4VglELO-7yQoozPStdp8Jp9aLgHoVvR1rhpaFJ-o"


def get_sheet():
    creds = Credentials.from_service_account_file("google_secrets.json")
    scoped = creds.with_scopes(
        [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
    )

    try:
        service = build("sheets", "v4", credentials=creds)

        # Call the Sheets API
        sheet = service.spreadsheets()
        return sheet

    except HttpError as err:
        print(err)

def get_drive():
    creds = Credentials.from_service_account_file("google_secrets.json")
    scoped = creds.with_scopes(
        [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
    )

    try:
        service = build("drive", "v3", credentials=creds)

        # Call the Drive API
        # drive = service.files()

        return service

    except HttpError as err:
        print(err)

def batch_update(body):
    get_sheet().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()


def sheet_list():
    return get_sheet().get(spreadsheetId=SPREADSHEET_ID).execute()


def format_body(sheet_id, field, value):
    body = {
        'requests': [
            {
                'updateSheetProperties': {
                    'properties': {
                        'sheetId': sheet_id,
                        field: value
                    },
                    'fields': field

                }
            }
        ]
    }
    return body


def hide_sheet(sheet_id):
    body = {
        'requests': [
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
    batch_update(body)


def show_sheet(sheet_id):
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
            }
        ]
    }
    batch_update(body)


def change_tab_colour(sheet_id, colour):
    body = {
        'requests': [
            {
                'updateSheetProperties': {
                    'properties': {
                        'sheetId': sheet_id,
                        'tabColor': {colour: 1}
                    },
                    'fields': 'tabColor'

                }
            }
        ]
    }
    batch_update(body)


def get_sheet_id_by_name(name):
    for sheet in sheet_list()["sheets"]:
        if(sheet['properties']['title']) == name:
            return sheet['properties']['sheetId']


def print_sheets():
    for sheet in sheet_list()["sheets"]:
        print(sheet['properties'])


def move_sheet_to_end(sheet_id):
    new_index = 0
    for sheet in sheet_list()["sheets"]:
        if(sheet['properties']['index']>new_index):
            new_index = sheet['properties']['index'] + 1
    batch_update(format_body(sheet_id, 'index', new_index))


def move_sheet_to_start(sheet_id):
    new_index = 2
    batch_update(format_body(sheet_id, 'index', new_index))


def get_overview():
    return get_sheet().values().get(spreadsheetId=SPREADSHEET_ID, range="OVERVIEW!B9:B").execute()


def copy_puzzle_info(start):
    body = {
        'requests': [
            {
                "copyPaste": {
                    "source": {
                        "sheetId": 0,
                        "startRowIndex": start,
                        "endRowIndex": start+1,
                        "startColumnIndex": 1,
                        "endColumnIndex": 7
                    },
                    "destination": {
                        "sheetId": 0,
                        "startRowIndex": start+1,
                        "endRowIndex": start+2,
                        "startColumnIndex": 1,
                        "endColumnIndex": 7
                    },
                    "pasteType": "PASTE_NORMAL",
                    "pasteOrientation": "NORMAL"
                }
            }

        ]
    }
    get_sheet().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()


def add_new_sheet(puzzle_name):
    body = {
        'requests': [
            {
                "duplicateSheet": {
                    "sourceSheetId": get_sheet_id_by_name("Tab template"),
                    "insertSheetIndex": 2,
                    "newSheetName": puzzle_name
                }
            }

        ]
    }
    get_sheet().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
    show_sheet(get_sheet_id_by_name(puzzle_name))


def delete_sheet(sheet_id):
    body = {
        'requests': [
            {
                "deleteSheet": {
                    "sheetId": sheet_id
                }
            }

        ]
    }
    get_sheet().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()


def update_puzzle_info(puzzle_name, overview_row, puzzle_url=None):
    new_sheet_id = get_sheet_id_by_name(puzzle_name)
    values = [
        [
            puzzle_name,
            '=hyperlink("#gid=' + str(new_sheet_id) + '";"LINK")',
            "='" + puzzle_name + "'!B5",
            "='" + puzzle_name + "'!B4",
        ]
    ]
    body = {
        "values": values
    }
    get_sheet().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range="OVERVIEW!B" + str(overview_row) + ":E" + str(overview_row),
        valueInputOption="USER_ENTERED",
        body=body).execute()
    values = [
        [

            "='OVERVIEW'!B" + str(overview_row),
            puzzle_url,
        ]
    ]
    body = {
        "values": values
    }
    get_sheet().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range="'" + puzzle_name + "'!B1:B2",
        valueInputOption="USER_ENTERED",
        body=body).execute()



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
        response = self.sheets_service.developerMetadata().search(
            spreadsheetId=self.get_spreadsheet_id(), body=body).execute()
        return response['matchedDeveloperMetadata'][0]['developerMetadata']['metadataValue']

def add_new_puzzle(puzzle_name, puzzle_url=None):
    add_new_sheet(puzzle_name)
    num_puzzles = len(get_overview()['values'])
    if get_sheet().values().get(spreadsheetId=SPREADSHEET_ID, range="OVERVIEW!B9").execute()['values'][0][0] != 'First Puzzle':
        copy_puzzle_info(num_puzzles + 7)
        update_puzzle_info(puzzle_name, num_puzzles + 9, puzzle_url)
    else:
        update_puzzle_info(puzzle_name, num_puzzles + 8, puzzle_url)


def solve_puzzle(puzzle_name):
    sheet_id = get_sheet_id_by_name(puzzle_name)
    move_sheet_to_end(sheet_id)
    change_tab_colour(sheet_id, 'green')
