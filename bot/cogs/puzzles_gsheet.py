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
from bot.store import PuzzleData, TagGroupJsonDb
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
        response = execute_sync(get_sheet().get(spreadsheetId=spreadsheet_id))
        if "sheets" not in response:
            # A valid spreadsheet always reports at least one sheet, so a response
            # without the key means the read returned bad/empty data (a transient
            # Google glitch), not an empty spreadsheet. Fail loudly here instead of
            # letting callers raise an opaque KeyError deep in a comprehension — or,
            # worse, treat it as "no tabs" and create a duplicate tab.
            raise RuntimeError(
                f"Sheets get for {spreadsheet_id} returned no 'sheets' field: {response!r}"
            )
        return response

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
        if new_puzzle.is_target:
            new_grid_columns = old_grid_columns + 2
        else:
            new_grid_columns = old_grid_columns
        new_grid_rows = old_grid_rows
        await self.update_grid_size(new_puzzle.google_page_id, new_grid_rows, new_grid_columns)
        old_start_row = new_start_row = self.FIRST_PUZZLE_ROW
        if new_puzzle.is_target:
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
        if puzzle.is_target:
            puzzle.google_page_id = await self.add_new_sheet(self.get_unique_tab_name(puzzle.name), index, self.METAPUZZLE_SHEET)
        else:
            puzzle.google_page_id = await self.add_new_sheet(self.get_unique_tab_name(puzzle.name), index)


    async def create_blank_tab(self, spreadsheet_id: str, name: str) -> int:
        """Add a plain blank tab to a spreadsheet. Returns the new sheet's page id."""
        body = {
            "requests": [{
                "addSheet": {
                    "properties": {"title": name}
                }
            }]
        }
        req = get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        result = await execute_with_retry(req, on_retry=self._notify_retry)
        return result["replies"][0]["addSheet"]["properties"]["sheetId"]

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

    async def delete_sheet_tab(self, sheet_id, spreadsheet_id=None):
        """Delete a tab by sheetId via batchUpdate. Tolerates an already-gone tab."""
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        body = {"requests": [{"deleteSheet": {"sheetId": int(sheet_id)}}]}
        req = get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        await execute_with_retry(req, on_retry=self._notify_retry)

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
        if puzzle_data.is_target is False and self.get_archive_spreadsheet_id():
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


    async def delete_hunt_spreadsheet(self, file_id: str):
        """Delete a whole Drive spreadsheet file by id. Tolerates already-gone files."""
        try:
            await execute_with_retry(
                get_drive().files().delete(fileId=file_id),
                on_retry=self._notify_retry,
            )
        except Exception:
            logger.exception("delete_hunt_spreadsheet: could not delete file %s (may already be gone)", file_id)

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

    # --- Generic roll-up engine (M2) -------------------------------------
    # One ctx-free bulk writer replacing the two hand-rolled per-cell renderers.
    # It writes a whole roll-up in two values() calls (clear + update) instead of
    # ~750 single-cell updateCells ops, and snapshots values RAW so an answer like
    # "=A1" is stored as text and never a live formula — archived puzzles move to a
    # separate spreadsheet where any cross-tab formula would resolve to #REF.

    @staticmethod
    def _col_letter(index: int) -> str:
        """0-based column index -> A1 column letters (0->A, 25->Z, 26->AA)."""
        letters = ""
        index += 1
        while index:
            index, rem = divmod(index - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    @classmethod
    def _a1_cell(cls, row: int, column: int) -> str:
        """0-based (row, column) -> A1 cell reference (0,0 -> A1)."""
        return f"{cls._col_letter(column)}{row + 1}"

    @staticmethod
    def _solution_text(puzzle: PuzzleData) -> str:
        """Answer-cell text for a roll-up row. A puzzle marked complete with no
        recorded answer carries the "✅" sentinel (see mark_as_complete); it is
        shown as-is so the row stays aligned instead of being silently dropped —
        the bug the old per-cell renderers had via a continue-before-row-increment."""
        return puzzle.solution if puzzle.solution else "Unsolved"

    def _target_spreadsheet_id(self, puzzle: PuzzleData):
        """Live spreadsheet, or the archive one once the puzzle has been archived."""
        return self.get_archive_spreadsheet_id() if puzzle.archived else self.get_spreadsheet_id()

    # --- Developer metadata: stable tab pointers (M5 substrate, M7 load-bearing) ---
    # A tab's numeric sheetId is reassigned when a sheet is moved to the archive
    # spreadsheet (see move_puzzle_spreadsheet), so a roll-up that keyed only on the
    # sheetId would dangle. Developer metadata is a searchable id that travels with
    # the tab. M5 records it as substrate but still renders via the meta puzzle's
    # google_page_id (the trusted pointer); M7 will resolve the target tab off it.

    async def ensure_tab_metadata(self, spreadsheet_id, page_id, key):
        """Idempotently attach searchable developer metadata `key` to the tab
        `page_id` in `spreadsheet_id`; return its metadataId. If `key` already
        exists in the spreadsheet, return the existing id instead of duplicating."""
        existing = await self._search_tab_metadata(spreadsheet_id, key)
        if existing is not None:
            return existing
        body = {"requests": [{"createDeveloperMetadata": {"developerMetadata": {
            "metadataKey": key,
            "location": {"sheetId": int(page_id)},
            "visibility": "DOCUMENT",
        }}}]}
        req = get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        resp = await execute_with_retry(req, on_retry=self._notify_retry)
        return (resp["replies"][0]["createDeveloperMetadata"]
                ["developerMetadata"]["metadataId"])

    async def _search_tab_metadata(self, spreadsheet_id, key):
        """Return the metadataId of developer metadata `key` in `spreadsheet_id`, or
        None if absent."""
        req = get_sheet().developerMetadata().search(
            spreadsheetId=spreadsheet_id,
            body={"dataFilters": [{"developerMetadataLookup": {"metadataKey": key}}]},
        )
        resp = await execute_with_retry(req, on_retry=self._notify_retry)
        matched = (resp or {}).get("matchedDeveloperMetadata", [])
        if matched:
            return matched[0]["developerMetadata"]["metadataId"]
        return None

    async def find_tab_by_metadata(self, spreadsheet_id, metadata_id):
        """Resolve the tab page id carrying developer metadata `metadata_id`, or None
        if absent (e.g. it didn't survive a copyTo/duplicate). Callers MUST fall back
        to a known page id — never trust this as the sole pointer."""
        req = get_sheet().developerMetadata().get(
            spreadsheetId=spreadsheet_id, metadataId=int(metadata_id))
        resp = await execute_with_retry(req, on_retry=self._notify_retry)
        return (resp or {}).get("location", {}).get("sheetId")

    async def stamp_rollup_metadata(self, puzzle: PuzzleData, key):
        """Best-effort: ensure developer metadata `key` is on this puzzle's tab,
        resolving the live-vs-archive spreadsheet. Returns (tab_page_id, metadata_id),
        or (None, None) if the puzzle has no sheet yet."""
        page_id = puzzle.google_page_id
        if page_id in (None, ""):
            return None, None
        spreadsheet_id = self._target_spreadsheet_id(puzzle)
        metadata_id = await self.ensure_tab_metadata(spreadsheet_id, int(page_id), key)
        return int(page_id), metadata_id

    async def _values_update(self, spreadsheet_id, a1_range, values):
        req = get_sheet().values().update(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            valueInputOption="RAW",   # store literally; never interpret as a formula
            body={"values": values},
        )
        return await execute_with_retry(req, on_retry=self._notify_retry)

    async def _values_clear(self, spreadsheet_id, a1_range):
        req = get_sheet().values().clear(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            body={},
        )
        return await execute_with_retry(req, on_retry=self._notify_retry)

    async def _values_get(self, spreadsheet_id, a1_range):
        req = get_sheet().values().get(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
        )
        return await execute_with_retry(req, on_retry=self._notify_retry)

    async def aggregate_to_tab(self, spreadsheet_id, tab_page_id, columns, rows,
                               start_row=4, start_column=0):
        """Write a `columns` header + `rows` data block to a tab as a snapshot.

        spreadsheet_id  target spreadsheet (caller picks live vs archive)
        tab_page_id     destination sheet's numeric page id
        columns         header strings, one per column
        rows            list of row-lists (cell strings), each len == len(columns)
        start_row/col   0-based top-left of the header (default A5, matching the
                        header row the old renderers used)

        Clears the old block first (replacing the fixed 50-blank-row padding) so a
        roll-up shorter than last time's leaves no stale trailing rows, then writes
        the header + rows in a single values().update.
        """
        tab_name = self.get_page_name_by_id(tab_page_id, spreadsheet_id)
        if tab_name is None:
            raise RuntimeError(
                f"aggregate_to_tab: no tab with page id {tab_page_id} "
                f"in spreadsheet {spreadsheet_id}"
            )
        safe_name = tab_name.replace("'", "''")   # escape for the quoted A1 range
        start_cell = self._a1_cell(start_row, start_column)
        last_col = self._col_letter(start_column + len(columns) - 1)
        # Open-ended range: clear everything from the header row down across the
        # columns this roll-up owns (leaves the puzzle's own B1/B3/I1 + grid alone).
        await self._values_clear(spreadsheet_id, f"'{safe_name}'!{start_cell}:{last_col}")
        values = [list(columns)] + [list(r) for r in rows]
        await self._values_update(spreadsheet_id, f"'{safe_name}'!{start_cell}", values)

    async def add_metapuzzle_data(self, puzzle: PuzzleData, round_puzzles: List[PuzzleData]):
        rows = [
            [round_puzzle.name, self._solution_text(round_puzzle)]
            for round_puzzle in round_puzzles
            if round_puzzle.id != puzzle.id   # skip the meta's own row
        ]
        await self.aggregate_to_tab(
            self._target_spreadsheet_id(puzzle),
            puzzle.google_page_id,
            ["Puzzle titles", "Puzzle solutions"],
            rows,
        )

    async def add_metametapuzzle_data(self, puzzle: PuzzleData, round_puzzles: List[PuzzleData]):
        rows = []
        for round_puzzle in round_puzzles:
            if round_puzzle.id == puzzle.id:      # skip self
                continue
            if round_puzzle.is_target:      # metas roll up via their feeders, not here
                continue
            if len(round_puzzle.tags) > 1:        # ambiguous multi-round membership
                continue
            if round_puzzle.tags:
                round = TagGroupJsonDb.get_by_attr(id=round_puzzle.tags[0])
                round_name = round.name
            else:
                round_name = "No Round"
            rows.append([round_name, round_puzzle.name, self._solution_text(round_puzzle)])
        await self.aggregate_to_tab(
            self._target_spreadsheet_id(puzzle),
            puzzle.google_page_id,
            ["Puzzle Round", "Puzzle Title", "Puzzle Solution"],
            rows,
        )

    # Column layout for the Unassigned Puzzles tab. Bot owns A-D; column E
    # (Notes) belongs to solvers and is NEVER written by the reconcile.
    UNASSIGNED_HEADER = ["id", "Puzzle", "Answer", "Length", "Notes"]

    async def ensure_unassigned_table(self, page_id, show_round=False):
        """Prepare the Unassigned Puzzles tab: write the header row and, best
        effort, wrap it in a native Table (hidden id column) for nicer styling.

        Uses the context-var spreadsheet id (set by _gsheet_cog). Returns a table
        id to persist on the hunt (the native Table's id when one is created,
        otherwise the page id as a harmless sentinel). The service guards this
        behind a null table id, so it runs once per hunt; the data writes do not
        depend on the native Table succeeding.
        """
        spreadsheet_id = self.get_spreadsheet_id()
        tab_name = self.get_page_name_by_id(page_id, spreadsheet_id)
        if tab_name is None:
            raise RuntimeError(f"ensure_unassigned_table: no tab with page id {page_id}")
        safe_name = tab_name.replace("'", "''")

        # Header row, written plainly so data is visible even without the Table.
        await self._values_update(spreadsheet_id, f"'{safe_name}'!A1", [self.UNASSIGNED_HEADER])

        table_id = page_id
        try:
            body = {"requests": [{"addTable": {"table": {
                "name": "Unassigned Puzzles",
                "range": {
                    "sheetId": page_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(self.UNASSIGNED_HEADER),
                },
                "columnProperties": [
                    {"columnIndex": 0, "columnName": "id"},
                    {"columnIndex": 1, "columnName": "Puzzle"},
                    {"columnIndex": 2, "columnName": "Answer"},
                    {"columnIndex": 3, "columnName": "Length"},
                    {"columnIndex": 4, "columnName": "Notes"},
                ],
            }}}]}
            result = await execute_with_retry(
                get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body),
                on_retry=self._notify_retry,
            )
            table_id = result["replies"][0]["addTable"]["table"]["tableId"]
            # Hide the bookkeeping id column.
            await execute_with_retry(
                get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{
                    "updateDimensionProperties": {
                        "range": {"sheetId": page_id, "dimension": "COLUMNS",
                                  "startIndex": 0, "endIndex": 1},
                        "properties": {"hiddenByUser": True},
                        "fields": "hiddenByUser",
                    }
                }]}),
                on_retry=self._notify_retry,
            )
        except Exception:
            logger.exception("ensure_unassigned_table: native Table styling skipped")
        return table_id

    async def read_unassigned_keys(self, page_id):
        """Read puzzle ids from column A of the Unassigned tab (skip header)."""
        spreadsheet_id = self.get_spreadsheet_id()
        tab_name = self.get_page_name_by_id(page_id, spreadsheet_id)
        if tab_name is None:
            return []
        safe_name = tab_name.replace("'", "''")
        resp = await self._values_get(spreadsheet_id, f"'{safe_name}'!A2:A")
        values = resp.get("values", []) or []
        keys = []
        for cell in values:
            try:
                keys.append(int(cell[0]))
            except (ValueError, IndexError):
                continue
        return keys

    @staticmethod
    def _unassigned_len_cell(value):
        """Length cell: a number when solved, an empty cell when unsolved.

        Blank is written as empty CellData (clears userEnteredValue) rather than
        an empty string — an empty cell is valid in a number-typed Table column,
        whereas a string "" makes Sheets reject the whole atomic batch.
        """
        if isinstance(value, (int, float)):
            return {"userEnteredValue": {"numberValue": value}}
        return {}

    async def apply_unassigned_changes(self, page_id, table_id, appends, updates, prunes):
        """Apply the incremental reconcile to the Unassigned Puzzles tab.

        Uses the context-var spreadsheet id (set by _gsheet_cog). Writes only the
        bot-owned columns A-D (id, Puzzle, Answer, Length) and NEVER column E
        (Notes). Reconcile is keyed on the id in column A:
          * updates  -> rewrite Puzzle/Answer/Length on the existing row
          * prunes   -> delete the whole row (bottom-first so indices stay valid)
          * appends  -> add new rows just below the existing data
        """
        spreadsheet_id = self.get_spreadsheet_id()
        # Resolve the tab name + current index in one metadata read.
        tab_name = None
        current_index = None
        for s in self.sheet_list(spreadsheet_id)["sheets"]:
            if s["properties"]["sheetId"] == int(page_id):
                tab_name = s["properties"]["title"]
                current_index = s["properties"].get("index")
                break
        if tab_name is None:
            return
        safe_name = tab_name.replace("'", "''")

        # Single read of the id column. vals[0] is the header (grid row 0), so a
        # data row at vals index i sits at 0-based grid row i.
        val_resp = await self._values_get(spreadsheet_id, f"'{safe_name}'!A:A")
        vals = val_resp.get("values", []) or []
        id_to_row = {}
        for i in range(1, len(vals)):
            cell = vals[i]
            if not cell:
                continue
            try:
                id_to_row[int(cell[0])] = i
            except (ValueError, IndexError):
                continue

        # Pin the tab to the far left, but only when it isn't already first — so a
        # steady-state reconcile with no row changes issues no Sheets write at all.
        requests = []
        if current_index not in (0, None):
            requests.append({"updateSheetProperties": {
                "properties": {"sheetId": page_id, "index": 0},
                "fields": "index",
            }})

        # Updates: refresh Puzzle/Answer/Length (cols B-D) on the matched row.
        update_by_id = {row[0]: row for row in updates}
        for rid, grid_row in id_to_row.items():
            row = update_by_id.get(rid)
            if row is None:
                continue
            requests.append({"updateCells": {
                "range": {
                    "sheetId": page_id,
                    "startRowIndex": grid_row,
                    "endRowIndex": grid_row + 1,
                    "startColumnIndex": 1,
                    "endColumnIndex": 4,
                },
                "fields": "userEnteredValue",
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": str(row[1])}},
                    {"userEnteredValue": {"stringValue": str(row[2])}},
                    self._unassigned_len_cell(row[3]),
                ]}],
            }})

        # Prunes: delete whole rows, bottom-first so earlier indices stay valid.
        prune_rows = sorted(
            (id_to_row[pid] for pid in prunes if pid in id_to_row),
            reverse=True,
        )
        for grid_row in prune_rows:
            requests.append({"deleteDimension": {
                "range": {
                    "sheetId": page_id,
                    "dimension": "ROWS",
                    "startIndex": grid_row,
                    "endIndex": grid_row + 1,
                },
            }})

        # Appends: write new rows (cols A-D) below the data. The pruned rows above
        # are deleted earlier in this same batch, so the new bottom is len(vals)
        # minus the number of pruned rows.
        if appends:
            append_start = len(vals) - len(prune_rows)
            grid_rows = []
            for r in appends:
                grid_rows.append([
                    {"userEnteredValue": {"numberValue": r[0]}},
                    {"userEnteredValue": {"stringValue": str(r[1])}},
                    {"userEnteredValue": {"stringValue": str(r[2])}},
                    self._unassigned_len_cell(r[3]),
                ])
            requests.append({"updateCells": {
                "range": {
                    "sheetId": page_id,
                    "startRowIndex": append_start,
                    "endRowIndex": append_start + len(grid_rows),
                    "startColumnIndex": 0,
                    "endColumnIndex": 4,
                },
                "fields": "userEnteredValue",
                "rows": [{"values": row} for row in grid_rows],
            }})

        if requests:
            req = get_sheet().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            )
            await execute_with_retry(req, on_retry=self._notify_retry)

async def setup(bot):
    # Comment this out if google-drive-related package are not installed!
    await bot.add_cog(GoogleSheets(bot))